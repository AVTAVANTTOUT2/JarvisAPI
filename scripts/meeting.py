"""Auto-résumé de réunions vocales — micro capté → résumé + actions.

Opt-in (``MEETING_CAPTURE_ENABLED=false`` par défaut) : le daemon audio pousse
chaque transcription ambiante dans ``meeting_tracker.add_utterance()``. Quand
la parole cumulée dépasse ``MEETING_MIN_SPEECH_S`` dans une fenêtre de
``MEETING_WINDOW_MIN`` minutes, une réunion s'ouvre. Un silence de
``MEETING_SILENCE_MIN`` minutes la clôt : DeepSeek produit alors un résumé et
des actions, persistés dans la table ``recordings`` (label « réunion ») avec
une tâche créée par action et une notification.

Zéro audio conservé — uniquement le texte transcrit par le STT local.
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime

import config
import llm
from database import create_notification, create_task, save_recording

logger = logging.getLogger(__name__)


class MeetingTracker:
    """Machine à états : buffer glissant → réunion ouverte → clôture au silence."""

    def __init__(self) -> None:
        self.active: bool = False
        self.buffer: list[tuple[float, str, float]] = []  # (timestamp, texte, durée_s)
        self.last_speech: float = 0.0
        self.started_at: float | None = None

    def add_utterance(self, text: str, duration_s: float, now: float | None = None) -> str | None:
        """Ajoute une transcription ambiante. Retourne "started" à l'ouverture."""
        if not config.MEETING_CAPTURE_ENABLED or not text or not text.strip():
            return None
        now = now or time.time()
        if not self.active:
            # fenêtre glissante : on oublie ce qui est trop vieux
            horizon = now - config.MEETING_WINDOW_MIN * 60
            self.buffer = [u for u in self.buffer if u[0] >= horizon]
        self.buffer.append((now, text.strip(), max(0.0, duration_s)))
        self.last_speech = now

        if not self.active:
            speech = sum(d for _, _, d in self.buffer)
            if speech >= config.MEETING_MIN_SPEECH_S:
                self.active = True
                self.started_at = self.buffer[0][0]
                logger.info(
                    "[meeting] Réunion détectée (%.0f min de parole cumulée)", speech / 60
                )
                return "started"
        return None

    def tick(self, now: float | None = None) -> dict | None:
        """Clôt la réunion après le silence requis. Retourne le paquet à résumer."""
        now = now or time.time()
        if not self.active:
            return None
        if now - self.last_speech < config.MEETING_SILENCE_MIN * 60:
            return None

        transcript = "\n".join(txt for _, txt, _ in self.buffer)
        meeting = {
            "started_at": datetime.fromtimestamp(self.started_at).strftime("%Y-%m-%d %H:%M:%S"),
            "ended_at": datetime.fromtimestamp(self.last_speech).strftime("%Y-%m-%d %H:%M:%S"),
            "duration_seconds": int(self.last_speech - self.started_at),
            "utterances": len(self.buffer),
            "transcript": transcript,
        }
        self.active = False
        self.buffer = []
        self.started_at = None
        logger.info(
            "[meeting] Réunion close — %d prises de parole, %d min",
            meeting["utterances"], meeting["duration_seconds"] // 60,
        )
        return meeting


def _parse_json_tolerant(raw: str) -> dict | None:
    """JSON brut, bloc ```json, ou JSON noyé dans du texte."""
    for candidate in (raw, ):
        try:
            return json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            pass
    m = re.search(r"\{.*\}", raw or "", re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
    return None


async def summarize_meeting(meeting: dict) -> dict:
    """Résumé + actions d'une réunion close. Persiste et notifie."""
    transcript = meeting.get("transcript", "")[:12000]
    title = f"Réunion du {meeting['started_at'][:16]}"
    summary = ""
    actions: list[dict] = []

    try:
        result = await llm.chat(
            messages=[{"role": "user", "content": transcript}],
            model=config.DEEPSEEK_MAIN_MODEL,
            system=(
                "Voici la transcription brute d'une réunion captée au micro "
                "(plusieurs interlocuteurs mélangés, sans attribution). Réponds "
                "UNIQUEMENT en JSON : {\"title\": \"titre 5 mots max\", "
                "\"summary\": \"résumé factuel en 4 phrases max\", "
                "\"actions\": [{\"title\": \"action concrète\", \"due_hint\": \"échéance si mentionnée ou null\"}]}. "
                "N'invente rien : uniquement ce qui est dit. Actions = engagements "
                "explicites uniquement, 5 max."
            ),
            max_tokens=500,
            temperature=0.2,
        )
        data = _parse_json_tolerant(result["content"]) or {}
        title = data.get("title") or title
        summary = data.get("summary") or ""
        actions = [a for a in (data.get("actions") or []) if isinstance(a, dict) and a.get("title")]
    except Exception as e:
        logger.warning("[meeting] résumé LLM indisponible : %s", e)
        summary = f"Réunion de {meeting['duration_seconds'] // 60} min captée. Transcription conservée, résumé indisponible."

    tasks_created = []
    for a in actions[:5]:
        try:
            create_task(title=a["title"][:200], category="reunion", priority="medium")
            tasks_created.append(a["title"])
        except Exception as e:
            logger.error("[meeting] create_task : %s", e)

    try:
        save_recording(
            conversation_id=None,
            label="réunion",
            title=title,
            duration_seconds=meeting["duration_seconds"],
            transcription=meeting.get("transcript", ""),
            summary=summary,
            synthesis={"actions": actions},
            actions={"tasks_created": tasks_created},
            audio_size_kb=0,
        )
    except Exception as e:
        logger.error("[meeting] save_recording : %s", e)

    content = summary or title
    if tasks_created:
        content += f" — {len(tasks_created)} action(s) créée(s)."
    create_notification(source="system", title=title, content=content, priority="medium")
    logger.info("[meeting] %s — %d action(s)", title, len(tasks_created))
    return {"title": title, "summary": summary, "actions": tasks_created}


meeting_tracker = MeetingTracker()
