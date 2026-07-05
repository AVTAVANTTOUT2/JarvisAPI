"""Agent DEVOPS — développement, infrastructure, projets techniques.

Agent principal pour tout ce qui touche au code, aux projets, au déploiement,
à l'infrastructure et aux opérations système. Remplace l'agent INFO pour
les questions techniques (qui était trop générique).
"""

import logging

import config
from agents import BaseAgent

logger = logging.getLogger(__name__)


class DevopsAgent(BaseAgent):
    """Agent technique polyvalent (DeepSeek v4). Gère le dev, l'infra et les projets."""

    name = "devops"
    description = (
        "Développement, infrastructure, projets techniques, code, déploiement, "
        "debug, architecture, shell, Git, Cloudflare, base de données."
    )
    model = config.DEEPSEEK_MAIN_MODEL
    supplementary_prompt_files = ("cursor_bug_fix.txt",)

    async def handle(self, user_message: str, conversation_id: int = None,
                     context: dict = None) -> dict:
        ctx = context or {}
        ctx.setdefault("user_name", config.USER_NAME)
        ctx.setdefault("language", config.LANGUAGE)

        return await self._call_llm(
            user_message,
            conversation_id=conversation_id,
            context=ctx,
            temperature=0.4,
        )


devops_agent = DevopsAgent()
