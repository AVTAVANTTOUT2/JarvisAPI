"""Bridge Kokoro MLX : sidecar local, en one-shot ou en processus chaud.

Le mode one-shot (``synthesize_bytes``) relance un interpréteur Python et
recharge le modèle à chaque appel. ``KokoroWorker`` garde au contraire un
processus vivant, modèle et voix déjà en mémoire, et diffuse le PCM fragment
par fragment : la lecture commence sur la première phrase au lieu d'attendre
la synthèse complète.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
from collections.abc import AsyncGenerator
from pathlib import Path

from native_audio.kokoro_mlx import (
    FRAME_HEADER,
    TAG_CHUNK,
    TAG_END,
    TAG_ERROR,
    TAG_READY,
)
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


class KokoroWorkerError(RuntimeError):
    """Le sidecar chaud n'a pas pu démarrer ou a rompu le protocole."""


class KokoroWorker:
    """Processus Kokoro maintenu chaud, une synthèse à la fois.

    Un seul énoncé est synthétisé à la fois (verrou) : deux générations MLX
    concurrentes se disputeraient le GPU et allongeraient les deux. Le
    processus est relancé à la demande s'il meurt, sans propager la panne au
    tour de parole en cours (l'appelant obtient un flux vide et bascule sur
    son repli).
    """

    def __init__(
        self,
        *,
        model: str,
        voice: str,
        lang_code: str,
        speed: float,
        max_tokens: int,
        first_chunk_max_tokens: int,
        start_timeout_s: float = 180.0,
        chunk_timeout_s: float = 60.0,
    ) -> None:
        self._model = model
        self._voice = voice
        self._lang_code = lang_code
        self._speed = speed
        self._max_tokens = max_tokens
        self._first_chunk_max_tokens = first_chunk_max_tokens
        self._start_timeout_s = start_timeout_s
        self._chunk_timeout_s = chunk_timeout_s

        self._proc: asyncio.subprocess.Process | None = None
        self._lock = asyncio.Lock()
        self._start_lock = asyncio.Lock()
        self._ready = False

    # ── Cycle de vie ────────────────────────────────────────────────────────

    @property
    def ready(self) -> bool:
        return self._ready and self._proc is not None and self._proc.returncode is None

    async def start(self) -> bool:
        """Démarre le sidecar et attend la trame READY (modèle chargé)."""
        async with self._start_lock:
            if self.ready:
                return True
            await self._terminate()

            binary = kokoro_mlx_binary()
            if binary is None:
                return False

            cmd = [
                str(binary),
                "--serve",
                "--model", self._model,
                "--voice", self._voice,
                "--lang-code", self._lang_code,
                "--speed", str(self._speed),
            ]
            env = os.environ.copy()
            python = mlx_python_path()
            if python is not None:
                env["JARVIS_VENV"] = str(python.parent.parent)

            try:
                self._proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=env,
                )
            except OSError as exc:
                logger.error("[kokoro-mlx] démarrage du sidecar chaud : %s", exc)
                self._proc = None
                return False

            try:
                tag, _ = await asyncio.wait_for(
                    self._read_frame(), timeout=self._start_timeout_s,
                )
            except (asyncio.TimeoutError, KokoroWorkerError) as exc:
                logger.error("[kokoro-mlx] sidecar chaud sans READY : %s", exc)
                await self._terminate()
                return False

            if tag != TAG_READY:
                logger.error("[kokoro-mlx] trame inattendue au démarrage : %r", tag)
                await self._terminate()
                return False

            self._ready = True
            logger.info(
                "[kokoro-mlx] sidecar chaud prêt (modèle=%s voix=%s)",
                self._model, self._voice,
            )
            return True

    async def stop(self) -> None:
        self._ready = False
        await self._terminate()

    async def _terminate(self) -> None:
        proc, self._proc = self._proc, None
        self._ready = False
        if proc is None or proc.returncode is not None:
            return
        try:
            proc.terminate()
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(proc.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass

    # ── Protocole ───────────────────────────────────────────────────────────

    async def _read_frame(self) -> tuple[bytes, bytes]:
        proc = self._proc
        if proc is None or proc.stdout is None:
            raise KokoroWorkerError("sidecar absent")
        try:
            header = await proc.stdout.readexactly(FRAME_HEADER.size)
        except (asyncio.IncompleteReadError, ConnectionResetError) as exc:
            raise KokoroWorkerError("flux du sidecar interrompu") from exc
        tag, length = FRAME_HEADER.unpack(header)
        payload = b""
        if length:
            try:
                payload = await proc.stdout.readexactly(length)
            except (asyncio.IncompleteReadError, ConnectionResetError) as exc:
                raise KokoroWorkerError("charge utile tronquée") from exc
        return tag, payload

    async def stream_pcm(self, text: str) -> AsyncGenerator[bytes, None]:
        """Diffuse le PCM16 24 kHz fragment par fragment.

        Ne lève pas : un sidecar mort produit un flux vide, à charge de
        l'appelant de basculer sur son moteur de repli.
        """
        cleaned = (text or "").strip()
        if not cleaned:
            return

        async with self._lock:
            if not await self.start():
                return
            proc = self._proc
            if proc is None or proc.stdin is None:
                return

            request = json.dumps({
                "text": cleaned,
                "voice": self._voice,
                "lang_code": self._lang_code,
                "speed": self._speed,
                "max_tokens": self._max_tokens,
                "first_chunk_max_tokens": self._first_chunk_max_tokens,
            }, ensure_ascii=False)

            try:
                proc.stdin.write(request.encode("utf-8") + b"\n")
                await proc.stdin.drain()
            except (BrokenPipeError, ConnectionResetError, OSError) as exc:
                logger.warning("[kokoro-mlx] envoi au sidecar chaud : %s", exc)
                await self._terminate()
                return

            while True:
                try:
                    tag, payload = await asyncio.wait_for(
                        self._read_frame(), timeout=self._chunk_timeout_s,
                    )
                except asyncio.TimeoutError:
                    logger.warning("[kokoro-mlx] sidecar chaud muet — redémarrage")
                    await self._terminate()
                    return
                except KokoroWorkerError as exc:
                    logger.warning("[kokoro-mlx] protocole rompu : %s", exc)
                    await self._terminate()
                    return

                if tag == TAG_CHUNK:
                    if payload:
                        yield payload
                elif tag == TAG_ERROR:
                    logger.warning(
                        "[kokoro-mlx] erreur du sidecar : %s",
                        payload.decode(errors="replace")[:200],
                    )
                elif tag == TAG_END:
                    return
                else:
                    logger.warning("[kokoro-mlx] trame inconnue %r — redémarrage", tag)
                    await self._terminate()
                    return


__all__ = [
    "KokoroWorker",
    "KokoroWorkerError",
    "is_kokoro_mlx_available",
    "kokoro_mlx_binary",
    "synthesize_bytes",
]
