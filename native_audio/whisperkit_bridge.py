"""Bridge optionnel WhisperKit (sidecar natif macOS) — sans téléchargement implicite."""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

_SIDECAR_DIR = Path(__file__).resolve().parent
_DEFAULT_BINARY = _SIDECAR_DIR / "whisperkit_transcribe"


def whisperkit_binary() -> Path | None:
    """Retourne le binaire sidecar s'il est compilé et exécutable."""
    env_path = shutil.which("jarvis-whisperkit")
    if env_path:
        return Path(env_path)
    if _DEFAULT_BINARY.is_file() and os.access(_DEFAULT_BINARY, os.X_OK):
        return _DEFAULT_BINARY
    return None


def is_whisperkit_available() -> bool:
    return whisperkit_binary() is not None


def transcribe_pcm_file(
    wav_path: Path,
    *,
    model: str,
    language: str = "fr",
    initial_prompt: str = "",
) -> dict | None:
    """Appelle le sidecar WhisperKit. Retourne {text, segments} ou None."""
    binary = whisperkit_binary()
    if binary is None:
        logger.error(
            "[whisperkit] Sidecar absent — compilez native_audio/whisperkit_transcribe "
            "ou installez jarvis-whisperkit dans le PATH"
        )
        return None

    cmd = [
        str(binary),
        "--input", str(wav_path),
        "--model", model,
        "--language", language,
    ]
    if initial_prompt:
        cmd.extend(["--prompt", initial_prompt])

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except subprocess.TimeoutExpired:
        logger.error("[whisperkit] Timeout transcription (>120s)")
        return None
    except OSError as e:
        logger.error("[whisperkit] Exécution sidecar impossible : %s", e)
        return None

    if proc.returncode != 0:
        logger.error(
            "[whisperkit] Sidecar code=%s stderr=%s",
            proc.returncode,
            (proc.stderr or "")[:300],
        )
        return None

    try:
        payload = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        logger.error("[whisperkit] JSON sidecar invalide")
        return None

    text = str(payload.get("text") or "").strip()
    return {
        "text": text,
        "segments": payload.get("segments") or [],
        "language": payload.get("language") or language,
    }


__all__ = ["is_whisperkit_available", "transcribe_pcm_file", "whisperkit_binary"]
