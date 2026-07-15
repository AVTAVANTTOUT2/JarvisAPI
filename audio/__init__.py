"""Module audio — STT local multi-moteurs et TTS Edge/local."""

from audio.stt_daemon import stt_daemon as stt
from audio.tts import tts

__all__ = ["stt", "tts"]
