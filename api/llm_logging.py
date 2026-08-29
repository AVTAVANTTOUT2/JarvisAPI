"""Journalisation non bloquante des appels LLM et actions."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from database import log_llm_action

logger = logging.getLogger("jarvis")
_pending_llm_logs: set[asyncio.Task[None]] = set()


async def flush_pending_llm_logs(timeout: float = 5.0) -> None:
    """Attend les journaux différés avant un teardown ou un arrêt propre."""
    pending = {task for task in _pending_llm_logs if not task.done()}
    if not pending:
        return
    try:
        await asyncio.wait_for(asyncio.gather(*pending, return_exceptions=True), timeout)
    except asyncio.TimeoutError:
        logger.warning("[llm-log] %d écriture(s) encore en vol à l'arrêt", len(pending))


def _schedule_llm_log(
    *,
    agent: str,
    action_type: str,
    payload: dict[str, Any] | str,
    status: str = "pending",
    execution_time_ms: int | None = None,
) -> None:
    """Log non bloquant des actions système/LLM."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        try:
            log_llm_action(agent, action_type, payload, status, execution_time_ms)
        except Exception:
            logger.debug("[llm-log] sync fallback failed", exc_info=True)
        return

    async def _runner() -> None:
        try:
            await loop.run_in_executor(
                None,
                lambda: log_llm_action(agent, action_type, payload, status, execution_time_ms),
            )
        except Exception:
            logger.debug("[llm-log] async failed", exc_info=True)

    task = asyncio.create_task(_runner())
    _pending_llm_logs.add(task)
    task.add_done_callback(_pending_llm_logs.discard)
