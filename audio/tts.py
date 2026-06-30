"""Text-to-Speech — ElevenLabs (qualité / latence réseau), Edge TTS (défaut, rapide)
ou macOS natif (say + afconvert, zéro latence réseau, M4 local).

Backends disponibles (``TTS_ENGINE`` dans `.env` ou DB `app_settings`) :
  - ``edge``       — Microsoft Edge Neural (gratuit, faible latence réseau)
  - ``elevenlabs`` — si ``ELEVENLABS_API_KEY`` + ``ELEVENLABS_VOICE_ID`` sont définis
  - ``macos``      — say + afconvert (macOS natif, zéro réseau, sort en AAC/M4A)

API :
    tts.synthesize(text, emotion)        → bytes audio
    tts.synthesize_stream(text, emotion) → AsyncGenerator[bytes]
    tts.get_backend_name()               → str

    macos_tts                            → singleton MacOSTTSEngine
    get_tts_by_name(name)                → retourne le bon singleton selon le nom
"""

import asyncio
import logging
import os
import shutil
import tempfile
from pathlib import Path
from typing import AsyncGenerator

import config
from jarvis.event_bus import JarvisEvent, event_bus

logger = logging.getLogger(__name__)

VALID_EMOTIONS = frozenset({
    "neutral", "warm", "serious", "concerned", "amused", "urgent", "encouraging"
})

ELEVENLABS_EMOTION_SETTINGS: dict[str, dict] = {
    "neutral":     {"stability": 0.45, "similarity_boost": 0.75, "style": 0.15, "use_speaker_boost": True},
    "warm":        {"stability": 0.40, "similarity_boost": 0.75, "style": 0.40, "use_speaker_boost": True},
    "serious":     {"stability": 0.70, "similarity_boost": 0.80, "style": 0.10, "use_speaker_boost": True},
    "concerned":   {"stability": 0.40, "similarity_boost": 0.75, "style": 0.50, "use_speaker_boost": True},
    "amused":      {"stability": 0.30, "similarity_boost": 0.70, "style": 0.70, "use_speaker_boost": True},
    "urgent":      {"stability": 0.60, "similarity_boost": 0.80, "style": 0.50, "use_speaker_boost": True},
    "encouraging": {"stability": 0.40, "similarity_boost": 0.75, "style": 0.50, "use_speaker_boost": True},
}


class TTSEngine:
    """TTS ElevenLabs (option) ou Edge TTS (défaut)."""

    def __init__(self) -> None:
        self._backend: str = "none"
        self.available: bool = False
        pref = (getattr(config, "TTS_ENGINE", "edge") or "edge").lower().strip()

        if pref == "elevenlabs" and getattr(config, "ELEVENLABS_API_KEY", "") and getattr(
            config, "ELEVENLABS_VOICE_ID", ""
        ):
            self._backend = "elevenlabs"
            self.available = True
            logger.info(
                "[TTS] Backend : ElevenLabs eleven_multilingual_v2 (voice_id=%s)",
                config.ELEVENLABS_VOICE_ID,
            )
            return

        if pref == "elevenlabs":
            logger.warning(
                "[TTS] TTS_ENGINE=elevenlabs mais ELEVENLABS_API_KEY ou "
                "ELEVENLABS_VOICE_ID manquant — passage sur Edge TTS."
            )

        try:
            import edge_tts  # noqa: F401

            self._backend = "edge"
            self.available = True
            logger.info("[TTS] Backend : Edge TTS (voix %s)", config.TTS_VOICE)
        except ImportError:
            logger.warning(
                "[TTS] edge-tts non installé (`pip install edge-tts`). "
                "Configure ElevenLabs dans .env pour du TTS."
            )

    def get_backend_name(self) -> str:
        return self._backend

    async def synthesize(self, text: str, emotion: str = "neutral") -> bytes:
        if not self.available or not text or not text.strip():
            return b""
        emotion = emotion if emotion in VALID_EMOTIONS else "neutral"
        logger.debug("[TTS] synthesize backend=%s emotion=%s len=%d", self._backend, emotion, len(text))

        asyncio.create_task(event_bus.emit(JarvisEvent(
            type="tts.start",
            data={"engine": self._backend, "text_length": len(text)},
        )))

        if self._backend == "elevenlabs":
            result = await self._synth_elevenlabs_full(text, emotion)
        else:
            result = await self._synth_edge(text)

        asyncio.create_task(event_bus.emit(JarvisEvent(type="tts.done")))
        return result

    async def synthesize_stream(
        self, text: str, emotion: str = "neutral"
    ) -> AsyncGenerator[bytes, None]:
        if not self.available or not text or not text.strip():
            return
        emotion = emotion if emotion in VALID_EMOTIONS else "neutral"
        if self._backend == "elevenlabs":
            async for chunk in self._synth_elevenlabs_stream(text, emotion):
                yield chunk
        else:
            data = await self.synthesize(text, emotion)
            if data:
                yield data

    def _elevenlabs_payload(self, text: str, emotion: str) -> dict:
        settings = ELEVENLABS_EMOTION_SETTINGS.get(
            emotion, ELEVENLABS_EMOTION_SETTINGS["neutral"]
        )
        return {"text": text, "model_id": "eleven_multilingual_v2", "voice_settings": settings}

    def _elevenlabs_headers(self) -> dict:
        return {"xi-api-key": config.ELEVENLABS_API_KEY, "Content-Type": "application/json"}

    async def _synth_elevenlabs_stream(
        self, text: str, emotion: str
    ) -> AsyncGenerator[bytes, None]:
        try:
            import httpx
        except ImportError:
            logger.error("[TTS] httpx requis pour ElevenLabs")
            return

        url = f"https://api.elevenlabs.io/v1/text-to-speech/{config.ELEVENLABS_VOICE_ID}/stream"
        params = {"output_format": "mp3_44100_128"}
        total = 0
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                async with client.stream(
                    "POST",
                    url,
                    json=self._elevenlabs_payload(text, emotion),
                    headers=self._elevenlabs_headers(),
                    params=params,
                ) as r:
                    if r.status_code != 200:
                        body = await r.aread()
                        logger.error("[TTS] ElevenLabs stream HTTP %d : %s", r.status_code, body[:200])
                        return
                    async for chunk in r.aiter_bytes(chunk_size=4096):
                        if chunk:
                            total += len(chunk)
                            yield chunk
            logger.info("[TTS] ElevenLabs stream OK : %d bytes emotion=%s", total, emotion)
        except Exception as e:
            logger.exception("[TTS] ElevenLabs stream : %s", e)

    async def _synth_elevenlabs_full(self, text: str, emotion: str) -> bytes:
        parts: list[bytes] = []
        async for chunk in self._synth_elevenlabs_stream(text, emotion):
            parts.append(chunk)
        return b"".join(parts)

    async def _synth_edge(self, text: str) -> bytes:
        try:
            import edge_tts
        except ImportError:
            return b""

        tmp_fd, tmp_path = tempfile.mkstemp(suffix=".mp3")
        os.close(tmp_fd)
        try:
            communicate = edge_tts.Communicate(text, config.TTS_VOICE)
            await communicate.save(tmp_path)
            data = Path(tmp_path).read_bytes()
            logger.debug("[TTS] Edge OK : %d bytes", len(data))
            return data
        except Exception as e:
            logger.exception("[TTS] Edge : %s", e)
            return b""
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    async def get_voices(self, locale_filter: str = "fr-FR") -> list:
        if self._backend != "edge":
            return []
        try:
            import edge_tts

            voices = await edge_tts.list_voices()
            if locale_filter:
                voices = [v for v in voices if locale_filter in v.get("ShortName", "")]
            return [
                {"name": v.get("ShortName"), "gender": v.get("Gender"), "locale": v.get("Locale")}
                for v in voices
            ]
        except Exception as e:
            logger.error("[TTS] list_voices : %s", e)
            return []


tts = TTSEngine()


class KokoroTTSEngine:
    """TTS local via kokoro-onnx (ONNX Runtime, ~24 kHz PCM → WAV bytes).

    Lazy-loading : le modèle n'est chargé qu'au premier appel ``synthesize``.
    Si le chargement échoue (fichiers manquants, erreur ONNX, dépendance absente),
    le moteur se désactive et ``get_fallback()`` retourne un moteur de secours.
    """

    SAMPLE_RATE = 24000
    MODEL_PATH = Path(__file__).resolve().parent.parent / "models" / "kokoro" / "kokoro-v0_19.onnx"
    VOICES_PATH = Path(__file__).resolve().parent.parent / "models" / "kokoro" / "voices.bin"

    def __init__(self) -> None:
        self._voice = getattr(config, "KOKORO_VOICE", "af_nicole")
        self._lang = getattr(config, "KOKORO_LANG", "fr-fr")
        self._kokoro: object | None = None
        self._load_failed = False
        self.available = self.MODEL_PATH.exists() and self.VOICES_PATH.exists()
        if self.available:
            logger.info(
                "[TTS] Kokoro pret (lazy) — modele=%s voix=%s",
                self.MODEL_PATH.name, self._voice,
            )
        else:
            logger.warning(
                "[TTS] Kokoro INACTIF — fichiers manquants : modele=%s voices=%s",
                self.MODEL_PATH.exists(), self.VOICES_PATH.exists(),
            )

    def _ensure_loaded(self) -> bool:
        """Charge le modèle ONNX au premier appel. Retourne True si prêt."""
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
            logger.info("[TTS] Kokoro charge en %.2fs", elapsed)
            return True
        except ImportError:
            logger.error(
                "[TTS] kokoro-onnx non installe — pip install kokoro-onnx"
            )
        except Exception as e:
            logger.exception("[TTS] Kokoro chargement echoue : %s", e)
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

    def get_fallback(self) -> "TTSEngine | MacOSTTSEngine":
        """Retourne un moteur de secours si Kokoro est inopérant."""
        if macos_tts.available:
            logger.warning("[TTS] Kokoro fallback → macOS TTS")
            return macos_tts
        logger.warning("[TTS] Kokoro fallback → Edge TTS")
        return tts

    async def synthesize(self, text: str, emotion: str = "neutral") -> bytes:
        if not text or not text.strip():
            return b""
        if not self._ensure_loaded():
            return await self.get_fallback().synthesize(text, emotion)

        asyncio.create_task(event_bus.emit(JarvisEvent(
            type="tts.start",
            data={"engine": "kokoro", "text_length": len(text)},
        )))

        try:
            loop = asyncio.get_event_loop()
            samples, sr = await loop.run_in_executor(
                None, lambda: self._kokoro.create(text, voice=self._voice, speed=1.0, lang=self._lang)
            )
            wav = self._pcm_to_wav_bytes(samples, sr)
            logger.debug(
                "[TTS] Kokoro OK : %d bytes WAV, %.1fs audio",
                len(wav), len(samples) / sr,
            )
            asyncio.create_task(event_bus.emit(JarvisEvent(type="tts.done")))
            return wav
        except Exception as e:
            logger.exception("[TTS] Kokoro synthesize erreur : %s", e)
            return await self.get_fallback().synthesize(text, emotion)

    async def synthesize_stream(
        self, text: str, emotion: str = "neutral"
    ) -> AsyncGenerator[bytes, None]:
        if not text or not text.strip():
            return
        if not self._ensure_loaded():
            async for chunk in self.get_fallback().synthesize_stream(text, emotion):
                yield chunk
            return
        try:
            async for samples, sr in self._kokoro.create_stream(
                text, voice=self._voice, speed=1.0, lang=self._lang
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
    ``MACOS_TTS_VOICE`` (config/.env, défaut : "Thomas"). Le fichier M4A est
    lisible par tous les navigateurs modernes (Chrome, Firefox, Safari).
    """

    def __init__(self) -> None:
        self._voice = getattr(config, "MACOS_TTS_VOICE", "Thomas")
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

        asyncio.create_task(event_bus.emit(JarvisEvent(
            type="tts.start",
            data={"engine": "macos", "text_length": len(text)},
        )))

        result = await self._synth_macos(text)

        asyncio.create_task(event_bus.emit(JarvisEvent(type="tts.done")))
        return result

    async def synthesize_stream(
        self, text: str, emotion: str = "neutral"
    ) -> AsyncGenerator[bytes, None]:
        """Stream compatible : retourne le fichier M4A complet en un seul chunk."""
        data = await self.synthesize(text, emotion)
        if data:
            yield data

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

TTS_ENGINE_NAMES = frozenset({"edge", "elevenlabs", "macos", "kokoro"})


def get_tts_by_name(name: str) -> TTSEngine | MacOSTTSEngine | KokoroTTSEngine:
    """Retourne le singleton correspondant au nom de moteur.

    Fallback sur `tts` (Edge par défaut) si le nom est inconnu ou non disponible.
    """
    if name == "kokoro" and kokoro_tts.available:
        return kokoro_tts
    if name == "macos" and macos_tts.available:
        return macos_tts
    return tts
