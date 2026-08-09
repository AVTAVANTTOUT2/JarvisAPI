"""Régressions sécurité — setup auth, OpenAPI et politique PIN réseau."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_main_disables_public_openapi_urls() -> None:
    """`/docs`, `/redoc` et `/openapi.json` vivent hors du préfixe `/api/`.

    Le verrou de session ne les regardait donc pas : la surface complète de
    l'API — chemins, corps, noms de champs — se lisait sans cookie. On vérifie
    l'application montée, pas le texte du fichier : une chaîne présente dans
    `main.py` ne prouve pas qu'elle s'applique à `app`.
    """
    from fastapi.testclient import TestClient

    import main

    assert main.app.docs_url is None
    assert main.app.redoc_url is None
    assert main.app.openapi_url is None

    with TestClient(main.app) as client:
        for path in ("/docs", "/redoc", "/openapi.json"):
            assert client.get(path).status_code == 404, path

    # Le schéma reste constructible en interne : les contrats de route
    # (`tests/test_phase4_route_contract.py`) s'appuient dessus.
    assert main.app.openapi()["paths"]


def test_setup_guard_rejects_off_loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    import api.router_auth as router_auth
    import auth

    monkeypatch.setattr(auth, "is_configured", lambda: False)
    monkeypatch.setattr(router_auth, "_is_loopback", lambda _request: False)

    class _Req:
        client = type("C", (), {"host": "203.0.113.1"})()

    with pytest.raises(HTTPException) as exc:
        router_auth._guard_setup(_Req())
    assert exc.value.status_code == 403


def test_network_bind_requires_six_digit_pin(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "pin-policy.db"
    monkeypatch.setattr("config.DB_PATH", str(db_path))
    monkeypatch.setattr("database.DB_PATH", db_path)
    from database import init_db

    init_db()
    import auth

    monkeypatch.setattr("config.WEB_ALLOW_NETWORK_BIND", True)
    with pytest.raises(ValueError, match="6"):
        auth.validate_secret_strength("1234")
    auth.validate_secret_strength("123456")


def test_loopback_allows_four_digit_pin(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "pin-loopback.db"
    monkeypatch.setattr("config.DB_PATH", str(db_path))
    monkeypatch.setattr("database.DB_PATH", db_path)
    from database import init_db

    init_db()
    import auth

    monkeypatch.setattr("config.WEB_ALLOW_NETWORK_BIND", False)
    auth.validate_secret_strength("1234")
