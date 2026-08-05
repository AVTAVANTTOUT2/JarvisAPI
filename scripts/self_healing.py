"""Self-healing sûr : diagnostic local puis proposition Cursor en PR.

Le supervisor peut demander un diagnostic après une boucle de crash. Ce module
ne modifie jamais le checkout actif : lorsque l'auto-réparation est activée, le
correctif est délégué à Cursor dans un worktree avec livraison ``pr_only``.
Sinon, seule la cause probable est notifiée.
"""

from __future__ import annotations

import logging

import config
from jarvis.notification_service import notification_service

logger = logging.getLogger(__name__)

DIAGNOSTIC_PROMPT = """Role: ingénieur SRE senior qui diagnostique un crash en production.
Voici les dernières lignes du log au moment du crash :
```
{log_tail}
```

Identifie la cause racine la plus probable et, si possible, le fichier
concerné. Ne propose pas de contenu de remplacement : toute correction sera
reproduite, testée et relue dans un worktree séparé.

Retourne UNIQUEMENT ce JSON :
{{
  "root_cause": "explication en 2-3 phrases",
  "confidence": "high|medium|low",
  "file": "chemin/relatif/fichier.py ou null"
}}
"""


async def diagnose_crash(log_tail: str) -> dict:
    """Analyse les dernières lignes de log et dégrade proprement sur erreur."""
    import llm
    from jarvis.security.llm_data_boundary import (
        UNTRUSTED_DATA_SYSTEM_RULE,
        wrap_untrusted_data,
    )

    try:
        safe_log = wrap_untrusted_data(
            "CRASH_LOG",
            log_tail,
            max_chars=4_000,
        )
        trusted_system = DIAGNOSTIC_PROMPT.format(
            log_tail="(journal fourni dans le message utilisateur délimité)"
        )
        result = await llm.chat(
            messages=[{"role": "user", "content": safe_log}],
            model=config.DEEPSEEK_MAIN_MODEL,
            system=UNTRUSTED_DATA_SYSTEM_RULE + "\n\n" + trusted_system,
            max_tokens=1_000,
            temperature=0.1,
        )
        from agents.devagent.utils import parse_json_response

        return parse_json_response(result["content"])
    except Exception as exc:
        logger.warning("[self-healing] diagnostic LLM indisponible : %s", exc)
        return {
            "root_cause": f"Diagnostic indisponible : {exc}",
            "confidence": "low",
            "file": None,
        }


async def handle_crash_loop(crash_tail: str) -> dict:
    """Diagnostique une crash-loop puis délègue uniquement via une PR Cursor."""
    if not config.SELF_HEALING_ENABLED:
        return {"ok": False, "reason": "SELF_HEALING_ENABLED désactivé"}

    try:
        diagnosis = await diagnose_crash(crash_tail)
        notification_service.create(
            source="system",
            title="Diagnostic self-healing",
            content=(
                f"{diagnosis.get('root_cause', 'cause inconnue')} "
                f"(confiance : {diagnosis.get('confidence', '?')})"
            ),
            priority="high",
        )
        logger.warning("[self-healing] diagnostic : %s", diagnosis.get("root_cause"))

        if not getattr(config, "SELF_REPAIR_ENABLED", False):
            return {
                "ok": True,
                "action": "diagnosed_only",
                "reason": "SELF_REPAIR_ENABLED=false",
                "diagnosis": diagnosis,
            }
        if not getattr(config, "CURSOR_DELEGATION_ENABLED", True):
            return {
                "ok": True,
                "action": "diagnosed_only",
                "reason": "CURSOR_DELEGATION_ENABLED=false",
                "diagnosis": diagnosis,
            }

        try:
            from integrations.cursor_delegation import cursor_delegation
            from jarvis.security.redaction import redact_sensitive_text

            job = await cursor_delegation.enqueue(
                title="Self-repair: crash loop",
                user_request=redact_sensitive_text(
                    "Auto-réparation JARVIS après crash loop.\n"
                    f"Diagnostic: {diagnosis.get('root_cause')}\n"
                    f"Fichier suspect: {diagnosis.get('file')}\n"
                    f"Log tail:\n{crash_tail[-3000:]}\n"
                    "Reproduire, corriger, tester, ouvrir une PR. "
                    "Ne jamais modifier main directement."
                ),
                template_id="self_repair",
                risk_level="high",
                interaction_mode="scheduled",
                auto_start=True,
                require_confirmation=False,
                delivery_mode="pr_only",
            )
        except Exception as exc:
            logger.warning("[self-healing] délégation Cursor échouée : %s", exc)
            return {
                "ok": False,
                "action": "cursor_failed_pr_only",
                "error": str(exc)[:300],
                "diagnosis": diagnosis,
            }

        notification_service.create(
            source="system",
            title="Self-repair délégué à Cursor",
            content=f"Job {job.get('job_id')} — mode pr_only",
            priority="high",
        )
        return {
            "ok": True,
            "action": "cursor_delegated",
            "job_id": job.get("job_id"),
            "diagnosis": diagnosis,
        }
    except Exception as exc:
        logger.exception(
            "[self-healing] erreur inattendue (ignorée, jamais bloquante) : %s",
            exc,
        )
        return {"ok": False, "reason": f"erreur interne : {exc}"}
