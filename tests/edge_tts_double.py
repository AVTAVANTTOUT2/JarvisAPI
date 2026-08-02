"""Double de test du module `edge_tts` — aucun octet ne quitte la machine.

`audio.tts` importe `edge_tts` paresseusement, à l'intérieur des méthodes :
injecter ce double dans `sys.modules` suffit donc à exercer tout le chemin de
synthèse Edge (paramètres transmis, parsing des messages, erreurs, délais)
sans réseau ni dépendance installée.

Le double reproduit fidèlement la surface réellement utilisée par JARVIS :
`edge_tts.Communicate(text, voice, connect_timeout=..., receive_timeout=...)`
puis `communicate.stream()` qui produit des messages `audio`,
`WordBoundary` et `SentenceBoundary`, et `edge_tts.list_voices()`.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any, Final

AUDIO_MESSAGE_TYPE: Final[str] = "audio"

# Deux trames plausibles : tag ID3 puis synchro MPEG brute. Chacune dépasse
# `tests.tts_contract.MIN_MP3_BYTES` pour être un payload valide à elle seule.
ID3_CHUNK: Final[bytes] = b"ID3\x04\x00\x00\x00\x00\x00\x00" + bytes(1_300)
FRAME_CHUNK: Final[bytes] = b"\xff\xfb\x90\x64" + bytes(1_200)
EXPECTED_AUDIO: Final[bytes] = ID3_CHUNK + FRAME_CHUNK

FRENCH_VOICE: Final[str] = "fr-FR-HenriNeural"

VOICE_CATALOG: Final[tuple[dict[str, str], ...]] = (
    {"ShortName": "fr-FR-HenriNeural", "Gender": "Male", "Locale": "fr-FR"},
    {"ShortName": "fr-FR-DeniseNeural", "Gender": "Female", "Locale": "fr-FR"},
    {"ShortName": "en-US-EmmaMultilingualNeural", "Gender": "Female", "Locale": "en-US"},
)


def default_messages() -> list[dict[str, Any]]:
    """Flux nominal : audio, marqueurs de prosodie, et un chunk audio vide."""
    return [
        {"type": AUDIO_MESSAGE_TYPE, "data": ID3_CHUNK},
        {"type": "WordBoundary", "offset": 0, "duration": 1_000_000, "text": "Bonjour"},
        {"type": AUDIO_MESSAGE_TYPE, "data": FRAME_CHUNK},
        {"type": AUDIO_MESSAGE_TYPE, "data": b""},
        {"type": "SentenceBoundary", "offset": 1_000_000, "duration": 500_000},
    ]


@dataclass(frozen=True)
class EdgeCall:
    """Appel enregistré à `edge_tts.Communicate`."""

    text: str
    voice: str
    kwargs: dict[str, Any] = field(default_factory=dict)


class FakeCommunicate:
    """Objet retourné par `FakeEdgeTTS.Communicate`."""

    def __init__(self, owner: "FakeEdgeTTS") -> None:
        self._owner = owner

    async def stream(self) -> AsyncIterator[dict[str, Any]]:
        owner = self._owner
        owner.stream_count += 1
        emitted = 0
        for message in owner.messages:
            if owner.error is not None and emitted >= owner.error_after:
                raise owner.error
            if owner.chunk_delay > 0:
                await asyncio.sleep(owner.chunk_delay)
            yield message
            emitted += 1
        if owner.error is not None:
            raise owner.error


class FakeEdgeTTS:
    """Substitut du module `edge_tts`, scénarisable et introspectable."""

    def __init__(
        self,
        *,
        messages: Iterable[dict[str, Any]] | None = None,
        error: BaseException | None = None,
        error_after: int = 0,
        chunk_delay: float = 0.0,
        voices: Sequence[dict[str, str]] = VOICE_CATALOG,
        voices_error: BaseException | None = None,
    ) -> None:
        self.messages: list[dict[str, Any]] = (
            list(messages) if messages is not None else default_messages()
        )
        self.error = error
        self.error_after = error_after
        self.chunk_delay = chunk_delay
        self.voices = list(voices)
        self.voices_error = voices_error
        self.calls: list[EdgeCall] = []
        self.stream_count = 0
        self.list_voices_count = 0

    # Nom volontairement capitalisé : c'est la classe `edge_tts.Communicate`.
    def Communicate(self, text: str, voice: str, **kwargs: Any) -> FakeCommunicate:  # noqa: N802
        self.calls.append(EdgeCall(text=text, voice=voice, kwargs=dict(kwargs)))
        return FakeCommunicate(self)

    async def list_voices(self) -> list[dict[str, str]]:
        self.list_voices_count += 1
        if self.voices_error is not None:
            raise self.voices_error
        return [dict(voice) for voice in self.voices]

    @property
    def last_call(self) -> EdgeCall:
        if not self.calls:
            raise AssertionError("Aucun appel à edge_tts.Communicate n'a été enregistré")
        return self.calls[-1]


__all__ = [
    "AUDIO_MESSAGE_TYPE",
    "EXPECTED_AUDIO",
    "FRAME_CHUNK",
    "FRENCH_VOICE",
    "FakeCommunicate",
    "FakeEdgeTTS",
    "EdgeCall",
    "ID3_CHUNK",
    "VOICE_CATALOG",
    "default_messages",
]
