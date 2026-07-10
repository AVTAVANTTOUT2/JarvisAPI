"""Journal parallèle de JARVIS — une entrée par jour, écrite à sa propre voix.

Contrairement au journal de l'utilisateur (`agents/journal.py`), ce journal
est écrit DU POINT DE VUE DE JARVIS qui observe la journée de l'utilisateur :
tâches, messages, lieux visités, humeur si connue. Composé une fois par jour
(23:50 par défaut) à partir de données déjà en base — jamais de fait inventé,
le LLM ne fait que mettre en forme les chiffres fournis.
"""

from __future__ import annotations

import logging
from datetime import datetime

import config
import llm
from database import get_db, upsert_jarvis_journal_entry

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "Tu es JARVIS, majordome IA britannique. Tu tiens un journal personnel, "
    "à TA voix, où tu notes ce que tu as observé de la journée de Monsieur. "
    "Ton sec, pince-sans-rire, INTERDIT : emoji, exclamation, flatterie. "
    "3 à 5 phrases, à la première personne ('J'ai remarqué que...', "
    "'Monsieur a...'). Base-toi uniquement sur les faits donnés, n'invente rien. "
    "Réponds en français."
)


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _day_facts(date: str) -> dict:
    """Chiffres bruts de la journée — SQL pur, zéro LLM."""
    with get_db() as conn:
        messages = conn.execute(
            "SELECT COUNT(*) FROM messages WHERE DATE(created_at) = ?", (date,)
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
            "SELECT mood_score, energy_level FROM mood_log WHERE DATE(created_at) = ? "
            "ORDER BY created_at DESC LIMIT 1",
            (date,),
        ).fetchone()
        notable = [
            r["notable"] for r in conn.execute(
                "SELECT notable FROM screen_activity WHERE DATE(created_at) = ? "
                "AND notable IS NOT NULL AND notable != '' LIMIT 5",
                (date,),
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
    """Compose et persiste l'entrée du jour. Ne plante jamais (fallback texte)."""
    date = date or _today()
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
