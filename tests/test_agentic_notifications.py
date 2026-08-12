"""Notifications génériques du cycle de vie agentique."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import config
import database
from database.agentic import AgenticRepository
from jarvis.agentic.models import (
    AgenticRun,
    AgenticRunStatus,
    ApprovalRequest,
    VerificationEvidence,
    VerificationResult,
    VerificationVerdict,
)
from jarvis.agentic.registry import RuntimeRegistry
from jarvis.agentic.service import AgenticService
from jarvis.event_bus import EventBus, event_bus
from jarvis.notification_service import NotificationService


@pytest.fixture
def agentic_notification_db(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> Path:
    db_path = tmp_path / "agentic-notifications.db"
    monkeypatch.setattr(config, "DB_PATH", str(db_path))
    monkeypatch.setattr(database, "DB_PATH", db_path)
    monkeypatch.setattr(database, "_dispatch_push_notification", lambda *args: None)
    database.init_db()
    return db_path


def _service() -> AgenticService:
    return AgenticService(
        repository=AgenticRepository(),
        registry=RuntimeRegistry(manifests=()),
        bus=EventBus(),
        notifications=NotificationService(),
    )


async def _create_run(service: AgenticService) -> AgenticRun:
    return await service.create_run(
        title="Prompt privé SECRET_TOKEN=ne-jamais-notifier",
        runtime_id="provider-interne-non-affiché",
    )


async def _transition_to(
    service: AgenticService,
    target: AgenticRunStatus,
) -> AgenticRun:
    run = await _create_run(service)
    paths = {
        AgenticRunStatus.BLOCKED: (
            AgenticRunStatus.QUEUED,
            AgenticRunStatus.PROVISIONING,
            AgenticRunStatus.BLOCKED,
        ),
        AgenticRunStatus.COMPLETED: (
            AgenticRunStatus.QUEUED,
            AgenticRunStatus.PROVISIONING,
            AgenticRunStatus.RUNNING,
            AgenticRunStatus.VERIFYING,
            AgenticRunStatus.COMPLETED,
        ),
    }
    for status in paths.get(target, (target,)):
        verification = None
        if status is AgenticRunStatus.COMPLETED:
            verification = VerificationResult(
                verdict=VerificationVerdict.PASS,
                verifier="jarvis-test",
                summary="Résumé provider privé SECRET_TOKEN=ne-jamais-notifier",
                evidence=(
                    VerificationEvidence(
                        check="test",
                        passed=True,
                        summary="preuve persistée",
                    ),
                ),
            )
        run = await service._transition(run, status, verification=verification)
    return run


@pytest.mark.asyncio
async def test_approval_notification_is_generic_redacted_and_idempotent(
    agentic_notification_db: Path,
) -> None:
    await event_bus.wait_until_idle()
    service = _service()
    run = await _transition_to(service, AgenticRunStatus.BLOCKED)
    run = await service._transition(run, AgenticRunStatus.RUNNING)
    approval = ApprovalRequest(
        approval_id="approval-secret-bearing-id",
        run_id=run.run_id,
        action="Publier le prompt privé SECRET_TOKEN=approbation",
        tool="provider.tool.secret",
        summary="Contenu sensible qui ne doit pas atteindre la notification",
        sanitized_arguments={"token": "SECRET_TOKEN=argument"},
        risks=("Exposition du prompt privé",),
    )

    await service.request_approval(approval)
    current = service.get(run.run_id)
    assert current is not None
    approval_event = next(
        event
        for event in service.events(run.run_id)
        if event.type == "agent.approval.requested"
    )
    with database.get_db() as conn:
        conn.execute(
            "UPDATE notifications SET created_at = '2000-01-01 00:00:00'"
        )
    service._notify_for_event(current, approval_event)
    await service._record_and_emit(
        current,
        approval_event.type,
        approval_event.payload,
        external_event_id=f"approval:{approval.approval_id}:requested",
    )
    await event_bus.wait_until_idle()

    rows = NotificationService().get_recent()
    approval_rows = [row for row in rows if row["title"] == "Approbation agentique requise"]
    assert len(approval_rows) == 1
    notification = approval_rows[0]
    assert notification["source"] == "agentic"
    assert notification["priority"] == "high"
    assert notification["content"] == (
        "Une action sensible attend votre décision dans JARVIS."
    )
    serialized = " ".join(str(value) for value in notification.values())
    assert "SECRET_TOKEN" not in serialized
    assert "provider.tool" not in serialized
    assert "approval-secret-bearing-id" not in serialized
    assert notification["email_id"].startswith("idempotency:")


@pytest.mark.asyncio
async def test_expired_approval_notification_is_canonical_and_idempotent(
    agentic_notification_db: Path,
) -> None:
    service = _service()
    run = await _transition_to(service, AgenticRunStatus.BLOCKED)
    run = await service._transition(run, AgenticRunStatus.RUNNING)
    now = datetime.now(timezone.utc)
    await service.request_approval(
        ApprovalRequest(
            approval_id="approval-expired-secret-id",
            run_id=run.run_id,
            action="Publier SECRET_TOKEN=expiration",
            tool="provider.expiry.secret",
            summary="Résumé privé",
            expires_at=now + timedelta(minutes=1),
        )
    )

    await service.sweep_expired_approvals(now=now + timedelta(minutes=2))
    await service.sweep_expired_approvals(now=now + timedelta(minutes=3))
    rows = NotificationService().get_recent()
    expiry_rows = [
        row for row in rows if row["title"] == "Approbation agentique expirée"
    ]
    assert len(expiry_rows) == 1
    notification = expiry_rows[0]
    assert notification["priority"] == "high"
    assert notification["content"] == (
        "Le délai de confirmation est dépassé ; le run attend votre intervention."
    )
    serialized = " ".join(str(value) for value in notification.values())
    assert "SECRET_TOKEN" not in serialized
    assert "provider.expiry" not in serialized
    assert "approval-expired-secret-id" not in serialized
    await service.dispose()


@pytest.mark.parametrize(
    ("status", "expected_title", "expected_priority"),
    [
        (AgenticRunStatus.BLOCKED, "Run agentique bloqué", "high"),
        (AgenticRunStatus.CANCELLED, "Run agentique annulé", "medium"),
        (AgenticRunStatus.COMPLETED, "Run agentique terminé", "medium"),
        (AgenticRunStatus.EXPIRED, "Run agentique expiré", "high"),
        (AgenticRunStatus.FAILED, "Échec du run agentique", "high"),
        (
            AgenticRunStatus.PROVIDER_UNAVAILABLE,
            "Runtime agentique indisponible",
            "high",
        ),
    ],
)
@pytest.mark.asyncio
async def test_important_run_notifications_are_canonical_and_not_replayed(
    agentic_notification_db: Path,
    status: AgenticRunStatus,
    expected_title: str,
    expected_priority: str,
) -> None:
    await event_bus.wait_until_idle()
    service = _service()
    run = await _transition_to(service, status)
    status_event = service.events(run.run_id)[-1]

    await service._record_and_emit(
        run,
        status_event.type,
        status_event.payload,
        event_id=status_event.event_id,
    )
    await event_bus.wait_until_idle()

    rows = NotificationService().get_recent()
    assert len(rows) == 1
    notification = rows[0]
    assert notification["source"] == "agentic"
    assert notification["title"] == expected_title
    assert notification["priority"] == expected_priority
    serialized = " ".join(str(value) for value in notification.values())
    assert "SECRET_TOKEN" not in serialized
    assert "provider-interne" not in serialized
    assert notification["email_id"].startswith("idempotency:")
