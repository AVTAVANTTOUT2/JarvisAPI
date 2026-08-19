"""Worker durable des collecteurs locaux, multi-profils et fail-closed."""

from __future__ import annotations

import asyncio
import fcntl
import inspect
import logging
import os
import random
import socket
import time
from collections.abc import Awaitable, Callable, Sequence
from contextlib import contextmanager, suppress
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import config
from database import (
    claim_ingestion_jobs,
    complete_ingestion_job,
    connector_binding_allows_external_account,
    enqueue_ingestion_job,
    fail_ingestion_job,
    get_connector_binding,
    get_db,
    get_ingestion_source_state,
    init_db,
    list_connector_bindings,
    list_ingestion_jobs,
    refresh_local_connector_device_hash,
    renew_ingestion_job_lease,
    touch_ingestion_heartbeat,
    update_connector_permission,
    update_ingestion_source_state,
    use_profile,
)
from database.email import cache_email_preview, save_email_full
from database.knowledge import upsert_calendar_events
from database.profiles import list_user_profiles
from database.time_buckets import sqlite_utc_timestamp
from jarvis.ingestion.models import (
    ConnectorBinding,
    IngestionJob,
    IngestionRunResult,
    IngestionSourceState,
)


logger = logging.getLogger(__name__)

IngestionHandler = Callable[
    [IngestionJob, ConnectorBinding | None, IngestionSourceState | None],
    IngestionRunResult | Awaitable[IngestionRunResult],
]
IngestionMaintenanceHook = Callable[[], Any | Awaitable[Any]]
_HANDLERS: dict[tuple[str, str], IngestionHandler] = {}
_MAINTENANCE_HOOKS: dict[str, IngestionMaintenanceHook] = {}
_SOURCE_ALIASES = {
    "email": "mail",
    "mail": "mail",
    "imessage": "imessage",
    "calendar": "calendar",
}
_INGESTION_LEASE_SECONDS = 180


def _create_ingestion_proposal(
    source: str,
    external_id: str,
    title: str,
    content: str,
) -> None:
    """Crée une proposition locale idempotente, sans déclencher d'action externe."""

    try:
        from jarvis.notification_service import notification_service

        notification_service.create(
            source=f"ingestion:{source}",
            title=str(title)[:300],
            content=str(content)[:1_500],
            priority="medium",
            idempotency_key=f"ingestion:{source}:{external_id}",
        )
    except Exception:
        logger.exception("[ingestion] notification proposal failed source=%s", source)


def _looks_like_personal_mail(sender: str) -> bool:
    lowered = str(sender or "").casefold()
    automated = ("no-reply", "noreply", "notification@", "newsletter", "marketing")
    return bool(lowered.strip()) and not any(marker in lowered for marker in automated)


def register_ingestion_handler(
    source: str,
    job_kind: str,
    handler: IngestionHandler,
    *,
    replace: bool = False,
) -> None:
    """Enregistre explicitement un handler ; aucun import dynamique arbitraire."""

    key = (str(source).strip().casefold(), str(job_kind).strip().casefold())
    if key in _HANDLERS and not replace and _HANDLERS[key] is not handler:
        raise RuntimeError(f"ingestion_handler_already_registered:{key[0]}:{key[1]}")
    _HANDLERS[key] = handler


def unregister_ingestion_handler(source: str, job_kind: str) -> None:
    _HANDLERS.pop(
        (str(source).strip().casefold(), str(job_kind).strip().casefold()), None
    )


def register_ingestion_maintenance_hook(
    name: str,
    hook: IngestionMaintenanceHook,
    *,
    replace: bool = False,
) -> None:
    hook_name = str(name or "").strip().casefold()
    if not hook_name or len(hook_name) > 128:
        raise ValueError("ingestion_maintenance_hook_name_invalid")
    if (
        hook_name in _MAINTENANCE_HOOKS
        and not replace
        and _MAINTENANCE_HOOKS[hook_name] is not hook
    ):
        raise RuntimeError(f"ingestion_maintenance_hook_already_registered:{hook_name}")
    _MAINTENANCE_HOOKS[hook_name] = hook


def unregister_ingestion_maintenance_hook(name: str) -> None:
    _MAINTENANCE_HOOKS.pop(str(name or "").strip().casefold(), None)


async def _run_maintenance_hooks() -> list[str]:
    errors: list[str] = []
    for name, hook in tuple(_MAINTENANCE_HOOKS.items()):
        try:
            result = hook()
            if inspect.isawaitable(result):
                await result
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("[ingestion] maintenance hook failed name=%s", name)
            errors.append(name)
    return errors


def _parse_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _calendar_windows(cursor: dict[str, Any] | None) -> list[tuple[datetime, datetime]]:
    windows: list[tuple[datetime, datetime]] = []
    raw_windows = (cursor or {}).get("coverage_windows", [])
    if not isinstance(raw_windows, list):
        return windows
    for raw in raw_windows:
        if not isinstance(raw, (list, tuple)) or len(raw) != 2:
            continue
        start = _parse_utc(str(raw[0]))
        end = _parse_utc(str(raw[1]))
        if start is not None and end is not None and start < end:
            windows.append((start, end))
    return windows


def _merge_calendar_windows(
    windows: Sequence[tuple[datetime, datetime]],
    new_window: tuple[datetime, datetime],
) -> list[tuple[datetime, datetime]]:
    ordered = sorted((*windows, new_window), key=lambda item: item[0])
    merged: list[tuple[datetime, datetime]] = []
    for start, end in ordered:
        if not merged or start > merged[-1][1]:
            merged.append((start, end))
            continue
        previous_start, previous_end = merged[-1]
        merged[-1] = (previous_start, max(previous_end, end))
    return merged


def _calendar_window_is_covered(
    windows: Sequence[tuple[datetime, datetime]],
    required_from: datetime | None,
    required_to: datetime | None,
) -> bool:
    if required_from is None and required_to is None:
        return bool(windows)
    for start, end in windows:
        if required_from is not None and start > required_from:
            continue
        if required_to is not None and end < required_to:
            continue
        return True
    return False


def _contiguous_calendar_end(
    windows: Sequence[tuple[datetime, datetime]], floor: datetime
) -> datetime | None:
    for start, end in windows:
        if start <= floor < end:
            return end
    return None


def _configured_interval(source: str, binding: ConnectorBinding) -> int:
    names = {
        "mail": "INGESTION_MAIL_INTERVAL_S",
        "imessage": "INGESTION_IMESSAGE_INTERVAL_S",
        "calendar": "INGESTION_CALENDAR_INTERVAL_S",
    }
    configured = int(
        getattr(config, names.get(source, ""), binding.sync_interval_seconds)
    )
    explicit = binding.settings.get("sync_interval_seconds")
    return max(15, int(explicit if explicit is not None else configured))


def _in_failure_backoff(
    binding: ConnectorBinding, state: IngestionSourceState | None
) -> bool:
    if state is None or state.consecutive_failures <= 0 or not state.last_attempt_at:
        return False
    last_attempt = _parse_utc(state.last_attempt_at)
    retry_seconds = max(
        _configured_interval(binding.source, binding),
        min(3600, 2 ** min(state.consecutive_failures, 12)),
    )
    if last_attempt is None:
        return False
    return datetime.now(timezone.utc) - last_attempt < timedelta(
        seconds=retry_seconds
    )


def _is_due(binding: ConnectorBinding, state: IngestionSourceState | None) -> bool:
    if _in_failure_backoff(binding, state):
        return False
    if (
        binding.source == "calendar"
        and state is not None
        and bool(state.cursor.get("backfill_pending"))
    ):
        return True
    if state is None or not state.last_success_at:
        return True
    last_success = _parse_utc(state.last_success_at)
    if last_success is None:
        return True
    return datetime.now(timezone.utc) - last_success >= timedelta(
        seconds=_configured_interval(binding.source, binding)
    )


def _coverage_satisfies(
    state: IngestionSourceState,
    from_iso: str | None,
    to_iso: str | None,
) -> bool:
    required_from = _parse_utc(from_iso) if from_iso else None
    required_to = _parse_utc(to_iso) if to_iso else None
    if state.source == "calendar":
        windows = _calendar_windows(state.cursor)
        if windows:
            return _calendar_window_is_covered(windows, required_from, required_to)
    if not from_iso and not to_iso:
        return state.completeness == "complete"
    if state.completeness == "complete" and bool(state.cursor.get("full_history")):
        return True
    covered_from = _parse_utc(state.coverage_start_utc)
    covered_to = _parse_utc(state.coverage_end_utc)
    if required_from is not None and (
        covered_from is None or covered_from > required_from
    ):
        return False
    if required_to is not None and (covered_to is None or covered_to < required_to):
        return False
    return True


def schedule_due_ingestion_jobs() -> int:
    """Enfile un seul job périodique actif par source liée au profil."""

    scheduled = 0
    for binding in list_connector_bindings():
        key = (binding.source, "sync")
        if key not in _HANDLERS or not _is_due(
            binding, get_ingestion_source_state(binding.source)
        ):
            continue
        enqueue_ingestion_job(
            binding.source,
            job_kind="sync",
            dedupe_key="sync:periodic",
            payload={"reason": "periodic"},
        )
        scheduled += 1
    return scheduled


def request_ingestion_freshness(
    sources: Sequence[str],
    *,
    from_iso: str | None = None,
    to_iso: str | None = None,
    budget_ms: int = 0,
) -> dict[str, IngestionSourceState | None]:
    """Enfile la fraîcheur et attend au plus le budget, sans I/O direct."""

    result: dict[str, IngestionSourceState | None] = {}
    baselines: dict[str, tuple[int, str | None]] = {}
    pending: set[str] = set()
    for requested in sources:
        source = _SOURCE_ALIASES.get(
            str(requested).strip().casefold(), str(requested).strip().casefold()
        )
        binding = get_connector_binding(source)
        if binding is None:
            result[source] = update_ingestion_source_state(
                source,
                status="disabled",
                completeness="unknown",
                error_code="connector_unbound",
                error_message=None,
            )
            continue
        state = get_ingestion_source_state(source)
        if state is None:
            state = update_ingestion_source_state(
                source, status="idle", completeness="unknown"
            )
        if _in_failure_backoff(binding, state):
            result[source] = state
            continue
        if not _is_due(binding, state) and _coverage_satisfies(state, from_iso, to_iso):
            result[source] = state
            continue
        baselines[source] = (state.generation, state.last_success_at)
        enqueue_ingestion_job(
            source,
            job_kind="sync",
            dedupe_key="sync:requested",
            mark_source_pending=True,
            payload={
                "reason": "freshness_request",
                "from_iso": from_iso,
                "to_iso": to_iso,
            },
        )
        state = get_ingestion_source_state(source) or state
        result[source] = state
        pending.add(source)
    deadline = time.monotonic() + max(0, int(budget_ms)) / 1000.0
    while pending and time.monotonic() < deadline:
        remaining = max(0.0, deadline - time.monotonic())
        time.sleep(min(0.025, remaining))
        for source in tuple(pending):
            current = get_ingestion_source_state(source)
            if current is None:
                continue
            baseline_generation, baseline_success = baselines[source]
            completed = current.last_success_at != baseline_success
            terminal_failure = (
                current.generation > baseline_generation
                and current.status in {"degraded", "error", "disabled"}
            )
            if completed or terminal_failure:
                result[source] = current
                pending.remove(source)
    return result


def request_email_hydration(source_id: str, *, budget_ms: int = 0) -> str:
    """Demande le corps complet d'un mail canonique, sans I/O dans l'appelant."""

    identifier = str(source_id or "").strip()
    if not identifier.isdigit() or get_connector_binding("mail") is None:
        return "unavailable"
    with get_db() as conn:
        row = conn.execute(
            "SELECT content_complete FROM email_summaries WHERE id = ?",
            (int(identifier),),
        ).fetchone()
    if row is None:
        return "unavailable"
    if bool(row["content_complete"]):
        return "complete"
    job = enqueue_ingestion_job(
        "mail",
        job_kind="hydrate",
        dedupe_key=f"hydrate:{identifier}",
        payload={"source_id": identifier},
    )
    deadline = time.monotonic() + max(0, min(5_000, int(budget_ms))) / 1000.0
    while time.monotonic() < deadline:
        current = next(
            (
                candidate
                for candidate in list_ingestion_jobs(source="mail", limit=100)
                if candidate.id == job.id
            ),
            None,
        )
        if current is None:
            return "unavailable"
        if current.status == "done":
            return "complete"
        if current.status in {"dead", "retry"}:
            return "degraded"
        time.sleep(min(0.025, max(0.0, deadline - time.monotonic())))
    return "queued"


async def _mail_sync(
    job: IngestionJob,
    binding: ConnectorBinding | None,
    state: IngestionSourceState | None,
) -> IngestionRunResult:
    if binding is None:
        return IngestionRunResult(status="unavailable", error_code="connector_unbound")
    from integrations.mail import mail_client

    if mail_client is None or not await asyncio.to_thread(mail_client.is_available):
        update_connector_permission("mail", "unknown")
        return IngestionRunResult(status="unavailable", error_code="mail_unavailable")
    update_connector_permission("mail", "granted")
    page_size = max(1, min(100, int(binding.settings.get("page_size", 50))))
    max_pages = max(1, min(20, int(binding.settings.get("max_pages_per_run", 5))))
    offset = 0
    if state and state.completeness == "partial":
        offset = max(0, int(state.cursor.get("offset", 0)) - page_size)
    item_count = 0
    complete = False
    last_item_at: str | None = None
    for _ in range(max_pages):
        result = await mail_client.get_recent_page_result(
            page_size,
            offset=offset,
            include_preview=False,
        )
        if result.status != "ok":
            return IngestionRunResult(
                status="degraded" if item_count else "unavailable",
                item_count=item_count,
                cursor={"offset": offset},
                completeness="partial",
                last_item_at=last_item_at,
                error_code=result.error or "mail_unavailable",
            )
        for message in result.messages:
            account_id = str(message.get("account_id") or "").strip()
            if not connector_binding_allows_external_account(binding, account_id):
                continue
            message_id = str(message.get("id") or "").strip()
            if not message_id:
                continue
            received_at = str(message.get("date") or "")
            with get_db() as conn:
                existed = (
                    conn.execute(
                        "SELECT 1 FROM email_summaries WHERE gmail_id = ?",
                        (message_id,),
                    ).fetchone()
                    is not None
                )
            cache_email_preview(
                gmail_id=message_id,
                sender=str(message.get("from") or ""),
                subject=str(message.get("subject") or ""),
                preview=str(message.get("preview") or message.get("snippet") or ""),
                received_at=received_at,
                is_read=bool(message.get("is_read")),
                account_id=account_id or None,
                mailbox_id=str(message.get("mailbox_id") or "") or None,
            )
            item_count += 1
            received = _parse_utc(received_at)
            previous_success = _parse_utc(state.last_success_at) if state else None
            if (
                not existed
                and previous_success is not None
                and received is not None
                and received > previous_success
                and _looks_like_personal_mail(str(message.get("from") or ""))
            ):
                _create_ingestion_proposal(
                    "mail",
                    message_id,
                    f"Nouveau mail de {message.get('from') or 'contact'}",
                    "Proposition : examiner "
                    + str(message.get("subject") or "ce message"),
                )
            if received_at and (last_item_at is None or received_at > last_item_at):
                last_item_at = received_at
        if not result.has_more or result.next_offset is None:
            complete = True
            offset = 0
            break
        offset = max(offset + len(result.messages), int(result.next_offset))
    with get_db() as conn:
        total = int(conn.execute("SELECT COUNT(*) FROM email_summaries").fetchone()[0])
    return IngestionRunResult(
        status="ok",
        item_count=total,
        cursor={
            "offset": offset,
            "page_size": page_size,
            "full_history": complete,
            "namespace": "inbox",
            "namespace_complete": complete,
            "deletion_reconciliation": "unsupported_without_full_mailbox_scan",
        },
        completeness="complete" if complete else "partial",
        last_item_at=last_item_at,
    )


async def _mail_hydrate(
    job: IngestionJob,
    binding: ConnectorBinding | None,
    state: IngestionSourceState | None,
) -> IngestionRunResult:
    if binding is None:
        return IngestionRunResult(status="unavailable", error_code="connector_unbound")
    source_id = str(job.payload.get("source_id") or "").strip()
    if not source_id.isdigit():
        return IngestionRunResult(
            status="unavailable", error_code="email_source_invalid"
        )
    with get_db() as conn:
        row = conn.execute(
            """SELECT gmail_id, sender, subject, summary, category, priority,
                      is_read, received_at, content_complete, account_id, mailbox_id
               FROM email_summaries WHERE id = ?""",
            (int(source_id),),
        ).fetchone()
    if row is None:
        return IngestionRunResult(status="unavailable", error_code="email_not_found")
    if not connector_binding_allows_external_account(binding, row["account_id"]):
        return IngestionRunResult(
            status="unavailable", error_code="email_account_not_bound"
        )
    if bool(row["content_complete"]):
        return IngestionRunResult(
            status="ok",
            cursor=dict(state.cursor) if state else {},
            completeness=state.completeness if state else "unknown",
            coverage_start_utc=state.coverage_start_utc if state else None,
            coverage_end_utc=state.coverage_end_utc if state else None,
            last_item_at=state.last_item_at if state else None,
            item_count=state.item_count if state else 0,
        )

    from integrations.mail import HYDRATION_BODY_MAX_CHARS, mail_client

    if mail_client is None or not await asyncio.to_thread(mail_client.is_available):
        update_connector_permission("mail", "unknown")
        return IngestionRunResult(status="unavailable", error_code="mail_unavailable")
    update_connector_permission("mail", "granted")
    full = await mail_client.get_message(
        str(row["gmail_id"]), max_body_chars=HYDRATION_BODY_MAX_CHARS
    )
    if not full:
        return IngestionRunResult(
            status="degraded", error_code="email_hydration_failed"
        )
    if not connector_binding_allows_external_account(
        binding, full.get("account_id") or row["account_id"]
    ):
        return IngestionRunResult(
            status="unavailable", error_code="email_account_not_bound"
        )
    body = str(full.get("body") or "")
    reported_truncated = full.get("body_truncated")
    body_truncated = (
        bool(reported_truncated)
        if reported_truncated is not None
        else len(body) >= HYDRATION_BODY_MAX_CHARS or body.endswith("[…tronqué…]")
    )
    content_complete = not body_truncated
    save_email_full(
        gmail_id=str(row["gmail_id"]),
        sender=str(full.get("from") or row["sender"] or ""),
        subject=str(full.get("subject") or row["subject"] or ""),
        body=body,
        received_at=str(full.get("date") or row["received_at"] or ""),
        summary=str(row["summary"] or full.get("subject") or ""),
        category=str(row["category"] or "info"),
        priority=str(row["priority"] or "low"),
        is_read=bool(full.get("is_read", row["is_read"])),
        content_complete=content_complete,
        ingestion_completeness="complete" if content_complete else "partial",
        account_id=str(full.get("account_id") or row["account_id"] or "") or None,
        mailbox_id=str(full.get("mailbox_id") or row["mailbox_id"] or "") or None,
    )
    return IngestionRunResult(
        status="ok" if content_complete else "degraded",
        cursor=dict(state.cursor) if state else {},
        completeness=(
            state.completeness
            if content_complete and state
            else ("unknown" if content_complete else "partial")
        ),
        coverage_start_utc=state.coverage_start_utc if state else None,
        coverage_end_utc=state.coverage_end_utc if state else None,
        last_item_at=state.last_item_at if state else None,
        item_count=state.item_count if state else 0,
        error_code=None if content_complete else "email_body_truncated",
        error_message=None,
    )


async def _imessage_sync(
    job: IngestionJob,
    binding: ConnectorBinding | None,
    state: IngestionSourceState | None,
) -> IngestionRunResult:
    del job
    if binding is None:
        return IngestionRunResult(status="unavailable", error_code="connector_unbound")
    from integrations.imessage_import import imessage_importer

    if not await asyncio.to_thread(imessage_importer.is_available):
        update_connector_permission("imessage", "unknown")
        return IngestionRunResult(
            status="unavailable",
            error_code="imessage_unavailable",
        )
    update_connector_permission("imessage", "granted")
    result = await asyncio.to_thread(imessage_importer.sync_incremental)
    skipped_busy = bool(result.errors) and all(
        error == "sync_already_running" for error in result.errors
    )
    if skipped_busy:
        return IngestionRunResult(
            status="ok",
            item_count=state.item_count if state else 0,
            cursor=dict(state.cursor) if state else {},
            completeness=state.completeness if state else "unknown",
            coverage_start_utc=state.coverage_start_utc if state else None,
            coverage_end_utc=state.coverage_end_utc if state else None,
            last_item_at=state.last_item_at if state else None,
        )
    reconciliation = (
        result.reconciliation if isinstance(result.reconciliation, dict) else {}
    )
    reconcile_failed = bool(reconciliation) and reconciliation.get("ok") is False
    failed = int(result.total_failed or 0)
    has_failure = bool(failed or result.errors or reconcile_failed)
    deletion_reconcile_failed = False
    if not has_failure:
        try:
            await asyncio.to_thread(imessage_importer.reconcile_deleted_messages)
        except Exception as exc:
            deletion_reconcile_failed = True
            has_failure = True
            logger.warning(
                "[ingestion] iMessage deletion reconciliation failed: %s",
                type(exc).__name__,
            )
    if state and state.last_success_at:
        with get_db() as conn:
            new_messages = conn.execute(
                """SELECT m.guid, m.text, COALESCE(ci.display_name, h.display_name,
                                                h.handle, c.display_name, 'Contact') AS sender
                   FROM imessage_messages m
                   LEFT JOIN imessage_handles h ON h.id = m.handle_id
                   LEFT JOIN imessage_chats c ON c.id = m.chat_id
                   LEFT JOIN contact_identities ci ON ci.id = h.contact_identity_id
                   WHERE m.is_from_me = 0 AND m.created_at > ?
                   ORDER BY m.created_at ASC LIMIT 20""",
                (state.last_success_at,),
            ).fetchall()
        for message in new_messages:
            _create_ingestion_proposal(
                "imessage",
                str(message["guid"]),
                f"Nouveau message de {message['sender']}",
                "Proposition : examiner " + str(message["text"] or "ce message"),
            )
    with get_db() as conn:
        cursor = conn.execute(
            "SELECT last_apple_rowid FROM imessage_sync_cursor WHERE id = 1"
        ).fetchone()
        aggregate = conn.execute(
            """
            SELECT COUNT(*) AS count, MIN(occurred_at_utc) AS first_at,
                   MAX(occurred_at_utc) AS last_at
            FROM imessage_messages
            """
        ).fetchone()
    error_code = None
    if reconcile_failed or deletion_reconcile_failed:
        error_code = "imessage_reconciliation_failed"
    elif has_failure:
        error_code = "imessage_partial_import"
    return IngestionRunResult(
        status="degraded" if has_failure else "ok",
        item_count=int(aggregate["count"] if aggregate else 0),
        cursor={
            "last_apple_rowid": int(cursor["last_apple_rowid"] if cursor else 0),
            "full_history": not has_failure,
            "namespace_complete": not has_failure,
        },
        completeness="partial" if has_failure else "complete",
        coverage_start_utc=aggregate["first_at"] if aggregate else None,
        coverage_end_utc=aggregate["last_at"] if aggregate else None,
        last_item_at=aggregate["last_at"] if aggregate else None,
        error_code=error_code,
        error_message=(
            "; ".join(result.errors[:3])
            if result.errors
            else (
                "reconciliation_incomplete"
                if reconcile_failed or deletion_reconcile_failed
                else None
            )
        ),
    )


async def _calendar_sync(
    job: IngestionJob,
    binding: ConnectorBinding | None,
    state: IngestionSourceState | None,
) -> IngestionRunResult:
    if binding is None:
        return IngestionRunResult(status="unavailable", error_code="connector_unbound")
    from integrations.calendar_api import calendar_client

    now = datetime.now(timezone.utc)
    requested_from = _parse_utc(str(job.payload.get("from_iso") or ""))
    requested_to = _parse_utc(str(job.payload.get("to_iso") or ""))
    if (requested_from is None) != (requested_to is None) or (
        requested_from is not None
        and requested_to is not None
        and requested_to <= requested_from
    ):
        return IngestionRunResult(
            status="unavailable", error_code="calendar_range_invalid"
        )

    history_start = _parse_utc(
        str(binding.settings.get("history_start_utc", "1970-01-01T00:00:00+00:00"))
    ) or datetime(1970, 1, 1, tzinfo=timezone.utc)
    lookahead_days = max(1, int(binding.settings.get("lookahead_days", 30)))
    backfill_target = now + timedelta(days=lookahead_days)
    cursor = dict(state.cursor) if state else {}
    windows = _calendar_windows(cursor)
    full_history = bool(cursor.get("full_history"))
    backfill_mode = False

    if requested_from is not None and requested_to is not None:
        window_start, window_end = requested_from, requested_to
    elif not full_history:
        chunk_days = max(
            1,
            min(3_660, int(binding.settings.get("backfill_chunk_days", 365))),
        )
        contiguous_end = _contiguous_calendar_end(windows, history_start)
        window_start = contiguous_end or history_start
        window_end = min(window_start + timedelta(days=chunk_days), backfill_target)
        backfill_mode = True
    else:
        lookback_days = max(0, int(binding.settings.get("lookback_days", 1)))
        window_start = now - timedelta(days=lookback_days)
        window_end = backfill_target

    if window_end <= window_start:
        return IngestionRunResult(
            status="unavailable", error_code="calendar_range_invalid"
        )
    from_iso = _iso_utc(window_start)
    to_iso = _iso_utc(window_end)
    result = await calendar_client.get_events_result(from_iso, to_iso)
    if result.status != "ok":
        update_connector_permission("calendar", "unknown")
        return IngestionRunResult(
            status="unavailable",
            completeness="unknown",
            coverage_start_utc=from_iso,
            coverage_end_utc=to_iso,
            error_code=result.error or "calendar_unavailable",
        )
    update_connector_permission("calendar", "granted")
    events = list(result.events)
    fetched_event_count = len(events)
    configured_calendars = binding.settings.get("calendar_names")
    allowed_calendars = (
        {
            str(value).strip().casefold()
            for value in configured_calendars
            if str(value).strip()
        }
        if isinstance(configured_calendars, list)
        else set()
    )
    if allowed_calendars:
        events = [
            event
            for event in events
            if str(event.get("calendar_name") or event.get("calendar") or "")
            .strip()
            .casefold()
            in allowed_calendars
        ]
    if not connector_binding_allows_external_account(binding, "local"):
        verifiable = [event for event in events if event.get("account_id")]
        if len(verifiable) != len(events):
            return IngestionRunResult(
                status="unavailable",
                error_code="calendar_account_unverifiable",
            )
        events = [
            event
            for event in events
            if connector_binding_allows_external_account(
                binding, str(event.get("account_id") or "")
            )
        ]

    if fetched_event_count > 0 and not events:
        logger.warning(
            "calendar sync: %d événements récupérés mais tous exclus par les filtres "
            "(fenêtre %s..%s)",
            fetched_event_count,
            from_iso,
            to_iso,
        )
        return IngestionRunResult(
            status="degraded",
            completeness="partial",
            coverage_start_utc=from_iso,
            coverage_end_utc=to_iso,
            error_code="calendar_filter_excluded_all",
            error_message=(
                "tous les événements ont été exclus par le filtre du connecteur"
            ),
        )

    upsert_calendar_events(events, window_start=from_iso, window_end=to_iso)
    with get_db() as conn:
        aggregate = conn.execute(
            "SELECT COUNT(*) AS count, MAX(start_at) AS last_at FROM calendar_events"
        ).fetchone()
    merged_windows = _merge_calendar_windows(windows, (window_start, window_end))
    contiguous_end = _contiguous_calendar_end(merged_windows, history_start)
    full_history = bool(contiguous_end and contiguous_end >= backfill_target)
    serialized_windows = [
        [_iso_utc(start), _iso_utc(end)] for start, end in merged_windows[-64:]
    ]
    if full_history and contiguous_end is not None:
        coverage_start = _iso_utc(history_start)
        coverage_end = _iso_utc(contiguous_end)
    elif len(merged_windows) == 1:
        coverage_start = _iso_utc(merged_windows[0][0])
        coverage_end = _iso_utc(merged_windows[0][1])
    else:
        coverage_start = None
        coverage_end = None
    return IngestionRunResult(
        status="ok",
        item_count=int(aggregate["count"] if aggregate else 0),
        cursor={
            "window_end": to_iso,
            "coverage_windows": serialized_windows,
            "backfill_next_utc": _iso_utc(contiguous_end or history_start),
            "backfill_pending": not full_history,
            "full_history": full_history,
            "full_history_from_utc": _iso_utc(history_start),
            "full_history_through_utc": _iso_utc(contiguous_end)
            if contiguous_end
            else None,
            "last_mode": "backfill" if backfill_mode else "window",
        },
        completeness="complete" if full_history else "partial",
        coverage_start_utc=coverage_start,
        coverage_end_utc=coverage_end,
        last_item_at=aggregate["last_at"] if aggregate else None,
    )


def _register_builtin_handlers() -> None:
    register_ingestion_handler("mail", "sync", _mail_sync, replace=True)
    register_ingestion_handler("mail", "hydrate", _mail_hydrate, replace=True)
    register_ingestion_handler("imessage", "sync", _imessage_sync, replace=True)
    register_ingestion_handler("calendar", "sync", _calendar_sync, replace=True)


async def _invoke_handler(
    handler: IngestionHandler,
    job: IngestionJob,
    binding: ConnectorBinding | None,
    state: IngestionSourceState | None,
) -> IngestionRunResult:
    result = handler(job, binding, state)
    if inspect.isawaitable(result):
        result = await result
    if not isinstance(result, IngestionRunResult):
        raise TypeError("ingestion_handler_result_invalid")
    return result


async def _renew_claimed_job_lease(
    job: IngestionJob, stop_event: asyncio.Event
) -> None:
    interval = max(5.0, _INGESTION_LEASE_SECONDS / 3)
    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
            return
        except TimeoutError:
            if not renew_ingestion_job_lease(
                job.id,
                job.lease_token or "",
                lease_seconds=_INGESTION_LEASE_SECONDS,
            ):
                logger.error(
                    "[ingestion] lease lost source=%s kind=%s",
                    job.source,
                    job.job_kind,
                )
                return


async def _process_profile_jobs(
    worker_id: str, *, max_jobs: int = 20
) -> dict[str, int]:
    pairs = sorted(_HANDLERS)
    if not pairs:
        return {"scheduled": 0, "claimed": 0, "completed": 0, "retried": 0}
    scheduled = schedule_due_ingestion_jobs()
    jobs = claim_ingestion_jobs(
        worker_id,
        lease_seconds=_INGESTION_LEASE_SECONDS,
        limit=max_jobs,
        handler_pairs=pairs,
    )
    counts = {
        "scheduled": scheduled,
        "claimed": len(jobs),
        "completed": 0,
        "retried": 0,
    }
    for job in jobs:
        handler = _HANDLERS.get((job.source, job.job_kind))
        if handler is None or not job.lease_token:
            continue
        binding = (
            get_connector_binding(job.source)
            if job.source in {"mail", "imessage", "calendar"}
            else None
        )
        if job.source in {"mail", "imessage", "calendar"} and binding is None:
            fail_ingestion_job(
                job.id,
                job.lease_token,
                error_code="connector_unbound",
                retry_delay_seconds=300,
            )
            counts["retried"] += 1
            continue
        state = get_ingestion_source_state(job.source)
        now = sqlite_utc_timestamp()
        update_ingestion_source_state(
            job.source,
            status="running",
            last_attempt_at=now,
            heartbeat_at=now,
            increment_generation=True,
        )
        lease_stop = asyncio.Event()
        lease_task = asyncio.create_task(_renew_claimed_job_lease(job, lease_stop))
        try:
            try:
                result = await _invoke_handler(handler, job, binding, state)
            except Exception as exc:
                logger.exception(
                    "[ingestion] handler failed source=%s kind=%s",
                    job.source,
                    job.job_kind,
                )
                if fail_ingestion_job(
                    job.id,
                    job.lease_token,
                    error_code=type(exc).__name__,
                    error_message=str(exc),
                ):
                    update_ingestion_source_state(
                        job.source,
                        status="error",
                        heartbeat_at=sqlite_utc_timestamp(),
                        error_code=type(exc).__name__,
                        error_message=str(exc),
                        consecutive_failures=(state.consecutive_failures + 1)
                        if state
                        else 1,
                    )
                    counts["retried"] += 1
                continue
        finally:
            lease_stop.set()
            lease_task.cancel()
            with suppress(asyncio.CancelledError):
                await lease_task
        if result.status == "ok":
            if complete_ingestion_job(job.id, job.lease_token):
                finished = sqlite_utc_timestamp()
                update_ingestion_source_state(
                    job.source,
                    status="idle",
                    cursor=result.cursor,
                    coverage_start_utc=result.coverage_start_utc,
                    coverage_end_utc=result.coverage_end_utc,
                    completeness=result.completeness,
                    last_success_at=finished,
                    last_item_at=result.last_item_at,
                    item_count=result.item_count,
                    heartbeat_at=finished,
                    error_code=None,
                    error_message=None,
                    consecutive_failures=0,
                )
                counts["completed"] += 1
        elif fail_ingestion_job(
            job.id,
            job.lease_token,
            error_code=result.error_code or "ingestion_unavailable",
            error_message=result.error_message or "",
        ):
            update_ingestion_source_state(
                job.source,
                status="degraded" if result.status == "degraded" else "error",
                cursor=result.cursor,
                coverage_start_utc=result.coverage_start_utc,
                coverage_end_utc=result.coverage_end_utc,
                completeness=result.completeness,
                last_item_at=result.last_item_at,
                item_count=result.item_count,
                heartbeat_at=sqlite_utc_timestamp(),
                error_code=result.error_code,
                error_message=result.error_message,
                consecutive_failures=(state.consecutive_failures + 1) if state else 1,
            )
            counts["retried"] += 1
    return counts


async def run_ingestion_maintenance_once(
    *, max_jobs_per_profile: int = 20
) -> dict[str, Any]:
    """Planifie et traite une passe pour tous les profils actifs."""

    _register_builtin_handlers()
    worker_id = f"{socket.gethostname()}:{os.getpid()}"
    summary: dict[str, Any] = {"profiles": {}, "errors": {}}
    try:
        profiles = list_user_profiles()
    except Exception as exc:
        summary["errors"]["registry"] = type(exc).__name__
        return summary
    for profile in profiles:
        profile_id = str(profile["id"])
        try:
            with use_profile(profile_id):
                init_db()
                for source in ("imessage", "mail", "calendar"):
                    refresh_local_connector_device_hash(source)
                touch_ingestion_heartbeat()
                profile_summary = await _process_profile_jobs(
                    worker_id,
                    max_jobs=max_jobs_per_profile,
                )
                hook_errors = await _run_maintenance_hooks()
                if hook_errors:
                    profile_summary["hook_errors"] = hook_errors
                summary["profiles"][profile_id] = profile_summary
        except Exception as exc:
            logger.exception("[ingestion] maintenance failed profile=%s", profile_id)
            summary["errors"][profile_id] = type(exc).__name__
    try:
        from jarvis.retrieval.worker import run_knowledge_maintenance_once

        summary["knowledge"] = await asyncio.to_thread(run_knowledge_maintenance_once)
    except Exception as exc:
        logger.exception("[ingestion] knowledge maintenance failed")
        summary["errors"]["knowledge"] = type(exc).__name__
    return summary


async def _run_imessage_file_watch(stop: asyncio.Event) -> None:
    """kqueue/debounce → jobs d'ingestion. Absent du process FastAPI."""

    from jarvis.ingestion.imessage_watch import IMessageFileWatcher

    debounce_ms = int(getattr(config, "INGESTION_IMESSAGE_WATCH_DEBOUNCE_MS", 300))
    watcher = IMessageFileWatcher(debounce_s=max(0.05, debounce_ms / 1000.0))
    await watcher.run_until(stop)


async def run_ingestion_worker(stop_event: asyncio.Event | None = None) -> None:
    """Boucle supervisable ; l'appelant contrôle l'arrêt via ``stop_event``."""

    if not bool(getattr(config, "INGESTION_SERVICE_ENABLED", True)):
        return
    stop = stop_event or asyncio.Event()
    heartbeat_interval = max(
        2.0, float(getattr(config, "INGESTION_HEARTBEAT_INTERVAL_S", 10))
    )
    consecutive_failures = 0
    watch_task: asyncio.Task[None] | None = None
    if bool(getattr(config, "INGESTION_IMESSAGE_WATCH_ENABLED", True)):
        watch_task = asyncio.create_task(
            _run_imessage_file_watch(stop),
            name="ingestion_imessage_watch",
        )
    try:
        while not stop.is_set():
            delay = heartbeat_interval
            try:
                summary = await run_ingestion_maintenance_once()
                if summary.get("errors"):
                    raise RuntimeError("ingestion_maintenance_degraded")
                consecutive_failures = 0
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                consecutive_failures += 1
                logger.exception("[ingestion] worker pass failed")
                try:
                    profiles = list_user_profiles()
                except Exception:
                    profiles = []
                for profile in profiles:
                    try:
                        with use_profile(str(profile["id"])):
                            update_ingestion_source_state(
                                "__service__",
                                status="degraded",
                                heartbeat_at=sqlite_utc_timestamp(),
                                error_code=type(exc).__name__,
                                error_message=None,
                                consecutive_failures=consecutive_failures,
                            )
                    except Exception:
                        logger.exception(
                            "[ingestion] failed to persist degraded heartbeat"
                        )
                base = min(300.0, 5.0 * (2 ** min(consecutive_failures - 1, 6)))
                delay = min(300.0, base + random.uniform(0.0, min(5.0, base * 0.1)))
            try:
                await asyncio.wait_for(stop.wait(), timeout=delay)
            except TimeoutError:
                pass
    finally:
        if watch_task is not None:
            watch_task.cancel()
            with suppress(asyncio.CancelledError):
                await watch_task


def ingestion_lock_path() -> Path:
    configured = str(getattr(config, "INGESTION_SERVICE_LOCK_PATH", "") or "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    db_path = Path(str(config.DB_PATH)).expanduser().resolve()
    return db_path.parent / f".{db_path.name}.ingestion-service.lock"


@contextmanager
def ingestion_singleton_lock():
    """Verrou inter-processus global ; le fichier ne contient aucune donnée."""

    path = ingestion_lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    handle = os.fdopen(fd, "a+")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        raise RuntimeError("ingestion_service_already_running") from None
    try:
        yield
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


__all__ = [
    "IngestionHandler",
    "IngestionMaintenanceHook",
    "ingestion_lock_path",
    "ingestion_singleton_lock",
    "register_ingestion_handler",
    "register_ingestion_maintenance_hook",
    "request_email_hydration",
    "request_ingestion_freshness",
    "run_ingestion_maintenance_once",
    "run_ingestion_worker",
    "schedule_due_ingestion_jobs",
    "unregister_ingestion_handler",
    "unregister_ingestion_maintenance_hook",
]
