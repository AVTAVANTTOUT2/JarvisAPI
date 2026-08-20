"""Adaptation WebSocket des réponses du runtime agentique générique."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
import logging
import hashlib
from typing import Any

from fastapi import WebSocket

import config
from api.agentic_processing import maybe_start_agentic_run
from api.chat_context import _send_tts_streaming
from database import save_message
from integrations.apple_music import maybe_handle_music_intent

logger = logging.getLogger("jarvis")

_VOICE_SUMMARY_LIMIT = 2_048
_voice_summaries_delivered: OrderedDict[str, None] = OrderedDict()
_voice_summary_tasks: set[asyncio.Task[None]] = set()


def _claim_voice_summary(run_id: str) -> bool:
    if run_id in _voice_summaries_delivered:
        return False
    _voice_summaries_delivered[run_id] = None
    while len(_voice_summaries_delivered) > _VOICE_SUMMARY_LIMIT:
        _voice_summaries_delivered.popitem(last=False)
    return True


def _terminal_voice_summary(status: str) -> str:
    return {
        "completed": "La tâche est terminée et vérifiée par JARVIS.",
        "failed": "La tâche a échoué. Les détails neutralisés sont disponibles dans JARVIS.",
        "blocked": "La tâche est bloquée et nécessite votre attention.",
        "cancelled": "La tâche a été annulée.",
        "expired": "La tâche a expiré avant sa conclusion.",
        "provider_unavailable": "Le runtime agentique est indisponible.",
    }.get(status, "La tâche agentique est terminée.")


async def _send_terminal_voice_summary(
    ws: WebSocket,
    run_id: str,
    *,
    service: Any | None = None,
) -> None:
    """Attend le terminal et parle une seule phrase constante, sans artefact brut."""

    if service is None:
        from jarvis.agentic import get_agentic_service

        service = get_agentic_service()
    try:
        run = await service.wait_for_terminal(
            run_id,
            timeout=float(getattr(config, "AGENTIC_MAX_RUN_SECONDS", 1800)) + 60.0,
        )
        status = str(getattr(run.status, "value", run.status))
        if not _claim_voice_summary(run_id):
            return
        summary = _terminal_voice_summary(status)
        await ws.send_json(
            {
                "type": "agentic_voice_summary",
                "run_id": run_id,
                "status": status,
                "content": summary,
            }
        )
        await _send_tts_streaming(ws, summary, "neutral")
    except Exception:
        logger.info("agentic_voice_summary_unavailable run_id=%s", run_id)


def _schedule_terminal_voice_summary(ws: WebSocket, run_id: str) -> None:
    task = asyncio.create_task(
        _send_terminal_voice_summary(ws, run_id),
        name=f"agentic-voice-summary-{hashlib.sha256(run_id.encode()).hexdigest()[:12]}",
    )
    _voice_summary_tasks.add(task)
    task.add_done_callback(_voice_summary_tasks.discard)


def agentic_idempotency_key(
    session_id: str, client_message_id: str | None
) -> str | None:
    """Dérive une clé opaque et bornée sans persister l'identifiant client."""

    if not client_message_id:
        return None
    digest = hashlib.sha256(f"{session_id}\x00{client_message_id}".encode()).hexdigest()
    return f"ws:{digest}"


async def maybe_send_agentic_run(
    ws: WebSocket,
    request: str,
    conversation_id: int,
    *,
    voice_mode: bool,
    send_tts: bool,
    idempotency_key: str | None,
    device: str | None = None,
    locale: str | None = None,
    timezone_name: str | None = None,
    enriched_context: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Crée éventuellement un run puis émet son accusé générique sur la socket."""

    music = await maybe_handle_music_intent(request)
    if music is not None:
        text = str(music.get("text") or "")
        knowledge = music.get("knowledge") or {}
        try:
            save_message(
                conversation_id,
                "assistant",
                text,
                agent=str(music.get("agent") or "info"),
                model=str(music.get("model") or "runtime"),
                tokens_in=0,
                tokens_out=0,
                cost=0.0,
            )
        except Exception:
            logger.debug("impossible de persister la réponse musique", exc_info=True)
        await ws.send_json(
            {
                "type": "response",
                "agent": music.get("agent") or "info",
                "content": text,
                "emotion": "neutral",
                "model": music.get("model") or "runtime",
                "tokens_in": 0,
                "tokens_out": 0,
                "cost": 0.0,
                "action": music.get("action"),
                "action_result": music.get("action_result"),
                "knowledge": knowledge,
            }
        )
        if send_tts:
            await _send_tts_streaming(ws, text, "neutral")
        return {
            "emotion": "neutral",
            "response": text,
            "knowledge": knowledge,
        }

    agentic = await maybe_start_agentic_run(
        request,
        conversation_id,
        channel="voice" if voice_mode else "websocket",
        voice_mode=voice_mode,
        persist_assistant=True,
        idempotency_key=idempotency_key,
        origin="websocket",
        device=device,
        locale=locale,
        timezone_name=timezone_name,
        enriched_context=enriched_context,
    )
    if agentic is None:
        return None
    run = agentic.get("agentic_run")
    task_control = agentic.get("task_control")
    if isinstance(run, dict):
        await ws.send_json({"type": "agentic_run", **run})
    elif isinstance(task_control, dict):
        await ws.send_json({"type": "task_control", **task_control})
    knowledge = agentic.get("knowledge") or {}
    await ws.send_json(
        {
            "type": "response",
            "agent": "agentic",
            "content": agentic["text"],
            "emotion": "neutral",
            "model": "runtime",
            "tokens_in": 0,
            "tokens_out": 0,
            "cost": 0.0,
            "run_id": run.get("run_id") if isinstance(run, dict) else None,
            "task_id": (
                task_control.get("task_id") if isinstance(task_control, dict) else None
            ),
            "knowledge": knowledge,
        }
    )
    if send_tts:
        await _send_tts_streaming(ws, agentic["text"], "neutral")
        if isinstance(run, dict) and run.get("run_id"):
            _schedule_terminal_voice_summary(ws, run["run_id"])
    return {
        "emotion": "neutral",
        "response": agentic["text"],
        "knowledge": knowledge,
    }


async def maybe_send_legacy_delegation(
    ws: WebSocket,
    text: str,
    conversation_id: int,
    *,
    voice_mode: bool,
    send_tts: bool,
    confirmation_session_id: str,
) -> dict[str, Any] | None:
    """Émet l'ancien parcours uniquement lorsqu'il est explicitement activé."""

    if str(getattr(config, "AGENTIC_RUNTIME_FALLBACK", "disabled")).lower() != "legacy":
        return None
    try:
        from api.chat_cognitive import (
            maybe_delegate_chat_to_cursor,
            route_chat_text,
            should_run_cursor_cognitive_path,
        )

        intent = route_chat_text(text, voice_mode=voice_mode)
        await ws.send_json({"type": "routing", "routing": intent.to_diagnostic()})
        if not should_run_cursor_cognitive_path(
            text, intent, conversation_id, confirmation_session_id
        ):
            return None
        delegated = await maybe_delegate_chat_to_cursor(
            text,
            conversation_id,
            intent=intent,
            interaction_mode="voice" if voice_mode else "chat",
        )
    except Exception as exc:
        logger.debug("[ws_agentic] routage legacy : %s", exc)
        return None
    if not delegated or not delegated.get("handled"):
        return None
    await ws.send_json(
        {
            "type": "response",
            "agent": "cognitive",
            "content": delegated["text"],
            "emotion": delegated.get("emotion", "neutral"),
            "model": "router",
            "tokens_in": 0,
            "tokens_out": 0,
            "cost": 0.0,
            "cursor_job_id": delegated.get("job_id"),
        }
    )
    if send_tts:
        await _send_tts_streaming(ws, delegated["text"], "neutral")
    return {"emotion": "neutral", "response": delegated["text"]}


__all__ = [
    "agentic_idempotency_key",
    "maybe_send_agentic_run",
    "maybe_send_legacy_delegation",
]
