"""Salutation quotidienne envoyée à la première connexion WebSocket."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from fastapi import WebSocket

import config
import llm
from database import get_recent_moods, get_tasks
from integrations import calendar_client, mail_client

BASE_DIR = Path(__file__).resolve().parent.parent
_WELCOME_MARKER = BASE_DIR / "data" / ".welcome_day"
logger = logging.getLogger("jarvis")



def _welcome_already_sent_today() -> bool:
    today = datetime.now().strftime("%Y-%m-%d")
    try:
        return _WELCOME_MARKER.read_text(encoding="utf-8").strip() == today
    except OSError:
        return False


def _mark_welcome_sent() -> None:
    try:
        _WELCOME_MARKER.parent.mkdir(parents=True, exist_ok=True)
        _WELCOME_MARKER.write_text(datetime.now().strftime("%Y-%m-%d"), encoding="utf-8")
    except OSError as e:
        logger.warning("welcome marker : %s", e)


async def _maybe_send_daily_welcome(ws: WebSocket) -> None:
    """Première connexion du jour : salutation contextualisée (Haiku)."""
    if _welcome_already_sent_today():
        return
    if not getattr(config, "DEEPSEEK_API_KEY", None):
        return
    try:
        moods = get_recent_moods(1)
        mood_line = ""
        if moods:
            m = moods[0]
            mood_line = (
                f"Dernier mood : {m.get('mood_score')}/10, énergie {m.get('energy_level')}/10."
            )
        h = datetime.now().hour
        if 5 <= h < 12:
            period = "matin"
        elif 12 <= h < 18:
            period = "après-midi"
        else:
            period = "soir"
        tasks = get_tasks()
        now = datetime.now()
        overdue_n = 0
        for t in tasks:
            dd = t.get("due_date")
            if not dd or t.get("status") == "done":
                continue
            try:
                ds = str(dd).replace("Z", "")
                if "T" in ds:
                    due = datetime.fromisoformat(ds[:19])
                else:
                    due = datetime.fromisoformat(ds[:10])
            except Exception:
                continue
            if due <= now:
                overdue_n += 1
        unread = 0
        if mail_client and mail_client.is_available():
            try:
                u = await mail_client.get_unread(80)
                unread = len(u or [])
            except Exception as e:
                logger.warning("welcome mail : %s", e)
        next_ev = ""
        if calendar_client and calendar_client.is_available():
            try:
                evs = await calendar_client.get_today_events()
                if evs:
                    e0 = evs[0]
                    next_ev = f"{e0.get('summary', '')} {e0.get('start', '')}"
            except Exception as e:
                logger.warning("welcome calendar : %s", e)
        prompt = (
            f"Une à deux phrases max. JARVIS, majordome britannique, français, "
            f"pas d'emoji, concis, poli. Période : {period}. {mood_line} "
            f"Tâches en retard (estimé) : {overdue_n}. Mails non lus (estimé) : {unread}. "
            f"Prochain événement aujourd’hui : {next_ev or 'aucun'}. "
            f"Utilisateur : {config.USER_NAME}."
        )
        r = await llm.chat(
            messages=[{"role": "user", "content": prompt}],
            model=config.DEEPSEEK_FAST_MODEL,
            system="Tu réponds uniquement par la salutation, sans balises.",
            max_tokens=120,
            temperature=0.7,
            use_cache=False,
        )
        text = (r.get("content") or "").strip()
        if text:
            await ws.send_json({"type": "welcome", "content": text})
            _mark_welcome_sent()
    except Exception as e:
        logger.exception("welcome message : %s", e)
