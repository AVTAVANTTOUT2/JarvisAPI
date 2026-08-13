"""Scénarios de chaos déterministes : états typés, récupérables."""

from __future__ import annotations

from integrations.opencode.lifecycle.install import (
    ArchiveSecurityError,
    ChecksumMismatchError,
    TransientDownloadError,
    is_transient_download_error,
)
from jarvis.agentic.models import AgenticErrorCode, AgenticRunStatus
from scripts.jarvis_stack import RestartBlocked


def test_typed_failure_states_remain_distinct() -> None:
    assert AgenticRunStatus.CANCELLED is not AgenticRunStatus.FAILED
    assert AgenticRunStatus.PROVIDER_UNAVAILABLE is not AgenticRunStatus.FAILED
    assert AgenticRunStatus.CANCELLING is not AgenticRunStatus.CANCELLED
    assert AgenticErrorCode.RESOURCE_PRESSURE.value == "resource_pressure"
    assert AgenticErrorCode.CANCELLED.value == "cancelled"


def test_checksum_and_archive_errors_are_not_transient() -> None:
    assert is_transient_download_error(ChecksumMismatchError("checksum")) is False
    assert is_transient_download_error(ArchiveSecurityError("archive")) is False
    assert is_transient_download_error(TransientDownloadError("HTTP 503 transitoire")) is True


def test_supervisor_restart_blocked_is_typed() -> None:
    error = RestartBlocked("port occupé par un processus tiers")
    assert isinstance(error, RuntimeError)
    assert "tiers" in str(error)


def test_health_optional_components_never_make_jarvis_unavailable() -> None:
    from jarvis import health

    components = [
        health.ComponentHealth(name="backend", state=health.HEALTHY),
        health.ComponentHealth(name="database", state=health.HEALTHY),
        health.ComponentHealth(
            name="claw3d",
            state=health.UNKNOWN,
            reason="optional_ui_absent",
        ),
        health.ComponentHealth(
            name="agentic_plugin",
            state=health.UNKNOWN,
            reason="optional_runtime_absent",
        ),
    ]
    assert health.aggregate_state(components) == health.HEALTHY
