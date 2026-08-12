"""Plafonds de corps HTTP appliqués avant les parseurs de routes."""

from __future__ import annotations

import re

from fastapi import Request
from fastapi.responses import JSONResponse

import config


_BODY_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def request_size_limit(method: str, path: str) -> int | None:
    """Retourne le plafond strict d'une route, ou ``None`` si elle est exclue."""

    if method in _BODY_METHODS and path.startswith("/api/agentic/"):
        return max(1, int(config.AGENTIC_MAX_REQUEST_BYTES))
    if method != "POST":
        return None
    if path == "/api/mobile/voice/turn":
        return max(1, int(config.MOBILE_VOICE_MAX_REQUEST_BYTES))
    if re.fullmatch(r"/api/devices/[^/]+/screen", path):
        return max(1, int(config.REMOTE_SCREEN_MAX_REQUEST_BYTES))
    return None


def content_length_error(request: Request) -> JSONResponse | None:
    """Refuse un corps non borné ou excessif avant parsing multipart/JSON."""

    limit = request_size_limit(request.method, request.url.path)
    if limit is None:
        return None
    raw_length = request.headers.get("content-length")
    if raw_length is None:
        return JSONResponse(
            {
                "detail": {
                    "code": "length_required",
                    "message": "Content-Length obligatoire sur cette route",
                }
            },
            status_code=411,
        )
    try:
        declared = int(raw_length)
    except ValueError:
        return JSONResponse(
            {
                "detail": {
                    "code": "invalid_content_length",
                    "message": "Content-Length invalide",
                }
            },
            status_code=400,
        )
    if declared < 0:
        return JSONResponse(
            {
                "detail": {
                    "code": "invalid_content_length",
                    "message": "Content-Length invalide",
                }
            },
            status_code=400,
        )
    if declared > limit:
        return JSONResponse(
            {
                "detail": {
                    "code": "payload_too_large",
                    "message": (
                        f"Corps de requête trop volumineux (maximum {limit} octets)"
                    ),
                }
            },
            status_code=413,
        )
    return None


__all__ = ["content_length_error", "request_size_limit"]
