"""Client générique d'un sidecar de synthèse maintenu chaud.

Le moteur tourne dans un autre interpréteur que JARVIS (celui qui porte MLX).
Ce module parle son protocole : une trame binaire ``tag + longueur + charge``,
une requête JSON par ligne sur stdin, des fragments PCM sur stdout.

Deux propriétés valent d'être explicitées, parce qu'elles ne se devinent pas :

- **Le processus reste vivant.** Relancer un interpréteur et recharger plusieurs
  gigaoctets de poids par phrase coûterait des secondes à chaque réponse. Le
  modèle est chargé une fois, à ``start()``.
- **L'annulation ne casse pas le protocole.** Cesser de lire au milieu d'une
  réponse laisserait des trames orphelines dans le tuyau, et la requête
  suivante lirait l'audio de la précédente. Une annulation cesse donc de
  *livrer* les fragments mais continue de *drainer* le flux jusqu'à la trame de
  fin. Le gaspillage est borné à un segment déjà lancé ; la lecture, elle,
  s'arrête immédiatement côté sortie audio.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import struct
from collections.abc import AsyncIterator
from pathlib import Path

from jarvis.audio.tts.errors import TTSSynthesisError, TTSUnavailableError

logger = logging.getLogger(__name__)

FRAME_HEADER = struct.Struct(">4sI")
TAG_READY = b"RDY\0"
TAG_CHUNK = b"CHK\0"
TAG_END = b"END\0"
TAG_ERROR = b"ERR\0"


class SidecarProtocolError(TTSSynthesisError):
    """Le sidecar a rompu le protocole (trame inconnue, flux tronqué)."""


class SidecarClient:
    """Processus de synthèse chaud, une requête à la fois."""

    def __init__(
        self,
        command: list[str],
        *,
        env: dict[str, str] | None = None,
        label: str = "tts-sidecar",
        start_timeout_s: float = 300.0,
        chunk_timeout_s: float = 60.0,
    ) -> None:
        self._command = command
        self._env = env or {}
        self._label = label
        self._start_timeout_s = start_timeout_s
        self._chunk_timeout_s = chunk_timeout_s

        self._proc: asyncio.subprocess.Process | None = None
        self._metadata: dict[str, object] = {}
        self._lock = asyncio.Lock()
        self._start_lock = asyncio.Lock()
        self._cancelled: set[str] = set()

    # ── Cycle de vie ────────────────────────────────────────────────────────

    @property
    def ready(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    @property
    def metadata(self) -> dict[str, object]:
        """Ce que le moteur a déclaré au démarrage (fréquence, voix, device)."""
        return dict(self._metadata)

    async def start(self) -> dict[str, object]:
        """Démarre le sidecar et attend la trame prête (modèle chargé).

        Lève ``TTSUnavailableError`` avec la sortie d'erreur du sidecar : un
        modèle absent ou un venv incomplet doit se lire dans le message, pas
        dans un ``False``.
        """
        async with self._start_lock:
            if self.ready:
                return self.metadata
            await self._terminate()

            env = os.environ.copy()
            env.update(self._env)
            try:
                self._proc = await asyncio.create_subprocess_exec(
                    *self._command,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=env,
                )
            except OSError as exc:
                self._proc = None
                raise TTSUnavailableError(
                    f"{self._label} : démarrage impossible ({exc})"
                ) from exc

            try:
                tag, payload = await asyncio.wait_for(
                    self._read_frame(), timeout=self._start_timeout_s,
                )
            except (asyncio.TimeoutError, SidecarProtocolError) as exc:
                detail = await self._drain_stderr()
                await self._terminate()
                raise TTSUnavailableError(
                    f"{self._label} : moteur non prêt ({exc}){detail}"
                ) from exc

            if tag != TAG_READY:
                await self._terminate()
                raise TTSUnavailableError(
                    f"{self._label} : trame inattendue au démarrage ({tag!r})"
                )

            self._metadata = self._decode_metadata(payload)
            logger.info("[%s] moteur prêt %s", self._label, self._metadata)
            return self.metadata

    async def stop(self) -> None:
        await self._terminate()

    async def _terminate(self) -> None:
        proc, self._proc = self._proc, None
        self._metadata = {}
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

    async def _drain_stderr(self, limit: int = 600) -> str:
        """Sortie d'erreur du sidecar, tronquée — pour un message actionnable."""
        proc = self._proc
        if proc is None or proc.stderr is None:
            return ""
        try:
            data = await asyncio.wait_for(proc.stderr.read(limit), timeout=1.0)
        except (asyncio.TimeoutError, ValueError):
            return ""
        text = (data or b"").decode(errors="replace").strip()
        return f" — {text}" if text else ""

    @staticmethod
    def _decode_metadata(payload: bytes) -> dict[str, object]:
        if not payload:
            return {}
        try:
            decoded = json.loads(payload.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return {}
        return decoded if isinstance(decoded, dict) else {}

    # ── Protocole ───────────────────────────────────────────────────────────

    async def _read_frame(self) -> tuple[bytes, bytes]:
        proc = self._proc
        if proc is None or proc.stdout is None:
            raise SidecarProtocolError(f"{self._label} : processus absent")
        try:
            header = await proc.stdout.readexactly(FRAME_HEADER.size)
        except (asyncio.IncompleteReadError, ConnectionResetError) as exc:
            raise SidecarProtocolError(f"{self._label} : flux interrompu") from exc
        tag, length = FRAME_HEADER.unpack(header)
        payload = b""
        if length:
            try:
                payload = await proc.stdout.readexactly(length)
            except (asyncio.IncompleteReadError, ConnectionResetError) as exc:
                raise SidecarProtocolError(
                    f"{self._label} : charge utile tronquée"
                ) from exc
        return tag, payload

    def cancel(self, request_id: str) -> None:
        """Marque une requête comme annulée — sans casser le flux en cours."""
        if request_id:
            self._cancelled.add(request_id)

    async def stream(
        self, request: dict[str, object], *, request_id: str,
    ) -> AsyncIterator[bytes]:
        """Envoie une requête et diffuse les fragments PCM jusqu'à la fin."""
        async with self._lock:
            self._cancelled.discard(request_id)
            await self.start()
            proc = self._proc
            if proc is None or proc.stdin is None:
                raise TTSUnavailableError(f"{self._label} : entrée fermée")

            line = json.dumps(request, ensure_ascii=False).encode("utf-8") + b"\n"
            try:
                proc.stdin.write(line)
                await proc.stdin.drain()
            except (BrokenPipeError, ConnectionResetError, OSError) as exc:
                await self._terminate()
                raise TTSSynthesisError(
                    f"{self._label} : envoi impossible ({exc})"
                ) from exc

            error_message: str | None = None
            try:
                while True:
                    try:
                        tag, payload = await asyncio.wait_for(
                            self._read_frame(), timeout=self._chunk_timeout_s,
                        )
                    except asyncio.TimeoutError as exc:
                        await self._terminate()
                        raise TTSSynthesisError(
                            f"{self._label} : moteur muet au-delà de "
                            f"{self._chunk_timeout_s:.0f}s"
                        ) from exc
                    except SidecarProtocolError:
                        await self._terminate()
                        raise

                    if tag == TAG_CHUNK:
                        if payload and request_id not in self._cancelled:
                            yield payload
                    elif tag == TAG_ERROR:
                        error_message = payload.decode(errors="replace")[:300]
                    elif tag == TAG_END:
                        break
                    else:
                        await self._terminate()
                        raise SidecarProtocolError(
                            f"{self._label} : trame inconnue {tag!r}"
                        )
            finally:
                self._cancelled.discard(request_id)

            if error_message:
                raise TTSSynthesisError(f"{self._label} : {error_message}")


def sidecar_launcher(name: str) -> Path | None:
    """Lanceur du dépôt s'il est exécutable, sinon ``None``."""
    path = Path(__file__).resolve().parents[4] / "native_audio" / name
    if path.is_file() and os.access(path, os.X_OK):
        return path
    return None


def mlx_python() -> Path | None:
    """Python de ``JARVIS_VENV`` (défaut ``~/mlx-env``) s'il est exécutable."""
    raw = (os.environ.get("JARVIS_VENV") or "").strip()
    if not raw:
        try:
            import config

            raw = str(getattr(config, "JARVIS_VENV", "") or "").strip()
        except Exception:
            raw = ""
    venv = Path(raw).expanduser() if raw else Path.home() / "mlx-env"
    python = venv / "bin" / "python"
    if python.is_file() and os.access(python, os.X_OK):
        return python
    return None


__all__ = [
    "SidecarClient",
    "SidecarProtocolError",
    "TAG_CHUNK",
    "TAG_END",
    "TAG_ERROR",
    "TAG_READY",
    "mlx_python",
    "sidecar_launcher",
]
