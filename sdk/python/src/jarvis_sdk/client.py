"""Client HTTP sûr et sans dépendance pour le contrat JARVIS."""

from __future__ import annotations

import http.cookiejar
import ipaddress
import json
import re
import ssl
import time
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit, urlunsplit
from urllib.request import (
    HTTPCookieProcessor,
    HTTPSHandler,
    HTTPRedirectHandler,
    OpenerDirector,
    Request,
    build_opener,
)

from .errors import (
    JarvisApiError,
    JarvisAuthenticationError,
    JarvisConfigurationError,
    JarvisResponseTooLarge,
    JarvisTransportError,
)
from .models import JarvisResponse, Operation
from .operations import OPERATIONS

_PATH_PARAMETER_RE = re.compile(r"\{([^}:]+)(?::[^}]+)?\}")
_PROFILE_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_IDEMPOTENT_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_RETRYABLE_STATUS_CODES = frozenset({408, 425, 429, 502, 503, 504})
_RESERVED_HEADERS = frozenset(
    {
        "authorization",
        "content-length",
        "cookie",
        "host",
        "origin",
        "x-csrf-token",
        "x-device-token",
        "x-location-token",
    }
)
_JSON_UNSET = object()


class _NoRedirectHandler(HTTPRedirectHandler):
    """Transforme tout redirect en erreur HTTP pour ne jamais transférer un secret."""

    def redirect_request(self, *_args: Any, **_kwargs: Any) -> None:
        return None


def _validate_secret_value(value: str | None, name: str) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized or any(char in normalized for char in "\r\n\x00"):
        raise JarvisConfigurationError(f"{name} invalide")
    return normalized


def _normalize_base_url(value: str) -> tuple[str, str]:
    parsed = urlsplit(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise JarvisConfigurationError("base_url doit être une URL HTTP(S) absolue")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise JarvisConfigurationError(
            "base_url ne doit contenir ni credentials, ni query, ni fragment"
        )
    if parsed.scheme == "http":
        hostname = parsed.hostname.rstrip(".").lower()
        is_loopback = hostname == "localhost"
        if not is_loopback:
            try:
                is_loopback = ipaddress.ip_address(hostname).is_loopback
            except ValueError:
                is_loopback = False
        if not is_loopback:
            raise JarvisConfigurationError("HTTP en clair est limité au loopback")
    path = parsed.path.rstrip("/")
    base = urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))
    origin = urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
    return base, origin


class JarvisClient:
    """Client d'instance JARVIS piloté par le registre OpenAPI généré."""

    def __init__(
        self,
        base_url: str,
        *,
        bearer_token: str | None = None,
        device_token: str | None = None,
        location_token: str | None = None,
        session_token: str | None = None,
        csrf_token: str | None = None,
        session_cookie_name: str = "jarvis_session",
        profile_id: str = "default",
        timeout_seconds: float = 30.0,
        retry_attempts: int = 3,
        retry_delay_seconds: float = 0.25,
        max_response_bytes: int = 16 * 1024 * 1024,
        cafile: str | Path | None = None,
        _opener: OpenerDirector | Any | None = None,
        _sleep: Any = time.sleep,
    ) -> None:
        self.base_url, self.origin = _normalize_base_url(base_url)
        if not _PROFILE_RE.fullmatch(profile_id):
            raise JarvisConfigurationError("profile_id invalide")
        if timeout_seconds <= 0 or retry_attempts < 1 or retry_delay_seconds < 0:
            raise JarvisConfigurationError("timeout/retry invalide")
        if max_response_bytes < 1:
            raise JarvisConfigurationError("max_response_bytes doit être positif")
        if not _PROFILE_RE.fullmatch(session_cookie_name):
            raise JarvisConfigurationError("session_cookie_name invalide")

        self.profile_id = profile_id
        self.timeout_seconds = float(timeout_seconds)
        self.retry_attempts = int(retry_attempts)
        self.retry_delay_seconds = float(retry_delay_seconds)
        self.max_response_bytes = int(max_response_bytes)
        self.bearer_token = _validate_secret_value(bearer_token, "bearer_token")
        self.device_token = _validate_secret_value(device_token, "device_token")
        self.location_token = _validate_secret_value(location_token, "location_token")
        self._session_token = _validate_secret_value(session_token, "session_token")
        self._csrf_token = _validate_secret_value(csrf_token, "csrf_token")
        self._session_cookie_name = session_cookie_name
        self._session_ready = self._session_token is not None
        if self._session_ready != (self._csrf_token is not None):
            raise JarvisConfigurationError(
                "session_token et csrf_token doivent être fournis ensemble"
            )
        self._sleep = _sleep
        self._cookie_jar = http.cookiejar.CookieJar()
        if _opener is not None:
            if cafile is not None:
                raise JarvisConfigurationError("cafile est incompatible avec _opener")
            self._opener = _opener
        else:
            try:
                context = ssl.create_default_context(cafile=str(cafile) if cafile else None)
            except OSError as exc:
                raise JarvisConfigurationError("cafile illisible") from exc
            self._opener = build_opener(
                HTTPCookieProcessor(self._cookie_jar),
                HTTPSHandler(context=context),
                _NoRedirectHandler(),
            )

    def __enter__(self) -> "JarvisClient":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._csrf_token = None
        self._session_token = None
        self._session_ready = False
        self._cookie_jar.clear()

    def operations(self, *, tag: str | None = None) -> tuple[Operation, ...]:
        values = OPERATIONS.values()
        if tag is not None:
            values = (operation for operation in values if operation.tag == tag)
        return tuple(sorted(values, key=lambda operation: operation.operation_id))

    def operation(self, operation_id: str) -> Operation:
        try:
            return OPERATIONS[operation_id]
        except KeyError as exc:
            raise JarvisConfigurationError(
                f"operationId inconnu : {operation_id}"
            ) from exc

    def unlock(self, secret: str) -> Mapping[str, Any]:
        if not isinstance(secret, str) or not secret:
            raise JarvisConfigurationError("secret vide")
        response = self.call(
            "post_api_auth_unlock",
            json_body={"secret": secret},
        )
        payload = response.json()
        csrf_token = payload.get("csrf_token") if isinstance(payload, dict) else None
        set_cookie = response.header("Set-Cookie") or ""
        if (
            not isinstance(csrf_token, str)
            or not csrf_token
            or f"{self._session_cookie_name}=" not in set_cookie
        ):
            raise JarvisTransportError("Réponse unlock incomplète")
        self._csrf_token = csrf_token
        self._session_ready = True
        return payload

    def logout(self) -> Any:
        try:
            return self.call_json("post_api_auth_logout")
        finally:
            self.close()

    def health(self) -> Any:
        return self.call_json("get_api_health_live")

    def call_json(self, operation_id: str, **kwargs: Any) -> Any:
        return self.call(operation_id, **kwargs).json()

    def call(
        self,
        operation_id: str,
        *,
        path_params: Mapping[str, Any] | None = None,
        query: Mapping[str, Any] | None = None,
        json_body: Any = _JSON_UNSET,
        content: bytes | bytearray | memoryview | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> JarvisResponse:
        operation = self.operation(operation_id)
        path = self._render_path(operation.path, path_params or {})
        url = self.base_url + path
        if query:
            encoded = urlencode(
                [(key, value) for key, raw in query.items() for value in self._query_values(raw)],
                doseq=True,
            )
            if encoded:
                url += "?" + encoded
        request_headers = self._headers_for(operation)
        self._merge_custom_headers(request_headers, headers or {})

        if json_body is not _JSON_UNSET and content is not None:
            raise JarvisConfigurationError("json_body et content sont exclusifs")
        data: bytes | None = None
        if json_body is not _JSON_UNSET:
            try:
                data = json.dumps(json_body, ensure_ascii=False, separators=(",", ":")).encode()
            except (TypeError, ValueError) as exc:
                raise JarvisConfigurationError("json_body non sérialisable") from exc
            request_headers["Content-Type"] = "application/json"
        elif content is not None:
            data = bytes(content)

        request = Request(url, data=data, headers=request_headers, method=operation.method)
        return self._execute(request, operation)

    @staticmethod
    def _query_values(raw: Any) -> Iterable[Any]:
        if raw is None:
            return ()
        if isinstance(raw, (list, tuple)):
            return raw
        return (raw,)

    @staticmethod
    def _render_path(template: str, values: Mapping[str, Any]) -> str:
        expected = set(_PATH_PARAMETER_RE.findall(template))
        provided = set(values)
        if expected != provided:
            missing = sorted(expected - provided)
            extra = sorted(provided - expected)
            raise JarvisConfigurationError(
                f"path_params invalides (manquants={missing}, en trop={extra})"
            )

        def replace(match: re.Match[str]) -> str:
            value = values[match.group(1)]
            if value is None:
                raise JarvisConfigurationError("path_param ne peut pas être null")
            return quote(str(value), safe="")

        return _PATH_PARAMETER_RE.sub(replace, template)

    def _headers_for(self, operation: Operation) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "User-Agent": "jarvis-developer-sdk-python/1.0.0",
        }
        if self.profile_id != "default":
            headers["X-Jarvis-Profile"] = self.profile_id
        auth_kind = operation.auth
        if auth_kind in {"public", "pairing_code"}:
            return headers
        if auth_kind == "mobile_bearer":
            self._use_bearer(headers)
            return headers
        if auth_kind == "device_token":
            if not self.device_token:
                raise JarvisAuthenticationError("device_token requis")
            headers["X-Device-Token"] = self.device_token
            return headers
        if auth_kind == "mobile_or_location_token":
            if self.bearer_token:
                self._use_bearer(headers)
            elif self.location_token:
                headers["X-Location-Token"] = self.location_token
            else:
                raise JarvisAuthenticationError("bearer_token ou location_token requis")
            return headers
        if auth_kind == "session_or_mobile" and self.bearer_token:
            self._use_bearer(headers)
            return headers
        if auth_kind in {"session", "session_or_mobile"}:
            self._use_session(headers, operation.method)
            return headers
        raise JarvisConfigurationError(f"frontière d'auth inconnue : {auth_kind}")

    def _use_bearer(self, headers: dict[str, str]) -> None:
        if not self.bearer_token:
            raise JarvisAuthenticationError("bearer_token requis")
        headers["Authorization"] = f"Bearer {self.bearer_token}"

    def _use_session(self, headers: dict[str, str], method: str) -> None:
        if not self._session_ready:
            raise JarvisAuthenticationError("session JARVIS requise ; appelez unlock()")
        if self._session_token:
            headers["Cookie"] = f"{self._session_cookie_name}={self._session_token}"
        if method in {"POST", "PUT", "PATCH", "DELETE"}:
            if not self._csrf_token:
                raise JarvisAuthenticationError("csrf_token requis pour cette mutation")
            headers["X-CSRF-Token"] = self._csrf_token
            headers["Origin"] = self.origin

    @staticmethod
    def _merge_custom_headers(target: dict[str, str], headers: Mapping[str, str]) -> None:
        for key, value in headers.items():
            if not isinstance(key, str) or not isinstance(value, str):
                raise JarvisConfigurationError("header invalide")
            if key.lower() in _RESERVED_HEADERS:
                raise JarvisConfigurationError(f"header réservé : {key}")
            if not key or any(char in key + value for char in "\r\n\x00"):
                raise JarvisConfigurationError("header invalide")
            target[key] = value

    def _execute(self, request: Request, operation: Operation) -> JarvisResponse:
        can_retry = operation.method in _IDEMPOTENT_METHODS
        for attempt in range(self.retry_attempts):
            try:
                raw = self._opener.open(request, timeout=self.timeout_seconds)
                try:
                    response = self._response_from_stream(raw, int(raw.status))
                finally:
                    raw.close()
            except HTTPError as exc:
                try:
                    response = self._response_from_stream(exc, int(exc.code))
                finally:
                    exc.close()
            except (URLError, TimeoutError, OSError) as exc:
                if can_retry and attempt + 1 < self.retry_attempts:
                    self._sleep(self._retry_delay(attempt, None))
                    continue
                raise JarvisTransportError(
                    f"Transport JARVIS indisponible ({type(exc).__name__})"
                ) from exc

            if 200 <= response.status_code < 300:
                return response
            if (
                can_retry
                and response.status_code in _RETRYABLE_STATUS_CODES
                and attempt + 1 < self.retry_attempts
            ):
                self._sleep(self._retry_delay(attempt, response.header("Retry-After")))
                continue
            self._raise_api_error(response)
        raise JarvisTransportError("Nombre de tentatives JARVIS épuisé")

    def _response_from_stream(self, stream: Any, status_code: int) -> JarvisResponse:
        chunks = bytearray()
        while True:
            chunk = stream.read(min(64 * 1024, self.max_response_bytes + 1 - len(chunks)))
            if not chunk:
                break
            chunks.extend(chunk)
            if len(chunks) > self.max_response_bytes:
                raise JarvisResponseTooLarge("Réponse JARVIS trop volumineuse")
        return JarvisResponse(
            status_code=status_code,
            headers={key: value for key, value in stream.headers.items()},
            content=bytes(chunks),
        )

    def _retry_delay(self, attempt: int, retry_after: str | None) -> float:
        if retry_after:
            try:
                return min(30.0, max(0.0, float(retry_after)))
            except ValueError:
                pass
        return min(5.0, self.retry_delay_seconds * (2**attempt))

    @staticmethod
    def _raise_api_error(response: JarvisResponse) -> None:
        try:
            payload = response.json()
        except (UnicodeDecodeError, json.JSONDecodeError):
            payload = None
        detail: Any = payload
        code = f"http_{response.status_code}"
        if isinstance(payload, dict):
            detail = payload.get("detail", payload.get("error", payload))
            if isinstance(payload.get("error"), str):
                code = payload["error"]
            elif isinstance(detail, dict) and isinstance(detail.get("code"), str):
                code = detail["code"]
        raise JarvisApiError(response.status_code, code, detail, response)
