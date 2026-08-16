"""Convergence des canaux conversationnels vers le runtime agentique générique."""

from __future__ import annotations

import logging
import re
import unicodedata
from typing import Any

import config
from database import current_profile_id, save_message
from jarvis.agentic import (
    classify_agentic_request,
    get_agentic_service,
    select_capability_profile,
)
from jarvis.cognitive import route_request
from jarvis.agentic.desktop_workspace import resolve_desktop_workspace
from jarvis.agentic.models import (
    AgenticRequestCategory,
    ApprovalDecision,
    normalize_agentic_client_context,
)
from api.agentic_context import agentic_memory_context as _agentic_memory_context


logger = logging.getLogger(__name__)

_UUID_PATTERN = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}"
_APPROVAL_ID_PATTERN = r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}"

_DELEGATED_CATEGORIES = frozenset(
    {
        AgenticRequestCategory.WORKFLOW,
        AgenticRequestCategory.AGENTIC_READONLY,
        AgenticRequestCategory.AGENTIC_REVERSIBLE,
        AgenticRequestCategory.AGENTIC_EXTERNAL_EFFECT,
        AgenticRequestCategory.AGENTIC_HIGH_RISK,
    }
)


def _strip_explicit_command(text: str) -> tuple[str, bool]:
    stripped = text.strip()
    for prefix in ("/agent ", "/agentic "):
        if stripped.lower().startswith(prefix):
            return stripped[len(prefix) :].strip(), True
    return text, False


def _normalized(text: str) -> str:
    value = unicodedata.normalize("NFKD", text.lower())
    value = "".join(char for char in value if not unicodedata.combining(char))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", value).split())


def _context_run(service: Any, conversation_id: int, text: str) -> Any | None:
    labelled = re.search(
        rf"(?:run|execution|tache)\s*(?:id)?\s*[:#-]?\s*({_UUID_PATTERN})", text, re.I
    )
    if labelled:
        return service.get(labelled.group(1))
    conversation = str(conversation_id)
    return next(
        (run for run in service.list(limit=100) if run.conversation_id == conversation),
        None,
    )


def _control_response(text: str, run: Any | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "text": text,
        "emotion": "neutral",
        "action": None,
        "action_result": None,
        "agent": "agentic",
        "model": "runtime",
        "cost": 0.0,
    }
    if run is not None:
        payload["agentic_run"] = {
            "run_id": run.run_id,
            "status": run.status.value,
            "phase": run.phase,
            "category": run.category.value,
        }
    return payload


def _persist_control_response(
    conversation_id: int, response: dict[str, Any], persist_assistant: bool
) -> dict[str, Any]:
    if persist_assistant:
        try:
            save_message(
                conversation_id,
                "assistant",
                response["text"],
                agent="agentic",
                model="runtime",
                tokens_in=0,
                tokens_out=0,
                cost=0.0,
            )
        except Exception:
            logger.exception("impossible de persister la réponse de contrôle agentique")
    return response


async def _maybe_handle_control_intent(
    text: str,
    conversation_id: int,
    *,
    channel: str,
    device: str | None,
    idempotency_key: str | None,
    persist_assistant: bool,
) -> dict[str, Any] | None:
    """Handle unambiguous run controls before classifying a new request."""

    normalized = _normalized(text)
    approval_match = re.search(
        rf"(?:approbation|approval)(?:_id)?\s*[:#-]?\s*({_APPROVAL_ID_PATTERN})(?![A-Za-z0-9._:-])",
        text,
        re.I,
    )
    approve = bool(re.search(r"\b(approuve|autorise|accepte|valide)\b", normalized))
    deny = bool(re.search(r"\b(refuse|rejette|interdis)\b", normalized))
    if approve or deny:
        if approval_match is None or approve == deny:
            return _persist_control_response(
                conversation_id,
                _control_response(
                    "Pour cette validation, indiquez explicitement approuver ou refuser avec l’identifiant exact de l’approbation."
                ),
                persist_assistant,
            )
        approval_id = approval_match.group(1)
        run = _context_run(service := get_agentic_service(), conversation_id, text)
        if run is None or not any(
            approval.approval_id == approval_id
            for approval in service.approvals(run.run_id)
        ):
            # L'identifiant d'approbation n'autorise jamais une recherche inter-profil.
            return _persist_control_response(
                conversation_id,
                _control_response(
                    "Cette approbation n’existe pas dans la tâche liée à cette conversation."
                ),
                persist_assistant,
            )
        decision = ApprovalDecision.APPROVED if approve else ApprovalDecision.DENIED
        decision_id = (
            idempotency_key
            or f"{channel}:{conversation_id}:{approval_id}:{decision.value}"
        )
        await service.decide_approval(
            run.run_id,
            approval_id,
            decision,
            decided_by=f"{channel}:{device or conversation_id}",
            decision_id=decision_id,
        )
        verb = "approuvée" if approve else "refusée"
        return _persist_control_response(
            conversation_id,
            _control_response(
                f"L’approbation {approval_id} a été {verb}.", service.get(run.run_id)
            ),
            persist_assistant,
        )

    action: str | None = None
    if re.search(
        r"\b(mets?(?: la| cette| le)?(?: tache)? en pause|pause la tache)\b", normalized
    ):
        action = "pause"
    elif re.search(
        r"\b(reprends?|relance)(?: la| cette| le)?(?: tache| travail)?\b", normalized
    ):
        action = "resume"
    elif re.search(
        r"\b(annule|arrete)(?: la| cette| le)?(?: tache| travail| run)?\b", normalized
    ):
        action = "cancel"
    status_requested = bool(
        re.search(
            r"\b(ou en est|statut|etat de)(?: la| cette)?(?: tache| execution| run)?\b",
            normalized,
        )
    )
    result_requested = bool(
        re.search(r"\b(lis moi|donne moi|affiche)(?: le)? resultat\b", normalized)
    )
    details_requested = bool(
        re.search(r"\b(ouvre|affiche)(?: les)? details\b", normalized)
    )
    if (
        action is None
        and not status_requested
        and not result_requested
        and not details_requested
    ):
        return None

    service = get_agentic_service()
    run = _context_run(service, conversation_id, text)
    if run is None:
        return _persist_control_response(
            conversation_id,
            _control_response(
                "Je ne trouve aucune tâche agentique liée à cette conversation."
            ),
            persist_assistant,
        )
    if action == "pause":
        run = await service.pause(run.run_id)
        message = "La tâche est en pause."
    elif action == "resume":
        run = await service.resume(run.run_id)
        message = "La tâche reprend."
    elif action == "cancel":
        run = await service.cancel(run.run_id)
        message = "L’annulation de la tâche est demandée."
    elif result_requested:
        verification = run.verification.summary if run.verification else None
        message = verification or f"La tâche est {run.status.value}, phase {run.phase}."
    elif details_requested:
        message = f"Détails de la tâche {run.run_id} ouverts, statut {run.status.value}, phase {run.phase}."
    else:
        message = f"La tâche est {run.status.value}, phase {run.phase}."
    return _persist_control_response(
        conversation_id, _control_response(message, run), persist_assistant
    )


async def _plan_instead_of_running(
    request: str,
    conversation_id: int,
    *,
    channel: str,
    voice_mode: bool,
    persist_assistant: bool,
) -> dict[str, Any] | None:
    """Crée une tâche en attente de plan plutôt que de lancer un run.

    Retourne ``None`` si le pilotage est indisponible : mieux vaut retomber sur
    le comportement historique que de perdre la demande de l'utilisateur. Le
    cas est journalisé, jamais silencieux.
    """

    try:
        from jarvis.task_control.ingest import create_task_from_user_request
    except Exception:
        logger.warning("pilotage de tâches indisponible : démarrage direct")
        return None

    created = await create_task_from_user_request(
        request,
        channel="voice" if voice_mode else channel,
        conversation_id=str(conversation_id),
    )
    if created is None:
        return None

    acknowledgement = (
        "J'ai préparé un plan. Il attend votre validation avant tout démarrage."
        if voice_mode
        else "Un plan est prêt. Ouvrez la tâche pour l'accepter, le refuser ou demander une correction."
    )
    if persist_assistant:
        try:
            save_message(
                conversation_id,
                "assistant",
                acknowledgement,
                agent="agentic",
                model="runtime",
                tokens_in=0,
                tokens_out=0,
                cost=0.0,
            )
        except Exception:
            logger.exception("persistance de l'accusé de planification impossible")
    return {
        "text": acknowledgement,
        "emotion": "neutral",
        "action": {"type": "task_control_task", "task_id": created["task_id"]},
        "action_result": {
            "ok": True,
            "accepted": True,
            "awaiting_plan_approval": True,
            "task_id": created["task_id"],
            "status": created["status"],
        },
        "task_control": {
            "task_id": created["task_id"],
            "status": created["status"],
        },
        "agent": "agentic",
        "model": "runtime",
        "cost": 0.0,
    }


async def maybe_start_agentic_run(
    text: str,
    conversation_id: int,
    *,
    channel: str,
    voice_mode: bool,
    persist_assistant: bool,
    idempotency_key: str | None = None,
    origin: str = "user",
    device: str | None = None,
    locale: str | None = None,
    timezone_name: str | None = None,
    enriched_context: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Retourne une prise en charge immédiate, ou ``None`` pour le legacy."""

    device, locale, timezone_name = normalize_agentic_client_context(
        device=device,
        locale=locale,
        timezone_name=timezone_name,
    )
    control = await _maybe_handle_control_intent(
        text,
        conversation_id,
        channel=channel,
        device=device,
        idempotency_key=idempotency_key,
        persist_assistant=persist_assistant,
    )
    if control is not None:
        return control

    request, explicit = _strip_explicit_command(text)
    if explicit and not request:
        return {
            "text": "Usage : /agent [tâche multi-étapes]",
            "emotion": "neutral",
            "action": None,
            "action_result": None,
            "agent": "agentic",
            "model": "runtime",
            "cost": 0.0,
        }
    runtime_setting = str(getattr(config, "AGENTIC_RUNTIME", "auto")).strip().lower()
    if runtime_setting == "disabled":
        return None
    intent = route_request(
        request,
        interaction_mode="voice" if voice_mode else "chat",
    )
    cognitive_agentic = intent.execution_type in {"agentic", "cursor"}
    classification = classify_agentic_request(
        request,
        origin=origin,
        adaptive=explicit or cognitive_agentic,
        requires_multiple_steps=True if explicit else None,
    )
    if classification.category not in _DELEGATED_CATEGORIES:
        return None

    capability_profile = select_capability_profile(
        request,
        classification.category,
        default_profile_id=str(
            getattr(config, "AGENTIC_DEFAULT_PROFILE", "readonly-research")
        ),
        route_overrides=getattr(config, "AGENTIC_PROFILE_ROUTE_OVERRIDES", {}),
    )

    # Porte de validation humaine. Une demande adressée à JARVIS — tapée,
    # dictée ou reçue — devient une tâche **planifiée**, pas une exécution.
    # C'est le même invariant que pour les tâches créées à la main : personne
    # n'est nécessairement devant l'écran au moment où le travail commencerait.
    if bool(getattr(config, "AGENTIC_REQUIRE_PLAN_APPROVAL", True)):
        planned = await _plan_instead_of_running(
            request,
            conversation_id,
            channel=channel,
            voice_mode=voice_mode,
            persist_assistant=persist_assistant,
        )
        if planned is not None:
            return planned

    service = get_agentic_service()
    runtime_id = service.resolve_runtime_id(
        None if runtime_setting == "auto" else runtime_setting
    )
    if (
        runtime_id is None
        and str(getattr(config, "AGENTIC_RUNTIME_FALLBACK", "disabled")).lower()
        == "legacy"
    ):
        return None

    if enriched_context is None:
        # Import local pour garder agentic_processing indépendant des transports
        # tout en utilisant exactement le même builder que chat/stream/voix.
        from api.chat_context import _build_enriched_context

        interaction_mode = (
            "voice" if voice_mode else "stream" if channel == "websocket" else "chat"
        )
        enriched_context = await _build_enriched_context(
            request,
            conversation_id,
            interaction_mode=interaction_mode,
        )

    selected_context = {
        "request": request,
        "classification": classification.reason,
        **_agentic_memory_context(enriched_context),
    }
    desktop_workspace = resolve_desktop_workspace(request)
    run = await service.create_and_start(
        title=request,
        runtime_id=runtime_id,
        profile_id=current_profile_id(),
        origin=origin,
        channel=channel,
        conversation_id=str(conversation_id),
        device=device,
        locale=locale,
        timezone_name=timezone_name,
        permissions=capability_profile.default_permissions,
        capability_profile_id=capability_profile.profile_id,
        selected_context=selected_context,
        category=classification.category,
        workspace=str(desktop_workspace) if desktop_workspace is not None else None,
        idempotency_key=idempotency_key,
    )
    acknowledgement = (
        "JARVIS travaille sur cette tâche. Je vous préviendrai si une validation est nécessaire."
        if voice_mode
        else "La tâche est lancée. Son avancement et ses validations apparaîtront ici."
    )
    if persist_assistant:
        try:
            save_message(
                conversation_id,
                "assistant",
                acknowledgement,
                agent="agentic",
                model="runtime",
                tokens_in=0,
                tokens_out=0,
                cost=0.0,
            )
        except Exception:
            logger.exception("impossible de persister l'accusé de réception agentique")
    return {
        "text": acknowledgement,
        "emotion": "neutral",
        "action": {"type": "agentic_run", "run_id": run.run_id},
        "action_result": {
            "ok": True,
            "accepted": True,
            "run_id": run.run_id,
            "status": run.status.value,
            "category": run.category.value,
            "capability_profile": capability_profile.profile_id,
        },
        "agentic_run": {
            "run_id": run.run_id,
            "status": run.status.value,
            "phase": run.phase,
            "category": run.category.value,
            "capability_profile": capability_profile.profile_id,
        },
        "agent": "agentic",
        "model": "runtime",
        "cost": 0.0,
        "routing": {
            "category": classification.category.value,
            "reason": classification.reason,
            "capability_profile": capability_profile.profile_id,
        },
    }


__all__ = ["maybe_start_agentic_run"]
