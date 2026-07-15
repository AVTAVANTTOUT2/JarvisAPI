"""Bridge optionnel TTSKit : sidecar local, PCM16 diffusé sur stdout."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
from collections.abc import AsyncGenerator
from pathlib import Path

logger = logging.getLogger(__name__)

_SIDECAR_DIR = Path(__file__).resolve().parent
_DEFAULT_BINARY = _SIDECAR_DIR / "ttskit_synthesize"


def ttskit_binary() -> Path | None:
    """Retourne le sidecar installé sans provoquer de téléchargement."""
    env_path = shutil.which("jarvis-ttskit")
    if env_path:
        return Path(env_path)
    if _DEFAULT_BINARY.is_file() and os.access(_DEFAULT_BINARY, os.X_OK):
        return _DEFAULT_BINARY
    return None


def is_ttskit_available() -> bool:
    return ttskit_binary() is not None


async def stream_pcm16(
    text: str,
    *,
    model: str,
    language: str,
    model_path: str = "",
) -> AsyncGenerator[bytes, None]:
    """Diffuse le PCM16/24 kHz mono produit par le sidecar sur stdout."""
    binary = ttskit_binary()
    if binary is None:
        return

    cmd = [
        str(binary),
        "--model", model,
        "--language", language,
        "--format", "pcm_s16le",
        "--sample-rate", "24000",
        "--text", text,
    ]
    if model_path:
        cmd.extend(["--model-path", model_path])

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    assert proc.stdout is not None
    assert proc.stderr is not None
    stderr_task = asyncio.create_task(proc.stderr.read())
    try:
        while True:
            chunk = await proc.stdout.read(4096)
            if not chunk:
                break
            yield chunk
        returncode = await proc.wait()
        stderr = await stderr_task
        if returncode != 0:
            logger.error(
                "[ttskit] sidecar code=%s stderr=%s",
                returncode,
                stderr.decode(errors="replace")[:300],
            )
    finally:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
        if not stderr_task.done():
            stderr_task.cancel()


__all__ = ["is_ttskit_available", "stream_pcm16", "ttskit_binary"]
