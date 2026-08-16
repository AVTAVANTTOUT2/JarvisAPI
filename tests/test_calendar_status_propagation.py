"""Non-régression : une panne Calendar ne doit jamais ressembler à un agenda vide."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from integrations.calendar_api import AppleCalendarClient, CalendarQueryResult


def test_calendar_relative_results_distinguish_empty_from_unavailable(monkeypatch):
    client = AppleCalendarClient()
    monkeypatch.setattr(client, "is_available", lambda: True)
    run_script = AsyncMock(return_value=None)
    monkeypatch.setattr(client, "_run_applescript_async", run_script)

    failed = asyncio.run(client.get_today_events_result())
    assert failed.status == "unavailable"
    assert failed.error == "calendar_no_response"

    run_script.return_value = ""
    empty = asyncio.run(client.get_today_events_result())
    assert empty.status == "ok"
    assert empty.events == ()

    week = asyncio.run(client.get_week_events_result())
    assert week.status == "ok"
    assert "(7 * days)" in run_script.await_args_list[-1].args[0]


def test_calendar_range_result_preserves_a_verified_empty_window(monkeypatch):
    client = AppleCalendarClient()
    monkeypatch.setattr(client, "is_available", lambda: True)
    monkeypatch.setattr(
        client,
        "_run_applescript_async",
        AsyncMock(return_value=""),
    )

    result = asyncio.run(
        client.get_events_result("2026-08-16 00:00", "2026-08-17 00:00")
    )

    assert result.status == "ok"
    assert result.events == ()


def test_calendar_range_result_rejects_empty_or_reversed_windows(monkeypatch):
    client = AppleCalendarClient()
    monkeypatch.setattr(client, "is_available", lambda: False)
    run_script = AsyncMock(return_value="")
    monkeypatch.setattr(client, "_run_applescript_async", run_script)

    equal = asyncio.run(
        client.get_events_result("2026-08-16 12:00", "2026-08-16 12:00")
    )
    reversed_range = asyncio.run(
        client.get_events_result("2026-08-17 00:00", "2026-08-16 00:00")
    )

    assert equal.status == "unavailable"
    assert equal.error == "calendar_range_invalid"
    assert reversed_range.status == "unavailable"
    assert reversed_range.error == "calendar_range_invalid"
    run_script.assert_not_awaited()


def test_calendar_relative_script_does_not_swallow_collection_errors():
    script = AppleCalendarClient()._events_script(1)

    assert "repeat with cal in calendars" in script
    assert "try\n            set evts to" not in script


def test_calendar_action_returns_unavailable_instead_of_empty(monkeypatch):
    import actions
    import integrations.calendar_api as calendar_api

    class FailedCalendar:
        async def get_today_events_result(self):
            return CalendarQueryResult(
                status="unavailable", error="calendar_no_response"
            )

        async def get_week_events_result(self):
            raise AssertionError("unexpected week query")

    monkeypatch.setattr(calendar_api, "calendar_client", FailedCalendar())

    result = asyncio.run(actions._action_calendar({"range": "today"}))

    assert result["ok"] is False
    assert result["status"] == "unavailable"
    assert result["error"] == "calendar_no_response"
    assert "events" not in result


def test_calendar_action_accepts_a_verified_empty_week(monkeypatch):
    import actions
    import integrations.calendar_api as calendar_api

    class EmptyCalendar:
        async def get_today_events_result(self):
            raise AssertionError("unexpected today query")

        async def get_week_events_result(self):
            return CalendarQueryResult(status="ok", events=())

    monkeypatch.setattr(calendar_api, "calendar_client", EmptyCalendar())

    result = asyncio.run(actions._action_calendar({"range": "week"}))

    assert result == {"ok": True, "events": []}


def test_productivity_context_marks_calendar_as_unavailable(monkeypatch):
    import agents.productivity as productivity

    class FailedCalendar:
        async def get_today_events_result(self):
            return CalendarQueryResult(
                status="unavailable", error="calendar_no_response"
            )

    monkeypatch.setattr(productivity, "calendar_client", FailedCalendar())
    monkeypatch.setattr(productivity, "weather", None)
    monkeypatch.setattr(productivity, "get_tasks", lambda: [])
    monkeypatch.setattr(
        productivity,
        "get_unread_notifications",
        lambda limit=15: [],
    )

    context = asyncio.run(
        productivity.ProductivityAgent()._collect_pro_context(skip_mail=True)
    )

    assert context["calendar_events"] == []
    assert context["calendar_status"] == "unavailable"
    assert context["calendar_error"] == "calendar_no_response"
    assert "agenda indisponible" in context["calendar_context"]
    assert "agenda vide" not in context["calendar_context"]


def test_briefing_propagates_calendar_unavailability(monkeypatch):
    import database
    import database.cursor_jobs as cursor_jobs
    import integrations
    from agents.briefing_engine import collect_briefing_sources

    class FailedCalendar:
        async def get_today_events_result(self):
            return CalendarQueryResult(
                status="unavailable", error="calendar_no_response"
            )

    monkeypatch.setattr(integrations, "calendar_client", FailedCalendar())
    monkeypatch.setattr(integrations, "weather", None)
    monkeypatch.setattr(database, "get_tasks", lambda **kwargs: [])
    monkeypatch.setattr(database, "get_recent_email_summaries", lambda limit=15: [])
    monkeypatch.setattr(database, "get_unread_notifications", lambda limit=15: [])
    monkeypatch.setattr(database, "get_commitments", lambda **kwargs: [])
    monkeypatch.setattr(cursor_jobs, "list_cursor_jobs", lambda limit=10: [])

    items, unavailable, raw = asyncio.run(collect_briefing_sources())

    assert not any(item.source == "calendar" for item in items)
    assert {entry["source"] for entry in unavailable} == {"calendar"}
    assert raw["calendar"] == []
    assert raw["calendar_status"] == "unavailable"
    assert raw["calendar_error"] == "calendar_no_response"


def test_briefing_prompt_warns_that_unavailable_is_not_empty():
    from agents.briefing_engine import generate_structured_briefing

    async def failed_sources():
        return [], [{"source": "calendar", "reason": "calendar_no_response"}], {}

    llm_mock = AsyncMock(
        return_value={"content": "Agenda non vérifiable.", "cost": 0.0}
    )
    with (
        patch(
            "agents.briefing_engine.collect_briefing_sources",
            new=failed_sources,
        ),
        patch("llm.chat", new=llm_mock),
    ):
        briefing = asyncio.run(generate_structured_briefing("morning"))

    main_prompt = llm_mock.await_args_list[0].kwargs["messages"][0]["content"]
    assert "Sources indisponibles" in main_prompt
    assert "ne pas conclure que cette source est vide" in main_prompt
    assert briefing.unavailable == [
        {"source": "calendar", "reason": "calendar_no_response"}
    ]


def test_welcome_marks_calendar_unavailable_instead_of_saying_none(monkeypatch):
    import api.welcome as welcome

    class FailedCalendar:
        async def get_today_events_result(self):
            return CalendarQueryResult(
                status="unavailable", error="calendar_no_response"
            )

    llm_chat = AsyncMock(return_value={"content": "Bonjour.", "cost": 0.0})
    ws = SimpleNamespace(send_json=AsyncMock())
    monkeypatch.setattr(welcome, "_welcome_already_sent_today", lambda: False)
    monkeypatch.setattr(welcome, "_mark_welcome_sent", lambda: None)
    monkeypatch.setattr(welcome, "get_recent_moods", lambda _limit: [])
    monkeypatch.setattr(welcome, "get_tasks", lambda: [])
    monkeypatch.setattr(welcome, "mail_client", None)
    monkeypatch.setattr(welcome, "calendar_client", FailedCalendar())
    monkeypatch.setattr(welcome.llm, "chat", llm_chat)
    monkeypatch.setattr(
        welcome.config,
        "DEEPSEEK_API_KEY",
        "test-key",
        raising=False,
    )

    asyncio.run(welcome._maybe_send_daily_welcome(ws))

    prompt = llm_chat.await_args_list[-1].kwargs["messages"][0]["content"]
    assert "Prochain événement aujourd’hui : non vérifiable" in prompt
    assert "Prochain événement aujourd’hui : aucun" not in prompt
    ws.send_json.assert_awaited_once()


def test_daemon_logs_calendar_unavailable_without_silent_empty(
    monkeypatch,
    caplog,
):
    import integrations.calendar_api as calendar_api
    import scripts.jarvis_daemon as daemon_module

    class FailedCalendar:
        async def get_today_events_result(self):
            return CalendarQueryResult(
                status="unavailable", error="calendar_no_response"
            )

    daemon = object.__new__(daemon_module.JarvisDaemon)
    daemon.running = True
    daemon.tts_queue = asyncio.Queue()

    async def stop_after_iteration(_seconds):
        daemon.running = False

    monkeypatch.setattr(calendar_api, "calendar_client", FailedCalendar())
    monkeypatch.setattr(daemon_module.asyncio, "sleep", stop_after_iteration)
    caplog.set_level("WARNING", logger="scripts.jarvis_daemon")

    asyncio.run(daemon._calendar_reminder_loop())

    assert "calendar indisponible : calendar_no_response" in caplog.text
    assert daemon.tts_queue.empty()
