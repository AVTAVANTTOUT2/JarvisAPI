"""Confidentialité, rétention et effacement des journaux d'actions."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.conftest import authenticate


@pytest.fixture
def action_log_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    db_path = tmp_path / "action-logs.db"
    monkeypatch.setattr("config.DB_PATH", str(db_path))
    monkeypatch.setattr("database.DB_PATH", db_path)
    monkeypatch.setattr("config.RETENTION_LLM_LOGS_DAYS", 7)
    monkeypatch.setattr("config.ACTION_LOG_MAX_PAYLOAD_CHARS", 2048)

    from database import init_db

    init_db()
    return db_path


def _client():
    import main
    from fastapi.testclient import TestClient

    return TestClient(main.app)


def test_redaction_masks_sensitive_fields_pii_secrets_and_paths(monkeypatch):
    from jarvis.log_privacy import redact_action_log_payload

    monkeypatch.setattr("config.ACTION_LOG_MAX_PAYLOAD_CHARS", 2048)
    raw = {
        "conversation_id": 42,
        "action": {
            "type": "terminal",
            "command": "curl -H 'Authorization: Bearer raw-token' /Users/alice/private.txt",
        },
        "result": {"message": "Réponse envoyée à alice@example.com"},
        "diagnostic": (
            "alice@example.com /Users/alice/Documents/secret.txt "
            "api_key=top-secret-value"
        ),
    }

    serialized = redact_action_log_payload(raw, "terminal")
    stored = json.loads(serialized)

    assert stored["conversation_id"] == 42
    assert stored["action"] == "[REDACTED]"
    assert stored["result"] == "[REDACTED]"
    assert "[EMAIL_1]" in stored["diagnostic"]
    assert "[LOCAL_PATH]" in stored["diagnostic"]
    for forbidden in (
        "raw-token",
        "alice@example.com",
        "/Users/alice",
        "top-secret-value",
        "curl",
    ):
        assert forbidden not in serialized


def test_clipboard_payload_is_never_persistable(monkeypatch):
    from jarvis.log_privacy import redact_action_log_payload

    monkeypatch.setattr("config.ACTION_LOG_MAX_PAYLOAD_CHARS", 2048)
    secret_clipboard = "code-2FA-938201 et mot de passe hunter2"

    serialized = redact_action_log_payload(
        {"action": {"type": "clipboard"}, "result": secret_clipboard},
        "clipboard",
    )

    assert secret_clipboard not in serialized
    assert json.loads(serialized) == {
        "redacted": "[CLIPBOARD_CONTENT_REDACTED]",
    }


def test_payload_has_strict_valid_json_limit(monkeypatch):
    from jarvis.log_privacy import redact_action_log_payload

    monkeypatch.setattr("config.ACTION_LOG_MAX_PAYLOAD_CHARS", 256)
    serialized = redact_action_log_payload(
        {
            f"safe_field_{index}": "diagnostic " * 100
            for index in range(100)
        },
        "context_enrichment",
    )

    assert len(serialized) <= 256
    assert json.loads(serialized)["truncated"] is True


def test_log_persistence_redacts_before_insert_and_purges_expired(action_log_db):
    from database import get_db, log_llm_action

    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO llm_action_logs (agent, action_type, payload, status, created_at)
            VALUES ('system', 'old', 'raw legacy value', 'success', datetime('now', '-8 days'))
            """
        )

    log_id = log_llm_action(
        "action_executor",
        "terminal",
        {
            "action": {
                "type": "terminal",
                "command": "echo token=secret-token-value",
            },
            "result": {
                "ok": True,
                "stdout": "alice@example.com depuis /Users/alice",
            },
        },
        "success",
        12,
    )

    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, agent, action_type, payload FROM llm_action_logs"
        ).fetchall()

    assert len(rows) == 1
    assert rows[0]["id"] == log_id
    assert rows[0]["agent"] == "action_executor"
    assert rows[0]["action_type"] == "terminal"
    assert json.loads(rows[0]["payload"]) == {
        "action": "[REDACTED]",
        "result": "[REDACTED]",
    }


def test_migration_purges_legacy_unredacted_logs_once(action_log_db):
    from database import get_db, init_db

    with get_db() as conn:
        conn.execute(
            "DELETE FROM app_settings WHERE key = 'action_log_privacy_v1'"
        )
        conn.execute(
            """
            INSERT INTO llm_action_logs (agent, action_type, payload, status)
            VALUES ('legacy', 'clipboard', 'raw clipboard secret', 'success')
            """
        )
        conn.execute(
            """
            INSERT INTO dev_loop_log (iteration, phase, content, success)
            VALUES (1, 'legacy', 'raw terminal output', 0)
            """
        )

    init_db()

    with get_db() as conn:
        llm_count = conn.execute(
            "SELECT COUNT(*) FROM llm_action_logs"
        ).fetchone()[0]
        dev_count = conn.execute(
            "SELECT COUNT(*) FROM dev_loop_log"
        ).fetchone()[0]
        marker = conn.execute(
            "SELECT value FROM app_settings WHERE key = 'action_log_privacy_v1'"
        ).fetchone()

    assert llm_count == 0
    assert dev_count == 0
    assert marker["value"] == "applied"

    # La migration marquée ne repurge pas les nouveaux logs déjà protégés.
    from database import log_llm_action

    log_llm_action("system", "context_enrichment", {"key_count": 1}, "success")
    init_db()
    with get_db() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM llm_action_logs"
        ).fetchone()[0] == 1


def test_invalid_labels_cannot_smuggle_content(action_log_db):
    from database import get_db, log_llm_action

    log_llm_action(
        "agent alice@example.com",
        "terminal secret-token-value",
        {"safe": True},
        "success",
    )

    with get_db() as conn:
        row = conn.execute(
            "SELECT agent, action_type FROM llm_action_logs"
        ).fetchone()

    assert dict(row) == {"agent": "unknown", "action_type": "unknown"}


def test_devagent_logs_use_same_redaction_boundary(action_log_db):
    from database import devagent as devagent_db
    from database import get_db

    project_id = devagent_db.create_dev_project("privacy", "Privacy", "/tmp/privacy")
    devagent_db.log_iteration(
        project_id,
        1,
        "test",
        "Bearer raw-dev-token pour dev@example.com dans /Users/dev/private",
        False,
    )

    with get_db() as conn:
        content = conn.execute("SELECT content FROM dev_loop_log").fetchone()["content"]

    assert "raw-dev-token" not in content
    assert "dev@example.com" not in content
    assert "/Users/dev" not in content
    assert json.loads(content) == "[REDACTED]"


def test_logs_clear_endpoint_requires_auth_and_clears_every_visible_log(action_log_db):
    from database import devagent as devagent_db
    from database import log_llm_action

    log_llm_action("system", "context_enrichment", {"key_count": 2}, "success")
    project_id = devagent_db.create_dev_project("clear-me", "Clear me", "/tmp/clear-me")
    devagent_db.log_iteration(project_id, 1, "plan", "safe diagnostic", True)

    with _client() as client:
        blocked = client.delete("/api/logs")
        assert blocked.status_code in (401, 428)

        authenticate(client)
        response = client.delete("/api/logs")
        assert response.status_code == 200
        assert response.json() == {
            "ok": True,
            "deleted": {"llm_action_logs": 1, "dev_loop_log": 1},
            "deleted_count": 2,
        }
        assert client.get("/api/logs").json() == {"logs": [], "count": 0}
