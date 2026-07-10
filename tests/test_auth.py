"""Tests : verrouillage app (PIN/passphrase, sessions, anti-brute-force)."""

from __future__ import annotations

import sys
import time
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

    auth.setup_secret("correct")
    for _ in range(3):
        assert auth.verify_only("wrong") is False

    locked, remaining = auth.is_locked_out()
    assert locked is True
    assert remaining > 0


def test_locked_out_rejects_even_correct_secret(tmp_db):
    import auth

    auth.setup_secret("correct")
    for _ in range(3):
        auth.verify_only("wrong")

    assert auth.verify_only("correct") is False


def test_successful_verify_resets_failed_attempts(tmp_db):
    import auth

    auth.setup_secret("correct")
    auth.verify_only("wrong")
    auth.verify_only("wrong")
    assert auth.verify_only("correct") is True

    # Le compteur est repassé à 0 : deux nouveaux échecs ne suffisent pas à verrouiller
    auth.verify_only("wrong")
    auth.verify_only("wrong")
    locked, _ = auth.is_locked_out()
    assert locked is False


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
