"""Contrat HTTP provider-neutral des runs agentiques."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import time

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

import config
from database import current_profile_id
import database
from database.agentic import AgenticRepository
from jarvis.agentic.models import (
    AgenticRun,
    AgenticRunStatus,
    ApprovalRequest,
)
from jarvis.agentic.registry import RuntimeRegistry
from jarvis.agentic.service import AgenticService


@pytest.fixture
def agentic_api(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    db_path = tmp_path / "agentic-api.db"
    monkeypatch.setattr("config.DB_PATH", str(db_path))
    monkeypatch.setattr("database.DB_PATH", db_path)
    monkeypatch.setattr(config, "AGENTIC_REQUIRE_PLAN_APPROVAL", False)

    from database import init_db
    from api import router_agentic

    init_db()
    service = AgenticService(
        repository=AgenticRepository(),
        registry=RuntimeRegistry(manifests=()),
    )
    monkeypatch.setattr(router_agentic, "get_agentic_service", lambda: service)
    app = FastAPI()
    app.include_router(router_agentic.router)
    with TestClient(app) as client:
        yield client, service


def test_create_is_idempotent_and_provider_absence_is_a_run_state(agentic_api) -> None:
    client, service = agentic_api
    headers = {"Idempotency-Key": "api:test-create:00000001"}
    body = {
        "title": "Inspecter puis vérifier le résultat",
        "runtime_id": "missing-runtime",
        "channel": "api",
        "category": "agentic_readonly",
        "permissions": ["workspace:read"],
    }

    first = client.post("/api/agentic/runs", json=body, headers=headers)
    second = client.post("/api/agentic/runs", json=body, headers=headers)

    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json()["run"]["run_id"] == second.json()["run"]["run_id"]
    run_id = first.json()["run"]["run_id"]
    deadline = time.monotonic() + 2
    terminal = service.get(run_id)
    while (
        terminal is not None and not terminal.terminal and time.monotonic() < deadline
    ):
        time.sleep(0.01)
        terminal = service.get(run_id)
    assert terminal is not None
    assert terminal.status is AgenticRunStatus.PROVIDER_UNAVAILABLE

    detail = client.get(f"/api/agentic/runs/{run_id}")
    events = client.get(f"/api/agentic/runs/{run_id}/events")
    listing = client.get("/api/agentic/runs")
    assert detail.status_code == 200
    assert events.status_code == 200
    assert listing.status_code == 200
    assert listing.json()["runs"][0]["run_id"] == run_id
    assert all("selected_context" not in event for event in events.json()["events"])


def test_create_rejects_unbounded_or_unknown_input(agentic_api) -> None:
    client, _service = agentic_api
    invalid_key = client.post(
        "/api/agentic/runs",
        json={"title": "Tâche"},
        headers={"Idempotency-Key": "short"},
    )
    unknown_field = client.post(
        "/api/agentic/runs",
        json={"title": "Tâche", "provider_prompt": "secret"},
        headers={"Idempotency-Key": "api:test-input:00000001"},
    )
    traversal = client.get("/api/agentic/runs/..")

    assert invalid_key.status_code == 400
    assert invalid_key.json()["detail"]["code"] == "invalid_idempotency_key"
    assert unknown_field.status_code == 422
    assert traversal.status_code in {404, 405}


def test_create_rejects_client_declared_trust_and_classifies_on_server(
    agentic_api,
) -> None:
    client, _service = agentic_api
    attacks = (
        {"title": "Tâche", "origin": "agent_runtime"},
        {"title": "Tâche", "channel": "agent_runtime"},
        {"title": "Tâche", "permissions": ["workspace:write"]},
        {"title": "Tâche", "permissions": ["tasks:write"]},
        {"title": "Tâche", "category": "agentic_external_effect"},
        {"title": "Tâche", "category": "agentic_high_risk"},
        {
            "title": "Tâche",
            "selected_context": {"bypass_agentic_reclassification": True},
        },
    )
    for index, body in enumerate(attacks):
        response = client.post(
            "/api/agentic/runs",
            json=body,
            headers={"Idempotency-Key": f"api:trust-attack:{index:08d}"},
        )
        assert response.status_code == 422

    classified = client.post(
        "/api/agentic/runs",
        json={
            "title": "Publie ensuite le rapport",
            "runtime_id": "missing-runtime",
            "category": "direct_action",
        },
        headers={"Idempotency-Key": "api:server-category:00000001"},
    )
    assert classified.status_code == 202
    assert classified.json()["run"]["category"] == "agentic_external_effect"


def test_create_idempotency_key_rejects_a_different_canonical_payload(
    agentic_api,
) -> None:
    client, _service = agentic_api
    headers = {"Idempotency-Key": "api:payload-binding:00000001"}
    first = client.post(
        "/api/agentic/runs",
        json={"title": "Inspecte le rapport", "runtime_id": "missing-runtime"},
        headers=headers,
    )
    conflict = client.post(
        "/api/agentic/runs",
        json={"title": "Inspecte un autre rapport", "runtime_id": "missing-runtime"},
        headers=headers,
    )
    assert first.status_code == 202
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "idempotency_payload_conflict"


def test_create_rejects_direct_run_when_plan_approval_required(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """POST /api/agentic/runs ne doit pas contourner la porte ADR-034."""

    db_path = tmp_path / "agentic-plan-gate.db"
    monkeypatch.setattr("config.DB_PATH", str(db_path))
    monkeypatch.setattr("database.DB_PATH", db_path)
    monkeypatch.setattr(config, "AGENTIC_REQUIRE_PLAN_APPROVAL", True)

    from database import init_db
    from api import router_agentic

    init_db()
    service = AgenticService(
        repository=AgenticRepository(),
        registry=RuntimeRegistry(manifests=()),
    )
    monkeypatch.setattr(router_agentic, "get_agentic_service", lambda: service)
    app = FastAPI()
    app.include_router(router_agentic.router)
    with TestClient(app) as client:
        response = client.post(
            "/api/agentic/runs",
            json={
                "title": "Publie le rapport avant vendredi",
                "runtime_id": "missing-runtime",
                "channel": "api",
            },
            headers={"Idempotency-Key": "api:plan-gate:00000001"},
        )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "plan_approval_required"
    assert service.list() == []


def test_approval_listing_and_decision_are_exact_and_idempotent(agentic_api) -> None:
    client, service = agentic_api
    run = AgenticRun.new(
        profile_id=current_profile_id(),
        origin="user",
        channel="api",
        runtime_id="missing-runtime",
        title="Action sensible",
        status=AgenticRunStatus.AWAITING_APPROVAL,
        phase="awaiting_approval",
    )
    service.repository.create_run(run)
    approval = ApprovalRequest(
        approval_id="approval-00000001",
        run_id=run.run_id,
        action="Publier",
        tool="publish",
        summary="Publier le résultat validé",
        sanitized_arguments={"destination": "team", "token": "secret"},
    )
    service.repository.create_approval(approval)

    listed = client.get(f"/api/agentic/runs/{run.run_id}/approvals")
    decided = client.post(
        f"/api/agentic/runs/{run.run_id}/approvals/{approval.approval_id}/decision",
        json={"decision": "denied"},
        headers={"Idempotency-Key": "approval:test:00000001"},
    )
    replay = client.post(
        f"/api/agentic/runs/{run.run_id}/approvals/{approval.approval_id}/decision",
        json={"decision": "denied"},
        headers={"Idempotency-Key": "approval:test:00000001"},
    )
    conflict = client.post(
        f"/api/agentic/runs/{run.run_id}/approvals/{approval.approval_id}/decision",
        json={"decision": "approved"},
        headers={"Idempotency-Key": "approval:test:00000002"},
    )

    assert listed.status_code == 200
    listed_approval = listed.json()["approvals"][0]
    assert listed_approval["approval_id"] == approval.approval_id
    assert listed_approval["sanitized_arguments"]["token"] == "[REDACTED]"
    assert listed_approval["risks"]
    assert listed_approval["expires_at"] is not None
    assert decided.status_code == 200
    assert decided.json()["approval"]["decision"] == "denied"
    assert replay.status_code == 200
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "approval_already_decided"


def test_expired_approval_is_refused_with_typed_error(agentic_api) -> None:
    client, service = agentic_api
    run = AgenticRun.new(
        profile_id=current_profile_id(),
        origin="user",
        channel="api",
        runtime_id="missing-runtime",
        title="Action expirée",
        status=AgenticRunStatus.AWAITING_APPROVAL,
        phase="awaiting_approval",
    )
    service.repository.create_run(run)
    approval = service.repository.create_approval(
        ApprovalRequest(
            approval_id="approval-expired-api",
            run_id=run.run_id,
            action="Publier",
            tool="publish",
            summary="Confirmation trop tardive",
        )
    )
    expired_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    with database.get_db() as conn:
        conn.execute(
            "UPDATE agent_approvals SET expires_at = ? WHERE approval_id = ?",
            (expired_at.isoformat(), approval.approval_id),
        )
    response = client.post(
        f"/api/agentic/runs/{run.run_id}/approvals/{approval.approval_id}/decision",
        json={"decision": "approved"},
        headers={"Idempotency-Key": "approval:expired:00000001"},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "approval_expired"
    stored = service.repository.get_approval(approval.approval_id)
    assert stored is not None
    assert stored.decision.value == "expired"


def test_runtime_status_stays_generic_when_no_plugin_is_present(agentic_api) -> None:
    client, _service = agentic_api
    response = client.get("/api/agentic/runtime/status")
    assert response.status_code == 200
    assert response.json() == {"runtimes": []}
