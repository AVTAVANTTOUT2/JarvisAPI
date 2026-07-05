"""Boucle d'interview adaptative DevAgent (QCM + texte libre)."""

from __future__ import annotations

import json
import logging
from typing import Any

from agents.devagent.utils import parse_json_response
from integrations.deepseek_client import call_deepseek

logger = logging.getLogger(__name__)

INTERVIEW_SYSTEM_PROMPT = """Role: architecte technique senior.
Tu conduis une interview projet dev pour produire une spec complete.

Contexte actuel (JSON): {context_json}

Decide: le contexte est-il suffisant pour generer une spec.json complete et actionnable?

Si NON:
Retourne UNIQUEMENT JSON:
{{"done": false, "question": "...", "type": "qcm_or_text", "options": ["opt1","opt2","opt3"]}}

Si OUI:
Retourne UNIQUEMENT JSON:
{{"done": true, "spec": {{
  "project_name": "...",
  "project_type": "api|ui|script|integration|cli",
  "stack": ["..."],
  "constraints": ["..."],
  "acceptance_criteria": ["..."],
  "loop_budget": {{"max_iterations": 25, "max_tokens": 500000, "max_consecutive_failures": 3}}
}}}}

Regles:
- Question ciblee selon le type de projet
- QCM 3-4 options max, toujours accepter une reponse texte libre
- Ne jamais repeter une question deja posee (voir qa_history)
- Arreter des que stack, scope, contraintes et criteres d'acceptation sont clairs
- Aucun texte hors JSON
"""


async def next_interview_step(project_id: int, context: dict[str, Any]) -> dict[str, Any]:
    """Genere la prochaine question ou la spec finale."""
    prompt = INTERVIEW_SYSTEM_PROMPT.format(
        context_json=json.dumps(context, ensure_ascii=False)
    )
    response = await call_deepseek(
        system=prompt,
        user="Continue l'interview.",
        json_mode=True,
    )
    result = parse_json_response(response["content"])
    logger.debug("[devagent] interview project_id=%s done=%s", project_id, result.get("done"))
    return result


async def submit_answer(
    project_id: int,
    question: str,
    answer: str,
    context: dict[str, Any],
) -> dict[str, Any]:
    """Enregistre une reponse et avance l'interview."""
    history = context.setdefault("qa_history", [])
    history.append({"q": question, "a": answer})
    return await next_interview_step(project_id, context)
