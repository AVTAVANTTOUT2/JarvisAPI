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


def _decode_meal_row(row: Any) -> dict[str, Any]:
    """Convertit une ligne ``meals`` avec items JSON et drapeau photo."""
    result = _decode_row(row)
    raw_items = result.pop("items_json", None)
    try:
        result["items"] = json.loads(raw_items) if raw_items else None
    except (TypeError, json.JSONDecodeError):
        result["items"] = None
    photo_path = result.get("photo_path")
    result["has_photo"] = bool(photo_path)
    if result.get("analysis_source") in (None, ""):
        result["analysis_source"] = "manual"
    return result


def _decode_json(value: str | None, fallback: Any) -> Any:
    try:
        return json.loads(value) if value else fallback
    except (TypeError, json.JSONDecodeError):
        return fallback


def _decode_program_session(row: Any) -> dict[str, Any]:
    result = dict(row)
    result["active"] = bool(result["active"])
    result["warmup"] = _decode_json(result.pop("warmup_json", None), [])
    result["exercises"] = _decode_json(result.pop("exercises_json", None), [])
    result["stretches"] = _decode_json(result.pop("stretches_json", None), [])
    result.pop("program_id", None)
    result.pop("created_at", None)
    result.pop("updated_at", None)
    return result


def _decode_progress(row: Any) -> dict[str, Any]:
    result = dict(row)
    result["date"] = date.fromisoformat(result["date"])
    result["exercise_results"] = _decode_json(
        result.pop("exercise_results_json", None), []
    )
    result["completed_at"] = (
        datetime.fromisoformat(result["completed_at"])
        if result.get("completed_at")
        else None
    )
    result["updated_at"] = datetime.fromisoformat(result["updated_at"])
    result.pop("created_at", None)
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
    protein_g: float | None,
    source: str,
    carbs_g: float | None = None,
    fat_g: float | None = None,
    fiber_g: float | None = None,
    items: list[dict[str, Any]] | None = None,
    photo_path: str | None = None,
    analysis_source: str = "manual",
    confidence: float | None = None,
    raw_input: str | None = None,
) -> dict[str, Any]:
    """Insère un repas et retourne la ligne créée."""
    encoded_items = (
        json.dumps(items, ensure_ascii=False, separators=(",", ":"))
        if items is not None
        else None
    )
    with get_db() as conn:
        cursor = conn.execute(
            """
            INSERT INTO meals (
                date, meal_type, description, calories_estimate, protein_g,
                carbs_g, fat_g, fiber_g, items_json, photo_path,
                analysis_source, confidence, raw_input, source
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                log_date,
                meal_type,
                description,
                calories_estimate,
                protein_g,
                carbs_g,
                fat_g,
                fiber_g,
                encoded_items,
                photo_path,
                analysis_source,
                confidence,
                raw_input,
                source,
            ),
        )
        row = conn.execute(
            "SELECT * FROM meals WHERE id = ?",
            (int(cursor.lastrowid),),
        ).fetchone()
    return _decode_meal_row(row)


def get_meal(meal_id: int) -> dict[str, Any] | None:
    """Retourne un repas par identifiant, ou ``None``."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM meals WHERE id = ?",
            (meal_id,),
        ).fetchone()
    return _decode_meal_row(row) if row is not None else None


def list_meals_for_date(log_date: str) -> list[dict[str, Any]]:
    """Liste les repas d'une date."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM meals WHERE date = ? ORDER BY id DESC",
            (log_date,),
        ).fetchall()
    return [_decode_meal_row(row) for row in rows]


def has_meal_type(log_date: str, meal_type: str) -> bool:
    with get_db() as conn:
        return (
            conn.execute(
                "SELECT 1 FROM meals WHERE date = ? AND meal_type = ? LIMIT 1",
                (log_date, meal_type),
            ).fetchone()
            is not None
        )


def get_active_program() -> dict[str, Any]:
    """Retourne le programme actif avec toutes ses séances."""
    with get_db() as conn:
        program_row = conn.execute(
            "SELECT * FROM fitness_programs WHERE active = 1 ORDER BY id LIMIT 1"
        ).fetchone()
        if program_row is None:
            raise LookupError("Aucun programme fitness actif")
        session_rows = conn.execute(
            """
            SELECT * FROM fitness_program_sessions
            WHERE program_id = ? ORDER BY position, id
            """,
            (program_row["id"],),
        ).fetchall()

    program = dict(program_row)
    program["reminders_enabled"] = bool(program["reminders_enabled"])
    program["meal_tracking_enabled"] = bool(program["meal_tracking_enabled"])
    program["sessions"] = [_decode_program_session(row) for row in session_rows]
    program["updated_at"] = datetime.fromisoformat(program["updated_at"])
    for key in ("active", "created_at"):
        program.pop(key, None)
    return program


def update_active_program(values: dict[str, Any]) -> dict[str, Any]:
    """Met à jour uniquement les réglages explicitement autorisés."""
    allowed = {
        "name",
        "goal",
        "weekly_min_sessions",
        "calories_min",
        "calories_max",
        "protein_min_g",
        "protein_max_g",
        "reminders_enabled",
        "reminder_time",
        "reminder_interval_min",
        "meal_tracking_enabled",
    }
    clean = {key: value for key, value in values.items() if key in allowed}
    if not clean:
        return get_active_program()
    for key in ("reminders_enabled", "meal_tracking_enabled"):
        if key in clean:
            clean[key] = int(bool(clean[key]))
    assignments = ", ".join(f"{key} = ?" for key in clean)
    with get_db() as conn:
        row = conn.execute(
            "SELECT id, calories_min, calories_max, protein_min_g, protein_max_g "
            "FROM fitness_programs WHERE active = 1 ORDER BY id LIMIT 1"
        ).fetchone()
        if row is None:
            raise LookupError("Aucun programme fitness actif")
        merged = dict(row)
        merged.update(clean)
        if int(merged["calories_min"]) > int(merged["calories_max"]):
            raise ValueError("La cible calorique minimale dépasse la maximale")
        if int(merged["protein_min_g"]) > int(merged["protein_max_g"]):
            raise ValueError("La cible protéique minimale dépasse la maximale")
        conn.execute(
            f"UPDATE fitness_programs SET {assignments}, updated_at = datetime('now') WHERE id = ?",
            (*clean.values(), int(row["id"])),
        )
    return get_active_program()


def update_program_session(session_id: int, values: dict[str, Any]) -> dict[str, Any]:
    """Modifie une séance sans accepter de colonne arbitraire."""
    column_map = {
        "day_of_week": "day_of_week",
        "title": "title",
        "description": "description",
        "warmup": "warmup_json",
        "exercises": "exercises_json",
        "stretches": "stretches_json",
        "notes": "notes",
        "active": "active",
    }
    clean: dict[str, Any] = {}
    for key, value in values.items():
        column = column_map.get(key)
        if column is None:
            continue
        if key in {"warmup", "exercises", "stretches"}:
            value = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        elif key == "active":
            value = int(bool(value))
        clean[column] = value
    with get_db() as conn:
        if clean:
            assignments = ", ".join(f"{key} = ?" for key in clean)
            cursor = conn.execute(
                f"UPDATE fitness_program_sessions SET {assignments}, "
                "updated_at = datetime('now') WHERE id = ?",
                (*clean.values(), session_id),
            )
            if cursor.rowcount == 0:
                raise LookupError("Séance fitness introuvable")
            conn.execute(
                """
                UPDATE fitness_programs SET updated_at = datetime('now')
                WHERE id = (SELECT program_id FROM fitness_program_sessions WHERE id = ?)
                """,
                (session_id,),
            )
        row = conn.execute(
            "SELECT * FROM fitness_program_sessions WHERE id = ?", (session_id,)
        ).fetchone()
    if row is None:
        raise LookupError("Séance fitness introuvable")
    return _decode_program_session(row)


def get_program_session(session_id: int) -> dict[str, Any] | None:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM fitness_program_sessions WHERE id = ?", (session_id,)
        ).fetchone()
    return _decode_program_session(row) if row is not None else None


def get_scheduled_session(log_date: str) -> dict[str, Any] | None:
    weekday = date.fromisoformat(log_date).weekday()
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT s.* FROM fitness_program_sessions s
            JOIN fitness_programs p ON p.id = s.program_id
            WHERE p.active = 1 AND s.active = 1 AND s.day_of_week = ?
            ORDER BY s.position LIMIT 1
            """,
            (weekday,),
        ).fetchone()
    return _decode_program_session(row) if row is not None else None


def get_next_session(log_date: str) -> dict[str, Any] | None:
    """Retourne la prochaine séance après la date fournie dans le cycle hebdo."""
    weekday = date.fromisoformat(log_date).weekday()
    with get_db() as conn:
        rows = conn.execute("""
            SELECT s.* FROM fitness_program_sessions s
            JOIN fitness_programs p ON p.id = s.program_id
            WHERE p.active = 1 AND s.active = 1 ORDER BY s.day_of_week, s.position
            """).fetchall()
    if not rows:
        return None
    row = next((item for item in rows if int(item["day_of_week"]) > weekday), rows[0])
    return _decode_program_session(row)


def upsert_session_progress(
    *,
    session_id: int,
    log_date: str,
    status: str,
    exercise_results: list[dict[str, Any]],
    duration_min: int | None,
    perceived_effort: int | None,
    notes: str | None,
) -> dict[str, Any]:
    """Crée ou remplace l'état journalier d'une séance."""
    encoded = json.dumps(exercise_results, ensure_ascii=False, separators=(",", ":"))
    completed_at = (
        datetime.now().isoformat(timespec="seconds") if status == "done" else None
    )
    with get_db() as conn:
        if (
            conn.execute(
                "SELECT 1 FROM fitness_program_sessions WHERE id = ?", (session_id,)
            ).fetchone()
            is None
        ):
            raise LookupError("Séance fitness introuvable")
        conn.execute(
            """
            INSERT INTO fitness_session_progress (
                program_session_id, date, status, exercise_results_json,
                duration_min, perceived_effort, notes, completed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(program_session_id, date) DO UPDATE SET
                status = excluded.status,
                exercise_results_json = excluded.exercise_results_json,
                duration_min = excluded.duration_min,
                perceived_effort = excluded.perceived_effort,
                notes = excluded.notes,
                completed_at = excluded.completed_at,
                updated_at = datetime('now')
            """,
            (
                session_id,
                log_date,
                status,
                encoded,
                duration_min,
                perceived_effort,
                notes,
                completed_at,
            ),
        )
        row = conn.execute(
            """
            SELECT * FROM fitness_session_progress
            WHERE program_session_id = ? AND date = ?
            """,
            (session_id, log_date),
        ).fetchone()
    return _decode_progress(row)


def get_session_progress(session_id: int, log_date: str) -> dict[str, Any] | None:
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT * FROM fitness_session_progress
            WHERE program_session_id = ? AND date = ?
            """,
            (session_id, log_date),
        ).fetchone()
    return _decode_progress(row) if row is not None else None


def get_progress_for_date(log_date: str) -> list[dict[str, Any]]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM fitness_session_progress WHERE date = ? ORDER BY id",
            (log_date,),
        ).fetchall()
    return [_decode_progress(row) for row in rows]


def _completed_session_count(conn: Any, start: date, end: date) -> int:
    """Compte uniformément progression moderne et séances legacy sans doublon journalier."""
    start_value = start.isoformat()
    end_value = end.isoformat()
    planned_done = int(
        conn.execute(
            """
            SELECT COUNT(*) FROM fitness_session_progress
            WHERE date BETWEEN ? AND ? AND status = 'done'
            """,
            (start_value, end_value),
        ).fetchone()[0]
    )
    legacy_dates = int(
        conn.execute(
            """
            SELECT COUNT(DISTINCT date) FROM workouts
            WHERE date BETWEEN ? AND ?
              AND date NOT IN (
                SELECT date FROM fitness_session_progress
                WHERE date BETWEEN ? AND ? AND status = 'done'
              )
            """,
            (start_value, end_value, start_value, end_value),
        ).fetchone()[0]
    )
    return planned_done + legacy_dates


def weekly_done_count(log_date: str) -> int:
    target = date.fromisoformat(log_date)
    monday = target.fromordinal(target.toordinal() - target.weekday())
    sunday = target.fromordinal(monday.toordinal() + 6)
    with get_db() as conn:
        return _completed_session_count(conn, monday, sunday)


def current_week_streak(log_date: str, weekly_target: int) -> int:
    """Nombre de semaines pleines consécutives ayant atteint l'objectif."""
    target = date.fromisoformat(log_date)
    monday = target.fromordinal(target.toordinal() - target.weekday())
    streak = 0
    with get_db() as conn:
        for offset in range(1, 53):
            end = monday.fromordinal(monday.toordinal() - 7 * offset + 6)
            start = end.fromordinal(end.toordinal() - 6)
            count = _completed_session_count(conn, start, end)
            if count < weekly_target:
                break
            streak += 1
    return streak


def upsert_weight(
    *, log_date: str, weight_kg: float, notes: str | None, source: str
) -> dict[str, Any]:
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO fitness_weight_logs (date, weight_kg, notes, source)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(date) DO UPDATE SET
                weight_kg = excluded.weight_kg,
                notes = excluded.notes,
                source = excluded.source,
                created_at = datetime('now')
            """,
            (log_date, weight_kg, notes, source),
        )
        row = conn.execute(
            "SELECT * FROM fitness_weight_logs WHERE date = ?", (log_date,)
        ).fetchone()
    return _decode_row(row)


def list_weights(limit: int = 52) -> list[dict[str, Any]]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM fitness_weight_logs ORDER BY date DESC LIMIT ?", (limit,)
        ).fetchall()
    return [_decode_row(row) for row in rows]


def latest_weight() -> dict[str, Any] | None:
    rows = list_weights(limit=1)
    return rows[0] if rows else None


def get_last_prompt(date_value: str, kind: str, reference: str) -> datetime | None:
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT prompted_at FROM fitness_prompt_log
            WHERE date = ? AND kind = ? AND reference = ?
            ORDER BY prompted_at DESC LIMIT 1
            """,
            (date_value, kind, reference),
        ).fetchone()
    return datetime.fromisoformat(row["prompted_at"]) if row is not None else None


def record_prompt(
    date_value: str,
    kind: str,
    reference: str,
    prompted_at: datetime | None = None,
) -> None:
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO fitness_prompt_log (date, kind, reference, prompted_at)
            VALUES (?, ?, ?, COALESCE(?, datetime('now', 'localtime')))
            """,
            (
                date_value,
                kind,
                reference,
                (
                    prompted_at.replace(tzinfo=None).isoformat(timespec="seconds")
                    if prompted_at is not None
                    else None
                ),
            ),
        )


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
        legacy_workout_count = int(
            conn.execute(
                "SELECT COUNT(*) FROM workouts WHERE date = ?",
                (log_date,),
            ).fetchone()[0]
        )
        program_workout_count = int(
            conn.execute(
                """
                SELECT COUNT(*) FROM fitness_session_progress
                WHERE date = ? AND status = 'done'
                """,
                (log_date,),
            ).fetchone()[0]
        )
        workout_count = legacy_workout_count + program_workout_count
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
