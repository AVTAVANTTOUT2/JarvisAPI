"""Taxonomie d'erreurs stable entre OpenCode et l'adaptateur JARVIS."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from integrations.opencode.security.redaction import redact_mapping, redact_text


@dataclass(frozen=True, slots=True)
class ErrorContext:
    method: str
    path: str
    status_code: int | None
    code: str
    message: str
    details: Any = None


class OpenCodeError(RuntimeError):
    default_code = "opencode_error"

    def __init__(self, message: str, *, context: ErrorContext | None = None) -> None:
        super().__init__(message)
        self.context = context


class OpenCodeNetworkError(OpenCodeError):
    default_code = "network"


class OpenCodeTimeoutError(OpenCodeNetworkError):
    default_code = "timeout"


class OpenCodeProtocolError(OpenCodeError):
    default_code = "protocol"


class OpenCodeAuthenticationError(OpenCodeError):
    default_code = "authentication"


class OpenCodePermissionError(OpenCodeError):
    default_code = "permission"


class OpenCodeNotFoundError(OpenCodeError):
    default_code = "not_found"


class OpenCodeVersionMismatchError(OpenCodeProtocolError):
    default_code = "version_mismatch"


class OpenCodeModelError(OpenCodeError):
    default_code = "model"


class OpenCodeToolError(OpenCodeError):
    default_code = "tool"


class OpenCodeServerError(OpenCodeError):
    default_code = "server"


def _error_payload(response: httpx.Response) -> tuple[str, str, Any]:
    try:
        payload: Any = response.json()
    except ValueError:
        return "http_error", f"HTTP {response.status_code}", None
    if not isinstance(payload, dict):
        return "http_error", f"HTTP {response.status_code}", redact_mapping(payload)
    redacted = redact_mapping(payload)
    candidate = (
        redacted.get("error") if isinstance(redacted.get("error"), dict) else redacted
    )
    code = (
        candidate.get("name")
        or candidate.get("code")
        or redacted.get("code")
        or "http_error"
    )
    message = (
        candidate.get("message")
        or redacted.get("message")
        or f"HTTP {response.status_code}"
    )
    return redact_text(str(code)), redact_text(str(message)), redacted


def exception_for_response(
    response: httpx.Response, *, method: str, path: str
) -> OpenCodeError:
    code, message, details = _error_payload(response)
    context = ErrorContext(method, path, response.status_code, code, message, details)
    normalized = f"{code} {message}".lower()
    if response.status_code == 401:
        cls: type[OpenCodeError] = OpenCodeAuthenticationError
    elif response.status_code == 403:
        cls = OpenCodePermissionError
    elif response.status_code == 404:
        cls = OpenCodeNotFoundError
    elif "permission" in normalized:
        cls = OpenCodePermissionError
    elif "model" in normalized:
        cls = OpenCodeModelError
    elif "tool" in normalized:
        cls = OpenCodeToolError
    elif response.status_code >= 500:
        cls = OpenCodeServerError
    else:
        cls = OpenCodeProtocolError
    return cls(f"Requête OpenCode refusée ({code})", context=context)
