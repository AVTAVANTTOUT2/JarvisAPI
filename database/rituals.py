"""Rituels, engagements, présence, journal et scores quotidiens."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from .core import get_db
from .settings import get_setting, set_setting
from .stats import get_daily_activity_stats


def set_daily_ritual(date: str, field: str, value: Any) -> None:
    """UPSERT d'un champ du rituel quotidien (roast/debrief/quote/score…)."""
    allowed = {"roast", "debrief", "quote", "productivity_score", "score_detail", "weekly_debrief"}
    if field not in allowed:
        raise ValueError(f"champ rituel invalide : {field}")
    with get_db() as conn:
        conn.execute(
            f"""INSERT INTO daily_rituals (date, {field}) VALUES (?, ?)
                ON CONFLICT(date) DO UPDATE SET
                    {field} = excluded.{field},
                    updated_at = CURRENT_TIMESTAMP""",  # noqa: S608 — champ whitelisté
            (date, value),
        )


def get_daily_ritual(date: str) -> dict | None:
    """Retourne la ligne de rituels du jour demandé, ou None."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM daily_rituals WHERE date = ?", (date,)
        ).fetchone()
        return dict(row) if row else None


def get_todays_birthdays(today_mm_dd: str | None = None) -> list[dict]:
    """Contacts dont l'anniversaire tombe aujourd'hui.

    ``people.birthday`` accepte 'YYYY-MM-DD' (âge calculable) ou 'MM-DD'.
    """
    mm_dd = today_mm_dd or datetime.now().strftime("%m-%d")
    with get_db() as conn:
        rows = conn.execute(
            """SELECT id, name, relationship, birthday FROM people
               WHERE birthday IS NOT NULL AND birthday != ''
                 AND (
                     substr(birthday, 6, 5) = ?  -- format YYYY-MM-DD
                     OR birthday = ?             -- format MM-DD
                 )""",
            (mm_dd, mm_dd),
        ).fetchall()
        return [dict(r) for r in rows]


def upsert_mood_signal(date: str, signal: dict) -> None:
    """UPSERT du signal comportemental du jour."""
    with get_db() as conn:
        conn.execute(
            """INSERT INTO mood_signals
                   (date, msg_count, msg_avg_14d, deviation_pct, voice_count,
                    screen_minutes, late_night_points, flags)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(date) DO UPDATE SET
                   msg_count = excluded.msg_count,
                   msg_avg_14d = excluded.msg_avg_14d,
                   deviation_pct = excluded.deviation_pct,
                   voice_count = excluded.voice_count,
                   screen_minutes = excluded.screen_minutes,
                   late_night_points = excluded.late_night_points,
                   flags = excluded.flags""",
            (
                date,
                signal.get("msg_count", 0),
                signal.get("msg_avg_14d", 0.0),
                signal.get("deviation_pct"),
                signal.get("voice_count", 0),
                signal.get("screen_minutes", 0.0),
                signal.get("late_night_points", 0),
                signal.get("flags"),
            ),
        )


def get_mood_signals(days: int = 14) -> list[dict]:
    """Signaux comportementaux des `days` derniers jours (récent en premier)."""
    days = max(1, min(days, 90))
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM mood_signals ORDER BY date DESC LIMIT ?", (days,)
        ).fetchall()
        return [dict(r) for r in rows]


def add_running_gag(person_id: int, gag: str) -> bool:
    """Ajoute une blague récurrente à un contact (dédup, cap à 15, FIFO)."""
    gag = (gag or "").strip()
    if not gag:
        return False
    with get_db() as conn:
        row = conn.execute(
            "SELECT running_gags FROM people WHERE id = ?", (person_id,)
        ).fetchone()
        if row is None:
            return False
        try:
            gags = json.loads(row[0]) if row[0] else []
        except json.JSONDecodeError:
            gags = []
        low = gag.lower()
        if any(low == g.lower() for g in gags):
            return False
        gags.append(gag)
        gags = gags[-15:]
        conn.execute(
            "UPDATE people SET running_gags = ? WHERE id = ?",
            (json.dumps(gags, ensure_ascii=False), person_id),
        )
        return True


def get_running_gags(person_id: int) -> list[str]:
    """Blagues récurrentes d'un contact."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT running_gags FROM people WHERE id = ?", (person_id,)
        ).fetchone()
    if not row or not row[0]:
        return []
    try:
        return json.loads(row[0])
    except json.JSONDecodeError:
        return []


def add_commitment(content: str, made_to: str | None = None,
                   due_hint: str | None = None, source: str = "conversation") -> int | None:
    """Enregistre un engagement. Dédup sur le contenu des engagements ouverts."""
    content = (content or "").strip()
    if not content:
        return None
    with get_db() as conn:
        dup = conn.execute(
            "SELECT id FROM commitments WHERE status = 'open' AND LOWER(content) = LOWER(?)",
            (content,),
        ).fetchone()
        if dup:
            return None
        cur = conn.execute(
            "INSERT INTO commitments (content, made_to, due_hint, source) VALUES (?, ?, ?, ?)",
            (content, made_to, due_hint, source),
        )
        return cur.lastrowid


def get_commitments(status: str = "open", limit: int = 50) -> list[dict]:
    """Engagements par statut (open/kept/dropped), le plus ancien en premier."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM commitments WHERE status = ? ORDER BY created_at ASC LIMIT ?",
            (status, max(1, min(limit, 200))),
        ).fetchall()
        return [dict(r) for r in rows]


def update_commitment_status(commitment_id: int, status: str) -> bool:
    """Marque un engagement tenu ('kept') ou abandonné ('dropped')."""
    if status not in ("open", "kept", "dropped"):
        raise ValueError(f"statut invalide : {status}")
    with get_db() as conn:
        cur = conn.execute(
            """UPDATE commitments
               SET status = ?, resolved_at = CASE WHEN ? = 'open' THEN NULL ELSE CURRENT_TIMESTAMP END
               WHERE id = ?""",
            (status, status, commitment_id),
        )
        return cur.rowcount > 0


def get_overdue_commitments(days: int = 3) -> list[dict]:
    """Engagements encore ouverts depuis plus de `days` jours."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM commitments WHERE status = 'open' AND created_at < datetime('now', ?) "
            "ORDER BY created_at ASC",
            (f"-{int(days)} days",),
        ).fetchall()
        return [dict(r) for r in rows]


def set_dnd(minutes: int) -> str:
    """Active le DND pour `minutes`. Retourne l'heure de fin (ISO locale)."""
    from datetime import timedelta

    until = (datetime.now() + timedelta(minutes=max(1, minutes))).isoformat(timespec="seconds")
    set_setting("dnd_until", until)
    return until


def clear_dnd() -> None:
    set_setting("dnd_until", "")


def get_dnd_status() -> dict:
    until = get_setting("dnd_until", "")
    active = bool(until) and until > datetime.now().isoformat(timespec="seconds")
    return {"active": active, "until": until or None}


def is_dnd_active() -> bool:
    """True si le mode silence total est en cours. Seul l'urgent passe."""
    try:
        return get_dnd_status()["active"]
    except Exception:
        return False


def open_presence_session(arrived_at: str) -> int:
    """Ouvre une session de présence. Retourne son id."""
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO presence_sessions (arrived_at) VALUES (?)", (arrived_at,)
        )
        return cur.lastrowid


def close_presence_session(session_id: int, left_at: str) -> None:
    """Ferme une session de présence et calcule sa durée en minutes."""
    with get_db() as conn:
        conn.execute(
            """UPDATE presence_sessions
               SET left_at = ?,
                   duration_min = ROUND((julianday(?) - julianday(arrived_at)) * 1440, 1)
               WHERE id = ? AND left_at IS NULL""",
            (left_at, left_at, session_id),
        )


def get_week_comparison() -> dict:
    """Comparatif toi vs toi : 7 derniers jours vs les 7 précédents. Ton neutre.

    Chiffres bruts + variation en % — aucune interprétation.
    """
    from datetime import timedelta

    daily = get_daily_activity_stats(14)
    prev_days, cur_days = daily[:7], daily[7:]

    def _sum(days: list[dict]) -> dict:
        return {
            "messages": sum(d["msg_count"] for d in days),
            "voice": sum(d["voice_count"] for d in days),
            "tokens": sum(d["tokens_in"] + d["tokens_out"] for d in days),
            "cost": round(sum(d["cost"] for d in days), 4),
        }

    cur, prev = _sum(cur_days), _sum(prev_days)

    today = datetime.now().date()
    cur_start = (today - timedelta(days=6)).isoformat()
    prev_start = (today - timedelta(days=13)).isoformat()
    with get_db() as conn:
        cur["tasks_done"] = conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE status = 'done' AND DATE(completed_at) >= ?",
            (cur_start,)).fetchone()[0]
        prev["tasks_done"] = conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE status = 'done' AND DATE(completed_at) >= ? "
            "AND DATE(completed_at) < ?", (prev_start, cur_start)).fetchone()[0]
        cur["screen_minutes"] = round((conn.execute(
            "SELECT COALESCE(SUM(duration_seconds), 0) FROM app_usage WHERE date >= ?",
            (cur_start,)).fetchone()[0] or 0) / 60, 1)
        prev["screen_minutes"] = round((conn.execute(
            "SELECT COALESCE(SUM(duration_seconds), 0) FROM app_usage WHERE date >= ? AND date < ?",
            (prev_start, cur_start)).fetchone()[0] or 0) / 60, 1)

    def _pct(c: float, p: float) -> float | None:
        if p <= 0:
            return None
        return round((c - p) / p * 100, 1)

    deltas = {k: _pct(cur[k], prev[k]) for k in cur}
    return {
        "this_week": cur,
        "last_week": prev,
        "deltas_pct": deltas,
        "period": {"this_start": cur_start, "prev_start": prev_start},
    }


def upsert_jarvis_journal_entry(date: str, entry: str) -> None:
    with get_db() as conn:
        conn.execute(
            """INSERT INTO jarvis_journal (date, entry) VALUES (?, ?)
               ON CONFLICT(date) DO UPDATE SET entry = excluded.entry""",
            (date, entry),
        )


def get_jarvis_journal_entries(days: int = 7) -> list[dict]:
    """Entrées récentes (plus récente en premier)."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM jarvis_journal ORDER BY date DESC LIMIT ?",
            (max(1, min(days, 365)),),
        ).fetchall()
        return [dict(r) for r in rows]


def get_jarvis_journal_entry(date: str) -> dict | None:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM jarvis_journal WHERE date = ?", (date,)).fetchone()
        return dict(row) if row else None


def upsert_day_score(date: str, exceptional_score: int, luck_score: int, factors: dict) -> None:
    with get_db() as conn:
        conn.execute(
            """INSERT INTO day_scores (date, exceptional_score, luck_score, factors_json)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(date) DO UPDATE SET
                   exceptional_score = excluded.exceptional_score,
                   luck_score = excluded.luck_score,
                   factors_json = excluded.factors_json""",
            (date, exceptional_score, luck_score, json.dumps(factors, ensure_ascii=False)),
        )


def get_day_score(date: str) -> dict | None:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM day_scores WHERE date = ?", (date,)).fetchone()
        return dict(row) if row else None


def get_top_days(metric: str = "exceptional_score", limit: int = 10, days: int = 90) -> list[dict]:
    """Classement des meilleurs jours sur `metric` (exceptional_score ou luck_score)."""
    from datetime import timedelta

    if metric not in ("exceptional_score", "luck_score"):
        raise ValueError(f"métrique invalide : {metric}")
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    with get_db() as conn:
        rows = conn.execute(
            f"""SELECT * FROM day_scores WHERE date >= ? AND {metric} IS NOT NULL
                ORDER BY {metric} DESC LIMIT ?""",  # noqa: S608 — metric whitelisté ci-dessus
            (since, limit),
        ).fetchall()
        return [dict(r) for r in rows]
