"""Agent INFO — questions factuelles, météo, recherche web, chat léger.

Phase 1 : version minimale sans intégrations externes (météo/web).
Les intégrations seront branchées en Phase 4.
"""

import logging

import config
from agents import BaseAgent

logger = logging.getLogger(__name__)


class InfoAgent(BaseAgent):
    """Agent rapide (Haiku) pour info factuelle et small talk."""

    name = "info"
    description = "Météo, recherche web, questions factuelles, chat léger."
    model = config.DEEPSEEK_FAST_MODEL

    async def handle(self, user_message: str, conversation_id: int = None,
                     context: dict = None) -> dict:
        ctx = context or {}
        ctx.setdefault("user_name", config.USER_NAME)
        ctx.setdefault("city", config.WEATHER_CITY)

        return await self._call_llm(
            user_message,
            conversation_id=conversation_id,
            context=ctx,
            temperature=0.6,
        )


info_agent = InfoAgent()
