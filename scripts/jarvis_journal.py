"""Journal parallèle de JARVIS — une entrée par jour, écrite à sa propre voix.

Contrairement au journal de l'utilisateur (`agents/journal.py`), ce journal
est écrit DU POINT DE VUE DE JARVIS qui observe la journée de l'utilisateur :
tâches, messages, lieux visités, humeur si connue. Composé une fois par jour
(23:50 par défaut) à partir de données déjà en base — jamais de fait inventé,
le LLM ne fait que mettre en forme les chiffres fournis.
"""

from __future__ import annotations

import asyncio
import logging

import config
import llm
from database import (
    claim_job_run,
    complete_job_run,
    get_db,
    get_jarvis_journal_entry,
    release_job_run,
    upsert_jarvis_journal_entry,
)
from database.time_buckets import local_datetime, utc_bounds_for_local_day

logger = logging.getLogger(__name__)
_JOURNAL_LOCK = asyncio.Lock()

_SYSTEM_PROMPT = (
    "Tu es JARVIS, majordome IA britannique. Tu tiens un journal personnel, "
    "à TA voix, où tu notes ce que tu as observé de la journée de Monsieur. "
    "Ton sec, pince-sans-rire, INTERDIT : emoji, exclamation, flatterie. "
    "3 à 5 phrases, à la première personne ('J'ai remarqué que...', "
    "'Monsieur a...'). Base-toi uniquement sur les faits donnés, n'invente rien. "
    "Réponds en français."
)


def _today() -> str:
    return local_datetime().date().isoformat()


def _day_facts(date: str) -> dict:
    """Chiffres bruts de la journée — SQL pur, zéro LLM."""
    start_utc, end_utc = utc_bounds_for_local_day(date)
    with get_db() as conn:
        messages = conn.execute(
            "SELECT COUNT(*) FROM messages WHERE created_at >= ? AND created_at < ?",
            (start_utc, end_utc),
        ).fetchone()[0]
        tasks_done = [
            r["title"] for r in conn.execute(
                "SELECT title FROM tasks WHERE status = 'done' AND DATE(completed_at) = ?", (date,)
            )
        ]
        visits = [
            r["place_name"] for r in conn.execute(
                """SELECT p.name AS place_name FROM visits v
                   JOIN places p ON p.id = v.place_id
                   WHERE DATE(v.arrived_at) = ? ORDER BY v.arrived_at""",
                (date,),
            )
        ]
        mood_row = conn.execute(
            "SELECT mood_score, energy_level FROM mood_log "
            "WHERE created_at >= ? AND created_at < ? "
            "ORDER BY created_at DESC LIMIT 1",
            (start_utc, end_utc),
        ).fetchone()
        notable = [
            r["notable"] for r in conn.execute(
                "SELECT notable FROM screen_activity "
                "WHERE created_at >= ? AND created_at < ? "
                "AND notable IS NOT NULL AND notable != '' LIMIT 5",
                (start_utc, end_utc),
            )
        ]
    return {
        "date": date,
        "messages": messages,
        "tasks_done": tasks_done,
        "visits": visits,
        "mood": dict(mood_row) if mood_row else None,
        "notable": notable,
    }


def _facts_to_text(facts: dict) -> str:
    lines = [
        f"Messages échangés : {facts['messages']}",
        f"Tâches terminées : {facts['tasks_done'] or 'aucune'}",
        f"Lieux visités : {facts['visits'] or 'aucun (ou non suivi)'}",
    ]
    if facts["mood"]:
        lines.append(
            f"Humeur/énergie du jour : {facts['mood'].get('mood_score')}/10, "
            f"{facts['mood'].get('energy_level')}/10"
        )
    if facts["notable"]:
        lines.append(f"Faits notables observés à l'écran : {facts['notable']}")
    return "\n".join(lines)


async def generate_journal_entry(date: str | None = None) -> dict:
    """Compose l'entrée une seule fois par jour, concurrence comprise."""
    date = date or _today()
    async with _JOURNAL_LOCK:
        existing = get_jarvis_journal_entry(date)
        if existing and existing.get("entry"):
            return {
                "date": date,
                "entry": existing["entry"],
                "facts": _day_facts(date),
                "cached": True,
            }
        claim = claim_job_run("jarvis_journal", date)
        if claim is None:
            existing = get_jarvis_journal_entry(date) or {}
            return {
                "date": date,
                "entry": existing.get("entry"),
                "facts": _day_facts(date),
                "cached": True,
                "running": True,
            }
        try:
            result = await _generate_journal_entry(date)
            complete_job_run(claim)
            result["cached"] = False
            return result
        except BaseException:
            release_job_run(claim)
            raise


async def _generate_journal_entry(date: str) -> dict:
    """Génère puis persiste l'entrée après acquisition du claim quotidien."""
    facts = _day_facts(date)
    facts_text = _facts_to_text(facts)

    try:
        result = await llm.chat(
            messages=[{"role": "user", "content": facts_text}],
            model=config.DEEPSEEK_MAIN_MODEL,
            system=_SYSTEM_PROMPT,
            max_tokens=250,
            temperature=0.6,
        )
        entry = result["content"].strip()
    except Exception as e:
        logger.warning("[jarvis_journal] LLM indisponible : %s", e)
        entry = (
            f"Journée du {date} consignée sans commentaire : {facts['messages']} échange(s), "
            f"{len(facts['tasks_done'])} tâche(s) menée(s) à terme."
        )

    upsert_jarvis_journal_entry(date, entry)
    logger.info("[jarvis_journal] entrée du %s générée", date)
    return {"date": date, "entry": entry, "facts": facts}
