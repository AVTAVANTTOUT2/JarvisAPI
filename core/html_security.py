"""Réponses HTML statiques avec CSP liée au contenu exact de la page."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.responses import FileResponse

from security_headers import content_security_policy_for_html


def secure_html_file_response(
    path: Path,
    *,
    headers: dict[str, str] | None = None,
    **kwargs: Any,
) -> FileResponse:
    """Sert un HTML avec hashes CSP, sans autoriser tous les scripts inline."""
    target = Path(path)
    response_headers = dict(headers or {})
    response_headers["Content-Security-Policy"] = content_security_policy_for_html(
        target.read_bytes()
    )
    return FileResponse(
        target,
        media_type="text/html; charset=utf-8",
        headers=response_headers,
        **kwargs,
    )
