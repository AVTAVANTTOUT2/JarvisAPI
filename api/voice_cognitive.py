"""Préambule cognitif du pipeline vocal — Cursor / briefing / heavy."""

from __future__ import annotations

import asyncio
import logging
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


async def maybe_handle_cognitive_voice(
    text: str,
    conversation_id: int,
    *,
    t0: float,
) -> dict[str, Any] | None:
    """Gère Cursor / briefing / heavy. Retourne une réponse finale ou None."""
    t_route = time.time()
    intent = route_request(text, interaction_mode="voice")
    routing_ms = round((time.time() - t_route) * 1000)
    debug_trace = build_voice_debug_trace(text, intent, routing_ms)

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

        debug_trace["response_clean"] = ack
        debug_trace["latency_total_ms"] = round((time.time() - t0) * 1000)
        _save_voice_messages(conversation_id, text, ack, 0.0)
        asyncio.create_task(_broadcast_voice_debug(debug_trace))
        _save_voice_debug_trace(debug_trace)
        return {
            "text": ack,
            "emotion": "warm",
            "cost": 0.0,
            "action": {"type": "cursor_delegate", "job_id": job_id} if job_id else None,
            "latency_ms": debug_trace["latency_total_ms"],
            "debug_trace": debug_trace,
            "routing": intent.to_diagnostic(),
        }

    if intent.domain == "briefing":
        try:
            from agents.briefing_engine import generate_structured_briefing

            briefing = await generate_structured_briefing("morning")
            voice_text = briefing.voice_text or briefing.full_text[:500]
            debug_trace["response_clean"] = voice_text
            debug_trace["latency_total_ms"] = round((time.time() - t0) * 1000)
            _save_voice_messages(conversation_id, text, voice_text, 0.0)
            asyncio.create_task(_broadcast_voice_debug(debug_trace))
            _save_voice_debug_trace(debug_trace)
            return {
                "text": voice_text,
                "emotion": "warm",
                "cost": 0.0,
                "action": None,
                "latency_ms": debug_trace["latency_total_ms"],
                "debug_trace": debug_trace,
                "routing": intent.to_diagnostic(),
            }
        except Exception as exc:
            logger.warning("[voice_fast] briefing engine : %s", exc)

    if intent.complexity == "heavy" and intent.execution_type == "answer" and intent.voice_ack:
        ack = intent.voice_ack
        debug_trace["response_clean"] = ack
        debug_trace["latency_total_ms"] = round((time.time() - t0) * 1000)

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
                if body:
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
                    notification_service.create(
                        source="cognitive",
                        title="Analyse prete",
                        content=body[:280],
                        priority="medium",
                    )
            except Exception as exc:
                logger.error("[voice_fast] heavy followup : %s", exc)

        asyncio.create_task(_heavy_followup(), name="voice-heavy-followup")
        _save_voice_messages(conversation_id, text, ack, 0.0)
        asyncio.create_task(_broadcast_voice_debug(debug_trace))
        _save_voice_debug_trace(debug_trace)
        return {
            "text": ack,
            "emotion": "warm",
            "cost": 0.0,
            "action": None,
            "latency_ms": debug_trace["latency_total_ms"],
            "debug_trace": debug_trace,
            "routing": intent.to_diagnostic(),
        }

    # Continuer le pipeline Flash classique — propager le routing dans le trace
    return {"__continue__": True, "intent": intent, "debug_trace": debug_trace}
