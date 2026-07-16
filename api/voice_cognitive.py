"""Préambule cognitif du pipeline vocal — Cursor / briefing / heavy.

Toutes les réponses vocales immédiates sortent de DeepSeek Flash. Les tâches
lourdes reçoivent un accusé immédiat puis un suivi en arrière-plan
(DeepSeek Main ou job Cursor), avec notification haute priorité — le daemon
audio la lit à voix haute et l'UI l'affiche.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import datetime
from typing import Any

import config
import llm
from api.voice_support import _broadcast_voice_debug, _save_voice_messages
from database import _save_voice_debug_trace
from jarvis.cognitive import route_request
from jarvis.cognitive.models import TaskIntent

logger = logging.getLogger("jarvis")


def build_voice_debug_trace(text: str, intent: TaskIntent, routing_ms: int) -> dict[str, Any]:
    return {
        "timestamp": datetime.now().strftime("%H:%M:%S"),
        "input_text": text,
        "system_prompt": "",
        "messages_sent": [],
        "raw_response": "",
        "response_clean": "",
        "emotion": "",
        "action_detected": None,
        "action_result": None,
        "pass2_prompt": None,
        "pass2_response": None,
        "latency_route_ms": routing_ms,
        "latency_stt_ms": 0,
        "latency_llm_pass1_ms": 0,
        "latency_llm_pass2_ms": 0,
        "latency_tts_ms": 0,
        "latency_total_ms": 0,
        "model": getattr(config, "VOICE_REASONING_MODEL", None) or config.DEEPSEEK_FAST_MODEL,
        "tokens_in": 0,
        "tokens_out": 0,
        "cost": 0.0,
        "error": None,
        "routing": intent.to_diagnostic(),
    }


def _detect_briefing_variant(text: str) -> dict[str, Any]:
    """Kind + filtres du briefing selon la formulation vocale."""
    t = (text or "").lower()
    kind = "morning"
    if re.search(r"\b(soir|soirée|soiree|résumé\s+du\s+soir|resume\s+du\s+soir|journée\s+écoulée)\b", t):
        kind = "evening"
    if re.search(r"\bqu[''\s]est[- ]ce\s+qui\s+a\s+changé|depuis\s+ce\s+matin\b", t):
        kind = "delta"
    filter_priority = None
    if re.search(r"\b(urgence|urgences|urgent|critique)\b", t):
        filter_priority = "critique"
    voice_only = bool(re.search(r"\b(version\s+courte|courte?|rapide|bref)\b", t))
    work_only = bool(re.search(r"\b(seulement\s+le\s+travail|que\s+le\s+travail|le\s+travail\s+uniquement)\b", t))
    return {
        "kind": kind,
        "filter_priority": filter_priority,
        "voice_only": voice_only,
        "work_only": work_only,
    }


def _finalize_voice_reply(
    debug_trace: dict[str, Any],
    conversation_id: int,
    text: str,
    reply: str,
    intent: TaskIntent,
    t0: float,
    *,
    emotion: str = "warm",
    action: dict[str, Any] | None = None,
    cost: float = 0.0,
) -> dict[str, Any]:
    debug_trace["response_clean"] = reply
    debug_trace["latency_total_ms"] = round((time.time() - t0) * 1000)
    _save_voice_messages(conversation_id, text, reply, cost)
    asyncio.create_task(_broadcast_voice_debug(debug_trace))
    trace_id = _save_voice_debug_trace(debug_trace)
    return {
        "text": reply,
        "emotion": emotion,
        "cost": cost,
        "action": action,
        "latency_ms": debug_trace["latency_total_ms"],
        "debug_trace": debug_trace,
        "trace_id": trace_id,
        "routing": intent.to_diagnostic(),
    }


async def maybe_handle_cognitive_voice(
    text: str,
    conversation_id: int,
    *,
    t0: float,
    stt_ms: int = 0,
) -> dict[str, Any] | None:
    """Gère Cursor / briefing / heavy. Retourne une réponse finale ou None."""
    t_route = time.time()
    intent = route_request(text, interaction_mode="voice")
    routing_ms = round((time.time() - t_route) * 1000)
    debug_trace = build_voice_debug_trace(text, intent, routing_ms)
    debug_trace["latency_stt_ms"] = int(stt_ms or 0)

    # ── Tâche technique → délégation Cursor + ack immédiat ──
    if intent.execution_type == "cursor":
        ack = intent.voice_ack or (
            "Je m'en occupe, Monsieur. Je délègue à Cursor et je vous rends compte."
        )
        job_id = None
        try:
            from integrations.cursor_delegation import cursor_delegation

            job = await cursor_delegation.enqueue(
                title=text[:120],
                user_request=text,
                template_id=intent.template_id or "feature_implementation",
                interaction_mode="voice",
                routing=intent.to_diagnostic(),
                auto_start=True,
            )
            job_id = job.get("job_id")
            debug_trace["cursor_job_id"] = job_id
        except Exception as exc:
            logger.error("[voice_fast] delegation Cursor : %s", exc)
            ack = f"Je ne peux pas lancer Cursor d'ici, Monsieur. Raison : {str(exc)[:120]}"
            debug_trace["error"] = str(exc)

        return _finalize_voice_reply(
            debug_trace, conversation_id, text, ack, intent, t0,
            action={"type": "cursor_delegate", "job_id": job_id} if job_id else None,
        )

    # ── Briefing à la demande (matin / soir / delta / urgences / courte) ──
    if intent.domain == "briefing":
        try:
            from agents.briefing_engine import generate_structured_briefing

            variant = _detect_briefing_variant(text)
            briefing = await generate_structured_briefing(
                variant["kind"],
                voice_only=variant["voice_only"],
                filter_priority=variant["filter_priority"],
                work_only=variant["work_only"],
            )
            voice_text = briefing.voice_text or briefing.full_text[:500]
            debug_trace["briefing_kind"] = variant["kind"]
            return _finalize_voice_reply(
                debug_trace, conversation_id, text, voice_text, intent, t0,
            )
        except Exception as exc:
            logger.warning("[voice_fast] briefing engine : %s", exc)
            # → chute vers le pipeline Flash classique (réponse honnête)

    # ── Réflexion lourde non technique : ack Flash + suivi Main en fond ──
    if intent.complexity == "heavy" and intent.execution_type == "answer" and intent.voice_ack:
        ack = intent.voice_ack

        async def _heavy_followup() -> None:
            try:
                result = await llm.chat(
                    messages=[{"role": "user", "content": text}],
                    model=intent.prompt_model or config.DEEPSEEK_MAIN_MODEL,
                    system=(
                        "Tu es JARVIS. Analyse structuree, francaise, sans emoji. "
                        "Le resultat complet sera lu dans l'interface ; reste clair."
                    ),
                    max_tokens=getattr(config, "HEAVY_TASK_MAX_TOKENS", 8192),
                    temperature=0.4,
                )
                body = (result.get("content") or "").strip()
                if not body:
                    return
                from agents.display_text import finalize_assistant_display_text
                from database import save_message
                from jarvis.notification_service import notification_service

                save_message(
                    conversation_id,
                    "assistant",
                    finalize_assistant_display_text(body),
                    agent="cognitive",
                    model=result.get("model"),
                    tokens_in=result.get("tokens_in"),
                    tokens_out=result.get("tokens_out"),
                    cost=result.get("cost"),
                )
                # Résumé vocal court du résultat (Flash) — lu par le daemon
                # audio via la notification high + proposé dans l'UI.
                voice_summary = ""
                try:
                    vs = await llm.chat(
                        messages=[{
                            "role": "user",
                            "content": (
                                "Résume ce plan en 2 phrases orales maximum, puis propose "
                                "UNE action concrète à faire maintenant. Ton JARVIS, pas de markdown.\n\n"
                                f"{body[:4000]}"
                            ),
                        }],
                        model=config.DEEPSEEK_FAST_MODEL,
                        system="Tu es JARVIS à l'oral. Concision absolue.",
                        max_tokens=120,
                        temperature=0.3,
                    )
                    voice_summary = (vs.get("content") or "").strip()
                except Exception as exc:
                    logger.debug("[voice_fast] résumé vocal heavy : %s", exc)

                notification_service.create(
                    source="cognitive",
                    title="Analyse prête",
                    content=voice_summary or body[:280],
                    priority="high",
                )
            except Exception as exc:
                logger.error("[voice_fast] heavy followup : %s", exc)

        asyncio.create_task(_heavy_followup(), name="voice-heavy-followup")
        return _finalize_voice_reply(
            debug_trace, conversation_id, text, ack, intent, t0,
        )

    # Continuer le pipeline Flash classique — propager le routing dans le trace
    return {"__continue__": True, "intent": intent, "debug_trace": debug_trace}
