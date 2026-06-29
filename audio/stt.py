"""Speech-to-Text via ElevenLabs Scribe — accepte directement le WebM/Opus du navigateur.

Zéro conversion, zéro ffmpeg, zéro fichier temporaire.
Scribe ingère les formats web natifs (WebM, Opus, MP3, WAV, OGG…).
"""

import logging
import re

import httpx

import config

logger = logging.getLogger(__name__)

MIN_AUDIO_BYTES = 1000
_AUDIO_EVENT_TAG_RE = re.compile(r"^\s*\([^)]+\)\s*")


class STT:
    def __init__(self):
        self.api_key = getattr(config, "ELEVENLABS_API_KEY", "") or ""
        self.available = bool(self.api_key)
        self.last_raw_text: str = ""
        self.last_clean_text: str = ""
        if self.available:
            logger.info("[STT] ElevenLabs Scribe initialisé")
        else:
            logger.warning("[STT] Pas de ELEVENLABS_API_KEY — STT indisponible")

    async def transcribe(self, audio_bytes: bytes, language: str = "fr", timeout: float | None = None) -> str:
        """Transcrit des bytes audio (WebM, Opus, MP3, WAV…) via ElevenLabs Scribe."""
        if not self.available:
            logger.error("[STT] ElevenLabs non configuré")
            return ""

        if len(audio_bytes) < MIN_AUDIO_BYTES:
            logger.warning("[STT] Audio trop court (%d bytes) — ignoré", len(audio_bytes))
            return ""

        client_timeout = timeout if timeout is not None else 30.0
        try:
            async with httpx.AsyncClient(timeout=client_timeout) as client:
                response = await client.post(
                    "https://api.elevenlabs.io/v1/speech-to-text",
                    headers={"xi-api-key": self.api_key},
                    files={"file": ("audio.wav", audio_bytes, "audio/wav")},
                    data={
                        "model_id": "scribe_v2",
                        "language_code": language,
                        # Evite les sorties "(musique)", "(rire)", etc.
                        "tag_audio_events": "false",
                    },
                )
                response.raise_for_status()
                result = response.json()

            raw_text = (result.get("text") or "").strip()
            self.last_raw_text = raw_text
            text = raw_text
            # Nettoie un éventuel tag audio résiduel en début de phrase.
            text = _AUDIO_EVENT_TAG_RE.sub("", text).strip()
            self.last_clean_text = text

            if not text or len(text) < 2:
                logger.info("[STT] Aucun texte reconnu")
                return ""

            logger.info('[STT] Scribe : "%s" — lang=%s', text[:80], language)
            return text

        except httpx.HTTPStatusError as e:
            logger.error(
                "[STT] Scribe HTTP %d : %s",
                e.response.status_code,
                e.response.text[:200],
            )
            return ""
        except httpx.TimeoutException:
            logger.error("[STT] Scribe timeout (%.0fs)", client_timeout)
            return ""
        except Exception as e:
            logger.error("[STT] Scribe erreur : %s", e)
            return ""


stt = STT()
