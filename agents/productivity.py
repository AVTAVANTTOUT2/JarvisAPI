"""Agent PRODUCTIVITÉ — emails, calendrier, tâches, briefings.

Enrichit le contexte avec les données live (Apple Mail, Calendar.app, météo) puis route
vers le modèle fast (résumé/triage rapide) ou le mode tâche lourde DeepSeek
(rédaction longue d'email, plan d'action détaillé) via `_route_task`.

Méthodes spéciales : `morning_briefing()` et `evening_summary()` pour les
résumés quotidiens (appelables via cron/launchd ou depuis l'UI).
"""

import asyncio
import logging
from datetime import datetime
from typing import AsyncGenerator

import config
import llm
from agents import BaseAgent
from agents.display_text import finalize_assistant_display_text
from database import (
    get_daily_messages,
    get_recent_email_summaries,
    get_tasks,
    get_unread_notifications,
    save_daily_briefing,
    save_message,
)
from integrations import calendar_client, mail_client, weather

logger = logging.getLogger(__name__)

STREAM_CHUNK_SIZE = 20


def _format_emails(emails: list[dict], max_items: int = 10) -> str:
    if not emails:
        return "(aucun email non lu)"
    lines = []
    for e in emails[:max_items]:
        sender = (e.get("from") or "?").split("<")[0].strip() or "?"
        snippet = (e.get("snippet") or "").replace("\n", " ")[:100]
        lines.append(f"- [{sender}] {e.get('subject', '(sans sujet)')} — {snippet}")
    return "\n".join(lines)


def _format_calendar(events: list[dict]) -> str:
    if not events:
        return "(agenda vide)"
    lines = []
    for ev in events:
        when = ev.get("start") or "?"
        loc = f" @ {ev['location']}" if ev.get("location") else ""
        lines.append(f"- {when} — {ev.get('summary', '(sans titre)')}{loc}")
    return "\n".join(lines)


def _format_weather(w: dict | None) -> str:
    if not w:
        return "(météo indisponible)"
    return (
        f"{w.get('icon', '')} {w.get('city')} : {w.get('temp')}°C "
        f"(ressenti {w.get('feels_like')}°C), {w.get('description')}, "
        f"humidité {w.get('humidity')}%, vent {w.get('wind_speed')} km/h"
    )


def _format_tasks(tasks: list[dict], max_items: int = 10) -> str:
    if not tasks:
        return "(aucune tâche en cours)"
    lines = []
    for t in tasks[:max_items]:
        prio = (t.get("priority") or "medium").upper()
        due = f" — échéance {t['due_date']}" if t.get("due_date") else ""
        cat = f" ({t['category']})" if t.get("category") else ""
        lines.append(f"- [{prio}] [{t.get('status')}] {t.get('title')}{cat}{due}")
    return "\n".join(lines)


def _format_analyzed_emails(summaries: list[dict], max_items: int = 12) -> str:
    """Pré-analysés par l'email watcher (pas de re-analyse Haiku au briefing)."""
    if not summaries:
        return "(aucun email analysé récemment)"
    lines = []
    for s in summaries[:max_items]:
        prio = (s.get("priority") or "medium").upper()
        action = " [À RÉPONDRE]" if s.get("action_needed") else ""
        sender = s.get("sender") or "?"
        subject = s.get("subject") or "(sans sujet)"
        summary = (s.get("summary") or "").replace("\n", " ")[:140]
        lines.append(f"- [{prio}]{action} {sender} — {subject} : {summary}")
    return "\n".join(lines)


def _format_notifications(notifs: list[dict], max_items: int = 10) -> str:
    if not notifs:
        return "(aucune notification en attente)"
    lines = []
    for n in notifs[:max_items]:
        prio = (n.get("priority") or "medium").upper()
        src = n.get("source") or "?"
        title = n.get("title") or "?"
        lines.append(f"- [{prio}] ({src}) {title}")
    return "\n".join(lines)


class ProductivityAgent(BaseAgent):
    """Agent productivité : DeepSeek main par défaut, mode tâche lourde pour les rédactions longues."""

    name = "productivity"
    description = "Emails, calendrier, tâches, briefings"
    model = config.DEEPSEEK_MAIN_MODEL

    async def _collect_pro_context(self, use_email_summaries: bool = False) -> dict:
        """Collecte en parallèle emails / calendar / météo / tâches.

        Si `use_email_summaries=True`, on lit les résumés déjà produits par
        `email_watcher` au lieu de re-analyser les non-lus à chaque appel.
        Recommandé pour `morning_briefing()` (économise des tokens Haiku).
        """
        async def _safe(coro, default):
            try:
                return await coro
            except Exception as e:
                logger.error(f"[productivity] context fetch : {e}")
                return default

        # En mode briefing, on ne refait pas l'appel Gmail (les résumés DB suffisent).
        if use_email_summaries:
            email_coro = asyncio.sleep(0, result=[])
        elif mail_client and mail_client.is_available():
            email_coro = _safe(mail_client.get_unread(10), [])
        else:
            email_coro = asyncio.sleep(0, result=[])

        results = await asyncio.gather(
            email_coro,
            _safe(calendar_client.get_today_events(), []) if calendar_client and calendar_client.is_available() else asyncio.sleep(0, result=[]),
            _safe(weather.get_current(), None) if weather and weather.is_available() else asyncio.sleep(0, result=None),
        )
        emails, cal_events, current_weather = results
        tasks = get_tasks()  # synchrone, rapide

        # Email context : soit live (handle), soit pré-analysé (briefing)
        if use_email_summaries:
            try:
                analyzed = get_recent_email_summaries(limit=15)
            except Exception as e:
                logger.error(f"[productivity] get_recent_email_summaries : {e}")
                analyzed = []
            emails_context = _format_analyzed_emails(analyzed)
        else:
            analyzed = []
            emails_context = _format_emails(emails)

        # Notifications en attente (email watcher, alertes patterns…)
        try:
            notifs = get_unread_notifications(limit=15)
        except Exception as e:
            logger.error(f"[productivity] get_unread_notifications : {e}")
            notifs = []

        return {
            "emails": emails,
            "email_summaries": analyzed,
            "calendar_events": cal_events,
            "weather": current_weather,
            "tasks": tasks,
            "notifications": notifs,
            "emails_context": emails_context,
            "calendar_context": _format_calendar(cal_events),
            "weather_context": _format_weather(current_weather),
            "tasks_context": _format_tasks(tasks),
            "notifications_context": _format_notifications(notifs),
            "pro_context": (
                f"Date : {datetime.now().strftime('%A %d %B %Y, %H:%M')}"
            ),
        }

    async def handle(self, user_message: str, conversation_id: int = None,
                     context: dict = None) -> dict:
        ctx = dict(context or {})
        ctx.update(await self._collect_pro_context())
        return await self._route_task(user_message, conversation_id, ctx)

    async def handle_stream(self, user_message: str, conversation_id: int = None,
                            context: dict = None) -> AsyncGenerator[dict, None]:
        yield {"type": "classification", "agent": self.name}

        ctx = dict(context or {})
        ctx.update(await self._collect_pro_context())

        result = await self._route_task(user_message, conversation_id, ctx)
        response_text = result.get("response", "")

        for i in range(0, len(response_text), STREAM_CHUNK_SIZE):
            yield {"type": "chunk", "content": response_text[i:i + STREAM_CHUNK_SIZE]}
            await asyncio.sleep(0.01)

        yield {
            "type": "done",
            "agent": self.name,
            "model": result.get("model"),
            "tokens_in": result.get("tokens_in", 0),
            "tokens_out": result.get("tokens_out", 0),
            "cost": result.get("cost", 0.0),
            "content": response_text,
        }

    # ── Briefings quotidiens ────────────────────────────────

    async def morning_briefing(self) -> str:
        """Génère le briefing du matin et le sauvegarde dans daily_briefings.

        Utilise les résumés d'emails déjà analysés par `email_watcher` (économise
        une analyse Haiku par mail à chaque briefing) + les notifications
        urgentes en attente + les tâches auto-créées par le watcher.
        """
        ctx = await self._collect_pro_context(use_email_summaries=True)
        ctx["user_name"] = config.USER_NAME

        # On ajoute explicitement les notifications dans le user message pour
        # que Sonnet en parle dans le briefing s'il y a des urgences.
        notif_count = len(ctx.get("notifications") or [])
        urgent_count = sum(
            1 for n in (ctx.get("notifications") or [])
            if (n.get("priority") or "").lower() == "urgent"
        )
        prefix = f"Génère le briefing du matin."
        if urgent_count:
            prefix += f"\n\n⚠️ {urgent_count} notification(s) URGENTE(s) en attente — mentionne-les en premier."
        elif notif_count:
            prefix += f"\n\n{notif_count} notification(s) en attente."

        system = self.build_system_prompt(ctx)
        result = await llm.chat(
            messages=[{"role": "user", "content": prefix}],
            model=config.DEEPSEEK_MAIN_MODEL,
            system=system,
            max_tokens=1500,
            temperature=0.5,
        )
        briefing = finalize_assistant_display_text(result["content"])

        try:
            today = datetime.now().strftime("%Y-%m-%d")
            save_daily_briefing(today, morning=briefing)
        except Exception as e:
            logger.error(f"[productivity] save briefing : {e}")

        logger.info(f"[productivity] Morning briefing généré ({result['tokens_out']} tokens)")
        return briefing

    async def evening_summary(self) -> str:
        """Génère le résumé du soir basé sur les messages/actions de la journée."""
        today = datetime.now().strftime("%Y-%m-%d")
        messages_today = get_daily_messages(today)
        tasks = get_tasks()

        # Construit un mini-récap des conversations du jour pour Claude
        conv_summary_lines = []
        for m in messages_today[-50:]:  # cap à 50 derniers
            role = m.get("role", "?")
            content = (m.get("content") or "").replace("\n", " ")[:120]
            conv_summary_lines.append(f"[{role}] {content}")
        conv_summary = "\n".join(conv_summary_lines) or "(aucune conversation aujourd'hui)"

        ctx = {
            "user_name": config.USER_NAME,
            "memory_context": "(résumé soir — pas de mémoire injectée)",
            "life_profile": "",
            "school_context": "",
            "recent_docs": "",
            "pro_context": (
                f"Date : {datetime.now().strftime('%A %d %B %Y')}\n\n"
                f"CONVERSATIONS DU JOUR ({len(messages_today)} messages) :\n{conv_summary}"
            ),
            "tasks_context": _format_tasks(tasks),
            "calendar_context": "(agenda du soir non récupéré)",
            "city": config.WEATHER_CITY,
        }

        system = self.build_system_prompt(ctx)
        result = await llm.chat(
            messages=[{"role": "user", "content": "Génère le résumé de la journée."}],
            model=config.DEEPSEEK_MAIN_MODEL,
            system=system,
            max_tokens=1500,
            temperature=0.5,
        )
        summary = finalize_assistant_display_text(result["content"])

        try:
            save_daily_briefing(today, evening=summary)
        except Exception as e:
            logger.error(f"[productivity] save evening : {e}")

        logger.info(f"[productivity] Evening summary généré ({result['tokens_out']} tokens)")
        return summary


productivity_agent = ProductivityAgent()
