"""Chemins vocaux sans LLM, persistance différée et appel Flash instrumenté.

Extrait de ``api/voice_processing.py`` : ce sont les pièces qui décident de
**ne pas** payer le pipeline complet (commandes de contrôle, interpellations),
plus les deux fonctions qui déterminent la latence perçue d'un tour de parole —
la persistance SQLite et l'appel au modèle.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

import config
import llm
from api.voice_support import _save_voice_messages

logger = logging.getLogger("jarvis")

# ── Commandes de contrôle vocal (barge-in) — zéro LLM, réponse instantanée ──
#
# Politique produit (Option A — commande uniquement) :
# - Pendant la lecture TTS, seuls les énoncés courts (≤30 car.) correspondant
#   exactement à une commande de contrôle interrompent la synthèse.
# - Exemples reconnus : « arrête », « stop », « annule », « silence », « continue ».
# - Toute autre parole pendant le TTS est ignorée (pas de barge-in libre).
# - Hors TTS, les mêmes commandes sont traitées en priorité avant le LLM.
# - Annulation explicite côté client : message WebSocket ``voice_cancel``.
_VOICE_CONTROL_MAX_LEN = 30

SILENT_ACKNOWLEDGEMENT = ""
"""Réponse d'une commande d'arrêt : couper la parole, sans en produire une autre.

Répondre « Bien. » à « stop » est une contradiction — on demande le silence et
on obtient une phrase de plus, prononcée par-dessus l'interruption. La commande
coupe la lecture et rend la main ; l'absence de réponse *est* la réponse.
"""

_VOICE_CONTROL_COMMANDS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("arrete", "arrête", "stop", "tais-toi", "tais toi", "chut", "silence", "stoppe",
      "plus court", "coupe"),
     SILENT_ACKNOWLEDGEMENT),
    (("annule", "annule tout", "laisse tomber", "oublie", "oublie ca", "oublie ça"),
     "C'est annulé."),
    (("continue", "poursuis", "vas-y continue"),
     "Je continue."),
    (("merci ca suffit", "merci ça suffit", "c'est tout", "c'est bon merci", "ca suffit", "ça suffit"),
     "À votre service."),
)


def _match_voice_control(text: str) -> str | None:
    """Commande de contrôle barge-in ? Retourne la réponse fixe ou None."""
    t = (text or "").strip().lower().rstrip(".!?, ")
    if not t or len(t) > _VOICE_CONTROL_MAX_LEN:
        return None
    for keywords, response in _VOICE_CONTROL_COMMANDS:
        if t in keywords:
            return response
    return None


# ── Interpellations triviales — zéro LLM, zéro contexte ─────────────────────
#
# « Jarvis ? » n'est pas une question : c'est une vérification de présence.
# La faire traverser l'historique de conversation, le contexte écran et un
# aller-retour réseau coûtait plusieurs secondes pour une réponse qui ne
# dépend d'aucune de ces données.
#
# Le périmètre est volontairement étroit et fermé : uniquement des énoncés
# entièrement composés du nom de l'assistant et/ou d'une formule de présence,
# éventuellement répétés (le STT rend « JARVIS. JARVIS. JARVIS. »). Tout ce
# qui porte une demande réelle continue vers le pipeline complet.
_HAIL_MAX_LEN = 60
_HAIL_NAME = frozenset({"jarvis", "jarvice", "jarviss"})
_HAIL_FILLERS = frozenset({"allo", "allô", "hey", "eh", "ok", "hé", "oh"})
_HAIL_PRESENCE_PHRASES = frozenset({
    "tu m'entends", "tu mentends", "tu m entends",
    "vous m'entendez", "vous m entendez",
    "tu es la", "tu es là", "t'es la", "t'es là", "tes la",
    "vous etes la", "vous êtes là", "vous etes là",
    "tu me recois", "tu me reçois",
})
# Une vérification de présence n'ouvre pas une session : elle constate qu'elle
# est déjà ouverte. Pas d'honorifique — il serait répété à chaque « Jarvis ? ».
_HAIL_RESPONSE = "Je vous écoute."


def _normalize_hail(text: str) -> str:
    """Minuscule, sans ponctuation ni accents parasites de fin."""
    return re.sub(r"[.!?,;:…]+", " ", (text or "").lower()).strip()


def match_trivial_hail(text: str) -> str | None:
    """Simple interpellation ? Retourne la réponse fixe, sinon ``None``.

    Ce n'est pas un contournement du problème général de latence : le pipeline
    complet reste mesuré et optimisé par ailleurs. C'est la reconnaissance
    qu'une vérification de présence n'a aucun contexte à consulter.
    """
    cleaned = _normalize_hail(text)
    if not cleaned or len(cleaned) > _HAIL_MAX_LEN:
        return None

    # Retirer d'abord les formules de présence complètes, puis vérifier que le
    # reste n'est que le nom et des interjections.
    for phrase in _HAIL_PRESENCE_PHRASES:
        cleaned = cleaned.replace(phrase, " ")

    words = [w for w in cleaned.split() if w]
    if not words:
        # L'énoncé n'était qu'une formule de présence (« tu m'entends ? »).
        return _HAIL_RESPONSE
    if all(w in _HAIL_NAME or w in _HAIL_FILLERS for w in words):
        # Au moins une occurrence du nom : « ok » seul n'est pas une interpellation.
        return _HAIL_RESPONSE if any(w in _HAIL_NAME for w in words) else None
    return None


# ── Instrumentation de latence ──────────────────────────────────────────────


def _mark(trace: Any | None, event_name: str, **fields: Any) -> None:
    """Pose une étape de latence si une trace accompagne le tour de parole."""
    if trace is None:
        return
    try:
        from audio import voice_latency as vl

        trace.mark(getattr(vl, event_name), **fields)
    except Exception:
        pass  # l'instrumentation ne doit jamais faire échouer une réponse


# ── Persistance hors du chemin de réponse ───────────────────────────────────
#
# Le verrou préserve l'ordre entre deux tours rapprochés : sans lui,
# l'historique pourrait s'écrire dans le désordre.
_persist_lock = asyncio.Lock()

# Références fortes sur les écritures en vol. Sans elles, le ramasse-miettes
# peut collecter une tâche détachée avant qu'elle n'ait écrit : le tour de
# parole serait perdu silencieusement.
_pending_persists: set[asyncio.Task] = set()


async def flush_pending_persists(timeout: float = 5.0) -> None:
    """Attend les écritures différées — à appeler avant un arrêt propre."""
    pending = {t for t in _pending_persists if not t.done()}
    if not pending:
        return
    try:
        await asyncio.wait_for(asyncio.gather(*pending, return_exceptions=True), timeout)
    except asyncio.TimeoutError:
        logger.warning("[voice_fast] %d écriture(s) encore en vol à l'arrêt", len(pending))


def _persist_voice_messages_async(
    conversation_id: int, user_text: str, reply: str, cost: float,
    trace: Any | None = None,
) -> None:
    """Programme la persistance sans la faire attendre par l'utilisateur.

    ``save_message`` est un appel SQLite synchrone : exécuté dans la coroutine,
    il bloque la boucle — donc le VAD, la file vocale et le WebSocket — avant
    même que le TTS ne démarre.
    """

    async def _write() -> None:
        async with _persist_lock:
            _mark(trace, "USER_MESSAGE_PERSIST_STARTED", text_chars=len(user_text))
            _mark(trace, "ASSISTANT_MESSAGE_PERSIST_STARTED", text_chars=len(reply))
            try:
                await asyncio.to_thread(
                    _save_voice_messages, conversation_id, user_text, reply, cost,
                )
                _mark(trace, "USER_MESSAGE_PERSIST_COMPLETED", ok=True)
                _mark(trace, "ASSISTANT_MESSAGE_PERSIST_COMPLETED", ok=True)
            except Exception as e:
                _mark(trace, "ASSISTANT_MESSAGE_PERSIST_COMPLETED", ok=False)
                logger.error("[voice_fast] persistance du tour : %s", e)

    try:
        asyncio.get_running_loop()
    except RuntimeError:  # hors boucle (tests synchrones) — écriture directe
        _save_voice_messages(conversation_id, user_text, reply, cost)
        return
    task = asyncio.create_task(_write(), name="voice-persist")
    _pending_persists.add(task)
    task.add_done_callback(_pending_persists.discard)


# ── Appel Flash ─────────────────────────────────────────────────────────────


async def _voice_llm_call(
    *,
    messages: list[dict],
    system: str,
    max_tokens: int,
    temperature: float,
    trace: Any | None = None,
) -> dict:
    """Appel Flash streamé quand c'est possible, bufferisé sinon.

    Le streaming ne sert pas à parler plus tôt — la réponse peut contenir un
    bloc ``action`` dont le résultat remplace le texte, donc rien ne doit être
    prononcé avant la fin de la passe 1. Il sert à mesurer ``llm.first_token``,
    seule façon de distinguer un modèle lent d'un réseau lent.
    """
    if bool(getattr(config, "VOICE_LLM_STREAMING", True)):
        try:
            return await llm.chat_stream_collect(
                messages=messages,
                model=config.DEEPSEEK_FAST_MODEL,
                system=system,
                max_tokens=max_tokens,
                temperature=temperature,
                on_first_token=lambda: _mark(
                    trace, "LLM_FIRST_TOKEN", model=config.DEEPSEEK_FAST_MODEL,
                ),
            )
        except Exception as e:
            logger.warning("[voice_fast] flux LLM indisponible (%s) — appel bufferisé", e)

    return await llm.chat(
        messages=messages,
        model=config.DEEPSEEK_FAST_MODEL,
        system=system,
        max_tokens=max_tokens,
        temperature=temperature,
    )


__all__ = [
    "SILENT_ACKNOWLEDGEMENT",
    "_mark",
    "_match_voice_control",
    "_persist_voice_messages_async",
    "_voice_llm_call",
    "flush_pending_persists",
    "match_trivial_hail",
]
