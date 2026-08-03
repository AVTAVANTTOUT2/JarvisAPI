"""Contrats HTTP des opérations qui renvoyaient auparavant ``200 ok:false``."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def api_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    db_path = tmp_path / "http-error-contract.db"
    monkeypatch.setattr("config.DB_PATH", str(db_path))
    monkeypatch.setattr("database.DB_PATH", db_path)

    from database import init_db
    from main import app
    from tests.conftest import authenticate

    init_db()
    with TestClient(app) as client:
        authenticate(client)
        yield client


def _assert_error(response, status: int, code: str) -> None:
    assert response.status_code == status, response.text
    detail = response.json()["detail"]
    assert detail["code"] == code
    assert isinstance(detail["message"], str) and detail["message"]


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/api/control/service-absent/detail"),
        ("post", "/api/control/service-absent/start"),
        ("post", "/api/control/service-absent/stop"),
        ("post", "/api/control/service-absent/restart"),
    ],
)
def test_unknown_control_service_is_a_structured_404(api_client, method: str, path: str):
    response = getattr(api_client, method)(path)
    _assert_error(response, 404, "service_not_found")


def test_unknown_imessage_contact_is_a_structured_404(api_client):
    response = api_client.post(
        "/api/people/contact-absent/send",
        json={"text": "Bonjour"},
    )
    _assert_error(response, 404, "person_not_found")


def test_imessage_contact_without_handle_is_a_structured_409(api_client, monkeypatch):
    from database import upsert_person

    upsert_person("Ada")
    monkeypatch.setattr("api.router_people._resolve_handle_with_contacts", lambda _name: None)

    response = api_client.post("/api/people/Ada/send", json={"text": "Bonjour"})
    _assert_error(response, 409, "imessage_handle_missing")


def test_imessage_transport_rejection_is_a_structured_502(api_client, monkeypatch):
    from database import upsert_person

    upsert_person("Ada")
    monkeypatch.setattr(
        "api.router_people._resolve_handle_with_contacts",
        lambda _name: "+33600000000",
    )
    monkeypatch.setattr(
        "integrations.imessage.send_imessage_to_address",
        lambda _handle, _text: (False, "détail interne à ne pas exposer"),
    )

    response = api_client.post("/api/people/Ada/send", json={"text": "Bonjour"})
    _assert_error(response, 502, "imessage_send_failed")
    assert "détail interne" not in response.text
