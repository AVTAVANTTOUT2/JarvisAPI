"""Décodage SSE, déduplication et backoff borné."""

from __future__ import annotations

from collections import deque
import json
import random
from typing import Any, Callable

from .errors import OpenCodeProtocolError
from .models import SSEEvent


MAX_SSE_LINE_BYTES = 256 * 1024
MAX_SSE_EVENT_BYTES = 1024 * 1024


class SSEProtocolError(OpenCodeProtocolError):
    pass


class SSEDecoder:
    def __init__(
        self,
        *,
        source: str,
        max_line_bytes: int = MAX_SSE_LINE_BYTES,
        max_event_bytes: int = MAX_SSE_EVENT_BYTES,
    ) -> None:
        self.source = source
        self.max_line_bytes = max_line_bytes
        self.max_event_bytes = max_event_bytes
        self._data: list[str] = []
        self._data_bytes = 0
        self._event: str | None = None
        self._id: str | None = None
        self._id_changed = False
        self._retry: int | None = None

    def feed_line(self, line: str) -> SSEEvent | None:
        line = line.rstrip("\r\n")
        if len(line.encode("utf-8")) > self.max_line_bytes:
            raise SSEProtocolError("Ligne SSE OpenCode trop volumineuse")
        if line == "":
            return self._dispatch()
        if line.startswith(":"):
            return None
        field, separator, value = line.partition(":")
        if separator and value.startswith(" "):
            value = value[1:]
        if field == "data":
            data_bytes = len(value.encode("utf-8")) + (1 if self._data else 0)
            if self._data_bytes + data_bytes > self.max_event_bytes:
                raise SSEProtocolError("Événement SSE OpenCode trop volumineux")
            self._data.append(value)
            self._data_bytes += data_bytes
        elif field == "event":
            self._event = value
        elif field == "id" and "\x00" not in value:
            self._id = value
            self._id_changed = True
        elif field == "retry":
            try:
                retry = int(value)
            except ValueError:
                return None
            if retry >= 0:
                self._retry = retry
        return None

    def finish(self) -> SSEEvent | None:
        return self._dispatch()

    def _dispatch(self) -> SSEEvent | None:
        if not self._data:
            self._data_bytes = 0
            self._event = None
            self._id_changed = False
            return None
        raw = "\n".join(self._data)
        try:
            data: Any = json.loads(raw)
        except json.JSONDecodeError:
            data = raw
        event = SSEEvent.create(
            event_id=self._id if self._id_changed else None,
            event_type=self._event,
            data=data,
            source=self.source,
            retry_ms=self._retry,
            resume_id=self._id,
        )
        self._data = []
        self._data_bytes = 0
        self._event = None
        self._id_changed = False
        return event


class SSELineDecoder:
    """Découpe un flux octet par octet sans tampon de ligne non borné."""

    def __init__(self, *, max_line_bytes: int = MAX_SSE_LINE_BYTES) -> None:
        self.max_line_bytes = max_line_bytes
        self._buffer = bytearray()

    def feed(self, chunk: bytes) -> list[str]:
        lines: list[str] = []
        offset = 0
        while True:
            newline = chunk.find(b"\n", offset)
            if newline < 0:
                self._extend(chunk[offset:])
                break
            self._extend(chunk[offset:newline])
            lines.append(self._decode_buffer())
            self._buffer.clear()
            offset = newline + 1
        return lines

    def finish(self) -> list[str]:
        if not self._buffer:
            return []
        line = self._decode_buffer()
        self._buffer.clear()
        return [line]

    def _extend(self, value: bytes) -> None:
        if len(self._buffer) + len(value) > self.max_line_bytes:
            raise SSEProtocolError("Ligne SSE OpenCode trop volumineuse")
        self._buffer.extend(value)

    def _decode_buffer(self) -> str:
        value = bytes(self._buffer)
        if value.endswith(b"\r"):
            value = value[:-1]
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SSEProtocolError("Encodage UTF-8 SSE OpenCode invalide") from exc


class EventDeduplicator:
    def __init__(self, max_entries: int = 4096) -> None:
        if max_entries <= 0:
            raise ValueError("max_entries doit être positif")
        self._max_entries = max_entries
        self._order: deque[str] = deque()
        self._seen: set[str] = set()

    def accept(self, event: SSEEvent) -> bool:
        if event.event_id in self._seen:
            return False
        self._seen.add(event.event_id)
        self._order.append(event.event_id)
        while len(self._order) > self._max_entries:
            self._seen.discard(self._order.popleft())
        return True


class RetryPolicy:
    def __init__(
        self,
        *,
        base_seconds: float,
        max_seconds: float,
        jitter_seconds: float,
        random_value: Callable[[], float] = random.random,
    ) -> None:
        if base_seconds <= 0 or max_seconds <= 0 or jitter_seconds < 0:
            raise ValueError("Politique de reconnexion invalide")
        self.base_seconds = base_seconds
        self.max_seconds = max_seconds
        self.jitter_seconds = jitter_seconds
        self.random_value = random_value

    def delay(self, attempt: int, server_retry_ms: int | None = None) -> float:
        if server_retry_ms is not None:
            base = min(self.max_seconds, max(0.0, server_retry_ms / 1000.0))
        else:
            base = min(self.max_seconds, self.base_seconds * (2 ** max(0, attempt)))
        return min(self.max_seconds, base + self.random_value() * self.jitter_seconds)
