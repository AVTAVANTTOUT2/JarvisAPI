"""E2E Chromium hermétique de la frontière SSRF et DOM du navigateur."""

from __future__ import annotations

import asyncio
from collections import Counter
from datetime import datetime, timedelta, timezone
import ipaddress
from pathlib import Path
import ssl
from urllib.parse import urlsplit

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
import pytest

from core.outbound_security import OutboundURLRejected, ResolvedPublicEndpoint
from integrations.browser_driver import PlaywrightDriver
from integrations.browser_security import BrowserSecurityError


class _FixturePolicy:
    """Injection test explicite ; elle n'est activable par aucune configuration."""

    def __init__(self, port: int) -> None:
        self.port = port

    def resolve(self, url: str) -> ResolvedPublicEndpoint:
        parsed = urlsplit(url)
        if parsed.scheme not in {"https", "wss"} or parsed.port != self.port:
            raise OutboundURLRejected("https_required", "fixture HTTPS invalide")
        host = str(parsed.hostname or "")
        if host != "public.test":
            raise OutboundURLRejected(
                "non_public_address", "destination privée simulée refusée"
            )
        return ResolvedPublicEndpoint(
            url=url,
            host=host,
            port=self.port,
            addresses=(ipaddress.ip_address("127.0.0.1"),),
        )


def _tls_context(tmp_path: Path) -> ssl.SSLContext:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "public.test")])
    now = datetime.now(timezone.utc)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.DNSName("public.test"), x509.DNSName("private.test")]
            ),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    cert_path = tmp_path / "fixture-cert.pem"
    key_path = tmp_path / "fixture-key.pem"
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_path, key_path)
    return context


def _response(
    status: str,
    body: str = "",
    *,
    headers: tuple[tuple[str, str], ...] = (),
) -> bytes:
    encoded = body.encode()
    lines = [
        f"HTTP/1.1 {status}",
        f"Content-Length: {len(encoded)}",
        "Content-Type: text/html; charset=utf-8",
        "Connection: close",
        *(f"{name}: {value}" for name, value in headers),
        "",
        "",
    ]
    return "\r\n".join(lines).encode() + encoded


@pytest.mark.asyncio
async def test_real_chromium_blocks_redirect_subresource_post_websocket_and_stale_dom(
    tmp_path: Path,
) -> None:
    requests: Counter[tuple[str, str, str]] = Counter()
    server_port = 0

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            raw = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 2)
            lines = raw.decode("latin-1").splitlines()
            method, path, _version = lines[0].split()
            host = next(
                (
                    line.split(":", 1)[1].strip().split(":", 1)[0]
                    for line in lines[1:]
                    if line.lower().startswith("host:")
                ),
                "",
            )
            requests[(host, method, path)] += 1
            if host == "private.test":
                payload = _response("200 OK", "PRIVATE_METADATA_MUST_NEVER_LEAK")
            elif path == "/redirect-same":
                payload = _response(
                    "302 Found",
                    headers=(
                        (
                            "Location",
                            f"https://public.test:{server_port}/redirect-same",
                        ),
                    ),
                )
            elif path == "/redirect":
                payload = _response(
                    "302 Found",
                    headers=(("Location", f"https://private.test:{server_port}/secret"),),
                )
            elif path == "/subresource":
                payload = _response(
                    "200 OK",
                    f'<iframe src="https://private.test:{server_port}/secret"></iframe>',
                )
            elif path == "/meta-refresh":
                payload = _response(
                    "200 OK",
                    (
                        '<meta http-equiv="refresh" content="0; url='
                        f'https://private.test:{server_port}/secret">'
                    ),
                )
            elif path == "/post":
                payload = _response(
                    "200 OK",
                    "<script>fetch('/mutation',{method:'POST',body:'x'})</script>",
                )
            elif path == "/websocket":
                payload = _response(
                    "200 OK",
                    f"<script>new WebSocket('wss://public.test:{server_port}/socket')</script>",
                )
            elif path == "/automatic-get":
                payload = _response(
                    "200 OK",
                    """
                    <title>Static page</title>
                    <img src="/opaque-image">
                    <iframe src="/opaque-frame"></iframe>
                    <script>fetch('/opaque-fetch')</script>
                    """,
                )
            elif path == "/ok":
                payload = _response(
                    "200 OK",
                    """
                    <title>Public hotels</title>
                    <a id="result" href="/next">Results</a>
                    <form action="/search" method="get">
                      <input type="search" name="q" aria-label="Destination"
                             formaction="/evil" formmethod="post">
                    </form>
                    """,
                )
            else:
                payload = _response("200 OK", "public")
            writer.write(payload)
            await writer.drain()
        except (ConnectionError, TimeoutError, ValueError):
            pass
        finally:
            writer.close()

    server = await asyncio.start_server(
        handle,
        "127.0.0.1",
        0,
        ssl=_tls_context(tmp_path),
    )
    server_port = int(server.sockets[0].getsockname()[1])
    policy = _FixturePolicy(server_port)

    driver = PlaywrightDriver(network_policy=policy, ignore_https_errors=True)
    await driver.start(headless=True, nav_ms=5_000, act_ms=3_000)
    try:
        await driver.open(f"https://public.test:{server_port}/ok")
        url, title, text, elements = await driver.observe()
        assert url.endswith("/ok")
        assert title == "Public hotels"
        assert "PRIVATE_METADATA" not in text
        search = next(item for item in elements if item.name == "Destination")
        assert search.form_action.endswith("/search")
        assert search.form_method == "get"
        result = next(item for item in elements if item.name == "Results")
        await driver._page.evaluate(
            """
            () => {
              const old = document.querySelector('#result');
              const replacement = document.createElement('a');
              replacement.id = 'result';
              replacement.href = '/checkout';
              replacement.textContent = 'Pay now';
              old.replaceWith(replacement);
            }
            """
        )
        with pytest.raises(BrowserSecurityError, match="périmée"):
            await driver.inspect(result)
    finally:
        await driver.close()

    repeated = PlaywrightDriver(network_policy=policy, ignore_https_errors=True)
    await repeated.start(headless=True, nav_ms=5_000, act_ms=3_000)
    try:
        with pytest.raises(BrowserSecurityError) as caught:
            await repeated.open(
                f"https://public.test:{server_port}/redirect-same"
            )
        assert caught.value.code == "redirect_blocked"
    finally:
        await repeated.close()
    assert requests[("public.test", "GET", "/redirect-same")] == 1

    for path in ("redirect", "subresource", "meta-refresh"):
        isolated = PlaywrightDriver(network_policy=policy, ignore_https_errors=True)
        await isolated.start(headless=True, nav_ms=5_000, act_ms=3_000)
        try:
            with pytest.raises(BrowserSecurityError):
                try:
                    await isolated.open(f"https://public.test:{server_port}/{path}")
                    await asyncio.sleep(0.1)
                    await isolated.observe()
                finally:
                    assert "PRIVATE_METADATA_MUST_NEVER_LEAK" not in isolated.url
        finally:
            await isolated.close()

    for path in ("post", "websocket", "automatic-get"):
        isolated = PlaywrightDriver(network_policy=policy, ignore_https_errors=True)
        await isolated.start(headless=True, nav_ms=5_000, act_ms=3_000)
        try:
            await isolated.open(f"https://public.test:{server_port}/{path}")
            _url, _title, text, _elements = await isolated.observe()
            assert "PRIVATE_METADATA_MUST_NEVER_LEAK" not in text
        finally:
            await isolated.close()

    server.close()
    await server.wait_closed()
    assert not any(host == "private.test" for host, _method, _path in requests)
    assert requests[("public.test", "POST", "/mutation")] == 0
    assert requests[("public.test", "GET", "/socket")] == 0
    assert requests[("public.test", "GET", "/opaque-image")] == 0
    assert requests[("public.test", "GET", "/opaque-frame")] == 0
    assert requests[("public.test", "GET", "/opaque-fetch")] == 0
