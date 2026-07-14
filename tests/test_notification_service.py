"""Contrats de NotificationService et de sa façade database historique."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

import database
from jarvis.event_bus import event_bus
from jarvis.notification_service import NotificationService, notification_service


PROJECT_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def tmp_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    db_path = tmp_path / "notification-service.db"
    monkeypatch.setattr("config.DB_PATH", str(db_path))
    monkeypatch.setattr(database, "DB_PATH", db_path)
    database.init_db()
    return db_path


@pytest.mark.asyncio
async def test_service_deduplicates_and_emits_only_after_new_persistence(tmp_db: Path):
    await event_bus.wait_until_idle()
    queue = event_bus.subscribe()
    service = NotificationService()
    try:
        first_id = service.create(
            source="email",
            title="Facture à régler",
            content="Échéance demain",
            priority="high",
            email_id="mail-1",
        )
        duplicate_id = service.create(
            source="email",
            title="Facture à régler",
            content="Échéance demain",
            priority="high",
            email_id="mail-1",
        )
        distinct_email_id = service.create(
            source="email",
            title="Facture à régler",
            content="Échéance demain",
            priority="high",
            email_id="mail-2",
        )
        await event_bus.wait_until_idle()

        assert duplicate_id == first_id
        assert distinct_email_id != first_id
        assert [event.event_type for event in _drain(queue)] == [
            "notification.created",
            "notification.created",
        ]
        assert {row["id"] for row in service.get_recent()} == {
            first_id,
            distinct_email_id,
        }
    finally:
        event_bus.unsubscribe(queue)


def test_service_normalizes_priority_and_legacy_database_api_delegates(tmp_db: Path):
    notification_id = database.create_notification(
        source="legacy",
        title="Compatibilité",
        priority="inconnue",
    )

    unread = notification_service.get_unread()
    assert unread == [
        {
            "id": notification_id,
            "source": "legacy",
            "title": "Compatibilité",
            "content": None,
            "priority": "medium",
            "read": 0,
            "email_id": None,
            "created_at": unread[0]["created_at"],
        }
    ]
    assert notification_service.mark_read(notification_id) is True
    assert notification_service.mark_all_read() == 0


def test_high_priority_push_is_dispatched_once_for_a_deduplicated_notification(
    tmp_db: Path, monkeypatch: pytest.MonkeyPatch
):
    dispatched: list[tuple[str, str | None, str]] = []
    monkeypatch.setattr(
        database,
        "_dispatch_push_notification",
        lambda title, content, priority: dispatched.append((title, content, priority)),
    )

    service = NotificationService()
    first_id = service.create("system", "Action requise", "Maintenant", "urgent")
    duplicate_id = service.create("system", "Action requise", "Maintenant", "urgent")

    assert duplicate_id == first_id
    assert dispatched == [("Action requise", "Maintenant", "urgent")]


def test_notification_deduplication_index_is_applied_by_migrations(tmp_db: Path):
    with database.get_db() as conn:
        conn.execute("DROP INDEX idx_notif_dedup")
        from database.migrations import run_migrations

        run_migrations(conn)
        indexes = {
            row[1]
            for row in conn.execute("PRAGMA index_list('notifications')").fetchall()
        }

    assert "idx_notif_dedup" in indexes


def test_application_producers_use_notification_service() -> None:
    direct_callers: list[Path] = []
    for directory in (PROJECT_ROOT / "agents", PROJECT_ROOT / "scripts"):
        for path in directory.rglob("*.py"):
            if "create_notification(" in path.read_text(encoding="utf-8"):
                direct_callers.append(path.relative_to(PROJECT_ROOT))

    assert direct_callers == []


def _drain(queue: asyncio.Queue) -> list:
    events = []
    while True:
        try:
            events.append(queue.get_nowait())
        except asyncio.QueueEmpty:
            return events
