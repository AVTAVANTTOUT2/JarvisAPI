"""Contrôleur WebSocket des enregistrements spoulés durablement."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger("jarvis")


class WebSocketRecordingController:
    """Possède au plus une session et ne l'abandonne jamais avant enqueue."""

    def __init__(self) -> None:
        self.active: Any | None = None

    def add_audio(self, audio_bytes: bytes) -> bool:
        recorder = self.active
        if recorder is None or not getattr(recorder, "is_active", False):
            return False
        recorder.add_chunk(audio_bytes)
        return True

    async def handle_message(
        self,
        ws: WebSocket,
        msg: dict[str, Any],
        msg_type: str,
        *,
        conversation_id: int,
        stt_available: bool,
    ) -> bool:
        if msg_type not in {"recording_start", "recording_stop"}:
            return False

        if msg_type == "recording_start":
            if self.active is not None:
                await ws.send_json(
                    {
                        "type": "error",
                        "message": "Un enregistrement est déjà en cours.",
                    }
                )
                return True
            if not stt_available:
                await ws.send_json(
                    {
                        "type": "error",
                        "message": "STT local indisponible (moteur ou modèle absent).",
                    }
                )
                return True

            from audio.continuous_recorder import ContinuousRecording

            label = str(msg.get("label") or "Enregistrement").strip()[:200]
            recorder = ContinuousRecording(conversation_id)
            session_id = recorder.start(label)
            self.active = recorder
            logger.info("[WS] Écoute continue démarrée")
            await ws.send_json(
                {
                    "type": "recording_started",
                    "label": label,
                    "session_id": session_id,
                }
            )
            return True

        recorder = self.active
        if recorder is None:
            await ws.send_json(
                {"type": "error", "message": "Aucun enregistrement en cours."}
            )
            return True
        try:
            result = recorder.queue_for_processing()
        except Exception as exc:
            # Le spool scellé reste possédé par le contrôleur. Un second stop ou
            # la fermeture de la socket retentera l'enqueue idempotent.
            logger.exception("[WS] recording_stop : %s", exc)
            await ws.send_json(
                {
                    "type": "recording_done",
                    "result": {
                        "ok": False,
                        "error": "recording_enqueue_failed",
                        "label": getattr(recorder, "label", ""),
                        "retryable": True,
                    },
                }
            )
            return True

        self.active = None
        await ws.send_json({"type": "recording_done", "result": result})
        return True

    def close(self) -> None:
        recorder = self.active
        if recorder is None:
            return
        try:
            recorder.queue_for_processing()
        except Exception as exc:
            logger.error("Erreur enqueue recording à la déconnexion : %s", exc)
            return
        self.active = None
