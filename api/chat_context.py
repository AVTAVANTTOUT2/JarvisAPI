"""Construction du contexte conversationnel, titres et TTS streaming."""

from __future__ import annotations

import asyncio
from dataclasses import replace
import logging
import uuid
from collections.abc import Mapping
from typing import Any

from fastapi import WebSocket

from api.llm_logging import _schedule_llm_log
from database import get_conversation_history
from integrations import weather
from jarvis.retrieval import (
    RetrievalRequest,
    format_retrieval_context,
    search_knowledge,
)
from jarvis.retrieval.live_sources import refresh_live_sources
from jarvis.security.llm_data_boundary import wrap_untrusted_data

logger = logging.getLogger("jarvis")

_RETRIEVAL_CONTEXT_MAX_CHARS = 8_000
_RETRIEVAL_HISTORY_LIMIT = 30
_RETRIEVAL_RECENT_USER_TURNS = 6


def _history_for_context(
    conversation_id: int,
    current_text: str,
    *,
    limit: int = _RETRIEVAL_HISTORY_LIMIT,
) -> list[dict[str, Any]]:
    """Charge une fois l'historique utile au LLM et à la résolution de références."""

    try:
        rows = get_conversation_history(conversation_id, limit=limit)
    except Exception as exc:
        logger.warning(
            "[ctx] historique conversation %s indisponible : %s", conversation_id, exc
        )
        return []

    history: list[dict[str, Any]] = []
    for row in rows:
        role = str(row.get("role") or "")
        content = str(row.get("content") or "").strip()
        if role not in {"user", "assistant"} or not content:
            continue
        history.append(
            {
                "role": role,
                "content": content,
                "created_at": row.get("created_at"),
            }
        )

    # Certains transports persistent le tour utilisateur avant l'enrichissement,
    # d'autres après. Le texte courant est déjà porté par RetrievalRequest.query.
    if (
        history
        and history[-1]["role"] == "user"
        and history[-1]["content"].strip() == (current_text or "").strip()
    ):
        history.pop()
    return history


def _retrieval_hit_references(hits: Any) -> list[dict[str, Any]]:
    """Expose uniquement les identifiants opaques nécessaires à un run agentique."""

    references: list[dict[str, Any]] = []
    for hit in list(hits or [])[:8]:
        if isinstance(hit, Mapping):
            raw = dict(hit)
        else:
            raw = {
                key: getattr(hit, key)
                for key in (
                    "uid",
                    "reference",
                    "source_type",
                    "source_id",
                    "canonical_id",
                    "id",
                )
                if hasattr(hit, key)
            }
        reference = {
            key: raw[key]
            for key in (
                "uid",
                "reference",
                "source_type",
                "source_id",
                "canonical_id",
                "id",
            )
            if raw.get(key) not in (None, "")
        }
        if reference:
            references.append(reference)
    return references


def _diagnostic_value(value: Any) -> Any:
    return getattr(value, "value", value)


def _merge_live_source_status(result: Any, live_report: Mapping[str, Any]) -> Any:
    """Conserve les hits en cache tout en signalant un contrôle live incomplet."""

    failures = {
        str(source): str(status)
        for source, status in (live_report or {}).items()
        if str(status) in {"degraded", "unavailable"}
    }
    if not failures:
        return result

    unavailable = tuple(sorted(set(result.unavailable_sources).union(failures)))
    diagnostics = tuple(
        dict.fromkeys(
            (
                *result.diagnostics,
                *(f"live:{source}:{status}" for source, status in failures.items()),
            )
        )
    )
    status = (
        "unavailable" if not result.verified_sources and not result.hits else "degraded"
    )
    return replace(
        result,
        status=status,
        unavailable_sources=unavailable,
        diagnostics=diagnostics,
    )


async def _attach_retrieval_context(
    context: dict[str, Any],
    *,
    text: str,
    conversation_id: int,
    interaction_mode: str,
) -> None:
    """Effectue l'unique recherche mémoire du tour et conserve sa provenance."""

    history = _history_for_context(conversation_id, text)
    context["history"] = history
    recent_user_turns = [
        str(message["content"]) for message in history if message.get("role") == "user"
    ][-_RETRIEVAL_RECENT_USER_TURNS:]
    mode = (
        interaction_mode if interaction_mode in {"chat", "voice", "stream"} else "chat"
    )
    request = RetrievalRequest(
        query=text,
        conversation_id=conversation_id,
        recent_user_turns=recent_user_turns,
        interaction_mode=mode,
        max_candidates=20,
        max_hits=8,
        char_budget=_RETRIEVAL_CONTEXT_MAX_CHARS,
    )

    try:
        live_report = await refresh_live_sources(request)
        if live_report:
            context["__retrieval_live"] = dict(live_report)
        result = await asyncio.to_thread(search_knowledge, request)
        result = _merge_live_source_status(result, live_report)
        formatted = format_retrieval_context(
            result,
            max_chars=_RETRIEVAL_CONTEXT_MAX_CHARS,
        )
        if not formatted.strip():
            raise ValueError("retrieval_context_vide")
    except Exception as exc:
        logger.exception("[ctx] retrieval unifié indisponible")
        context["retrieval_context"] = wrap_untrusted_data(
            "KNOWLEDGE_RETRIEVAL",
            f"Recherche mémoire indisponible ({type(exc).__name__}).",
            max_chars=500,
        )
        context["__retrieval"] = {
            "status": "unavailable",
            "verified_sources": [],
            "unavailable_sources": ["retrieval"],
            "diagnostics": [f"retrieval:{type(exc).__name__}"],
            "latency_ms": None,
        }
        context["__retrieval_references"] = []
        context["__retrieval_done"] = True
        return

    context["retrieval_context"] = formatted
    context["__retrieval"] = {
        "status": _diagnostic_value(result.status),
        "verified_sources": list(result.verified_sources),
        "unavailable_sources": list(result.unavailable_sources),
        "diagnostics": list(getattr(result, "diagnostics", ())),
        "latency_ms": getattr(result, "latency_ms", None),
    }
    context["__retrieval_references"] = _retrieval_hit_references(result.hits)
    context["__retrieval_done"] = True


async def _send_tts_streaming(
    ws: WebSocket,
    text: str,
    emotion: str,
    *,
    turn_id: str | None = None,
    cancel_event: Any | None = None,
) -> str:
    """Envoie `speaking`, chunks audio, puis `speech_done` (boucle cliente).

    Annulable via ``cancel_event`` (asyncio.Event) : dès qu'il est set, on
    arrête d'envoyer des chunks et on signale ``speech_cancelled`` pour que
    le client jette l'audio du ``turn_id`` courant.

    Le navigateur reçoit **un seul blob WAV** : contrairement au MP3, des
    fragments WAV concaténés ne forment pas un fichier valide, et le client
    assemble avant de lire. Le chemin qui compte pour la latence — le tour de
    parole local — passe, lui, par la diffusion fragment par fragment
    (``jarvis.audio.tts.playback``).

    Retourne ``"completed"`` | ``"cancelled"`` | ``"skipped"``.
    """
    from audio.audio_format import DEFAULT_TTS_MIME
    from audio.tts_cache import last_tts, speculative_tts
    from jarvis.audio.tts import get_local_tts_provider
    from jarvis.audio.tts.errors import TTSError
    from jarvis.audio.tts.wav import pcm_to_wav

    audio_mime = DEFAULT_TTS_MIME
    payload: dict[str, Any] = {
        "type": "speaking",
        "emotion": emotion,
        "audio_mime": audio_mime,
    }
    if turn_id:
        payload["turn_id"] = turn_id
    await ws.send_json(payload)

    def _cancelled() -> bool:
        return bool(cancel_event is not None and cancel_event.is_set())

    if _cancelled():
        await ws.send_json({"type": "speech_cancelled", "turn_id": turn_id})
        return "cancelled"

    if not (text and text.strip()):
        await ws.send_json({"type": "speech_done", "turn_id": turn_id})
        return "skipped"

    # TTS spéculatif : la réponse correspond à un audio déjà pré-généré
    cached = speculative_tts.get(text, emotion)
    if cached:
        if _cancelled():
            await ws.send_json({"type": "speech_cancelled", "turn_id": turn_id})
            return "cancelled"
        try:
            await ws.send_bytes(cached)
            last_tts.store(text, emotion, cached, audio_mime)
        except asyncio.CancelledError:
            await ws.send_json({"type": "speech_cancelled", "turn_id": turn_id})
            raise
        except Exception as e:
            logger.error("[TTS] envoi cache spéculatif : %s", e)
        if _cancelled():
            await ws.send_json({"type": "speech_cancelled", "turn_id": turn_id})
            return "cancelled"
        await ws.send_json({"type": "speech_done", "turn_id": turn_id})
        return "completed"

    request_id = turn_id or uuid.uuid4().hex
    audio = b""
    provider = None
    try:
        provider = get_local_tts_provider()
        # On consomme les fragments nous-mêmes plutôt que d'appeler la
        # synthèse complète : une annulation en cours de route doit arrêter la
        # génération, pas seulement jeter le résultat à la fin.
        pcm: list[bytes] = []
        sample_rate = provider.info().sample_rate
        channels = provider.info().channels
        async for chunk in provider.stream(
            text,
            request_id=request_id,
            utterance_id=request_id,
        ):
            if _cancelled():
                await provider.cancel(request_id)
                break
            if chunk.data:
                pcm.append(chunk.data)
                sample_rate = chunk.sample_rate
                channels = chunk.channels
        if pcm and not _cancelled():
            audio = pcm_to_wav(
                b"".join(pcm),
                sample_rate=sample_rate,
                channels=channels,
            )
    except asyncio.CancelledError:
        await ws.send_json({"type": "speech_cancelled", "turn_id": turn_id})
        raise
    except TTSError as e:
        # Pas de repli vers un autre moteur ni vers un service distant : le
        # client garde la réponse texte et sait que la voix est indisponible.
        logger.error("[TTS] synthèse locale indisponible : %s", e)
    except Exception as e:
        logger.error("[TTS] Erreur de synthèse : %s", e)

    if _cancelled():
        await ws.send_json({"type": "speech_cancelled", "turn_id": turn_id})
        return "cancelled"

    if audio:
        try:
            await ws.send_bytes(audio)
            last_tts.store(text, emotion, audio, audio_mime)
        except Exception as e:
            logger.error("[TTS] envoi audio : %s", e)

    if _cancelled():
        await ws.send_json({"type": "speech_cancelled", "turn_id": turn_id})
        return "cancelled"
    await ws.send_json({"type": "speech_done", "turn_id": turn_id})
    return "completed"


async def _build_enriched_context(
    text: str,
    conversation_id: int,
    *,
    interaction_mode: str = "chat",
) -> dict:
    """Construit un contexte unique : retrieval borné + services non mémoriels."""

    context: dict = {}
    await _attach_retrieval_context(
        context,
        text=text,
        conversation_id=conversation_id,
        interaction_mode=interaction_mode,
    )

    # La météo est un service instantané, pas une donnée de mémoire. Toutes les
    # autres données personnelles passent exclusivement par le coordinator.
    lower = text.casefold()
    weather_triggers = (
        "météo",
        "meteo",
        "pluie",
        "soleil",
        "parapluie",
        "température",
    )
    if any(trigger in lower for trigger in weather_triggers):
        try:
            if weather and weather.is_available():
                current = await weather.get_current()
                if current:
                    context["weather_context"] = (
                        f"{current.get('city', '?')} : {current.get('temp', '?')}°C, "
                        f"{current.get('description', '?')}"
                    )
        except Exception as exc:
            logger.warning("[ctx] météo indisponible : %s", type(exc).__name__)

    try:
        from jarvis.cognitive import route_request

        intent = route_request(text, interaction_mode=interaction_mode)
        context["__routing"] = intent.to_diagnostic()
        context["__context_trace"] = {
            "selected": ["KNOWLEDGE_HITS"],
            "status": context.get("__retrieval", {}).get("status"),
            "budget_chars": _RETRIEVAL_CONTEXT_MAX_CHARS,
        }
    except Exception as exc:
        logger.debug("[ctx] routage diagnostic indisponible : %s", type(exc).__name__)

    _schedule_llm_log(
        agent="system",
        action_type="context_enrichment",
        payload={
            "conversation_id": conversation_id,
            "keys": sorted(k for k in context if not k.startswith("__")),
            "key_count": len(context),
            "routing": context.get("__routing"),
            "context_trace": context.get("__context_trace"),
            "retrieval": context.get("__retrieval"),
        },
        status="success",
    )
    return context
