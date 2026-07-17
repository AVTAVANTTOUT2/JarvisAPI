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
_DEFAULT_MLX_VENV = Path.home() / "mlx-env"


def ttskit_binary() -> Path | None:
    """Retourne le sidecar installé sans provoquer de téléchargement."""
    env_path = shutil.which("jarvis-ttskit")
    if env_path:
        return Path(env_path)
    if _DEFAULT_BINARY.is_file() and os.access(_DEFAULT_BINARY, os.X_OK):
        return _DEFAULT_BINARY
    return None


def mlx_python_path() -> Path | None:
    """Python de ``JARVIS_VENV`` (défaut ``~/mlx-env``) s'il est exécutable."""
    raw = (os.environ.get("JARVIS_VENV") or "").strip()
    if not raw:
        try:
            import config

            raw = str(getattr(config, "JARVIS_VENV", "") or "").strip()
        except Exception:
            raw = ""
    venv = Path(raw).expanduser() if raw else _DEFAULT_MLX_VENV
    python = venv / "bin" / "python"
    if python.is_file() and os.access(python, os.X_OK):
        return python
    return None


def is_repo_ttskit_launcher(binary: Path | None = None) -> bool:
    """True si le binaire est le lanceur bash du dépôt (nécessite JARVIS_VENV)."""
    path = binary if binary is not None else ttskit_binary()
    if path is None:
        return False
    try:
        return path.resolve() == _DEFAULT_BINARY.resolve()
    except OSError:
        return False


def is_ttskit_available() -> bool:
    """Sidecar présent et, pour le lanceur repo, Python MLX exécutable.

    ``jarvis-ttskit`` dans le PATH est considéré autonome (pas de probe venv).
    Le lanceur ``native_audio/ttskit_synthesize`` exige ``$JARVIS_VENV/bin/python``
    pour éviter un faux positif quand le script est dans le dépôt mais mlx-audio
    n'est pas installé.
    """
    binary = ttskit_binary()
    if binary is None:
        return False
    if is_repo_ttskit_launcher(binary):
        return mlx_python_path() is not None
    return True


async def stream_pcm16(
    text: str,
    *,
    model: str,
    language: str,
    model_path: str = "",
    speaker: str = "",
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
    speaker_value = (speaker or "").strip()
    if speaker_value:
        cmd.extend(["--speaker", speaker_value])

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


__all__ = [
    "is_ttskit_available",
    "is_repo_ttskit_launcher",
    "mlx_python_path",
    "stream_pcm16",
    "ttskit_binary",
]
