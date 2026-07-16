"""Boucle autonome JARVIS — mode /loop sans limite DeepSeek configurable."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Awaitable, Callable, Protocol

import config
import llm
from agents.display_text import strip_assistant_code_fences

logger = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "autonomous_loop.txt"

# Plafond de sécurité absolu — évite une boucle infinie en cas de bug LLM.
# Ignoré si LOOP_MAX_STEPS=0 (illimité côté config utilisateur).
HARD_SAFETY_MAX_STEPS = 500

LoopEventCallback = Callable[[str, dict], Awaitable[None]]


class AutonomousLoopError(Exception):
    """Erreur bloquante dans la boucle autonome."""


def parse_loop_command(text: str) -> str | None:
    """Extrait la tâche d'une commande ``/loop …`` ou ``/loop: …``.

    Returns:
        Tâche décrite par l'utilisateur, ou None si ce n'est pas une commande /loop.
        Chaîne vide si /loop sans argument.
    """
    if not text or not isinstance(text, str):
        return None
    stripped = text.strip()
    lower = stripped.lower()
    if not (lower.startswith("/loop") or lower.startswith("/loop:")):
        return None
    if lower.startswith("/loop:"):
        task = stripped[6:].strip()
    elif lower.startswith("/loop "):
        task = stripped[5:].strip()
    else:
        # "/loop" seul
        task = stripped[5:].strip()
    return task


def _load_loop_prompt() -> str:
    if PROMPT_PATH.exists():
        return PROMPT_PATH.read_text(encoding="utf-8")
    return "Tu es JARVIS en mode autonome. Accomplis la tâche étape par étape."


def _effective_limit(config_value: int, hard_max: int) -> int | None:
    """0 = illimité (None). Sinon min(config, hard_max) si hard_max > 0."""
    if config_value == 0:
        return None
    if hard_max > 0:
        return min(config_value, hard_max)
    return config_value


def _prepare_action_for_loop(action: dict) -> dict:
    """Auto-confirme les actions qui nécessitent une validation en mode autonome."""
    prepared = dict(action)
    prepared["confirmed"] = True
    if prepared.get("type") == "terminal" and prepared.get("complex") is not False:
        # Tâches complexes par défaut en mode loop
        if "complex" not in prepared:
            prepared["complex"] = True
    return prepared


def _extract_action_from_llm(response: str) -> tuple[dict | None, str, bool]:
    """Parse la réponse LLM : action, texte descriptif, terminé."""
    if not response:
        return None, "", False

    upper = response.upper()
    if re.search(r"\bTERMINE\b", upper) and "```action" not in response.lower():
        return None, strip_assistant_code_fences(response.strip()), True

    action_re = re.compile(r"```action\s*\n?(.*?)```", re.DOTALL | re.IGNORECASE)
    match = action_re.search(response)
    if match:
        json_str = match.group(1).strip()
        clean = (response[: match.start()] + response[match.end() :]).strip()
        try:
            action = json.loads(json_str)
            if isinstance(action, dict) and action.get("type"):
                return action, strip_assistant_code_fences(clean), False
        except json.JSONDecodeError:
            logger.warning("[loop] JSON action invalide : %s", json_str[:200])

    inline_re = re.compile(r'\{\s*"type"\s*:\s*"(\w+)"', re.DOTALL)
    m2 = inline_re.search(response)
    if m2:
        start = m2.start()
        depth = 0
        end = start
        for i, ch in enumerate(response[start:], start):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        try:
            action = json.loads(response[start:end])
            if isinstance(action, dict) and action.get("type"):
                clean = response[:start] + response[end:]
                return action, strip_assistant_code_fences(clean.strip()), False
        except json.JSONDecodeError:
            pass

    if "TERMINE" in upper:
        return None, strip_assistant_code_fences(response.strip()), True

    return None, strip_assistant_code_fences(response.strip()), False


def _summarize_result(result: dict, max_len: int = 800) -> str:
    for key in ("output", "message", "error"):
        val = result.get(key)
        if val:
            text = str(val)
            return text if len(text) <= max_len else text[: max_len - 3] + "..."
    return str(result)[:max_len]


def _build_context_summary(results: list[dict]) -> str:
    lines: list[str] = []
    for entry in results:
        step = entry.get("step")
        if not isinstance(step, int):
            continue
        action = entry.get("action") or {}
        result = entry.get("result") or {}
        status = "OK" if result.get("ok") else "ÉCHEC"
        lines.append(
            f"Étape {step} [{status}] {action.get('type', '?')}: "
            f"{_summarize_result(result, 600)}"
        )
    return "\n".join(lines) if lines else "(aucune étape exécutée)"


async def _emit(
    callback: LoopEventCallback | None,
    event_type: str,
    data: dict,
) -> None:
    if callback is None:
        return
    try:
        await callback(event_type, data)
    except Exception as exc:
        logger.warning("[loop] callback %s : %s", event_type, exc)


async def run_autonomous_loop(
    user_message: str,
    conversation_id: int | None,
    context: dict | None,
    *,
    on_event: LoopEventCallback | None = None,
    unlimited: bool | None = None,
) -> dict:
    """Exécute une boucle autonome jusqu'à TERMINE ou épuisement des limites.

    Args:
        user_message: Tâche décrite par l'utilisateur (sans le préfixe /loop).
        conversation_id: ID conversation pour persistance workflow.
        context: Contexte enrichi JARVIS (mails, météo, etc.).
        on_event: Callback async ``(event_type, payload)`` pour WebSocket UI.
        unlimited: Force le mode sans limite. Défaut : config LOOP_UNLIMITED.

    Returns:
        Dict avec results, step_count, total_output_chars, final_status,
        workflow_id, synthesis, total_llm_calls, total_cost.
    """
    from actions import execute_action

    if unlimited is None:
        unlimited = config.LOOP_UNLIMITED

    max_steps = None if unlimited else _effective_limit(config.LOOP_MAX_STEPS, HARD_SAFETY_MAX_STEPS)
    max_output = None if unlimited else (None if config.LOOP_MAX_OUTPUT_CHARS == 0 else config.LOOP_MAX_OUTPUT_CHARS)
    max_llm = None if unlimited else (None if config.LOOP_MAX_LLM_CALLS == 0 else config.LOOP_MAX_LLM_CALLS)

    if max_steps is None and unlimited:
        max_steps = HARD_SAFETY_MAX_STEPS  # garde-fou technique uniquement

    workflow_id: int | None = None
    if conversation_id:
        try:
            from database import create_agentic_workflow, update_agentic_workflow

            workflow_id = create_agentic_workflow(
                conversation_id,
                f"[LOOP] {user_message}",
                {"type": "loop", "task": user_message},
            )
        except Exception as exc:
            logger.warning("[loop] création workflow : %s", exc)

    if not user_message or not str(user_message).strip():
        synthesis = "Tâche vide — précisez : /loop [description]"
        await _emit(on_event, "loop_done", {"status": "failed", "steps": 0, "synthesis": synthesis})
        return _finalize_loop(
            [], workflow_id, "failed", 0, 0, 0.0, synthesis,
        )

    # ── Routage cognitif : tâches techniques → Cursor (préféré à Open Interpreter)
    try:
        from jarvis.cognitive import route_request

        intent = route_request(str(user_message), interaction_mode="loop")
        if intent.execution_type == "cursor" and getattr(config, "CURSOR_DELEGATION_ENABLED", True):
            from integrations.cursor_delegation import cursor_delegation

            await _emit(on_event, "loop_progress", {
                "message": "Délégation technique à Cursor CLI (worktree isolé).",
                "routing": intent.to_diagnostic(),
            })
            job = await cursor_delegation.enqueue(
                title=str(user_message)[:120],
                user_request=str(user_message),
                template_id=intent.template_id or "feature_implementation",
                interaction_mode="loop",
                routing=intent.to_diagnostic(),
                auto_start=True,
            )
            synthesis = (
                f"Tâche technique déléguée à Cursor — job `{job.get('job_id')}`. "
                "Suivi via /api/cursor/jobs."
            )
            await _emit(on_event, "loop_done", {
                "status": "completed",
                "steps": 1,
                "synthesis": synthesis,
                "cursor_job_id": job.get("job_id"),
            })
            return _finalize_loop(
                [{"step": 1, "result": {"ok": True, "job": job}}],
                workflow_id, "completed", 0, 0, 0.0, synthesis,
            )
    except Exception as exc:
        logger.warning("[loop] délégation Cursor skip: %s", exc)

    results: list[dict] = []
    total_output_chars = 0
    total_llm_calls = 0
    total_cost = 0.0
    consecutive_failures = 0
    same_action_failures: dict[str, int] = {}

    loop_prompt = _load_loop_prompt().replace("{{user_name}}", config.USER_NAME)
    model = config.LOOP_MODEL or config.DEEPSEEK_MAIN_MODEL
    decision_model = config.LOOP_DECISION_MODEL or config.DEEPSEEK_FAST_MODEL

    ctx = context or {}
    memory_hint = ""
    if ctx.get("tasks_context"):
        memory_hint += f"\n[TÂCHES]\n{ctx['tasks_context'][:1500]}"
    if ctx.get("screen_context"):
        memory_hint += f"\n[ÉCRAN]\n{ctx['screen_context']}"

    await _emit(on_event, "loop_started", {
        "task": user_message,
        "max_steps": max_steps,
        "unlimited": unlimited,
        "model": model,
    })

    async def _llm_decide(prompt: str, *, use_main: bool = False) -> str:
        nonlocal total_llm_calls, total_cost
        if max_llm is not None and total_llm_calls >= max_llm:
            raise AutonomousLoopError(
                f"Limite d'appels LLM atteinte ({max_llm}). "
                "Augmente LOOP_MAX_LLM_CALLS ou active LOOP_UNLIMITED=true."
            )
        chosen = model if use_main else decision_model
        resp = await llm.chat(
            messages=[{"role": "user", "content": prompt}],
            model=chosen,
            system=loop_prompt,
            max_tokens=config.LOOP_MAX_TOKENS,
            temperature=0.2,
        )
        total_llm_calls += 1
        total_cost += float(resp.get("cost") or 0.0)
        return resp.get("content") or ""

    # ── Planification initiale ──
    initial_prompt = (
        f"TÂCHE À ACCOMPLIR AUTONOMEMENT :\n{user_message}\n"
        f"{memory_hint}\n\n"
        "C'est la première étape. Décris brièvement ta démarche puis fournis "
        "la première action ```action {...}``` ou TERMINE si rien à faire."
    )
    try:
        initial_response = await _llm_decide(initial_prompt, use_main=True)
    except AutonomousLoopError as exc:
        return _finalize_loop(
            results, workflow_id, "failed", total_output_chars,
            total_llm_calls, total_cost, str(exc),
        )

    action, description, done = _extract_action_from_llm(initial_response)
    if description:
        await _emit(on_event, "loop_progress", {"message": description})

    if done and not action:
        synthesis = description or "Tâche déjà accomplie ou aucune action requise."
        await _emit(on_event, "loop_done", {"status": "completed", "steps": 0, "synthesis": synthesis})
        return _finalize_loop(
            results, workflow_id, "completed", total_output_chars,
            total_llm_calls, total_cost, synthesis,
        )

    if not action:
        synthesis = description or "Impossible de planifier la première action."
        await _emit(on_event, "loop_done", {"status": "failed", "steps": 0, "synthesis": synthesis})
        return _finalize_loop(
            results, workflow_id, "failed", total_output_chars,
            total_llm_calls, total_cost, synthesis,
        )

    current_action = _prepare_action_for_loop(action)
    step_limit = max_steps or HARD_SAFETY_MAX_STEPS

    for step in range(step_limit):
        logger.info(
            "[loop] Step %d/%s: type=%s",
            step + 1,
            max_steps or "∞",
            current_action.get("type", "?"),
        )

        await _emit(on_event, "loop_step", {
            "step": step + 1,
            "action_type": current_action.get("type"),
            "preview": _summarize_result(current_action, 200),
            "status": "running",
        })

        try:
            result = await execute_action(current_action)
        except Exception as exc:
            result = {"ok": False, "message": str(exc)}

        results.append({"step": step + 1, "action": current_action, "result": result})

        output_text = _summarize_result(result, 2000)
        total_output_chars += len(output_text)

        await _emit(on_event, "loop_step", {
            "step": step + 1,
            "action_type": current_action.get("type"),
            "ok": result.get("ok"),
            "output_preview": output_text[:500],
            "status": "done" if result.get("ok") else "failed",
        })

        if result.get("ok"):
            consecutive_failures = 0
        else:
            consecutive_failures += 1
            action_key = json.dumps(current_action, sort_keys=True, default=str)
            same_action_failures[action_key] = same_action_failures.get(action_key, 0) + 1

        if consecutive_failures >= config.LOOP_MAX_CONSECUTIVE_FAILURES:
            reason = (
                f"Trop d'échecs consécutifs ({consecutive_failures}) : "
                f"{_summarize_result(result, 300)}"
            )
            results.append({"step": "aborted", "reason": reason})
            break

        if max_output is not None and total_output_chars > max_output:
            results.append({
                "step": "truncated",
                "reason": f"Limite de sortie ({max_output} chars)",
            })
            break

        context_summary = _build_context_summary(results)
        decision_prompt = (
            f"HISTORIQUE D'EXÉCUTION :\n{context_summary}\n\n"
            f"TÂCHE ORIGINALE : {user_message}\n\n"
            "La tâche est-elle terminée ? Si OUI → réponds TERMINE.\n"
            "Si NON → décris la prochaine étape et fournis ```action {...}```.\n"
            "Ne répète pas une action identique qui a échoué 2 fois."
        )

        try:
            decision_text = await _llm_decide(decision_prompt)
        except AutonomousLoopError as exc:
            results.append({"step": "aborted", "reason": str(exc)})
            break

        next_action, next_desc, finished = _extract_action_from_llm(decision_text)

        if next_desc:
            await _emit(on_event, "loop_progress", {"message": next_desc})

        if finished and not next_action:
            break

        if not next_action:
            break

        next_prepared = _prepare_action_for_loop(next_action)
        action_key = json.dumps(next_prepared, sort_keys=True, default=str)
        if same_action_failures.get(action_key, 0) >= 2:
            logger.warning("[loop] Action répétée trop souvent, arrêt")
            results.append({
                "step": "aborted",
                "reason": "Action répétée après échecs multiples",
            })
            break

        current_action = next_prepared

    # ── Synthèse finale ──
    context_summary = _build_context_summary(results)
    synthesis_prompt = (
        f"Résultats de la boucle autonome :\n{context_summary}\n\n"
        f"Tâche demandée : {user_message}\n\n"
        "Synthétise en 2-4 phrases ce qui a été fait, le résultat, et ce qui reste "
        "éventuellement à faire. Ton JARVIS : concis, pas d'emoji, pas de chatbot."
    )
    try:
        synth_resp = await llm.chat(
            messages=[{"role": "user", "content": synthesis_prompt}],
            model=model,
            system=loop_prompt,
            max_tokens=min(config.LOOP_MAX_TOKENS, 800),
            temperature=0.3,
        )
        total_llm_calls += 1
        total_cost += float(synth_resp.get("cost") or 0.0)
        synthesis = strip_assistant_code_fences(synth_resp.get("content") or "")
    except Exception as exc:
        logger.warning("[loop] synthèse : %s", exc)
        synthesis = context_summary[:2000]

    step_count = len([r for r in results if isinstance(r.get("step"), int)])
    final_status = (
        "failed" if consecutive_failures >= config.LOOP_MAX_CONSECUTIVE_FAILURES
        else "partial" if any(
            isinstance(r.get("step"), int) and not r.get("result", {}).get("ok")
            for r in results
        )
        else "completed"
    )

    await _emit(on_event, "loop_done", {
        "status": final_status,
        "steps": step_count,
        "synthesis": synthesis,
        "total_llm_calls": total_llm_calls,
        "total_cost": round(total_cost, 6),
    })

    return _finalize_loop(
        results, workflow_id, final_status, total_output_chars,
        total_llm_calls, total_cost, synthesis,
    )


def _finalize_loop(
    results: list[dict],
    workflow_id: int | None,
    final_status: str,
    total_output_chars: int,
    total_llm_calls: int,
    total_cost: float,
    synthesis: str,
) -> dict:
    step_count = len([r for r in results if isinstance(r.get("step"), int)])

    if workflow_id:
        try:
            from database import update_agentic_workflow

            update_agentic_workflow(
                workflow_id,
                steps_json=json.dumps(results, ensure_ascii=False, default=str),
                status=final_status,
                final_synthesis=synthesis,
                total_steps=step_count,
                total_output_chars=total_output_chars,
            )
        except Exception as exc:
            logger.warning("[loop] mise à jour workflow %s : %s", workflow_id, exc)

    return {
        "results": results,
        "step_count": step_count,
        "total_output_chars": total_output_chars,
        "final_status": final_status,
        "workflow_id": workflow_id,
        "synthesis": synthesis,
        "total_llm_calls": total_llm_calls,
        "total_cost": total_cost,
    }
