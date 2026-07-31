"""Bridge optionnel Kokoro MLX : sidecar local, WAV/PCM sur stdout."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
from pathlib import Path

from native_audio.ttskit_bridge import mlx_python_path

logger = logging.getLogger(__name__)

_SIDECAR_DIR = Path(__file__).resolve().parent
_DEFAULT_BINARY = _SIDECAR_DIR / "kokoro_synthesize"


def kokoro_mlx_binary() -> Path | None:
    """Retourne le lanceur Kokoro MLX s'il est exécutable."""
    env_path = shutil.which("jarvis-kokoro-mlx")
    if env_path:
        return Path(env_path)
    if _DEFAULT_BINARY.is_file() and os.access(_DEFAULT_BINARY, os.X_OK):
        return _DEFAULT_BINARY
    return None


def is_kokoro_mlx_available() -> bool:
    """Lanceur présent et Python MLX (JARVIS_VENV) exécutable."""
    if kokoro_mlx_binary() is None:
        return False
    return mlx_python_path() is not None


async def synthesize_bytes(
    text: str,
    *,
    model: str,
    voice: str,
    lang_code: str,
    speed: float,
    max_tokens: int,
    audio_format: str = "wav",
) -> bytes:
    """Appelle le sidecar et retourne WAV ou PCM16 (stdout)."""
    binary = kokoro_mlx_binary()
    if binary is None:
        return b""
    cleaned = (text or "").strip()
    if not cleaned:
        return b""

    cmd = [
        str(binary),
        "--model", model,
        "--voice", voice,
        "--lang-code", lang_code,
        "--speed", str(speed),
        "--max-tokens", str(int(max_tokens)),
        "--format", audio_format,
        "--text", cleaned,
    ]
    env = os.environ.copy()
    python = mlx_python_path()
    if python is not None:
        # Le lanceur lit JARVIS_VENV ; on force le parent du bin/python.
        env["JARVIS_VENV"] = str(python.parent.parent)

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        logger.error(
            "[kokoro-mlx] sidecar code=%s stderr=%s",
            proc.returncode,
            (stderr or b"").decode(errors="replace")[:400],
        )
        return b""
    if stderr:
        logger.debug(
            "[kokoro-mlx] stderr=%s",
            stderr.decode(errors="replace")[:300],
        )
    return stdout or b""


__all__ = [
    "is_kokoro_mlx_available",
    "kokoro_mlx_binary",
    "synthesize_bytes",
]
