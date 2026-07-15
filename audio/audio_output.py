"""Sortie audio native persistante — lecture PCM progressive via sounddevice."""

from __future__ import annotations

import asyncio
import io
import logging
import threading
from collections.abc import AsyncGenerator, Iterable
from typing import Any

logger = logging.getLogger(__name__)


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

    async def play_bytes(self, audio_bytes: bytes, *, blocking: bool = True) -> bool:
        """Joue des bytes audio (WAV/MP3/M4A/FLAC) via soundfile."""
        if not self.available or not audio_bytes:
            return False
        self._stop_flag.clear()
        loop = asyncio.get_running_loop()

        def _play() -> None:
            with self._lock:
                self._playing = True
            try:
                data, samplerate = self._sf.read(io.BytesIO(audio_bytes))
                if self._stop_flag.is_set():
                    return
                self._sd.play(data, samplerate)
                if blocking:
                    self._sd.wait()
            finally:
                with self._lock:
                    self._playing = False

        await loop.run_in_executor(None, _play)
        return not self._stop_flag.is_set()

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

        def _play_stream() -> None:
            import numpy as np  # type: ignore[import-untyped]

            with self._lock:
                self._playing = True
            try:
                stream = self._sd.OutputStream(samplerate=sample_rate, channels=1, dtype="int16")
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

        async def _producer() -> None:
            try:
                async for chunk in stream:
                    if self._stop_flag.is_set():
                        break
                    if chunk:
                        await loop.run_in_executor(None, pcm_queue.put, chunk)
            finally:
                await loop.run_in_executor(None, pcm_queue.put, None)

        def _consumer() -> None:
            import numpy as np  # type: ignore[import-untyped]

            with self._lock:
                self._playing = True
            try:
                out = self._sd.OutputStream(
                    samplerate=sample_rate, channels=1, dtype="int16",
                )
                out.start()
                while not self._stop_flag.is_set():
                    item = pcm_queue.get(timeout=120)
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

__all__ = ["NativeAudioOutput", "native_audio_output"]
