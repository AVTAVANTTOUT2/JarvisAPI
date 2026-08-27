"""Frontière réseau fail-closed du navigateur agentique.

Le garde Playwright bloque les méthodes non idempotentes et revalide chaque
URL. Le proxy CONNECT résout à nouveau la destination puis ouvre le tunnel vers
l'adresse IP validée elle-même : le DNS n'est donc jamais réinterprété par
Chromium au moment de la connexion.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlsplit, urlunsplit

from core.outbound_security import (
    OutboundURLRejected,
    ResolvedPublicEndpoint,
    canonicalize_open_world_https_url,
    resolve_open_world_https_url,
)

ALLOWED_NETWORK_METHODS = frozenset({"GET", "HEAD"})
MAX_PROXY_HEADER_BYTES = 16 * 1024
MAX_PROXY_CONNECTIONS = 32
MAX_PROXY_TUNNEL_BYTES = 16 * 1024 * 1024
PROXY_IO_TIMEOUT_SECONDS = 10.0


class BrowserSecurityError(RuntimeError):
    """Violation structurée qui invalide intégralement la session."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class BrowserSecurityIncident:
    code: str
    method: str
    resource_type: str
    safe_url: str


class BrowserNetworkPolicy(Protocol):
    """Politique injectable ; la production utilise uniquement HTTPS public."""

    def resolve(self, url: str) -> ResolvedPublicEndpoint: ...


class PublicHTTPSNetworkPolicy:
    """Résout toutes les adresses et refuse toute destination non publique."""

    def __init__(self, *, resolver: Callable[..., list[tuple]] | None = None) -> None:
        self._resolver = resolver

    def resolve(self, url: str) -> ResolvedPublicEndpoint:
        parsed = urlsplit(str(url or "").strip())
        scheme = parsed.scheme.lower()
        if scheme == "wss":
            candidate = urlunsplit(
                ("https", parsed.netloc, parsed.path, parsed.query, parsed.fragment)
            )
        elif scheme == "https":
            candidate = str(url).strip()
        else:
            raise OutboundURLRejected(
                "https_required", "Le navigateur autorise uniquement HTTPS/WSS"
            )
        return resolve_open_world_https_url(candidate, resolver=self._resolver)


def sanitized_browser_url(url: str) -> str:
    """Ne conserve que l'origine, jamais chemin, query, fragment ou userinfo."""

    try:
        parsed = urlsplit(str(url or "").strip())
        scheme = parsed.scheme.lower()
        if scheme not in {"https", "wss"}:
            return "[URL_REDACTED]"
        candidate = urlunsplit(
            ("https", parsed.netloc, parsed.path, parsed.query, parsed.fragment)
        )
        canonical = urlsplit(canonicalize_open_world_https_url(candidate))
        return urlunsplit((scheme, canonical.netloc, "/", "", ""))
    except (OutboundURLRejected, ValueError):
        return "[URL_REDACTED]"


def sanitized_browser_path(url: str) -> str:
    """Expose un chemin borné, jamais la query, le fragment ou les identifiants."""

    try:
        parsed = urlsplit(str(url or "").strip())
        if (
            parsed.scheme.lower() not in {"https", "wss"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
        ):
            return "[PATH_REDACTED]"
        path = parsed.path or "/"
        if len(path) > 512 or any(ord(char) < 32 for char in path):
            return "[PATH_REDACTED]"
        return path
    except ValueError:
        return "[PATH_REDACTED]"


class BrowserRequestGuard:
    """Intercepte HTTP et WebSocket avant toute page du BrowserContext."""

    def __init__(self, policy: BrowserNetworkPolicy) -> None:
        self.policy = policy
        self._incident: BrowserSecurityIncident | None = None
        self._page: Any = None
        self._authorized_document: str | None = None

    @property
    def incident(self) -> BrowserSecurityIncident | None:
        return self._incident

    def _record(
        self,
        code: str,
        *,
        url: str,
        method: str = "",
        resource_type: str = "",
    ) -> None:
        if self._incident is None:
            self._incident = BrowserSecurityIncident(
                code=code,
                method=method[:16],
                resource_type=resource_type[:40],
                safe_url=sanitized_browser_url(url),
            )

    async def install(self, context: Any) -> None:
        await context.route("**/*", self._route_request)
        await context.route_web_socket("**/*", self._route_web_socket)

    def bind_page(self, page: Any) -> None:
        """Lie l'unique page principale créée après installation du garde."""

        if self._page is not None and self._page is not page:
            raise BrowserSecurityError("page_binding_conflict", "Page déjà liée")
        self._page = page

    def authorize_document(self, url: str) -> None:
        """Autorise temporairement un unique GET document principal exact."""

        if self._incident is not None:
            self.raise_if_blocked()
        self._authorized_document = str(url)

    def clear_document_authorization(self) -> None:
        self._authorized_document = None

    def _is_main_document(self, request: Any, resource_type: str) -> bool:
        if self._page is None or resource_type != "document":
            return False
        try:
            return bool(
                request.is_navigation_request()
                and request.frame is self._page.main_frame
            )
        except Exception:
            return False

    async def _route_request(self, route: Any) -> None:
        request = route.request
        method = str(request.method or "").upper()
        url = str(request.url or "")
        resource_type = str(request.resource_type or "")
        if self._incident is not None:
            await route.abort("blockedbyclient")
            return
        if method not in ALLOWED_NETWORK_METHODS:
            self._record(
                "non_idempotent_request",
                url=url,
                method=method,
                resource_type=resource_type,
            )
            await route.abort("blockedbyclient")
            return
        try:
            endpoint = await asyncio.to_thread(self.policy.resolve, url)
        except OutboundURLRejected as exc:
            self._record(
                exc.code,
                url=url,
                method=method,
                resource_type=resource_type,
            )
            await route.abort("blockedbyclient")
            return
        if self._incident is not None:
            await route.abort("blockedbyclient")
            return
        if (
            method == "GET"
            and self._is_main_document(request, resource_type)
            and endpoint.url == self._authorized_document
        ):
            # Consume before yielding to Playwright. A 30x loop back to the
            # exact same URL is still a second network effect and must not
            # reuse the one-shot approval.
            self._authorized_document = None
            # ``continue_`` lets Chromium follow redirects inside the same
            # routed request. Fetch exactly one response and reject any 30x
            # before Chromium can contact its Location target.
            response: Any = None
            try:
                response = await route.fetch(max_redirects=0)
                status = int(getattr(response, "status", 0))
                if 300 <= status < 400:
                    self._record(
                        "redirect_blocked",
                        url=url,
                        method=method,
                        resource_type=resource_type,
                    )
                    await route.abort("blockedbyclient")
                    return
                await route.fulfill(response=response)
            finally:
                dispose = getattr(response, "dispose", None)
                if callable(dispose):
                    await dispose()
            return
        if self._is_main_document(request, resource_type):
            self._record(
                "unexpected_document",
                url=url,
                method=method,
                resource_type=resource_type,
            )
        await route.abort("blockedbyclient")

    async def _route_web_socket(self, route: Any) -> None:
        url = str(route.url or "")
        self._record(
            "websocket_blocked",
            url=url,
            method="GET",
            resource_type="websocket",
        )
        await route.close(code=1008, reason="blocked by browser policy")

    async def validate_page(self, page: Any) -> None:
        self.raise_if_blocked()
        for frame in tuple(page.frames):
            url = str(frame.url or "")
            # Chromium keeps an empty, non-navigated frame object when a
            # subframe request is aborted by the route guard.  It has no
            # destination or document content to validate.
            if url in {"", "about:blank"}:
                continue
            # An aborted subframe can also be replaced with Chromium's local
            # error document.  Never exempt the main frame: a failed top-level
            # navigation must remain visible to the caller.
            if url == "chrome-error://chromewebdata/" and frame != page.main_frame:
                continue
            try:
                await asyncio.to_thread(self.policy.resolve, url)
            except OutboundURLRejected as exc:
                self._record(
                    exc.code,
                    url=url,
                    method="GET",
                    resource_type="frame",
                )
                raise BrowserSecurityError(
                    exc.code, "Destination du navigateur refusée"
                ) from exc
        self.raise_if_blocked()

    def raise_if_blocked(self) -> None:
        if self._incident is not None:
            raise BrowserSecurityError(
                self._incident.code, "Requête du navigateur refusée par la politique"
            )


def _connect_authority(authority: str) -> tuple[str, int]:
    value = authority.strip()
    if not value or "@" in value or any(ord(char) < 33 for char in value):
        raise OutboundURLRejected("invalid_url", "Autorité CONNECT invalide")
    if value.startswith("["):
        closing = value.find("]")
        if closing < 0 or closing + 1 >= len(value) or value[closing + 1] != ":":
            raise OutboundURLRejected("invalid_url", "Autorité CONNECT invalide")
        host = value[1:closing]
        raw_port = value[closing + 2 :]
    else:
        if value.count(":") != 1:
            raise OutboundURLRejected("invalid_url", "Autorité CONNECT invalide")
        host, raw_port = value.rsplit(":", 1)
    try:
        port = int(raw_port)
    except ValueError as exc:
        raise OutboundURLRejected("invalid_url", "Port CONNECT invalide") from exc
    if not host or not 1 <= port <= 65535:
        raise OutboundURLRejected("invalid_url", "Autorité CONNECT invalide")
    return host, port


class SecureEgressProxy:
    """Proxy HTTPS local qui connecte exclusivement une IP validée et épinglée."""

    def __init__(self, policy: BrowserNetworkPolicy) -> None:
        self.policy = policy
        self._server: asyncio.AbstractServer | None = None
        self._writers: set[asyncio.StreamWriter] = set()
        self._tasks: set[asyncio.Task[Any]] = set()
        self._incident: BrowserSecurityIncident | None = None

    @property
    def server_url(self) -> str:
        if self._server is None or not self._server.sockets:
            raise RuntimeError("proxy_not_started")
        port = int(self._server.sockets[0].getsockname()[1])
        return f"http://127.0.0.1:{port}"

    async def start(self) -> None:
        if self._server is not None:
            return
        self._server = await asyncio.start_server(
            self._handle_client,
            host="127.0.0.1",
            port=0,
            limit=MAX_PROXY_HEADER_BYTES,
        )

    async def _read_headers(self, reader: asyncio.StreamReader) -> str:
        consumed = 0
        lines: list[bytes] = []
        while True:
            line = await asyncio.wait_for(
                reader.readline(), timeout=PROXY_IO_TIMEOUT_SECONDS
            )
            consumed += len(line)
            if not line or consumed > MAX_PROXY_HEADER_BYTES:
                raise BrowserSecurityError("proxy_request_invalid", "Requête proxy invalide")
            if line in {b"\r\n", b"\n"}:
                break
            lines.append(line)
        try:
            return b"".join(lines).decode("ascii")
        except UnicodeDecodeError as exc:
            raise BrowserSecurityError(
                "proxy_request_invalid", "Requête proxy invalide"
            ) from exc

    async def _open_pinned(
        self, endpoint: ResolvedPublicEndpoint
    ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        last_error: BaseException | None = None
        for address in endpoint.addresses:
            try:
                return await asyncio.wait_for(
                    asyncio.open_connection(str(address), endpoint.port),
                    timeout=PROXY_IO_TIMEOUT_SECONDS,
                )
            except (OSError, TimeoutError) as exc:
                last_error = exc
        raise BrowserSecurityError(
            "egress_connect_failed", "Connexion à la destination publique impossible"
        ) from last_error

    async def _pipe(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        transferred = 0
        try:
            while data := await reader.read(64 * 1024):
                transferred += len(data)
                if transferred > MAX_PROXY_TUNNEL_BYTES:
                    self._record_incident("proxy_byte_limit")
                    raise BrowserSecurityError(
                        "proxy_byte_limit", "Volume du tunnel navigateur dépassé"
                    )
                writer.write(data)
                await writer.drain()
        except (ConnectionError, asyncio.CancelledError):
            pass
        finally:
            writer.close()

    def _record_incident(self, code: str) -> None:
        if self._incident is None:
            self._incident = BrowserSecurityIncident(
                code=code,
                method="CONNECT",
                resource_type="proxy",
                safe_url="[URL_REDACTED]",
            )

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        task = asyncio.current_task()
        if task is not None:
            self._tasks.add(task)
        if len(self._writers) >= MAX_PROXY_CONNECTIONS:
            self._record_incident("proxy_capacity")
        self._writers.add(writer)
        target_writer: asyncio.StreamWriter | None = None
        pipe_tasks: list[asyncio.Task[None]] = []
        try:
            if self._incident is not None:
                raise BrowserSecurityError(
                    self._incident.code, "Session proxy déjà refusée"
                )
            headers = await self._read_headers(reader)
            first_line = headers.splitlines()[0] if headers else ""
            parts = first_line.split()
            if len(parts) != 3 or parts[0].upper() != "CONNECT":
                raise BrowserSecurityError(
                    "proxy_method_blocked", "Seul CONNECT est autorisé"
                )
            host, port = _connect_authority(parts[1])
            endpoint = await asyncio.to_thread(
                self.policy.resolve,
                f"https://{'[' + host + ']' if ':' in host else host}:{port}/",
            )
            if self._incident is not None:
                raise BrowserSecurityError(
                    self._incident.code, "Session proxy déjà refusée"
                )
            target_reader, target_writer = await self._open_pinned(endpoint)
            writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            await writer.drain()
            pipe_tasks = [
                asyncio.create_task(self._pipe(reader, target_writer)),
                asyncio.create_task(self._pipe(target_reader, writer)),
            ]
            await asyncio.gather(*pipe_tasks)
        except (OutboundURLRejected, BrowserSecurityError) as exc:
            self._record_incident(exc.code)
            if not writer.is_closing():
                writer.write(b"HTTP/1.1 403 Forbidden\r\nConnection: close\r\n\r\n")
                try:
                    await writer.drain()
                except ConnectionError:
                    pass
        except (ConnectionError, TimeoutError):
            pass
        finally:
            for pipe_task in pipe_tasks:
                if not pipe_task.done():
                    pipe_task.cancel()
            if pipe_tasks:
                await asyncio.gather(*pipe_tasks, return_exceptions=True)
            if target_writer is not None:
                target_writer.close()
            writer.close()
            self._writers.discard(writer)
            if task is not None:
                self._tasks.discard(task)

    def raise_if_blocked(self) -> None:
        if self._incident is not None:
            raise BrowserSecurityError(
                self._incident.code, "Connexion proxy refusée par la politique"
            )

    async def close(self) -> None:
        server, self._server = self._server, None
        if server is not None:
            server.close()
            await server.wait_closed()
        for writer in tuple(self._writers):
            writer.close()
        current = asyncio.current_task()
        tasks = [task for task in tuple(self._tasks) if task is not current]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._writers.clear()
        self._tasks.clear()


__all__ = [
    "ALLOWED_NETWORK_METHODS",
    "BrowserNetworkPolicy",
    "BrowserRequestGuard",
    "BrowserSecurityError",
    "MAX_PROXY_CONNECTIONS",
    "MAX_PROXY_TUNNEL_BYTES",
    "PublicHTTPSNetworkPolicy",
    "SecureEgressProxy",
    "sanitized_browser_path",
    "sanitized_browser_url",
]
