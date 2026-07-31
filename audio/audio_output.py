"""Sortie audio native persistante — lecture PCM progressive via sounddevice."""

from __future__ import annotations

import asyncio
import io
import logging
import os
import tempfile
import threading
from collections.abc import AsyncGenerator, Iterable
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def resolve_preferred_output_device(sd_module: Any | None = None) -> int | None:
    """Retourne la sortie audio du **système** (ou un override explicite).

    Priorité :
    1. ``AUDIO_DAEMON_OUTPUT_DEVICE`` (index entier ou sous-chaîne du nom)
    2. Sortie par défaut CoreAudio / macOS — jamais d'appariement micro→casque
    """
    if sd_module is not None:
        sd_mod = sd_module
    else:
        try:
            import sounddevice as sd  # type: ignore[import-not-found]
        except ImportError:
            return None
        sd_mod = sd

    override = (os.environ.get("AUDIO_DAEMON_OUTPUT_DEVICE") or "").strip()
    if not override:
        try:
            import config

            override = str(getattr(config, "AUDIO_DAEMON_OUTPUT_DEVICE", "") or "").strip()
        except Exception:
            override = ""

    try:
        devices = list(sd_mod.query_devices())
    except Exception as exc:
        logger.debug("[audio_output] query_devices : %s", exc)
        return None

    def _system_default_output() -> int | None:
        """Lit la sortie par défaut (tuple, ndarray ou ``_InputOutputPair``)."""
        try:
            raw = sd_mod.default.device
            out_i = int(raw[1])
            if out_i >= 0:
                return out_i
        except Exception:
            pass
        try:
            host = sd_mod.query_hostapis(0)
            out = host.get("default_output_device")
            return int(out) if out is not None and int(out) >= 0 else None
        except Exception:
            return None

    if override:
        if override.isdigit():
            idx = int(override)
            if 0 <= idx < len(devices) and int(devices[idx].get("max_output_channels") or 0) > 0:
                return idx
        needle = override.lower()
        for i, dev in enumerate(devices):
            name = str(dev.get("name") or "")
            if needle in name.lower() and int(dev.get("max_output_channels") or 0) > 0:
                return i
        logger.warning(
            "[audio_output] AUDIO_DAEMON_OUTPUT_DEVICE=%r introuvable — défaut système",
            override,
        )

    default_out = _system_default_output()
    if default_out is not None and 0 <= default_out < len(devices):
        try:
            name = str(devices[default_out].get("name") or "")
            logger.debug(
                "[audio_output] sortie = défaut système « %s » (device %s)",
                name,
                default_out,
            )
        except Exception:
            pass
    return default_out


def _afconvert_to_wav(audio_bytes: bytes, suffix: str) -> bytes | None:
    """Convertit M4A/MP3 → WAV PCM via afconvert (stdout bytes)."""
    import subprocess

    tmp_in: str | None = None
    tmp_out: str | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as fin:
            fin.write(audio_bytes)
            tmp_in = fin.name
        tmp_out = tmp_in + ".wav"
        proc = subprocess.run(
            ["afconvert", "-f", "WAVE", "-d", "LEI16", tmp_in, tmp_out],
            check=False,
            capture_output=True,
            timeout=30,
        )
        if proc.returncode != 0 or not Path(tmp_out).is_file():
            logger.debug(
                "[audio_output] afconvert échec code=%s stderr=%s",
                proc.returncode,
                (proc.stderr or b"")[:200],
            )
            return None
        return Path(tmp_out).read_bytes()
    except Exception as exc:
        logger.debug("[audio_output] afconvert : %s", exc)
        return None
    finally:
        for path in (tmp_in, tmp_out):
            if path:
                try:
                    Path(path).unlink(missing_ok=True)
                except OSError:
                    pass


class NativeAudioOutput:
    """Flux de sortie CoreAudio via sounddevice (fallback subprocess interdit ici)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._playing = False
        self._stop_flag = threading.Event()
        self._sd: Any = None
        self._sf: Any = None
        try:
            import sounddevice as sd  # type: ignore[import-not-found]
            import soundfile as sf  # type: ignore[import-not-found]

            self._sd = sd
            self._sf = sf
            self.available = True
        except ImportError:
            self.available = False
            logger.warning(
                "[audio_output] sounddevice/soundfile absents — pip install sounddevice soundfile"
            )

    @property
    def is_playing(self) -> bool:
        return self._playing

    def stop(self) -> None:
        self._stop_flag.set()
        if self._sd is not None:
            try:
                self._sd.stop()
            except Exception:
                pass

    def _prepare_wav_bytes(self, audio_bytes: bytes) -> bytes | None:
        """Retourne des octets décodables par soundfile (WAV/FLAC…), ou None."""
        if not audio_bytes:
            return None
        try:
            self._sf.read(io.BytesIO(audio_bytes))
            return audio_bytes
        except Exception:
            pass

        from audio.audio_format import is_mpeg4_container, playback_file_extension

        ext = playback_file_extension(audio_bytes)
        if is_mpeg4_container(audio_bytes) or ext in {".m4a", ".mp3", ".mp4"}:
            converted = _afconvert_to_wav(audio_bytes, ext)
            if converted:
                logger.debug(
                    "[audio_output] conteneur %s converti en WAV (%d → %d octets)",
                    ext,
                    len(audio_bytes),
                    len(converted),
                )
                return converted
        return None

    async def play_bytes(self, audio_bytes: bytes, *, blocking: bool = True) -> bool:
        """Joue des bytes audio (WAV/MP3/M4A/FLAC) via soundfile sur la bonne sortie."""
        if not self.available or not audio_bytes:
            return False
        self._stop_flag.clear()
        loop = asyncio.get_running_loop()
        prepared = await loop.run_in_executor(None, self._prepare_wav_bytes, audio_bytes)
        if not prepared:
            magic = audio_bytes[:8].hex() if audio_bytes else ""
            logger.info(
                "[audio_output] format non décodable (len=%d magic=%s)",
                len(audio_bytes),
                magic,
            )
            return False

        device = resolve_preferred_output_device(self._sd)

        def _play() -> bool:
            with self._lock:
                self._playing = True
            try:
                data, samplerate = self._sf.read(io.BytesIO(prepared))
                if self._stop_flag.is_set():
                    return False
                play_kw: dict[str, Any] = {}
                if device is not None:
                    play_kw["device"] = device
                try:
                    device_name = ""
                    if device is not None:
                        device_name = str(self._sd.query_devices(device).get("name") or "")
                    logger.info(
                        "[audio_output] lecture %d octets @ %s Hz → %s",
                        len(prepared),
                        samplerate,
                        device_name or f"device={device}",
                    )
                except Exception:
                    pass
                self._sd.play(data, samplerate, **play_kw)
                if blocking:
                    self._sd.wait()
                return not self._stop_flag.is_set()
            except Exception as exc:
                logger.warning("[audio_output] lecture échouée : %s", exc)
                return False
            finally:
                with self._lock:
                    self._playing = False

        return bool(await loop.run_in_executor(None, _play))

    async def play_pcm16_stream(
        self,
        chunks: Iterable[bytes],
        *,
        sample_rate: int = 24000,
        blocking: bool = True,
    ) -> bool:
        """Joue des chunks PCM16 mono dès réception (streaming)."""
        if not self.available:
            return False
        self._stop_flag.clear()
        loop = asyncio.get_running_loop()
        device = resolve_preferred_output_device(self._sd)

        def _play_stream() -> None:
            import numpy as np  # type: ignore[import-untyped]

            with self._lock:
                self._playing = True
            try:
                stream_kw: dict[str, Any] = {
                    "samplerate": sample_rate,
                    "channels": 1,
                    "dtype": "int16",
                }
                if device is not None:
                    stream_kw["device"] = device
                stream = self._sd.OutputStream(**stream_kw)
                stream.start()
                for chunk in chunks:
                    if self._stop_flag.is_set():
                        break
                    if not chunk:
                        continue
                    arr = np.frombuffer(chunk, dtype=np.int16)
                    if arr.size == 0:
                        continue
                    stream.write(arr.reshape(-1, 1))
                stream.stop()
                stream.close()
            finally:
                with self._lock:
                    self._playing = False

        await loop.run_in_executor(None, _play_stream)
        return not self._stop_flag.is_set()

    async def play_stream_from_async(
        self,
        stream: AsyncGenerator[bytes, None],
        *,
        sample_rate: int = 24000,
    ) -> bool:
        """Consomme un générateur async et joue au fil de l'eau."""
        if not self.available:
            return False

        import queue as thread_queue

        pcm_queue: thread_queue.Queue[bytes | None] = thread_queue.Queue(maxsize=16)
        self._stop_flag.clear()
        loop = asyncio.get_running_loop()
        device = resolve_preferred_output_device(self._sd)

        async def _producer() -> None:
            def _put(item: bytes | None) -> bool:
                while not self._stop_flag.is_set():
                    try:
                        pcm_queue.put(item, timeout=0.25)
                        return True
                    except thread_queue.Full:
                        continue
                return False

            try:
                async for chunk in stream:
                    if self._stop_flag.is_set():
                        break
                    if chunk:
                        if not await loop.run_in_executor(None, _put, chunk):
                            break
            finally:
                try:
                    pcm_queue.put_nowait(None)
                except thread_queue.Full:
                    pass

        def _consumer() -> None:
            import numpy as np  # type: ignore[import-untyped]

            with self._lock:
                self._playing = True
            try:
                stream_kw: dict[str, Any] = {
                    "samplerate": sample_rate,
                    "channels": 1,
                    "dtype": "int16",
                }
                if device is not None:
                    stream_kw["device"] = device
                out = self._sd.OutputStream(**stream_kw)
                out.start()
                while not self._stop_flag.is_set():
                    try:
                        item = pcm_queue.get(timeout=0.25)
                    except thread_queue.Empty:
                        if producer.done():
                            break
                        continue
                    if item is None:
                        break
                    arr = np.frombuffer(item, dtype=np.int16)
                    if arr.size:
                        out.write(arr.reshape(-1, 1))
                out.stop()
                out.close()
            finally:
                with self._lock:
                    self._playing = False

        producer = asyncio.create_task(_producer())
        try:
            await loop.run_in_executor(None, _consumer)
        finally:
            await producer
        return not self._stop_flag.is_set()


native_audio_output = NativeAudioOutput()

__all__ = [
    "NativeAudioOutput",
    "native_audio_output",
    "resolve_preferred_output_device",
]
