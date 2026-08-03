"""Erreurs HTTP publiques stables, sans détail d'exception interne."""

from __future__ import annotations

from fastapi import HTTPException


def api_error(status_code: int, code: str, message: str) -> HTTPException:
    """Construit le contrat commun ``detail.{code,message}``."""
    return HTTPException(
        status_code=status_code,
        detail={"code": code, "message": message},
    )


def internal_error(code: str, message: str) -> HTTPException:
    """Erreur 500 volontairement dépourvue du texte de l'exception source."""
    return api_error(500, code, message)
