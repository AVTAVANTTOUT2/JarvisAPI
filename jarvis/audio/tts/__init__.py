"""Synthèse vocale locale de JARVIS.

Le pipeline vocal ne connaît que ce paquet :

    from jarvis.audio.tts import create_local_tts_provider

    provider = create_local_tts_provider()
    await provider.warmup()
    async for chunk in provider.stream(text, request_id=rid, utterance_id=uid):
        ...

Aucun moteur concret n'est nommé ailleurs. Aucun appel réseau n'est possible :
la table des fournisseurs est fermée et ne contient que des backends locaux.

Importer ce paquet ne charge ni MLX, ni les poids, ni un sous-processus — les
backends sont importés à la construction, et le modèle au ``warmup()``.
"""

from jarvis.audio.tts.base import (
    DEFAULT_CHANNELS,
    DEFAULT_SAMPLE_RATE,
    PCM_S16LE,
    AudioChunk,
    LocalTTSProvider,
    ProviderInfo,
)
from jarvis.audio.tts.config import TTSSettings, load_tts_settings
from jarvis.audio.tts.errors import (
    TTSCancelledError,
    TTSError,
    TTSModelNotFoundError,
    TTSSynthesisError,
    TTSUnavailableError,
    TTSUnsupportedDeviceError,
)
from jarvis.audio.tts.factory import (
    create_local_tts_provider,
    get_local_tts_provider,
    reset_local_tts_provider,
)
from jarvis.audio.tts.segmenter import TextStreamSegmenter, segment_stream

__all__ = [
    "DEFAULT_CHANNELS",
    "DEFAULT_SAMPLE_RATE",
    "PCM_S16LE",
    "AudioChunk",
    "LocalTTSProvider",
    "ProviderInfo",
    "TTSCancelledError",
    "TTSError",
    "TTSModelNotFoundError",
    "TTSSettings",
    "TTSSynthesisError",
    "TTSUnavailableError",
    "TTSUnsupportedDeviceError",
    "TextStreamSegmenter",
    "create_local_tts_provider",
    "get_local_tts_provider",
    "load_tts_settings",
    "reset_local_tts_provider",
    "segment_stream",
]
