"""Daemon audio natif JARVIS — wake word + conversation mains libres sur le Mac Mini.

Pipeline :
  Thread pyaudio → asyncio.Queue → VAD (Silero + ring pre-roll) → STT local
  → moteur conversationnel vocal unique → file vocale prioritaire → moteur TTS local
  → sounddevice PCM streaming.

Half-duplex par défaut (micro coupé pendant TTS) — voir AUDIO_DAEMON_HALF_DUPLEX.
"""

from __future__ import annotations

import asyncio
import io
import logging
import math
import struct
import subprocess
import tempfile
import threading
import time
import uuid
import wave
from pathlib import Path
from typing import Any, Awaitable, Callable, Coroutine

import config
from audio import voice_latency as vl
from audio.audio_output import native_audio_output
from audio.vad_utterance import (
    VadUtteranceCollector,
    chunk_rms,
    vad_config_from_runtime,
)
from audio.voice_latency import UtteranceTrace
from audio.voice_queue import VoicePriority, voice_queue
from jarvis.audio.tts import (
    TextStreamSegmenter,
    get_local_tts_provider,
    load_tts_settings,
)
from jarvis.audio.tts.errors import TTSError
from jarvis.audio.tts.playback import play_chunks
from pipeline import process_voice_fast

# Detection Silero VAD (sans log, le logger est defini plus bas)
try:
    from audio.vad_silero import silero_vad as _vad_silero
    USE_SILERO_VAD: bool = _vad_silero.available
except ImportError:
    USE_SILERO_VAD = False
    _vad_silero = None

from database import create_conversation
from jarvis.event_bus import JarvisEvent, event_bus

logger = logging.getLogger("audio_daemon")

if USE_SILERO_VAD:
    logger.info("[audio_daemon] VAD : Silero (neural)")
else:
    logger.info("[audio_daemon] VAD : RMS (fallback)")

# ── Constantes nominales ─────────────────────────────────────────────────────

SAMPLE_RATE = int(getattr(config, "AUDIO_DAEMON_SAMPLE_RATE", 16000) or 16000)
CHANNELS = 1
SAMPLE_WIDTH = 2  # 16-bit
CHUNK_MS = 30  # fenêtre VAD
CHUNK_SAMPLES = int(SAMPLE_RATE * CHUNK_MS / 1000)
CHUNK_BYTES = CHUNK_SAMPLES * SAMPLE_WIDTH

# Wake word fallback volume
FALLBACK_WAKE_RMS = 0.03
FALLBACK_WAKE_DURATION_MS = 500
FALLBACK_WAKE_CHUNKS = int(FALLBACK_WAKE_DURATION_MS / CHUNK_MS)

# Timeout subprocess audio (afplay / say)
AFPLAY_TIMEOUT_S = 30.0


async def _enqueue_utterance_with_backpressure(
    queue: asyncio.Queue[tuple[UtteranceTrace, bytes]],
    item: tuple[UtteranceTrace, bytes],
) -> int:
    """Attend une place au lieu de perdre une phrase complète.

    La file reste bornée en mémoire. Le temps d'attente retourné est injecté
    dans la trace de latence afin que toute pression soit mesurable.
    """
    started = time.perf_counter()
    await queue.put(item)
    return round((time.perf_counter() - started) * 1000)


def _should_interrupt_half_duplex(
    *,
    state: str,
    rms: float,
    speech_threshold: float,
) -> bool:
    """N'interrompt que la parole TTS, jamais un traitement encore silencieux."""
    return state == "speaking" and rms > speech_threshold


def _should_collect_utterance(state: str) -> bool:
    """Le micro continue de préparer le tour suivant pendant le traitement."""
    return state in ("listening", "processing")

# Son de confirmation
WAKE_SOUND_PATH = Path(__file__).resolve().parent.parent / "data" / "sounds" / "wake.wav"
END_SOUND_PATH = Path(__file__).resolve().parent.parent / "data" / "sounds" / "end.wav"

# Type du callback broadcast injecté par main.py
BroadcastFn = Callable[[dict[str, Any]], Coroutine[Any, Any, None]]

# ── Filtre hallucinations STT (bruit TV/YouTube) ──
# Whisper / faster-whisper hallucine des phrases de sous-titres YouTube
# sur du silence ou bruit ambiant non-vocal. Ce filtre bloque ces artefacts
# avant qu'ils n'atteignent le LLM.
STT_GHOST_PHRASES: list[str] = [
    "sous-titres realises par",
    "amara.org",
    "sous-titrage",
    "merci d'avoir regarde",
    "merci d avoir regarde",
    "abonnez-vous",
    "n'oubliez pas de",
    "n oubliez pas de",
    "like and subscribe",
    "subtitles by",
    "thank you for watching",
    "musique",
    "\u266a",  # ♪
    "[musique]",
    "[applaudissements]",
    "community generated",
    "auto-generated",
    "cc by",
    "creative commons",
    # Bruit TV françophone (hallucinations Whisper sur fond sonore)
    "l'une de",
    "service à",
    "pour votre",
    "votre attention",
    "merci de",
    "merci pour",
]

# Commandes courtes acceptées (un seul mot)
ALLOWED_SHORT_COMMANDS: frozenset[str] = frozenset({
    "stop", "oui", "non", "merci", "silence", "annule", "continue",
    "pause", "jarvis",
})

# Phrases de mise en veille / reveil (detection directe, bypass LLM)
SLEEP_PHRASES: list[str] = [
    "mets-toi en veille",
    "mets toi en veille",
    "en veille",
    "dors",
    "bonne nuit",
    "pause",
    "silence",
    "arrete d'ecouter",
    "arrete de m'ecouter",
]
WAKE_PHRASES: list[str] = [
    "reveille-toi",
    "reveille toi",
    "reveille-toi",
    "je suis la",
    "je suis ici",
    "c'est bon",
]


def _normalize_transcript(text: str) -> str:
    return text.lower().strip().strip(".,!?;:")


def _is_acceptable_transcript(text: str, *, used_local_stt: bool, segments: list) -> bool:
    """Filtre bruit / hallucinations — conserve les commandes courtes."""
    clean = (text or "").strip()
    if not clean:
        return False

    norm = _normalize_transcript(clean)
    if norm in ALLOWED_SHORT_COMMANDS:
        return True

    if _is_stt_hallucination(clean):
        return False

    if used_local_stt and _is_low_confidence(segments):
        return False

    return True


def _is_stt_hallucination(text: str) -> bool:
    """Detecte les hallucinations classiques de Whisper sur bruit ambiant.

    Retourne True si le texte est probablement un artefact de sous-titrage
    genere par le modele STT sur du silence ou du bruit non-vocal.
    """
    lower = text.lower().strip()
    if len(lower) < 2:
        return True
    return any(ghost in lower for ghost in STT_GHOST_PHRASES)


def _is_low_confidence(segments: list) -> bool:
    """Retourne True si la transcription est probablement du bruit.

    faster-whisper retourne un score `avg_logprob` par segment.
    avg_logprob < -1.0 = probable bruit / hallucination STT.
    """
    if not segments:
        return True
    scores = []
    for s in segments:
        if hasattr(s, "avg_logprob") and s.avg_logprob is not None and s.avg_logprob > -100:
            scores.append(s.avg_logprob)
    if not scores:
        return False
    avg_score = sum(scores) / len(scores)
    return avg_score < -1.0


async def _wait_subprocess(
    proc: asyncio.subprocess.Process,
    timeout: float = AFPLAY_TIMEOUT_S,
    context: str = "subprocess",
) -> None:
    """Attend la fin d'un subprocess avec timeout ; kill si dépassé."""
    try:
        await asyncio.wait_for(proc.wait(), timeout=timeout)
    except asyncio.TimeoutError:
        logger.warning("[audio_daemon] %s timeout (%.0fs) — kill", context, timeout)
        try:
            proc.kill()
            await proc.wait()
        except Exception as e:
            logger.debug("[audio_daemon] %s kill: %s", context, e)


def _generate_wake_sound() -> None:
    """Génère un bip sinusoïdal 880Hz, 150ms, fade in/out si absent."""
    path = WAKE_SOUND_PATH
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    duration = 0.150
    freq = 880.0
    n_samples = int(SAMPLE_RATE * duration)

    with wave.open(str(path), "w") as f:
        f.setnchannels(CHANNELS)
        f.setsampwidth(SAMPLE_WIDTH)
        f.setframerate(SAMPLE_RATE)
        for i in range(n_samples):
            t = i / SAMPLE_RATE
            fade = min(t / 0.020, 1.0) * min((duration - t) / 0.020, 1.0)
            val = int(16000 * fade * math.sin(2 * math.pi * freq * t))
            val = max(-32768, min(32767, val))
            f.writeframes(struct.pack("<h", val))

    logger.info("[audio_daemon] Son de wake généré : %s", path)


def _generate_end_sound() -> None:
    """Génère un bip grave 440Hz, 120ms, fade in/out si absent."""
    path = END_SOUND_PATH
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    duration = 0.120
    freq = 440.0
    n_samples = int(SAMPLE_RATE * duration)

    with wave.open(str(path), "w") as f:
        f.setnchannels(CHANNELS)
        f.setsampwidth(SAMPLE_WIDTH)
        f.setframerate(SAMPLE_RATE)
        for i in range(n_samples):
            t = i / SAMPLE_RATE
            fade = min(t / 0.015, 1.0) * min((duration - t) / 0.015, 1.0)
            val = int(12000 * fade * math.sin(2 * math.pi * freq * t))
            val = max(-32768, min(32767, val))
            f.writeframes(struct.pack("<h", val))

    logger.info("[audio_daemon] Son de fin généré : %s", path)


# ── Classe AudioDaemon ────────────────────────────────────────────────────────


# Intervalle de sonde quand aucun micro n'est présent. Assez court pour que
# rebrancher un micro reprenne tout seul, assez long pour ne rien coûter :
# l'attente ne fait qu'énumérer les périphériques.
NO_INPUT_DEVICE_POLL_S = 20.0


class NoInputDeviceError(RuntimeError):
    """Aucun périphérique d'entrée n'est présenté par le système.

    Ce n'est **pas** un plantage : c'est une précondition non remplie. Un Mac
    mini n'a pas de micro intégré, et débrancher un micro USB suffit à produire
    cet état. Le distinguer d'un crash change tout le comportement en aval —
    relancer ne peut pas faire apparaître du matériel, et une trace complète par
    tentative ne fait que remplir le journal.
    """

class AudioDaemon:
    """Daemon audio natif — wake word + conversation mains libres sur le Mac Mini."""

    __slots__ = (
        "state",
        "enabled",
        "wake_word_enabled",
        "continuous_mode",
        "_broadcast",
        "_pa",
        "_stream",
        "_porcupine",
        "_wake_thread_future",
        "_audio_queue",
        "_utterance_queue",
        "_wake_event",
        "_vad_task",
        "_process_task",
        "_watchdog_task",
        "_tts_playing_event",
        "_last_interaction",
        "_last_tts_end",
        "_conv_start_time",
        "_conv_id",
        "_running",
        "_stop_event",
        "_interrupt_event",
        "_loop",
        "_input_future",
        "_input_stop_event",
        "_tts_proc",
        "_no_frame_count",
        "_audio_buffer",
        "_speech_frames",
        "_silence_frames",
        "_mic_mute_logged",
        "_sleep_detected",
        "_last_frame_time",
        "_sleep_mode",
        "_half_duplex",
        "_tts_unavailable_reason", "_error_reason",
    )

    def __init__(self) -> None:
        self.state: str = "idle"  # idle | wake_listening | listening | processing | speaking | error
        self.enabled: bool = False
        # Lecture depuis config : False par defaut = ecoute continue sans wake word
        self.wake_word_enabled: bool = str(
            getattr(config, "WAKE_WORD_ENABLED", "false")
        ).lower() in ("true", "1", "yes")
        self.continuous_mode: bool = False

        self._broadcast: BroadcastFn | None = None
        self._pa: Any = None
        self._stream: Any = None
        self._porcupine: Any = None
        self._wake_thread_future: Any = None
        self._audio_queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=300)   # frames brutes micro — surchargé dans start()
        self._utterance_queue: asyncio.Queue[
            tuple[UtteranceTrace, bytes]
        ] = asyncio.Queue(maxsize=3)  # phrases complètes — surchargé dans start()
        self._wake_event: asyncio.Event | None = None
        self._vad_task: asyncio.Task[Any] | None = None
        self._process_task: asyncio.Task[Any] | None = None
        self._watchdog_task: asyncio.Task[Any] | None = None
        self._tts_playing_event = threading.Event()
        self._last_interaction: float = 0.0
        self._last_tts_end: float = 0.0
        self._conv_start_time: float = 0.0
        self._conv_id: int | None = None
        self._running: bool = False
        self._stop_event: asyncio.Event | None = None
        self._interrupt_event: asyncio.Event | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._input_future: asyncio.Future[Any] | None = None
        self._input_stop_event: threading.Event | None = None
        self._tts_proc: asyncio.subprocess.Process | None = None
        self._no_frame_count: int = 0
        self._audio_buffer: bytearray = bytearray()
        self._speech_frames: int = 0
        self._silence_frames: int = 0
        self._mic_mute_logged: bool = False

        # Mode veille applicative (controle par action LLM ou commande directe)
        self._sleep_mode: bool = False
        self._half_duplex: bool = bool(getattr(config, "AUDIO_DAEMON_HALF_DUPLEX", True))
        # Renseigné quand le moteur local n'a pas pu se charger : l'API expose
        # un état « indisponible » explicite plutôt qu'un silence inexpliqué.
        self._tts_unavailable_reason: str | None = None
        self._error_reason: str | None = None

    # ── Préchauffage des moteurs ──────────────────────────────────────────────

    async def _warmup_tts(self) -> None:
        """Charge le modèle TTS hors tour de parole (jamais bloquant).

        Un échec ici n'arrête pas le daemon : JARVIS continue de comprendre et
        d'agir, et l'état TTS indisponible est visible dans ``get_status``.
        Basculer en silence sur un autre moteur ferait entendre une voix que
        l'utilisateur n'a pas choisie.
        """
        settings = load_tts_settings()
        if not settings.warmup:
            logger.info("[audio_daemon] Préchauffage TTS désactivé (TTS_WARMUP=false)")
            return
        try:
            provider = get_local_tts_provider(settings)
            await provider.warmup()
            info = provider.info()
            logger.info(
                "TTS ready provider=%s backend=%s device=%s voice=%s "
                "streaming=%s offline=%s",
                info.provider, info.backend, info.device, info.voice,
                info.streaming, str(info.offline).lower(),
            )
            self._tts_unavailable_reason = None
        except TTSError as e:
            self._tts_unavailable_reason = str(e)
            logger.error("[audio_daemon] TTS local indisponible : %s", e)
        except Exception as e:
            self._tts_unavailable_reason = str(e)
            logger.exception("[audio_daemon] Préchauffage TTS : %s", e)

    async def _warmup_tts_pipeline(self) -> None:
        """Moteur d'abord, phrases spéculatives ensuite — jamais en parallèle."""
        await self._warmup_tts()
        if not getattr(config, "SPECULATIVE_TTS_ENABLED", False):
            return
        if self._tts_unavailable_reason:
            return
        try:
            from audio.tts_cache import speculative_tts

            await speculative_tts.warmup()
        except Exception as e:
            logger.debug("[audio_daemon] warmup TTS spéculatif : %s", e)

    # ── Réarmement du pipeline ────────────────────────────────────────────────

    def conversation_open(self) -> bool:
        """La conversation est-elle encore ouverte (pas de wake word à refaire) ?"""
        if self.continuous_mode:
            return True
        if self._conv_start_time <= 0:
            return False
        timeout = float(getattr(config, "AUDIO_DAEMON_CONVERSATION_TIMEOUT", 45.0))
        return (time.time() - self._conv_start_time) <= timeout

    def rearm_state(self) -> str:
        """État d'écoute à restaurer après un tour, abouti ou non.

        Un tour avorté (transcription vide, écho, bruit rejeté) n'est pas une
        fin de conversation : exiger un nouveau wake word rendrait le micro
        sourd jusqu'au prochain déclenchement, ce qui se lit comme un blocage
        de plusieurs dizaines de secondes côté utilisateur.
        """
        if self.wake_word_enabled and not self.conversation_open():
            return "wake_listening"
        return "listening"

    async def _rearm(
        self,
        *,
        reason: str,
        trace: UtteranceTrace | None = None,
        purge_utterances: bool = False,
    ) -> None:
        """Remet le pipeline en écoute immédiatement, sans état résiduel."""
        if USE_SILERO_VAD and _vad_silero is not None:
            try:
                _vad_silero.reset()
            except Exception as e:  # pragma: no cover — moteur externe
                logger.debug("[audio_daemon] reset Silero au réarmement : %s", e)

        self._tts_playing_event.clear()
        if self._interrupt_event is not None and self._interrupt_event.is_set():
            self._interrupt_event.clear()

        if purge_utterances and self._utterance_queue is not None:
            while not self._utterance_queue.empty():
                try:
                    self._utterance_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break

        # Le micro fait partie de l'état résiduel. En half-duplex, l'accusé
        # anticipé et le TTS arrêtent le flux d'entrée ; seul le chemin de fin
        # de tour normal le rouvrait. Toutes les sorties anticipées passent
        # ici — transcription vide, écho post-TTS, transcription rejetée,
        # interruption — et laissaient donc le daemon définitivement sourd.
        if self._half_duplex and self._stream is not None and self._running:
            try:
                if not self._stream.is_active():
                    self._stream.start_stream()
            except Exception as e:  # pragma: no cover — dépend du périphérique
                logger.debug("[audio_daemon] réouverture micro au réarmement : %s", e)

        self.state = self.rearm_state()
        voice_queue.set_user_conversation_active(False)
        if trace is not None:
            trace.mark(vl.PIPELINE_REARMED, reason=reason)
            trace.log_summary(reason=reason)
        await self._broadcast_state()

    # ── Mode veille applicative ───────────────────────────────────────────────

    def enter_sleep_mode(self) -> None:
        """Coupe l'ecoute active — seul 'wake' ou le wake word peut reactiver."""
        self._sleep_mode = True
        logger.info("[audio_daemon] Mode veille active — micro en sourdine")

    def exit_sleep_mode(self) -> None:
        """Reactive l'ecoute."""
        self._sleep_mode = False
        logger.info("[audio_daemon] Mode veille desactive — ecoute active")

    # ── API publique ──────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Démarre le daemon (micro + wake word). Appelé par l'API.

        Boucle immortelle : redémarre automatiquement en cas de crash avec
        backoff exponentiel (3s → 30s max). Nettoie proprement les ressources
        audio avant chaque redémarrage via ``_cleanup()``.
        """
        if self._running:
            logger.info("[audio_daemon] Déjà actif")
            return
        self._running = True
        self.enabled = True
        self._sleep_detected = False
        self._error_reason = None
        self._loop = asyncio.get_running_loop()
        logger.info("[audio_daemon] Démarrage (boucle immortelle)…")

        # Sons de confirmation au premier lancement
        try:
            _generate_wake_sound()
            _generate_end_sound()
        except Exception as e:
            logger.warning("[audio_daemon] Génération sons échouée : %s", e)

        # Un seul chemin de préchauffage : le cache spéculatif appelle aussi
        # ``provider.warmup()``. Deux ``create_task`` concurrents ouvraient
        # deux sidecars de synthèse, chacun avec ses poids, avant le verrou —
        # on sérialise ici pour que le moteur charge une seule fois, puis
        # éventuellement pré-génère les phrases.
        asyncio.create_task(self._warmup_tts_pipeline(), name="tts_engine_warmup")

        backoff_s = 3.0
        max_backoff_s = 30.0
        consecutive_crashes = 0

        while self.enabled:
            try:
                # Reset backoff apres 60s de fonctionnement stable
                await self._run()
                # Si _run() se termine normalement (shutdown), reset
                break
            except asyncio.CancelledError:
                logger.info("[audio_daemon] Annulé (shutdown)")
                break
            except NoInputDeviceError as e:
                # Attente, pas relance. Le compteur de crashes n'est pas
                # incrémenté et le backoff n'est pas escaladé : rien n'a
                # échoué, il manque du matériel. On sonde à intervalle fixe
                # pour reprendre tout seul dès qu'un micro réapparaît, et on
                # ne journalise qu'au changement d'état — sinon le journal
                # gagne une trace complète toutes les trois secondes.
                if self._error_reason != str(e):
                    logger.warning("[audio_daemon] %s", e)
                self._error_reason = str(e)
                self.state = "no_input_device"
                self._schedule_state_broadcast("no_input_device")
                self._running = False
                self._cleanup()
                await asyncio.sleep(NO_INPUT_DEVICE_POLL_S)
                # Un arrêt a pu tomber pendant la sonde. Se redéclarer actif
                # sans le vérifier laissait ``_running`` à True après la sortie
                # de boucle, et ``start()`` répond « Déjà actif » dans ce
                # cas — le service devenait irrécupérable sans redémarrer le
                # processus, précisément quand l'utilisateur tente de le
                # relancer parce qu'il manque un micro.
                if not self.enabled:
                    break
                self._running = True
                continue
            except Exception as e:
                consecutive_crashes += 1
                crash_type = type(e).__name__

                # Détection veille système : délai supplémentaire pour laisser macOS
                # réactiver les périphériques audio après un réveil
                extra_delay = 0.0
                if self._sleep_detected:
                    extra_delay = 5.0
                    logger.warning(
                        "[audio_daemon] Veille systeme detectee — delai de reprise de %.0fs",
                        extra_delay,
                    )
                    self._sleep_detected = False

                logger.error(
                    "[audio_daemon] Crash #%d (%s) : %s — redémarrage dans %.0fs",
                    consecutive_crashes, crash_type, e, backoff_s + extra_delay,
                    exc_info=True,
                )
                # Signal au thread pyaudio de s'arrêter AVANT _cleanup()
                # (pa.terminate() segfault sur Apple Silicon si le thread tient encore le stream)
                self._running = False
                self._signal_input_stop()
                await self._wait_for_input_thread()
                await voice_queue.cancel_current()
                voice_queue.set_mic_capture_active(False)
                voice_queue.set_user_conversation_active(False)
                self._cleanup()

                # Backoff exponentiel avec cap + délai veille
                await asyncio.sleep(backoff_s + extra_delay)
                backoff_s = min(backoff_s * 1.5, max_backoff_s)

                # Si trop de crashes consecutifs → abandon temporaire
                if consecutive_crashes >= 10:
                    logger.critical(
                        "[audio_daemon] %d crashes consecutifs — abandon pour 5 minutes",
                        consecutive_crashes,
                    )
                    await asyncio.sleep(300)
                    consecutive_crashes = 0
                    backoff_s = 3.0

                self._running = True

        logger.info("[audio_daemon] Arrêté définitivement")


    async def _run(self) -> None:
        """Cycle de vie complet du daemon — peut lever des exceptions.

        Initialise PyAudio + stream, lance les boucles VAD / process / watchdog,
        et attend que l'une d'elles se termine (crash). Propage l'exception
        pour que ``start()`` puisse redémarrer.
        """
        # Pyaudio doit être importé dans la boucle d'event (pas au niveau module)
        try:
            import pyaudio  # type: ignore[import-not-found]
        except ImportError:
            logger.error("[audio_daemon] pyaudio non installé — daemon inactif")
            self.state = "error"
            self._running = False
            self.enabled = False
            return

        self._stop_event = asyncio.Event()
        self._wake_event = asyncio.Event()
        self._interrupt_event = asyncio.Event()
        self._conv_start_time = 0.0
        self._last_interaction = 0.0
        self._last_tts_end = 0.0
        self._no_frame_count = 0
        self._mic_mute_logged = False
        self._last_frame_time: float = 0.0
        self._sleep_detected: bool = False
        # Jeton propre à CE cycle. Ne jamais utiliser seulement ``_running``
        # dans le thread : un redémarrage rapide remet _running à True avant
        # que l'ancien stream ait quitté sa boucle et conserve alors le micro.
        self._input_stop_event = threading.Event()
        self._input_future = None

        self._pa = pyaudio.PyAudio()

        # Précondition matérielle, vérifiée **avant** de lancer quoi que ce
        # soit. Sans ce contrôle, le daemon démarrait ses trois boucles, ouvrait
        # un stream sur un périphérique inexistant, et mourait dans le thread
        # pyaudio — ce que la boucle de relance interprétait comme un crash.
        if self._resolve_input_device_index(self._pa) is None:
            self._cleanup()
            raise NoInputDeviceError(
                "Aucun micro détecté. Branchez un périphérique d'entrée, ou "
                "renseignez AUDIO_DAEMON_INPUT_DEVICE si le vôtre existe sous "
                "un autre nom."
            )

        self._audio_queue = asyncio.Queue(maxsize=300)
        self._utterance_queue = asyncio.Queue(maxsize=3)

        # TTS local : le chargement du modèle appartient au warmup, jamais à un
        # tour de parole. `_warmup_tts` est lancé plus bas, hors conversation.
        await voice_queue.start(self._play_tts_native, self._stop_current_tts)
        voice_queue.set_mic_capture_active(True)

        # Pre-charger le modele STT local
        try:
            from audio.engine_config import log_audio_startup_config
            from audio.stt_local import stt_local as _stt_local

            log_audio_startup_config()
            logger.info(
                "[audio_daemon] Pre-chargement STT local (%s) ...",
                _stt_local.get_backend_name(),
            )
            loop_local = asyncio.get_running_loop()
            available = await loop_local.run_in_executor(None, _stt_local.preload_sync)
            if available:
                logger.info("[audio_daemon] Modele STT local pret")
            else:
                logger.error("[audio_daemon] Aucun moteur STT local préchargé")
        except Exception as e:
            logger.debug("[audio_daemon] pre-chargement STT local: %s", e)

        # Architecture 3 boucles, lancées uniquement quand les moteurs sont prêts :
        #   _vad_loop_safe : VAD + wake word + interruption — jamais bloqué
        #   _process_loop_safe : STT + LLM + TTS — peut bloquer sans impacter le VAD
        #   _mic_watchdog : détection déconnexion micro — restart si silencieux > 60s
        self._vad_task = asyncio.create_task(self._vad_loop_safe(), name="audio_daemon_vad")
        self._process_task = asyncio.create_task(self._process_loop_safe(), name="audio_daemon_process")
        self._watchdog_task = asyncio.create_task(self._mic_watchdog(), name="audio_daemon_watchdog")

        if self.wake_word_enabled and not self.continuous_mode:
            logger.info("[audio_daemon] Wake word actif — détection volume sur flux unique")

        self.state = "wake_listening" if self.wake_word_enabled else "listening"
        logger.info("[audio_daemon] Actif — state=%s wake_word=%s", self.state, self.wake_word_enabled)
        await self._broadcast_state()
        asyncio.create_task(event_bus.emit(JarvisEvent(type="voice.listening")))

        # Attendre que l'une des tâches se termine (= crash)
        done, pending = await asyncio.wait(
            [self._vad_task, self._process_task, self._watchdog_task],
            return_when=asyncio.FIRST_COMPLETED,
        )

        # Annuler les tâches restantes et propager l'exception
        for task in pending:
            task.cancel()
        for task in done:
            exc = task.exception()
            if exc:
                raise exc


    def _cleanup(self) -> None:
        """Nettoie les ressources audio (appelé avant chaque restart).

        Libère PyAudio, ferme le stream, vide les queues, annule les tâches
        async encore actives. Résilient : aucune exception ne remonte.
        """
        # Tuer le processus TTS si actif
        if self._tts_proc and self._tts_proc.returncode is None:
            try:
                self._tts_proc.kill()
            except Exception:
                pass
            self._tts_proc = None

        self._tts_playing_event.clear()
        self._signal_input_stop()

        # Annuler les tâches async
        for task_attr in ("_vad_task", "_process_task", "_watchdog_task"):
            task: asyncio.Task[Any] | None = getattr(self, task_attr, None)
            if task and not task.done():
                task.cancel()
            setattr(self, task_attr, None)

        # Fermer le stream pyaudio (robustesse : stream deja ferme → OK)
        if self._stream:
            try:
                # Verifier l'etat interne avant d'appeler stop_stream
                # PyAudio peut etre dans un etat inconsistent apres un crash
                if hasattr(self._stream, '_stream') and self._stream._stream is not None:
                    if self._stream.is_active():
                        self._stream.stop_stream()
                self._stream.close()
            except (OSError, AttributeError) as e:
                # Stream not open, Stream already closed, etc. → silencieux
                logger.debug("[audio_daemon] Cleanup stream (deja ferme) : %s", e)
            except Exception:
                pass
            self._stream = None

        # Le thread d'entrée est joint avant cet appel. PortAudio peut donc être
        # terminé sans garder l'ancien périphérique CoreAudio captif.
        if self._pa:
            pa = self._pa
            self._pa = None
            try:
                pa.terminate()
            except Exception as e:
                logger.debug("[audio_daemon] terminate PyAudio : %s", e)
        self._input_stop_event = None

        # Vider les queues
        for queue_attr in ("_audio_queue", "_utterance_queue"):
            q: asyncio.Queue | None = getattr(self, queue_attr, None)
            if q:
                while not q.empty():
                    try:
                        q.get_nowait()
                    except asyncio.QueueEmpty:
                        break

        # Reset état VAD
        self._audio_buffer.clear()
        self._speech_frames = 0
        self._silence_frames = 0
        self._mic_mute_logged = False

        # Reset Silero VAD
        if USE_SILERO_VAD:
            _vad_silero.reset()

        # Annuler le thread wake word
        if self._wake_thread_future:
            self._wake_thread_future.cancel()
            self._wake_thread_future = None
        if self._porcupine:
            try:
                self._porcupine.delete()
            except Exception:
                pass
            self._porcupine = None

    async def stop(self) -> None:
        """Arrête proprement (micro, porcupine, pyaudio)."""
        logger.info("[audio_daemon] Arrêt demandé")

        # Les messages du dernier tour sont écrits en arrière-plan pour ne pas
        # retarder la parole : on les attend ici plutôt que de les perdre.
        try:
            from api.voice_fastpath import flush_pending_persists

            await flush_pending_persists()
        except Exception as e:
            logger.debug("[audio_daemon] vidage des écritures vocales : %s", e)
        self._running = False
        self.enabled = False

        if self._stop_event:
            self._stop_event.set()
        if self._interrupt_event:
            self._interrupt_event.set()
        self._signal_input_stop()

        # Annule le thread wake word
        if self._wake_thread_future:
            self._wake_thread_future.cancel()
            self._wake_thread_future = None

        # Annule les tâches (VAD + processeur + watchdog)
        for task_attr in ("_vad_task", "_process_task", "_watchdog_task"):
            task: asyncio.Task[Any] | None = getattr(self, task_attr, None)
            if task:
                task.cancel()
                try:
                    await asyncio.wait_for(task, timeout=5.0)
                except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
                    pass
                setattr(self, task_attr, None)

        # Le callback PortAudio vit dans un executor indépendant des tâches
        # asyncio. Il doit être réellement terminé avant qu'un nouveau start()
        # puisse ouvrir le même périphérique USB.
        await self._wait_for_input_thread()

        await voice_queue.cancel_current()
        if not getattr(config, "DAEMON_ENABLED", True):
            await voice_queue.stop()
        voice_queue.set_mic_capture_active(False)
        voice_queue.set_user_conversation_active(False)

        # Libère pyaudio
        self._cleanup_audio()

        # Le modèle vocal tient plusieurs gigaoctets et un sous-processus :
        # les laisser derrière soi empêcherait un redémarrage propre du daemon.
        try:
            from jarvis.audio.tts import reset_local_tts_provider

            await reset_local_tts_provider()
        except Exception as e:
            logger.debug("[audio_daemon] fermeture du moteur vocal : %s", e)

        self.state = "idle"
        await self._broadcast_state()
        logger.info("[audio_daemon] Arrêté")

    async def set_wake_word(self, enabled: bool) -> None:
        """Active/désactive le wake word."""
        self.wake_word_enabled = enabled
        if enabled:
            self.continuous_mode = False
        else:
            self.continuous_mode = True

        if self._running:
            self._cancel_wake_thread()
            if self.wake_word_enabled and not self.continuous_mode:
                logger.info("[audio_daemon] Wake word — détection volume sur flux unique")
                self.state = "wake_listening"
            else:
                self.state = "listening"
            await self._broadcast_state()

        logger.info("[audio_daemon] wake_word=%s continuous=%s", self.wake_word_enabled, self.continuous_mode)

    async def set_continuous_mode(self, enabled: bool) -> None:
        """Mode écoute continue (pas de wake word)."""
        self.continuous_mode = enabled
        if enabled:
            self.wake_word_enabled = False

        if self._running:
            self._cancel_wake_thread()
            if not enabled and self.wake_word_enabled:
                logger.info("[audio_daemon] Wake word — détection volume sur flux unique")
                self.state = "wake_listening"
            else:
                self.state = "listening"
            await self._broadcast_state()

        logger.info("[audio_daemon] continuous=%s wake_word=%s", self.continuous_mode, self.wake_word_enabled)

    def get_status(self) -> dict[str, Any]:
        """Retourne l'état complet pour l'API."""
        stt_engine = "none"
        try:
            from audio.stt_local import stt_local as _stt_local
            if _stt_local.available:
                stt_engine = _stt_local.get_backend_name()
        except Exception as e:
            logger.debug("[audio_daemon] get_status STT local: %s", e)

        tts_engine = "none"
        tts_info: dict[str, Any] = {}
        try:
            info = get_local_tts_provider().info()
            tts_engine = info.provider
            tts_info = dict(info.as_log_fields())
        except Exception as e:
            logger.debug("[audio_daemon] get_status TTS local: %s", e)

        return {
            "enabled": self.enabled,
            "state": self.state,
            "wake_word_enabled": self.wake_word_enabled,
            "continuous_mode": self.continuous_mode,
            "sleep_mode": self._sleep_mode,
            "last_interaction": self._last_interaction,
            "stt_engine": stt_engine,
            "tts_engine": tts_engine,
            "tts": tts_info,
            "tts_available": self._tts_unavailable_reason is None,
            "tts_error": self._tts_unavailable_reason,
            # Cause lisible de l'état courant. Un état « error » sans raison
            # oblige à lire les journaux du serveur pour découvrir qu'il
            # manque simplement un micro.
            "error": self._error_reason,
            "has_porcupine": self._porcupine is not None,
        }

    def set_broadcast(self, fn: BroadcastFn) -> None:
        """Injection du callback broadcast depuis main.py."""
        self._broadcast = fn

    # ── Boucle VAD (collecteur, jamais bloqué) ─────────────────────────────────

    async def _vad_loop_safe(self) -> None:
        """Collecteur VAD continu avec protection par itération.

        Chaque itération est wrappée dans un try/except. Les erreurs consécutives
        sont comptées — après 50 erreurs, une exception est levée pour forcer
        le restart de ``_run()``.

        Heartbeat toutes les ~60s pour preuve de vie dans les logs.
        """
        loop = asyncio.get_running_loop()
        audio_queue = self._audio_queue
        utterance_queue = self._utterance_queue
        interrupt_event = self._interrupt_event
        input_stop_event = self._input_stop_event
        assert interrupt_event is not None
        assert input_stop_event is not None

        # ── Lance le thread pyaudio ──
        def _blocking_input() -> None:
            """Thread bloquant : lit le micro et alimente audio_queue avec drain intelligent."""
            try:
                pa = self._pa
                fmt = 8  # pyaudio.paInt16
                stream = pa.open(
                    rate=SAMPLE_RATE,
                    channels=CHANNELS,
                    format=fmt,
                    input=True,
                    input_device_index=self._resolve_input_device_index(pa),
                    frames_per_buffer=CHUNK_SAMPLES,
                )
                self._stream = stream
                logger.info("[audio_daemon] Stream micro ouvert (rate=%d, chunks=%d)", SAMPLE_RATE, CHUNK_SAMPLES)

                # Détection micro muet (permission macOS refusée)
                silent_since = 0
                PERM_CHECK_CHUNKS = int(3000 / CHUNK_MS)

                while not input_stop_event.is_set():
                    try:
                        data = stream.read(CHUNK_SAMPLES, exception_on_overflow=False)
                    except OSError as e:
                        logger.warning("[audio_daemon] OSError sur stream.read() : %s — sortie boucle input", e)
                        # Détection veille système : si on n'a pas eu de frame depuis > 30s,
                        # c'est probablement un réveil de veille → force reinit complète
                        now = time.time()
                        if self._last_frame_time > 0 and (now - self._last_frame_time) > 30:
                            self._sleep_detected = True
                            logger.warning(
                                "[audio_daemon] Veille systeme detectee (gap=%.0fs) — reinit complete PyAudio au prochain cycle",
                                now - self._last_frame_time,
                            )
                        break

                    self._last_frame_time = time.time()

                    rms = self._chunk_rms(data)

                    # Présence au bureau : tout son ambiant au-dessus du seuil
                    # compte (pas seulement la parole). Le TTS de JARVIS est
                    # exclu pour ne pas se détecter soi-même.
                    if (
                        rms > getattr(config, "PRESENCE_NOISE_RMS", 0.015)
                        and not self._tts_playing_event.is_set()
                    ):
                        try:
                            from scripts.presence import presence_detector

                            if presence_detector.on_sound() == "arrived":
                                from database import is_dnd_active

                                if not config.is_quiet_hours() and not is_dnd_active():
                                    asyncio.run_coroutine_threadsafe(
                                        self._play_tts(config.PRESENCE_GREETING, emotion="warm"),
                                        loop,
                                    )
                        except Exception as e:
                            logger.debug("[audio_daemon] presence : %s", e)

                    if rms <= 0.0001:
                        silent_since += 1
                        if silent_since == PERM_CHECK_CHUNKS and not self._mic_mute_logged:
                            self._mic_mute_logged = True
                            logger.critical(
                                "[audio_daemon] Micro muet depuis 3s — permission macOS probablement refusee. "
                                "Reglages Systeme > Confidentialite > Microphone → cocher JARVIS "
                                "(et le Python du venv si present)."
                            )
                    else:
                        silent_since = 0

                    # Half-duplex : ne pas alimenter la queue pendant le TTS
                    if self._half_duplex and self._tts_playing_event.is_set():
                        continue

                    # Safe put: QueueFull est catché DANS le callback (pas autour de call_soon_threadsafe)
                    def _safe_put(data_bytes: bytes) -> None:
                        try:
                            audio_queue.put_nowait(data_bytes)
                        except asyncio.QueueFull:
                            # Queue saturée → drainer les vieux frames (garder ~3s récentes)
                            drained = 0
                            while audio_queue.qsize() > 100:
                                try:
                                    audio_queue.get_nowait()
                                    drained += 1
                                except asyncio.QueueEmpty:
                                    break
                            if drained > 0:
                                logger.warning("[audio_daemon] Queue drainée : %d frames jetées", drained)
                            # Réessayer d'insérer le chunk courant
                            try:
                                audio_queue.put_nowait(data_bytes)
                            except asyncio.QueueFull:
                                pass  # abandon silencieux

                    loop.call_soon_threadsafe(_safe_put, data)

                try:
                    if stream.is_active():
                        stream.stop_stream()
                except (OSError, AttributeError):
                    pass  # stream deja ferme
                try:
                    stream.close()
                except (OSError, AttributeError):
                    pass
                logger.info("[audio_daemon] Stream micro fermé")
            except Exception as e:
                logger.exception("[audio_daemon] Erreur thread input pyaudio : %s", e)
                self.state = "error"
                loop.call_soon_threadsafe(self._schedule_state_broadcast, "error")

        self._input_future = loop.run_in_executor(None, _blocking_input)

        # ── Paramètres VAD (config stricte, sans paliers codés en dur) ──
        timeout = getattr(config, "AUDIO_DAEMON_CONVERSATION_TIMEOUT", 30.0)
        vad_cfg = vad_config_from_runtime(
            config,
            chunk_ms=CHUNK_MS,
            use_silero=USE_SILERO_VAD,
        )
        speech_threshold = vad_cfg.speech_threshold

        def _rms_is_speech(chunk: bytes) -> bool:
            return chunk_rms(chunk) > speech_threshold

        utterance_collector = VadUtteranceCollector(
            config=vad_cfg,
            is_speech_fn=_rms_is_speech,
            get_speech_prob_fn=_vad_silero.get_probability if USE_SILERO_VAD else None,
        )

        frame_count = 0
        HEARTBEAT_INTERVAL = 2000
        consecutive_errors = 0
        MAX_CONSECUTIVE_ERRORS = 50
        wake_loud_chunks = 0

        while self._running and self._stop_event and not self._stop_event.is_set():
            try:
                if self._sleep_mode:
                    while not audio_queue.empty():
                        try:
                            audio_queue.get_nowait()
                        except asyncio.QueueEmpty:
                            break
                    await asyncio.sleep(0.5)
                    continue

                # Wake word : un seul flux micro — détection volume sur le flux principal
                if self.state in ("idle", "wake_listening") and self.wake_word_enabled:
                    try:
                        chunk = await asyncio.wait_for(audio_queue.get(), timeout=0.5)
                    except asyncio.TimeoutError:
                        continue
                    rms = chunk_rms(chunk)
                    if rms > speech_threshold:
                        wake_loud_chunks += 1
                        if wake_loud_chunks >= FALLBACK_WAKE_CHUNKS:
                            wake_loud_chunks = 0
                            self._conv_start_time = time.time() if not self.continuous_mode else 0.0
                            try:
                                if getattr(config, "AUDIO_DAEMON_WAKE_SOUND", True) and WAKE_SOUND_PATH.exists():
                                    proc = await asyncio.create_subprocess_exec(
                                        "afplay", str(WAKE_SOUND_PATH),
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                    )
                                    await _wait_subprocess(proc, context="wake_sound afplay")
                                else:
                                    await self._play_tts("Oui Monsieur ?", emotion="neutral")
                            except Exception as e:
                                logger.debug("[audio_daemon] wake sound/TTS: %s", e)
                            self.state = "listening"
                            await self._broadcast_state()
                            utterance_collector.reset()
                            if USE_SILERO_VAD:
                                _vad_silero.reset()
                    else:
                        wake_loud_chunks = max(0, wake_loud_chunks - 1)
                    continue

                try:
                    chunk = await asyncio.wait_for(audio_queue.get(), timeout=0.1)
                except asyncio.TimeoutError:
                    if (
                        not self.continuous_mode
                        and self._conv_start_time > 0
                        and (time.time() - self._conv_start_time > timeout)
                    ):
                        logger.info("[audio_daemon] Timeout conversation (%ss) → retour veille", timeout)
                        self._conv_start_time = 0.0
                        self.state = "wake_listening" if self.wake_word_enabled else "listening"
                        await self._broadcast_state()
                        utterance_collector.reset()
                    continue

                frame_count += 1
                consecutive_errors = 0

                if frame_count % HEARTBEAT_INTERVAL == 0:
                    logger.debug(
                        "[audio_daemon] Heartbeat — state=%s queue=%d vad=%s half_duplex=%s",
                        self.state, audio_queue.qsize(),
                        "silero" if USE_SILERO_VAD else "rms", self._half_duplex,
                    )

                if self.state == "speaking":
                    if not self._half_duplex:
                        pass  # barge-in possible si micro ouvert — non implémenté sans VoiceProcessingIO
                    elif _should_interrupt_half_duplex(
                        state=self.state,
                        rms=chunk_rms(chunk),
                        speech_threshold=speech_threshold,
                    ):
                        native_audio_output.stop()
                        if self._tts_proc and self._tts_proc.returncode is None:
                            try:
                                self._tts_proc.kill()
                            except Exception:
                                pass
                            self._tts_proc = None
                        await voice_queue.cancel_current()
                        self._tts_playing_event.clear()
                        self.state = "listening"
                        interrupt_event.set()
                        await self._broadcast_state()
                        logger.info("[audio_daemon] Interruption (half-duplex limité)")
                        if not utterance_collector.has_speech:
                            utterance_collector.reset()
                        utterance_collector.ingest(chunk)
                    continue

                if not _should_collect_utterance(self.state):
                    continue

                completed = utterance_collector.ingest(chunk)
                if completed:
                    # Origine de la trace = fin de parole réelle (le silence de
                    # détection est retranché), pas l'instant de finalisation.
                    trace = UtteranceTrace(
                        conversation_id=self._conv_id,
                        t0=utterance_collector.last_speech_ended_at or time.perf_counter(),
                        speech_ms=round(utterance_collector.last_speech_ms),
                    )
                    audio_ms = utterance_collector.last_audio_ms
                    started_at = utterance_collector.last_speech_started_at
                    if started_at is not None:
                        trace.marks.append(vl.Mark(
                            event=vl.SEGMENT_SPEECH_STARTED,
                            at=started_at,
                            since_start_ms=(started_at - trace.t0) * 1000.0,
                            since_previous_ms=0.0,
                            fields={"audio_ms": audio_ms},
                        ))
                    trace.mark(vl.SEGMENT_SPEECH_ENDED, audio_ms=audio_ms)
                    trace.mark(
                        vl.SEGMENT_FINALIZED,
                        audio_ms=audio_ms,
                        engine="silero" if USE_SILERO_VAD else "rms",
                    )
                    queue_wait_ms = await _enqueue_utterance_with_backpressure(
                        utterance_queue,
                        (trace, completed),
                    )
                    trace.mark(
                        vl.STT_QUEUE_ENTERED,
                        queue_depth=utterance_queue.qsize(),
                        backpressure_wait_ms=queue_wait_ms,
                    )
                    if queue_wait_ms:
                        logger.warning(
                            "[audio_daemon] backpressure file utterance : %dms",
                            queue_wait_ms,
                        )
                        await self._broadcast_state({
                            "type": "voice_backpressure",
                            "queue_depth": utterance_queue.qsize(),
                            "wait_ms": queue_wait_ms,
                        })
                    if USE_SILERO_VAD:
                        _vad_silero.reset()
                    try:
                        asyncio.create_task(event_bus.emit(JarvisEvent(type="voice.speech_start")))
                    except Exception:
                        pass

                if (
                    not self.continuous_mode
                    and self._conv_start_time > 0
                    and (time.time() - self._conv_start_time > timeout)
                ):
                    logger.info("[audio_daemon] Timeout conversation (%ss) → retour veille", timeout)
                    self._conv_start_time = 0.0
                    self.state = "wake_listening" if self.wake_word_enabled else "listening"
                    await self._broadcast_state()
                    utterance_collector.reset()

            except asyncio.CancelledError:
                raise
            except Exception as e:
                consecutive_errors += 1
                utterance_collector.reset()
                logger.warning("[audio_daemon] VAD erreur #%d: %s", consecutive_errors, e)
                if consecutive_errors > MAX_CONSECUTIVE_ERRORS:
                    raise RuntimeError(f"VAD loop crash after {consecutive_errors} errors") from e
                await asyncio.sleep(0.1)

        logger.info("[audio_daemon] Boucle VAD terminée")

    # ── Boucle processeur (STT + LLM + TTS, peut bloquer) ──────────────────────

    async def _process_loop_safe(self) -> None:
        """Boucle de traitement avec protection par itération.

        Chaque itération est wrappée dans un try/except. Les erreurs consécutives
        sont comptées — après 10 erreurs, une exception est levée pour forcer
        le restart de ``_run()``. Remet le micro en marche si crashé pendant TTS.
        """
        utterance_queue = self._utterance_queue
        interrupt_event = self._interrupt_event
        assert interrupt_event is not None

        stt_available = False
        try:
            from audio.stt_local import stt_local as _stt_local
            stt_available = _stt_local.available
        except Exception as e:
            logger.debug("[audio_daemon] detection STT local: %s", e)

        if not stt_available:
            logger.warning("[audio_daemon] Aucun STT local disponible — daemon muet")

        consecutive_errors = 0
        MAX_CONSECUTIVE_ERRORS = 10
        CONVERSATION_TIMEOUT = getattr(config, "AUDIO_DAEMON_CONVERSATION_TIMEOUT", 15.0)

        while self._running and self._stop_event and not self._stop_event.is_set():
            try:
                item = await asyncio.wait_for(utterance_queue.get(), timeout=2.0)
                consecutive_errors = 0  # reset
                trace, audio_bytes = item

                # Vérifier si on a été interrompu pendant l'attente
                if interrupt_event.is_set():
                    interrupt_event.clear()
                    continue

                await self._process_single_utterance(audio_bytes, stt_available, trace=trace)

            except asyncio.TimeoutError:
                # Vérifier timeout conversation (pas en mode continu)
                if (
                    not self.continuous_mode
                    and self.state in ("wake_listening", "listening")
                    and self._last_interaction > 0
                    and (time.time() - self._last_interaction) > CONVERSATION_TIMEOUT
                ):
                    self.state = "wake_listening" if self.wake_word_enabled else "listening"
                    self._last_interaction = 0
                    await self._broadcast_state()
                continue
            except asyncio.CancelledError:
                raise  # propager l'annulation
            except Exception as e:
                consecutive_errors += 1
                logger.error("[audio_daemon] Process erreur #%d: %s", consecutive_errors, e)
                # Remettre le micro en marche si crashé pendant TTS
                self._tts_playing_event.clear()
                if self._stream and not self._stream.is_active():
                    try:
                        self._stream.start_stream()
                    except Exception as e:
                        logger.debug("[audio_daemon] restart stream apres erreur process: %s", e)
                if consecutive_errors > MAX_CONSECUTIVE_ERRORS:
                    logger.error("[audio_daemon] Trop d'erreurs process (%d) — restart", consecutive_errors)
                    raise RuntimeError(f"Process loop crash after {consecutive_errors} errors") from e
                await asyncio.sleep(1)

        logger.info("[audio_daemon] Boucle process terminee")

    # ── Filtres bruit ambiant + detection sleep/wake ──────────────────────────

    def _check_sleep_wake(self, transcript: str) -> bool:
        """Retourne True si le transcript est une commande sleep/wake (traitee, skip LLM)."""
        lower = transcript.lower().strip()

        if any(p in lower for p in SLEEP_PHRASES):
            self.enter_sleep_mode()
            asyncio.create_task(self._play_tts("Bien, Monsieur. Je me mets en veille.", emotion="warm"))
            return True

        if any(p in lower for p in WAKE_PHRASES):
            self.exit_sleep_mode()
            asyncio.create_task(self._play_tts("Me revoici, Monsieur.", emotion="warm"))
            return True

        return False

    # ── Traitement d'une utterance ────────────────────────────────────────────

    async def _process_single_utterance(
        self,
        pcm_bytes: bytes,
        stt_available: bool,
        *,
        trace: UtteranceTrace | None = None,
    ) -> None:
        """Garantit la libération du verrou conversation, même sur erreur/retour anticipé."""
        trace = trace or UtteranceTrace(conversation_id=self._conv_id)
        voice_queue.set_user_conversation_active(True)
        try:
            from scripts.screen_watcher import screen_watcher

            screen_watcher.defer_for_voice()
        except Exception as e:
            logger.debug("[audio_daemon] report analyse écran : %s", e)
        try:
            await self._process_single_utterance_active(
                pcm_bytes, stt_available, trace=trace,
            )
        finally:
            voice_queue.set_user_conversation_active(False)

    async def _play_anticipatory_ack(self, trace: UtteranceTrace) -> None:
        """Fait entendre un accusé local pendant que le moteur prépare la réponse.

        Ce chemin n'est déclenché qu'après les fast-paths/cas cognitifs : une
        interpellation, une commande de contrôle, Fitness, Cursor ou un briefing
        ne reçoit donc jamais deux réponses. Le TTS et le tour canonique tournent
        en parallèle ; la file vocale sérialise ensuite la réponse finale.
        """
        if not getattr(config, "VOICE_ANTICIPATORY_ACK_ENABLED", True):
            return
        if self._tts_unavailable_reason:
            return
        if self._interrupt_event is not None and self._interrupt_event.is_set():
            return

        ack = "Bien, Monsieur."
        self.state = "speaking"
        await self._broadcast_state({
            "type": "voice_anticipatory_ack",
            "response": ack,
            "emotion": "neutral",
        })
        if self._half_duplex and self._stream:
            try:
                self._stream.stop_stream()
            except Exception as exc:
                logger.debug("[audio_daemon] stop stream avant accusé : %s", exc)

        await self._play_tts(
            ack,
            emotion="neutral",
            priority=VoicePriority.USER_RESPONSE,
            wait=True,
            trace=trace,
        )
        # L'accusé tourne en tâche concurrente et peut finir **après** que le
        # tour a été abandonné et le pipeline réarmé. Ne rendre la main à
        # « processing » que si l'état est toujours celui que cette méthode a
        # posé : sinon l'interface annonce un traitement en cours alors que le
        # daemon écoute déjà.
        if self.state != "speaking":
            return
        if self._interrupt_event is None or not self._interrupt_event.is_set():
            self.state = "processing"
            await self._broadcast_state()

    async def _process_single_utterance_active(
        self, pcm_bytes: bytes, stt_available: bool, *, trace: UtteranceTrace,
    ) -> None:
        """Traitement complet d'une phrase : STT → moteur vocal unique → TTS → playback + purge post-TTS.

        Utilise l'adaptateur vocal rapide du moteur conversationnel canonique.
        Cible : < 2s entre fin de phrase et debut TTS.
        Envoie des events WebSocket de debug : voice_debug_stt et voice_debug_tts.
        """
        import time as _time
        interrupt_event = self._interrupt_event
        assert interrupt_event is not None

        # Vérifier interruption avant de commencer
        if interrupt_event.is_set():
            interrupt_event.clear()
            return

        self.state = "processing"
        await self._broadcast_state()

        # 1. STT local uniquement (PCM natif, pas de cloud)
        text = ""
        stt_segments: list = []
        used_local_stt = False
        audio_duration_ms = round(len(pcm_bytes) / (SAMPLE_RATE * SAMPLE_WIDTH) * 1000)
        _t_stt_start = _time.time()

        meta: dict | None = None
        quality_ack_started = False

        def _quality_fallback_ack() -> Awaitable[None]:
            nonlocal quality_ack_started
            quality_ack_started = True
            return self._play_anticipatory_ack(trace)

        if stt_available:
            try:
                from audio.stt_local import stt_local as _stt_local
                trace.mark(vl.STT_STARTED, audio_ms=audio_duration_ms)
                meta = await _stt_local.transcribe_with_metadata(
                    pcm_bytes,
                    sample_rate=SAMPLE_RATE,
                    language=getattr(config, "LANGUAGE", "fr"),
                    speech_ms=trace.speech_ms or None,
                    on_quality_fallback=_quality_fallback_ack,
                )
                if meta:
                    text = str(meta.get("text") or "").strip()
                    stt_segments = meta.get("segments") or []
                    used_local_stt = True
                    logger.debug("[audio_daemon] STT local (%s) : %s", meta.get("engine"), text[:80])
            except Exception as e:
                logger.warning("[audio_daemon] STT local echoue : %s", e)

        stt_latency_ms = round((_time.time() - _t_stt_start) * 1000)
        stt_engine = (meta or {}).get("engine", "local") if used_local_stt else "none"
        stt_inference_ms = int((meta or {}).get("inference_ms") or stt_latency_ms)
        trace.mark(
            vl.STT_COMPLETED,
            engine=stt_engine,
            audio_ms=audio_duration_ms,
            text_chars=len(text),
            real_time_factor=(
                round(stt_inference_ms / audio_duration_ms, 3)
                if audio_duration_ms > 0 else None
            ),
        )

        # ── Event bus : STT result ──
        try:
            asyncio.create_task(event_bus.emit(JarvisEvent(
                type="voice.stt_result",
                data={
                    "transcript": text[:200] if text else "",
                    "latency_ms": stt_latency_ms,
                    "engine": stt_engine,
                },
            )))
        except Exception:
            pass

        # ── Broadcast debug STT ───────────────────────────────────────────────
        try:
            await self._broadcast_state({
                "type": "voice_debug_stt",
                "timestamp": _time.strftime("%H:%M:%S"),
                "transcript": text,
                "audio_duration_ms": audio_duration_ms,
                "stt_latency_ms": stt_latency_ms,
                "stt_engine": stt_engine,
                "vad_engine": "silero" if USE_SILERO_VAD else "rms",
                "audio_bytes": len(pcm_bytes),
            })
        except Exception as e:
            logger.debug("[audio_daemon] broadcast debug STT: %s", e)

        # Vérifier interruption après STT
        if interrupt_event.is_set():
            interrupt_event.clear()
            logger.debug("[audio_daemon] Interruption après STT — abandon traitement")
            await self._rearm(reason="interrupted_after_stt", trace=trace)
            return

        if not text:
            # Transcription vide : aucune conversation, aucun message, aucun
            # LLM, aucun TTS. Le pipeline se réarme sur-le-champ — c'est le cas
            # qui laissait le micro sourd jusqu'au prochain wake word.
            if not stt_available:
                logger.warning("[audio_daemon] Aucun STT local disponible — skip")
            await self._rearm(reason="empty_transcript", trace=trace)
            return

        # ── 1. Detection sleep/wake (bypass total LLM, latence zero) ──
        if self._check_sleep_wake(text):
            self.state = "wake_listening" if self.wake_word_enabled else "listening"
            await self._broadcast_state()
            return

        # Filtrage post-TTS : ignore écho sauf commandes courtes autorisées
        now = time.time()
        norm = _normalize_transcript(text)
        if (
            len(text.strip()) < 10
            and norm not in ALLOWED_SHORT_COMMANDS
            and (now - self._last_tts_end) < 2.0
        ):
            logger.debug("[audio_daemon] Transcription post-TTS ignorée (résidu d'écho) : %s", text[:60])
            await self._rearm(reason="tts_echo", trace=trace)
            return

        if not _is_acceptable_transcript(text, used_local_stt=used_local_stt, segments=stt_segments):
            logger.debug("[audio_daemon] Transcription rejetée (bruit/confiance) : %r", text[:80])
            await self._rearm(reason="rejected_transcript", trace=trace)
            return

        logger.info("[audio_daemon] Entendu : %s", text)

        # ── Raccourci « répète » : rejoue le dernier TTS, zéro re-génération ──
        try:
            from audio.tts_cache import is_repeat_request, last_tts as _last

            if is_repeat_request(text):
                entry = _last.get()
                self.state = "speaking"
                await self._broadcast_state()
                if entry:
                    logger.info("[audio_daemon] Répétition : %s", entry["text"][:60])
                    self._tts_playing_event.set()
                    try:
                        await self._play_audio_local(entry["audio"])
                    finally:
                        self._tts_playing_event.clear()
                        self._last_tts_end = time.time()
                else:
                    await self._play_tts("Je n'ai encore rien dit, Monsieur.", emotion="amused")
                self.state = "wake_listening" if self.wake_word_enabled else "listening"
                await self._broadcast_state()
                return
        except Exception as e:
            logger.debug("[audio_daemon] répète : %s", e)

        # Auto-résumé de réunions (opt-in) : chaque transcription ambiante
        # alimente le tracker — l'ouverture/clôture est gérée par lui.
        try:
            from scripts.meeting import meeting_tracker

            if meeting_tracker.add_utterance(text, audio_duration_ms / 1000) == "started":
                logger.info("[audio_daemon] Réunion détectée — capture des transcriptions")
        except Exception as e:
            logger.debug("[audio_daemon] meeting tracker : %s", e)

        # Broadcast transcript
        await self._broadcast_state({"transcript": text})

        # 2. Vérification phrases de fin
        lower = text.lower()
        if any(p in lower for p in config.END_PHRASES):
            logger.info("[audio_daemon] Phrase de fin détectée → retour veille")
            self._conv_start_time = 0.0
            self.state = "wake_listening" if self.wake_word_enabled else "listening"
            await self._broadcast_state()
            await self._play_tts("Bien Monsieur, je reste en veille.", emotion="warm")
            await self._play_end_sound()
            return

        # Vérifier interruption avant LLM
        if interrupt_event.is_set():
            interrupt_event.clear()
            logger.debug("[audio_daemon] Interruption avant LLM — abandon traitement")
            await self._rearm(reason="interrupted_before_llm", trace=trace)
            return

        # 3. Moteur conversationnel vocal canonique + adaptateur de latence
        self._last_interaction = time.time()

        if self._conv_id is None:
            trace.mark(vl.CONVERSATION_LOOKUP_STARTED)
            loop = asyncio.get_running_loop()
            # Création SQLite : hors boucle, sinon elle bloque le tour de parole
            # et tout ce qui partage la boucle (VAD, WebSocket, file vocale).
            self._conv_id = await loop.run_in_executor(
                None, lambda: create_conversation(agent="daemon_audio"),
            )
            trace.mark(vl.CONVERSATION_LOOKUP_COMPLETED)
        trace.set_conversation(self._conv_id)

        try:
            turn_callback = None if quality_ack_started else (
                lambda: self._play_anticipatory_ack(trace)
            )
            result = await process_voice_fast(
                text,
                self._conv_id,
                stt_ms=stt_latency_ms,
                trace=trace,
                on_canonical_turn_started=turn_callback,
            )
        except Exception as e:
            logger.exception("[audio_daemon] _process_voice_fast : %s", e)
            await self._rearm(reason="llm_error", trace=trace)
            await self._play_tts("Désolé Monsieur, je rencontre un problème technique.", emotion="concerned")
            return

        response_text = (result or {}).get("text") or ""
        emotion = (result or {}).get("emotion", "neutral") or "neutral"
        latency_ms = (result or {}).get("latency_ms", 0)
        logger.info("[audio_daemon] Voice fast : %.0fms", latency_ms)

        # Le protocole local transporte l'action séparément du texte prononcé.
        state_payload: dict[str, Any] = {"response": response_text, "emotion": emotion}
        if (result or {}).get("action") is not None:
            state_payload["action"] = result["action"]
        if (result or {}).get("action_result") is not None:
            state_payload["action_result"] = result["action_result"]
        await self._broadcast_state(state_payload)

        # Vérifier interruption avant TTS
        if interrupt_event.is_set():
            interrupt_event.clear()
            logger.debug("[audio_daemon] Interruption avant TTS — abandon playback")
            await self._rearm(reason="interrupted_before_tts", trace=trace)
            return

        # 4. TTS + playback
        self.state = "speaking"
        await self._broadcast_state()

        if self._half_duplex and self._stream:
            try:
                self._stream.stop_stream()
            except Exception as e:
                logger.debug("[audio_daemon] stop stream avant TTS: %s", e)

        _t_tts_start = _time.time()
        tts_engine_name = str(getattr(config, "TTS_PROVIDER", config.DEFAULT_TTS_PROVIDER))
        try:
            tts_engine_name = get_local_tts_provider().info().provider
            await self._play_tts(
                response_text,
                emotion=emotion,
                priority=VoicePriority.USER_RESPONSE,
                wait=True,
                trace=trace,
            )
        except Exception as e:
            logger.warning("[audio_daemon] TTS/playback echoue : %s", e)
        tts_latency_ms = round((_time.time() - _t_tts_start) * 1000)

        # ── Broadcast debug TTS ───────────────────────────────────────────────
        try:
            # Le `{}` historique ne disait rien de la répartition interne :
            # on expose la chronologie complète, corrélée par utterance_id.
            await self._broadcast_state({
                "type": "voice_debug_tts",
                "timestamp": _time.strftime("%H:%M:%S"),
                "text": response_text[:200] if response_text else "",
                "engine": tts_engine_name,
                "tts_engine": tts_engine_name,
                "chars": len(response_text or ""),
                "tts_latency_ms": tts_latency_ms,
                "latency": trace.snapshot(),
            })
        except Exception as e:
            logger.debug("[audio_daemon] broadcast debug TTS: %s", e)

        # 5. Purge post-TTS obligatoire
        #    a) Attendre que l'écho acoustique s'éteigne
        await asyncio.sleep(0.15)
        #    b) Purger la queue audio (résidus du TTS captés par le micro)
        if self._audio_queue is not None:
            drained = 0
            while not self._audio_queue.empty():
                try:
                    self._audio_queue.get_nowait()
                    drained += 1
                except asyncio.QueueEmpty:
                    break
            if drained:
                logger.debug("[audio_daemon] Purge post-TTS : %d frames jetées", drained)

        #    c) Purger aussi les utterances en attente (plus pertinentes après reprise)
        if self._utterance_queue is not None:
            while not self._utterance_queue.empty():
                try:
                    self._utterance_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break

        #    d) Reset Silero apres purge (buffer d'accumulation vide)
        if USE_SILERO_VAD:
            _vad_silero.reset_buffer()

        if self._half_duplex and self._stream and self._running:
            try:
                self._stream.start_stream()
            except Exception as e:
                logger.debug("[audio_daemon] restart stream apres TTS: %s", e)

        self._last_tts_end = time.time()
        self._conv_start_time = time.time()
        await self._rearm(reason="turn_completed", trace=trace)

    # ── Watchdog micro ───────────────────────────────────────────────────────

    async def _mic_watchdog(self) -> None:
        """Vérifie que le micro est toujours actif — détecte les déconnexions USB.

        Toutes les 10 secondes :
        - Vérifie que le stream est actif (sinon tente de le redémarrer)
        - Vérifie que des frames arrivent dans la queue (sinon, après 60s de silence, restart)

        Anti-spam : les échecs consécutifs de vérification du stream sont comptés.
        Un restart n'est déclenché qu'après MIC_RESTART_THRESHOLD échecs (défaut : 3 = 30s).
        Cela évite la boucle crash→restart→crash observée quand le stream est instable.
        """
        MIC_WATCHDOG_INTERVAL = 10  # secondes
        MAX_NO_FRAME_ITERATIONS = 6  # 6 x 10s = 60s sans frame → restart
        MIC_RESTART_THRESHOLD = 3  # échecs consécutifs de stream avant restart

        _consecutive_stream_failures = 0  # compteur anti-spam

        while self.enabled and self._stop_event and not self._stop_event.is_set():
            try:
                await asyncio.sleep(MIC_WATCHDOG_INTERVAL)

                if self._tts_playing_event.is_set():
                    # Micro volontairement arrêté pendant le playback TTS
                    self._no_frame_count = 0
                    _consecutive_stream_failures = 0
                    continue

                # Vérifier que le stream est actif
                try:
                    stream_alive = self._stream is not None and self._stream.is_active()
                except (OSError, AttributeError) as e:
                    # Stream corrompu — état PyAudio incohérent après crash audio
                    stream_alive = False
                    _consecutive_stream_failures += 1
                    if _consecutive_stream_failures == 1:
                        logger.warning(
                            "[audio_daemon] Stream micro corrompu (%s) — "
                            "échec #%d/%d avant restart",
                            e, _consecutive_stream_failures, MIC_RESTART_THRESHOLD,
                        )

                if not stream_alive and not self._tts_playing_event.is_set():
                    if _consecutive_stream_failures >= MIC_RESTART_THRESHOLD:
                        logger.error(
                            "[audio_daemon] %d échecs stream consécutifs — restart complet",
                            _consecutive_stream_failures,
                        )
                        raise RuntimeError(
                            f"Micro déconnecté après {_consecutive_stream_failures} échecs"
                        )
                    # Essayer de redémarrer le stream
                    if self._stream and not stream_alive:
                        try:
                            self._stream.start_stream()
                            _consecutive_stream_failures = 0  # succès
                        except Exception:
                            _consecutive_stream_failures += 1
                else:
                    _consecutive_stream_failures = max(0, _consecutive_stream_failures - 1)

                # Vérifier que la queue reçoit des frames (seulement en mode écoute).
                # Ne PAS forcer un restart ici : le silence micro est normal quand
                # personne ne parle, et pa.terminate() segfault sur Apple Silicon.
                # Si le stream est vraiment mort, le check stream_alive ci-dessus
                # le détectera et déclenchera un restart contrôlé.
                if self._audio_queue.empty() and self.state in ("listening", "wake_listening"):
                    self._no_frame_count += 1
                    if self._no_frame_count == MAX_NO_FRAME_ITERATIONS:
                        logger.warning(
                            "[audio_daemon] Aucune frame micro depuis %ds — silence prolongé, pas de restart",
                            self._no_frame_count * MIC_WATCHDOG_INTERVAL,
                        )
                    # Capper le compteur pour éviter l'overflow du log
                    if self._no_frame_count > MAX_NO_FRAME_ITERATIONS + 60:
                        self._no_frame_count = MAX_NO_FRAME_ITERATIONS + 1
                else:
                    self._no_frame_count = 0

            except asyncio.CancelledError:
                raise
            except RuntimeError:
                raise  # propager les erreurs de restart
            except Exception as e:
                logger.warning("[audio_daemon] Watchdog erreur : %s", e)
                await asyncio.sleep(1)

        logger.info("[audio_daemon] Watchdog micro terminé")

    # ── Wake word ─────────────────────────────────────────────────────────────

    def _start_wake_detection(self, loop: asyncio.AbstractEventLoop) -> None:
        """Désactivé — le wake word utilise le flux micro unique (_vad_loop_safe)."""
        logger.debug(
            "[audio_daemon] _start_wake_detection ignoré — un seul flux micro (pas de Porcupine séparé)"
        )

    def _cancel_wake_thread(self) -> None:
        """Annule le thread de détection wake word."""
        if self._wake_thread_future:
            self._wake_thread_future.cancel()
            self._wake_thread_future = None
        if self._wake_event is not None:
            self._wake_event.clear()
        if self._porcupine:
            try:
                self._porcupine.delete()
            except Exception as e:
                logger.debug("[audio_daemon] porcupine delete: %s", e)
            self._porcupine = None

    def _porcupine_wake_loop(self) -> None:
        """Thread bloquant : écoute le wake word via Porcupine."""
        try:
            # Sonde de disponibilité : l'import lui-même est le test.
            import pvporcupine  # noqa: F401  # type: ignore[import-not-found]
        except ImportError:
            return

        loop = self._loop
        porcupine = self._porcupine
        wake_event = self._wake_event
        if porcupine is None or wake_event is None or loop is None:
            return

        try:
            pa = self._pa
            assert pa is not None
            fmt = 8  # pyaudio.paInt16
            stream = pa.open(
                rate=porcupine.sample_rate,
                channels=1,
                format=fmt,
                input=True,
                input_device_index=self._resolve_input_device_index(pa),
                frames_per_buffer=porcupine.frame_length,
            )
            self._stream = stream
            logger.info("[audio_daemon] Stream Porcupine ouvert (rate=%d)", porcupine.sample_rate)

            while self._running and wake_event is not None and not wake_event.is_set():
                try:
                    pcm = stream.read(porcupine.frame_length, exception_on_overflow=False)
                except OSError:
                    break
                pcm_unpacked = struct.unpack_from("h" * porcupine.frame_length, pcm)
                if porcupine.process(pcm_unpacked) >= 0:
                    logger.info("[audio_daemon] Wake word detecte par Porcupine !")
                    loop.call_soon_threadsafe(wake_event.set)

            stream.stop_stream()
            stream.close()
        except Exception as e:
            logger.exception("[audio_daemon] Erreur boucle Porcupine : %s", e)

    def _volume_wake_loop(self) -> None:
        """Thread bloquant : détection de volume comme wake word fallback.

        Ouvre son propre stream pyaudio — ne partage pas _audio_queue avec _vad_loop.
        Quand le volume depasse FALLBACK_WAKE_RMS pendant FALLBACK_WAKE_DURATION_MS,
        signale _wake_event pour que _main_loop bascule en listening.
        """
        wake_event = self._wake_event
        loop = self._loop
        if wake_event is None or loop is None:
            return

        try:
            pa = self._pa
            if pa is None:
                logger.warning("[audio_daemon] Volume wake: PyAudio indisponible")
                return

            stream = pa.open(
                rate=SAMPLE_RATE,
                channels=CHANNELS,
                format=8,  # pyaudio.paInt16
                input=True,
                input_device_index=self._resolve_input_device_index(pa),
                frames_per_buffer=CHUNK_SAMPLES,
            )
            logger.info("[audio_daemon] Volume wake stream ouvert")

            loud_chunks = 0
            while self._running and loop is not None and not loop.is_closed() and wake_event is not None and not wake_event.is_set():
                try:
                    chunk = stream.read(CHUNK_SAMPLES, exception_on_overflow=False)
                except OSError:
                    break

                rms = self._chunk_rms(chunk)
                if rms > FALLBACK_WAKE_RMS:
                    loud_chunks += 1
                    if loud_chunks >= FALLBACK_WAKE_CHUNKS:
                        loud_chunks = 0
                        logger.info("[audio_daemon] Wake detecte par volume !")
                        loop.call_soon_threadsafe(wake_event.set)
                else:
                    loud_chunks = max(0, loud_chunks - 1)

            stream.stop_stream()
            stream.close()
            logger.info("[audio_daemon] Volume wake stream fermé")
        except Exception as e:
            logger.exception("[audio_daemon] Erreur boucle volume wake : %s", e)

    # ── Helpers audio ─────────────────────────────────────────────────────────

    async def _play_tts(
        self,
        text: str,
        emotion: str = "neutral",
        *,
        priority: VoicePriority | None = None,
        wait: bool = False,
        trace: UtteranceTrace | None = None,
    ) -> None:
        """Enfile une synthèse vocale sur le moteur local résolu."""
        if not text or not text.strip():
            return
        if priority is None:
            priority = {
                "urgent": VoicePriority.CRITICAL,
                "alert": VoicePriority.IMPORTANT,
            }.get(emotion, VoicePriority.USER_RESPONSE)
        if trace is not None:
            trace.mark(vl.TTS_QUEUE_ENTERED, text_chars=len(text))
        await voice_queue.enqueue(
            text,
            emotion=emotion,
            priority=priority,
            wait=wait,
            trace=trace,
        )

    async def _play_tts_native(
        self,
        text: str,
        emotion: str = "neutral",
        cancel_event: asyncio.Event | None = None,
        trace: UtteranceTrace | None = None,
    ) -> None:
        """Lecture TTS locale — appelée par la file vocale centrale."""
        if not text or not text.strip():
            return
        if cancel_event and cancel_event.is_set():
            return

        self._tts_playing_event.set()
        try:
            from audio.tts_cache import last_tts as _last_tts, speculative_tts as _spec_tts

            if getattr(config, "SPECULATIVE_TTS_ENABLED", False):
                cached = _spec_tts.get(text, emotion)
                if cached:
                    _last_tts.store(text, emotion, cached)
                    if trace is not None:
                        trace.mark(vl.TTS_FIRST_AUDIO_CHUNK, engine="cache")
                        trace.mark(vl.TTS_PLAYBACK_STARTED, engine="cache")
                    await self._play_audio_local(cached, cancel_event=cancel_event)
                    if trace is not None:
                        trace.mark(vl.TTS_PLAYBACK_COMPLETED, engine="cache")
                    return

            await self._speak_local(
                text, emotion, cancel_event=cancel_event, trace=trace,
            )
        except TTSError as e:
            # Le moteur a échoué : la réponse texte est déjà persistée et
            # affichée, le pipeline se réarme. Pas de bascule vers un autre
            # moteur, pas de service distant.
            self._tts_unavailable_reason = str(e)
            logger.error("[audio_daemon] Synthèse locale échouée : %s", e)
        except Exception as e:
            logger.warning("[audio_daemon] TTS native erreur : %s", e)
        finally:
            self._tts_playing_event.clear()
            self._last_tts_end = time.time()

    async def _speak_local(
        self,
        text: str,
        emotion: str,
        *,
        cancel_event: asyncio.Event | None,
        trace: UtteranceTrace | None,
    ) -> None:
        """Segmente la réponse et joue chaque segment dès qu'il est synthétisé.

        Le découpage est ce qui fait arriver le premier son avant la fin de la
        synthèse complète : pendant que le segment N est joué, le segment N+1
        se génère. La file bornée de la sortie audio fournit la contre-pression
        — un moteur plus rapide que la lecture ne remplit pas la mémoire.
        """
        from audio.tts_cache import last_tts as _last_tts

        settings = load_tts_settings()
        provider = get_local_tts_provider(settings)
        await provider.warmup()
        self._tts_unavailable_reason = None

        info = provider.info()
        request_id = uuid.uuid4().hex
        utterance_id = getattr(trace, "utterance_id", "") if trace is not None else ""

        if trace is not None:
            trace.mark(
                vl.TTS_MODEL_READY, engine=info.provider, sample_rate=info.sample_rate,
            )
            trace.mark(
                vl.TTS_SYNTHESIS_STARTED,
                engine=info.provider,
                text_chars=len(text),
                sample_rate=info.sample_rate,
            )

        segmenter = TextStreamSegmenter(settings)
        segments = segmenter.feed(text) + segmenter.flush()
        collected: list[bytes] = []

        async def _chunks():
            index = 0
            for segment in segments:
                if cancel_event is not None and cancel_event.is_set():
                    await provider.cancel(request_id)
                    native_audio_output.stop()
                    return
                async for chunk in provider.stream(
                    segment, request_id=request_id, utterance_id=utterance_id,
                ):
                    if cancel_event is not None and cancel_event.is_set():
                        await provider.cancel(request_id)
                        native_audio_output.stop()
                        return
                    if trace is not None and index == 0:
                        trace.mark(
                            vl.TTS_FIRST_AUDIO_CHUNK,
                            engine=info.provider, chunk_index=index,
                        )
                    collected.append(chunk.data)
                    index += 1
                    yield chunk

        def _on_playback_started() -> None:
            if trace is not None:
                trace.mark(vl.TTS_PLAYBACK_STARTED, engine=info.provider)

        await play_chunks(
            _chunks(),
            sample_rate=info.sample_rate,
            on_playback_started=_on_playback_started,
        )

        if trace is not None:
            trace.mark(vl.TTS_SYNTHESIS_COMPLETED, engine=info.provider)
            trace.mark(vl.TTS_PLAYBACK_COMPLETED, engine=info.provider)

        if collected:
            from jarvis.audio.tts.wav import pcm_to_wav

            _last_tts.store(
                text,
                emotion,
                pcm_to_wav(
                    b"".join(collected),
                    sample_rate=info.sample_rate,
                    channels=info.channels,
                ),
            )

    def _stop_current_tts(self) -> None:
        """Interrompt immédiatement la sortie active (priorité critique/barge-in)."""
        native_audio_output.stop()
        if self._tts_proc and self._tts_proc.returncode is None:
            try:
                self._tts_proc.kill()
            except Exception:
                pass

    async def _play_audio_local(
        self,
        audio_bytes: bytes,
        *,
        cancel_event: asyncio.Event | None = None,
    ) -> None:
        """Joue l'audio via sounddevice (prioritaire), sans service TTS réseau."""
        if cancel_event and cancel_event.is_set():
            return

        if native_audio_output.available:
            played = await native_audio_output.play_bytes(audio_bytes)
            if played:
                return
            logger.warning(
                "[audio_daemon] Lecture native échouée (len=%d magic=%s) — repli afplay",
                len(audio_bytes),
                audio_bytes[:8].hex() if audio_bytes else "",
            )

        # Fallback minimal si sounddevice absent / conversion impossible
        tmp_path: str | None = None
        try:
            from audio.audio_format import playback_file_extension

            ext = playback_file_extension(audio_bytes)
            with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
                tmp.write(audio_bytes)
                tmp_path = tmp.name
            logger.info(
                "[audio_daemon] afplay fallback %s (%d octets) — sortie = défaut macOS",
                ext,
                len(audio_bytes),
            )
            self._tts_proc = await asyncio.create_subprocess_exec(
                "afplay", tmp_path,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            await _wait_subprocess(self._tts_proc, context="afplay playback fallback")
            self._tts_proc = None
        except Exception as e:
            logger.warning("[audio_daemon] playback fallback : %s", e)
        finally:
            if tmp_path:
                try:
                    Path(tmp_path).unlink(missing_ok=True)
                except Exception:
                    pass

    async def _play_end_sound(self) -> None:
        """Joue le son de fin de session (bip grave 440Hz)."""
        if not END_SOUND_PATH.exists():
            logger.debug("[audio_daemon] Son de fin absent — ignoré")
            return
        try:
            proc = await asyncio.create_subprocess_exec(
                "afplay", str(END_SOUND_PATH),
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            await _wait_subprocess(proc, context="end_sound afplay")
        except Exception as e:
            logger.debug("[audio_daemon] Son de fin erreur : %s", e)

    @staticmethod
    def _chunk_rms(chunk: bytes) -> float:
        """Calcule le RMS normalisé d'un chunk PCM 16-bit mono."""
        n = len(chunk) // 2
        if n == 0:
            return 0.0
        samples = struct.unpack(f"{n}h", chunk)
        sum_sq = sum(s * s for s in samples)
        return math.sqrt(sum_sq / n) / 32768.0

    @staticmethod
    def _pcm_to_wav(pcm_bytes: bytes) -> bytes:
        """Convertit un buffer PCM 16-bit 16kHz mono en WAV."""
        buf = io.BytesIO()
        with wave.open(buf, "w") as f:
            f.setnchannels(CHANNELS)
            f.setsampwidth(SAMPLE_WIDTH)
            f.setframerate(SAMPLE_RATE)
            f.writeframes(pcm_bytes)
        return buf.getvalue()

    def _resolve_input_device_index(self, pa: Any) -> int | None:
        """Résout l'index du périphérique d'entrée depuis la config.

        Priorité :
        1. ``AUDIO_DAEMON_INPUT_DEVICE`` explicite dans .env (nom ou sous-chaîne)
        2. Entrée par défaut macOS / système
        3. Premier micro trouvé (dernier recours)
        """
        device_name = (getattr(config, "AUDIO_DAEMON_INPUT_DEVICE", "") or "").strip()
        if device_name:
            for i in range(pa.get_device_count()):
                info = pa.get_device_info_by_index(i)
                if info.get("maxInputChannels", 0) > 0 and device_name.lower() in str(info.get("name", "")).lower():
                    logger.info("[audio_daemon] Peripherique d'entree selectionne : %s (index=%d)", info["name"], i)
                    return i
            logger.warning(
                "[audio_daemon] Peripherique '%s' non trouve — fallback sur defaut systeme",
                device_name,
            )

        # Defaut systeme avec index explicite (evite Errno -9996 sous LaunchAgent)
        try:
            default_info = pa.get_default_input_device_info()
            idx = int(default_info["index"])
            logger.info(
                "[audio_daemon] Entree defaut systeme : %s (index=%d)",
                default_info.get("name"),
                idx,
            )
            return idx
        except Exception as e:
            logger.warning("[audio_daemon] Pas de defaut systeme (%s) — 1er micro trouve", e)

        for i in range(pa.get_device_count()):
            info = pa.get_device_info_by_index(i)
            if info.get("maxInputChannels", 0) > 0:
                logger.info("[audio_daemon] Fallback 1er micro : %s (index=%d)", info["name"], i)
                return i

        logger.error("[audio_daemon] Aucun peripherique d'entree disponible")
        return None

    def _cleanup_audio(self) -> None:
        """Libere proprement les ressources audio."""
        self._signal_input_stop()
        self._cancel_wake_thread()
        # Tuer le processus TTS si actif
        if self._tts_proc and self._tts_proc.returncode is None:
            try:
                self._tts_proc.kill()
                self._tts_proc = None
            except Exception:
                pass
        self._tts_playing_event.clear()
        if self._stream:
            try:
                if self._stream.is_active():
                    self._stream.stop_stream()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        if self._pa:
            pa = self._pa
            self._pa = None
            try:
                pa.terminate()
            except Exception as e:
                logger.debug("[audio_daemon] terminate PyAudio : %s", e)
        self._input_stop_event = None
        # Annuler les tâches async si encore actives (filet de sécurité)
        for task_attr in ("_vad_task", "_process_task", "_watchdog_task"):
            task: asyncio.Task[Any] | None = getattr(self, task_attr, None)
            if task and not task.done():
                task.cancel()

    def _signal_input_stop(self) -> None:
        """Arrête le cycle micro courant sans pouvoir être réarmé par start()."""
        if self._input_stop_event is not None:
            self._input_stop_event.set()

    async def _wait_for_input_thread(self, timeout: float = 5.0) -> None:
        """Joint le lecteur PortAudio avant toute réouverture du périphérique."""
        future = self._input_future
        if future is None:
            return
        try:
            if not future.done():
                await asyncio.wait_for(asyncio.shield(future), timeout=timeout)
            else:
                future.result()
        except asyncio.TimeoutError:
            logger.error(
                "[audio_daemon] Thread micro bloqué après %.1fs — fermeture forcée",
                timeout,
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.debug("[audio_daemon] fin thread micro : %s", e)
        finally:
            self._input_future = None

    # ── Broadcast WebSocket ───────────────────────────────────────────────────

    async def _broadcast_state(self, extra: dict[str, Any] | None = None) -> None:
        """Envoie l'état du daemon à tous les clients WebSocket connectés."""
        if self._broadcast is None:
            return
        event: dict[str, Any] = {
            "type": "audio_daemon_state",
            "state": self.state,
            "enabled": self.enabled,
            "wake_word_enabled": self.wake_word_enabled,
            "continuous_mode": self.continuous_mode,
            "last_interaction": self._last_interaction,
        }
        if extra:
            event.update(extra)
        try:
            await self._broadcast(event)
        except Exception as e:
            logger.debug("[audio_daemon] broadcast state: %s", e)

    def _schedule_state_broadcast(self, state: str) -> None:
        """Programme un broadcast d'état depuis un thread (thread-safe)."""
        self.state = state
        try:
            loop = asyncio.get_running_loop()
            asyncio.run_coroutine_threadsafe(self._broadcast_state(), loop)
        except RuntimeError:
            pass


# ── Singleton ─────────────────────────────────────────────────────────────────

audio_daemon = AudioDaemon()


@event_bus.on("notification.created")
async def _speak_priority_notification(event: JarvisEvent) -> None:
    """Annonce les notifications prioritaires dès que le lecteur central tourne."""
    payload = event.payload
    if payload.get("priority") not in ("urgent", "high"):
        return
    if not audio_daemon.enabled and not voice_queue.running:
        return
    title = str(payload.get("title") or "Notification")
    content = str(payload.get("content") or "").strip()
    text = f"{title}. {content}" if content else title
    is_urgent = payload.get("priority") == "urgent"
    await audio_daemon._play_tts(
        text,
        emotion="urgent" if is_urgent else "alert",
    )
