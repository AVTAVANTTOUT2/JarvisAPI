"""File vocale prioritaire — unique point de sortie TTS pour le pipeline natif macOS."""

from __future__ import annotations

import asyncio
import heapq
import itertools
import logging
import time
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)


class VoicePriority(IntEnum):
    """Ordre croissant = plus prioritaire (heap min)."""

    CRITICAL = 0
    USER_RESPONSE = 1
    IMPORTANT = 2
    BACKGROUND = 3


@dataclass(order=True)
class VoiceRequest:
    priority: int
    seq: int
    text: str = field(compare=False)
    emotion: str = field(default="neutral", compare=False)
    cancel_event: asyncio.Event | None = field(default=None, compare=False)
    done_event: asyncio.Event | None = field(default=None, compare=False)


PlayFn = Callable[[str, str, asyncio.Event | None], Awaitable[None]]
CancelFn = Callable[[], None]


class VoiceQueue:
    """File centrale TTS avec priorités et verrou conversation utilisateur."""

    def __init__(self) -> None:
        self._heap: list[tuple[int, int, VoiceRequest]] = []
        self._seq = itertools.count()
        self._lock = asyncio.Lock()
        self._cond = asyncio.Condition(self._lock)
        self._consumer_task: asyncio.Task[Any] | None = None
        self._play_fn: PlayFn | None = None
        self._cancel_fn: CancelFn | None = None
        self._running = False
        self._user_conversation_active = False
        self._mic_capture_active = False
        self._tts_playing = False
        self._current_cancel: asyncio.Event | None = None
        self._current_request: VoiceRequest | None = None

    @property
    def user_conversation_active(self) -> bool:
        return self._user_conversation_active

    @property
    def running(self) -> bool:
        return self._running

    @property
    def voice_busy(self) -> bool:
        """True pendant un tour utilisateur, une lecture TTS ou si la file attend."""
        return (
            self._user_conversation_active
            or self._tts_playing
            or bool(self._heap)
        )

    @property
    def mic_capture_active(self) -> bool:
        """État informatif du micro, sans bloquer le ScreenWatcher à lui seul."""
        return self._mic_capture_active

    def set_user_conversation_active(self, active: bool) -> None:
        self._user_conversation_active = active

    def set_mic_capture_active(self, active: bool) -> None:
        self._mic_capture_active = active

    async def enqueue(
        self,
        text: str,
        *,
        emotion: str = "neutral",
        priority: VoicePriority = VoicePriority.BACKGROUND,
        wait: bool = False,
        timeout: float = 120.0,
    ) -> bool:
        """Ajoute une demande de parole. Si ``wait=True``, bloque jusqu'à la fin."""
        clean = (text or "").strip()
        if not clean:
            return False

        done_event = asyncio.Event() if wait else None
        req = VoiceRequest(
            priority=int(priority),
            seq=next(self._seq),
            text=clean,
            emotion=emotion or "neutral",
            cancel_event=asyncio.Event(),
            done_event=done_event,
        )

        async with self._cond:
            heapq.heappush(self._heap, (req.priority, req.seq, req))
            self._cond.notify()

        if (
            priority == VoicePriority.CRITICAL
            and self._current_request is not None
            and self._current_request.priority > int(VoicePriority.CRITICAL)
        ):
            await self.cancel_current()

        if wait and done_event is not None:
            try:
                await asyncio.wait_for(done_event.wait(), timeout=timeout)
            except asyncio.TimeoutError:
                logger.warning("[voice_queue] Timeout attente TTS")
                return False
        return True

    async def enqueue_and_wait(
        self,
        text: str,
        *,
        emotion: str = "neutral",
        priority: VoicePriority = VoicePriority.USER_RESPONSE,
        timeout: float = 120.0,
    ) -> bool:
        return await self.enqueue(
            text, emotion=emotion, priority=priority, wait=True, timeout=timeout,
        )

    async def cancel_current(self) -> None:
        if self._current_cancel and not self._current_cancel.is_set():
            self._current_cancel.set()
        if self._cancel_fn is not None:
            try:
                self._cancel_fn()
            except Exception as exc:
                logger.debug("[voice_queue] annulation sortie : %s", exc)

    async def start(self, play_fn: PlayFn, cancel_fn: CancelFn | None = None) -> None:
        if self._running:
            # Le daemon micro peut prendre le relais du lecteur léger démarré
            # par la sentinelle, sans recréer la file ni perdre les requêtes.
            self._play_fn = play_fn
            if cancel_fn is not None:
                self._cancel_fn = cancel_fn
            return
        self._play_fn = play_fn
        self._cancel_fn = cancel_fn
        self._running = True
        self._consumer_task = asyncio.create_task(self._consumer_loop(), name="voice_queue")

    async def stop(self) -> None:
        self._running = False
        await self.cancel_current()
        async with self._cond:
            self._cond.notify_all()
        if self._consumer_task:
            self._consumer_task.cancel()
            try:
                await self._consumer_task
            except asyncio.CancelledError:
                pass
            self._consumer_task = None
        self._cancel_fn = None
        async with self._lock:
            pending = [entry[2] for entry in self._heap]
            self._heap.clear()
        for req in pending:
            if req.done_event and not req.done_event.is_set():
                req.done_event.set()

    async def _consumer_loop(self) -> None:
        assert self._play_fn is not None
        while self._running:
            req: VoiceRequest | None = None
            async with self._cond:
                while self._running and not self._heap:
                    await self._cond.wait()
                if not self._running:
                    break
                _, _, candidate = self._heap[0]
                blocked_by_user_turn = (
                    self._user_conversation_active
                    and candidate.priority >= int(VoicePriority.IMPORTANT)
                )
                if not blocked_by_user_turn:
                    _, _, req = heapq.heappop(self._heap)

            if req is None:
                # Conserver les notifications normales jusqu'à la fin du tour
                # utilisateur, sans boucle active ni perte silencieuse.
                await asyncio.sleep(0.1)
                continue

            self._current_cancel = req.cancel_event
            self._current_request = req
            self._tts_playing = True
            try:
                await self._play_fn(req.text, req.emotion, req.cancel_event)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning("[voice_queue] Lecture TTS : %s", e)
            finally:
                self._tts_playing = False
                self._current_cancel = None
                self._current_request = None
                if req.done_event and not req.done_event.is_set():
                    req.done_event.set()
                await asyncio.sleep(0.05)


voice_queue = VoiceQueue()


def priority_from_string(value: str) -> VoicePriority:
    mapping = {
        "urgent": VoicePriority.CRITICAL,
        "critical": VoicePriority.CRITICAL,
        "user": VoicePriority.USER_RESPONSE,
        "response": VoicePriority.USER_RESPONSE,
        "high": VoicePriority.IMPORTANT,
        "important": VoicePriority.IMPORTANT,
        "normal": VoicePriority.BACKGROUND,
        "low": VoicePriority.BACKGROUND,
        "background": VoicePriority.BACKGROUND,
    }
    return mapping.get((value or "").lower().strip(), VoicePriority.BACKGROUND)


__all__ = [
    "VoicePriority",
    "VoiceQueue",
    "VoiceRequest",
    "priority_from_string",
    "voice_queue",
]
