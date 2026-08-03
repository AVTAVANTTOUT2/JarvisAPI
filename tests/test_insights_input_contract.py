"""Validation stricte des mutations Insights."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def insights_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    db_path = tmp_path / "insights-input-contract.db"
    monkeypatch.setattr("config.DB_PATH", str(db_path))
    monkeypatch.setattr("database.DB_PATH", db_path)

    from database import init_db
    from main import app
    from tests.conftest import authenticate

    init_db()
    with TestClient(app) as client:
        authenticate(client)
        yield client


@pytest.mark.parametrize(
    "payload",
    [
        {"date": "2026-02-30"},
        {"date": True},
        {"date": "2026-08-03", "extra": 1},
    ],
)
def test_jarvis_journal_generate_rejects_invalid_payloads(insights_client, payload):
    response = insights_client.post("/api/jarvis-journal/generate", json=payload)
    assert response.status_code == 422, response.text


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"log_tail": ""},
        {"log_tail": True},
        {"log_tail": "trace", "extra": 1},
    ],
)
def test_self_healing_diagnose_rejects_invalid_payloads(insights_client, payload):
    response = insights_client.post("/api/self-healing/diagnose", json=payload)
    assert response.status_code == 422, response.text


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"status": "done"},
        {"status": True},
        {"status": "kept", "extra": 1},
    ],
)
def test_commitment_update_rejects_invalid_payloads(insights_client, payload):
    response = insights_client.patch("/api/commitments/1", json=payload)
    assert response.status_code == 422, response.text


def test_valid_journal_date_is_normalized_before_generation(insights_client):
    generated = AsyncMock(return_value={"date": "2026-08-03", "entry": "Test"})
    with patch("scripts.jarvis_journal.generate_journal_entry", generated):
        response = insights_client.post(
            "/api/jarvis-journal/generate",
            json={"date": "2026-08-03"},
        )

    assert response.status_code == 200, response.text
    generated.assert_awaited_once_with(date="2026-08-03")


def test_valid_self_healing_log_is_trimmed(insights_client):
    diagnose = AsyncMock(return_value={"ok": True, "action": "diagnosed_only"})
    with patch("scripts.self_healing.handle_crash_loop", diagnose):
        response = insights_client.post(
            "/api/self-healing/diagnose",
            json={"log_tail": "  traceback  "},
        )

    assert response.status_code == 200, response.text
    diagnose.assert_awaited_once_with("traceback")


def test_valid_commitment_status_is_persisted(insights_client):
    from database import add_commitment, get_commitments

    commitment_id = add_commitment("Livrer la PR")
    response = insights_client.patch(
        f"/api/commitments/{commitment_id}",
        json={"status": "kept"},
    )

    assert response.status_code == 200, response.text
    assert get_commitments("kept")[0]["id"] == commitment_id
