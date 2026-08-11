"""Modèles publics du SDK JARVIS."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class Operation:
    operation_id: str
    method: str
    path: str
    auth: str
    tag: str
    summary: str


@dataclass(frozen=True, slots=True)
class JarvisResponse:
    status_code: int
    headers: Mapping[str, str]
    content: bytes

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")

    def json(self) -> Any:
        if not self.content:
            return None
        return json.loads(self.content)

    def header(self, name: str) -> str | None:
        lowered = name.lower()
        for key, value in self.headers.items():
            if key.lower() == lowered:
                return value
        return None
