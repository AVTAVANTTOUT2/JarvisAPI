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

    async def transcribe_with_diarization(
        self, audio_bytes: bytes, language: str = "fr", timeout: float | None = None
    ) -> list[dict]:
        """Transcrit avec segmentation par locuteur (paramètre `diarize` de Scribe).

        Retourne une liste de tours de parole triés chronologiquement :
        ``[{"speaker_label": "A", "text": "...", "start_ms": int, "end_ms": int}, ...]``.
        Les identifiants (« A », « B »…) sont propres à CET appel — Scribe ne
        fournit pas d'empreinte vocale persistante d'un enregistrement à
        l'autre, seulement une distinction entre locuteurs dans un même flux.

        Note : n'a pas pu être vérifié contre l'API ElevenLabs réelle en
        développement (pas de clé de test) — dégrade silencieusement vers un
        tour unique si la réponse ne contient pas la structure `words`
        attendue (`speaker_id` par mot), pour ne jamais planter au premier
        format de réponse imprévu.
        """
        if not self.available:
            logger.error("[STT] ElevenLabs non configuré")
            return []
        if len(audio_bytes) < MIN_AUDIO_BYTES:
            logger.warning("[STT] Audio trop court (%d bytes) — ignoré", len(audio_bytes))
            return []

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
                    "diarize": "true",
                },
                timeout=client_timeout,
            )
            response.raise_for_status()
            result = response.json()
        except httpx.HTTPStatusError as e:
            logger.error("[STT] Scribe diarize HTTP %d : %s", e.response.status_code, e.response.text[:200])
            return []
        except httpx.TimeoutException:
            logger.error("[STT] Scribe diarize timeout (%.0fs)", client_timeout)
            return []
        except Exception as e:
            logger.error("[STT] Scribe diarize erreur : %s", e)
            return []

        return group_words_into_turns(result)


def group_words_into_turns(scribe_result: dict) -> list[dict]:
    """Regroupe la liste `words` (un mot = un `speaker_id`) en tours de parole consécutifs.

    Fonction pure — testable sans appel réseau. Dégrade vers un tour unique
    « A » si `words`/`speaker_id` sont absents (réponse non diarisée ou
    format inattendu).
    """
    words = scribe_result.get("words")
    if not isinstance(words, list) or not words:
        text = (scribe_result.get("text") or "").strip()
        return [{"speaker_label": "A", "text": text, "start_ms": None, "end_ms": None}] if text else []

    turns: list[dict] = []
    current_speaker: str | None = None
    current_words: list[str] = []
    current_start: float | None = None
    current_end: float | None = None
    speaker_ids_seen: dict[str, str] = {}  # "speaker_0" -> "A"
    next_letter = ord("A")

    def _label_for(raw_id: str) -> str:
        nonlocal next_letter
        if raw_id not in speaker_ids_seen:
            speaker_ids_seen[raw_id] = chr(next_letter)
            next_letter += 1
        return speaker_ids_seen[raw_id]

    def _flush():
        if current_words and current_speaker is not None:
            turns.append({
                "speaker_label": current_speaker,
                "text": " ".join(current_words).strip(),
                "start_ms": int(current_start * 1000) if current_start is not None else None,
                "end_ms": int(current_end * 1000) if current_end is not None else None,
            })

    for w in words:
        if not isinstance(w, dict) or w.get("type") == "spacing":
            continue
        raw_speaker = w.get("speaker_id") or "speaker_0"
        label = _label_for(raw_speaker)
        text = str(w.get("text") or "").strip()
        if not text:
            continue
        if label != current_speaker:
            _flush()
            current_speaker = label
            current_words = []
            current_start = w.get("start")
        current_words.append(text)
        current_end = w.get("end")
    _flush()

    return turns


stt = STT()
