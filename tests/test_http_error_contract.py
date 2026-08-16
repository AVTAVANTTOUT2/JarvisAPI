"""Contrats HTTP des opérations qui renvoyaient auparavant ``200 ok:false``."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

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
def test_unknown_control_service_is_a_structured_404(
    api_client, method: str, path: str
):
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
    monkeypatch.setattr(
        "api.router_people._resolve_handle_with_contacts", lambda _name: None
    )

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


def test_people_analytics_without_handle_is_a_structured_409(api_client, monkeypatch):
    from database import upsert_person

    upsert_person("Ada")
    monkeypatch.setattr(
        "api.router_people._resolve_handle_with_contacts", lambda _name: None
    )

    response = api_client.get("/api/people/Ada/analytics")
    _assert_error(response, 409, "imessage_handle_missing")


def test_calendar_test_unavailable_is_a_structured_503(api_client, monkeypatch):
    monkeypatch.setattr("api.misc_relationships.calendar_client", None)

    response = api_client.post("/api/calendar/test")
    _assert_error(response, 503, "calendar_unavailable")


def test_calendar_test_rejection_is_a_structured_502(api_client, monkeypatch):
    class RejectingCalendar:
        @staticmethod
        def is_available() -> bool:
            return True

        @staticmethod
        async def create_event(**_kwargs):
            return {"ok": False, "message": "détail AppleScript privé"}

    monkeypatch.setattr("api.misc_relationships.calendar_client", RejectingCalendar())

    response = api_client.post("/api/calendar/test")
    _assert_error(response, 502, "calendar_test_failed")
    assert "AppleScript privé" not in response.text


def test_calendar_read_exception_is_a_structured_502(api_client, monkeypatch):
    class BrokenCalendar:
        @staticmethod
        def is_available() -> bool:
            return True

        @staticmethod
        async def get_events_result(_start: str, _end: str):
            raise RuntimeError("détail Calendar privé")

    monkeypatch.setattr("api.misc_relationships.calendar_client", BrokenCalendar())

    response = api_client.get(
        "/api/calendar",
        params={"start": "2026-08-03", "end": "2026-08-04"},
    )
    _assert_error(response, 502, "calendar_read_failed")
    assert "Calendar privé" not in response.text


def test_calendar_read_unavailable_result_is_not_an_empty_200(api_client, monkeypatch):
    from integrations.calendar_api import CalendarQueryResult

    class UnavailableCalendar:
        @staticmethod
        async def get_events_result(_start: str, _end: str):
            return CalendarQueryResult(
                status="unavailable",
                error="calendar_no_response",
            )

    monkeypatch.setattr("api.misc_relationships.calendar_client", UnavailableCalendar())

    response = api_client.get(
        "/api/calendar",
        params={"start": "2026-08-03", "end": "2026-08-04"},
    )

    _assert_error(response, 502, "calendar_read_failed")


def test_calendar_read_invalid_range_is_a_structured_400(api_client, monkeypatch):
    from integrations.calendar_api import CalendarQueryResult

    class InvalidRangeCalendar:
        @staticmethod
        async def get_events_result(_start: str, _end: str):
            return CalendarQueryResult(
                status="unavailable",
                error="calendar_range_invalid",
            )

    monkeypatch.setattr(
        "api.misc_relationships.calendar_client", InvalidRangeCalendar()
    )

    response = api_client.get(
        "/api/calendar",
        params={"start": "2026-08-04", "end": "2026-08-03"},
    )

    _assert_error(response, 400, "calendar_range_invalid")


def test_contacts_failure_is_a_structured_503(api_client, monkeypatch):
    from integrations.imessage_reader import IMessageReader

    def fail_contacts(_self):
        raise RuntimeError("chat.db détail privé")

    monkeypatch.setattr(IMessageReader, "get_all_contacts", fail_contacts)

    response = api_client.get("/api/contacts")
    _assert_error(response, 503, "contacts_unavailable")
    assert "chat.db détail privé" not in response.text


def test_misc_relationship_validation_errors_are_structured(api_client):
    _assert_error(
        api_client.get("/api/time-machine/pas-une-date"),
        400,
        "invalid_calendar_date",
    )
    _assert_error(
        api_client.get("/api/export", params={"format": "xml"}),
        400,
        "unsupported_export_format",
    )


def test_food_session_probe_failure_is_a_structured_409(api_client, monkeypatch):
    from api.food_control import FoodControlError

    monkeypatch.setattr(
        "api.router_food.probe_session",
        AsyncMock(
            side_effect=FoodControlError(
                "Session Uber Eats expirée ; lancer une nouvelle capture.",
                status_code=409,
                code="food_session_expired",
            )
        ),
    )

    response = api_client.post("/api/food/session/probe")
    _assert_error(response, 409, "food_session_expired")
    assert "detail AppleScript" not in response.text


def test_control_service_refusal_is_not_returned_as_http_200(api_client, monkeypatch):
    monkeypatch.setattr(
        "api.router_daemon._start_service",
        AsyncMock(return_value={"ok": False, "error": "detail interne Ollama"}),
    )

    response = api_client.post("/api/control/ollama/start")
    _assert_error(response, 503, "service_start_failed")
    assert "detail interne" not in response.text


def test_bulk_control_failure_reports_only_failed_service_ids(api_client, monkeypatch):
    monkeypatch.setattr("api.router_daemon.INTERNAL_SERVICES", ["scheduler"])
    monkeypatch.setattr(
        "api.router_daemon._start_service",
        AsyncMock(side_effect=RuntimeError("detail système privé")),
    )

    response = api_client.post("/api/control/start-all")
    _assert_error(response, 503, "service_bulk_start_failed")
    assert response.json()["detail"]["context"] == {"failed_services": ["scheduler"]}
    assert "detail système privé" not in response.text


def test_control_logs_failure_is_a_structured_500(api_client, monkeypatch, tmp_path):
    log_file = tmp_path / "data" / ".jarvis_restart" / "backend.log"
    log_file.parent.mkdir(parents=True)
    log_file.write_text("scheduler démarré", encoding="utf-8")
    monkeypatch.setattr("api.router_daemon.BACKEND_LOG_FILE", log_file)

    def fail_read(*_args, **_kwargs):
        raise RuntimeError("detail grep privé")

    monkeypatch.setattr("api.router_daemon._read_service_log_lines", fail_read)

    response = api_client.get("/api/control/scheduler/logs")
    _assert_error(response, 500, "service_logs_unavailable")
    assert "detail grep privé" not in response.text


def test_control_logs_reject_unknown_service_and_unbounded_limit(api_client):
    _assert_error(
        api_client.get("/api/control/inconnu/logs"),
        404,
        "service_not_found",
    )
    response = api_client.get("/api/control/scheduler/logs", params={"lines": 501})
    assert response.status_code == 422
