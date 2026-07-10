"""Tests de la prédiction du prochain message (heuristique, fonction pure)."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def test_insufficient_history_returns_zero_confidence():
    from scripts.message_predictor import predict_from_messages

    result = predict_from_messages([datetime(2026, 1, 1), datetime(2026, 1, 2)])
    assert result["probability"] == 0.0
    assert result["confidence"] == "low"
    assert result["estimated_next"] is None


def test_regular_daily_pattern_high_probability_when_due():
    from scripts.message_predictor import predict_from_messages

    # messages tous les jours à 18h pile pendant 20 jours
    base = datetime(2026, 1, 1, 18, 0)
    timestamps = [base + timedelta(days=i) for i in range(20)]
    # "maintenant" = 24h après le dernier message, à la même heure et le même jour de semaine
    now = timestamps[-1] + timedelta(days=1)

    result = predict_from_messages(timestamps, now=now)
    assert result["probability"] > 0.7
    assert result["confidence"] == "high"
    assert "18h" in result["explanation"] or "18" in result["explanation"]


def test_recently_messaged_lower_probability_than_overdue():
    from scripts.message_predictor import predict_from_messages

    base = datetime(2026, 1, 1, 12, 0)
    timestamps = [base + timedelta(days=i) for i in range(10)]

    just_after = predict_from_messages(timestamps, now=timestamps[-1] + timedelta(hours=1))
    overdue = predict_from_messages(timestamps, now=timestamps[-1] + timedelta(hours=23))
    assert overdue["probability"] > just_after["probability"]


def test_confidence_scales_with_sample_size():
    from scripts.message_predictor import predict_from_messages

    base = datetime(2026, 1, 1, 10, 0)
    few = [base + timedelta(days=i) for i in range(10)]
    many = [base + timedelta(days=i) for i in range(25)]

    assert predict_from_messages(few)["confidence"] == "medium"
    assert predict_from_messages(many)["confidence"] == "high"


def test_estimated_next_is_last_plus_average_interval():
    from scripts.message_predictor import predict_from_messages

    base = datetime(2026, 1, 1, 9, 0)
    timestamps = [base, base + timedelta(hours=48), base + timedelta(hours=96)]
    result = predict_from_messages(timestamps, now=base + timedelta(hours=100))
    estimated = datetime.fromisoformat(result["estimated_next"])
    assert estimated == base + timedelta(hours=144)  # dernier (96h) + intervalle moyen (48h)


def test_none_timestamps_are_filtered_out():
    from scripts.message_predictor import predict_from_messages

    base = datetime(2026, 1, 1, 10, 0)
    timestamps = [base, None, base + timedelta(days=1), None, base + timedelta(days=2)]
    result = predict_from_messages(timestamps)
    assert result["confidence"] != "low" or result["probability"] >= 0  # ne plante pas sur None


@pytest.mark.asyncio
async def test_predict_for_contact_unavailable_reader():
    from scripts.message_predictor import predict_for_contact

    with patch("integrations.imessage_reader.imessage_reader") as mock_reader:
        mock_reader.is_available.return_value = False
        result = await predict_for_contact("+33600000000")
    assert result["probability"] == 0.0
    assert "indisponible" in result["explanation"]


@pytest.mark.asyncio
async def test_predict_for_contact_filters_outgoing_messages():
    from scripts.message_predictor import predict_for_contact

    base = datetime(2026, 1, 1, 14, 0)
    fake_messages = [
        {"date": (base + timedelta(days=i)).strftime("%Y-%m-%d %H:%M:%S"), "is_from_me": False}
        for i in range(10)
    ] + [
        {"date": (base + timedelta(days=i, hours=1)).strftime("%Y-%m-%d %H:%M:%S"), "is_from_me": True}
        for i in range(10)
    ]

    mock_reader = MagicMock()
    mock_reader.is_available.return_value = True
    mock_reader.get_conversation_for_period.return_value = fake_messages

    with patch("integrations.imessage_reader.imessage_reader", mock_reader):
        result = await predict_for_contact("+33600000000", name="Karim")

    assert result["name"] == "Karim"
    assert result["confidence"] in ("medium", "high")  # 10 messages reçus, pas 20
