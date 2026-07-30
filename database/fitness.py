"""Persistance SQLite exclusivement propriétaire des données fitness."""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

from .core import get_db


def _decode_row(row: Any, *, exercises: bool = False) -> dict[str, Any]:
    """Convertit une ligne SQLite en valeurs Python strictement typées."""
    result = dict(row)
    result["date"] = date.fromisoformat(result["date"])
    result["created_at"] = datetime.fromisoformat(result["created_at"])
    if exercises:
        raw = result.get("exercises_json")
        result["exercises_json"] = json.loads(raw) if raw else None
    return result


def create_workout(
    *,
    log_date: str,
    workout_type: str,
    exercises_json: list[dict[str, Any]] | None,
    duration_min: int | None,
    source: str,
) -> dict[str, Any]:
    """Insère une séance et retourne la ligne créée."""
    encoded_exercises = (
        json.dumps(exercises_json, ensure_ascii=False, separators=(",", ":"))
        if exercises_json is not None
        else None
    )
    with get_db() as conn:
        cursor = conn.execute(
            """
            INSERT INTO workouts (
                date, type, exercises_json, duration_min, source
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (log_date, workout_type, encoded_exercises, duration_min, source),
        )
        row = conn.execute(
            "SELECT * FROM workouts WHERE id = ?",
            (int(cursor.lastrowid),),
        ).fetchone()
    return _decode_row(row, exercises=True)


def list_workouts(
    *,
    from_date: str | None = None,
    to_date: str | None = None,
) -> list[dict[str, Any]]:
    """Liste les séances dans une plage inclusive."""
    clauses: list[str] = []
    params: list[str] = []
    if from_date is not None:
        clauses.append("date >= ?")
        params.append(from_date)
    if to_date is not None:
        clauses.append("date <= ?")
        params.append(to_date)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with get_db() as conn:
        rows = conn.execute(
            f"SELECT * FROM workouts {where} ORDER BY date DESC, id DESC",
            tuple(params),
        ).fetchall()
    return [_decode_row(row, exercises=True) for row in rows]


def create_meal(
    *,
    log_date: str,
    meal_type: str | None,
    description: str,
    calories_estimate: int | None,
    source: str,
) -> dict[str, Any]:
    """Insère un repas et retourne la ligne créée."""
    with get_db() as conn:
        cursor = conn.execute(
            """
            INSERT INTO meals (
                date, meal_type, description, calories_estimate, source
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (log_date, meal_type, description, calories_estimate, source),
        )
        row = conn.execute(
            "SELECT * FROM meals WHERE id = ?",
            (int(cursor.lastrowid),),
        ).fetchone()
    return _decode_row(row)


def list_meals_for_date(log_date: str) -> list[dict[str, Any]]:
    """Liste les repas d'une date."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM meals WHERE date = ? ORDER BY id DESC",
            (log_date,),
        ).fetchall()
    return [_decode_row(row) for row in rows]


def create_water_intake(
    *,
    log_date: str,
    amount_ml: int,
    source: str,
) -> dict[str, Any]:
    """Insère un ajout d'eau et retourne la ligne créée."""
    with get_db() as conn:
        cursor = conn.execute(
            "INSERT INTO water_intake (date, amount_ml, source) VALUES (?, ?, ?)",
            (log_date, amount_ml, source),
        )
        row = conn.execute(
            "SELECT * FROM water_intake WHERE id = ?",
            (int(cursor.lastrowid),),
        ).fetchone()
    return _decode_row(row)


def get_water_total(log_date: str) -> int:
    """Retourne le cumul d'eau d'une date."""
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT COALESCE(SUM(amount_ml), 0)
            FROM water_intake
            WHERE date = ?
            """,
            (log_date,),
        ).fetchone()
    return int(row[0])


def create_wellbeing_log(
    *,
    log_date: str,
    rating: int | None,
    journal_text: str | None,
    source: str,
) -> dict[str, Any]:
    """Insère une note ou entrée de journal de bien-être."""
    with get_db() as conn:
        cursor = conn.execute(
            """
            INSERT INTO wellbeing_logs (
                date, rating, journal_text, source
            ) VALUES (?, ?, ?, ?)
            """,
            (log_date, rating, journal_text, source),
        )
        row = conn.execute(
            "SELECT * FROM wellbeing_logs WHERE id = ?",
            (int(cursor.lastrowid),),
        ).fetchone()
    return _decode_row(row)


def list_wellbeing_logs(
    *,
    from_date: str | None = None,
    to_date: str | None = None,
) -> list[dict[str, Any]]:
    """Liste les logs de bien-être dans une plage inclusive."""
    clauses: list[str] = []
    params: list[str] = []
    if from_date is not None:
        clauses.append("date >= ?")
        params.append(from_date)
    if to_date is not None:
        clauses.append("date <= ?")
        params.append(to_date)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with get_db() as conn:
        rows = conn.execute(
            f"SELECT * FROM wellbeing_logs {where} ORDER BY date DESC, id DESC",
            tuple(params),
        ).fetchall()
    return [_decode_row(row) for row in rows]


def get_today_summary(log_date: str) -> dict[str, Any]:
    """Agrège les quatre domaines fitness pour une date."""
    with get_db() as conn:
        workout_count = int(
            conn.execute(
                "SELECT COUNT(*) FROM workouts WHERE date = ?",
                (log_date,),
            ).fetchone()[0]
        )
        meal_row = conn.execute(
            """
            SELECT COUNT(*), COALESCE(SUM(calories_estimate), 0)
            FROM meals
            WHERE date = ?
            """,
            (log_date,),
        ).fetchone()
        water_ml = int(
            conn.execute(
                "SELECT COALESCE(SUM(amount_ml), 0) FROM water_intake WHERE date = ?",
                (log_date,),
            ).fetchone()[0]
        )
        rating_row = conn.execute(
            """
            SELECT rating
            FROM wellbeing_logs
            WHERE date = ? AND rating IS NOT NULL
            ORDER BY id DESC
            LIMIT 1
            """,
            (log_date,),
        ).fetchone()
        journal_row = conn.execute(
            """
            SELECT journal_text
            FROM wellbeing_logs
            WHERE date = ? AND journal_text IS NOT NULL
            ORDER BY id DESC
            LIMIT 1
            """,
            (log_date,),
        ).fetchone()

    wellbeing = None
    if rating_row is not None or journal_row is not None:
        wellbeing = {
            "rating": rating_row["rating"] if rating_row is not None else None,
            "journal_text": (
                journal_row["journal_text"] if journal_row is not None else None
            ),
        }

    return {
        "date": date.fromisoformat(log_date),
        "workout_done": workout_count > 0,
        "workout_count": workout_count,
        "meal_count": int(meal_row[0]),
        "calories_estimate": int(meal_row[1]),
        "water_ml": water_ml,
        "wellbeing": wellbeing,
    }
