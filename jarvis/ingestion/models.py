"""Contrats communs de l'ingestion durable locale.

Le profil n'est jamais fourni par un appelant externe : il est capturé depuis
``database.core.current_profile_id`` puis recopié dans les objets persistés afin
que les workers multi-profils puissent vérifier leur frontière d'isolation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


REQUIRED_CONNECTOR_SOURCES: tuple[str, ...] = ("mail", "imessage", "calendar")
IngestionStatus = Literal["idle", "running", "degraded", "error", "disabled"]
Completeness = Literal["unknown", "partial", "complete"]
RunStatus = Literal["ok", "degraded", "unavailable"]


@dataclass(frozen=True, slots=True)
class ConnectorBinding:
    source: str
    profile_id: str
    connector_kind: str
    account_ref: str = "local"
    device_id_hash: str = ""
    external_account_hash: str = ""
    permission_state: str = "unknown"
    consent_source: str = "explicit"
    enabled: bool = True
    sync_interval_seconds: int = 300
    settings: dict[str, Any] = field(default_factory=dict)
    created_at: str | None = None
    updated_at: str | None = None


@dataclass(frozen=True, slots=True)
class IngestionSourceState:
    source: str
    profile_id: str
    status: IngestionStatus = "idle"
    cursor: dict[str, Any] = field(default_factory=dict)
    coverage_start_utc: str | None = None
    coverage_end_utc: str | None = None
    completeness: Completeness = "unknown"
    last_attempt_at: str | None = None
    last_success_at: str | None = None
    last_item_at: str | None = None
    item_count: int = 0
    heartbeat_at: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    consecutive_failures: int = 0
    generation: int = 0
    updated_at: str | None = None


@dataclass(frozen=True, slots=True)
class IngestionJob:
    id: int
    profile_id: str
    source: str
    job_kind: str
    dedupe_key: str
    payload: dict[str, Any] = field(default_factory=dict)
    status: str = "pending"
    attempts: int = 0
    max_attempts: int = 5
    available_at: str | None = None
    lease_token: str | None = None
    lease_owner: str | None = None
    lease_expires_at: str | None = None
    last_error_code: str | None = None
    last_error_message: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    completed_at: str | None = None


@dataclass(frozen=True, slots=True)
class IngestionRunResult:
    status: RunStatus
    item_count: int = 0
    cursor: dict[str, Any] = field(default_factory=dict)
    completeness: Completeness = "unknown"
    coverage_start_utc: str | None = None
    coverage_end_utc: str | None = None
    last_item_at: str | None = None
    error_code: str | None = None
    error_message: str | None = None


@dataclass(frozen=True, slots=True)
class RecordingSession:
    id: str
    profile_id: str
    conversation_id: int | None
    label: str
    state: str
    spool_path: str
    size_bytes: int
    checksum: str
    attempts: int
    error: str | None
    transcript: str | None
    summary: str | None
    desktop_notification_claimed_at: str | None
    retention_until: str | None
    created_at: str | None
    updated_at: str | None
