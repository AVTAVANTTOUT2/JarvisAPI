"""Helpers purs de couverture calendrier / backoff d'ingestion.

Ces fonctions ont causé des régressions réelles (#259/#273) : une fenêtre
mal fusionnée ou un backoff mal calculé écrase un curseur valide. Les
tests restent hors base pour rester déterministes.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from jarvis.ingestion.models import ConnectorBinding, IngestionSourceState
from jarvis.ingestion.service import (
    _calendar_window_is_covered,
    _calendar_windows,
    _contiguous_calendar_end,
    _in_failure_backoff,
    _is_due,
    _merge_calendar_windows,
    _parse_utc,
)


def _dt(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.astimezone(timezone.utc)


def test_merge_calendar_windows_joins_overlap_and_keeps_gaps() -> None:
    a = (_dt("2026-01-01T00:00:00Z"), _dt("2026-02-01T00:00:00Z"))
    b = (_dt("2026-01-15T00:00:00Z"), _dt("2026-03-01T00:00:00Z"))
    c = (_dt("2026-04-01T00:00:00Z"), _dt("2026-05-01T00:00:00Z"))

    merged = _merge_calendar_windows([a, c], b)

    assert merged == [
        (_dt("2026-01-01T00:00:00Z"), _dt("2026-03-01T00:00:00Z")),
        (_dt("2026-04-01T00:00:00Z"), _dt("2026-05-01T00:00:00Z")),
    ]


def test_merge_calendar_windows_joins_adjacent_touching_ends() -> None:
    left = (_dt("2026-01-01T00:00:00Z"), _dt("2026-02-01T00:00:00Z"))
    right = (_dt("2026-02-01T00:00:00Z"), _dt("2026-03-01T00:00:00Z"))

    assert _merge_calendar_windows([left], right) == [
        (_dt("2026-01-01T00:00:00Z"), _dt("2026-03-01T00:00:00Z")),
    ]


def test_calendar_window_is_covered_requires_full_span() -> None:
    windows = [
        (_dt("2026-01-01T00:00:00Z"), _dt("2026-02-01T00:00:00Z")),
        (_dt("2026-03-01T00:00:00Z"), _dt("2026-04-01T00:00:00Z")),
    ]

    assert _calendar_window_is_covered(
        windows, _dt("2026-01-10T00:00:00Z"), _dt("2026-01-20T00:00:00Z")
    )
    assert not _calendar_window_is_covered(
        windows, _dt("2026-01-15T00:00:00Z"), _dt("2026-03-15T00:00:00Z")
    )
    assert not _calendar_window_is_covered(
        windows, _dt("2026-02-10T00:00:00Z"), _dt("2026-02-20T00:00:00Z")
    )


def test_calendar_window_is_covered_empty_bounds_need_any_window() -> None:
    assert not _calendar_window_is_covered([], None, None)
    assert _calendar_window_is_covered(
        [(_dt("2026-01-01T00:00:00Z"), _dt("2026-02-01T00:00:00Z"))],
        None,
        None,
    )


def test_contiguous_calendar_end_stops_at_gap() -> None:
    windows = [
        (_dt("2026-01-01T00:00:00Z"), _dt("2026-02-01T00:00:00Z")),
        (_dt("2026-03-01T00:00:00Z"), _dt("2026-04-01T00:00:00Z")),
    ]
    assert (
        _contiguous_calendar_end(windows, _dt("2026-01-15T00:00:00Z"))
        == _dt("2026-02-01T00:00:00Z")
    )
    assert _contiguous_calendar_end(windows, _dt("2026-02-15T00:00:00Z")) is None


def test_calendar_windows_skips_malformed_cursor_entries() -> None:
    windows = _calendar_windows(
        {
            "coverage_windows": [
                ["2026-01-01T00:00:00Z", "2026-02-01T00:00:00Z"],
                ["bad"],
                ["2026-03-01T00:00:00Z", "not-a-date"],
                ["2026-04-01T00:00:00Z", "2026-03-01T00:00:00Z"],  # start >= end
                "oops",
            ]
        }
    )
    assert windows == [(_dt("2026-01-01T00:00:00Z"), _dt("2026-02-01T00:00:00Z"))]


def test_parse_utc_accepts_z_and_naive_as_utc() -> None:
    assert _parse_utc("2026-08-21T10:00:00Z") == _dt("2026-08-21T10:00:00Z")
    assert _parse_utc("2026-08-21T10:00:00") == _dt("2026-08-21T10:00:00Z")
    assert _parse_utc("not-a-date") is None
    assert _parse_utc("") is None


def _freeze_service_now(monkeypatch, now: datetime) -> None:
    class _FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):  # noqa: ANN001
            return now if tz is None else now.astimezone(tz)

    monkeypatch.setattr("jarvis.ingestion.service.datetime", _FixedDateTime)


def test_in_failure_backoff_respects_interval_floor(monkeypatch) -> None:
    now = _dt("2026-08-21T12:00:00Z")
    _freeze_service_now(monkeypatch, now)
    binding = ConnectorBinding(
        source="calendar",
        profile_id="default",
        connector_kind="apple",
        sync_interval_seconds=300,
        settings={"sync_interval_seconds": 30},
    )
    # failures=3 → 2**3=8, floor = max(30, 8) = 30s
    recent = IngestionSourceState(
        source="calendar",
        profile_id="default",
        consecutive_failures=3,
        last_attempt_at=(now - timedelta(seconds=5)).isoformat(),
    )
    expired = IngestionSourceState(
        source="calendar",
        profile_id="default",
        consecutive_failures=3,
        last_attempt_at=(now - timedelta(seconds=31)).isoformat(),
    )
    assert _in_failure_backoff(binding, recent) is True
    assert _in_failure_backoff(binding, expired) is False
    assert _in_failure_backoff(binding, None) is False


def test_in_failure_backoff_grows_beyond_interval(monkeypatch) -> None:
    now = _dt("2026-08-21T12:00:00Z")
    _freeze_service_now(monkeypatch, now)
    binding = ConnectorBinding(
        source="mail",
        profile_id="default",
        connector_kind="apple",
        sync_interval_seconds=300,
        settings={"sync_interval_seconds": 30},
    )
    # failures=8 → 2**8=256 > 30
    state = IngestionSourceState(
        source="mail",
        profile_id="default",
        consecutive_failures=8,
        last_attempt_at=(now - timedelta(seconds=100)).isoformat(),
    )
    assert _in_failure_backoff(binding, state) is True
    cooled = IngestionSourceState(
        source="mail",
        profile_id="default",
        consecutive_failures=8,
        last_attempt_at=(now - timedelta(seconds=257)).isoformat(),
    )
    assert _in_failure_backoff(binding, cooled) is False


def test_is_due_forces_calendar_when_backfill_pending(monkeypatch) -> None:
    now = _dt("2026-08-21T12:00:00Z")
    _freeze_service_now(monkeypatch, now)
    binding = ConnectorBinding(
        source="calendar",
        profile_id="default",
        connector_kind="apple",
        sync_interval_seconds=300,
    )
    state = IngestionSourceState(
        source="calendar",
        profile_id="default",
        last_success_at=now.isoformat(),
        cursor={"backfill_pending": True},
    )
    assert _is_due(binding, state) is True

    no_backfill = IngestionSourceState(
        source="calendar",
        profile_id="default",
        last_success_at=now.isoformat(),
        cursor={"backfill_pending": False},
    )
    assert _is_due(binding, no_backfill) is False
