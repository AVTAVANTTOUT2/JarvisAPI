"""Couche transverse de la parole de JARVIS.

Ce paquet ne synthétise rien et ne joue rien : il décrit **ce que** JARVIS
s'apprête à dire et sous quelles règles. La synthèse vit dans
``jarvis/audio/tts``, la lecture dans ``audio/voice_queue.py``, le transport
dans ``api/`` et ``scripts/audio_daemon.py``.

La séparation est délibérée. Avant ce module, la personnalité vocale était
dispersée dans une dizaine de producteurs — chaînes en dur du daemon, réponses
de repli des fast-paths, phrases pré-synthétisées du cache, prompts persona —
qui pouvaient se contredire et s'empiler dans le même tour de parole.
"""

from __future__ import annotations

from jarvis.voice.address import (
    HONORIFIC_ALLOWED_KINDS,
    VoiceAddressPolicy,
    VoiceSession,
    VoiceUtterance,
    VoiceUtteranceKind,
    apply_address_policy,
    close_voice_session,
    get_voice_session,
    reset_voice_sessions,
    strip_honorific,
)

__all__ = [
    "HONORIFIC_ALLOWED_KINDS",
    "VoiceAddressPolicy",
    "VoiceSession",
    "VoiceUtterance",
    "VoiceUtteranceKind",
    "apply_address_policy",
    "close_voice_session",
    "get_voice_session",
    "reset_voice_sessions",
    "strip_honorific",
]
