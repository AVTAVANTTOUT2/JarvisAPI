"""Régressions — réduction de la divulgation d'informations."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_imessage_error_response_is_generic() -> None:
    source = (REPO_ROOT / "integrations" / "imessage.py").read_text(encoding="utf-8")
    assert "type(e).__name__" not in source
    assert "traitement impossible" in source


def test_status_payload_helpers_redact_sensitive_fields() -> None:
    from api.misc_status import _computer_status_payload, _imessage_status_payload

    computer = _computer_status_payload()
    assert "shell" not in computer

    imessage = _imessage_status_payload()
    assert "target" not in imessage
    assert "configured" in imessage


@pytest.mark.asyncio
async def test_api_status_omits_route_names_and_imessage_target(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "status-redact.db"
    monkeypatch.setattr("config.DB_PATH", str(db_path))
    monkeypatch.setattr("database.DB_PATH", db_path)
    monkeypatch.setattr("config.IMESSAGE_TARGET", "+33612345678")
    from database import init_db

    init_db()

    mock_manager = MagicMock()
    mock_manager.get_status = AsyncMock(return_value={"ok": True})
    mock_manager.get_daily_summary = AsyncMock(
        return_value={"visits": [{"place_name": "Maison"}], "trip_count": 1, "total_distance_km": 2.0}
    )

    with patch("integrations.location.location_manager", mock_manager), patch(
        "database.location_helpers.get_today_visits",
        return_value=[{"place_name": "Maison"}],
    ), patch(
        "database.location_helpers.get_active_location_patterns",
        return_value=[],
    ), patch(
        "api.misc_status.get_usage_stats",
        return_value={"msg_count": 0, "total_cost": 0.0},
    ), patch(
        "api.misc_status._tts_status_payload",
        return_value={"tts_available": False},
    ), patch(
        "api.misc_status._audio_daemon_status_payload",
        return_value={},
    ), patch(
        "api.misc_status.count_memory_stats",
        return_value={},
    ):
        from api.misc_status import api_status

        payload = await api_status()

    assert "today_route" not in payload.get("location", {})
    assert "target" not in payload.get("imessage", {})
    assert payload["imessage"]["configured"] is True
    assert "shell" not in payload.get("computer", {})
    assert "agentic" in payload["agents_registered"]
    assert set(payload["agentic"]) == {
        "available",
            "runtimes",
            "active_run_count",
            "attention_required_count",
            "observability",
        }
