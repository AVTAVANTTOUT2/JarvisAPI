"""Tests : verrouillage app (PIN/passphrase, sessions, anti-brute-force)."""

from __future__ import annotations

import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture
def tmp_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    db_path = tmp_path / "test_jarvis.db"
    monkeypatch.setattr("config.DB_PATH", str(db_path))
    monkeypatch.setattr("database.DB_PATH", db_path)
    monkeypatch.setattr("config.AUTH_LOCKOUT_MAX_ATTEMPTS", 3)
    monkeypatch.setattr("config.AUTH_LOCKOUT_MINUTES", 15)
    monkeypatch.setattr("config.AUTH_RATE_WINDOW_MINUTES", 15)
    monkeypatch.setattr("config.AUTH_PROGRESSIVE_DELAY_SECONDS", 0)
    monkeypatch.setattr("config.AUTH_PROGRESSIVE_DELAY_MAX_SECONDS", 30)
    monkeypatch.setattr("config.AUTH_GLOBAL_MAX_ATTEMPTS", 50)
    monkeypatch.setattr("config.AUTH_GLOBAL_LOCKOUT_MINUTES", 5)
    monkeypatch.setattr("config.SESSION_INACTIVITY_DAYS", 14)
    monkeypatch.setattr("config.SESSION_MAX_AGE_DAYS", 30)
    from database import init_db

    init_db()
    return db_path


# ── Setup / configuration ─────────────────────────────────────

def test_not_configured_by_default(tmp_db):
    import auth

    assert auth.is_configured() is False


def test_setup_secret_marks_configured(tmp_db):
    import auth

    auth.setup_secret("correct-horse")
    assert auth.is_configured() is True


def test_setup_secret_rejects_second_call(tmp_db):
    import auth

    auth.setup_secret("first-secret")
    with pytest.raises(ValueError):
        auth.setup_secret("second-secret")


def test_setup_secret_rejects_too_short(tmp_db):
    import auth

    with pytest.raises(ValueError):
        auth.setup_secret("abc")


@pytest.mark.parametrize("secret", ["12345", "abcdefghi"])
def test_setup_secret_enforces_strong_pin_or_passphrase(tmp_db, secret):
    import auth

    with pytest.raises(ValueError):
        auth.setup_secret(secret)


@pytest.mark.parametrize("secret", ["123456", "abcdefghij"])
def test_setup_secret_accepts_strong_pin_or_passphrase(tmp_db, secret):
    import auth

    auth.setup_secret(secret)
    assert auth.is_configured() is True


# ── Vérification (verify_only, lock screen) ───────────────────

def test_verify_only_correct_secret(tmp_db):
    import auth

    auth.setup_secret("my-passphrase")
    assert auth.verify_only("my-passphrase") is True


def test_verify_only_wrong_secret(tmp_db):
    import auth

    auth.setup_secret("my-passphrase")
    assert auth.verify_only("wrong-one") is False


def test_verify_only_before_configured_returns_false(tmp_db):
    import auth

    assert auth.verify_only("anything") is False


def test_secret_not_stored_in_plaintext(tmp_db):
    import auth
    from database import get_setting

    auth.setup_secret("super-secret-value")
    stored = get_setting("auth_secret_hash", "")
    assert "super-secret-value" not in stored
    assert "$" in stored  # format salt$hash


# ── Anti-brute-force ───────────────────────────────────────────

def test_lockout_after_max_failed_attempts(tmp_db):
    import auth

    auth.setup_secret("correct-secret")
    for _ in range(3):
        assert auth.verify_only("wrong") is False

    locked, remaining = auth.is_locked_out()
    assert locked is True
    assert remaining > 0


def test_locked_out_rejects_even_correct_secret(tmp_db):
    import auth

    auth.setup_secret("correct-secret")
    for _ in range(3):
        auth.verify_only("wrong")

    assert auth.verify_only("correct-secret") is False


def test_successful_verify_resets_failed_attempts(tmp_db):
    import auth

    auth.setup_secret("correct-secret")
    auth.verify_only("wrong")
    auth.verify_only("wrong")
    assert auth.verify_only("correct-secret") is True

    # Le compteur est repassé à 0 : deux nouveaux échecs ne suffisent pas à verrouiller
    auth.verify_only("wrong")
    auth.verify_only("wrong")
    locked, _ = auth.is_locked_out()
    assert locked is False


def test_client_lockout_does_not_block_another_client(tmp_db):
    import auth

    auth.setup_secret("correct-secret")
    blocked_client = auth.client_rate_key("203.0.113.10", channel="web")
    other_client = auth.client_rate_key("203.0.113.11", channel="web")

    for _ in range(3):
        assert auth.verify_only("wrong", blocked_client, channel="web") is False

    assert auth.rate_limit_status(blocked_client).blocked is True
    assert auth.rate_limit_status(other_client).blocked is False
    assert auth.verify_only("correct-secret", other_client, channel="web") is True


def test_progressive_delay_doubles_until_cap(tmp_db, monkeypatch):
    import auth

    monkeypatch.setattr("config.AUTH_LOCKOUT_MAX_ATTEMPTS", 10)
    monkeypatch.setattr("config.AUTH_PROGRESSIVE_DELAY_SECONDS", 2)
    monkeypatch.setattr("config.AUTH_PROGRESSIVE_DELAY_MAX_SECONDS", 5)
    client_key = auth.client_rate_key("198.51.100.2", channel="web")
    start = datetime(2026, 7, 24, 12, 0, tzinfo=timezone.utc)

    auth.record_failed_attempt(client_key, channel="web", now=start)
    assert auth.rate_limit_status(client_key, now=start).retry_after == 2

    second = start + timedelta(seconds=3)
    auth.record_failed_attempt(client_key, channel="web", now=second)
    assert auth.rate_limit_status(client_key, now=second).retry_after == 4

    third = second + timedelta(seconds=5)
    auth.record_failed_attempt(client_key, channel="web", now=third)
    assert auth.rate_limit_status(client_key, now=third).retry_after == 5


def test_secondary_global_cap_blocks_distributed_failures(tmp_db, monkeypatch):
    import auth

    monkeypatch.setattr("config.AUTH_LOCKOUT_MAX_ATTEMPTS", 100)
    monkeypatch.setattr("config.AUTH_GLOBAL_MAX_ATTEMPTS", 3)
    auth.setup_secret("correct-secret")

    for suffix in range(3):
        key = auth.client_rate_key(f"203.0.113.{suffix}", channel="web")
        auth.record_failed_attempt(key, channel="web")

    untouched = auth.client_rate_key("203.0.113.200", channel="web")
    status = auth.rate_limit_status(untouched)
    assert status.blocked is True
    assert status.scope == "global"
    assert status.hard is True
    assert auth.verify_only("correct-secret", untouched, channel="web") is False


def test_rate_limit_storage_never_contains_raw_ip(tmp_db):
    import auth
    from database import get_db

    raw_ip = "198.51.100.99"
    client_key = auth.client_rate_key(raw_ip, channel="web")
    auth.record_failed_attempt(client_key, channel="web")

    with get_db() as conn:
        keys = [
            row[0]
            for row in conn.execute(
                "SELECT client_key FROM auth_rate_limits ORDER BY client_key"
            ).fetchall()
        ]

    assert client_key in keys
    assert all(raw_ip not in key for key in keys)


def test_hard_lock_is_journaled_and_alerted_without_raw_identifier(
    tmp_db, monkeypatch
):
    import auth
    from database import get_db
    from jarvis.notification_service import notification_service

    alerts: list[tuple] = []
    monkeypatch.setattr(
        notification_service,
        "create",
        lambda *args, **kwargs: alerts.append((args, kwargs)) or 1,
    )
    raw_ip = "192.0.2.44"
    client_key = auth.client_rate_key(raw_ip, channel="web")

    auth.record_failed_attempt(client_key, channel="web")
    auth.record_failed_attempt(client_key, channel="web")
    auth.record_failed_attempt(client_key, channel="web")

    with get_db() as conn:
        rows = conn.execute(
            "SELECT action_type, payload FROM llm_action_logs ORDER BY id"
        ).fetchall()

    assert len(alerts) == 1
    assert len(rows) == 3
    assert all(row["action_type"] == "auth_failed" for row in rows)
    assert all(raw_ip not in row["payload"] for row in rows)


def test_sessions_migration_removes_legacy_global_lock(tmp_db):
    from database import get_db, set_setting
    from database.migrations import _migrate_sessions

    set_setting("auth_failed_attempts", "999")
    set_setting("auth_lockout_until", "2999-01-01T00:00:00")
    with get_db() as conn:
        _migrate_sessions(conn)
        old_rows = conn.execute(
            """
            SELECT key FROM app_settings
            WHERE key IN ('auth_failed_attempts', 'auth_lockout_until')
            """
        ).fetchall()
        table = conn.execute(
            """
            SELECT name FROM sqlite_master
            WHERE type = 'table' AND name = 'auth_rate_limits'
            """
        ).fetchone()

    assert old_rows == []
    assert table is not None


# ── change_secret ──────────────────────────────────────────────

def test_change_secret_with_correct_current(tmp_db):
    import auth

    auth.setup_secret("old-secret")
    assert auth.change_secret("old-secret", "new-secret") is True
    assert auth.verify_only("new-secret") is True
    assert auth.verify_only("old-secret") is False


def test_change_secret_with_wrong_current_fails(tmp_db):
    import auth

    auth.setup_secret("old-secret")
    assert auth.change_secret("wrong-current", "new-secret") is False
    assert auth.verify_only("old-secret") is True


def test_change_secret_rejects_weak_new_secret(tmp_db):
    import auth

    auth.setup_secret("old-secret")
    with pytest.raises(ValueError):
        auth.change_secret("old-secret", "12345")
    assert auth.verify_only("old-secret") is True


def test_change_secret_wrong_current_counts_toward_lockout(tmp_db):
    import auth

    auth.setup_secret("old-secret")
    for _ in range(3):
        assert auth.change_secret("wrong-current", "new-secret") is False

    locked, remaining = auth.is_locked_out()
    assert locked is True
    assert remaining > 0


# ── Sessions ───────────────────────────────────────────────────

def test_create_and_verify_session(tmp_db):
    import auth

    token, expires_at = auth.create_session(user_agent="pytest", ip="127.0.0.1")
    assert token
    assert expires_at

    row = auth.verify_session(token)
    assert row is not None


def test_verify_session_rejects_unknown_token(tmp_db):
    import auth

    assert auth.verify_session("not-a-real-token") is None


def test_verify_session_rejects_none(tmp_db):
    import auth

    assert auth.verify_session(None) is None


def test_revoke_session_invalidates_it(tmp_db):
    import auth

    token, _ = auth.create_session()
    assert auth.verify_session(token) is not None

    assert auth.revoke_session(token) is True
    assert auth.verify_session(token) is None


def test_revoke_unknown_session_returns_false(tmp_db):
    import auth

    assert auth.revoke_session("nonexistent") is False


def test_session_expires_after_inactivity_window(tmp_db, monkeypatch):
    import auth

    monkeypatch.setattr("config.SESSION_INACTIVITY_DAYS", 0)
    token, _ = auth.create_session()
    # Fenêtre d'inactivité à 0 jour → immédiatement expirée dès la prochaine vérification
    time.sleep(1.1)
    assert auth.verify_session(token) is None


def test_session_expires_after_absolute_max_age(tmp_db, monkeypatch):
    import auth
    from database import get_db

    token, _ = auth.create_session()
    token_hash = auth.hash_token(token)
    # Simule une session créée il y a plus longtemps que SESSION_MAX_AGE_DAYS
    with get_db() as conn:
        conn.execute(
            "UPDATE sessions SET created_at = datetime('now', '-31 days') WHERE token_hash = ?",
            (token_hash,),
        )
    assert auth.verify_session(token) is None


def test_list_active_sessions_excludes_revoked(tmp_db):
    import auth
    from database import list_active_sessions

    token1, _ = auth.create_session(user_agent="device-1")
    token2, _ = auth.create_session(user_agent="device-2")
    auth.revoke_session(token1)

    sessions = list_active_sessions()
    assert len(sessions) == 1
    assert sessions[0]["user_agent"] == "device-2"


def test_revoke_all_sessions_except_current(tmp_db):
    import auth
    from database import list_active_sessions

    keep, _ = auth.create_session(user_agent="keep-me")
    drop, _ = auth.create_session(user_agent="drop-me")

    from database import revoke_all_sessions

    revoke_all_sessions(except_token_hash=auth.hash_token(keep))

    assert auth.verify_session(keep) is not None
    assert auth.verify_session(drop) is None
