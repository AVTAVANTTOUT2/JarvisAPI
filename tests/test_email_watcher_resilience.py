"""Résilience H24 du watcher Mail : panne ≠ vide, quarantaine anti-poison."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest


@pytest.fixture
def watcher_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    db_path = tmp_path / "email_resilience.db"
    monkeypatch.setattr("config.DB_PATH", str(db_path))
    monkeypatch.setattr("database.DB_PATH", db_path)
    monkeypatch.setattr("config.EMAIL_WATCHER_LOCK_PATH", str(tmp_path / "email.lock"))
    from database import init_db

    init_db()
    return db_path


class _UnavailableMail:
    def is_available(self) -> bool:
        return True

    async def get_recent_result(self, _limit: int, *, include_preview: bool = True):
        from integrations.mail import MailQueryResult

        return MailQueryResult(status="unavailable", error="mail_no_response")


class _PoisonMail:
    def __init__(self) -> None:
        self.email_id = "poison-1"

    def is_available(self) -> bool:
        return True

    async def get_recent(self, _limit: int) -> list[dict]:
        return [
            {
                "id": self.email_id,
                "subject": "Sujet poison",
                "from": "Bot <bot@example.test>",
                "is_read": False,
            }
        ]

    async def get_message(self, email_id: str) -> dict:
        return {
            "id": email_id,
            "from": "Bot <bot@example.test>",
            "subject": "Sujet poison",
            "body": "corps",
            "date": "2026-08-16T00:00:00",
            "is_read": False,
        }


@pytest.mark.asyncio
async def test_mail_unavailable_does_not_complete_first_cycle(
    watcher_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import integrations
    from scripts import email_watcher as email_module

    monkeypatch.setattr(integrations, "mail_client", _UnavailableMail())
    watcher = email_module.EmailWatcher()
    assert watcher._initialized is False

    await watcher._check_new_emails()

    assert watcher._initialized is False
    assert watcher._last_cycle_stats["mode"] == "mail_unavailable"
    assert watcher._last_cycle_stats["mail_error"] == "mail_no_response"


@pytest.mark.asyncio
async def test_persistent_analysis_failure_is_quarantined(
    watcher_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import integrations
    from database import get_all_processed_email_ids
    from scripts import email_watcher as email_module

    mail = _PoisonMail()
    monkeypatch.setattr(integrations, "mail_client", mail)
    monkeypatch.setattr(
        email_module.llm,
        "chat",
        AsyncMock(return_value={"content": "pas du json du tout"}),
    )
    watcher = email_module.EmailWatcher()
    watcher._initialized = True

    for _ in range(email_module.MAX_ANALYSIS_FAILURES - 1):
        await watcher._check_new_emails()
        assert mail.email_id not in watcher.last_processed_ids
        assert get_all_processed_email_ids() == set()

    await watcher._check_new_emails()
    assert mail.email_id in watcher.last_processed_ids
    assert get_all_processed_email_ids() == {mail.email_id}
    assert watcher._last_cycle_stats["quarantined"] == 1


def test_get_recent_metadata_uses_full_timeout_not_eight_seconds() -> None:
    """Garde-fou : le listing watcher ne doit plus hardcoder 8 s."""
    import inspect

    from integrations import mail as mail_module

    src = inspect.getsource(mail_module.AppleMailClient.get_recent_result)
    assert "timeout=8.0" not in src
    assert "OSASCRIPT_TIMEOUT" in src
    assert "include_preview" in src
