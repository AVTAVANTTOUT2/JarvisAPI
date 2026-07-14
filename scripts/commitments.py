"""Traqueur de promesses non tenues — Memory flag les engagements oubliés.

Deux passes :
- ``extract_today_commitments()`` (22:40) : DeepSeek fast relit les messages
  utilisateur du jour et extrait les engagements EXPLICITES (« je t'envoie ça
  demain », « je m'en occupe », « promis je te rappelle ») → table
  ``commitments`` (dédup sur contenu ouvert).
- ``check_overdue_commitments_job()`` (10:00) : les engagements ouverts depuis
  plus de ``COMMITMENT_OVERDUE_DAYS`` jours remontent en notification, ton sec.

Résolution : PATCH /api/commitments/{id} (kept / dropped).
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime

import config
import llm
from database import (
    add_commitment,
    get_db,
    get_overdue_commitments,
)
from jarvis.notification_service import notification_service

logger = logging.getLogger(__name__)

COMMITMENT_OVERDUE_DAYS = 3
_MAX_MESSAGES = 80


def _todays_user_messages() -> list[str]:
    today = datetime.now().strftime("%Y-%m-%d")
    with get_db() as conn:
        rows = conn.execute(
            """SELECT content FROM messages
               WHERE role = 'user' AND DATE(created_at) = ?
               ORDER BY created_at ASC LIMIT ?""",
            (today, _MAX_MESSAGES),
        ).fetchall()
    return [r[0] for r in rows if r[0] and len(r[0]) > 10]


def _parse_json_tolerant(raw: str) -> list | None:
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else data.get("commitments")
    except (json.JSONDecodeError, TypeError, AttributeError):
        pass
    m = re.search(r"\[.*\]", raw or "", re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
    return None


async def extract_today_commitments() -> list[dict]:
    """Extrait les engagements explicites des messages du jour. Retourne les ajoutés."""
    messages = _todays_user_messages()
    if not messages:
        return []

    corpus = "\n".join(f"- {m[:300]}" for m in messages)
    try:
        result = await llm.chat(
            messages=[{"role": "user", "content": corpus}],
            model=config.DEEPSEEK_FAST_MODEL,
            system=(
                "Voici les messages écrits aujourd'hui par l'utilisateur. Extrais "
                "UNIQUEMENT ses engagements EXPLICITES envers quelqu'un ou envers "
                "lui-même : promesse d'envoyer, de faire, de rappeler, de rendre. "
                "Pas les intentions vagues, pas les questions. Réponds UNIQUEMENT "
                "en JSON : [{\"content\": \"l'engagement reformulé court\", "
                "\"made_to\": \"destinataire ou null\", \"due_hint\": \"échéance "
                "mentionnée ou null\"}]. Liste vide [] si aucun."
            ),
            max_tokens=300,
            temperature=0.0,
        )
        items = _parse_json_tolerant(result["content"]) or []
    except Exception as e:
        logger.warning("[commitments] extraction LLM indisponible : %s", e)
        return []

    added = []
    for item in items[:10]:
        if not isinstance(item, dict) or not item.get("content"):
            continue
        cid = add_commitment(
            content=item["content"],
            made_to=item.get("made_to"),
            due_hint=item.get("due_hint"),
            source="conversation",
        )
        if cid:
            added.append({"id": cid, **item})
    if added:
        logger.info("[commitments] %d engagement(s) extrait(s)", len(added))
    return added


def check_overdue_commitments_job() -> dict | None:
    """Notification sèche pour les engagements ouverts depuis > 3 jours."""
    overdue = get_overdue_commitments(COMMITMENT_OVERDUE_DAYS)
    if not overdue:
        return None

    today = datetime.now().strftime("%Y-%m-%d")
    title = f"Promesses en attente — {today}"
    with get_db() as conn:
        dup = conn.execute(
            "SELECT 1 FROM notifications WHERE title = ? LIMIT 1", (title,)
        ).fetchone()
    if dup:
        return None

    lines = [
        f"« {c['content']} »" + (f" (à {c['made_to']})" if c.get("made_to") else "")
        for c in overdue[:5]
    ]
    content = (
        f"{len(overdue)} engagement(s) pris et toujours en suspens, Monsieur : "
        + " ; ".join(lines)
        + ". Votre parole a une date de péremption."
    )
    notification_service.create(source="system", title=title, content=content, priority="medium")
    logger.info("[commitments] %d engagement(s) en souffrance notifiés", len(overdue))
    return {"overdue": len(overdue)}
