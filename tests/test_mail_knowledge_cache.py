"""Contrats du cache Mail utilisé par le retrieval universel."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture()
def mail_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    db_path = tmp_path / "mail-knowledge.db"
    monkeypatch.setattr("config.DB_PATH", str(db_path))
    monkeypatch.setattr("database.DB_PATH", db_path)

    from database import init_db

    init_db()
    return db_path


def test_mail_parser_preserves_read_state() -> None:
    from integrations.mail import AppleMailClient, MSG_SEPARATOR

    raw = (
        f"{MSG_SEPARATOR}\n"
        "ID:101\nFROM:Grégoire <gregoire@example.test>\n"
        "SUBJECT:Projet Atlas\nDATE:2026-08-15 20:15:00\n"
        "READ:true\nPREVIEW:La validation est terminée.\n"
        f"{MSG_SEPARATOR}\n"
        "ID:102\nFROM:Autre <autre@example.test>\n"
        "SUBJECT:Non lu\nDATE:2026-08-15 21:00:00\n"
        "READ:false\nPREVIEW:À traiter.\n"
    )

    messages = AppleMailClient()._parse_message_list(raw)

    assert [message["id"] for message in messages] == ["101", "102"]
    assert [message["is_read"] for message in messages] == [True, False]


def test_recent_email_cache_includes_read_and_sorts_received_at(mail_db: Path) -> None:
    from database.email import get_recent_emails_from_db, save_email_full

    save_email_full(
        gmail_id="older-unread",
        sender="Autre",
        subject="Ancien",
        body="Ancien message",
        received_at="2026-08-14 09:00:00",
        summary="Ancien",
        is_read=False,
    )
    save_email_full(
        gmail_id="gregoire-read",
        sender="Grégoire",
        subject="Projet Atlas",
        body="La validation est terminée.",
        received_at="2026-08-15 20:15:00",
        summary="Validation terminée",
        is_read=True,
    )

    recent = get_recent_emails_from_db(limit=5)

    assert [item["gmail_id"] for item in recent] == [
        "gregoire-read",
        "older-unread",
    ]
    assert recent[0]["is_read"] == 1
