"""Contrat de reprise durable du flux Server-Sent Events."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from starlette.requests import Request

import database
from api.misc_integrations import (
    _durable_event_stream,
    _format_sse_event,
    _format_stream_reset,
    _parse_last_event_id,
    events_stream,
)
from database.event_log import _persist_event, get_event_replay_window
from jarvis.event_bus import JarvisEvent, event_bus


@pytest.fixture
def event_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    path = tmp_path / "sse-events.db"
    monkeypatch.setattr("config.DB_PATH", str(path))
    monkeypatch.setattr(database, "DB_PATH", path)
    database.init_db()
    return path


def _store_events(count: int) -> list[int]:
    for sequence in range(1, count + 1):
        _persist_event(
            JarvisEvent(
                type="system.service_up",
                agent="sse-test",
                data={"sequence": sequence},
                event_id=f"sse-event-{sequence}",
            )
        )
    with database.get_db() as conn:
        return [
            int(row[0])
            for row in conn.execute("SELECT id FROM event_log ORDER BY id ASC")
        ]


def test_initial_window_uses_latest_monotone_database_ids(event_db: Path):
    ids = _store_events(5)

    window = get_event_replay_window(None, initial_limit=3)

    assert [event["sse_id"] for event in window.events] == ids[-3:]
    assert [event["data"]["sequence"] for event in window.events] == [3, 4, 5]
    assert window.resume_after == ids[-3] - 1
    assert window.reset_reason is None


def test_last_event_id_resumes_strictly_after_cursor(event_db: Path):
    ids = _store_events(5)

    window = get_event_replay_window(ids[1])

    assert [event["sse_id"] for event in window.events] == ids[2:]
    assert window.requested_after == ids[1]
    assert window.skipped == 0
    assert window.reset_reason is None


def test_replay_overflow_is_explicit_and_bounded(event_db: Path):
    ids = _store_events(5)

    window = get_event_replay_window(0, replay_limit=2)

    assert [event["sse_id"] for event in window.events] == ids[-2:]
    assert window.resume_after == ids[-2] - 1
    assert window.skipped == 3
    assert window.reset_reason == "replay_limit_exceeded"


def test_cursor_ahead_after_database_reset_replays_recent_window(event_db: Path):
    ids = _store_events(3)

    window = get_event_replay_window(ids[-1] + 100, initial_limit=2)

    assert [event["sse_id"] for event in window.events] == ids[-2:]
    assert window.reset_reason == "cursor_ahead"
    assert window.resume_after == ids[-2] - 1


def test_deleted_old_history_advances_reset_cursor_to_available_window(event_db: Path):
    ids = _store_events(5)
    with database.get_db() as conn:
        conn.execute("DELETE FROM event_log WHERE id < ?", (ids[3],))

    window = get_event_replay_window(0)

    assert [event["sse_id"] for event in window.events] == ids[3:]
    assert window.reset_reason == "history_truncated"
    assert window.resume_after == ids[3] - 1


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, (None, None)),
        ("", (None, None)),
        (" 42 ", (42, None)),
        ("-1", (None, "invalid_last_event_id")),
        ("uuid-legacy", (None, "invalid_last_event_id")),
    ],
)
def test_last_event_id_parser_is_fail_safe(raw: str | None, expected):
    assert _parse_last_event_id(raw) == expected


def test_sse_frames_expose_id_and_reset_control_event(event_db: Path):
    _store_events(1)
    event = get_event_replay_window(None).events[0]

    frame = _format_sse_event(event)
    assert frame.startswith(f"id: {event['sse_id']}\n")
    assert json.loads(frame.split("data: ", 1)[1])["data"] == {"sequence": 1}

    reset = _format_stream_reset(
        reason="replay_limit_exceeded",
        requested_after=1,
        resume_after=50,
        skipped=49,
    )
    assert reset.startswith("event: stream.reset\nid: 50\n")
    assert '"skipped":49' in reset


@pytest.mark.asyncio
async def test_endpoint_honours_last_event_id_from_request_header(event_db: Path):
    ids = _store_events(3)
    subscribers_before = len(event_bus._subscribers)
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/events/stream",
            "headers": [(b"last-event-id", str(ids[0]).encode("ascii"))],
        }
    )

    response = await events_stream(request)
    iterator = response.body_iterator
    try:
        first = await anext(iterator)
        second = await anext(iterator)
    finally:
        await iterator.aclose()

    assert first.startswith(f"id: {ids[1]}\n")
    assert second.startswith(f"id: {ids[2]}\n")
    assert response.headers["cache-control"] == "no-cache, no-transform"
    assert len(event_bus._subscribers) == subscribers_before


@pytest.mark.asyncio
async def test_new_generator_resumes_after_process_local_history_is_lost(event_db: Path):
    ids = _store_events(3)

    generator = _durable_event_stream(ids[1])
    try:
        frame = await anext(generator)
    finally:
        await generator.aclose()

    assert frame.startswith(f"id: {ids[2]}\n")


@pytest.mark.asyncio
async def test_invalid_header_emits_reset_before_recent_history(event_db: Path):
    ids = _store_events(2)
    generator = _durable_event_stream(None, "invalid_last_event_id")
    try:
        reset = await anext(generator)
        first_event = await anext(generator)
    finally:
        await generator.aclose()

    assert reset.startswith(f"event: stream.reset\nid: {ids[0] - 1}\n")
    assert '"reason":"invalid_last_event_id"' in reset
    assert first_event.startswith(f"id: {ids[0]}\n")
