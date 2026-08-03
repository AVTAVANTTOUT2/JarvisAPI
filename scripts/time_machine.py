"""Machine à remonter le temps — reconstruction chronologique d'une journée.

Assemble tout ce que JARVIS sait réellement d'une date donnée : messages
échangés, tâches terminées, lieux visités, humeur enregistrée, moments
notables captés à l'écran, et l'entrée du journal de JARVIS ce jour-là.

Explicitement **sans** photos, appels téléphoniques, historique musical ou
navigation web — aucune de ces sources n'est collectée par JARVIS. Seules
les données réellement en base sont utilisées, jamais de reconstruction
inventée pour combler les trous.
"""

from __future__ import annotations

from database.time_buckets import sqlite_utc_to_local, utc_bounds_for_local_day


def build_day_timeline(date: str) -> dict:
    """Reconstruction chronologique d'une date (``YYYY-MM-DD``) — SQL pur."""
    from database import get_db, get_jarvis_journal_entry

    events: list[dict] = []
    start_utc, end_utc = utc_bounds_for_local_day(date)

    with get_db() as conn:
        msg_count = conn.execute(
            "SELECT COUNT(*) FROM messages WHERE created_at >= ? AND created_at < ?",
            (start_utc, end_utc),
        ).fetchone()[0]

        tasks_done = conn.execute(
            "SELECT title, completed_at FROM tasks WHERE status = 'done' AND DATE(completed_at) = ? "
            "ORDER BY completed_at",
            (date,),
        ).fetchall()
        for t in tasks_done:
            events.append({
                "time": (t["completed_at"] or "")[11:16],
                "type": "task_done",
                "description": f"Tâche terminée : {t['title']}",
            })

        visits = conn.execute(
            """SELECT p.name AS place_name, v.arrived_at, v.departed_at, v.duration_min
               FROM visits v JOIN places p ON p.id = v.place_id
               WHERE DATE(v.arrived_at) = ? ORDER BY v.arrived_at""",
            (date,),
        ).fetchall()
        for v in visits:
            desc = f"Visite à {v['place_name']}"
            if v["duration_min"]:
                desc += f" ({round(v['duration_min'])} min)"
            events.append({
                "time": (v["arrived_at"] or "")[11:16],
                "type": "visit",
                "description": desc,
            })

        moods = conn.execute(
            "SELECT mood_score, energy_level, context, created_at FROM mood_log "
            "WHERE created_at >= ? AND created_at < ? ORDER BY created_at",
            (start_utc, end_utc),
        ).fetchall()
        for m in moods:
            desc = f"Humeur {m['mood_score']}/10, énergie {m['energy_level']}/10"
            if m["context"]:
                desc += f" — {m['context']}"
            events.append({
                "time": sqlite_utc_to_local(m["created_at"]).strftime("%H:%M"),
                "type": "mood",
                "description": desc,
            })

        notable = conn.execute(
            "SELECT app, notable, created_at FROM screen_activity "
            "WHERE created_at >= ? AND created_at < ? "
            "AND notable IS NOT NULL AND notable != '' ORDER BY created_at",
            (start_utc, end_utc),
        ).fetchall()
        for n in notable:
            events.append({
                "time": sqlite_utc_to_local(n["created_at"]).strftime("%H:%M"),
                "type": "screen_notable",
                "description": f"[{n['app']}] {n['notable']}",
            })

    events.sort(key=lambda e: e["time"] or "")
    journal = get_jarvis_journal_entry(date)

    return {
        "date": date,
        "timeline": events,
        "journal_entry": journal["entry"] if journal else None,
        "summary": {
            "messages": msg_count,
            "tasks_done": len(tasks_done),
            "visits": len(visits),
            "mood_entries": len(moods),
            "notable_moments": len(notable),
        },
    }
