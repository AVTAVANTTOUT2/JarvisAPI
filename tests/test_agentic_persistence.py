"""Persistance, idempotence, redaction et isolation des runs agentiques."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sqlite3 as stdlib_sqlite

import pytest

import config
import database
from database.agentic import (
    AGENTIC_SCHEMA,
    AgenticIdempotencyConflict,
    AgenticPersistenceConflict,
    AgenticRepository,
    ApprovalAlreadyDecided,
    ApprovalExpired,
    migrate_agentic_tables,
)
from jarvis.agentic.models import (
    AgenticRun,
    ApprovalDecision,
    ApprovalRequest,
    Artifact,
    RuntimeEvent,
)


@pytest.fixture
def agentic_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    path = tmp_path / "agentic.db"
    monkeypatch.setattr(config, "DB_PATH", str(path))
    monkeypatch.setattr(database, "DB_PATH", path)
    database.init_db()
    return path


def _run(
    *, profile_id: str = "default", idempotency_key: str | None = None
) -> AgenticRun:
    return AgenticRun.new(
        profile_id=profile_id,
        origin="user",
        channel="web",
        runtime_id="fake-runtime",
        title="Préparer un rapport",
        selected_context={"summary": "minimum", "password": "do-not-store"},
        idempotency_key=idempotency_key,
    )


def test_fresh_schema_and_migration_are_idempotent_and_provider_neutral(
    agentic_db: Path,
):
    database.init_db()
    with database.get_db() as conn:
        rows = conn.execute(
            "SELECT name, sql FROM sqlite_master WHERE type = 'table' AND name LIKE 'agent_%'"
        ).fetchall()
    names = {row["name"] for row in rows}
    assert {
        "agent_runs",
        "agent_events",
        "agent_event_inbox",
        "agent_steps",
        "agent_approvals",
        "agent_artifacts",
        "agent_capability_grants",
        "agent_checkpoints",
        "agent_metrics",
    }.issubset(names)
    assert all(
        "opencode" not in (row["sql"] or "").lower()
        for row in rows
        if row["name"] in names
    )


def test_legacy_idempotency_rows_are_safely_backfilled(
    agentic_db: Path,
    tmp_path: Path,
):
    legacy_path = tmp_path / "legacy-agentic.db"
    conn = stdlib_sqlite.connect(legacy_path)
    conn.row_factory = stdlib_sqlite.Row
    try:
        conn.executescript(AGENTIC_SCHEMA.replace("    idempotency_digest TEXT,\n", ""))
        template = _run(idempotency_key="legacy-request")
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """
            INSERT INTO agent_runs (
                run_id, profile_id, task_id, conversation_id, origin, channel,
                device, locale, timezone, runtime_id, provider_session_id,
                status, phase, category, title, permissions_json, context_json,
                budget_json, workspace, idempotency_key, error_json,
                verification_json, created_at, started_at, finished_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                template.run_id,
                template.profile_id,
                None,
                None,
                template.origin,
                template.channel,
                None,
                template.locale,
                template.timezone,
                template.runtime_id,
                None,
                template.status.value,
                template.phase,
                template.category.value,
                template.title,
                "[]",
                "{}",
                json.dumps(vars(template.budget), default=str),
                None,
                template.idempotency_key,
                None,
                None,
                now,
                None,
                None,
                now,
            ),
        )
        migrate_agentic_tables(conn)
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(agent_runs)")}
        migrated = conn.execute(
            "SELECT idempotency_digest FROM agent_runs WHERE run_id = ?",
            (template.run_id,),
        ).fetchone()
        assert "idempotency_digest" in columns
        assert migrated is not None
        assert len(migrated["idempotency_digest"]) == 64
    finally:
        conn.close()


def test_run_idempotency_profile_isolation_and_redacted_context(agentic_db: Path):
    repository = AgenticRepository()
    created, first = repository.create_run(_run(idempotency_key="same-request"))
    duplicate, second = repository.create_run(_run(idempotency_key="same-request"))
    assert first is True
    assert second is False
    assert duplicate.run_id == created.run_id
    assert duplicate.selected_context["password"] == "[REDACTED]"

    with database.use_profile("other"):
        database.init_db()
        assert repository.get_run(created.run_id) is None
        other, was_created = repository.create_run(_run(profile_id="other"))
        assert was_created is True
        assert repository.get_run(other.run_id) == other

    assert repository.get_run(created.run_id) is not None


def test_runtime_event_inbox_reclaims_a_crashed_consumer_without_stale_ack(
    agentic_db: Path,
):
    repository = AgenticRepository()
    run, _created = repository.create_run(_run())
    event = RuntimeEvent(
        event_id="runtime-event-crash-window",
        run_id=run.run_id,
        sequence=0,
        type="agent.run.phase_changed",
        timestamp=datetime.now(timezone.utc),
        payload={"phase": "runtime_failed"},
        external_event_id="provider-event-crash-window",
    )

    stored, created = repository.append_event(event, requires_processing=True)
    replayed, replay_created = repository.append_event(
        replace(event, event_id="provider-replayed-with-another-id"),
        requires_processing=True,
    )
    assert created is True
    assert replay_created is False
    assert replayed.event_id == stored.event_id
    assert repository.event_processing_state(run.run_id, stored.event_id) == "pending"

    claimed_at = datetime.now(timezone.utc)
    first_claim = repository.claim_event_processing(
        run.run_id,
        stored.event_id,
        now=claimed_at,
        lease_seconds=60,
    )
    assert first_claim is not None
    assert repository.event_processing_state(run.run_id, stored.event_id) == "processing"
    assert (
        repository.claim_event_processing(
            run.run_id,
            stored.event_id,
            now=claimed_at + timedelta(seconds=59),
            lease_seconds=60,
        )
        is None
    )

    second_claim = repository.claim_event_processing(
        run.run_id,
        stored.event_id,
        now=claimed_at + timedelta(seconds=61),
        lease_seconds=60,
    )
    assert second_claim is not None
    assert second_claim != first_claim
    assert (
        repository.complete_event_processing(
            run.run_id,
            stored.event_id,
            first_claim,
        )
        is False
    )
    assert repository.complete_event_processing(
        run.run_id,
        stored.event_id,
        second_claim,
    )
    assert repository.event_processing_state(run.run_id, stored.event_id) == "processed"


def test_approval_outbox_reclaims_only_a_stale_delivery_lease(agentic_db: Path):
    repository = AgenticRepository()
    run, _created = repository.create_run(_run())
    approval = repository.create_approval(
        ApprovalRequest(
            approval_id="approval-stale-outbox",
            run_id=run.run_id,
            action="Publier",
            tool="publisher",
            summary="Confirmer la publication",
        )
    )
    claimed_at = datetime.now(timezone.utc)
    repository.decide_approval(
        approval.approval_id,
        ApprovalDecision.APPROVED,
        decided_by="user",
        decision_id="decision-stale-outbox",
        now=claimed_at,
    )

    assert repository.claim_approval_delivery(
        approval.approval_id,
        now=claimed_at,
        lease_seconds=60,
    )
    assert not repository.claim_approval_delivery(
        approval.approval_id,
        now=claimed_at + timedelta(seconds=59),
        lease_seconds=60,
    )
    assert repository.claim_approval_delivery(
        approval.approval_id,
        now=claimed_at + timedelta(seconds=61),
        lease_seconds=60,
    )
    with pytest.raises(AgenticPersistenceConflict):
        repository.complete_approval_delivery(
            approval.approval_id,
            now=claimed_at + timedelta(seconds=61, milliseconds=500),
            claim_started_at=claimed_at,
        )
    repository.complete_approval_delivery(
        approval.approval_id,
        now=claimed_at + timedelta(seconds=62),
        claim_started_at=claimed_at + timedelta(seconds=61),
    )
    assert not repository.claim_approval_delivery(
        approval.approval_id,
        now=claimed_at + timedelta(minutes=5),
        lease_seconds=60,
    )
    assert repository.approval_delivery_status(approval.approval_id) == "delivered"


def test_idempotency_digest_rejects_payload_reuse_and_is_race_safe(
    agentic_db: Path,
):
    repository = AgenticRepository()
    key = "digest-bound-request"
    created, first = repository.create_run(_run(idempotency_key=key))
    assert first is True
    assert created.idempotency_digest is not None
    with pytest.raises(AgenticIdempotencyConflict):
        repository.create_run(
            replace(
                _run(idempotency_key=key),
                title="Un autre payload",
            )
        )

    race_key = "concurrent-identical-request"

    def create_identical(_index: int) -> tuple[str, bool]:
        run, was_created = AgenticRepository().create_run(
            _run(idempotency_key=race_key)
        )
        return run.run_id, was_created

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(create_identical, range(8)))
    assert sum(was_created for _run_id, was_created in results) == 1
    assert len({run_id for run_id, _was_created in results}) == 1


def test_events_are_monotonic_idempotent_and_strictly_neutralized(agentic_db: Path):
    repository = AgenticRepository()
    run, _ = repository.create_run(_run())
    first = RuntimeEvent.new(
        run_id=run.run_id,
        type="agent.tool.started",
        external_event_id="provider-1",
        payload={
            "status": "running",
            "phase": "tool",
            "tool": "browser",
            "objective": "raw user objective",
            "tool_result": "Authorization: Bearer token-value",
        },
    )
    stored, created = repository.append_event(first)
    assert created is True
    assert stored.sequence == 1
    assert stored.payload["run_id"] == run.run_id
    assert "objective" not in stored.payload
    assert "tool_result" not in stored.payload

    duplicate, created_again = repository.append_event(
        replace(first, event_id="different-event-id")
    )
    assert created_again is False
    assert duplicate.event_id == stored.event_id

    with pytest.raises(AgenticPersistenceConflict, match="séquence attendue 2"):
        repository.append_event(
            RuntimeEvent.new(
                run_id=run.run_id,
                type="agent.tool.completed",
                sequence=8,
            )
        )

    second_run, _ = repository.create_run(_run())
    second_event = RuntimeEvent.new(
        run_id=second_run.run_id,
        type="agent.tool.started",
        external_event_id="provider-1",
    )
    second_stored, second_created = repository.append_event(
        replace(second_event, event_id=stored.event_id)
    )
    assert second_created is True
    assert second_stored.sequence == 1

    with database.use_profile("other"):
        database.init_db()
        other_run, _ = repository.create_run(_run(profile_id="other"))
        other_event = RuntimeEvent.new(
            run_id=other_run.run_id,
            type="agent.tool.started",
            external_event_id="provider-1",
        )
        other_stored, other_created = repository.append_event(
            replace(other_event, event_id=stored.event_id)
        )
        assert other_created is True
        assert other_stored.sequence == 1


def test_event_sequences_remain_unique_under_concurrent_writers(agentic_db: Path):
    repository = AgenticRepository()
    run, _ = repository.create_run(_run())

    def append(index: int) -> int:
        stored, created = repository.append_event(
            RuntimeEvent.new(
                run_id=run.run_id,
                type="agent.tool.started",
                external_event_id=f"external-{index}",
                payload={"tool": f"tool-{index}"},
            )
        )
        assert created is True
        return stored.sequence

    with ThreadPoolExecutor(max_workers=4) as pool:
        sequences = list(pool.map(append, range(8)))
    assert sorted(sequences) == list(range(1, 9))
    assert [event.sequence for event in repository.list_events(run.run_id)] == list(
        range(1, 9)
    )


def test_approval_is_expiring_one_shot_and_decision_is_idempotent(agentic_db: Path):
    repository = AgenticRepository()
    run, _ = repository.create_run(_run())
    approval = repository.create_approval(
        ApprovalRequest(
            approval_id="approval-1",
            run_id=run.run_id,
            action="Envoyer le brouillon",
            tool="mail.send",
            summary="Premier envoi",
            sanitized_arguments={"recipient": "contact", "token": "hidden"},
            risks=("effet externe",),
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        )
    )
    assert approval.sanitized_arguments["token"] == "[REDACTED]"

    decided = repository.decide_approval(
        approval.approval_id,
        ApprovalDecision.APPROVED,
        decided_by="user",
        decision_id="decision-1",
    )
    assert decided.decision is ApprovalDecision.APPROVED
    assert decided.decision_id == "decision-1"
    assert repository.approval_delivery_status(approval.approval_id) == "pending"
    assert repository.claim_approval_delivery(approval.approval_id) is True
    assert repository.claim_approval_delivery(approval.approval_id) is False
    repository.complete_approval_delivery(approval.approval_id)
    assert repository.approval_delivery_status(approval.approval_id) == "delivered"
    same = repository.decide_approval(
        approval.approval_id,
        ApprovalDecision.APPROVED,
        decided_by="user",
        decision_id="decision-1",
    )
    assert same == decided
    with pytest.raises(AgenticPersistenceConflict):
        repository.decide_approval(
            approval.approval_id,
            ApprovalDecision.DENIED,
            decided_by="other",
            decision_id="decision-1",
        )
    second = repository.create_approval(
        ApprovalRequest(
            approval_id="approval-2",
            run_id=run.run_id,
            action="Publier le rapport",
            tool="report.publish",
            summary="Deuxième effet externe",
        )
    )
    with pytest.raises(AgenticPersistenceConflict):
        repository.decide_approval(
            second.approval_id,
            ApprovalDecision.APPROVED,
            decided_by="other",
            decision_id="decision-1",
        )
    with pytest.raises(ApprovalAlreadyDecided):
        repository.decide_approval(
            approval.approval_id,
            ApprovalDecision.DENIED,
            decided_by="other",
            decision_id="decision-2",
        )

    expiry = datetime.now(timezone.utc) + timedelta(minutes=1)
    expiring = repository.create_approval(
        ApprovalRequest(
            approval_id="approval-expiring",
            run_id=run.run_id,
            action="Action courte",
            tool="effect.short",
            summary="Confirmation avec échéance",
            expires_at=expiry,
        )
    )
    assert expiring.expires_at == expiry
    assert expiring.risks
    with pytest.raises(ApprovalExpired):
        repository.decide_approval(
            expiring.approval_id,
            ApprovalDecision.APPROVED,
            decided_by="late-user",
            decision_id="decision-expired",
            now=expiry + timedelta(seconds=1),
        )
    expired = repository.get_approval(expiring.approval_id)
    assert expired is not None
    assert expired.decision is ApprovalDecision.EXPIRED
    assert repository.approval_delivery_status(expiring.approval_id) is None

    with database.use_profile("other"):
        database.init_db()
        other_run, _ = repository.create_run(_run(profile_id="other"))
        other_approval = repository.create_approval(
            ApprovalRequest(
                approval_id="approval-other",
                run_id=other_run.run_id,
                action="Action isolée",
                tool="isolated.tool",
                summary="Profil distinct",
            )
        )
        other_decided = repository.decide_approval(
            other_approval.approval_id,
            ApprovalDecision.APPROVED,
            decided_by="other-user",
            decision_id="decision-1",
        )
        assert other_decided.decision is ApprovalDecision.APPROVED


def test_artifact_checkpoint_and_metric_redact_metadata(agentic_db: Path):
    repository = AgenticRepository()
    run, _ = repository.create_run(_run())
    artifact, created = repository.add_artifact(
        Artifact(
            artifact_id="artifact-1",
            run_id=run.run_id,
            type="report",
            reference="report.json",
            metadata={"format": "json", "secret": "hidden"},
        )
    )
    assert created is True
    assert artifact.metadata["secret"] == "[REDACTED]"
    assert repository.list_artifacts(run.run_id) == [artifact]
    assert repository.save_checkpoint(
        checkpoint_id="checkpoint-1",
        run_id=run.run_id,
        sequence=1,
        state={"phase": "running", "token": "hidden"},
        checksum="abc",
    )
    step = repository.record_step(
        step_id="step-1",
        run_id=run.run_id,
        sequence=1,
        title="Inspecter",
        status="running",
        metadata={"token": "hidden"},
    )
    assert step["metadata"]["token"] == "[REDACTED]"
    assert repository.grant_capability(
        grant_id="grant-1",
        run_id=run.run_id,
        capability="workspace.read",
        scope="workspace:read",
        granted_by="policy",
        metadata={"secret": "hidden"},
    )
    grants = repository.list_capability_grants(run.run_id)
    assert grants[0]["metadata"]["secret"] == "[REDACTED]"
    assert repository.revoke_capability("grant-1") is True
    assert repository.list_capability_grants(run.run_id) == []
    repository.record_metric(
        run_id=run.run_id,
        metric="tool_calls",
        value=1,
        metadata={"secret": "hidden"},
    )
    with database.get_db() as conn:
        checkpoint = conn.execute(
            "SELECT state_json FROM agent_checkpoints WHERE checkpoint_id = ?",
            ("checkpoint-1",),
        ).fetchone()
        metric = conn.execute(
            "SELECT metadata_json FROM agent_metrics WHERE run_id = ?",
            (run.run_id,),
        ).fetchone()
    assert json.loads(checkpoint["state_json"])["token"] == "[REDACTED]"
    assert json.loads(metric["metadata_json"])["secret"] == "[REDACTED]"
