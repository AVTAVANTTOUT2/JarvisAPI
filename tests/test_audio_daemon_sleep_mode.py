"""Mode veille du daemon audio — récupération et non-collision avec commandes de contrôle.

Le mode veille coupait la boucle d'écoute avant la détection du wake word : une
fois endormi, le daemon restait sourd jusqu'au redémarrage du processus. Ces
tests verrouillent les deux moitiés du correctif — le comportement observable
d'un tour de parole, et la structure de la boucle VAD qui ne peut pas être
exercée sans matériel audio.
"""

from __future__ import annotations

import asyncio
import inspect
from unittest.mock import AsyncMock, patch

import pytest


def _pcm_chunk(samples: int = 480, value: int = 8000) -> bytes:
    import struct

    return struct.pack(f"<{samples}h", *([value] * samples))


def _daemon():
    from scripts.audio_daemon import AudioDaemon

    daemon = AudioDaemon()
    daemon._interrupt_event = asyncio.Event()
    daemon._utterance_queue = asyncio.Queue(maxsize=3)
    daemon._audio_queue = asyncio.Queue(maxsize=300)
    daemon.state = "listening"
    # Conversation déjà ouverte : sinon le tour crée une ligne SQLite réelle.
    daemon._conv_id = 42
    return daemon


def _transcript(text: str):
    """Force la transcription STT sans toucher au moteur local."""
    return patch(
        "audio.stt_local.stt_local.transcribe_with_metadata",
        new_callable=AsyncMock,
        return_value={"text": text, "segments": [], "engine": "test"},
    )


@pytest.mark.asyncio
async def test_sleep_mode_wake_phrase_exits_sleep() -> None:
    """Une formule de réveil doit sortir du mode veille sans LLM."""
    from scripts.audio_daemon import AudioDaemon

    daemon = _daemon()
    daemon._sleep_mode = True

    with (
        _transcript("reveille-toi"),
        patch.object(AudioDaemon, "_play_tts", new_callable=AsyncMock) as play_tts,
        patch.object(AudioDaemon, "_broadcast_state", new_callable=AsyncMock),
        patch.object(AudioDaemon, "_rearm", new_callable=AsyncMock) as rearm,
        patch("scripts.audio_daemon.process_voice_fast", new_callable=AsyncMock) as voice_fast,
    ):
        await daemon._process_single_utterance(_pcm_chunk(), stt_available=True)
        await asyncio.sleep(0)

    assert daemon._sleep_mode is False
    play_tts.assert_awaited_once()
    voice_fast.assert_not_awaited()
    rearm.assert_not_awaited()


@pytest.mark.asyncio
async def test_sleep_mode_ignores_non_wake_utterance() -> None:
    """En veille, une phrase ordinaire est ignorée sans appeler le LLM."""
    from scripts.audio_daemon import AudioDaemon

    daemon = _daemon()
    daemon._sleep_mode = True

    with (
        _transcript("quel temps fait-il"),
        patch.object(AudioDaemon, "_broadcast_state", new_callable=AsyncMock),
        patch.object(AudioDaemon, "_rearm", new_callable=AsyncMock) as rearm,
        patch("scripts.audio_daemon.process_voice_fast", new_callable=AsyncMock) as voice_fast,
    ):
        await daemon._process_single_utterance(_pcm_chunk(), stt_available=True)

    assert daemon._sleep_mode is True
    voice_fast.assert_not_awaited()
    rearm.assert_awaited_once()
    assert rearm.await_args.kwargs.get("reason") == "sleep_mode"


@pytest.mark.asyncio
async def test_control_command_does_not_wake_from_sleep() -> None:
    """Une commande de contrôle prononcée en veille ne réveille pas JARVIS.

    Seules une formule de réveil et le wake word sortent de la veille. Réveiller
    sur « stop » serait invisible pour l'utilisateur : aucune parole ne le lui
    signale, et le micro redeviendrait actif à son insu.
    """
    from scripts.audio_daemon import AudioDaemon

    daemon = _daemon()
    daemon._sleep_mode = True

    with (
        _transcript("stop"),
        patch.object(AudioDaemon, "_play_tts", new_callable=AsyncMock),
        patch.object(AudioDaemon, "_broadcast_state", new_callable=AsyncMock),
        patch.object(AudioDaemon, "_rearm", new_callable=AsyncMock) as rearm,
        patch("scripts.audio_daemon.process_voice_fast", new_callable=AsyncMock) as voice_fast,
    ):
        await daemon._process_single_utterance(_pcm_chunk(), stt_available=True)

    assert daemon._sleep_mode is True
    voice_fast.assert_not_awaited()
    rearm.assert_awaited_once()
    assert rearm.await_args.kwargs.get("reason") == "sleep_mode"


@pytest.mark.asyncio
async def test_silence_is_not_a_sleep_phrase() -> None:
    """« silence » est une commande d'arrêt, pas une mise en veille.

    Le daemon ne la traite pas lui-même : elle traverse le pipeline, dont le
    point d'application unique (`api.voice_fastpath._match_voice_control`) coupe
    la parole. Dupliquer cette table dans le daemon ferait taire les
    confirmations parlées des autres commandes.
    """
    from scripts.audio_daemon import SLEEP_PHRASES, AudioDaemon

    assert "silence" not in SLEEP_PHRASES
    assert "pause" not in SLEEP_PHRASES

    daemon = _daemon()

    with (
        _transcript("silence"),
        patch.object(AudioDaemon, "_play_tts", new_callable=AsyncMock),
        patch.object(AudioDaemon, "_broadcast_state", new_callable=AsyncMock),
        patch.object(AudioDaemon, "_rearm", new_callable=AsyncMock),
        patch(
            "scripts.audio_daemon.process_voice_fast",
            new_callable=AsyncMock,
            return_value={"text": "", "emotion": "neutral", "latency_ms": 0},
        ) as voice_fast,
    ):
        await daemon._process_single_utterance(_pcm_chunk(), stt_available=True)

    assert daemon._sleep_mode is False
    voice_fast.assert_awaited_once()


@pytest.mark.asyncio
async def test_control_table_still_owns_silence() -> None:
    """Le point d'application unique reconnaît toujours « silence »."""
    from api.voice_fastpath import _match_voice_control

    assert _match_voice_control("silence") is not None


@pytest.mark.asyncio
async def test_sleep_phrase_enters_sleep_mode() -> None:
    """Une formule de veille explicite active le mode veille."""
    from scripts.audio_daemon import AudioDaemon

    daemon = _daemon()

    with (
        _transcript("mets-toi en veille"),
        patch.object(AudioDaemon, "_play_tts", new_callable=AsyncMock),
        patch.object(AudioDaemon, "_broadcast_state", new_callable=AsyncMock),
        patch("scripts.audio_daemon.process_voice_fast", new_callable=AsyncMock) as voice_fast,
    ):
        await daemon._process_single_utterance(_pcm_chunk(), stt_available=True)
        await asyncio.sleep(0)

    assert daemon._sleep_mode is True
    voice_fast.assert_not_awaited()


# ── Boucle VAD : contrat de source ────────────────────────────────────────────
#
# `_vad_loop_safe` démarre un thread pyaudio et lit un flux micro réel : le
# chemin ne peut pas être exercé en test sans matériel. La régression corrigée
# ici est structurelle — une sortie anticipée en tête de boucle — donc c'est la
# structure qu'on verrouille, plutôt que d'écrire un test qui réimplémenterait
# la condition et ne prouverait rien du code de production.


def _vad_loop_source() -> str:
    from scripts.audio_daemon import AudioDaemon

    return inspect.getsource(AudioDaemon._vad_loop_safe)


def test_vad_loop_has_no_sleep_mode_shortcircuit() -> None:
    """La veille ne doit plus vider la file ni court-circuiter la boucle."""
    source = _vad_loop_source()

    assert "_sleep_mode" in source, "la boucle doit encore connaître la veille"

    for line in source.splitlines():
        stripped = line.strip()
        if stripped.startswith("if self._sleep_mode:"):
            indent = len(line) - len(line.lstrip())
            # Une garde en tête de boucle (indentation faible, suivie d'un
            # `continue`) est exactement la régression : elle rend sourde la
            # détection du wake word placée en dessous.
            assert indent > 16, (
                "garde de veille en tête de boucle VAD — le wake word "
                "redeviendrait inatteignable"
            )


def test_vad_loop_wake_word_exits_sleep_mode() -> None:
    """La détection du wake word doit sortir de la veille."""
    source = _vad_loop_source()

    assert "self.exit_sleep_mode()" in source, (
        "le wake word doit rendre la main : sans cela, la veille ne se termine "
        "que par une formule de réveil, qui n'est pas écoutée si le daemon "
        "attend justement le wake word"
    )


def test_exit_sleep_mode_clears_the_flag() -> None:
    """Sortie de veille : effet observable, pas seulement un log."""
    daemon = _daemon()
    daemon._sleep_mode = True

    daemon.exit_sleep_mode()

    assert daemon._sleep_mode is False
