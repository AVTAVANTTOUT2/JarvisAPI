"""Erreurs HTTP publiques stables, sans détail d'exception interne."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from fastapi import HTTPException


def api_error(
    status_code: int,
    code: str,
    message: str,
    *,
    context: Mapping[str, Any] | None = None,
) -> HTTPException:
    """Construit le contrat commun ``detail.{code,message}``."""
    detail: dict[str, Any] = {"code": code, "message": message}
    if context:
        detail["context"] = dict(context)
    return HTTPException(
        status_code=status_code,
        detail=detail,
    )


def internal_error(code: str, message: str) -> HTTPException:
    """Erreur 500 volontairement dépourvue du texte de l'exception source."""
    return api_error(500, code, message)
