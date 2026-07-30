"""Intégration HTTP et persistance du module fitness."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from tests.conftest import authenticate


@pytest.fixture
def fitness_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    db_path = tmp_path / "fitness.db"
    monkeypatch.setattr("config.DB_PATH", str(db_path))
    monkeypatch.setattr("database.DB_PATH", db_path)

    from database import init_db

    init_db()
    return db_path


def _client():
    from fastapi.testclient import TestClient

    import main

    return TestClient(main.app)


def test_fitness_schema_is_registered_idempotently(fitness_db: Path) -> None:
    from database import init_db

    init_db()
    with sqlite3.connect(fitness_db) as conn:
        objects = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'index')"
            )
        }

    assert {
        "workouts",
        "meals",
        "water_intake",
        "wellbeing_logs",
        "idx_workouts_date",
        "idx_meals_date",
        "idx_water_date",
        "idx_wellbeing_date",
    } <= objects


def test_fitness_schema_rejects_empty_wellbeing_log(fitness_db: Path) -> None:
    with sqlite3.connect(fitness_db) as conn, pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """
            INSERT INTO wellbeing_logs (date, rating, journal_text, source)
            VALUES ('2026-07-30', NULL, NULL, 'pwa')
            """
        )


def test_workouts_create_and_range_history(fitness_db: Path) -> None:
    today = datetime.now(ZoneInfo("Europe/Paris")).date()
    yesterday = today - timedelta(days=1)
    with _client() as client:
        authenticate(client)
        first = client.post(
            "/api/fitness/workouts",
            json={
                "date": yesterday.isoformat(),
                "type": "tirage",
                "duration_min": 40,
                "source": "pwa",
            },
        )
        second = client.post(
            "/api/fitness/workouts",
            json={
                "date": today.isoformat(),
                "type": "jambes",
                "exercises_json": [{"name": "Squat", "sets": 4, "reps": 8}],
                "duration_min": 55,
                "source": "pwa",
            },
        )
        history = client.get(
            "/api/fitness/workouts",
            params={"from": today.isoformat(), "to": today.isoformat()},
        )

    assert first.status_code == 201
    assert second.status_code == 201
    assert second.json()["type"] == "jambes"
    assert second.json()["exercises_json"][0]["name"] == "Squat"
    assert history.status_code == 200
    assert [item["type"] for item in history.json()["workouts"]] == ["jambes"]


def test_meals_create_and_read_for_date(fitness_db: Path) -> None:
    today = datetime.now(ZoneInfo("Europe/Paris")).date().isoformat()
    with _client() as client:
        authenticate(client)
        created = client.post(
            "/api/fitness/meals",
            json={
                "date": today,
                "meal_type": "dejeuner",
                "description": "Poulet, riz et légumes",
                "calories_estimate": 620,
                "source": "pwa",
            },
        )
        meals = client.get("/api/fitness/meals", params={"date": today})

    assert created.status_code == 201
    assert created.json()["calories_estimate"] == 620
    assert meals.status_code == 200
    assert meals.json()["meals"][0]["description"] == "Poulet, riz et légumes"


def test_water_is_incremental_and_today_returns_total(fitness_db: Path) -> None:
    today = datetime.now(ZoneInfo("Europe/Paris")).date().isoformat()
    with _client() as client:
        authenticate(client)
        first = client.post(
            "/api/fitness/water",
            json={"date": today, "amount_ml": 250, "source": "pwa"},
        )
        second = client.post(
            "/api/fitness/water",
            json={"date": today, "amount_ml": 500, "source": "pwa"},
        )
        total = client.get("/api/fitness/water/today")

    assert first.status_code == 201
    assert first.json()["total_today_ml"] == 250
    assert second.status_code == 201
    assert second.json()["total_today_ml"] == 750
    assert total.status_code == 200
    assert total.json() == {"date": today, "amount_ml": 750}


def test_wellbeing_create_and_range_history(fitness_db: Path) -> None:
    today = datetime.now(ZoneInfo("Europe/Paris")).date().isoformat()
    with _client() as client:
        authenticate(client)
        created = client.post(
            "/api/fitness/wellbeing",
            json={
                "date": today,
                "rating": 8,
                "journal_text": "Bonne énergie, sommeil un peu court.",
                "source": "pwa",
            },
        )
        history = client.get(
            "/api/fitness/wellbeing",
            params={"from": today, "to": today},
        )

    assert created.status_code == 201
    assert created.json()["rating"] == 8
    assert history.status_code == 200
    assert history.json()["wellbeing"][0]["journal_text"].startswith("Bonne énergie")


def test_today_summary_aggregates_all_domains(fitness_db: Path) -> None:
    today = datetime.now(ZoneInfo("Europe/Paris")).date().isoformat()
    with _client() as client:
        authenticate(client)
        assert (
            client.post(
                "/api/fitness/workouts",
                json={
                    "date": today,
                    "type": "natation",
                    "duration_min": 30,
                    "source": "pwa",
                },
            ).status_code
            == 201
        )
        assert (
            client.post(
                "/api/fitness/meals",
                json={
                    "date": today,
                    "meal_type": "diner",
                    "description": "Pâtes",
                    "calories_estimate": 700,
                    "source": "pwa",
                },
            ).status_code
            == 201
        )
        assert (
            client.post(
                "/api/fitness/water",
                json={"date": today, "amount_ml": 1000, "source": "pwa"},
            ).status_code
            == 201
        )
        assert (
            client.post(
                "/api/fitness/wellbeing",
                json={"date": today, "rating": 7, "source": "pwa"},
            ).status_code
            == 201
        )
        assert (
            client.post(
                "/api/fitness/wellbeing",
                json={
                    "date": today,
                    "journal_text": "Énergie stable.",
                    "source": "pwa",
                },
            ).status_code
            == 201
        )

        summary = client.get("/api/fitness/summary/today")

    assert summary.status_code == 200
    assert summary.json() == {
        "date": today,
        "workout_done": True,
        "workout_count": 1,
        "meal_count": 1,
        "calories_estimate": 700,
        "water_ml": 1000,
        "wellbeing": {"rating": 7, "journal_text": "Énergie stable."},
    }


@pytest.mark.parametrize(
    ("path", "payload"),
    [
        (
            "/api/fitness/workouts",
            {"date": "2026-07-30", "type": "dos", "source": "pwa"},
        ),
        (
            "/api/fitness/meals",
            {"date": "30/07/2026", "description": "Salade", "source": "pwa"},
        ),
        (
            "/api/fitness/water",
            {"date": "2026-07-30", "amount_ml": -250, "source": "pwa"},
        ),
        (
            "/api/fitness/wellbeing",
            {"date": "2026-07-30", "rating": 11, "source": "pwa"},
        ),
    ],
)
def test_invalid_payloads_return_422(
    fitness_db: Path,
    path: str,
    payload: dict[str, object],
) -> None:
    with _client() as client:
        authenticate(client)
        response = client.post(path, json=payload)

    assert response.status_code == 422


def test_invalid_date_filters_return_422(fitness_db: Path) -> None:
    with _client() as client:
        authenticate(client)
        malformed = client.get("/api/fitness/workouts", params={"from": "hier"})
        reversed_range = client.get(
            "/api/fitness/wellbeing",
            params={"from": "2026-07-31", "to": "2026-07-30"},
        )

    assert malformed.status_code == 422
    assert reversed_range.status_code == 422
