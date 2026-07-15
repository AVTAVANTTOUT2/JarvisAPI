"""Détection de format audio et conversion PCM → WAV (partagé STT / daemons)."""

from __future__ import annotations

import io
import struct
import wave

WEBM_MAGIC = b"\x1a\x45\xdf\xa3"
RIFF_MAGIC = b"RIFF"
ID3_MAGIC = b"ID3"
OGG_MAGIC = b"OggS"


def detect_upload_format(audio_bytes: bytes) -> tuple[str, str]:
    """Retourne ``(filename, mime)`` pour identifier un conteneur audio."""
    if len(audio_bytes) >= 4 and audio_bytes[:4] == RIFF_MAGIC:
        return "audio.wav", "audio/wav"
    if len(audio_bytes) >= 4 and audio_bytes[:4] == WEBM_MAGIC:
        return "audio.webm", "audio/webm"
    if len(audio_bytes) >= 3 and audio_bytes[:3] == ID3_MAGIC:
        return "audio.mp3", "audio/mpeg"
    if len(audio_bytes) >= 2 and audio_bytes[0] == 0xFF and (audio_bytes[1] & 0xE0) == 0xE0:
        return "audio.mp3", "audio/mpeg"
    if len(audio_bytes) >= 4 and audio_bytes[:4] == OGG_MAGIC:
        return "audio.ogg", "audio/ogg"
    # PCM brut 16-bit mono (pas de header) — typique wake word jarvis_daemon
    if len(audio_bytes) >= 2 and len(audio_bytes) % 2 == 0:
        try:
            samples = struct.unpack(f"{min(8, len(audio_bytes) // 2)}h", audio_bytes[:16])
            if all(-32768 <= s <= 32767 for s in samples):
                return "audio.wav", "audio/wav"
        except struct.error:
            pass
    return "audio.webm", "audio/webm"


def pcm_to_wav(
    pcm_bytes: bytes,
    *,
    sample_rate: int = 16000,
    channels: int = 1,
    sample_width: int = 2,
) -> bytes:
    """Encapsule du PCM 16-bit en WAV."""
    buf = io.BytesIO()
    with wave.open(buf, "w") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(sample_width)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_bytes)
    return buf.getvalue()


def prepare_stt_bytes(audio_bytes: bytes, sample_rate: int = 16000) -> bytes:
    """Normalise les bytes pour le STT : WAV si PCM brut, sinon inchangé."""
    filename, _mime = detect_upload_format(audio_bytes)
    if filename == "audio.wav" and audio_bytes[:4] != RIFF_MAGIC:
        return pcm_to_wav(audio_bytes, sample_rate=sample_rate)
    return audio_bytes


def playback_file_extension(audio_bytes: bytes) -> str:
    """Extension fichier temporaire pour ``afplay`` selon le format."""
    if len(audio_bytes) >= 4 and audio_bytes[:4] == RIFF_MAGIC:
        return ".wav"
    if len(audio_bytes) >= 3 and audio_bytes[:3] == ID3_MAGIC:
        return ".mp3"
    if len(audio_bytes) >= 2 and audio_bytes[0] == 0xFF and (audio_bytes[1] & 0xE0) == 0xE0:
        return ".mp3"
    # M4A / AAC (macOS say + afconvert)
    if len(audio_bytes) >= 8 and audio_bytes[4:8] == b"ftyp":
        return ".m4a"
    return ".m4a"


def tts_audio_mime(engine_name: str) -> str:
    """MIME à annoncer au client WebSocket selon le moteur TTS."""
    name = (engine_name or "edge").lower().strip()
    if name == "kokoro":
        return "audio/wav"
    if name == "macos":
        return "audio/mp4"
    return "audio/mpeg"
