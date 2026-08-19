"""Rafraîchissement borné des caches Apple utilisés par le retrieval."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture()
def live_source_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    db_path = tmp_path / "live-sources.db"
    monkeypatch.setattr("config.DB_PATH", str(db_path))
    monkeypatch.setattr("database.DB_PATH", db_path)
    from database import init_db
    from jarvis.retrieval.live_sources import reset_live_source_cache_for_tests

    init_db()
    reset_live_source_cache_for_tests()
    return db_path


@pytest.mark.asyncio
async def test_mail_refresh_caches_read_messages(live_source_db, monkeypatch):
    import integrations
    from database.email import get_recent_emails_from_db
    from integrations.mail import MailQueryResult
    from jarvis.retrieval import RetrievalRequest
    from jarvis.retrieval.live_sources import refresh_live_sources

    class MailStub:
        async def get_recent_result(self, max_results: int):
            assert max_results == 5
            return MailQueryResult(
                status="ok",
                messages=(
                    {
                        "id": "mail-gregoire",
                        "from": "Grégoire <gregoire@example.test>",
                        "subject": "Projet Atlas",
                        "date": "2026-08-15T20:15:00",
                        "is_read": True,
                        "snippet": "La validation est terminée.",
                    },
                ),
            )

    monkeypatch.setattr(integrations, "mail_client", MailStub())

    report = await refresh_live_sources(
        RetrievalRequest(query="résume mes 3 derniers mails")
    )

    assert report == {"email": "ok"}
    cached = get_recent_emails_from_db(limit=5)
    assert cached[0]["gmail_id"] == "mail-gregoire"
    assert cached[0]["is_read"] == 1


@pytest.mark.asyncio
async def test_calendar_refresh_persists_historical_window(live_source_db, monkeypatch):
    import integrations
    from database.knowledge import get_cached_calendar_events
    from jarvis.retrieval import RetrievalRequest
    from jarvis.retrieval.live_sources import refresh_live_sources

    captured = {}

    class CalendarStub:
        def is_available(self) -> bool:
            return True

        async def get_events(self, start: str, end: str):
            captured.update(start=start, end=end)
            return [
                {
                    "id": "event-gregoire",
                    "title": "Point avec Grégoire",
                    "start": start,
                    "end": end,
                    "calendar": "Travail",
                    "notes": "Projet Atlas",
                }
            ]

    monkeypatch.setattr(integrations, "calendar_client", CalendarStub())

    report = await refresh_live_sources(
        RetrievalRequest(query="qu'est-il arrivé hier avec Grégoire ?")
    )

    assert report == {"calendar": "ok"}
    assert "T00:00:00" in captured["start"]
    assert get_cached_calendar_events(limit=5)[0]["title"] == "Point avec Grégoire"


@pytest.mark.asyncio
async def test_follow_up_uses_recent_turns_to_select_live_mail(
    live_source_db, monkeypatch
):
    import integrations
    from integrations.mail import MailQueryResult
    from jarvis.retrieval import RetrievalRequest
    from jarvis.retrieval.live_sources import refresh_live_sources

    calls = 0

    class MailStub:
        async def get_recent_result(self, max_results: int):
            nonlocal calls
            calls += 1
            return MailQueryResult(status="ok", messages=())

    monkeypatch.setattr(integrations, "mail_client", MailStub())

    report = await refresh_live_sources(
        RetrievalRequest(
            query="et celui de Grégoire ?",
            recent_user_turns=("résume mes mails",),
        )
    )

    assert report == {"email": "ok"}
    assert calls == 1


@pytest.mark.asyncio
async def test_live_source_ttl_is_isolated_per_profile(live_source_db, monkeypatch):
    import integrations
    from database import init_db, use_profile
    from integrations.mail import MailQueryResult
    from jarvis.retrieval import RetrievalRequest
    from jarvis.retrieval.live_sources import refresh_live_sources

    calls = 0

    class MailStub:
        async def get_recent_result(self, max_results: int):
            nonlocal calls
            calls += 1
            return MailQueryResult(status="ok", messages=())

    monkeypatch.setattr(integrations, "mail_client", MailStub())
    request = RetrievalRequest(query="résume mes mails")

    with use_profile("alpha"):
        init_db()
        assert await refresh_live_sources(request) == {"email": "ok"}
    with use_profile("beta"):
        init_db()
        assert await refresh_live_sources(request) == {"email": "ok"}

    assert calls == 2


@pytest.mark.asyncio
async def test_calendar_refresh_removes_events_deleted_from_live_window(
    live_source_db,
    monkeypatch,
):
    import integrations
    from database.knowledge import get_cached_calendar_events
    from jarvis.retrieval import RetrievalRequest
    from jarvis.retrieval.live_sources import (
        refresh_live_sources,
        reset_live_source_cache_for_tests,
    )

    class CalendarStub:
        events = [
            {
                "id": "event-removed",
                "title": "Événement supprimé",
            }
        ]

        def is_available(self) -> bool:
            return True

        async def get_events(self, start: str, end: str):
            return [dict(event, start=start, end=end) for event in self.events]

    calendar = CalendarStub()
    monkeypatch.setattr(integrations, "calendar_client", calendar)
    request = RetrievalRequest(query="qu'est-il arrivé hier ?")

    assert await refresh_live_sources(request) == {"calendar": "ok"}
    assert get_cached_calendar_events(limit=5)[0]["external_id"] == "event-removed"

    calendar.events = []
    reset_live_source_cache_for_tests()
    assert await refresh_live_sources(request) == {"calendar": "ok"}
    assert get_cached_calendar_events(limit=5) == []


@pytest.mark.asyncio
async def test_calendar_failure_keeps_cached_window(live_source_db, monkeypatch):
    import integrations
    from database.knowledge import get_cached_calendar_events
    from integrations.calendar_api import CalendarQueryResult
    from jarvis.retrieval import RetrievalRequest
    from jarvis.retrieval.live_sources import (
        refresh_live_sources,
        reset_live_source_cache_for_tests,
    )

    class CalendarStub:
        available = True

        def is_available(self) -> bool:
            return True

        async def get_events_result(self, start: str, end: str):
            if not self.available:
                return CalendarQueryResult(
                    status="unavailable",
                    error="calendar_no_response",
                )
            return CalendarQueryResult(
                status="ok",
                events=(
                    {
                        "id": "event-kept",
                        "title": "Événement conservé",
                        "start": start,
                        "end": end,
                    },
                ),
            )

    calendar = CalendarStub()
    monkeypatch.setattr(integrations, "calendar_client", calendar)
    request = RetrievalRequest(query="qu'est-il arrivé hier ?")
    assert await refresh_live_sources(request) == {"calendar": "ok"}

    calendar.available = False
    reset_live_source_cache_for_tests()
    assert await refresh_live_sources(request) == {"calendar": "degraded"}
    assert get_cached_calendar_events(limit=5)[0]["external_id"] == "event-kept"


@pytest.mark.asyncio
async def test_imessage_refresh_does_not_run_incremental_sync(
    live_source_db, monkeypatch
):
    from jarvis.retrieval import RetrievalRequest
    from jarvis.retrieval.live_sources import refresh_live_sources

    calls = []

    class Importer:
        def is_available(self) -> bool:
            return True

        def sync_incremental(self):
            calls.append("sync")
            raise AssertionError("retrieval must not open chat.db")

    monkeypatch.setattr(
        "integrations.imessage_import.IMessageImporter",
        Importer,
    )

    report = await refresh_live_sources(
        RetrievalRequest(query="qu'est-ce que Grégoire m'a écrit en message ?")
    )
    assert report == {"imessage": "ok"}
    assert calls == []
