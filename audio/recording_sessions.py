"""Service local du protocole de capture audio longue reprenable."""

from __future__ import annotations

from typing import Any

import config
from audio.continuous_recorder import ContinuousRecording
from audio.recording_spool import (
    RECORDING_PROCESSING_MAX_ATTEMPTS,
    RECORDING_PROTOCOL_VERSION,
    RecordingSpool,
    RecordingSpoolError,
)
from database import get_recording_session


_NON_RETRYABLE_ERRORS = frozenset(
    {
        "recording_cancelled",
        "recording_duration_exceeded",
        "recording_too_short",
        "recording_chunk_too_large",
        "recording_container_invalid",
        "recording_container_unsupported",
        "recording_chunk_corrupt",
        "recording_manifest_corrupt",
        "recording_capture_expired",
        "recording_active_session_limit",
        "recording_profile_quota_exceeded",
        "recording_session_quota_exceeded",
    }
)


def _require_session(session_id: str):
    session = get_recording_session(str(session_id))
    if session is None:
        raise RecordingSpoolError(
            "recording_session_not_found",
            "Session d'enregistrement introuvable",
            status_code=404,
        )
    return session


def recording_session_status(session_id: str) -> dict[str, Any]:
    """Retourne un état public sans chemin local ni contenu audio."""

    session = _require_session(session_id)
    public_state = (
        "cancelled" if session.error == "recording_cancelled" else session.state
    )
    chunks = 0
    size_bytes = max(0, int(session.size_bytes))
    duration_ms = 0
    checksum = session.checksum
    if session.spool_path:
        try:
            spool = RecordingSpool.open(session.id)
        except FileNotFoundError:
            spool = None
        if spool is not None:
            chunks = spool.chunk_count
            size_bytes = spool.size_bytes
            duration_ms = spool.duration_ms
            checksum = spool.checksum
    retryable = (
        public_state in {"retry", "failed"}
        and session.error not in _NON_RETRYABLE_ERRORS
        and session.attempts < RECORDING_PROCESSING_MAX_ATTEMPTS
        and bool(session.spool_path)
    )
    return {
        "ok": True,
        "protocol_version": RECORDING_PROTOCOL_VERSION,
        "session_id": session.id,
        "state": public_state,
        "label": session.label,
        "next_sequence": chunks,
        "received_chunks": chunks,
        "size_bytes": size_bytes,
        "duration_ms": duration_ms,
        "duration_seconds": (duration_ms + 999) // 1000,
        "checksum": checksum,
        "attempts": session.attempts,
        "max_attempts": RECORDING_PROCESSING_MAX_ATTEMPTS,
        "retryable": retryable,
        "error_code": session.error,
    }


def start_recording_session(
    *,
    client_recording_id: str,
    conversation_id: int | None,
    label: str,
) -> dict[str, Any]:
    """Crée ou retrouve la même capture pour une clé client stable."""

    spool = RecordingSpool.create(
        conversation_id=conversation_id,
        label=label,
        client_recording_id=client_recording_id,
    )
    status = recording_session_status(spool.session_id)
    if status["state"] != "capturing":
        raise RecordingSpoolError(
            "recording_session_already_sealed",
            "Cette clé d'enregistrement désigne une session déjà scellée",
            context={"state": status["state"]},
        )
    return status


def complete_recording_session(
    session_id: str,
    *,
    expected_chunks: int,
    duration_seconds: int | None = None,
) -> dict[str, Any]:
    """Scelle une capture une seule fois et enfile un job borné."""

    session = _require_session(session_id)
    if session.error == "recording_cancelled":
        raise RecordingSpoolError(
            "recording_session_cancelled",
            "La session a été annulée",
        )
    if session.state in {"queued", "processing", "completed", "ready"}:
        return {
            **recording_session_status(session.id),
            "queued": session.state in {"queued", "processing"},
            "idempotent": True,
        }
    if session.state != "capturing":
        raise RecordingSpoolError(
            "recording_session_not_completable",
            "La session ne peut pas être clôturée dans cet état",
            context={"state": session.state},
        )
    recording = ContinuousRecording.from_spool(
        session.id,
        duration_seconds=duration_seconds,
    )
    result = recording.queue_for_processing(
        duration_seconds=duration_seconds,
        expected_chunks=expected_chunks,
    )
    if not result.get("ok"):
        code = str(result.get("error") or "recording_complete_failed")
        raise RecordingSpoolError(
            code if code.startswith("recording_") else "recording_complete_failed",
            "Impossible de clôturer cet enregistrement",
            status_code=422,
            context={
                key: result[key]
                for key in ("expected_chunks", "received_chunks")
                if key in result
            },
        )
    return {
        **recording_session_status(session.id),
        **result,
        "idempotent": bool(result.get("idempotent", False)),
    }


def cancel_recording_session(session_id: str) -> dict[str, Any]:
    """Annule de façon idempotente et détruit l'audio brut local."""

    session = _require_session(session_id)
    if session.error == "recording_cancelled" and not session.spool_path:
        return recording_session_status(session.id)
    if session.state in {"completed", "ready", "expired"}:
        raise RecordingSpoolError(
            "recording_session_not_cancellable",
            "Une session terminée ne peut plus être annulée",
            context={"state": session.state},
        )
    if session.state != "capturing" and session.error != "recording_cancelled":
        raise RecordingSpoolError(
            "recording_session_not_cancellable",
            "Seule une capture en cours peut être annulée",
            context={"state": session.state},
        )
    if not session.spool_path:
        raise RecordingSpoolError(
            "recording_spool_unavailable",
            "L'audio brut de cette session n'est plus disponible",
        )
    spool = RecordingSpool.open(session.id)
    spool.cancel_capture()
    return recording_session_status(session.id)


def retry_recording_session(session_id: str) -> dict[str, Any]:
    """Réenfile au plus trois essais de traitement pour une erreur transitoire."""

    session = _require_session(session_id)
    if (
        session.state not in {"retry", "failed"}
        or session.error in _NON_RETRYABLE_ERRORS
        or session.attempts >= RECORDING_PROCESSING_MAX_ATTEMPTS
        or not session.spool_path
    ):
        raise RecordingSpoolError(
            "recording_retry_not_allowed",
            "Aucun nouvel essai n'est autorisé pour cette session",
            context={
                "state": (
                    "cancelled"
                    if session.error == "recording_cancelled"
                    else session.state
                ),
                "attempts": session.attempts,
                "max_attempts": RECORDING_PROCESSING_MAX_ATTEMPTS,
            },
        )
    spool = RecordingSpool.open(session.id)
    duration_seconds = max(0, (spool.duration_ms + 999) // 1000)
    if duration_seconds > int(config.RECORDING_MAX_DURATION_MIN) * 60:
        raise RecordingSpoolError(
            "recording_duration_exceeded",
            "Durée maximale d'enregistrement dépassée",
            status_code=422,
        )
    spool.enqueue(label=session.label, duration_seconds=duration_seconds)
    return recording_session_status(session.id)


__all__ = [
    "cancel_recording_session",
    "complete_recording_session",
    "recording_session_status",
    "retry_recording_session",
    "start_recording_session",
]
