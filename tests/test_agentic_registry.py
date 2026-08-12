"""Discovery dynamique, service singleton et diffusion sûre des runtimes."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

import config
import database
from jarvis.agentic import (
    AgenticRunStatus,
    AgenticRequestCategory,
    AgenticService,
    ApprovalDecision,
    ApprovalRequest,
    RunBudget,
    RuntimeEvent,
    RuntimePluginError,
    RuntimeRegistry,
    VerificationVerdict,
    discover_runtime_plugins,
)
from jarvis.agentic.events import build_agentic_bus_event
from jarvis.agentic.models import VerificationEvidence, VerificationResult
from jarvis.event_bus import EventBus, event_bus
from websocket_registry import add_websocket, remove_websocket


PLUGIN_CODE = """
from jarvis.agentic.models import RuntimeHealth, RuntimeHealthStatus

class FakeRuntime:
    runtime_id = "fake-runtime"
    capabilities = ()

    def __init__(self):
        self.calls = []

    async def health(self):
        return RuntimeHealth(RuntimeHealthStatus.HEALTHY, version="1.0.0")

    async def create_run(self, run, context):
        self.calls.append(("create", run.run_id, context.channel))
        return "opaque-session"

    async def start(self, run):
        self.calls.append(("start", run.run_id))

    async def pause(self, run_id):
        self.calls.append(("pause", run_id))

    async def resume(self, run_id):
        self.calls.append(("resume", run_id))

    async def cancel(self, run_id):
        self.calls.append(("cancel", run_id))

    async def answer_approval(self, run_id, approval):
        self.calls.append(("approval", run_id, approval.decision.value))

    async def stream_events(self, run_id):
        if False:
            yield run_id

    async def get_artifacts(self, run_id):
        return ()

    async def dispose(self):
        self.calls.append(("dispose",))

def create_runtime(manifest):
    return FakeRuntime()
"""


COMPLETING_PLUGIN_CODE = """
from datetime import datetime, timezone
import hashlib
from pathlib import Path
from jarvis.agentic.models import (
    Artifact,
    RuntimeEvent,
    RuntimeHealth,
    RuntimeHealthStatus,
)

class FakeRuntime:
    runtime_id = "fake-runtime"
    capabilities = ()

    def __init__(self):
        self.workspaces = {}

    async def health(self):
        return RuntimeHealth(RuntimeHealthStatus.HEALTHY, version="1.0.0")

    async def create_run(self, run, context):
        workspace = Path(run.workspace)
        workspace.mkdir(parents=True, exist_ok=True)
        (workspace / "verified-report.txt").write_bytes(b"verified report")
        self.workspaces[run.run_id] = workspace
        return "opaque-session"

    async def start(self, run):
        return None

    async def pause(self, run_id):
        return None

    async def resume(self, run_id):
        return None

    async def cancel(self, run_id):
        return None

    async def answer_approval(self, run_id, approval):
        return None

    async def stream_events(self, run_id):
        yield RuntimeEvent(
            event_id=f"runtime-completed:{run_id}",
            run_id=run_id,
            sequence=1,
            type="agent.run.completed",
            timestamp=datetime.now(timezone.utc),
            payload={"progress": 1.0, "objective": "must-not-escape"},
            external_event_id=f"runtime-completed:{run_id}",
        )

    async def get_artifacts(self, run_id):
        content = (self.workspaces[run_id] / "verified-report.txt").read_bytes()
        return (
            Artifact(
                artifact_id=f"artifact:{run_id}",
                run_id=run_id,
                type="report_file",
                reference="verified-report.txt",
                sha256=hashlib.sha256(content).hexdigest(),
                size_bytes=len(content),
            ),
        )

    async def dispose(self):
        return None

def create_runtime(manifest):
    return FakeRuntime()
"""

FAILING_PLUGIN_CODE = COMPLETING_PLUGIN_CODE.replace(
    'type="agent.run.completed"',
    'type="agent.run.failed"',
)
BROKEN_FACTORY_PLUGIN_CODE = PLUGIN_CODE.replace(
    "    return FakeRuntime()",
    '    raise RuntimeError("factory unavailable")',
)


@pytest.fixture
def agentic_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    path = tmp_path / "registry.db"
    monkeypatch.setattr(config, "DB_PATH", str(path))
    monkeypatch.setattr(database, "DB_PATH", path)
    database.init_db()
    return path


def _plugin(tmp_path: Path, *, code: str = PLUGIN_CODE) -> Path:
    integrations = tmp_path / "integrations"
    plugin = integrations / "fake"
    plugin.mkdir(parents=True)
    (plugin / "register.py").write_text(code, encoding="utf-8")
    (plugin / "plugin.json").write_text(
        json.dumps(
            {
                "manifest_version": 1,
                "runtime": {
                    "id": "fake-runtime",
                    "name": "Fake runtime",
                    "version": "1.0.0",
                    "entrypoint": "register.py:create_runtime",
                    "capabilities": [{"name": "read", "scope": "workspace:read"}],
                },
            }
        ),
        encoding="utf-8",
    )
    return integrations


async def _wait_for_status(
    service: AgenticService,
    run_id: str,
    status: AgenticRunStatus,
) -> None:
    for _ in range(100):
        current = service.get(run_id)
        if current is not None and current.status is status:
            return
        await asyncio.sleep(0.01)
    current = service.get(run_id)
    assert current is not None
    assert current.status is status


@pytest.mark.asyncio
async def test_discovery_does_not_import_and_registry_loads_factory_lazily(
    tmp_path: Path,
):
    integrations = _plugin(tmp_path)
    manifests = discover_runtime_plugins(integrations)
    assert len(manifests) == 1
    manifest = manifests[0]
    assert manifest.runtime_id == "fake-runtime"
    assert manifest.capabilities[0].scope == "workspace:read"

    registry = RuntimeRegistry(manifests)
    runtime = await registry.get("fake-runtime")
    assert runtime is not None
    assert runtime.runtime_id == "fake-runtime"
    assert await registry.get("missing") is None
    await registry.dispose()
    assert runtime.calls[-1] == ("dispose",)


@pytest.mark.asyncio
async def test_runtime_status_reports_native_client_run_counts(
    agentic_db: Path,
    tmp_path: Path,
):
    registry = RuntimeRegistry(discover_runtime_plugins(_plugin(tmp_path)))
    service = AgenticService(registry=registry, bus=EventBus())
    try:
        queued = await service.create_run(
            title="En file",
            runtime_id="fake-runtime",
            idempotency_key="runtime-status-queued",
        )
        active = await service.create_run(
            title="Active",
            runtime_id="fake-runtime",
            idempotency_key="runtime-status-active",
        )
        await service.start_run(active.run_id)

        statuses = await service.runtime_status()
        assert len(statuses) == 1
        status = statuses[0]
        assert status["available"] is True
        assert status["active_runs"] == 1
        assert status["queued_runs"] == 1
        assert status["label"] == "Fake runtime"
        assert status["checked_at"]
        assert queued.status is AgenticRunStatus.CREATED
        observability = service.observability_summary()
        assert observability["runs_by_status"]["running"] == 1
        assert observability["runs_by_status"]["created"] == 1
        assert observability["metrics"]["run.status.running"]["samples"] == 1
    finally:
        await service.dispose()


@pytest.mark.asyncio
async def test_service_lifecycle_events_and_wait_do_not_replay(
    agentic_db: Path,
    tmp_path: Path,
):
    registry = RuntimeRegistry(discover_runtime_plugins(_plugin(tmp_path)))
    bus = EventBus()
    received = []

    @bus.on("*")
    async def capture(event):
        received.append(event)

    service = AgenticService(registry=registry, bus=bus)
    assert service.resolve_runtime_id("auto") == "fake-runtime"
    run = await service.create_and_start(
        title="Rapport sûr",
        runtime_id="auto",
        channel="voice",
        selected_context={"objective": "raw", "summary": "utile"},
        idempotency_key="voice-request-1",
    )
    assert run.status is AgenticRunStatus.CREATED

    for _ in range(50):
        current = service.get(run.run_id)
        if current is not None and current.status is AgenticRunStatus.RUNNING:
            break
        await asyncio.sleep(0.01)
    current = service.get(run.run_id)
    assert current is not None
    assert current.status is AgenticRunStatus.RUNNING
    assert current.provider_session_id == "opaque-session"
    assert current.selected_context["request"] == "Rapport sûr"

    paused = await service.pause(run.run_id)
    assert paused.status is AgenticRunStatus.PAUSED
    resumed = await service.resume(run.run_id)
    assert resumed.status is AgenticRunStatus.RUNNING
    cancelled = await service.cancel(run.run_id)
    assert cancelled.status is AgenticRunStatus.CANCELLED
    assert await service.wait_for_terminal(run.run_id, timeout=0.5) == cancelled
    await bus.wait_until_idle()

    payloads = [event.payload for event in received]
    assert payloads
    assert all("objective" not in payload for payload in payloads)
    assert all("tool_result" not in payload for payload in payloads)
    assert any(event.event_type == "agent.run.created" for event in received)
    assert any(event.event_type == "agent.run.cancelled" for event in received)
    sequences = [event.sequence for event in service.events(run.run_id)]
    assert sequences == list(range(1, len(sequences) + 1))

    refused = await service.create_run(
        title="Capacité refusée",
        runtime_id="fake-runtime",
        permissions=("admin",),
    )
    refused = await service.start_run(refused.run_id)
    assert refused.status is AgenticRunStatus.FAILED
    assert refused.error is not None
    assert refused.error.code.value == "permission_denied"

    expired = await service.create_run(
        title="Deadline passée",
        runtime_id="fake-runtime",
        budget=RunBudget(deadline=datetime.now(timezone.utc) - timedelta(seconds=1)),
    )
    expired = await service.start_run(expired.run_id)
    assert expired.status is AgenticRunStatus.EXPIRED

    recursive = await service.create_run(
        title="Ne pas reclassifier",
        origin="agent_runtime",
    )
    recursive = await service.start_run(recursive.run_id)
    assert recursive.status is AgenticRunStatus.FAILED
    assert recursive.error is not None
    assert recursive.error.code.value == "invalid_request"
    await service.dispose()


@pytest.mark.asyncio
async def test_runtime_completion_requires_deterministic_verifier_pass(
    agentic_db: Path,
    tmp_path: Path,
):
    integrations = _plugin(tmp_path, code=COMPLETING_PLUGIN_CODE)
    service = AgenticService(
        registry=RuntimeRegistry(discover_runtime_plugins(integrations)),
        bus=EventBus(),
    )
    workspace = tmp_path / "workspace"
    created = await service.create_and_start(
        title="Rapport à vérifier",
        workspace=workspace,
    )
    terminal = await service.wait_for_terminal(created.run_id, timeout=1.0)

    assert terminal.status is AgenticRunStatus.COMPLETED
    assert terminal.verification is not None
    assert terminal.verification.verdict is VerificationVerdict.PASS
    assert terminal.verification.verifier == "jarvis.verifier.code.v1"
    assert len(service.artifacts(created.run_id)) == 1

    events = service.events(created.run_id)
    types = [event.type for event in events]
    assert types.index("agent.run.verifying") < types.index("agent.run.completed")
    required = {
        "run_id",
        "status",
        "phase",
        "channel",
        "title",
        "progress",
        "needs_attention",
        "spoken_summary",
    }
    assert all(required <= set(event.payload) for event in events)
    assert all("objective" not in event.payload for event in events)
    await service.dispose()

@pytest.mark.asyncio
async def test_jarvis_owned_run_waits_for_durable_test_receipt(
    agentic_db: Path,
    tmp_path: Path,
):
    integrations = _plugin(tmp_path, code=COMPLETING_PLUGIN_CODE)
    service = AgenticService(
        registry=RuntimeRegistry(discover_runtime_plugins(integrations)),
        bus=EventBus(),
    )
    workspace = tmp_path / "jarvis-owned-workspace"
    created = await service.create_and_start(
        title="Modification à valider par JARVIS",
        workspace=workspace,
        selected_context={"jarvis_owns_delivery": True},
    )

    reviewing = await service.wait_for_jarvis_delivery(
        created.run_id,
        timeout=1.0,
    )
    assert reviewing.status is AgenticRunStatus.REVIEWING
    assert reviewing.phase == "awaiting_jarvis_validation"
    assert not reviewing.terminal
    assert "agent.run.completed" not in {
        event.type for event in service.events(created.run_id)
    }
    forged = VerificationResult(
        verdict=VerificationVerdict.PASS,
        verifier="forged",
        summary="affirmation sans gate",
        evidence=(
            VerificationEvidence(
                check="forged",
                passed=True,
                summary="preuve non liée à JARVIS",
            ),
        ),
    )
    with pytest.raises(ValueError, match="reçu de tests JARVIS"):
        await service.apply_verification_result(created.run_id, forged)
    assert service.get(created.run_id).status is AgenticRunStatus.REVIEWING
    reconciled = await service.reconcile_nonterminal()
    assert next(run for run in reconciled if run.run_id == created.run_id).status is (
        AgenticRunStatus.REVIEWING
    )

    receipt_id = f"receipt:test:devagent:{created.run_id}"
    first = await service.record_verification_receipt(
        created.run_id,
        kind="test",
        subject="Gates techniques DevAgent",
        details={"returncode": 0},
        artifact_id=receipt_id,
    )
    replay = await service.record_verification_receipt(
        created.run_id,
        kind="test",
        subject="Gates techniques DevAgent",
        details={"returncode": 0},
        artifact_id=receipt_id,
    )
    assert replay == first
    assert sum(
        artifact.artifact_id == receipt_id
        for artifact in service.artifacts(created.run_id)
    ) == 1

    terminal = await service.verify_run(created.run_id)
    assert terminal.status is AgenticRunStatus.COMPLETED
    assert terminal.verification is not None
    assert terminal.verification.verdict is VerificationVerdict.PASS
    await service.dispose()


@pytest.mark.asyncio
async def test_external_effect_completion_requires_persisted_jarvis_receipt(
    agentic_db: Path,
    tmp_path: Path,
):
    integrations = _plugin(tmp_path, code=COMPLETING_PLUGIN_CODE)
    service = AgenticService(
        registry=RuntimeRegistry(discover_runtime_plugins(integrations)),
        bus=EventBus(),
    )
    workspace = tmp_path / "external-workspace"
    created = await service.create_run(
        title="Publier le rapport",
        category=AgenticRequestCategory.AGENTIC_EXTERNAL_EFFECT,
        workspace=workspace,
    )
    receipt = await service.record_verification_receipt(
        created.run_id,
        kind="effect",
        subject="publication:report-1",
        details={"receipt": "observed", "token": "secret"},
    )
    assert receipt.metadata["details"]["token"] == "[REDACTED]"
    await service.start_run(created.run_id)
    terminal = await service.wait_for_terminal(created.run_id, timeout=1.0)
    assert terminal.status is AgenticRunStatus.COMPLETED
    assert terminal.verification is not None
    assert terminal.verification.verdict is VerificationVerdict.PASS
    assert {artifact.type for artifact in service.artifacts(created.run_id)} >= {
        "jarvis_effect_receipt",
        "report_file",
    }
    await service.dispose()


@pytest.mark.asyncio
async def test_runtime_failure_is_terminal_without_provider_self_completion(
    agentic_db: Path,
    tmp_path: Path,
):
    integrations = _plugin(tmp_path, code=FAILING_PLUGIN_CODE)
    service = AgenticService(
        registry=RuntimeRegistry(discover_runtime_plugins(integrations)),
        bus=EventBus(),
    )
    created = await service.create_and_start(title="Échec runtime")
    terminal = await service.wait_for_terminal(created.run_id, timeout=1.0)
    assert terminal.status is AgenticRunStatus.FAILED
    assert terminal.verification is None
    assert "agent.run.completed" not in {
        event.type for event in service.events(created.run_id)
    }
    await service.dispose()


@pytest.mark.asyncio
async def test_concurrency_budget_admission_is_atomic(
    agentic_db: Path,
    tmp_path: Path,
):
    registry = RuntimeRegistry(discover_runtime_plugins(_plugin(tmp_path)))
    service = AgenticService(registry=registry, bus=EventBus())
    budget = RunBudget(concurrency_limit=1)
    first = await service.create_run(title="Premier", budget=budget)
    second = await service.create_run(title="Second", budget=budget)

    results = await asyncio.gather(
        service.start_run(first.run_id),
        service.start_run(second.run_id),
    )
    running = [run for run in results if run.status is AgenticRunStatus.RUNNING]
    queued = [run for run in results if run.status is AgenticRunStatus.QUEUED]
    assert len(running) == 1
    assert len(queued) == 1

    await service.cancel(running[0].run_id)
    for _ in range(50):
        admitted = service.get(queued[0].run_id)
        if admitted is not None and admitted.status is AgenticRunStatus.RUNNING:
            break
        await asyncio.sleep(0.01)
    assert admitted is not None
    assert admitted.status is AgenticRunStatus.RUNNING
    await service.cancel(admitted.run_id)
    await service.dispose()


@pytest.mark.asyncio
async def test_reconcile_never_replays_a_pending_cancellation(
    agentic_db: Path,
    tmp_path: Path,
):
    registry = RuntimeRegistry(discover_runtime_plugins(_plugin(tmp_path)))
    service = AgenticService(registry=registry, bus=EventBus())
    run = await service.create_run(title="Annulation interrompue")
    service.repository.transition_run(run.run_id, AgenticRunStatus.CANCELLING)

    reconciled = await service.reconcile_nonterminal()
    assert len(reconciled) == 1
    assert reconciled[0].status is AgenticRunStatus.FAILED
    assert reconciled[0].error is not None
    assert reconciled[0].error.code.value == "runtime_unavailable"
    assert registry._instances == {}
    await service.dispose()


@pytest.mark.asyncio
async def test_idempotent_create_does_not_replay_a_blocked_run(
    agentic_db: Path,
    tmp_path: Path,
):
    registry = RuntimeRegistry(discover_runtime_plugins(_plugin(tmp_path)))
    service = AgenticService(registry=registry, bus=EventBus())
    run = await service.create_run(
        title="Effet déjà tenté",
        idempotency_key="stable-request",
    )
    for status in (
        AgenticRunStatus.CLASSIFIED,
        AgenticRunStatus.QUEUED,
        AgenticRunStatus.PROVISIONING,
        AgenticRunStatus.RUNNING,
        AgenticRunStatus.BLOCKED,
    ):
        service.repository.transition_run(run.run_id, status)

    replay = await service.create_and_start(
        title="Effet déjà tenté",
        idempotency_key="stable-request",
    )
    await asyncio.sleep(0)
    assert replay.run_id == run.run_id
    assert service.get(run.run_id).status is AgenticRunStatus.BLOCKED
    assert registry._instances == {}
    await service.dispose()


@pytest.mark.asyncio
async def test_multiple_approvals_use_outbox_and_preserve_awaiting_state(
    agentic_db: Path,
    tmp_path: Path,
):
    registry = RuntimeRegistry(discover_runtime_plugins(_plugin(tmp_path)))
    service = AgenticService(registry=registry, bus=EventBus())
    run = await service.create_run(
        title="Deux confirmations", runtime_id="fake-runtime"
    )
    for status in (
        AgenticRunStatus.CLASSIFIED,
        AgenticRunStatus.QUEUED,
        AgenticRunStatus.PROVISIONING,
        AgenticRunStatus.RUNNING,
    ):
        run = service.repository.transition_run(run.run_id, status)
    runtime = await registry.get("fake-runtime")
    assert runtime is not None

    first = await service.request_approval(
        ApprovalRequest(
            approval_id="approval-multi-1",
            run_id=run.run_id,
            action="Publier le rapport",
            tool="report.publish",
            summary="Première confirmation",
            sanitized_arguments={"destination": "team", "token": "secret"},
            risks=("publication externe",),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
    )
    second = await service.request_approval(
        ApprovalRequest(
            approval_id="approval-multi-2",
            run_id=run.run_id,
            action="Notifier l'équipe",
            tool="notify.send",
            summary="Deuxième confirmation",
        )
    )
    assert first.sanitized_arguments["token"] == "[REDACTED]"
    assert first.risks
    assert first.expires_at is not None
    assert first.expires_at <= datetime.now(timezone.utc) + timedelta(minutes=15)
    assert second.risks
    assert service.get(run.run_id).status is AgenticRunStatus.AWAITING_APPROVAL

    denied = await service.decide_approval(
        run.run_id,
        first.approval_id,
        ApprovalDecision.DENIED,
        decided_by="user",
        decision_id="approval-decision-multi-1",
    )
    assert denied.decision is ApprovalDecision.DENIED
    assert service.get(run.run_id).status is AgenticRunStatus.AWAITING_APPROVAL
    approval_calls = [call for call in runtime.calls if call[0] == "approval"]
    assert len(approval_calls) == 1

    replay = await service.decide_approval(
        run.run_id,
        first.approval_id,
        ApprovalDecision.DENIED,
        decided_by="user",
        decision_id="approval-decision-multi-1",
    )
    assert replay == denied
    approval_calls = [call for call in runtime.calls if call[0] == "approval"]
    assert len(approval_calls) == 1

    await service.decide_approval(
        run.run_id,
        second.approval_id,
        ApprovalDecision.APPROVED,
        decided_by="user",
        decision_id="approval-decision-multi-2",
    )
    assert service.get(run.run_id).status is AgenticRunStatus.BLOCKED
    approval_calls = [call for call in runtime.calls if call[0] == "approval"]
    assert len(approval_calls) == 2
    assert service.repository.approval_delivery_status(first.approval_id) == "delivered"
    assert (
        service.repository.approval_delivery_status(second.approval_id) == "delivered"
    )
    await service.dispose()


@pytest.mark.asyncio
async def test_approval_outbox_retries_failure_without_replaying_success(
    agentic_db: Path,
    tmp_path: Path,
):
    registry = RuntimeRegistry(discover_runtime_plugins(_plugin(tmp_path)))
    service = AgenticService(registry=registry, bus=EventBus())
    run = await service.create_run(
        title="Confirmation retry", runtime_id="fake-runtime"
    )
    for status in (
        AgenticRunStatus.CLASSIFIED,
        AgenticRunStatus.QUEUED,
        AgenticRunStatus.PROVISIONING,
        AgenticRunStatus.RUNNING,
    ):
        run = service.repository.transition_run(run.run_id, status)
    runtime = await registry.get("fake-runtime")
    assert runtime is not None
    original_answer = runtime.answer_approval
    delivery_attempts = 0

    async def flaky_answer(run_id, approval):
        nonlocal delivery_attempts
        delivery_attempts += 1
        if delivery_attempts == 1:
            raise RuntimeError("temporary delivery failure")
        await original_answer(run_id, approval)

    runtime.answer_approval = flaky_answer
    approval = await service.request_approval(
        ApprovalRequest(
            approval_id="approval-retry",
            run_id=run.run_id,
            action="Appliquer",
            tool="effect.apply",
            summary="Confirmer l'action",
        )
    )
    with pytest.raises(RuntimePluginError):
        await service.decide_approval(
            run.run_id,
            approval.approval_id,
            ApprovalDecision.APPROVED,
            decided_by="user",
            decision_id="approval-retry-decision",
        )
    assert (
        service.repository.approval_delivery_status(approval.approval_id) == "pending"
    )
    assert service.get(run.run_id).status is AgenticRunStatus.AWAITING_APPROVAL

    await service.decide_approval(
        run.run_id,
        approval.approval_id,
        ApprovalDecision.APPROVED,
        decided_by="user",
        decision_id="approval-retry-decision",
    )
    assert delivery_attempts == 2
    assert (
        service.repository.approval_delivery_status(approval.approval_id) == "delivered"
    )
    assert service.get(run.run_id).status is AgenticRunStatus.RUNNING

    await service.decide_approval(
        run.run_id,
        approval.approval_id,
        ApprovalDecision.APPROVED,
        decided_by="user",
        decision_id="approval-retry-decision",
    )
    assert delivery_attempts == 2
    await service.dispose()


@pytest.mark.asyncio
async def test_missing_runtime_is_terminal_without_import_failure(agentic_db: Path):
    service = AgenticService(registry=RuntimeRegistry(()), bus=EventBus())
    assert service.resolve_runtime_id("auto") is None
    run = await service.create_run(title="Sans plugin")
    assert run.runtime_id == "unavailable"
    terminal = await service.start_run(run.run_id)
    assert terminal.status is AgenticRunStatus.PROVIDER_UNAVAILABLE
    assert (
        await service.wait_for_terminal(run.run_id, timeout=0.1)
    ).run_id == run.run_id
    assert await service.runtime_status() == []
    await service.dispose()


@pytest.mark.asyncio
async def test_broken_factory_is_persisted_as_provider_unavailable(
    agentic_db: Path,
    tmp_path: Path,
):
    integrations = _plugin(tmp_path, code=BROKEN_FACTORY_PLUGIN_CODE)
    service = AgenticService(
        registry=RuntimeRegistry(discover_runtime_plugins(integrations)),
        bus=EventBus(),
    )
    run = await service.create_run(title="Factory indisponible")
    terminal = await service.start_run(run.run_id)
    assert terminal.status is AgenticRunStatus.PROVIDER_UNAVAILABLE
    assert terminal.error is not None
    assert terminal.error.code.value == "runtime_unavailable"
    await service.dispose()


@pytest.mark.asyncio
async def test_agentic_event_bus_payload_is_broadcast_to_websocket_profile(
    agentic_db: Path,
):
    class Socket:
        def __init__(self) -> None:
            self.events = []

        async def send_json(self, event):
            self.events.append(event)

    socket = Socket()
    await add_websocket(socket)
    try:
        await event_bus.emit(
            build_agentic_bus_event(
                "agent.tool.started",
                {
                    "run_id": "run-safe",
                    "status": "running",
                    "phase": "tool",
                    "tool": "browser",
                    "objective": "raw",
                    "token": "hidden",
                },
            )
        )
        await event_bus.wait_until_idle()
        assert socket.events[-1]["event_type"] == "agent.tool.started"
        assert socket.events[-1]["payload"]["run_id"] == "run-safe"
        assert {
            "run_id",
            "status",
            "phase",
            "channel",
            "title",
            "progress",
            "needs_attention",
            "spoken_summary",
        } <= set(socket.events[-1]["payload"])
        assert "objective" not in socket.events[-1]["payload"]
        assert "token" not in socket.events[-1]["payload"]
    finally:
        await remove_websocket(socket)


@pytest.mark.asyncio
async def test_crash_persisted_runtime_approval_is_replayed_once(
    agentic_db: Path,
    tmp_path: Path,
):
    registry = RuntimeRegistry(discover_runtime_plugins(_plugin(tmp_path)))
    service = AgenticService(registry=registry, bus=EventBus())
    run = await service.create_and_start(title="Approbation après crash")
    await _wait_for_status(service, run.run_id, AgenticRunStatus.RUNNING)
    current = service.get(run.run_id)
    assert current is not None
    event = RuntimeEvent(
        event_id="persisted-approval-event",
        run_id=run.run_id,
        sequence=0,
        type="agent.approval.requested",
        timestamp=datetime.now(timezone.utc),
        payload={
            "run_id": run.run_id,
            "status": current.status.value,
            "phase": current.phase,
            "channel": current.channel,
            "title": current.title,
            "progress": 0.5,
            "needs_attention": True,
            "spoken_summary": "Confirmer l’action",
            "approval_id": "approval-after-crash",
            "action": "Publier le rapport",
            "tool": "publisher",
            "sanitized_arguments": {"target": "staging"},
            "risks": ["Publication externe"],
        },
        external_event_id="provider-approval-after-crash",
    )
    stored, created = service.repository.append_event(
        event,
        requires_processing=True,
    )
    assert created is True
    assert service.repository.event_processing_state(run.run_id, stored.event_id) == "pending"

    assert await service.replay_unprocessed_runtime_events() == 1
    assert service.get(run.run_id).status is AgenticRunStatus.AWAITING_APPROVAL
    assert [item.approval_id for item in service.approvals(run.run_id)] == [
        "approval-after-crash"
    ]
    assert service.repository.event_processing_state(run.run_id, stored.event_id) == "processed"
    assert await service.replay_unprocessed_runtime_events() == 0
    assert len(service.approvals(run.run_id)) == 1
    await service.dispose()


@pytest.mark.asyncio
async def test_restart_replays_runtime_inbox_for_noncurrent_profile(
    agentic_db: Path,
    tmp_path: Path,
):
    profile_id = database.create_user_profile("Profil reprise agentique")["id"]
    integrations = _plugin(tmp_path)
    service = AgenticService(
        registry=RuntimeRegistry(discover_runtime_plugins(integrations)),
        bus=EventBus(),
    )
    with database.use_profile(profile_id):
        run = await service.create_and_start(title="Reprise profil secondaire")
        await _wait_for_status(service, run.run_id, AgenticRunStatus.RUNNING)
        current = service.get(run.run_id)
        assert current is not None
        stored, created = service.repository.append_event(
            RuntimeEvent(
                event_id="secondary-profile-persisted-approval",
                run_id=run.run_id,
                sequence=0,
                type="agent.approval.requested",
                timestamp=datetime.now(timezone.utc),
                payload={
                    "run_id": run.run_id,
                    "status": current.status.value,
                    "phase": current.phase,
                    "channel": current.channel,
                    "title": current.title,
                    "progress": 0.5,
                    "needs_attention": True,
                    "spoken_summary": "Confirmer l’action secondaire",
                    "approval_id": "secondary-profile-approval",
                    "action": "Publier le rapport secondaire",
                    "tool": "publisher",
                    "sanitized_arguments": {"target": "staging"},
                    "risks": ["Publication externe"],
                },
                external_event_id="secondary-profile-provider-approval",
            ),
            requires_processing=True,
        )
        assert created is True
        assert (
            service.repository.event_processing_state(run.run_id, stored.event_id)
            == "pending"
        )
    await service.dispose()

    restarted = AgenticService(
        registry=RuntimeRegistry(discover_runtime_plugins(integrations)),
        bus=EventBus(),
    )
    assert await restarted.replay_unprocessed_runtime_events() == 1
    with database.use_profile(profile_id):
        replayed = restarted.get(run.run_id)
        assert replayed is not None
        assert replayed.status is AgenticRunStatus.AWAITING_APPROVAL
        assert [item.approval_id for item in restarted.approvals(run.run_id)] == [
            "secondary-profile-approval"
        ]
        assert (
            restarted.repository.event_processing_state(run.run_id, stored.event_id)
            == "processed"
        )
    assert await restarted.replay_unprocessed_runtime_events() == 0
    await restarted.dispose()


@pytest.mark.asyncio
async def test_crash_windows_for_runtime_completion_and_failure_are_idempotent(
    agentic_db: Path,
    tmp_path: Path,
):
    registry = RuntimeRegistry(discover_runtime_plugins(_plugin(tmp_path)))
    service = AgenticService(registry=registry, bus=EventBus())

    completion = await service.create_and_start(title="Completion à reprendre")
    await _wait_for_status(service, completion.run_id, AgenticRunStatus.RUNNING)
    completion_event = RuntimeEvent(
        event_id="persisted-completion-event",
        run_id=completion.run_id,
        sequence=0,
        type="agent.run.phase_changed",
        timestamp=datetime.now(timezone.utc),
        payload={"phase": "runtime_completed"},
        external_event_id="provider-completion-after-crash",
    )
    service.repository.append_event(completion_event, requires_processing=True)
    # Fenêtre simulée : la transition a été persistée, puis le processus tombe
    # avant la vérification et avant l'acquittement inbox.
    service.repository.transition_run(
        completion.run_id,
        AgenticRunStatus.VERIFYING,
    )
    assert await service.replay_unprocessed_runtime_events() == 1
    assert service.get(completion.run_id).status is AgenticRunStatus.BLOCKED
    blocked_events = [
        event
        for event in service.events(completion.run_id)
        if event.type == "agent.run.blocked"
    ]
    assert len(blocked_events) == 1
    assert await service.replay_unprocessed_runtime_events() == 0
    assert len(
        [
            event
            for event in service.events(completion.run_id)
            if event.type == "agent.run.blocked"
        ]
    ) == 1

    failure = await service.create_and_start(title="Échec à reprendre")
    await _wait_for_status(service, failure.run_id, AgenticRunStatus.RUNNING)
    failure_event = RuntimeEvent(
        event_id="persisted-failure-event",
        run_id=failure.run_id,
        sequence=0,
        type="agent.run.phase_changed",
        timestamp=datetime.now(timezone.utc),
        payload={"phase": "runtime_failed"},
        external_event_id="provider-failure-after-crash",
    )
    service.repository.append_event(failure_event, requires_processing=True)
    assert await service.replay_unprocessed_runtime_events() == 1
    assert service.get(failure.run_id).status is AgenticRunStatus.FAILED
    assert await service.replay_unprocessed_runtime_events() == 0
    assert len(
        [
            event
            for event in service.events(failure.run_id)
            if event.type == "agent.run.failed"
        ]
    ) == 1
    await service.dispose()


@pytest.mark.asyncio
async def test_approval_sweeper_preserves_other_pending_then_blocks_once(
    agentic_db: Path,
    tmp_path: Path,
):
    service = AgenticService(
        registry=RuntimeRegistry(discover_runtime_plugins(_plugin(tmp_path))),
        bus=EventBus(),
    )
    run = await service.create_and_start(title="Deux approbations TTL")
    await _wait_for_status(service, run.run_id, AgenticRunStatus.RUNNING)
    now = datetime.now(timezone.utc)
    first = await service.request_approval(
        ApprovalRequest(
            approval_id="approval-ttl-first",
            run_id=run.run_id,
            action="Première action",
            tool="tool.first",
            summary="Première confirmation",
            expires_at=now + timedelta(minutes=1),
        )
    )
    second = await service.request_approval(
        ApprovalRequest(
            approval_id="approval-ttl-second",
            run_id=run.run_id,
            action="Seconde action",
            tool="tool.second",
            summary="Seconde confirmation",
            expires_at=now + timedelta(minutes=2),
        )
    )

    expired_first = await service.sweep_expired_approvals(
        now=now + timedelta(seconds=90)
    )
    assert [item.approval_id for item in expired_first] == [first.approval_id]
    assert service.get(run.run_id).status is AgenticRunStatus.AWAITING_APPROVAL
    assert service.repository.get_approval(second.approval_id).decision is ApprovalDecision.PENDING

    expired_second = await service.sweep_expired_approvals(
        now=now + timedelta(minutes=3)
    )
    assert [item.approval_id for item in expired_second] == [second.approval_id]
    blocked = service.get(run.run_id)
    assert blocked.status is AgenticRunStatus.BLOCKED
    assert blocked.error is not None
    assert blocked.error.code.value == "approval_expired"
    assert await service.sweep_expired_approvals(
        now=now + timedelta(minutes=4)
    ) == []
    expired_events = [
        event
        for event in service.events(run.run_id)
        if event.type == "agent.approval.resolved"
        and event.payload.get("decision") == "expired"
    ]
    assert {event.payload["approval_id"] for event in expired_events} == {
        first.approval_id,
        second.approval_id,
    }
    await service.dispose()


@pytest.mark.asyncio
async def test_approval_sweeper_expires_noncurrent_profile(
    agentic_db: Path,
    tmp_path: Path,
):
    profile_id = database.create_user_profile("Profil expiration agentique")["id"]
    service = AgenticService(
        registry=RuntimeRegistry(discover_runtime_plugins(_plugin(tmp_path))),
        bus=EventBus(),
    )
    now = datetime.now(timezone.utc)
    with database.use_profile(profile_id):
        run = await service.create_and_start(title="Expiration profil secondaire")
        await _wait_for_status(service, run.run_id, AgenticRunStatus.RUNNING)
        approval = await service.request_approval(
            ApprovalRequest(
                approval_id="secondary-profile-expiring-approval",
                run_id=run.run_id,
                action="Action secondaire différée",
                tool="tool.secondary",
                summary="Confirmation du profil secondaire",
                expires_at=now + timedelta(minutes=1),
            )
        )

    expired = await service.sweep_expired_approvals(
        now=now + timedelta(minutes=2)
    )
    assert [item.approval_id for item in expired] == [approval.approval_id]
    with database.use_profile(profile_id):
        blocked = service.get(run.run_id)
        assert blocked is not None
        assert blocked.status is AgenticRunStatus.BLOCKED
        assert blocked.error is not None
        assert blocked.error.code.value == "approval_expired"
        stored = service.repository.get_approval(approval.approval_id)
        assert stored is not None
        assert stored.decision is ApprovalDecision.EXPIRED
    await service.dispose()


@pytest.mark.asyncio
async def test_startup_reconciliation_repairs_expiration_crash_window(
    agentic_db: Path,
    tmp_path: Path,
):
    integrations = _plugin(tmp_path)
    service = AgenticService(
        registry=RuntimeRegistry(discover_runtime_plugins(integrations)),
        bus=EventBus(),
    )
    run = await service.create_and_start(title="Expiration persistée sans event")
    await _wait_for_status(service, run.run_id, AgenticRunStatus.RUNNING)
    approval = await service.request_approval(
        ApprovalRequest(
            approval_id="approval-expiry-startup",
            run_id=run.run_id,
            action="Action différée",
            tool="tool.startup",
            summary="Confirmation au redémarrage",
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=1),
        )
    )
    service.repository.expire_due_approval_requests(
        run.run_id,
        now=approval.expires_at + timedelta(seconds=1),
    )
    assert not [
        event
        for event in service.events(run.run_id)
        if event.external_event_id
        == f"approval:{approval.approval_id}:resolved:expired"
    ]
    await service.dispose()

    restarted = AgenticService(
        registry=RuntimeRegistry(discover_runtime_plugins(integrations)),
        bus=EventBus(),
    )
    await restarted.reconcile_nonterminal()
    repaired = restarted.get(run.run_id)
    assert repaired is not None
    assert repaired.status is AgenticRunStatus.BLOCKED
    assert len(
        [
            event
            for event in restarted.events(run.run_id)
            if event.external_event_id
            == f"approval:{approval.approval_id}:resolved:expired"
        ]
    ) == 1
    await restarted.dispose()


@pytest.mark.asyncio
async def test_stale_approval_delivery_is_reclaimed_once_with_stable_decision(
    agentic_db: Path,
    tmp_path: Path,
):
    registry = RuntimeRegistry(discover_runtime_plugins(_plugin(tmp_path)))
    service = AgenticService(registry=registry, bus=EventBus())
    run = await service.create_and_start(title="Livraison stale")
    await _wait_for_status(service, run.run_id, AgenticRunStatus.RUNNING)
    approval = await service.request_approval(
        ApprovalRequest(
            approval_id="approval-stale-runtime",
            run_id=run.run_id,
            action="Publier",
            tool="publisher",
            summary="Confirmer",
        )
    )
    stale_at = datetime.now(timezone.utc) - timedelta(seconds=61)
    service.repository.decide_approval(
        approval.approval_id,
        ApprovalDecision.APPROVED,
        decided_by="user",
        decision_id="stable-stale-decision",
        now=stale_at,
    )
    assert service.repository.claim_approval_delivery(
        approval.approval_id,
        now=stale_at,
        lease_seconds=60,
    )

    decided = await service.decide_approval(
        run.run_id,
        approval.approval_id,
        ApprovalDecision.APPROVED,
        decided_by="user",
        decision_id="stable-stale-decision",
    )
    runtime = await registry.get(run.runtime_id)
    assert runtime is not None
    assert decided.decision_id == "stable-stale-decision"
    assert len([call for call in runtime.calls if call[0] == "approval"]) == 1

    await service.decide_approval(
        run.run_id,
        approval.approval_id,
        ApprovalDecision.APPROVED,
        decided_by="user",
        decision_id="stable-stale-decision",
    )
    assert len([call for call in runtime.calls if call[0] == "approval"]) == 1
    await service.dispose()
