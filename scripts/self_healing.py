"""Self-healing sûr : diagnostic local puis workflow agentique en PR.

Le supervisor peut demander un diagnostic après une boucle de crash. Ce module
ne modifie jamais le checkout actif : lorsque l'auto-réparation est activée, le
correctif est délégué dans un worktree géré par JARVIS avec livraison ``pr_only``.
Sinon, seule la cause probable est notifiée.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from pathlib import Path

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


def _crash_idempotency_key(crash_tail: str) -> str:
    """Déduplique une même crash-loop pendant une heure, même après redémarrage."""
    window = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H")
    digest = hashlib.sha256(crash_tail.encode("utf-8", errors="replace")).hexdigest()[:16]
    return f"self-healing:{window}:{digest}"


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
    """Diagnostique une crash-loop puis délègue via un worktree PR-only."""
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
        try:
            from agents.devagent import agentic_runtime
            from jarvis.security.redaction import redact_sensitive_text

            safe_tail = redact_sensitive_text(crash_tail[-3000:])
            idempotency_key = _crash_idempotency_key(crash_tail)
            job = await agentic_runtime.delegate_engineering_task(
                title="Self-repair: crash loop",
                user_request=redact_sensitive_text(
                    "Auto-réparation JARVIS après crash loop.\n"
                    f"Diagnostic: {diagnosis.get('root_cause')}\n"
                    f"Fichier suspect: {diagnosis.get('file')}\n"
                    f"Log tail:\n{safe_tail}\n"
                    "Reproduire, corriger, tester, ouvrir une PR. "
                    "Ne jamais modifier main directement."
                ),
                template_id="self_repair",
                workflow_id="self_healing",
                risk="high",
                interaction_mode="scheduled",
                origin="supervisor",
                channel="self_healing",
                task_id=f"self-healing:{idempotency_key.rsplit(':', 1)[-1]}",
                idempotency_key=idempotency_key,
                selected_context={
                    "delivery_owner": "jarvis",
                    "crash_window": idempotency_key.split(":", 2)[1],
                    "diagnosis_confidence": diagnosis.get("confidence"),
                },
                evidence={
                    "diagnosis": diagnosis,
                    "log_tail": safe_tail,
                },
                permissions=("workspace:read", "workspace:write"),
                acceptance_criteria=(
                    "La crash-loop est reproductible puis corrigée à sa cause racine",
                    "Un test de non-régression déterministe couvre la panne",
                    "Le runtime n'exécute ni Git, ni push, ni PR, ni déploiement",
                ),
                required_tests=(
                    ("python", "-m", "pytest", "tests/test_self_healing.py", "-q"),
                ),
                auto_start=True,
                require_confirmation=False,
                delivery_mode="pr_only",
                repo_root=Path(config.BASE_DIR),
                wait_for_completion=False,
            )
        except agentic_runtime.AgenticRuntimeUnavailable as exc:
            logger.warning("[self-healing] runtime agentique indisponible : %s", exc)
            return {
                "ok": True,
                "action": "diagnosed_only",
                "reason": str(exc)[:300],
                "diagnosis": diagnosis,
            }
        except Exception as exc:
            logger.warning("[self-healing] délégation agentique échouée : %s", exc)
            return {
                "ok": False,
                "action": "runtime_failed_pr_only",
                "error": str(exc)[:300],
                "diagnosis": diagnosis,
            }

        notification_service.create(
            source="system",
            title="Self-repair agentique démarré",
            content=f"Job {job.get('job_id')} — mode pr_only",
            priority="high",
        )
        return {
            "ok": True,
            "action": "agentic_delegated",
            "job_id": job.get("job_id"),
            "run_id": job.get("run_id"),
            "diagnosis": diagnosis,
        }
    except Exception as exc:
        logger.exception(
            "[self-healing] erreur inattendue (ignorée, jamais bloquante) : %s",
            exc,
        )
        return {"ok": False, "reason": f"erreur interne : {exc}"}
