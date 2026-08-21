"""Contrats purs du domaine agentique provider-neutral."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timezone
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from jarvis.agentic import (
    ALLOWED_RUN_TRANSITIONS,
    TERMINAL_RUN_STATUSES,
    AgenticRecursionError,
    AgenticRequestCategory,
    AgenticRun,
    AgenticRunStatus,
    Artifact,
    BudgetUsage,
    DoomLoopDetector,
    InvalidRunTransition,
    RunBudget,
    VerificationEvidence,
    VerificationResult,
    VerificationVerdict,
    build_agentic_context,
    build_jarvis_receipt_artifact,
    build_run_budget,
    classify_agentic_request,
    check_budget,
    verify_runtime_completion,
)
from jarvis.agentic.redaction import neutralize_event_payload
from jarvis.agentic.verifier import (
    DEFAULT_VERIFIER_REGISTRY,
    FAIL_CLOSED_VERIFIER,
)
from jarvis.event_bus import AGENTIC_EVENT_TYPES, DOMAIN_EVENT_TYPES, EVENT_TYPES


EXPECTED_STATUSES = {
    "created",
    "classified",
    "queued",
    "provisioning",
    "planning",
    "awaiting_approval",
    "running",
    "verifying",
    "reviewing",
    "paused",
    "blocked",
    "cancelling",
    "cancelled",
    "failed",
    "completed",
    "expired",
    "provider_unavailable",
}


def _run() -> AgenticRun:
    return AgenticRun.new(
        profile_id="default",
        origin="user",
        channel="web",
        runtime_id="fake-runtime",
        title="Tâche neutre",
    )


def test_agentic_client_context_is_bounded_and_normalized_in_domain():
    run = AgenticRun.new(
        profile_id="default",
        origin="websocket",
        channel="voice",
        runtime_id="fake-runtime",
        title="Contexte client",
        device="x" * 129,
        locale="fr-FR\nforged",
        timezone="Not/A_Zone",
    )

    assert run.device is None
    assert run.locale == "fr-FR"
    assert run.timezone == "Europe/Paris"


@pytest.mark.parametrize(
    ("task_type", "verifier_name"),
    [
        ("code", "jarvis.verifier.code.v1"),
        ("email", "jarvis.verifier.email_invoice.v1"),
        ("facture", "jarvis.verifier.email_invoice.v1"),
        ("invoice", "jarvis.verifier.email_invoice.v1"),
        ("browser", "jarvis.verifier.browser.v1"),
        ("obs", "jarvis.verifier.obs_video.v1"),
        ("video", "jarvis.verifier.obs_video.v1"),
    ],
)
def test_default_verifier_registry_selects_registered_family(
    task_type: str, verifier_name: str
):
    run = replace(_run(), selected_context={"verification_type": task_type})

    selected = DEFAULT_VERIFIER_REGISTRY.resolve(run=run)

    assert selected.descriptor.name == verifier_name


def test_default_verifier_registry_is_fail_closed_for_unknown_family():
    run = replace(_run(), selected_context={"verification_type": "unknown"})

    selected = DEFAULT_VERIFIER_REGISTRY.resolve(run=run)
    result = selected.verify(run=run, artifacts=())

    assert selected is FAIL_CLOSED_VERIFIER
    assert result.verdict is VerificationVerdict.BLOCKED


def test_default_verifier_registry_infers_family_from_artifact_type():
    run = _run()
    screenshot = Artifact(
        artifact_id="browser-proof",
        run_id=run.run_id,
        type="screenshot",
        reference="/tmp/browser-proof.png",
    )

    selected = DEFAULT_VERIFIER_REGISTRY.resolve(run=run, artifacts=(screenshot,))

    assert selected.descriptor.name == "jarvis.verifier.browser.v1"


def test_state_machine_is_exhaustive_and_terminal_states_are_closed():
    assert {status.value for status in AgenticRunStatus} == EXPECTED_STATUSES
    assert set(ALLOWED_RUN_TRANSITIONS) == set(AgenticRunStatus)
    assert all(not ALLOWED_RUN_TRANSITIONS[status] for status in TERMINAL_RUN_STATUSES)

    run = _run()
    run = run.transition(AgenticRunStatus.CLASSIFIED)
    run = run.transition(AgenticRunStatus.QUEUED)
    run = run.transition(AgenticRunStatus.PROVISIONING)
    run = run.transition(AgenticRunStatus.RUNNING)
    run = run.transition(AgenticRunStatus.VERIFYING)
    with pytest.raises(InvalidRunTransition):
        run.transition(AgenticRunStatus.COMPLETED)
    run = run.transition(
        AgenticRunStatus.COMPLETED,
        verification=VerificationResult(
            verdict=VerificationVerdict.PASS,
            verifier="test",
            summary="preuve validée",
            evidence=(
                VerificationEvidence(
                    check="test",
                    passed=True,
                    summary="preuve déterministe",
                ),
            ),
        ),
    )
    assert run.terminal is True
    assert run.started_at is not None
    assert run.finished_at is not None

    with pytest.raises(InvalidRunTransition):
        run.transition(AgenticRunStatus.RUNNING)
    with pytest.raises(FrozenInstanceError):
        run.phase = "forged"  # type: ignore[misc]
    with pytest.raises(ValueError, match="verdict de vérification PASS"):
        replace(_run(), status=AgenticRunStatus.COMPLETED)


def test_classifier_covers_exact_categories_and_blocks_recursion():
    assert (
        classify_agentic_request("ouvre le calendrier").category
        is AgenticRequestCategory.DIRECT_ACTION
    )
    assert (
        classify_agentic_request("fais ceci puis cela").category
        is AgenticRequestCategory.WORKFLOW
    )
    assert (
        classify_agentic_request("analyse tout le dépôt", adaptive=True).category
        is AgenticRequestCategory.AGENTIC_READONLY
    )
    assert (
        classify_agentic_request("modifie puis teste").category
        is AgenticRequestCategory.AGENTIC_REVERSIBLE
    )
    assert (
        classify_agentic_request("crée une todolist html").category
        is AgenticRequestCategory.DIRECT_ACTION
    )
    assert (
        classify_agentic_request("crée une todolist html", adaptive=True).category
        is AgenticRequestCategory.AGENTIC_REVERSIBLE
    )
    assert (
        classify_agentic_request("envoie ce message").category
        is AgenticRequestCategory.AGENTIC_EXTERNAL_EFFECT
    )
    assert (
        classify_agentic_request("book me a hotel in Barcelona").category
        is AgenticRequestCategory.AGENTIC_EXTERNAL_EFFECT
    )
    assert (
        classify_agentic_request("Joue Werenoi sur Apple Music").category
        is AgenticRequestCategory.DIRECT_ACTION
    )
    assert (
        classify_agentic_request("déploie en production").category
        is AgenticRequestCategory.AGENTIC_HIGH_RISK
    )

    with pytest.raises(AgenticRecursionError):
        classify_agentic_request("rappelle l'outil", origin="agent_runtime")
    bypassed = classify_agentic_request(
        "rappelle l'outil",
        origin="agent_runtime",
        bypass_agentic_reclassification=True,
    )
    assert bypassed.bypassed is True
    assert bypassed.category is AgenticRequestCategory.DIRECT_ACTION


def test_budget_and_context_are_built_without_runtime_imports():
    config = SimpleNamespace(
        AGENTIC_MAX_DURATION_S=90,
        AGENTIC_MAX_STEPS=7,
        AGENTIC_MAX_TOOL_CALLS=11,
        AGENTIC_MAX_RETRIES=1,
        AGENTIC_MODEL_TOKEN_BUDGET=4096,
        AGENTIC_COST_BUDGET="2.5",
        AGENTIC_CONCURRENCY_LIMIT=2,
        AGENTIC_MAX_ARTIFACT_BYTES=1024,
        AGENTIC_MAX_CONTEXT_TOKENS=2048,
        AGENTIC_COMPACTION_POLICY="summary",
        AGENTIC_BLOCKING_STRATEGY="block",
    )
    now = datetime(2026, 8, 11, tzinfo=timezone.utc)
    budget = build_run_budget(config, now=now)
    assert budget == RunBudget(
        max_duration_s=90,
        max_steps=7,
        max_tool_calls=11,
        max_retries=1,
        model_token_budget=4096,
        cost_budget=2.5,
        concurrency_limit=2,
        deadline=datetime(2026, 8, 11, 0, 1, 30, tzinfo=timezone.utc),
        max_artifact_bytes=1024,
        max_context_tokens=2048,
        compaction_policy="summary",
        blocking_strategy="block",
    )

    context = build_agentic_context(
        run_id="run-1",
        profile_id="default",
        channel="voice",
        permissions=("read", "read", "draft"),
        selected_context={"summary": "utile", "token": "super-secret"},
    )
    assert context.permissions == ("read", "draft")
    assert context.selected_context["token"] == "[REDACTED]"
    with pytest.raises(ValueError):
        build_agentic_context(
            run_id="run-2",
            profile_id="default",
            origin="agent_runtime",
        )


def test_agentic_events_preserve_domain_tail_and_use_strict_payload_allowlist():
    assert DOMAIN_EVENT_TYPES == EVENT_TYPES[-10:]
    assert len(DOMAIN_EVENT_TYPES) == 10
    assert set(AGENTIC_EVENT_TYPES).isdisjoint(DOMAIN_EVENT_TYPES)
    assert {
        "agent.run.created",
        "agent.run.resource_wait",
        "agent.tool.started",
        "agent.approval.requested",
    }.issubset(AGENTIC_EVENT_TYPES)

    safe = neutralize_event_payload(
        {
            "run_id": "run-1",
            "status": "running",
            "phase": "tool",
            "channel": "voice",
            "title": "Titre",
            "progress": 5,
            "needs_attention": 1,
            "spoken_summary": "C'est prêt",
            "objective": "contenu utilisateur brut",
            "tool_result": "secret",
            "token": "abc",
        }
    )
    assert safe["progress"] == 1.0
    assert safe["needs_attention"] is True
    assert "objective" not in safe
    assert "tool_result" not in safe
    assert "token" not in safe


def test_budget_guard_and_doom_loop_detection_are_deterministic():
    budget = RunBudget(max_steps=2, max_tool_calls=2)
    assert check_budget(budget, BudgetUsage(steps=2, tool_calls=2)) is None
    error = check_budget(budget, BudgetUsage(steps=3, tool_calls=2))
    assert error is not None
    assert error.code.value == "budget_exceeded"
    assert error.details["budget"] == "steps"

    detector = DoomLoopDetector()
    assert detector.record(tool="read", arguments={"path": "a"}) is None
    assert detector.record(tool="read", arguments={"path": "a"}) is None
    assert (
        detector.record(tool="read", arguments={"path": "a"}) == "same_tool_arguments"
    )


def test_generic_verifier_maps_contract_evidence_to_all_verdicts(tmp_path: Path):
    content = b"verified report"
    (tmp_path / "report.txt").write_bytes(content)
    run = replace(_run(), workspace=str(tmp_path))
    for status in (
        AgenticRunStatus.CLASSIFIED,
        AgenticRunStatus.QUEUED,
        AgenticRunStatus.PROVISIONING,
        AgenticRunStatus.RUNNING,
        AgenticRunStatus.VERIFYING,
    ):
        run = run.transition(status)

    artifact = Artifact(
        artifact_id="artifact-1",
        run_id=run.run_id,
        type="report_file",
        reference="report.txt",
        sha256=hashlib.sha256(content).hexdigest(),
        size_bytes=len(content),
    )
    passed = verify_runtime_completion(run=run, artifacts=(artifact,))
    assert passed.verdict is VerificationVerdict.PASS
    assert {item.check for item in passed.evidence} == {
        "runtime_completion_observed",
        "artifact_collection",
        "artifact_contract",
        "artifact_budget",
        "structured_proof",
    }

    failed = verify_runtime_completion(
        run=run,
        artifacts=(
            Artifact(
                artifact_id="artifact-2",
                run_id="another-run",
                type="report",
                reference="memory://wrong-run",
            ),
        ),
    )
    assert failed.verdict is VerificationVerdict.FAIL

    blocked = verify_runtime_completion(
        run=run,
        artifacts=(),
        collection_error_code="runtime_protocol",
    )
    assert blocked.verdict is VerificationVerdict.BLOCKED


def test_verifier_refuses_model_text_missing_paths_and_untrusted_receipts(
    tmp_path: Path,
):
    run = replace(_run(), workspace=str(tmp_path))
    for status in (
        AgenticRunStatus.CLASSIFIED,
        AgenticRunStatus.QUEUED,
        AgenticRunStatus.PROVISIONING,
        AgenticRunStatus.RUNNING,
        AgenticRunStatus.VERIFYING,
    ):
        run = run.transition(status)

    text = b"the model says this succeeded"
    narrative = Artifact(
        artifact_id="model-text",
        run_id=run.run_id,
        type="runtime_result",
        reference=f"agentic://{run.run_id}/result",
        sha256=hashlib.sha256(text).hexdigest(),
        size_bytes=len(text),
        metadata={"summary": text.decode()},
    )
    assert (
        verify_runtime_completion(run=run, artifacts=(narrative,)).verdict
        is VerificationVerdict.BLOCKED
    )

    missing = Artifact(
        artifact_id="missing-file",
        run_id=run.run_id,
        type="changed_file",
        reference="does-not-exist.txt",
        sha256="a" * 64,
        size_bytes=12,
    )
    assert (
        verify_runtime_completion(run=run, artifacts=(missing,)).verdict
        is VerificationVerdict.BLOCKED
    )

    external = replace(run, category=AgenticRequestCategory.AGENTIC_EXTERNAL_EFFECT)
    receipt = build_jarvis_receipt_artifact(
        run_id=run.run_id,
        kind="effect",
        subject="mail.send:message-1",
    )
    assert (
        verify_runtime_completion(run=external, artifacts=(receipt,)).verdict
        is VerificationVerdict.BLOCKED
    )
    trusted = verify_runtime_completion(
        run=external,
        artifacts=(receipt,),
        trusted_artifact_ids=frozenset({receipt.artifact_id}),
    )
    assert trusted.verdict is VerificationVerdict.PASS


def test_budget_violation_survives_event_neutralisation() -> None:
    """`budget_exceeded` sans borne nommée n'apprend rien à personne.

    Le service pose `violation` sur la transition d'échec ; l'allowlist de
    neutralisation la supprimait silencieusement, si bien que le diagnostic
    ajouté côté runtime n'atteignait aucun consommateur.
    """

    from jarvis.agentic.redaction import SAFE_EVENT_FIELDS, neutralize_event_payload

    assert "violation" in SAFE_EVENT_FIELDS

    safe = neutralize_event_payload(
        {
            "run_id": "run-1",
            "error_code": "budget_exceeded",
            "violation": "event_budget_exceeded",
            "session_token": "secret-a-jeter",
        }
    )
    assert safe["violation"] == "event_budget_exceeded"
    assert safe["error_code"] == "budget_exceeded"
    assert "session_token" not in safe


def test_neutralised_violation_is_bounded() -> None:
    """Le champ reste une étiquette courte, jamais un canal de sortie."""

    from jarvis.agentic.redaction import neutralize_event_payload

    safe = neutralize_event_payload({"violation": "x" * 5_000})
    assert len(safe["violation"]) <= 200
