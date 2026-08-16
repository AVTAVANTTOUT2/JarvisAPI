"""Persistance profilée et transactionnelle des collecteurs locaux."""

from __future__ import annotations

import json
import hashlib
import re
import socket
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping, Sequence

from database import dbapi as sqlite3
from database.core import current_profile_id, get_connection, get_db
from database.time_buckets import sqlite_utc_timestamp
from jarvis.ingestion.models import (
    REQUIRED_CONNECTOR_SOURCES,
    ConnectorBinding,
    IngestionJob,
    IngestionSourceState,
    RecordingSession,
)


_SOURCE_RE = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")
_SECRET_SETTING_RE = re.compile(
    r"(?:password|passwd|token|secret|credential|api[_-]?key)", re.I
)
_UNSET = object()


class IngestionProfileMismatch(RuntimeError):
    """Une ligne persistée ne correspond pas au profil ContextVar actif."""


class ConnectorBindingRequired(RuntimeError):
    """Une source personnelle n'est pas explicitement liée au profil actif."""


def _source(value: str) -> str:
    normalized = str(value or "").strip().casefold()
    if normalized != "__service__" and not _SOURCE_RE.fullmatch(normalized):
        raise ValueError("ingestion_source_invalid")
    return normalized


def _json_dict(value: Mapping[str, Any] | None) -> str:
    return json.dumps(
        dict(value or {}), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def _decode_json(value: Any) -> dict[str, Any]:
    try:
        decoded = json.loads(str(value or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _assert_profile(row: Mapping[str, Any]) -> None:
    if str(row["profile_id"]) != current_profile_id():
        raise IngestionProfileMismatch("ingestion_profile_mismatch")


def _binding_digest(profile_id: str, value: str) -> str:
    return hashlib.sha256(f"{profile_id}:{value}".encode()).hexdigest()


def _current_device_digest(profile_id: str) -> str:
    return _binding_digest(profile_id, str(socket.gethostname() or "local"))


def _binding_is_authorized(binding: ConnectorBinding) -> bool:
    return bool(
        binding.enabled
        and binding.permission_state != "denied"
        and binding.device_id_hash
        and binding.device_id_hash == _current_device_digest(binding.profile_id)
    )


def connector_binding_allows_external_account(
    binding: ConnectorBinding, external_account_id: str | None
) -> bool:
    """Vérifie un compte lorsque le connecteur fournit un identifiant fiable."""

    if not _binding_is_authorized(binding):
        return False
    local_hash = _binding_digest(binding.profile_id, "local")
    if binding.external_account_hash == local_hash and binding.account_ref == "local":
        return True
    candidate = str(external_account_id or "").strip()
    return bool(
        candidate
        and _binding_digest(binding.profile_id, candidate)
        == binding.external_account_hash
    )


def _merge_sync_payload(
    existing: Mapping[str, Any], incoming: Mapping[str, Any] | None
) -> dict[str, Any]:
    """Fusionne une enveloppe temporelle sans perdre une demande déjà durable."""

    merged = dict(existing)
    new_payload = dict(incoming or {})
    merged.update(
        {key: value for key, value in new_payload.items() if value is not None}
    )

    def parsed(value: Any) -> datetime | None:
        raw = str(value or "").strip()
        if not raw:
            return None
        try:
            parsed_value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed_value.tzinfo is None:
            parsed_value = parsed_value.replace(tzinfo=timezone.utc)
        return parsed_value.astimezone(timezone.utc)

    for key, choose in (("from_iso", min), ("to_iso", max)):
        candidates = [
            value
            for value in (existing.get(key), new_payload.get(key))
            if parsed(value) is not None
        ]
        if candidates:
            merged[key] = choose(
                candidates,
                key=lambda value: (
                    parsed(value) or datetime.min.replace(tzinfo=timezone.utc)
                ),
            )
    if existing and new_payload and existing != new_payload:
        merged["reason"] = "coalesced"
    return merged


def _binding_from_row(row: Mapping[str, Any]) -> ConnectorBinding:
    _assert_profile(row)
    return ConnectorBinding(
        source=str(row["source"]),
        profile_id=str(row["profile_id"]),
        connector_kind=str(row["connector_kind"]),
        account_ref=str(row["account_ref"]),
        device_id_hash=str(row["device_id_hash"] or ""),
        external_account_hash=str(row["external_account_hash"] or ""),
        permission_state=str(row["permission_state"] or "unknown"),
        consent_source=str(row["consent_source"]),
        enabled=bool(row["enabled"]),
        sync_interval_seconds=int(row["sync_interval_seconds"]),
        settings=_decode_json(row["settings_json"]),
        created_at=str(row["created_at"]) if row["created_at"] else None,
        updated_at=str(row["updated_at"]) if row["updated_at"] else None,
    )


def _state_from_row(row: Mapping[str, Any]) -> IngestionSourceState:
    _assert_profile(row)
    return IngestionSourceState(
        source=str(row["source"]),
        profile_id=str(row["profile_id"]),
        status=str(row["status"]),  # type: ignore[arg-type]
        cursor=_decode_json(row["cursor_json"]),
        coverage_start_utc=row["coverage_start_utc"],
        coverage_end_utc=row["coverage_end_utc"],
        completeness=str(row["completeness"]),  # type: ignore[arg-type]
        last_attempt_at=row["last_attempt_at"],
        last_success_at=row["last_success_at"],
        last_item_at=row["last_item_at"],
        item_count=int(row["item_count"]),
        heartbeat_at=row["heartbeat_at"],
        error_code=row["error_code"],
        error_message=row["error_message"],
        consecutive_failures=int(row["consecutive_failures"]),
        generation=int(row["generation"]),
        updated_at=row["updated_at"],
    )


def _job_from_row(row: Mapping[str, Any]) -> IngestionJob:
    _assert_profile(row)
    return IngestionJob(
        id=int(row["id"]),
        profile_id=str(row["profile_id"]),
        source=str(row["source"]),
        job_kind=str(row["job_kind"]),
        dedupe_key=str(row["dedupe_key"]),
        payload=_decode_json(row["payload_json"]),
        status=str(row["status"]),
        attempts=int(row["attempts"]),
        max_attempts=int(row["max_attempts"]),
        available_at=row["available_at"],
        lease_token=row["lease_token"],
        lease_owner=row["lease_owner"],
        lease_expires_at=row["lease_expires_at"],
        last_error_code=row["last_error_code"],
        last_error_message=row["last_error_message"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        completed_at=row["completed_at"],
    )


def _recording_from_row(row: Mapping[str, Any]) -> RecordingSession:
    _assert_profile(row)
    return RecordingSession(
        id=str(row["id"]),
        profile_id=str(row["profile_id"]),
        conversation_id=int(row["conversation_id"])
        if row["conversation_id"] is not None
        else None,
        label=str(row["label"] or ""),
        state=str(row["state"]),
        spool_path=str(row["spool_path"]),
        size_bytes=int(row["size_bytes"]),
        checksum=str(row["checksum"] or ""),
        attempts=int(row["attempts"]),
        error=row["error"],
        transcript=row["transcript"],
        summary=row["summary"],
        desktop_notification_claimed_at=row["desktop_notification_claimed_at"],
        retention_until=row["retention_until"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def bind_connector(
    source: str,
    *,
    connector_kind: str | None = None,
    account_ref: str = "local",
    device_id: str | None = None,
    external_account_id: str | None = None,
    permission_state: str = "unknown",
    consent_source: str = "explicit",
    enabled: bool = True,
    sync_interval_seconds: int = 300,
    settings: Mapping[str, Any] | None = None,
) -> ConnectorBinding:
    """Lie une source au profil actif, sans accepter de profil en argument."""

    source_key = _source(source)
    kind = _source(connector_kind or source_key)
    interval = max(15, min(86_400, int(sync_interval_seconds)))
    settings_dict = dict(settings or {})
    if any(_SECRET_SETTING_RE.search(str(key)) for key in settings_dict):
        raise ValueError("connector_settings_must_not_contain_secrets")
    now = sqlite_utc_timestamp()
    profile_id = current_profile_id()
    permission = str(permission_state or "unknown").strip().lower()
    if permission not in {"unknown", "granted", "denied"}:
        raise ValueError("connector_permission_state_invalid")

    device_hash = _binding_digest(
        profile_id, str(device_id or socket.gethostname() or "local")
    )
    account_hash = _binding_digest(
        profile_id, str(external_account_id or account_ref or "local")
    )
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO connector_bindings(
                source, profile_id, connector_kind, account_ref, device_id_hash,
                external_account_hash, permission_state, consent_source, enabled,
                sync_interval_seconds, settings_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source) DO UPDATE SET
                profile_id = excluded.profile_id,
                connector_kind = excluded.connector_kind,
                account_ref = excluded.account_ref,
                device_id_hash = excluded.device_id_hash,
                external_account_hash = excluded.external_account_hash,
                permission_state = excluded.permission_state,
                consent_source = excluded.consent_source,
                enabled = excluded.enabled,
                sync_interval_seconds = excluded.sync_interval_seconds,
                settings_json = excluded.settings_json,
                updated_at = excluded.updated_at
            """,
            (
                source_key,
                profile_id,
                kind,
                str(account_ref or "local")[:256],
                device_hash,
                account_hash,
                permission,
                str(consent_source or "explicit")[:128],
                int(enabled),
                interval,
                _json_dict(settings_dict),
                now,
                now,
            ),
        )
        row = conn.execute(
            "SELECT * FROM connector_bindings WHERE source = ?", (source_key,)
        ).fetchone()
    assert row is not None
    return _binding_from_row(row)


def update_connector_permission(source: str, permission_state: str) -> ConnectorBinding:
    source_key = _source(source)
    permission = str(permission_state or "").strip().lower()
    if permission not in {"unknown", "granted", "denied"}:
        raise ValueError("connector_permission_state_invalid")
    with get_db() as conn:
        conn.execute(
            """UPDATE connector_bindings
               SET permission_state = ?, updated_at = ?
               WHERE source = ? AND profile_id = ?""",
            (
                permission,
                sqlite_utc_timestamp(),
                source_key,
                current_profile_id(),
            ),
        )
        row = conn.execute(
            "SELECT * FROM connector_bindings WHERE source = ?",
            (source_key,),
        ).fetchone()
    if row is None:
        raise ConnectorBindingRequired(f"connector_unbound:{source_key}")
    return _binding_from_row(row)


def get_connector_binding(
    source: str, *, include_disabled: bool = False
) -> ConnectorBinding | None:
    source_key = _source(source)
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM connector_bindings WHERE source = ?", (source_key,)
        ).fetchone()
    if row is None:
        return None
    binding = _binding_from_row(row)
    return binding if include_disabled or _binding_is_authorized(binding) else None


def list_connector_bindings(
    *, include_disabled: bool = False
) -> list[ConnectorBinding]:
    where = "" if include_disabled else " WHERE enabled = 1"
    with get_db() as conn:
        rows = conn.execute(
            f"SELECT * FROM connector_bindings{where} ORDER BY source"  # noqa: S608
        ).fetchall()
    bindings = [_binding_from_row(row) for row in rows]
    if include_disabled:
        return bindings
    return [binding for binding in bindings if _binding_is_authorized(binding)]


def unbind_connector(source: str) -> bool:
    source_key = _source(source)
    with get_db() as conn:
        row = conn.execute(
            "SELECT profile_id FROM connector_bindings WHERE source = ?", (source_key,)
        ).fetchone()
        if row is None:
            return False
        _assert_profile(row)
        cursor = conn.execute(
            "DELETE FROM connector_bindings WHERE source = ?", (source_key,)
        )
    return bool(cursor.rowcount)


def connector_binding_health(
    required_sources: Iterable[str] = REQUIRED_CONNECTOR_SOURCES,
) -> dict[str, list[str]]:
    required = {_source(source) for source in required_sources}
    connector_bindings = list_connector_bindings()
    bindings = {binding.source for binding in connector_bindings}
    granted = {
        binding.source
        for binding in connector_bindings
        if binding.permission_state == "granted"
    }
    return {
        "bound": sorted(required & bindings),
        "unbound": sorted(required - bindings),
        "permission_granted": sorted(required & granted),
        "permission_missing": sorted((required & bindings) - granted),
    }


def get_ingestion_source_state(source: str) -> IngestionSourceState | None:
    source_key = _source(source)
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM ingestion_source_state WHERE source = ?", (source_key,)
        ).fetchone()
    return _state_from_row(row) if row else None


def list_ingestion_source_states() -> list[IngestionSourceState]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM ingestion_source_state ORDER BY source"
        ).fetchall()
    return [_state_from_row(row) for row in rows]


def update_ingestion_source_state(
    source: str,
    *,
    status: str | object = _UNSET,
    cursor: Mapping[str, Any] | object = _UNSET,
    coverage_start_utc: str | None | object = _UNSET,
    coverage_end_utc: str | None | object = _UNSET,
    completeness: str | object = _UNSET,
    last_attempt_at: str | None | object = _UNSET,
    last_success_at: str | None | object = _UNSET,
    last_item_at: str | None | object = _UNSET,
    item_count: int | object = _UNSET,
    heartbeat_at: str | None | object = _UNSET,
    error_code: str | None | object = _UNSET,
    error_message: str | None | object = _UNSET,
    consecutive_failures: int | object = _UNSET,
    increment_generation: bool = False,
) -> IngestionSourceState:
    source_key = _source(source)
    existing = get_ingestion_source_state(source_key)
    now = sqlite_utc_timestamp()
    profile_id = current_profile_id()

    def choose(value: Any, previous: Any) -> Any:
        return previous if value is _UNSET else value

    status_value = str(choose(status, existing.status if existing else "idle"))
    if status_value not in {"idle", "running", "degraded", "error", "disabled"}:
        raise ValueError("ingestion_status_invalid")
    completeness_value = str(
        choose(completeness, existing.completeness if existing else "unknown")
    )
    if completeness_value not in {"unknown", "partial", "complete"}:
        raise ValueError("ingestion_completeness_invalid")
    cursor_value = choose(cursor, existing.cursor if existing else {})
    failures = int(
        choose(consecutive_failures, existing.consecutive_failures if existing else 0)
    )
    generation = (existing.generation if existing else 0) + int(increment_generation)
    values = (
        source_key,
        profile_id,
        status_value,
        _json_dict(cursor_value),
        choose(coverage_start_utc, existing.coverage_start_utc if existing else None),
        choose(coverage_end_utc, existing.coverage_end_utc if existing else None),
        completeness_value,
        choose(last_attempt_at, existing.last_attempt_at if existing else None),
        choose(last_success_at, existing.last_success_at if existing else None),
        choose(last_item_at, existing.last_item_at if existing else None),
        max(0, int(choose(item_count, existing.item_count if existing else 0))),
        choose(heartbeat_at, existing.heartbeat_at if existing else None),
        choose(error_code, existing.error_code if existing else None),
        str(choose(error_message, existing.error_message if existing else None))[:1000]
        if choose(error_message, existing.error_message if existing else None)
        is not None
        else None,
        max(0, failures),
        generation,
        now,
    )
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO ingestion_source_state(
                source, profile_id, status, cursor_json, coverage_start_utc,
                coverage_end_utc, completeness, last_attempt_at,
                last_success_at, last_item_at, item_count, heartbeat_at, error_code,
                error_message, consecutive_failures, generation, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source) DO UPDATE SET
                profile_id = excluded.profile_id,
                status = excluded.status,
                cursor_json = excluded.cursor_json,
                coverage_start_utc = excluded.coverage_start_utc,
                coverage_end_utc = excluded.coverage_end_utc,
                completeness = excluded.completeness,
                last_attempt_at = excluded.last_attempt_at,
                last_success_at = excluded.last_success_at,
                last_item_at = excluded.last_item_at,
                item_count = excluded.item_count,
                heartbeat_at = excluded.heartbeat_at,
                error_code = excluded.error_code,
                error_message = excluded.error_message,
                consecutive_failures = excluded.consecutive_failures,
                generation = excluded.generation,
                updated_at = excluded.updated_at
            """,
            values,
        )
        row = conn.execute(
            "SELECT * FROM ingestion_source_state WHERE source = ?", (source_key,)
        ).fetchone()
    assert row is not None
    return _state_from_row(row)


def touch_ingestion_heartbeat(source: str = "__service__") -> IngestionSourceState:
    now = sqlite_utc_timestamp()
    return update_ingestion_source_state(
        source,
        status="running",
        heartbeat_at=now,
        error_code=None,
        error_message=None,
    )


def enqueue_ingestion_job(
    source: str,
    *,
    job_kind: str = "sync",
    payload: Mapping[str, Any] | None = None,
    dedupe_key: str | None = None,
    max_attempts: int = 5,
    available_at: str | None = None,
    require_binding: bool | None = None,
    mark_source_pending: bool = False,
) -> IngestionJob:
    source_key = _source(source)
    kind = _source(job_kind)
    if require_binding is None:
        require_binding = source_key in REQUIRED_CONNECTOR_SOURCES
    if require_binding and get_connector_binding(source_key) is None:
        raise ConnectorBindingRequired(f"connector_unbound:{source_key}")
    profile_id = current_profile_id()
    now = sqlite_utc_timestamp()
    due = available_at or now
    key = str(dedupe_key or f"{kind}:periodic")[:256]
    with get_db() as conn:
        if mark_source_pending:
            conn.execute(
                """
                UPDATE ingestion_source_state
                SET status = 'degraded', completeness = 'partial',
                    error_code = 'freshness_pending', error_message = NULL,
                    updated_at = ?
                WHERE source = ? AND profile_id = ?
                """,
                (now, source_key, profile_id),
            )
        try:
            cursor = conn.execute(
                """
                INSERT INTO ingestion_jobs(
                    profile_id, source, job_kind, dedupe_key, payload_json,
                    max_attempts, available_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    profile_id,
                    source_key,
                    kind,
                    key,
                    _json_dict(payload),
                    max(1, min(50, int(max_attempts))),
                    due,
                    now,
                    now,
                ),
            )
            job_id = int(cursor.lastrowid)
        except sqlite3.IntegrityError:
            row = conn.execute(
                """
                SELECT * FROM ingestion_jobs
                WHERE profile_id = ? AND source = ? AND job_kind = ?
                  AND dedupe_key = ?
                  AND status IN ('pending', 'running', 'retry')
                ORDER BY id DESC LIMIT 1
                """,
                (profile_id, source_key, kind, key),
            ).fetchone()
            if row is None:
                raise
            existing = _job_from_row(row)
            merged_payload = (
                _merge_sync_payload(existing.payload, payload)
                if kind == "sync"
                else existing.payload
            )
            if existing.status in {"pending", "retry"}:
                conn.execute(
                    """
                    UPDATE ingestion_jobs
                    SET payload_json = ?, available_at = MIN(available_at, ?),
                        updated_at = ?
                    WHERE id = ? AND profile_id = ?
                      AND status IN ('pending', 'retry')
                    """,
                    (_json_dict(merged_payload), due, now, existing.id, profile_id),
                )
                refreshed = conn.execute(
                    "SELECT * FROM ingestion_jobs WHERE id = ?", (existing.id,)
                ).fetchone()
                assert refreshed is not None
                return _job_from_row(refreshed)

            requested_window = bool(
                payload and (payload.get("from_iso") or payload.get("to_iso"))
            )
            if kind != "sync" or not requested_window:
                return existing

            followup = conn.execute(
                """
                SELECT * FROM ingestion_jobs
                WHERE profile_id = ? AND source = ? AND job_kind = ?
                  AND dedupe_key LIKE ? AND status IN ('pending', 'retry')
                ORDER BY id DESC LIMIT 1
                """,
                (profile_id, source_key, kind, f"{key}:followup%"),
            ).fetchone()
            if followup is not None:
                followup_payload = _merge_sync_payload(
                    _decode_json(followup["payload_json"]), payload
                )
                conn.execute(
                    """
                    UPDATE ingestion_jobs
                    SET payload_json = ?, available_at = MIN(available_at, ?),
                        updated_at = ?
                    WHERE id = ? AND profile_id = ?
                      AND status IN ('pending', 'retry')
                    """,
                    (
                        _json_dict(followup_payload),
                        due,
                        now,
                        int(followup["id"]),
                        profile_id,
                    ),
                )
                refreshed = conn.execute(
                    "SELECT * FROM ingestion_jobs WHERE id = ?",
                    (int(followup["id"]),),
                ).fetchone()
                assert refreshed is not None
                return _job_from_row(refreshed)

            followup_key = f"{key}:followup"
            running_followup = conn.execute(
                """
                SELECT 1 FROM ingestion_jobs
                WHERE profile_id = ? AND source = ? AND job_kind = ?
                  AND dedupe_key = ? AND status = 'running'
                """,
                (profile_id, source_key, kind, followup_key),
            ).fetchone()
            if running_followup is not None:
                followup_key = f"{followup_key}:{uuid.uuid4().hex[:12]}"
            cursor = conn.execute(
                """
                INSERT INTO ingestion_jobs(
                    profile_id, source, job_kind, dedupe_key, payload_json,
                    max_attempts, available_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    profile_id,
                    source_key,
                    kind,
                    followup_key,
                    _json_dict(payload),
                    max(1, min(50, int(max_attempts))),
                    due,
                    now,
                    now,
                ),
            )
            followup_row = conn.execute(
                "SELECT * FROM ingestion_jobs WHERE id = ?", (int(cursor.lastrowid),)
            ).fetchone()
            assert followup_row is not None
            return _job_from_row(followup_row)
        row = conn.execute(
            "SELECT * FROM ingestion_jobs WHERE id = ?", (job_id,)
        ).fetchone()
    assert row is not None
    return _job_from_row(row)


def claim_ingestion_jobs(
    worker_id: str,
    *,
    lease_seconds: int = 120,
    limit: int = 10,
    sources: Sequence[str] | None = None,
    job_kinds: Sequence[str] | None = None,
    handler_pairs: Sequence[tuple[str, str]] | None = None,
) -> list[IngestionJob]:
    """Claim atomique avec récupération des leases expirés et fencing token."""

    profile_id = current_profile_id()
    now = sqlite_utc_timestamp()
    expires = sqlite_utc_timestamp(
        datetime.now(timezone.utc) + timedelta(seconds=max(15, int(lease_seconds)))
    )
    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            """
            UPDATE ingestion_jobs
            SET status = CASE
                    WHEN attempts >= max_attempts THEN 'dead'
                    ELSE 'retry'
                END,
                completed_at = CASE
                    WHEN attempts >= max_attempts THEN ?
                    ELSE completed_at
                END,
                lease_token = NULL, lease_owner = NULL,
                lease_expires_at = NULL, available_at = ?, updated_at = ?
            WHERE profile_id = ? AND status = 'running'
              AND lease_expires_at IS NOT NULL AND lease_expires_at <= ?
            """,
            (now, now, now, profile_id, now),
        )
        clauses = [
            "profile_id = ?",
            "status IN ('pending', 'retry')",
            "available_at <= ?",
            "attempts < max_attempts",
        ]
        params: list[Any] = [profile_id, now]
        if sources:
            normalized_sources = [_source(source) for source in sources]
            clauses.append(
                "source IN (" + ",".join("?" for _ in normalized_sources) + ")"
            )
            params.extend(normalized_sources)
        if job_kinds:
            normalized_kinds = [_source(kind) for kind in job_kinds]
            clauses.append(
                "job_kind IN (" + ",".join("?" for _ in normalized_kinds) + ")"
            )
            params.extend(normalized_kinds)
        if handler_pairs:
            normalized_pairs = [
                (_source(source), _source(kind)) for source, kind in handler_pairs
            ]
            clauses.append(
                "("
                + " OR ".join("(source = ? AND job_kind = ?)" for _ in normalized_pairs)
                + ")"
            )
            for source_key, kind_key in normalized_pairs:
                params.extend((source_key, kind_key))
        rows = conn.execute(
            "SELECT id FROM ingestion_jobs WHERE "
            + " AND ".join(clauses)
            + " ORDER BY available_at, id LIMIT ?",
            (*params, max(1, min(100, int(limit)))),
        ).fetchall()
        claimed: list[IngestionJob] = []
        for selected in rows:
            token = uuid.uuid4().hex
            changed = conn.execute(
                """
                UPDATE ingestion_jobs
                SET status = 'running', attempts = attempts + 1,
                    lease_token = ?, lease_owner = ?, lease_expires_at = ?,
                    updated_at = ?
                WHERE id = ? AND profile_id = ?
                  AND status IN ('pending', 'retry')
                """,
                (
                    token,
                    str(worker_id)[:128],
                    expires,
                    now,
                    int(selected["id"]),
                    profile_id,
                ),
            )
            if not changed.rowcount:
                continue
            row = conn.execute(
                "SELECT * FROM ingestion_jobs WHERE id = ?", (int(selected["id"]),)
            ).fetchone()
            if row:
                claimed.append(_job_from_row(row))
        conn.commit()
        return claimed
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def complete_ingestion_job(job_id: int, lease_token: str) -> bool:
    now = sqlite_utc_timestamp()
    with get_db() as conn:
        cursor = conn.execute(
            """
            UPDATE ingestion_jobs
            SET status = 'done', completed_at = ?, updated_at = ?,
                lease_token = NULL, lease_owner = NULL, lease_expires_at = NULL,
                last_error_code = NULL, last_error_message = NULL
            WHERE id = ? AND profile_id = ? AND status = 'running'
              AND lease_token = ?
            """,
            (now, now, int(job_id), current_profile_id(), str(lease_token)),
        )
    return bool(cursor.rowcount)


def renew_ingestion_job_lease(
    job_id: int,
    lease_token: str,
    *,
    lease_seconds: int = 120,
) -> bool:
    """Prolonge un claim courant sans permettre à un ancien worker de le voler."""

    now = sqlite_utc_timestamp()
    expires = sqlite_utc_timestamp(
        datetime.now(timezone.utc) + timedelta(seconds=max(15, int(lease_seconds)))
    )
    with get_db() as conn:
        cursor = conn.execute(
            """
            UPDATE ingestion_jobs
            SET lease_expires_at = ?, updated_at = ?
            WHERE id = ? AND profile_id = ? AND status = 'running'
              AND lease_token = ?
            """,
            (
                expires,
                now,
                int(job_id),
                current_profile_id(),
                str(lease_token),
            ),
        )
    return bool(cursor.rowcount)


def fail_ingestion_job(
    job_id: int,
    lease_token: str,
    *,
    error_code: str,
    error_message: str = "",
    retry_delay_seconds: int | None = None,
) -> bool:
    now_dt = datetime.now(timezone.utc)
    now = sqlite_utc_timestamp(now_dt)
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT attempts, max_attempts FROM ingestion_jobs
            WHERE id = ? AND profile_id = ? AND status = 'running'
              AND lease_token = ?
            """,
            (int(job_id), current_profile_id(), str(lease_token)),
        ).fetchone()
        if row is None:
            return False
        dead = int(row["attempts"]) >= int(row["max_attempts"])
        delay = retry_delay_seconds
        if delay is None:
            delay = min(3600, 2 ** min(int(row["attempts"]), 10))
        available = sqlite_utc_timestamp(now_dt + timedelta(seconds=max(1, int(delay))))
        cursor = conn.execute(
            """
            UPDATE ingestion_jobs
            SET status = ?, available_at = ?, updated_at = ?,
                lease_token = NULL, lease_owner = NULL, lease_expires_at = NULL,
                last_error_code = ?, last_error_message = ?
            WHERE id = ? AND profile_id = ? AND status = 'running'
              AND lease_token = ?
            """,
            (
                "dead" if dead else "retry",
                available,
                now,
                str(error_code)[:128],
                str(error_message)[:1000],
                int(job_id),
                current_profile_id(),
                str(lease_token),
            ),
        )
    return bool(cursor.rowcount)


def list_ingestion_jobs(
    *, status: str | None = None, source: str | None = None, limit: int = 100
) -> list[IngestionJob]:
    clauses = ["profile_id = ?"]
    params: list[Any] = [current_profile_id()]
    if status:
        clauses.append("status = ?")
        params.append(str(status))
    if source:
        clauses.append("source = ?")
        params.append(_source(source))
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM ingestion_jobs WHERE "
            + " AND ".join(clauses)
            + " ORDER BY id DESC LIMIT ?",
            (*params, max(1, min(1000, int(limit)))),
        ).fetchall()
    return [_job_from_row(row) for row in rows]


def get_ingestion_health_summary(
    required_sources: Iterable[str] = REQUIRED_CONNECTOR_SOURCES,
) -> dict[str, Any]:
    """Vue agrégée sans payload, curseur, contenu ni message d'erreur."""

    profile_id = current_profile_id()
    with get_db() as conn:
        job_rows = conn.execute(
            """
            SELECT source, status, COUNT(*) AS count,
                   MIN(created_at) AS oldest_created_at,
                   MAX(updated_at) AS latest_updated_at
            FROM ingestion_jobs WHERE profile_id = ?
            GROUP BY source, status ORDER BY source, status
            """,
            (profile_id,),
        ).fetchall()
        state_rows = conn.execute(
            """
            SELECT source, status, completeness, coverage_start_utc,
                   coverage_end_utc, last_attempt_at, last_success_at,
                   last_item_at, item_count, heartbeat_at, error_code,
                   consecutive_failures, generation, updated_at
            FROM ingestion_source_state WHERE profile_id = ? ORDER BY source
            """,
            (profile_id,),
        ).fetchall()
    return {
        "profile_id": profile_id,
        "bindings": connector_binding_health(required_sources),
        "jobs": [dict(row) for row in job_rows],
        "states": [dict(row) for row in state_rows],
    }


def normalize_contact_identity(identity_type: str, value: str) -> tuple[str, str]:
    kind = str(identity_type or "handle").strip().casefold()
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("contact_identity_empty")
    if kind == "email" or "@" in raw:
        return "email", raw.strip("<>").casefold()
    phone = re.sub(r"[^0-9+]", "", raw)
    if kind in {"phone", "imessage"} and any(ch.isdigit() for ch in phone):
        if phone.startswith("00"):
            phone = "+" + phone[2:]
        return "phone", phone
    if kind not in {"handle", "imessage"}:
        raise ValueError("contact_identity_type_invalid")
    return kind, raw.casefold()


def upsert_contact_identity(
    identity_type: str,
    value: str,
    *,
    display_name: str = "",
    source: str = "",
    person_id: int | None = None,
    confidence: float = 1.0,
) -> dict[str, Any]:
    kind, normalized = normalize_contact_identity(identity_type, value)
    name = " ".join(str(display_name or "").split())[:500]
    with get_db() as conn:
        linked_person = person_id
        if linked_person is None and name:
            person = conn.execute(
                "SELECT id FROM people WHERE name = ? COLLATE NOCASE LIMIT 1", (name,)
            ).fetchone()
            linked_person = int(person["id"]) if person else None
        conn.execute(
            """
            INSERT INTO contact_identities(
                identity_type, normalized_value, display_name, person_id,
                source, confidence, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(identity_type, normalized_value) DO UPDATE SET
                display_name = CASE
                    WHEN excluded.display_name <> '' THEN excluded.display_name
                    ELSE contact_identities.display_name END,
                person_id = COALESCE(excluded.person_id, contact_identities.person_id),
                source = CASE WHEN excluded.source <> '' THEN excluded.source ELSE contact_identities.source END,
                confidence = MAX(contact_identities.confidence, excluded.confidence),
                updated_at = excluded.updated_at
            """,
            (
                kind,
                normalized,
                name,
                linked_person,
                str(source or "")[:64],
                max(0.0, min(1.0, float(confidence))),
                sqlite_utc_timestamp(),
                sqlite_utc_timestamp(),
            ),
        )
        row = conn.execute(
            "SELECT * FROM contact_identities WHERE identity_type = ? AND normalized_value = ?",
            (kind, normalized),
        ).fetchone()
    assert row is not None
    return dict(row)


def get_contact_identity(identity_type: str, value: str) -> dict[str, Any] | None:
    kind, normalized = normalize_contact_identity(identity_type, value)
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM contact_identities WHERE identity_type = ? AND normalized_value = ?",
            (kind, normalized),
        ).fetchone()
    return dict(row) if row else None


def create_recording_session(
    *,
    spool_path: str,
    session_id: str | None = None,
    conversation_id: int | None = None,
    label: str = "",
    state: str = "capturing",
    size_bytes: int = 0,
    checksum: str = "",
    retention_until: str | None = None,
) -> RecordingSession:
    if not str(spool_path or "").strip():
        raise ValueError("recording_spool_path_required")
    record_id = str(session_id or uuid.uuid4())
    now = sqlite_utc_timestamp()
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO recording_sessions(
                id, profile_id, conversation_id, label, state, spool_path,
                size_bytes, checksum, retention_until, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record_id,
                current_profile_id(),
                conversation_id,
                str(label or "")[:500],
                str(state),
                str(spool_path),
                max(0, int(size_bytes)),
                str(checksum or "")[:256],
                retention_until,
                now,
                now,
            ),
        )
        row = conn.execute(
            "SELECT * FROM recording_sessions WHERE id = ?", (record_id,)
        ).fetchone()
    assert row is not None
    return _recording_from_row(row)


def get_recording_session(session_id: str) -> RecordingSession | None:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM recording_sessions WHERE id = ? AND profile_id = ?",
            (str(session_id), current_profile_id()),
        ).fetchone()
    return _recording_from_row(row) if row else None


def update_recording_session(
    session_id: str, **changes: Any
) -> RecordingSession | None:
    allowed = {
        "conversation_id",
        "label",
        "state",
        "spool_path",
        "size_bytes",
        "checksum",
        "attempts",
        "error",
        "transcript",
        "summary",
        "desktop_notification_claimed_at",
        "retention_until",
    }
    unknown = set(changes) - allowed
    if unknown:
        raise ValueError(
            "recording_session_fields_invalid:" + ",".join(sorted(unknown))
        )
    if not changes:
        return get_recording_session(session_id)
    normalized = dict(changes)
    if "size_bytes" in normalized:
        normalized["size_bytes"] = max(0, int(normalized["size_bytes"]))
    if "attempts" in normalized:
        normalized["attempts"] = max(0, int(normalized["attempts"]))
    normalized["updated_at"] = sqlite_utc_timestamp()
    assignments = ", ".join(f"{name} = ?" for name in normalized)
    with get_db() as conn:
        conn.execute(
            f"UPDATE recording_sessions SET {assignments} WHERE id = ? AND profile_id = ?",  # noqa: S608
            (*normalized.values(), str(session_id), current_profile_id()),
        )
        row = conn.execute(
            "SELECT * FROM recording_sessions WHERE id = ? AND profile_id = ?",
            (str(session_id), current_profile_id()),
        ).fetchone()
    return _recording_from_row(row) if row else None


def claim_recording_desktop_notification(session_id: str) -> bool:
    """Réserve atomiquement l'unique notification macOS d'une session."""

    with get_db() as conn:
        cursor = conn.execute(
            """
            UPDATE recording_sessions
            SET desktop_notification_claimed_at = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND profile_id = ?
              AND desktop_notification_claimed_at IS NULL
            """,
            (sqlite_utc_timestamp(), str(session_id), current_profile_id()),
        )
    return cursor.rowcount == 1


def list_pending_recording_sessions(
    *,
    states: Sequence[str] = ("queued", "retry", "partial"),
    due_before: str | None = None,
    limit: int = 100,
) -> list[RecordingSession]:
    if not states:
        return []
    before = due_before or sqlite_utc_timestamp()
    placeholders = ",".join("?" for _ in states)
    with get_db() as conn:
        rows = conn.execute(
            f"""
            SELECT * FROM recording_sessions
            WHERE profile_id = ? AND state IN ({placeholders})
              AND updated_at <= ?
              AND (retention_until IS NULL OR retention_until > ?)
            ORDER BY updated_at, id LIMIT ?
            """,  # noqa: S608
            (
                current_profile_id(),
                *[str(state) for state in states],
                before,
                sqlite_utc_timestamp(),
                max(1, min(1000, int(limit))),
            ),
        ).fetchall()
    return [_recording_from_row(row) for row in rows]


def list_due_recording_sessions(
    *,
    states: Sequence[str] = ("queued", "retry", "partial"),
    due_before: str | None = None,
    limit: int = 100,
) -> list[RecordingSession]:
    """Alias historique : les sessions dues sont les sessions à traiter."""

    return list_pending_recording_sessions(
        states=states,
        due_before=due_before,
        limit=limit,
    )


def list_expired_recording_sessions(
    *, expired_before: str | None = None, limit: int = 100
) -> list[RecordingSession]:
    before = expired_before or sqlite_utc_timestamp()
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT * FROM recording_sessions
            WHERE profile_id = ? AND retention_until IS NOT NULL
              AND retention_until <= ?
            ORDER BY retention_until, id LIMIT ?
            """,
            (current_profile_id(), before, max(1, min(1000, int(limit)))),
        ).fetchall()
    return [_recording_from_row(row) for row in rows]


__all__ = [
    "ConnectorBindingRequired",
    "IngestionProfileMismatch",
    "bind_connector",
    "claim_ingestion_jobs",
    "claim_recording_desktop_notification",
    "complete_ingestion_job",
    "connector_binding_allows_external_account",
    "connector_binding_health",
    "create_recording_session",
    "enqueue_ingestion_job",
    "fail_ingestion_job",
    "get_connector_binding",
    "get_contact_identity",
    "get_ingestion_source_state",
    "get_ingestion_health_summary",
    "get_recording_session",
    "list_connector_bindings",
    "list_due_recording_sessions",
    "list_expired_recording_sessions",
    "list_ingestion_jobs",
    "list_ingestion_source_states",
    "list_pending_recording_sessions",
    "normalize_contact_identity",
    "renew_ingestion_job_lease",
    "touch_ingestion_heartbeat",
    "unbind_connector",
    "update_ingestion_source_state",
    "update_recording_session",
    "upsert_contact_identity",
]
