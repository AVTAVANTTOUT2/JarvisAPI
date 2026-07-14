"""État partagé du daemon audio."""

from __future__ import annotations

from typing import Any


def _audio_daemon_status_payload() -> dict[str, Any]:
    """Payload pour /api/status et /api/integrations."""
    try:
        from scripts.audio_daemon import audio_daemon as _ad
        return _ad.get_status()
    except Exception:
        return {"enabled": False, "state": "idle", "error": "indisponible"}
