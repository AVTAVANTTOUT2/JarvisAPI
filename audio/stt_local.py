"""Compatibilité — délègue au STT multi-moteurs du daemon."""

from __future__ import annotations

from audio.stt_daemon import (
    DaemonSTT,
    stt_daemon,
    stt_local,
)

__all__ = ["DaemonSTT", "stt_daemon", "stt_local"]
