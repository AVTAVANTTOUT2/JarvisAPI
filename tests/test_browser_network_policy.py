"""Tests hermétiques de la frontière réseau du navigateur agentique."""

from __future__ import annotations

import asyncio
import ipaddress
from urllib.parse import urlsplit

import pytest

from core.outbound_security import OutboundURLRejected, ResolvedPublicEndpoint
from integrations.browser_security import (
    BrowserRequestGuard,
    BrowserSecurityError,
    SecureEgressProxy,
    sanitized_browser_path,
    sanitized_browser_url,
)


class _PublicPolicy:
    def __init__(self) -> None:
        self.urls: list[str] = []

    def resolve(self, url: str) -> ResolvedPublicEndpoint:
        self.urls.append(url)
        parsed = urlsplit(url)
        return ResolvedPublicEndpoint(
            url=url,
            host=str(parsed.hostname),
            port=parsed.port or 443,
            addresses=(ipaddress.ip_address("8.8.8.8"),),
        )


class _Frame:
    pass


class _Page:
    def __init__(self) -> None:
        self.main_frame = _Frame()


class _Request:
    def __init__(
        self,
        url: str,
        method: str,
        resource_type: str = "document",
        *,
        frame: _Frame | None = None,
        navigation: bool = False,
    ) -> None:
        self.url = url
        self.method = method
        self.resource_type = resource_type
        self.frame = frame
        self._navigation = navigation

    def is_navigation_request(self) -> bool:
        return self._navigation


class _Route:
    def __init__(
        self,
        request: _Request,
        *,
        response_status: int = 200,
        fail_fulfill: bool = False,
    ) -> None:
        self.request = request
        self.aborted: str | None = None
        self.continued = False
        self.fetched: dict[str, object] | None = None
        self.fulfilled: object | None = None
        self.response = _Response(response_status)
        self.fail_fulfill = fail_fulfill

    async def abort(self, code: str) -> None:
        self.aborted = code

    async def continue_(self) -> None:
        self.continued = True

    async def fetch(self, **kwargs: object) -> object:
        self.fetched = kwargs
        return self.response

    async def fulfill(self, *, response: object) -> None:
        if self.fail_fulfill:
            raise RuntimeError("fulfill failed")
        self.fulfilled = response


class _WebSocketRoute:
    def __init__(self, url: str) -> None:
        self.url = url
        self.closed: tuple[int, str] | None = None

    async def close(self, *, code: int, reason: str) -> None:
        self.closed = (code, reason)


class _Response:
    def __init__(self, status: int) -> None:
        self.status = status
        self.disposed = False

    async def dispose(self) -> None:
        self.disposed = True


@pytest.mark.asyncio
async def test_request_guard_allows_only_the_exact_approved_main_document() -> None:
    policy = _PublicPolicy()
    guard = BrowserRequestGuard(policy)
    page = _Page()
    guard.bind_page(page)
    guard.authorize_document("https://public.example/")
    allowed = _Route(
        _Request(
            "https://public.example/",
            "GET",
            frame=page.main_frame,
            navigation=True,
        )
    )
    await guard._route_request(allowed)
    assert allowed.fetched == {"max_redirects": 0}
    assert allowed.fulfilled is not None
    assert allowed.response.disposed is True
    assert allowed.aborted is None

    automatic_get = _Route(
        _Request("https://public.example/data", "GET", "fetch")
    )
    await guard._route_request(automatic_get)
    assert automatic_get.aborted == "blockedbyclient"
    assert automatic_get.continued is False
    guard.raise_if_blocked()

    for method in ("OPTIONS", "POST", "PUT", "PATCH", "DELETE"):
        denied = _Route(_Request("https://public.example/mutate", method, "fetch"))
        await guard._route_request(denied)
        assert denied.aborted == "blockedbyclient"
        assert denied.continued is False
    with pytest.raises(BrowserSecurityError, match="refusée"):
        guard.raise_if_blocked()

    after_incident = _Route(
        _Request("https://public.example/still-safe", "GET", "document")
    )
    await guard._route_request(after_incident)
    assert after_incident.aborted == "blockedbyclient"
    assert after_incident.continued is False


@pytest.mark.asyncio
async def test_exact_document_authorization_is_consumed_after_one_get() -> None:
    guard = BrowserRequestGuard(_PublicPolicy())
    page = _Page()
    guard.bind_page(page)
    guard.authorize_document("https://public.example/")

    first = _Route(
        _Request(
            "https://public.example/",
            "GET",
            frame=page.main_frame,
            navigation=True,
        )
    )
    repeated = _Route(
        _Request(
            "https://public.example/",
            "GET",
            frame=page.main_frame,
            navigation=True,
        )
    )

    await guard._route_request(first)
    await guard._route_request(repeated)

    assert first.fetched == {"max_redirects": 0}
    assert first.fulfilled is not None
    assert repeated.aborted == "blockedbyclient"
    with pytest.raises(BrowserSecurityError) as caught:
        guard.raise_if_blocked()
    assert caught.value.code == "unexpected_document"


@pytest.mark.asyncio
async def test_redirect_response_is_disposed_and_blocked_before_location() -> None:
    guard = BrowserRequestGuard(_PublicPolicy())
    page = _Page()
    guard.bind_page(page)
    guard.authorize_document("https://public.example/")
    redirect = _Route(
        _Request(
            "https://public.example/",
            "GET",
            frame=page.main_frame,
            navigation=True,
        ),
        response_status=302,
    )

    await guard._route_request(redirect)

    assert redirect.fetched == {"max_redirects": 0}
    assert redirect.fulfilled is None
    assert redirect.aborted == "blockedbyclient"
    assert redirect.response.disposed is True
    with pytest.raises(BrowserSecurityError) as caught:
        guard.raise_if_blocked()
    assert caught.value.code == "redirect_blocked"


@pytest.mark.asyncio
async def test_fetched_response_is_disposed_when_fulfill_fails() -> None:
    guard = BrowserRequestGuard(_PublicPolicy())
    page = _Page()
    guard.bind_page(page)
    guard.authorize_document("https://public.example/")
    route = _Route(
        _Request(
            "https://public.example/",
            "GET",
            frame=page.main_frame,
            navigation=True,
        ),
        fail_fulfill=True,
    )

    with pytest.raises(RuntimeError, match="fulfill failed"):
        await guard._route_request(route)

    assert route.response.disposed is True


@pytest.mark.asyncio
async def test_public_redirect_or_second_main_navigation_taints_the_session() -> None:
    policy = _PublicPolicy()
    guard = BrowserRequestGuard(policy)
    page = _Page()
    guard.bind_page(page)
    guard.authorize_document("https://public.example/")
    redirect = _Route(
        _Request(
            "https://public.example/opaque",
            "GET",
            frame=page.main_frame,
            navigation=True,
        )
    )

    await guard._route_request(redirect)

    assert redirect.aborted == "blockedbyclient"
    assert redirect.continued is False
    with pytest.raises(BrowserSecurityError) as caught:
        guard.raise_if_blocked()
    assert caught.value.code == "unexpected_document"


@pytest.mark.asyncio
async def test_request_guard_blocks_every_websocket_without_connecting() -> None:
    guard = BrowserRequestGuard(_PublicPolicy())
    route = _WebSocketRoute("wss://public.example/socket")
    await guard._route_web_socket(route)
    assert route.closed == (1008, "blocked by browser policy")
    with pytest.raises(BrowserSecurityError) as caught:
        guard.raise_if_blocked()
    assert caught.value.code == "websocket_blocked"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (
            "https://user:pass@public.example/private/token?q=secret#fragment",
            "[URL_REDACTED]",
        ),
        ("https://faß.de./private", "https://fass.de/"),
        ("https://bücher.example/private", "https://xn--bcher-kva.example/"),
        ("https://EXAMPLE.COM.:443/", "https://example.com/"),
        ("file:///etc/passwd", "[URL_REDACTED]"),
        ("not a url", "[URL_REDACTED]"),
    ],
)
def test_browser_url_sanitizer_keeps_only_public_origin(
    value: str, expected: str
) -> None:
    assert sanitized_browser_url(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (
            "https://public.example/private/token?q=secret#fragment",
            "/private/token",
        ),
        ("https://public.example/", "/"),
        (
            "https://user:pass@public.example/private/token?q=secret",
            "[PATH_REDACTED]",
        ),
        ("file:///etc/passwd", "[PATH_REDACTED]"),
        ("http://public.example/path", "[PATH_REDACTED]"),
        ("not a url", "[PATH_REDACTED]"),
        ("https://public.example/" + ("a" * 520), "[PATH_REDACTED]"),
        ("https://public.example/bad\x00path", "[PATH_REDACTED]"),
    ],
)
def test_browser_path_sanitizer_keeps_bounded_path_only(
    value: str, expected: str
) -> None:
    assert sanitized_browser_path(value) == expected


class _PinnedPolicy:
    def __init__(self, port: int, *, reject: bool = False) -> None:
        self.port = port
        self.reject = reject
        self.urls: list[str] = []

    def resolve(self, url: str) -> ResolvedPublicEndpoint:
        self.urls.append(url)
        if self.reject:
            raise OutboundURLRejected(
                "non_public_address", "destination test refusée"
            )
        return ResolvedPublicEndpoint(
            url=url,
            host="pinned.test",
            port=self.port,
            addresses=(ipaddress.ip_address("127.0.0.1"),),
        )


async def _connect(proxy: SecureEgressProxy, authority: str):
    parsed = urlsplit(proxy.server_url)
    reader, writer = await asyncio.open_connection(parsed.hostname, parsed.port)
    writer.write(
        f"CONNECT {authority} HTTP/1.1\r\nHost: {authority}\r\n\r\n".encode()
    )
    await writer.drain()
    response = await reader.readuntil(b"\r\n\r\n")
    return reader, writer, response


@pytest.mark.asyncio
async def test_connect_proxy_uses_the_validated_ip_instead_of_dns() -> None:
    received = asyncio.Event()

    async def target(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        assert await reader.readexactly(4) == b"PING"
        writer.write(b"PONG")
        await writer.drain()
        received.set()
        writer.close()

    target_server = await asyncio.start_server(target, "127.0.0.1", 0)
    target_port = int(target_server.sockets[0].getsockname()[1])
    policy = _PinnedPolicy(target_port)
    proxy = SecureEgressProxy(policy)
    await proxy.start()
    try:
        reader, writer, response = await _connect(proxy, "unresolvable.test:443")
        assert response.startswith(b"HTTP/1.1 200")
        writer.write(b"PING")
        await writer.drain()
        assert await reader.readexactly(4) == b"PONG"
        await asyncio.wait_for(received.wait(), 1)
        assert policy.urls == ["https://unresolvable.test:443/"]
        writer.close()
        await writer.wait_closed()
    finally:
        await proxy.close()
        target_server.close()
        await target_server.wait_closed()


@pytest.mark.asyncio
async def test_connect_proxy_fails_closed_before_any_target_connection() -> None:
    policy = _PinnedPolicy(443, reject=True)
    proxy = SecureEgressProxy(policy)
    await proxy.start()
    try:
        _reader, writer, response = await _connect(proxy, "private.test:443")
        assert response.startswith(b"HTTP/1.1 403")
        with pytest.raises(BrowserSecurityError) as caught:
            proxy.raise_if_blocked()
        assert caught.value.code == "non_public_address"
        writer.close()
        await writer.wait_closed()

        _reader, second_writer, second_response = await _connect(
            proxy, "public-after-incident.test:443"
        )
        assert second_response.startswith(b"HTTP/1.1 403")
        assert len(policy.urls) == 1
        second_writer.close()
        await second_writer.wait_closed()
    finally:
        await proxy.close()
