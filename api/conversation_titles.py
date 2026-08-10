"""Cycle de vie des titres de conversation, indépendant du transport."""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

from fastapi import WebSocket

import config
import llm
from api.llm_logging import _schedule_llm_log
from database import get_conversation_detail, update_generated_conversation_title
from jarvis.security.llm_data_boundary import (
    UNTRUSTED_DATA_SYSTEM_RULE,
    wrap_untrusted_data,
)

logger = logging.getLogger("jarvis")

_title_tasks: dict[int, asyncio.Task[str | None]] = {}
_title_callbacks: dict[
    int,
    list[Callable[[dict[str, Any]], Awaitable[None]]],
] = {}


def _fallback_conversation_title(messages: list[dict]) -> str:
    first_user = next(
        (
            str(message.get("content") or "")
            for message in messages
            if message.get("role") == "user"
        ),
        "",
    )
    compact = re.sub(r"\s+", " ", first_user).strip(" -–—:;,.!?\"'")
    words = compact.split()
    if not words:
        return "Conversation avec JARVIS"
    title = " ".join(words[:7])
    if len(title) > 72:
        title = title[:72].rsplit(" ", 1)[0] or title[:72]
    return title[0].upper() + title[1:]


def _clean_generated_title(value: Any) -> str:
    title = str(value or "").splitlines()[0].strip().strip('"').strip("'")
    title = re.sub(r"\s+", " ", title).strip(" -–—:;,.!?")
    if not 2 <= len(title.split()) <= 8:
        return ""
    return title[:80].rstrip()


async def _maybe_title_conversation(conv_id: int) -> str | None:
    """Produit un titre IA validé, avec repli local et état explicite."""
    conv: dict | None = None
    try:
        conv = get_conversation_detail(conv_id)
        if not conv:
            return None
        if conv.get("title") and conv.get("title_status") in {"ready", "manual"}:
            return str(conv["title"])
        messages = conv.get("messages", [])
        has_user = any(message.get("role") == "user" for message in messages)
        has_assistant = any(message.get("role") == "assistant" for message in messages)
        if not (has_user and has_assistant):
            return None
        raw_context = "\n".join(
            f"{message['role']}: {message['content'][:100]}" for message in messages[:4]
        )
        context = wrap_untrusted_data(
            "CONVERSATION_HISTORY_FOR_TITLE",
            raw_context,
            max_chars=800,
        )
        result = await llm.chat(
            messages=[{"role": "user", "content": context}],
            model=config.DEEPSEEK_FAST_MODEL,
            system=(
                UNTRUSTED_DATA_SYSTEM_RULE
                + "\nGénère un titre court (3-6 mots) pour cette conversation. "
                "Pas de guillemets, pas de ponctuation finale. Juste le titre."
            ),
            max_tokens=20,
            temperature=0.3,
            use_cache=False,
        )
        title = _clean_generated_title(result.get("content"))
        if title:
            updated = update_generated_conversation_title(
                conv_id,
                title=title,
                title_status="ready",
                title_source="ai",
                title_updated_at=datetime.now(timezone.utc).isoformat(),
            )
            if not updated:
                current = get_conversation_detail(conv_id)
                return str(current.get("title") or "") if current else None
            _schedule_llm_log(
                agent="system",
                action_type="auto_title",
                payload={"conversation_id": conv_id, "title": title},
                status="success",
            )
            logger.info("[conv] Titre auto : #%d → %s", conv_id, title)
            return title
    except Exception as exc:
        _schedule_llm_log(
            agent="system",
            action_type="auto_title",
            payload={"conversation_id": conv_id, "error": str(exc)},
            status="error",
        )
        logger.warning("[conv] titrage IA indisponible pour #%d : %s", conv_id, exc)

    if conv:
        fallback = _fallback_conversation_title(conv.get("messages", []))
        updated = update_generated_conversation_title(
            conv_id,
            title=fallback,
            title_status="fallback",
            title_source="first_user_message",
            title_updated_at=datetime.now(timezone.utc).isoformat(),
        )
        if updated:
            return fallback
        current = get_conversation_detail(conv_id)
        return str(current.get("title") or "") if current else None
    return None


def schedule_conversation_title(
    conv_id: int,
    *,
    title_factory: Callable[[int], Awaitable[str | None]] | None = None,
    on_update: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
) -> asyncio.Task[str | None]:
    """Déduplique le titrage et notifie tous les transports intéressés."""
    if on_update:
        _title_callbacks.setdefault(conv_id, []).append(on_update)
    current = _title_tasks.get(conv_id)
    if current and not current.done():
        return current

    factory = title_factory or _maybe_title_conversation

    async def _run() -> str | None:
        title = await factory(conv_id)
        detail = get_conversation_detail(conv_id)
        callbacks = _title_callbacks.pop(conv_id, [])
        if detail:
            for callback in callbacks:
                try:
                    await callback(detail)
                except Exception:
                    logger.debug(
                        "[conv] notification de titre ignorée",
                        exc_info=True,
                    )
        return title

    task = asyncio.create_task(_run(), name=f"conversation-title-{conv_id}")
    _title_tasks[conv_id] = task

    def _cleanup(completed: asyncio.Task[str | None]) -> None:
        if _title_tasks.get(conv_id) is completed:
            _title_tasks.pop(conv_id, None)
        if completed.cancelled():
            _title_callbacks.pop(conv_id, None)
            return
        try:
            error = completed.exception()
            if error:
                _title_callbacks.pop(conv_id, None)
                logger.warning("[conv] tâche de titrage en échec : %s", error)
        except Exception:
            _title_callbacks.pop(conv_id, None)
            logger.debug("[conv] tâche de titrage en échec", exc_info=True)

    task.add_done_callback(_cleanup)
    return task


def _conversation_updated_payload(detail: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "conversation_updated",
        "conversation_id": detail["id"],
        "checkpoint_id": detail.get("checkpoint_id"),
        "title": detail.get("title"),
        "title_status": detail.get("title_status"),
        "title_source": detail.get("title_source"),
        "message_count": detail.get("message_count", 0),
    }


async def notify_and_schedule_conversation_title(
    ws: WebSocket,
    conv_id: int,
) -> None:
    """Publie l'état immédiat puis le titre final quand il devient disponible."""
    detail = get_conversation_detail(conv_id)

    async def _notify(updated: dict[str, Any]) -> None:
        await ws.send_json(_conversation_updated_payload(updated))

    try:
        if detail:
            await ws.send_json(_conversation_updated_payload(detail))
    finally:
        # Le titrage doit survivre à une socket fermée entre la réponse et la
        # notification. Le callback de transport est, lui, best-effort.
        schedule_conversation_title(conv_id, on_update=_notify)
