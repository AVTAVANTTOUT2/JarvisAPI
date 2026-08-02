"""Text-to-Speech — Edge TTS ou moteurs locaux macOS/Kokoro/TTSKit.

Backends disponibles (``TTS_ENGINE`` dans `.env` ou DB `app_settings`) :
  - ``edge``       — Microsoft Edge Neural (gratuit, faible latence réseau)
  - ``macos``      — say + afconvert (macOS natif, zéro réseau, sort en AAC/M4A)
  - ``kokoro``     — Kokoro local (MLX-Audio par défaut, ONNX en option)
  - ``ttskit``     — sidecar natif local

API :
    tts.synthesize(text, emotion)        → bytes audio
    tts.synthesize_stream(text, emotion) → AsyncGenerator[bytes]
    tts.get_backend_name()               → str

    macos_tts                            → singleton MacOSTTSEngine
    get_tts_by_name(name)                → retourne le bon singleton selon le nom

Frontière réseau : ``edge`` est le seul moteur qui sort de la machine
(``speech.platform.bing.com``). Ses appels sont bornés par
``EDGE_TTS_CONNECT_TIMEOUT_SEC``, ``EDGE_TTS_RECEIVE_TIMEOUT_SEC`` et
``EDGE_TTS_TOTAL_TIMEOUT_SEC``, et ses échecs sont qualifiés par
``audio.tts_errors`` : service injoignable (avertissement) ou contrat rompu
(erreur). Un échec Edge ne bascule jamais en silence sur un moteur local : le
type MIME est annoncé au client avant la synthèse, donc le repli appartient à
l'appelant (``audio.tts_native.get_native_tts_engine`` pour la chaîne locale).
"""

import asyncio
import logging
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, AsyncGenerator

import config
from audio.tts_errors import describe_tts_failure, is_network_unavailable
from jarvis.event_bus import JarvisEvent, event_bus

logger = logging.getLogger(__name__)

VALID_EMOTIONS = frozenset({
    "neutral", "warm", "serious", "concerned", "amused", "urgent", "encouraging"
})

# Type des messages Edge porteurs d'octets MP3 ; les autres sont des marqueurs
# de prosodie (`WordBoundary`, `SentenceBoundary`) sans audio.
EDGE_AUDIO_MESSAGE_TYPE = "audio"


def _emit_background(event: JarvisEvent) -> None:
    """Émet sans bloquer la synthèse, en gardant une référence forte à la tâche.

    `event_bus.emit_nowait` suit ses tâches (`_pending`) : un `create_task` nu
    peut être ramassé par le GC avant d'avoir émis, et rend `wait_until_idle()`
    aveugle. Un bus indisponible ne doit jamais casser un tour de parole.
    """
    try:
        event_bus.emit_nowait(event)
    except Exception as exc:  # pragma: no cover - dépend de l'état de la boucle
        logger.debug("[TTS] émission %s ignorée : %s", event.type, exc)


class TTSEngine:
    """TTS Edge pour les clients réseau ; les moteurs locaux sont séparés."""

    def __init__(self) -> None:
        self._backend: str = "none"
        self.available: bool = False
        try:
            import edge_tts  # noqa: F401

            self._backend = "edge"
            self.available = True
            logger.info("[TTS] Backend : Edge TTS (voix %s)", config.TTS_VOICE)
        except ImportError:
            logger.warning(
                "[TTS] edge-tts non installé (`pip install edge-tts`)."
            )

    def get_backend_name(self) -> str:
        return self._backend

    async def synthesize(self, text: str, emotion: str = "neutral") -> bytes:
        if not self.available or not text or not text.strip():
            return b""
        emotion = emotion if emotion in VALID_EMOTIONS else "neutral"
        logger.debug("[TTS] synthesize backend=%s emotion=%s len=%d", self._backend, emotion, len(text))

        _emit_background(JarvisEvent(
            type="tts.start",
            data={"engine": self._backend, "text_length": len(text)},
        ))

        result = await self._synth_edge(text)

        _emit_background(JarvisEvent(type="tts.done"))
        return result

    async def synthesize_stream(
        self, text: str, emotion: str = "neutral"
    ) -> AsyncGenerator[bytes, None]:
        if not self.available or not text or not text.strip():
            return
        emotion = emotion if emotion in VALID_EMOTIONS else "neutral"
        async for chunk in self._synth_edge_stream(text):
            yield chunk

    def _build_communicate(self, edge_tts_module: Any, text: str) -> Any:
        """Prépare l'appel Edge : voix résolue et délais réseau bornés.

        La voix passe par `resolve_tts_voice("edge")` : un `TTS_VOICE` vide
        laisserait `edge_tts` choisir sa voix par défaut, qui est anglaise.
        """
        return edge_tts_module.Communicate(
            text,
            resolve_tts_voice("edge"),
            connect_timeout=config.EDGE_TTS_CONNECT_TIMEOUT_SEC,
            receive_timeout=config.EDGE_TTS_RECEIVE_TIMEOUT_SEC,
        )

    async def _edge_audio_chunks(self, text: str) -> AsyncGenerator[bytes, None]:
        """Octets MP3 renvoyés par Edge, sans filet : l'appelant classe l'échec.

        Chemin unique vers `edge_tts` — la version bufferisée et la version
        streamée partagent donc exactement les mêmes paramètres réseau.
        """
        import edge_tts

        communicate = self._build_communicate(edge_tts, text)
        async for message in communicate.stream():
            if message.get("type") != EDGE_AUDIO_MESSAGE_TYPE:
                continue
            data = message.get("data")
            if data:
                yield data

    def _log_edge_failure(self, stage: str, exc: BaseException) -> None:
        """Journalise selon la nature de l'échec : réseau absent ou contrat rompu."""
        detail = describe_tts_failure(exc)
        if is_network_unavailable(exc):
            logger.warning("[TTS] Edge %s — service injoignable : %s", stage, detail)
        else:
            logger.error("[TTS] Edge %s — défaut fonctionnel : %s", stage, detail, exc_info=exc)

    async def _synth_edge_stream(self, text: str) -> AsyncGenerator[bytes, None]:
        """Stream Edge TTS chunk par chunk (time-to-first-byte réduit).

        Pas de plafond global ici : le flux est consommé au fil de l'eau et
        annulable par le client. Les bornes `connect`/`receive` d'`edge_tts`
        couvrent le silence réseau.
        """
        try:
            async for chunk in self._edge_audio_chunks(text):
                yield chunk
        except ImportError:
            logger.warning("[TTS] Edge stream : edge-tts non installé")
        except Exception as exc:
            self._log_edge_failure("stream", exc)

    async def _synth_edge(self, text: str) -> bytes:
        """MP3 complet en mémoire — aucun fichier temporaire.

        Le texte synthétisé est une réponse personnelle de JARVIS : il n'a rien
        à faire dans `/tmp`. `edge_tts.Communicate.save()` ne fait de toute
        façon que concaténer les mêmes messages `audio` dans un fichier.
        """
        try:
            async with asyncio.timeout(config.EDGE_TTS_TOTAL_TIMEOUT_SEC):
                chunks = [chunk async for chunk in self._edge_audio_chunks(text)]
        except ImportError:
            logger.warning("[TTS] Edge : edge-tts non installé")
            return b""
        except Exception as exc:
            self._log_edge_failure("synthèse", exc)
            return b""

        data = b"".join(chunks)
        if not data:
            logger.error(
                "[TTS] Edge n'a renvoyé aucun audio (voix %s, %d caractères)",
                resolve_tts_voice("edge"),
                len(text),
            )
            return b""
        logger.debug("[TTS] Edge OK : %d octets", len(data))
        return data

    async def get_voices(self, locale_filter: str = "fr-FR") -> list[dict[str, Any]]:
        if self._backend != "edge":
            return []
        try:
            import edge_tts

            async with asyncio.timeout(config.EDGE_TTS_TOTAL_TIMEOUT_SEC):
                voices = await edge_tts.list_voices()
        except ImportError:
            logger.warning("[TTS] list_voices : edge-tts non installé")
            return []
        except Exception as exc:
            self._log_edge_failure("list_voices", exc)
            return []

        if locale_filter:
            voices = [v for v in voices if locale_filter in v.get("ShortName", "")]
        return [
            {"name": v.get("ShortName"), "gender": v.get("Gender"), "locale": v.get("Locale")}
            for v in voices
        ]


tts = TTSEngine()


class KokoroTTSEngine:
    """TTS local Kokoro — backend MLX-Audio (défaut) ou kokoro-onnx.

    ``KOKORO_BACKEND=mlx`` : sidecar ``native_audio/kokoro_synthesize`` dans
    ``JARVIS_VENV`` (mlx-audio + misaki + espeak-ng). Repli immédiat → macOS
    ``say`` (jamais Edge).

    ``KOKORO_BACKEND=onnx`` : modèle ONNX lazy-loadé au premier ``synthesize``.
    """

    SAMPLE_RATE = 24000
    MODEL_PATH = Path(__file__).resolve().parent.parent / "models" / "kokoro" / "kokoro-v0_19.onnx"
    VOICES_PATH = Path(__file__).resolve().parent.parent / "models" / "kokoro" / "voices.bin"

    def __init__(self) -> None:
        self._backend = (
            getattr(config, "KOKORO_BACKEND", config.DEFAULT_KOKORO_BACKEND)
            or config.DEFAULT_KOKORO_BACKEND
        ).strip().lower()
        self._voice = getattr(config, "KOKORO_VOICE", config.DEFAULT_KOKORO_VOICE)
        self._lang = getattr(config, "KOKORO_LANG", config.DEFAULT_KOKORO_LANG)
        self._lang_code = getattr(
            config, "KOKORO_LANG_CODE", config.DEFAULT_KOKORO_LANG_CODE
        )
        self._model = getattr(config, "KOKORO_MODEL", config.DEFAULT_KOKORO_MODEL)
        self._speed = float(
            getattr(config, "KOKORO_SPEED", config.DEFAULT_KOKORO_SPEED)
        )
        self._max_tokens = int(
            getattr(config, "KOKORO_MAX_TOKENS", config.DEFAULT_KOKORO_MAX_TOKENS)
        )
        self._kokoro: object | None = None
        self._load_failed = False
        self._worker: object | None = None
        self._worker_disabled = not bool(
            getattr(config, "KOKORO_WARM_WORKER", config.DEFAULT_KOKORO_WARM_WORKER)
        )
        self._first_chunk_max_tokens = int(getattr(
            config,
            "KOKORO_FIRST_CHUNK_MAX_TOKENS",
            config.DEFAULT_KOKORO_FIRST_CHUNK_MAX_TOKENS,
        ))
        self.available = self._probe_available()
        if self.available:
            logger.info(
                "[TTS] Kokoro prêt (lazy) — backend=%s voix=%s model=%s",
                self._backend,
                self._voice,
                self._model if self._backend == "mlx" else self.MODEL_PATH.name,
            )
        else:
            logger.warning(
                "[TTS] Kokoro INACTIF — backend=%s (mlx: JARVIS_VENV/mlx-audio ; "
                "onnx: models/kokoro/*.onnx)",
                self._backend,
            )

    def _probe_available(self) -> bool:
        if self._backend == "mlx":
            try:
                from native_audio.kokoro_bridge import is_kokoro_mlx_available

                return is_kokoro_mlx_available()
            except Exception as e:
                logger.debug("[TTS] probe Kokoro MLX : %s", e)
                return False
        return self.MODEL_PATH.exists() and self.VOICES_PATH.exists()

    def refresh_availability(self) -> bool:
        """Recalcule ``available`` (ex. après install mlx-audio)."""
        self.available = self._probe_available()
        return self.available

    def _ensure_loaded(self) -> bool:
        """Charge le modèle ONNX au premier appel. Retourne True si prêt."""
        if self._backend == "mlx":
            return self.refresh_availability()
        if self._kokoro is not None:
            return True
        if self._load_failed:
            return False
        try:
            from kokoro_onnx import Kokoro
            import time as _t

            t0 = _t.perf_counter()
            self._kokoro = Kokoro(str(self.MODEL_PATH), str(self.VOICES_PATH))
            elapsed = _t.perf_counter() - t0
            logger.info("[TTS] Kokoro ONNX chargé en %.2fs", elapsed)
            return True
        except ImportError:
            logger.error(
                "[TTS] kokoro-onnx non installé — pip install kokoro-onnx"
            )
        except Exception as e:
            logger.exception("[TTS] Kokoro chargement échoué : %s", e)
        self._load_failed = True
        self.available = False
        return False

    @staticmethod
    def _pcm_to_wav_bytes(samples, sample_rate: int) -> bytes:
        """Convertit un ndarray PCM float32 en bytes WAV complets (header + data)."""
        import io
        import soundfile as sf

        buf = io.BytesIO()
        sf.write(buf, samples, sample_rate, format="WAV", subtype="PCM_16")
        return buf.getvalue()

    def get_backend_name(self) -> str:
        return "kokoro"

    def get_fallback(self) -> "MacOSTTSEngine":
        """Retourne un moteur de secours local uniquement (pas Edge)."""
        if macos_tts.available:
            logger.warning(
                "[TTS] Kokoro fallback → macOS TTS (voix %s)",
                getattr(macos_tts, "_voice", "?"),
            )
            return macos_tts
        logger.error("[TTS] Kokoro fallback indisponible — aucun TTS local")
        return macos_tts

    async def _fallback_synthesize(self, text: str, emotion: str) -> bytes:
        """Repli macOS en WAV PCM (lisible par sounddevice), pas M4A web."""
        fb = self.get_fallback()
        native = getattr(fb, "synthesize_native", None)
        if callable(native):
            return await native(text, emotion)
        return await fb.synthesize(text, emotion)

    # ── Sidecar chaud (modèle chargé une seule fois) ────────────────────────

    def _get_worker(self) -> object | None:
        """Instancie le worker chaud à la demande. ``None`` si désactivé."""
        if self._worker_disabled or self._backend != "mlx":
            return None
        if self._worker is None:
            try:
                from native_audio.kokoro_bridge import KokoroWorker

                self._worker = KokoroWorker(
                    model=self._model,
                    voice=self._voice,
                    lang_code=self._lang_code,
                    speed=self._speed,
                    max_tokens=self._max_tokens,
                    first_chunk_max_tokens=self._first_chunk_max_tokens,
                )
            except Exception as e:
                logger.warning("[TTS] Kokoro worker chaud indisponible : %s", e)
                self._worker_disabled = True
                return None
        return self._worker

    async def warmup(self) -> bool:
        """Charge le modèle hors tour de parole. À appeler au démarrage."""
        worker = self._get_worker()
        if worker is None:
            return False
        try:
            started = await worker.start()  # type: ignore[attr-defined]
        except Exception as e:
            logger.warning("[TTS] Kokoro warmup : %s", e)
            return False
        if not started:
            logger.warning("[TTS] Kokoro chaud non démarré — repli one-shot par synthèse")
        return started

    async def shutdown(self) -> None:
        worker = self._worker
        self._worker = None
        if worker is not None:
            try:
                await worker.stop()  # type: ignore[attr-defined]
            except Exception as e:
                logger.debug("[TTS] arrêt worker Kokoro : %s", e)

    async def synthesize_stream_pcm(
        self, text: str, emotion: str = "neutral",
    ) -> AsyncGenerator[bytes, None]:
        """PCM16 24 kHz fragment par fragment, via le sidecar chaud.

        Ne produit rien si le sidecar chaud est indisponible : l'appelant doit
        alors passer par ``synthesize`` (WAV complet) plutôt que rester muet.
        """
        worker = self._get_worker()
        if worker is None:
            return
        async for chunk in worker.stream_pcm(text):  # type: ignore[attr-defined]
            if chunk:
                yield chunk

    async def _synthesize_mlx(self, text: str) -> bytes:
        # Chemin chaud d'abord : même processus, modèle déjà en mémoire.
        worker = self._get_worker()
        if worker is not None:
            chunks: list[bytes] = []
            try:
                async for chunk in worker.stream_pcm(text):  # type: ignore[attr-defined]
                    if chunk:
                        chunks.append(chunk)
            except Exception as e:
                logger.warning("[TTS] Kokoro chaud : %s — repli one-shot", e)
                chunks = []
            if chunks:
                from native_audio.kokoro_mlx import audio_to_wav_bytes
                import numpy as np

                pcm = b"".join(chunks)
                samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
                return audio_to_wav_bytes(samples, sample_rate=self.SAMPLE_RATE)

        from native_audio.kokoro_bridge import synthesize_bytes

        return await synthesize_bytes(
            text,
            model=self._model,
            voice=self._voice,
            lang_code=self._lang_code,
            speed=self._speed,
            max_tokens=self._max_tokens,
            audio_format="wav",
        )

    async def _synthesize_onnx(self, text: str) -> bytes:
        loop = asyncio.get_event_loop()
        samples, sr = await loop.run_in_executor(
            None,
            lambda: self._kokoro.create(
                text, voice=self._voice, speed=self._speed, lang=self._lang
            ),
        )
        return self._pcm_to_wav_bytes(samples, sr)

    async def synthesize(self, text: str, emotion: str = "neutral") -> bytes:
        if not text or not text.strip():
            return b""
        if not self._ensure_loaded():
            return await self._fallback_synthesize(text, emotion)

        _emit_background(JarvisEvent(
            type="tts.start",
            data={
                "engine": "kokoro",
                "backend": self._backend,
                "text_length": len(text),
            },
        ))

        try:
            if self._backend == "mlx":
                wav = await self._synthesize_mlx(text)
            else:
                wav = await self._synthesize_onnx(text)
            if not wav:
                logger.warning(
                    "[TTS] Kokoro %s sortie vide — fallback macOS (%s)",
                    self._backend,
                    getattr(macos_tts, "_voice", "?"),
                )
                return await self._fallback_synthesize(text, emotion)
            logger.info(
                "[TTS] Kokoro OK backend=%s voice=%s lang=%s bytes=%d",
                self._backend,
                self._voice,
                self._lang_code if self._backend == "mlx" else self._lang,
                len(wav),
            )
            _emit_background(JarvisEvent(type="tts.done"))
            return wav
        except Exception as e:
            logger.exception("[TTS] Kokoro synthesize erreur : %s", e)
            return await self._fallback_synthesize(text, emotion)

    async def synthesize_native(self, text: str, emotion: str = "neutral") -> bytes:
        """WAV PCM pour la sortie locale (daemon) — même payload que ``synthesize``."""
        return await self.synthesize(text, emotion)

    async def synthesize_stream(
        self, text: str, emotion: str = "neutral"
    ) -> AsyncGenerator[bytes, None]:
        if not text or not text.strip():
            return
        if self._backend == "mlx":
            data = await self.synthesize(text, emotion)
            if data:
                yield data
            return
        if not self._ensure_loaded():
            async for chunk in self.get_fallback().synthesize_stream(text, emotion):
                yield chunk
            return
        try:
            async for samples, sr in self._kokoro.create_stream(
                text, voice=self._voice, speed=self._speed, lang=self._lang
            ):
                wav = self._pcm_to_wav_bytes(samples, sr)
                if wav:
                    yield wav
        except Exception as e:
            logger.exception("[TTS] Kokoro stream erreur : %s — fallback", e)
            data = await self.get_fallback().synthesize(text, emotion)
            if data:
                yield data


kokoro_tts = KokoroTTSEngine()


class MacOSTTSEngine:
    """TTS natif macOS : `say` génère un AIFF, `afconvert` le compresse en AAC/M4A.

    Aucune dépendance réseau. Fonctionne hors-ligne. La voix par défaut est
    ``MACOS_TTS_VOICE`` (config/.env, défaut : "Jacques"). Le fichier M4A est
    lisible par tous les navigateurs modernes (Chrome, Firefox, Safari).
    """

    def __init__(self) -> None:
        self._voice = getattr(config, "MACOS_TTS_VOICE", "Jacques")
        self.available = bool(shutil.which("say") and shutil.which("afconvert"))
        if self.available:
            logger.info("[TTS] Backend macOS : say + afconvert (voix %s)", self._voice)
        else:
            logger.warning(
                "[TTS] MacOS TTS indisponible — commandes 'say' ou 'afconvert' introuvables"
            )

    def get_backend_name(self) -> str:
        return "macos"

    async def synthesize(self, text: str, emotion: str = "neutral") -> bytes:
        """Synthétise `text` en M4A (AAC) via say + afconvert."""
        if not self.available or not (text and text.strip()):
            return b""

        _emit_background(JarvisEvent(
            type="tts.start",
            data={"engine": "macos", "text_length": len(text)},
        ))

        result = await self._synth_macos(text)

        _emit_background(JarvisEvent(type="tts.done"))
        return result

    async def synthesize_stream(
        self, text: str, emotion: str = "neutral"
    ) -> AsyncGenerator[bytes, None]:
        """Stream compatible : retourne le fichier M4A complet en un seul chunk."""
        data = await self.synthesize(text, emotion)
        if data:
            yield data

    async def synthesize_native(self, text: str, emotion: str = "neutral") -> bytes:
        """Produit un WAV PCM pour la sortie locale sounddevice/CoreAudio.

        Le chemin web historique conserve le M4A. Sur les versions récentes
        de macOS, l'encodeur AAC d'afconvert peut être absent alors que la
        conversion PCM WAVE reste disponible.
        """
        if not self.available or not (text and text.strip()):
            return b""
        with tempfile.TemporaryDirectory(prefix="jarvis_tts_native_") as tmpdir:
            aiff_path = os.path.join(tmpdir, "out.aiff")
            wav_path = os.path.join(tmpdir, "out.wav")
            try:
                say_proc = await asyncio.create_subprocess_exec(
                    "say", "-v", self._voice, "-o", aiff_path, text,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                if await say_proc.wait() != 0 or not os.path.exists(aiff_path):
                    return b""

                afc_proc = await asyncio.create_subprocess_exec(
                    "afconvert", "-f", "WAVE", "-d", "LEI16", aiff_path, wav_path,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                if await afc_proc.wait() != 0 or not os.path.exists(wav_path):
                    return b""
                return Path(wav_path).read_bytes()
            except Exception as e:
                logger.exception("[TTS] macOS natif erreur : %s", e)
                return b""

    async def _synth_macos(self, text: str) -> bytes:
        with tempfile.TemporaryDirectory(prefix="jarvis_tts_") as tmpdir:
            aiff_path = os.path.join(tmpdir, "out.aiff")
            m4a_path = os.path.join(tmpdir, "out.m4a")
            try:
                # Génère l'AIFF via la commande `say`
                say_proc = await asyncio.create_subprocess_exec(
                    "say", "-v", self._voice, "-o", aiff_path, text,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await say_proc.wait()

                if not os.path.exists(aiff_path) or os.path.getsize(aiff_path) == 0:
                    logger.error("[TTS] macOS : say n'a pas produit de fichier AIFF")
                    return b""

                # Convertit AIFF → M4A (AAC) via afconvert
                afc_proc = await asyncio.create_subprocess_exec(
                    "afconvert", "-f", "m4af", "-d", "aac", aiff_path, m4a_path,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await afc_proc.wait()

                if not os.path.exists(m4a_path):
                    logger.error("[TTS] macOS : afconvert n'a pas produit de fichier M4A")
                    return b""

                data = Path(m4a_path).read_bytes()
                logger.debug("[TTS] macOS OK : %d bytes", len(data))
                return data

            except Exception as e:
                logger.exception("[TTS] macOS erreur : %s", e)
                return b""

    async def get_voices(self) -> list[dict]:
        """Liste les voix disponibles via `say -v ?`."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "say", "-v", "?",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await proc.communicate()
            voices = []
            for line in (stdout or b"").decode("utf-8", errors="replace").splitlines():
                parts = line.split()
                if parts:
                    voices.append({"name": parts[0], "locale": parts[1] if len(parts) > 1 else "?"})
            return voices
        except Exception as e:
            logger.error("[TTS] macOS list_voices : %s", e)
            return []


macos_tts = MacOSTTSEngine()

TTS_ENGINE_NAMES = frozenset({"edge", "macos", "kokoro", "ttskit"})


def resolve_tts_engine_name() -> str:
    """Source de vérité TTS : DB ``tts_engine`` puis ``config.TTS_ENGINE``.

    Évite qu'un vieux ``TTS_ENGINE=kokoro`` dans l'environnement du process
    ou un réglage UI ``macos`` ignore le choix Edge / Henri du ``.env.config``.
    """
    try:
        from database import get_setting

        db_engine = (get_setting("tts_engine", "") or "").strip().lower()
    except Exception:
        db_engine = ""
    if db_engine in TTS_ENGINE_NAMES:
        return db_engine
    configured = (getattr(config, "TTS_ENGINE", "") or config.DEFAULT_TTS_ENGINE).strip().lower()
    if configured in TTS_ENGINE_NAMES:
        return configured
    return config.DEFAULT_TTS_ENGINE


def resolve_tts_voice(engine_name: str | None = None) -> str:
    """Voix annoncée / utilisée selon le moteur (Henri Edge, Thomas macOS, …)."""
    name = (engine_name or resolve_tts_engine_name()).strip().lower()
    if name == "edge":
        return getattr(config, "TTS_VOICE", "fr-FR-HenriNeural") or "fr-FR-HenriNeural"
    if name == "macos":
        return getattr(config, "MACOS_TTS_VOICE", "Jacques") or "Jacques"
    if name == "kokoro":
        return getattr(config, "KOKORO_VOICE", config.DEFAULT_KOKORO_VOICE) or config.DEFAULT_KOKORO_VOICE
    return getattr(config, "TTS_VOICE", "") or ""


def get_tts_by_name(name: str) -> TTSEngine | MacOSTTSEngine | KokoroTTSEngine:
    """Retourne le singleton correspondant au nom de moteur.

    - ``kokoro`` : toujours le moteur Kokoro (repli interne local → macOS ``say``, jamais Edge).
    - ``macos`` / ``edge`` / ``ttskit`` : moteur demandé explicitement.
    - nom inconnu : Edge uniquement pour compatibilité des appels historiques explicites.

    Pour le pipeline natif macOS, utiliser ``audio.tts_native.get_native_tts_engine``.
    """
    normalized = (name or "").strip().lower()
    if normalized == "ttskit":
        from audio.tts_native import ttskit_tts

        if ttskit_tts.preload_sync():
            return ttskit_tts  # type: ignore[return-value]
    if normalized == "kokoro":
        return kokoro_tts
    if normalized == "macos":
        return macos_tts
    if normalized == "edge":
        return tts
    return tts
