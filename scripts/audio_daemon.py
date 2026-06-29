"""Daemon audio natif JARVIS — wake word + conversation mains libres sur le Mac Mini.

Pipeline (architecture 3 boucles + watchdog — correctif 29 juin 2026) :
  Thread pyaudio (16kHz mono PCM) → asyncio.Queue[bytes] (maxsize=300)
  → _vad_loop_safe : VAD adaptatif + try/except par itération → heartbeat 60s
  → _process_loop_safe : WAV → stt.transcribe() → _process_voice_fast() (DeepSeek flash direct)
  → Edge TTS (fr-FR-Vivienne) → afplay (subprocess) → purge post-TTS
  → _mic_watchdog : détection déconnexion USB / silence prolongé → auto-restart
  — micro stoppé pendant playback, queues drainees, seuil de silence adaptatif.
  — boucle immortelle : redémarrage automatique après crash (délai 3s).

Le daemon cohabite avec la page /voice web (micros différents, même backend).
"""

from __future__ import annotations

import asyncio
import io
import logging
import math
import os
import struct
import subprocess
import tempfile
import time
import wave
from pathlib import Path
from typing import Any, Callable, Coroutine

import config
from database import create_conversation, get_setting, save_message

logger = logging.getLogger("audio_daemon")

# ── Constantes nominales ─────────────────────────────────────────────────────

SAMPLE_RATE = 16000
CHANNELS = 1
SAMPLE_WIDTH = 2  # 16-bit
CHUNK_MS = 30  # fenêtre VAD
CHUNK_SAMPLES = int(SAMPLE_RATE * CHUNK_MS / 1000)
CHUNK_BYTES = CHUNK_SAMPLES * SAMPLE_WIDTH

# Wake word fallback volume
FALLBACK_WAKE_RMS = 0.03
FALLBACK_WAKE_DURATION_MS = 500
FALLBACK_WAKE_CHUNKS = int(FALLBACK_WAKE_DURATION_MS / CHUNK_MS)

# Phrases de fin de conversation
END_PHRASES: tuple[str, ...] = (
    "merci jarvis", "c'est bon jarvis", "c'est tout jarvis",
    "merci c'est bon", "c'est fini", "bonne nuit jarvis",
    "a plus jarvis", "ok merci", "au revoir", "stop",
    "arrête", "arrête-toi",
)

# Son de confirmation
WAKE_SOUND_PATH = Path(__file__).resolve().parent.parent / "data" / "sounds" / "wake.wav"
END_SOUND_PATH = Path(__file__).resolve().parent.parent / "data" / "sounds" / "end.wav"

# Type du callback broadcast injecté par main.py
BroadcastFn = Callable[[dict[str, Any]], Coroutine[Any, Any, None]]


def _generate_wake_sound() -> None:
    """Génère un bip sinusoïdal 880Hz, 150ms, fade in/out si absent."""
    path = WAKE_SOUND_PATH
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    duration = 0.150
    freq = 880.0
    n_samples = int(SAMPLE_RATE * duration)
    fade_samples = int(SAMPLE_RATE * 0.020)

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
    fade_samples = int(SAMPLE_RATE * 0.015)

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
        "_tts_playing",
        "_last_interaction",
        "_last_tts_end",
        "_conv_start_time",
        "_conv_id",
        "_running",
        "_stop_event",
        "_interrupt_event",
        "_loop",
        "_tts_proc",
        "_no_frame_count",
        "_audio_buffer",
        "_speech_frames",
        "_silence_frames",
        "_mic_mute_logged",
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
        self._utterance_queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=3)  # phrases complètes — surchargé dans start()
        self._wake_event: asyncio.Event | None = None
        self._vad_task: asyncio.Task[Any] | None = None
        self._process_task: asyncio.Task[Any] | None = None
        self._watchdog_task: asyncio.Task[Any] | None = None
        self._tts_playing: bool = False
        self._last_interaction: float = 0.0
        self._last_tts_end: float = 0.0
        self._conv_start_time: float = 0.0
        self._conv_id: int | None = None
        self._running: bool = False
        self._stop_event: asyncio.Event | None = None
        self._interrupt_event: asyncio.Event | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._tts_proc: asyncio.subprocess.Process | None = None
        self._no_frame_count: int = 0
        self._audio_buffer: bytearray = bytearray()
        self._speech_frames: int = 0
        self._silence_frames: int = 0
        self._mic_mute_logged: bool = False

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
        self._loop = asyncio.get_running_loop()
        logger.info("[audio_daemon] Démarrage (boucle immortelle)…")

        # Sons de confirmation au premier lancement
        try:
            _generate_wake_sound()
            _generate_end_sound()
        except Exception as e:
            logger.warning("[audio_daemon] Génération sons échouée : %s", e)

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
            except Exception as e:
                consecutive_crashes += 1
                crash_type = type(e).__name__
                logger.error(
                    "[audio_daemon] Crash #%d (%s) : %s — redémarrage dans %.0fs",
                    consecutive_crashes, crash_type, e, backoff_s,
                )
                self._cleanup()

                # Backoff exponentiel avec cap
                await asyncio.sleep(backoff_s)
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

        self._pa = pyaudio.PyAudio()
        self._audio_queue = asyncio.Queue(maxsize=300)
        self._utterance_queue = asyncio.Queue(maxsize=3)

        # Architecture 3 boucles :
        #   _vad_loop_safe : VAD + wake word + interruption — jamais bloqué
        #   _process_loop_safe : STT + LLM + TTS — peut bloquer 2-5s sans impacter le VAD
        #   _mic_watchdog : détection déconnexion micro — restart si silencieux > 60s
        loop = asyncio.get_running_loop()
        self._vad_task = asyncio.create_task(self._vad_loop_safe(), name="audio_daemon_vad")
        self._process_task = asyncio.create_task(self._process_loop_safe(), name="audio_daemon_process")
        self._watchdog_task = asyncio.create_task(self._mic_watchdog(), name="audio_daemon_watchdog")

        # Wake word (Porcupine ou fallback volume)
        if self.wake_word_enabled and not self.continuous_mode:
            self._start_wake_detection(loop)

        self.state = "wake_listening" if self.wake_word_enabled else "listening"
        logger.info("[audio_daemon] Actif — state=%s wake_word=%s", self.state, self.wake_word_enabled)
        await self._broadcast_state()

        # Pre-charger le modele STT local (evite le lag de 2s au premier message)
        try:
            from audio.stt_local import stt_local as _stt_local
            if _stt_local.available and not _stt_local._loaded:
                logger.info("[audio_daemon] Pre-chargement modele STT local (%s) ...", _stt_local._model_size)
                loop_local = asyncio.get_running_loop()
                await loop_local.run_in_executor(None, _stt_local._ensure_model_sync)
                logger.info("[audio_daemon] Modele STT local pret")
        except Exception:
            pass

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

        self._tts_playing = False

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

        # Terminer PyAudio
        if self._pa:
            try:
                self._pa.terminate()
            except Exception:
                pass
            self._pa = None

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
        self._running = False
        self.enabled = False

        if self._stop_event:
            self._stop_event.set()
        if self._interrupt_event:
            self._interrupt_event.set()

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

        # Libère pyaudio
        self._cleanup_audio()
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
            # Redémarre la détection
            self._cancel_wake_thread()
            loop = asyncio.get_running_loop()
            if self.wake_word_enabled and not self.continuous_mode:
                self._start_wake_detection(loop)
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
            loop = asyncio.get_running_loop()
            if not enabled and self.wake_word_enabled:
                self._start_wake_detection(loop)
                self.state = "wake_listening"
            else:
                self.state = "listening"
            await self._broadcast_state()

        logger.info("[audio_daemon] continuous=%s wake_word=%s", self.continuous_mode, self.wake_word_enabled)

    def get_status(self) -> dict[str, Any]:
        """Retourne l'état complet pour l'API."""
        # STT engine — ElevenLabs Scribe en priorite, faster-whisper en fallback
        stt_engine = "none"
        try:
            from audio.stt import stt as _stt
            if getattr(_stt, "available", False):
                stt_engine = "elevenlabs_scribe"
        except Exception:
            pass
        if stt_engine == "none":
            try:
                from audio.stt_local import stt_local as _stt_local
                if _stt_local.available:
                    stt_engine = _stt_local.get_backend_name()
            except Exception:
                pass

        # TTS engine — Edge TTS (fr-FR-VivienneMultilingualNeural)
        tts_engine = "edge"
        try:
            from audio.tts import get_tts_by_name as _get_tts
            engine = _get_tts("edge")
            if not (engine and engine.available):
                tts_engine = "macos"  # fallback effectif
        except Exception:
            tts_engine = "macos"

        return {
            "enabled": self.enabled,
            "state": self.state,
            "wake_word_enabled": self.wake_word_enabled,
            "continuous_mode": self.continuous_mode,
            "last_interaction": self._last_interaction,
            "stt_engine": stt_engine,
            "tts_engine": tts_engine,
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
        assert interrupt_event is not None

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

                while self._running:
                    try:
                        data = stream.read(CHUNK_SAMPLES, exception_on_overflow=False)
                    except OSError:
                        break

                    rms = self._chunk_rms(data)
                    if rms <= 0.0001:
                        silent_since += 1
                        if silent_since == PERM_CHECK_CHUNKS and not self._mic_mute_logged:
                            self._mic_mute_logged = True
                            logger.critical(
                                "[audio_daemon] Micro muet depuis 3s — permission macOS probablement refusee. "
                                "Reglages Systeme > Confidentialite > Microphone → cocher Cursor et Terminal."
                            )
                    else:
                        silent_since = 0

                    # Ne pas alimenter la queue pendant le TTS (anti-écho + éviter QueueFull)
                    if self._tts_playing:
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

        loop.run_in_executor(None, _blocking_input)

        # ── Paramètres VAD ──
        silence_ms = getattr(config, "AUDIO_DAEMON_SILENCE_MS", 1500)
        speech_threshold = getattr(config, "AUDIO_DAEMON_SPEECH_THRESHOLD", 0.02)
        min_speech_ms = getattr(config, "AUDIO_DAEMON_MIN_SPEECH_MS", 600)
        max_utterance_s = getattr(config, "AUDIO_DAEMON_MAX_UTTERANCE_S", 15)
        timeout = getattr(config, "AUDIO_DAEMON_CONVERSATION_TIMEOUT", 15.0)

        silence_chunks = int(silence_ms / CHUNK_MS)
        min_speech_chunks = int(min_speech_ms / CHUNK_MS)
        max_chunks = int(max_utterance_s * 1000 / CHUNK_MS)
        # Seuil de silence adaptatif : si parole < 2s, exiger au moins 2s de silence
        ADAPTIVE_SILENCE_CHUNKS = int(2000 / CHUNK_MS)
        PRE_SPEECH_CHUNKS = 10  # 300ms de pré-buffer

        # ── État VAD ──
        frames: list[bytes] = []
        pre_speech_ring: list[bytes] = []
        has_speech = False
        speech_chunks = 0
        silent_chunks = 0
        total_chunks = 0
        frame_count = 0  # pour heartbeat
        HEARTBEAT_INTERVAL = 2000  # ~60s à 33fps

        consecutive_errors = 0
        MAX_CONSECUTIVE_ERRORS = 50

        while self._running and self._stop_event and not self._stop_event.is_set():
            try:
                # ── Mode veille / wake_listening : on ne consomme pas la queue ──
                if self.state in ("idle", "wake_listening"):
                    wake = self._wake_event
                    if wake is not None:
                        try:
                            await asyncio.wait_for(wake.wait(), timeout=0.5)
                        except asyncio.TimeoutError:
                            continue
                        wake.clear()
                        if not self._running:
                            break
                        # Mode continu : ne jamais timeout vers idle
                        timeout_ref = self._conv_start_time
                        self._conv_start_time = time.time() if not self.continuous_mode else 0.0
                        try:
                            if getattr(config, "AUDIO_DAEMON_WAKE_SOUND", True) and WAKE_SOUND_PATH.exists():
                                proc = await asyncio.create_subprocess_exec(
                                    "afplay", str(WAKE_SOUND_PATH),
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                )
                                await proc.wait()
                            else:
                                await self._play_tts("Oui Monsieur ?", emotion="neutral")
                        except Exception:
                            pass
                        self.state = "listening"
                        await self._broadcast_state()
                        # Reset VAD après wake
                        frames.clear()
                        pre_speech_ring.clear()
                        has_speech = False
                        speech_chunks = 0
                        silent_chunks = 0
                        total_chunks = 0
                    continue

                # ── Consomme la queue PCM ──
                try:
                    chunk = await asyncio.wait_for(audio_queue.get(), timeout=0.1)
                except asyncio.TimeoutError:
                    # Vérifier timeout conversation (pas en mode continu)
                    if (
                        not self.continuous_mode
                        and self._conv_start_time > 0
                        and (time.time() - self._conv_start_time > timeout)
                    ):
                        logger.info("[audio_daemon] Timeout conversation (%ss) → retour veille", timeout)
                        self._conv_start_time = 0.0
                        self.state = "wake_listening" if self.wake_word_enabled else "listening"
                        await self._broadcast_state()
                        frames.clear()
                        pre_speech_ring.clear()
                        has_speech = False
                        speech_chunks = 0
                        silent_chunks = 0
                        total_chunks = 0
                    continue

                frame_count += 1
                consecutive_errors = 0  # reset à chaque frame réussie

                # Heartbeat toutes les ~60s
                if frame_count % HEARTBEAT_INTERVAL == 0:
                    logger.debug(
                        "[audio_daemon] Heartbeat — state=%s, queue=%d, speech=%d",
                        self.state, audio_queue.qsize(), self._speech_frames,
                    )

                # ── En speaking ou processing : détection d'interruption ──
                if self.state in ("speaking", "processing"):
                    rms = self._chunk_rms(chunk)
                    if rms > speech_threshold:
                        # Interruption détectée — tuer le playback TTS
                        if self._tts_proc and self._tts_proc.returncode is None:
                            try:
                                self._tts_proc.kill()
                            except Exception:
                                pass
                            self._tts_proc = None
                        self._tts_playing = False
                        self.state = "listening"
                        interrupt_event.set()
                        await self._broadcast_state()
                        logger.info("[audio_daemon] Interruption détectée — nouvelle écoute")
                        # Réinitialiser VAD pour la nouvelle phrase
                        frames.clear()
                        pre_speech_ring.clear()
                        has_speech = True
                        speech_chunks = 1
                        silent_chunks = 0
                        total_chunks = 1
                        frames.append(chunk)
                        pre_speech_ring.append(chunk)
                        if len(pre_speech_ring) > PRE_SPEECH_CHUNKS:
                            pre_speech_ring.pop(0)
                        continue
                    # Sinon, ignorer (anti-écho pendant playback)
                    continue

                # ── Mode listening : accumulation et VAD ──
                frames.append(chunk)
                total_chunks += 1
                rms = self._chunk_rms(chunk)

                # Pre-speech ring buffer
                pre_speech_ring.append(chunk)
                if len(pre_speech_ring) > PRE_SPEECH_CHUNKS:
                    pre_speech_ring.pop(0)

                if rms > speech_threshold:
                    if not has_speech:
                        has_speech = True
                        speech_chunks = 0
                        silent_chunks = 0
                        # Injecter le pre-speech buffer (300ms avant le seuil)
                        frames = list(pre_speech_ring) + frames
                        total_chunks += len(pre_speech_ring)
                    speech_chunks += 1
                    self._speech_frames += 1
                elif has_speech:
                    silent_chunks += 1
                    self._silence_frames += 1

                # Seuil de silence adaptatif : parole courte → silence plus long exigé
                effective_silence = silence_chunks
                if speech_chunks < ADAPTIVE_SILENCE_CHUNKS:
                    effective_silence = max(silence_chunks, ADAPTIVE_SILENCE_CHUNKS)

                # Fin de phrase ou flush forcé
                flush_force = has_speech and total_chunks >= max_chunks
                end_detected = has_speech and silent_chunks >= effective_silence

                if (end_detected and speech_chunks >= min_speech_chunks) or flush_force:
                    if end_detected:
                        logger.debug(
                            "[audio_daemon] Fin de phrase : speech=%d, silence=%d (seuil=%d)",
                            speech_chunks, silent_chunks, effective_silence,
                        )
                    else:
                        logger.debug("[audio_daemon] Flush forcé : %d chunks (max)", total_chunks)

                    audio_bytes = b"".join(frames)
                    frames.clear()
                    pre_speech_ring.clear()
                    has_speech = False
                    speech_chunks = 0
                    silent_chunks = 0
                    total_chunks = 0

                    try:
                        utterance_queue.put_nowait(audio_bytes)
                    except asyncio.QueueFull:
                        logger.warning("[audio_daemon] utterance_queue pleine — utterance jetée")

                # Timeout conversation (pas en mode continu)
                if (
                    not self.continuous_mode
                    and self._conv_start_time > 0
                    and (time.time() - self._conv_start_time > timeout)
                ):
                    logger.info("[audio_daemon] Timeout conversation (%ss) → retour veille", timeout)
                    self._conv_start_time = 0.0
                    self.state = "wake_listening" if self.wake_word_enabled else "listening"
                    await self._broadcast_state()
                    frames.clear()
                    pre_speech_ring.clear()
                    has_speech = False
                    speech_chunks = 0
                    silent_chunks = 0
                    total_chunks = 0

            except asyncio.CancelledError:
                raise  # propager l'annulation au _run()
            except Exception as e:
                consecutive_errors += 1
                logger.warning("[audio_daemon] VAD erreur #%d: %s", consecutive_errors, e)
                if consecutive_errors > MAX_CONSECUTIVE_ERRORS:
                    logger.error("[audio_daemon] Trop d'erreurs VAD (%d) — restart", consecutive_errors)
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

        # Détection STT
        stt_available = False
        try:
            from audio.stt import stt as _stt
            stt_available = _stt is not None and getattr(_stt, "available", False)
        except Exception:
            pass
        if not stt_available:
            try:
                from audio.stt_local import stt_local as _stt_local
                stt_available = _stt_local.available
            except Exception:
                pass

        if not stt_available:
            logger.warning("[audio_daemon] Aucun STT disponible — daemon muet")

        consecutive_errors = 0
        MAX_CONSECUTIVE_ERRORS = 10
        CONVERSATION_TIMEOUT = getattr(config, "AUDIO_DAEMON_CONVERSATION_TIMEOUT", 15.0)

        while self._running and self._stop_event and not self._stop_event.is_set():
            try:
                audio_bytes = await asyncio.wait_for(utterance_queue.get(), timeout=2.0)
                consecutive_errors = 0  # reset

                # Vérifier si on a été interrompu pendant l'attente
                if interrupt_event.is_set():
                    interrupt_event.clear()
                    continue

                await self._process_single_utterance(audio_bytes, stt_available)

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
                self._tts_playing = False
                if self._stream and not self._stream.is_active():
                    try:
                        self._stream.start_stream()
                    except Exception:
                        pass
                if consecutive_errors > MAX_CONSECUTIVE_ERRORS:
                    logger.error("[audio_daemon] Trop d'erreurs process (%d) — restart", consecutive_errors)
                    raise RuntimeError(f"Process loop crash after {consecutive_errors} errors") from e
                await asyncio.sleep(1)

        logger.info("[audio_daemon] Boucle process terminée")

    # ── Traitement d'une utterance ────────────────────────────────────────────

    async def _process_single_utterance(self, pcm_bytes: bytes, stt_available: bool) -> None:
        """Traitement complet d'une phrase : STT → _process_voice_fast → TTS → playback + purge post-TTS.

        Utilise le pipeline vocal rapide (_process_voice_fast) qui bypass l'orchestrateur
        et appelle DeepSeek flash directement. Cible : < 2s entre fin de phrase et début TTS.
        """
        interrupt_event = self._interrupt_event
        assert interrupt_event is not None

        # Vérifier interruption avant de commencer
        if interrupt_event.is_set():
            interrupt_event.clear()
            return

        self.state = "processing"
        await self._broadcast_state()

        # 1. STT — faster-whisper local d'abord (~50ms), ElevenLabs Scribe cloud en fallback (~400ms)
        wav_bytes = self._pcm_to_wav(pcm_bytes)
        text = ""

        # 1a. STT local (faster-whisper, gratuit, zero latence reseau)
        local_available = False
        try:
            from audio.stt_local import stt_local as _stt_local
            local_available = _stt_local is not None and getattr(_stt_local, "available", False)
        except Exception:
            pass

        if local_available:
            try:
                from audio.stt_local import stt_local as _stt_local
                text = await _stt_local.transcribe(wav_bytes, language=getattr(config, "LANGUAGE", "fr"))
                if text and len(text.strip()) >= 3:
                    logger.debug("[audio_daemon] STT local : %s", text[:80])
            except Exception as e:
                logger.warning("[audio_daemon] STT local echoue : %s — fallback Scribe", e)
                text = ""

        # 1b. STT cloud (ElevenLabs Scribe, fallback si local echoue)
        if not text:
            scribe_available = False
            try:
                from audio.stt import stt as _stt
                scribe_available = _stt is not None and getattr(_stt, "available", False)
            except Exception:
                pass

            if scribe_available:
                try:
                    from audio.stt import stt as _stt
                    text = await _stt.transcribe(wav_bytes, language=getattr(config, "LANGUAGE", "fr"))
                    if text:
                        logger.debug("[audio_daemon] STT ElevenLabs : %s", text[:80])
                except Exception as e:
                    logger.warning("[audio_daemon] STT ElevenLabs echoue : %s", e)

        # Vérifier interruption après STT
        if interrupt_event.is_set():
            interrupt_event.clear()
            logger.debug("[audio_daemon] Interruption après STT — abandon traitement")
            self.state = "wake_listening" if self.wake_word_enabled else "listening"
            await self._broadcast_state()
            return

        if not text:
            if not scribe_available and not local_stt_available:
                logger.warning("[audio_daemon] Aucun STT disponible — skip")
            self.state = "wake_listening" if self.wake_word_enabled else "listening"
            await self._broadcast_state()
            return

        # Filtrage post-TTS : transcription trop courte dans les 2s après un TTS → résidu d'écho
        now = time.time()
        if len(text.strip()) < 10 and (now - self._last_tts_end) < 2.0:
            logger.debug("[audio_daemon] Transcription post-TTS ignorée (résidu d'écho) : %s", text[:60])
            self.state = "wake_listening" if self.wake_word_enabled else "listening"
            await self._broadcast_state()
            return

        if len(text.strip()) < 3:
            logger.debug("[audio_daemon] Transcription trop courte ou vide — ignorée")
            self.state = "wake_listening" if self.wake_word_enabled else "listening"
            await self._broadcast_state()
            return

        logger.info("[audio_daemon] Entendu : %s", text)

        # Broadcast transcript
        await self._broadcast_state({"transcript": text})

        # 2. Vérification phrases de fin
        lower = text.lower()
        if any(p in lower for p in END_PHRASES):
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
            self.state = "wake_listening" if self.wake_word_enabled else "listening"
            await self._broadcast_state()
            return

        # 3. Pipeline vocal rapide (bypass orchestrateur, DeepSeek flash direct)
        self._last_interaction = time.time()

        if self._conv_id is None:
            self._conv_id = create_conversation(agent="daemon_audio")

        try:
            from main import _process_voice_fast
            result = await _process_voice_fast(text, self._conv_id)
        except Exception as e:
            logger.exception("[audio_daemon] _process_voice_fast : %s", e)
            self.state = "wake_listening" if self.wake_word_enabled else "listening"
            await self._broadcast_state()
            await self._play_tts("Désolé Monsieur, je rencontre un problème technique.", emotion="concerned")
            return

        response_text = (result or {}).get("text") or ""
        emotion = (result or {}).get("emotion", "neutral") or "neutral"
        latency_ms = (result or {}).get("latency_ms", 0)
        logger.info("[audio_daemon] Voice fast : %.0fms", latency_ms)

        # Broadcast response
        await self._broadcast_state({"response": response_text, "emotion": emotion})

        # Vérifier interruption avant TTS
        if interrupt_event.is_set():
            interrupt_event.clear()
            logger.debug("[audio_daemon] Interruption avant TTS — abandon playback")
            self._tts_playing = False
            self.state = "wake_listening" if self.wake_word_enabled else "listening"
            await self._broadcast_state()
            return

        # 4. TTS + playback
        self.state = "speaking"
        await self._broadcast_state()

        if self._stream:
            try:
                self._stream.stop_stream()
            except Exception:
                pass

        try:
            await self._play_tts(response_text, emotion=emotion)
        except Exception as e:
            logger.warning("[audio_daemon] TTS/playback échoué : %s", e)

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

        # 6. Reprendre le micro
        if self._stream and self._running:
            try:
                self._stream.start_stream()
            except Exception:
                pass

        self._tts_playing = False
        self._last_tts_end = time.time()
        self._conv_start_time = time.time()  # reset timeout
        self.state = "wake_listening" if self.wake_word_enabled else "listening"
        await self._broadcast_state()

    # ── Watchdog micro ───────────────────────────────────────────────────────

    async def _mic_watchdog(self) -> None:
        """Vérifie que le micro est toujours actif — détecte les déconnexions USB.

        Toutes les 10 secondes :
        - Vérifie que le stream est actif (sinon tente de le redémarrer)
        - Vérifie que des frames arrivent dans la queue (sinon, après 60s de silence, restart)
        """
        MIC_WATCHDOG_INTERVAL = 10  # secondes
        MAX_NO_FRAME_ITERATIONS = 6  # 6 x 10s = 60s sans frame → restart

        while self.enabled and self._stop_event and not self._stop_event.is_set():
            try:
                await asyncio.sleep(MIC_WATCHDOG_INTERVAL)

                if self._tts_playing:
                    # Micro volontairement arrêté pendant le playback TTS
                    self._no_frame_count = 0
                    continue

                # Vérifier que le stream est actif
                if self._stream and not self._stream.is_active():
                    logger.warning("[audio_daemon] Stream micro inactif — tentative de réouverture")
                    try:
                        self._stream.start_stream()
                    except Exception as e:
                        logger.error("[audio_daemon] Micro inaccessible : %s — restart complet", e)
                        raise RuntimeError("Micro déconnecté (stream inaccessible)") from e

                # Vérifier que la queue reçoit des frames (seulement en mode écoute)
                if self._audio_queue.empty() and self.state in ("listening", "wake_listening"):
                    self._no_frame_count += 1
                    if self._no_frame_count > MAX_NO_FRAME_ITERATIONS:
                        logger.error(
                            "[audio_daemon] Aucune frame micro depuis %ds — restart",
                            self._no_frame_count * MIC_WATCHDOG_INTERVAL,
                        )
                        raise RuntimeError("Micro silencieux (aucune frame)")
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
        """Démarre la détection du wake word (Porcupine ou fallback volume)."""
        access_key = str(getattr(config, "PORCUPINE_ACCESS_KEY", "") or "")

        try:
            import pvporcupine  # type: ignore[import-not-found]
            self._porcupine = pvporcupine.create(access_key=access_key, keywords=["jarvis"])
            logger.info("[audio_daemon] Wake word 'Jarvis' activé (Porcupine)")
            self._wake_thread_future = loop.run_in_executor(None, self._porcupine_wake_loop)
        except Exception as e:
            logger.warning("[audio_daemon] Porcupine indisponible (%s) — fallback volume", e)
            self._porcupine = None
            self._wake_thread_future = loop.run_in_executor(None, self._volume_wake_loop)

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
            except Exception:
                pass
            self._porcupine = None

    def _porcupine_wake_loop(self) -> None:
        """Thread bloquant : écoute le wake word via Porcupine."""
        try:
            import pvporcupine  # type: ignore[import-not-found]
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

    async def _play_tts(self, text: str, emotion: str = "neutral") -> None:
        """Synthetise et joue le texte sur les enceintes locales.

        Priorite :
        1. Edge TTS (fr-FR-VivienneMultilingualNeural, qualite naturelle)
        2. macOS TTS natif (say + afconvert) — fallback zero reseau
        3. say direct — dernier recours
        """
        if not text or not text.strip():
            return

        self._tts_playing = True
        try:
            from audio.tts import get_tts_by_name as _get_tts, macos_tts as _macos

            # 1. Edge TTS (voix naturelle, latence ~200ms)
            engine = _get_tts("edge")
            if engine and engine.available:
                audio_bytes = await engine.synthesize(text, emotion=emotion)
                if audio_bytes:
                    await self._play_audio_local(audio_bytes)
                    return

            # 2. Fallback macOS TTS (local, zero reseau)
            if _macos.available:
                logger.info("[audio_daemon] Edge TTS indisponible — fallback macOS TTS")
                audio_bytes = await _macos.synthesize(text, emotion=emotion)
                if audio_bytes:
                    await self._play_audio_local(audio_bytes)
                    return

            # 3. Fallback extreme : say direct
            logger.warning("[audio_daemon] TTS indisponible — fallback say direct")
            proc = await asyncio.create_subprocess_exec(
                "say", text,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            await proc.wait()
        except Exception as e:
            logger.warning("[audio_daemon] TTS erreur : %s", e)
        finally:
            self._tts_playing = False
            self._last_tts_end = time.time()

    async def _play_audio_local(self, audio_bytes: bytes) -> None:
        """Joue l'audio (MP3 Edge, WAV Kokoro, M4A macOS) localement.

        Priorite : sounddevice (direct CoreAudio, instantane) → afplay (fallback subprocess).
        """
        # 1. Tenter sounddevice (instantané, pas de subprocess)
        try:
            import sounddevice as sd
            import soundfile as sf

            audio_data, samplerate = sf.read(io.BytesIO(audio_bytes))
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, lambda: sd.play(audio_data, samplerate, blocking=True))
            return
        except ImportError:
            pass  # sounddevice/soundfile pas installés → fallback afplay
        except Exception as e:
            logger.warning("[audio_daemon] sounddevice erreur : %s — fallback afplay", e)

        # 2. Fallback : afplay (subprocess macOS, ~200ms startup)
        tmp_path: str | None = None
        try:
            # Detection du format : WAV = RIFF, MP3 = ID3 ou 0xFF 0xFB
            if audio_bytes[:4] == b"RIFF":
                ext = ".wav"
            elif audio_bytes[:3] == b"ID3" or (audio_bytes[0] == 0xFF and (audio_bytes[1] & 0xE0) == 0xE0):
                ext = ".mp3"
            else:
                ext = ".m4a"
            with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
                tmp.write(audio_bytes)
                tmp_path = tmp.name

            self._tts_proc = await asyncio.create_subprocess_exec(
                "afplay", tmp_path,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            await self._tts_proc.wait()
            self._tts_proc = None
        except Exception as e:
            logger.warning("[audio_daemon] playback erreur : %s", e)
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
            await proc.wait()
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

        Priorite :
        1. ``AUDIO_DAEMON_INPUT_DEVICE`` explicite dans .env
        2. Auto-detection "Blue Snowball" si dispo
        3. Defaut systeme
        """
        device_name = getattr(config, "AUDIO_DAEMON_INPUT_DEVICE", "") or ""
        if device_name:
            for i in range(pa.get_device_count()):
                info = pa.get_device_info_by_index(i)
                if info.get("maxInputChannels", 0) > 0 and device_name.lower() in str(info.get("name", "")).lower():
                    logger.info("[audio_daemon] Peripherique d'entree selectionne : %s (index=%d)", info["name"], i)
                    return i
            logger.warning("[audio_daemon] Peripherique '%s' non trouve — fallback sur defaut systeme", device_name)
            return None

        # Auto-detection Blue Snowball
        for i in range(pa.get_device_count()):
            info = pa.get_device_info_by_index(i)
            if info.get("maxInputChannels", 0) > 0 and "blue snowball" in str(info.get("name", "")).lower():
                logger.info("[audio_daemon] Blue Snowball auto-detecte (index=%d)", i)
                return i

        # Defaut systeme
        logger.info("[audio_daemon] Aucun peripherique specifique configure — defaut systeme")
        return None

    def _cleanup_audio(self) -> None:
        """Libere proprement les ressources audio."""
        self._cancel_wake_thread()
        # Tuer le processus TTS si actif
        if self._tts_proc and self._tts_proc.returncode is None:
            try:
                self._tts_proc.kill()
                self._tts_proc = None
            except Exception:
                pass
        self._tts_playing = False
        if self._stream:
            try:
                if self._stream.is_active():
                    self._stream.stop_stream()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        if self._pa:
            try:
                self._pa.terminate()
            except Exception:
                pass
            self._pa = None
        # Annuler les tâches async si encore actives (filet de sécurité)
        for task_attr in ("_vad_task", "_process_task", "_watchdog_task"):
            task: asyncio.Task[Any] | None = getattr(self, task_attr, None)
            if task and not task.done():
                task.cancel()

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
        except Exception:
            pass

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
