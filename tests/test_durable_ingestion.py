"""Non-régressions des collecteurs durables et de leur isolation profilée."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest


@pytest.fixture()
def ingestion_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    db_path = tmp_path / "ingestion.db"
    monkeypatch.setattr("config.DB_PATH", str(db_path))
    monkeypatch.setattr("database.DB_PATH", db_path)
    from database import init_db

    init_db()
    monkeypatch.setattr(
        "jarvis.retrieval.worker.run_knowledge_maintenance_once", lambda: {}
    )
    return db_path


def test_connector_binding_is_explicit_and_profile_isolated(ingestion_db: Path) -> None:
    from database import init_db, use_profile
    from database.ingestion import (
        ConnectorBindingRequired,
        bind_connector,
        enqueue_ingestion_job,
        get_connector_binding,
    )

    with pytest.raises(ConnectorBindingRequired, match="connector_unbound:mail"):
        enqueue_ingestion_job("mail")

    binding = bind_connector(
        "mail", consent_source="explicit_test", sync_interval_seconds=15
    )
    assert binding.profile_id == "default"
    assert binding.consent_source == "explicit_test"

    with use_profile("secondary"):
        init_db()
        assert get_connector_binding("mail") is None
        with pytest.raises(ConnectorBindingRequired, match="connector_unbound:mail"):
            enqueue_ingestion_job("mail")

    assert get_connector_binding("mail") is not None


def test_connector_binding_denied_device_and_account_are_fail_closed(
    ingestion_db: Path,
) -> None:
    from database.ingestion import (
        bind_connector,
        connector_binding_allows_external_account,
        get_connector_binding,
    )

    bind_connector("mail", permission_state="denied")
    assert get_connector_binding("mail") is None
    assert get_connector_binding("mail", include_disabled=True) is not None

    bind_connector("mail", device_id="another-device", permission_state="unknown")
    assert get_connector_binding("mail") is None

    binding = bind_connector(
        "mail",
        external_account_id="Personnel",
        permission_state="granted",
    )
    assert get_connector_binding("mail") is not None
    assert connector_binding_allows_external_account(binding, "Personnel") is True
    assert connector_binding_allows_external_account(binding, "Travail") is False


def test_requested_sync_windows_merge_and_running_job_gets_followup(
    ingestion_db: Path,
) -> None:
    from database.ingestion import (
        bind_connector,
        claim_ingestion_jobs,
        enqueue_ingestion_job,
    )

    bind_connector("calendar", permission_state="granted")
    first = enqueue_ingestion_job(
        "calendar",
        job_kind="sync",
        dedupe_key="sync:requested",
        payload={
            "from_iso": "2026-02-01T00:00:00Z",
            "to_iso": "2026-02-02T00:00:00Z",
        },
    )
    merged = enqueue_ingestion_job(
        "calendar",
        job_kind="sync",
        dedupe_key="sync:requested",
        payload={
            "from_iso": "2026-01-01T00:00:00Z",
            "to_iso": "2026-03-01T00:00:00Z",
        },
    )
    assert merged.id == first.id
    assert merged.payload["from_iso"] == "2026-01-01T00:00:00Z"
    assert merged.payload["to_iso"] == "2026-03-01T00:00:00Z"

    claimed = claim_ingestion_jobs("worker", handler_pairs=[("calendar", "sync")])[0]
    followup = enqueue_ingestion_job(
        "calendar",
        job_kind="sync",
        dedupe_key="sync:requested",
        payload={
            "from_iso": "2025-01-01T00:00:00Z",
            "to_iso": "2027-01-01T00:00:00Z",
        },
    )
    assert followup.id != claimed.id
    assert followup.status == "pending"
    assert followup.dedupe_key.startswith("sync:requested:followup")


def test_calendar_coverage_never_bridges_uncollected_gaps(ingestion_db: Path) -> None:
    from database.ingestion import update_ingestion_source_state
    from jarvis.ingestion.service import _coverage_satisfies

    state = update_ingestion_source_state(
        "calendar",
        completeness="complete",
        coverage_start_utc="2026-01-01T00:00:00Z",
        coverage_end_utc="2026-04-01T00:00:00Z",
        cursor={
            "full_history": False,
            "coverage_windows": [
                ["2026-01-01T00:00:00Z", "2026-02-01T00:00:00Z"],
                ["2026-03-01T00:00:00Z", "2026-04-01T00:00:00Z"],
            ],
        },
    )

    assert _coverage_satisfies(state, "2026-01-10T00:00:00Z", "2026-01-20T00:00:00Z")
    assert not _coverage_satisfies(
        state, "2026-02-10T00:00:00Z", "2026-02-20T00:00:00Z"
    )
    assert not _coverage_satisfies(
        state, "2027-01-01T00:00:00Z", "2027-01-02T00:00:00Z"
    )


def test_mail_preview_refresh_never_downgrades_a_full_body(
    ingestion_db: Path,
) -> None:
    from database import get_db
    from database.email import cache_email_preview, save_email_full

    save_email_full(
        gmail_id="mail-full",
        sender="Grégoire <gregoire@example.test>",
        subject="Contrat",
        body="Corps complet et durable",
        received_at="2026-08-15T09:30:00+00:00",
        summary="Contrat",
        content_complete=True,
        ingestion_completeness="complete",
    )
    cache_email_preview(
        gmail_id="mail-full",
        sender="Grégoire <gregoire@example.test>",
        subject="Contrat",
        preview="Aperçu court",
        received_at="2026-08-15T09:30:00+00:00",
        is_read=True,
    )

    with get_db() as conn:
        row = conn.execute(
            """SELECT body, content_complete, ingestion_completeness
               FROM email_summaries WHERE gmail_id = 'mail-full'"""
        ).fetchone()
    assert row["body"] == "Corps complet et durable"
    assert row["content_complete"] == 1
    assert row["ingestion_completeness"] == "complete"


def test_legacy_french_mail_dates_are_normalized_to_utc(
    ingestion_db: Path,
) -> None:
    from database.time_buckets import sqlite_utc_timestamp

    assert sqlite_utc_timestamp("vendredi 15 août 2025 à 10:30:00") == (
        "2025-08-15 08:30:00"
    )


def test_freshness_requests_coalesce_while_source_is_inside_slo(
    ingestion_db: Path,
) -> None:
    from database.ingestion import (
        bind_connector,
        list_ingestion_jobs,
        update_ingestion_source_state,
    )
    from database.time_buckets import sqlite_utc_timestamp
    from jarvis.ingestion.service import request_ingestion_freshness

    bind_connector(
        "mail",
        consent_source="explicit_test",
        sync_interval_seconds=120,
    )
    update_ingestion_source_state(
        "mail",
        status="idle",
        completeness="complete",
        last_success_at=sqlite_utc_timestamp(),
    )

    states = request_ingestion_freshness(("mail",), budget_ms=50)

    assert states["mail"] is not None
    assert list_ingestion_jobs(source="mail") == []


def test_stale_complete_source_is_immediately_pending_after_freshness_enqueue(
    ingestion_db: Path,
) -> None:
    from database.ingestion import (
        bind_connector,
        get_ingestion_source_state,
        list_ingestion_jobs,
        update_ingestion_source_state,
    )
    from jarvis.ingestion.service import request_ingestion_freshness

    bind_connector("mail", consent_source="explicit_test", sync_interval_seconds=15)
    update_ingestion_source_state(
        "mail",
        status="idle",
        completeness="complete",
        cursor={"full_history": True, "namespace_complete": True},
        last_success_at=(datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
        item_count=0,
    )

    states = request_ingestion_freshness(("mail",), budget_ms=0)

    state = states["mail"]
    assert state is not None
    assert state.status == "degraded"
    assert state.completeness == "partial"
    assert state.error_code == "freshness_pending"
    assert get_ingestion_source_state("mail") == state
    jobs = list_ingestion_jobs(source="mail")
    assert len(jobs) == 1
    assert jobs[0].status == "pending"


@pytest.mark.asyncio
async def test_mail_body_hydration_is_durable_and_idempotent(
    ingestion_db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from database import get_db
    from database.email import cache_email_preview
    from database.ingestion import bind_connector, update_ingestion_source_state
    from database.time_buckets import sqlite_utc_timestamp
    from integrations import mail as mail_integration
    from jarvis.ingestion import service

    class _Mail:
        def is_available(self) -> bool:
            return True

        async def get_message(
            self, message_id: str, *, max_body_chars: int
        ) -> dict[str, Any]:
            assert message_id == "external-42"
            assert max_body_chars > 3000
            return {
                "id": message_id,
                "from": "Grégoire <gregoire@example.test>",
                "subject": "Orion",
                "date": "2026-08-15T20:15:00+02:00",
                "is_read": True,
                "body": "O" * 4001,
                "body_truncated": False,
            }

    monkeypatch.setattr(mail_integration, "mail_client", _Mail())
    source_id = cache_email_preview(
        gmail_id="external-42",
        sender="Grégoire <gregoire@example.test>",
        subject="Orion",
        preview="Aperçu",
        received_at="2026-08-15T20:15:00+02:00",
        is_read=True,
        account_id="Personnel",
        mailbox_id="Réception",
    )
    bind_connector(
        "mail",
        consent_source="explicit_test",
        sync_interval_seconds=120,
    )
    update_ingestion_source_state(
        "mail",
        status="idle",
        completeness="complete",
        last_success_at=sqlite_utc_timestamp(),
    )
    assert service.request_email_hydration(str(source_id), budget_ms=0) == "queued"

    service._register_builtin_handlers()
    await service._process_profile_jobs("test-worker", max_jobs=10)

    with get_db() as conn:
        row = conn.execute(
            """SELECT body, content_complete, ingestion_completeness,
                      account_id, mailbox_id
               FROM email_summaries WHERE id = ?""",
            (source_id,),
        ).fetchone()
    assert row["body"] == "O" * 4001
    assert row["content_complete"] == 1
    assert row["ingestion_completeness"] == "complete"
    assert row["account_id"] == "Personnel"
    assert row["mailbox_id"] == "Réception"
    assert service.request_email_hydration(str(source_id), budget_ms=0) == "complete"


def test_mail_parser_can_return_body_beyond_preview_limit_with_explicit_bound(
    ingestion_db: Path,
) -> None:
    from integrations.mail import AppleMailClient, BODY_MAX_CHARS

    body = "L" * (BODY_MAX_CHARS + 1001)
    raw = "\n".join(
        [
            "ACCOUNT:Personnel",
            "MAILBOX:Réception",
            "FROM:Grégoire <gregoire@example.test>",
            "TO:jarvis@example.test",
            "SUBJECT:Long",
            "DATE:2026-08-15T18:15:00Z",
            "READ:true",
            f"BODY:{body}",
        ]
    )
    client = AppleMailClient()

    preview = client._parse_single_message(raw, "42")
    hydrated = client._parse_single_message(raw, "42", max_body_chars=len(body) + 1)

    assert preview["body_truncated"] is True
    assert hydrated["body_truncated"] is False
    assert hydrated["body"] == body
    assert hydrated["body_char_count"] == len(body)


def test_jobs_use_fenced_leases_and_health_never_exposes_payload(
    ingestion_db: Path,
) -> None:
    from database.ingestion import (
        claim_ingestion_jobs,
        complete_ingestion_job,
        enqueue_ingestion_job,
        get_ingestion_health_summary,
        list_ingestion_jobs,
        renew_ingestion_job_lease,
    )

    job = enqueue_ingestion_job(
        "recording",
        job_kind="recording_process",
        dedupe_key="recording:one",
        payload={"session_id": "private-session"},
        require_binding=False,
    )
    assert claim_ingestion_jobs("worker", handler_pairs=[("mail", "sync")]) == []
    claimed = claim_ingestion_jobs(
        "worker",
        handler_pairs=[("recording", "recording_process")],
    )
    assert [item.id for item in claimed] == [job.id]
    token = claimed[0].lease_token or ""
    assert complete_ingestion_job(job.id, "obsolete-token") is False
    assert complete_ingestion_job(job.id, token) is True

    health = get_ingestion_health_summary()
    assert health["jobs"][0]["count"] == 1
    assert "payload" not in repr(health)
    assert "private-session" not in repr(health)

    exhausted = enqueue_ingestion_job(
        "recording",
        job_kind="recording_process",
        dedupe_key="recording:exhausted",
        max_attempts=1,
        require_binding=False,
    )
    exhausted_claim = claim_ingestion_jobs(
        "worker",
        lease_seconds=15,
        handler_pairs=[("recording", "recording_process")],
    )[0]
    assert renew_ingestion_job_lease(
        exhausted.id, exhausted_claim.lease_token or "", lease_seconds=300
    )
    assert not renew_ingestion_job_lease(exhausted.id, "obsolete-token")
    from database import get_db

    with get_db() as conn:
        conn.execute(
            "UPDATE ingestion_jobs SET lease_expires_at = '1970-01-01 00:00:00' WHERE id = ?",
            (exhausted.id,),
        )
    assert (
        claim_ingestion_jobs(
            "worker-2",
            handler_pairs=[("recording", "recording_process")],
        )
        == []
    )
    assert list_ingestion_jobs(source="recording")[0].status == "dead"


def test_recording_sessions_separate_pending_and_expired(ingestion_db: Path) -> None:
    from database.ingestion import (
        create_recording_session,
        list_expired_recording_sessions,
        list_pending_recording_sessions,
        update_recording_session,
    )

    pending = create_recording_session(
        spool_path=str(ingestion_db.parent / "pending.wav"),
        state="queued",
        retention_until="2099-01-01T00:00:00Z",
    )
    expired = create_recording_session(
        spool_path=str(ingestion_db.parent / "expired.wav"),
        state="completed",
        retention_until="2000-01-01T00:00:00Z",
    )
    assert [row.id for row in list_pending_recording_sessions()] == [pending.id]
    assert [row.id for row in list_expired_recording_sessions()] == [expired.id]
    updated = update_recording_session(pending.id, state="processing")
    assert updated is not None
    assert updated.state == "processing"


class _PagedMail:
    def __init__(self, messages: list[dict[str, Any]]) -> None:
        self.messages = messages
        self.offsets: list[int] = []

    def is_available(self) -> bool:
        return True

    async def get_recent_page_result(
        self,
        limit: int,
        *,
        offset: int = 0,
        include_preview: bool = True,
    ):
        del include_preview
        from integrations.mail import MailQueryResult

        self.offsets.append(offset)
        page = tuple(self.messages[offset : offset + limit])
        next_offset = offset + len(page)
        has_more = next_offset < len(self.messages)
        return MailQueryResult(
            status="ok",
            messages=page,
            next_offset=next_offset if has_more else None,
            has_more=has_more,
            complete=not has_more,
        )


@pytest.mark.asyncio
async def test_partial_mail_scan_never_deletes_cached_messages(
    ingestion_db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from database import get_db
    from database.email import cache_email_preview
    from database.ingestion import bind_connector, get_ingestion_source_state
    from integrations import mail as mail_module
    from jarvis.ingestion.service import run_ingestion_maintenance_once

    cache_email_preview(
        gmail_id="existing",
        sender="gregoire@example.com",
        subject="À conserver",
        preview="Historique",
        received_at="2026-01-01T00:00:00Z",
        is_read=True,
    )
    fake = _PagedMail([_mail_message("partial", 1), _mail_message("partial", 2)])
    monkeypatch.setattr(mail_module, "mail_client", fake)
    bind_connector(
        "mail",
        permission_state="granted",
        settings={"page_size": 1, "max_pages_per_run": 1},
    )

    await run_ingestion_maintenance_once()

    with get_db() as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM email_summaries WHERE gmail_id = 'existing'"
            ).fetchone()[0]
            == 1
        )
    state = get_ingestion_source_state("mail")
    assert state is not None
    assert state.completeness == "partial"
    assert (
        state.cursor["deletion_reconciliation"]
        == "unsupported_without_full_mailbox_scan"
    )


def _mail_message(prefix: str, index: int) -> dict[str, Any]:
    return {
        "id": f"{prefix}-{index}",
        "from": "Grégoire <gregoire@example.test>",
        "subject": f"Message {prefix} {index}",
        "date": f"2026-08-{15 if prefix == 'backlog' else 16:02d}T12:{index % 60:02d}:00",
        "is_read": False,
    }


@pytest.mark.asyncio
async def test_mail_watcher_pages_backlog_50_and_burst_50(
    ingestion_db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import integrations
    from database import get_all_processed_email_ids, save_email_full
    from scripts.email_watcher import EmailWatcher

    client = _PagedMail([_mail_message("backlog", index) for index in range(50)])
    monkeypatch.setattr(integrations, "mail_client", client)
    watcher = EmailWatcher()

    async def persist(message: dict[str, Any], stats: dict[str, Any]) -> None:
        message_id = str(message["id"])
        save_email_full(
            gmail_id=message_id,
            sender=str(message["from"]),
            subject=str(message["subject"]),
            body="body",
            received_at=str(message["date"]),
            summary=str(message["subject"]),
        )
        watcher.last_processed_ids.add(message_id)
        stats["incremental_new"] = stats.get("incremental_new", 0)

    watcher._process_one_email = persist  # type: ignore[method-assign]
    await watcher._check_new_emails()
    assert len(get_all_processed_email_ids()) == 50
    assert client.offsets == [0, 20, 40]

    client.offsets.clear()
    client.messages = [
        *[_mail_message("burst", index) for index in range(50)],
        *client.messages,
    ]
    await watcher._check_new_emails()
    assert len(get_all_processed_email_ids()) == 100
    assert client.offsets == [0, 20, 40, 60]


@pytest.mark.asyncio
async def test_imessage_reconciliation_failure_is_degraded_not_success(
    ingestion_db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    from database.ingestion import bind_connector
    from integrations import imessage_import as imessage_module
    from jarvis.ingestion.service import _imessage_sync

    class _Importer:
        @staticmethod
        def is_available() -> bool:
            return True

        @staticmethod
        def sync_incremental() -> SimpleNamespace:
            return SimpleNamespace(
                reconciliation={"ok": True}, total_failed=0, errors=[]
            )

        @staticmethod
        def reconcile_deleted_messages() -> int:
            raise sqlite3.OperationalError("source inventory failed")

    monkeypatch.setattr(imessage_module, "imessage_importer", _Importer())
    binding = bind_connector("imessage", consent_source="explicit_test")

    result = await _imessage_sync(None, binding, None)  # type: ignore[arg-type]

    assert result.status == "degraded"
    assert result.completeness == "partial"
    assert result.error_code == "imessage_reconciliation_failed"
    assert result.cursor["full_history"] is False


def test_imessage_failed_rowid_is_retried_before_cursor_advances(
    ingestion_db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from integrations.imessage_import import IMessageImporter, ReconciliationReport

    source = sqlite3.connect(":memory:")
    source.row_factory = sqlite3.Row
    source.execute("CREATE TABLE message(date INTEGER, guid TEXT)")
    source.executemany(
        "INSERT INTO message(date, guid) VALUES (?, ?)",
        [(100, "one"), (200, "two"), (300, "three")],
    )
    importer = IMessageImporter()
    monkeypatch.setattr(importer, "_get_max_chat_rowid", lambda: 3)
    monkeypatch.setattr(importer, "_open_chat_db", lambda: source)
    monkeypatch.setattr(importer, "_close_chat_db", lambda: None)
    monkeypatch.setattr(importer, "_import_new_handles", lambda *_: {})
    monkeypatch.setattr(importer, "_import_new_chats", lambda *_: {})
    monkeypatch.setattr(importer, "_import_chat_handles", lambda *_: None)
    monkeypatch.setattr(importer, "_import_new_attachments", lambda *_: {"imported": 0})
    monkeypatch.setattr(importer, "_import_reactions_since", lambda *_: {"imported": 0})
    monkeypatch.setattr(importer, "reconcile", lambda: ReconciliationReport(ok=True))
    monkeypatch.setattr(
        importer,
        "_import_messages_since",
        lambda *_: {
            "imported": 2,
            "skipped": 0,
            "failed": 1,
            "errors": ["Message ROWID=2: injected"],
            "failed_rowids": [2],
            "last_contiguous_rowid": 1,
        },
    )

    first = importer._sync_incremental_locked()
    assert first.total_failed == 1
    assert importer._get_cursor()["last_apple_rowid"] == 1
    assert importer._get_cursor()["status"] == "error"

    monkeypatch.setattr(
        importer,
        "_import_messages_since",
        lambda *_: {
            "imported": 2,
            "skipped": 0,
            "failed": 0,
            "errors": [],
            "failed_rowids": [],
            "last_contiguous_rowid": 3,
        },
    )
    second = importer._sync_incremental_locked()
    assert second.total_failed == 0
    cursor = importer._get_cursor()
    assert cursor["last_apple_rowid"] == 3
    assert cursor["last_date"] == 300
    assert cursor["last_guid"] == "three"


def test_imessage_persists_true_utc_date_and_contact_identity(
    ingestion_db: Path,
) -> None:
    from database import get_db
    from integrations.imessage_import import IMessageImporter

    class _Contacts:
        def resolve_handle(self, handle: str) -> str:
            assert handle == "+33600000001"
            return "Grégoire"

    importer = IMessageImporter(data_service=_Contacts())  # type: ignore[arg-type]
    with get_db() as conn:
        conn.execute("INSERT INTO people(name) VALUES ('Grégoire')")
        handle_id = importer._upsert_handle(conn, 1, "+33600000001")
        assert handle_id is not None
        inserted = importer._insert_message(
            conn,
            apple_rowid=1,
            guid="message-one",
            apple_handle_id=1,
            handles_map={1: handle_id},
            apple_chat_roomname="",
            chats_map={},
            text="Bonjour",
            date=600_000_000_000_000_000,
            date_read=0,
            is_from_me=0,
            is_read=1,
            item_type=0,
            group_title=None,
            associated_message_guid=None,
            associated_message_type=0,
        )
        assert inserted is True
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT m.occurred_at_utc, h.display_name, ci.normalized_value,
                   ci.person_id, p.name
            FROM imessage_messages m
            JOIN imessage_handles h ON h.id = m.handle_id
            JOIN contact_identities ci ON ci.id = h.contact_identity_id
            LEFT JOIN people p ON p.id = ci.person_id
            WHERE m.guid = 'message-one'
            """
        ).fetchone()
    assert row["occurred_at_utc"].endswith("+00:00")
    assert row["display_name"] == "Grégoire"
    assert row["normalized_value"] == "+33600000001"
    assert row["name"] == "Grégoire"


def test_calendar_range_uses_overlap_predicate() -> None:
    from datetime import datetime, timezone

    from integrations.calendar_api import AppleCalendarClient

    script = AppleCalendarClient()._events_range_script(
        datetime(2026, 8, 16, tzinfo=timezone.utc),
        datetime(2026, 8, 17, tzinfo=timezone.utc),
    )
    assert "start date < rangeEnd and end date > rangeStart" in script


@pytest.mark.asyncio
async def test_calendar_periodic_sync_requires_binding_and_persists_window(
    ingestion_db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from database import get_cached_calendar_events
    from database.ingestion import bind_connector, get_ingestion_source_state
    from integrations import calendar_api
    from integrations.calendar_api import CalendarQueryResult
    from jarvis.ingestion.service import run_ingestion_maintenance_once

    calls: list[tuple[str, str]] = []

    class _Calendar:
        async def get_events_result(self, start: str, end: str) -> CalendarQueryResult:
            calls.append((start, end))
            return CalendarQueryResult(
                status="ok",
                events=(
                    {
                        "uid": "spanning-event",
                        "title": "Projet Atlas",
                        "start": start,
                        "end": end,
                        "calendar": "Travail",
                    },
                ),
            )

    monkeypatch.setattr(calendar_api, "calendar_client", _Calendar())
    assert get_ingestion_source_state("calendar") is None
    history_start = datetime.now(timezone.utc) - timedelta(days=800)
    bind_connector(
        "calendar",
        consent_source="explicit_test",
        sync_interval_seconds=15,
        settings={
            "history_start_utc": history_start.isoformat(),
            "backfill_chunk_days": 365,
        },
    )
    summaries = [await run_ingestion_maintenance_once() for _ in range(3)]

    assert all(summary["errors"] == {} for summary in summaries)
    assert len(calls) == 3
    assert all(
        datetime.fromisoformat(end) - datetime.fromisoformat(start)
        <= timedelta(days=365)
        for start, end in calls
    )
    assert get_cached_calendar_events()[0]["external_id"] == "spanning-event"
    state = get_ingestion_source_state("calendar")
    assert state is not None
    assert state.status == "idle"
    assert state.completeness == "complete"
    assert state.cursor["full_history"] is True
    assert state.cursor["backfill_pending"] is False
    assert len(state.cursor["coverage_windows"]) == 1
    assert state.coverage_start_utc == history_start.isoformat()
    assert state.item_count == 1
