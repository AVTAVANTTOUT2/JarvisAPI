"""Speech-to-Text via ElevenLabs Scribe — accepte directement le WebM/Opus du navigateur.

Zéro conversion, zéro ffmpeg, zéro fichier temporaire.
Scribe ingère les formats web natifs (WebM, Opus, MP3, WAV, OGG…).
"""

import logging
import re

import httpx

import config
from audio.audio_format import detect_upload_format, prepare_stt_bytes

logger = logging.getLogger(__name__)

MIN_AUDIO_BYTES = 1000
_AUDIO_EVENT_TAG_RE = re.compile(r"^\s*\([^)]+\)\s*")

_shared_http_client: httpx.AsyncClient | None = None


def _get_http_client(timeout: float) -> httpx.AsyncClient:
    """Client httpx réutilisé (pool TCP/TLS) ; timeout par requête via ``request``."""
    global _shared_http_client
    if _shared_http_client is None or _shared_http_client.is_closed:
        _shared_http_client = httpx.AsyncClient(timeout=timeout)
    return _shared_http_client


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
        """Transcrit des bytes audio (WebM, Opus, MP3, WAV, PCM…) via ElevenLabs Scribe."""
        if not self.available:
            logger.error("[STT] ElevenLabs non configuré")
            return ""

        if len(audio_bytes) < MIN_AUDIO_BYTES:
            logger.warning("[STT] Audio trop court (%d bytes) — ignoré", len(audio_bytes))
            return ""

        client_timeout = timeout if timeout is not None else 30.0
        payload = prepare_stt_bytes(audio_bytes)
        filename, mime = detect_upload_format(payload)

        try:
            client = _get_http_client(client_timeout)
            response = await client.post(
                "https://api.elevenlabs.io/v1/speech-to-text",
                headers={"xi-api-key": self.api_key},
                files={"file": (filename, payload, mime)},
                data={
                    "model_id": "scribe_v2",
                    "language_code": language,
                    "tag_audio_events": "false",
                },
                timeout=client_timeout,
            )
            response.raise_for_status()
            result = response.json()

            raw_text = (result.get("text") or "").strip()
            self.last_raw_text = raw_text
            text = raw_text
            text = _AUDIO_EVENT_TAG_RE.sub("", text).strip()
            self.last_clean_text = text

            if not text or len(text) < 2:
                logger.info("[STT] Aucun texte reconnu")
                return ""

            logger.info('[STT] Scribe : "%s" — lang=%s mime=%s', text[:80], language, mime)
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
