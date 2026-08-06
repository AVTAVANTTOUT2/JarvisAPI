"""Régressions sécurité — setup auth, OpenAPI et politique PIN réseau."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_main_disables_public_openapi_urls() -> None:
    source = (REPO_ROOT / "main.py").read_text(encoding="utf-8")
    assert "docs_url=None" in source
    assert "redoc_url=None" in source
    assert "openapi_url=None" in source


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
