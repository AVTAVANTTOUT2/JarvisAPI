"""Adaptateur vocal du moteur de conversation canonique.

Les raccourcis strictement déterministes restent ici parce qu'ils ne sont pas
des tours conversationnels (barge-in, interpellation, fitness). Tout tour qui
requiert contexte, LLM, confirmation ou action est délégué à
``api.chat_processing._process_message_internal``. Les transports reçoivent
ensuite ``action`` et ``action_result`` comme champs structurés : aucun bloc de
texte ``action`` n'est interprété une seconde fois dans la pile vocale.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

import config
from api.chat_processing import _process_message_internal
from api.voice_fastpath import (
    _match_voice_control,
    _persist_voice_messages_async,
    _voice_llm_call,
    match_trivial_hail,
)
from api.voice_support import _broadcast_voice_debug
from app.fitness.voice import maybe_handle_fitness_voice
from database import _save_voice_debug_trace
from jarvis.voice import VoiceUtteranceKind, apply_address_policy, get_voice_session

logger = logging.getLogger("jarvis")

# Types d'action qui déplacent réellement la frontière de session. Eux seuls
# peuvent porter l'honorifique — et une seule fois par session.
_SESSION_BOUNDARY_ACTIONS: dict[str, VoiceUtteranceKind] = {
    "sleep": VoiceUtteranceKind.FAREWELL,
    "wake": VoiceUtteranceKind.GREETING,
}


def _utterance_kind(result: dict[str, Any]) -> VoiceUtteranceKind:
    """Nature de l'énoncé produit par un tour canonique.

    Déterministe et lue sur le résultat structuré, jamais sur le texte : c'est
    l'action réellement exécutée qui dit si la session s'ouvre ou se ferme, pas
    la formulation choisie par le modèle.
    """
    action = result.get("action")
    if isinstance(action, dict):
        kind = _SESSION_BOUNDARY_ACTIONS.get(str(action.get("type") or ""))
        if kind is not None:
            return kind
    return VoiceUtteranceKind.ANSWER


def _speakable(
    text: str,
    *,
    conversation_id: int,
    kind: VoiceUtteranceKind = VoiceUtteranceKind.ANSWER,
) -> str:
    """Passe obligatoire de toute réponse vocale par la politique d'adresse.

    Unique goulet côté API : daemon local, page ``/voice``, mains-libres et
    mobile empruntent tous ``_process_voice_fast``, donc tous ce filtre. Le
    prompt reste la première ligne de défense ; ceci en est la garantie.
    """
    boundary = kind in (VoiceUtteranceKind.GREETING, VoiceUtteranceKind.FAREWELL)
    return apply_address_policy(
        text,
        kind=kind,
        session=get_voice_session(conversation_id) if boundary else None,
        allow_honorific=boundary,
        session_boundary=boundary,
    )


def _deterministic_voice_result(
    *,
    text: str,
    reply: str,
    model: str,
    conversation_id: int,
    started_at: float,
    stt_ms: int,
    trace: Any | None,
) -> dict[str, Any]:
    """Construit et persiste une réponse locale qui ne requiert aucun moteur."""
    reply = _speakable(reply, conversation_id=conversation_id)
    latency_ms = round((time.time() - started_at) * 1000)
    debug_trace = {
        "input_text": text,
        "response_clean": reply,
        "model": model,
        "latency_stt_ms": int(stt_ms or 0),
        "latency_total_ms": latency_ms,
        "action_detected": None,
        "action_result": None,
    }
    if reply:
        # Une commande d'arrêt ne produit aucune parole : lui inventer un tour
        # d'assistant vide polluerait l'historique et la reprise de contexte.
        _persist_voice_messages_async(conversation_id, text, reply, 0.0, trace)
    return {
        "text": reply,
        "emotion": "neutral",
        "cost": 0.0,
        "action": None,
        "action_result": None,
        "agent": "voice-fastpath",
        "model": model,
        "latency_ms": latency_ms,
        "debug_trace": debug_trace,
    }


async def _process_voice_fast(
    text: str,
    conversation_id: int,
    *,
    stt_ms: int = 0,
    confirmation_session_id: str | None = None,
    trace: Any | None = None,
    on_canonical_turn_started: Callable[[], Awaitable[None]] | None = None,
    agentic_device: str | None = None,
    agentic_locale: str | None = None,
    agentic_timezone: str | None = None,
) -> dict[str, Any]:
    """Traite un tour vocal avec le moteur conversationnel unique.

    Le nom historique est conservé pour les producteurs (daemon, mobile et
    mains-libres). Il désigne désormais un adaptateur de transport/latence, pas
    un second moteur doté de ses propres prompts, actions et passes LLM.
    """
    from api.voice_cognitive import maybe_handle_cognitive_voice

    started_at = time.time()
    confirmation_session_id = confirmation_session_id or f"local-voice:{conversation_id}"

    control = _match_voice_control(text)
    if control is not None:
        return _deterministic_voice_result(
            text=text,
            reply=control,
            model="control",
            conversation_id=conversation_id,
            started_at=started_at,
            stt_ms=stt_ms,
            trace=trace,
        )

    from jarvis.voice_display import handle_voice_display_command

    if display_command := handle_voice_display_command(text):
        result = _deterministic_voice_result(
            text=text,
            reply=str(display_command["reply"]),
            model="voice-display-control",
            conversation_id=conversation_id,
            started_at=started_at,
            stt_ms=stt_ms,
            trace=trace,
        )
        result["action"] = {
            "type": "voice_display_navigation",
            "intent": display_command["intent"],
        }
        result["action_result"] = {"ok": True, **display_command}
        return result

    hail = match_trivial_hail(text)
    if hail is not None:
        result = _deterministic_voice_result(
            text=text,
            reply=hail,
            model="hail",
            conversation_id=conversation_id,
            started_at=started_at,
            stt_ms=stt_ms,
            trace=trace,
        )
        logger.info("[voice] %dms (hail) — aucun appel LLM", result["latency_ms"])
        return result

    if (fitness := maybe_handle_fitness_voice(text, conversation_id, stt_ms=stt_ms)) is not None:
        return fitness

    early = await maybe_handle_cognitive_voice(
        text,
        conversation_id,
        t0=started_at,
        stt_ms=stt_ms,
        confirmation_session_id=confirmation_session_id,
    )
    if early and not early.get("__continue__"):
        return early

    debug_trace = dict((early or {}).get("debug_trace") or {})
    debug_trace.update({
        "input_text": text,
        "latency_stt_ms": int(stt_ms or 0),
        "conversation_engine": "api.chat_processing._process_message_internal",
    })

    turn_started = time.time()
    canonical_task = asyncio.create_task(
        _process_message_internal(
            text,
            conversation_id,
            voice_mode=True,
            confirmation_session_id=confirmation_session_id,
            persist_assistant=False,
            trace=trace,
            agentic_idempotency_key=(
                f"voice:{confirmation_session_id}:{trace.utterance_id}"
                if trace is not None and getattr(trace, "utterance_id", None)
                else None
            ),
            agentic_origin="voice",
            agentic_channel=(
                "android_voice"
                if confirmation_session_id.startswith("mobile:")
                else "voice"
            ),
            agentic_device=(
                agentic_device
                or (
                    confirmation_session_id.removeprefix("mobile:")
                    if confirmation_session_id.startswith("mobile:")
                    else None
                )
            ),
            agentic_locale=agentic_locale,
            agentic_timezone=agentic_timezone,
        ),
        name="voice-canonical-turn",
    )

    # ``on_canonical_turn_started`` signale un **état**, il ne parle pas.
    # Historiquement il déclenchait « Bien, Monsieur. » puis le tour attendait
    # les deux via ``asyncio.gather`` : la vraie réponse pouvait être prête et
    # rester en file derrière une phrase creuse. Le signal part désormais en
    # parallèle et n'entre jamais dans le chemin critique — il est simplement
    # récolté à la fin pour ne laisser aucune tâche orpheline.
    signal_task: asyncio.Task | None = None
    if on_canonical_turn_started is not None:
        async def _signal_turn_started() -> None:
            try:
                await on_canonical_turn_started()
            except Exception:
                logger.warning("[voice] signal de début de tour indisponible", exc_info=True)

        signal_task = asyncio.create_task(
            _signal_turn_started(), name="voice-turn-started-signal",
        )

    try:
        result = await canonical_task
    finally:
        if signal_task is not None:
            await asyncio.gather(signal_task, return_exceptions=True)
    turn_ms = round((time.time() - turn_started) * 1000)

    response_text = _speakable(
        str(result.get("text") or "").strip(),
        conversation_id=conversation_id,
        kind=_utterance_kind(result),
    )
    if not response_text and result.get("agent") != "none":
        # Ne pas accuser la compréhension : la transcription est le plus souvent
        # parfaite, et l'utilisateur reformulait dans le vide en croyant mal
        # articuler. Le diagnostic des deux causes possibles appartient au
        # moteur canonique, seul endroit qui voit la réponse brute du modèle et
        # ses jetons ; il le journalise en WARNING et remonte
        # ``empty_response_cause``.
        response_text = "Je n'ai pas obtenu de réponse."

    total_cost = float(result.get("cost") or 0.0)
    action = result.get("action")
    action_result = result.get("action_result")
    debug_action_result = action_result
    if isinstance(action, dict) and action.get("type") == "clipboard":
        # Le résultat reste utilisable localement par le moteur canonique mais
        # le presse-papiers ne traverse jamais les protocoles voix/mobile ni la
        # table de traces. C'est le même contrat local-only que précédemment.
        action_result = {
            "ok": bool(action_result.get("ok")) if isinstance(action_result, dict) else False,
        }
        if isinstance(result.get("action_result"), dict):
            for key in ("needs_confirmation", "deferred", "error"):
                if key in result["action_result"]:
                    action_result[key] = result["action_result"][key]
        debug_action_result = "[LOCAL_ONLY]"
    latency_ms = round((time.time() - started_at) * 1000)

    debug_trace.update({
        "response_clean": response_text,
        "emotion": result.get("emotion") or "neutral",
        "action_detected": action,
        "action_result": debug_action_result,
        "agent": result.get("agent"),
        "model": result.get("model") or config.DEEPSEEK_FAST_MODEL,
        "cost": total_cost,
        "latency_conversation_turn_ms": turn_ms,
        "latency_total_ms": latency_ms,
    })
    if result.get("empty_response_cause"):
        debug_trace["empty_response_cause"] = result["empty_response_cause"]

    # /loop conserve sa persistance transactionnelle interne. Tous les autres
    # chemins vocaux écrivent le couple hors boucle, dans l'ordre, après que le
    # moteur canonique a produit son résultat structuré.
    if response_text and result.get("agent") != "loop":
        _persist_voice_messages_async(
            conversation_id,
            text,
            response_text,
            total_cost,
            trace,
        )

    asyncio.create_task(_broadcast_voice_debug(debug_trace))
    trace_id = _save_voice_debug_trace(debug_trace)
    logger.info(
        "[voice] %dms (canonical%s) — «%s» → «%s»",
        latency_ms,
        f":{action.get('type')}" if isinstance(action, dict) else "",
        text[:40],
        response_text[:60],
    )

    return {
        **result,
        "text": response_text,
        "emotion": result.get("emotion") or "neutral",
        "cost": total_cost,
        "action": action,
        "action_result": action_result,
        "latency_ms": latency_ms,
        "debug_trace": debug_trace,
        "trace_id": trace_id,
    }


__all__ = [
    "_match_voice_control",
    "_process_voice_fast",
    "_voice_llm_call",
    "match_trivial_hail",
]
