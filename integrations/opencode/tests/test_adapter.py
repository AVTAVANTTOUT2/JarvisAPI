"""Tests de frontière entre le plugin OpenCode et le domaine générique."""

from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from integrations.opencode.adapter import OpenCodeRuntime, _RunState, _result_summaries
from integrations.opencode.client.models import (
    MessageEnvelope,
    ModelSelection,
    SSEEvent,
)
from integrations.opencode.config import RuntimeLayout
from integrations.opencode.event_mapper import map_opencode_event
from jarvis.agentic.models import (
    AgenticContext,
    AgenticRequestCategory,
    AgenticRun,
    RuntimeHealthStatus,
)


class _ArtifactsClient:
    def __init__(self, *, path: str, final_text: str):
        self.path = path
        self.final_text = final_text

    async def diff(self, *_args, **_kwargs):
        return ({"path": self.path},)

    async def messages(self, *_args, **_kwargs):
        return (
            MessageEnvelope(
                info={"role": "assistant"},
                parts=(
                    {"type": "reasoning", "text": "raisonnement interne"},
                    {"type": "text", "text": self.final_text},
                ),
            ),
        )


class _PromptClient:
    def __init__(self) -> None:
        self.tools: dict[str, bool] | None = None

    async def prompt_async(self, *_args, tools, **_kwargs) -> None:
        self.tools = tools


def _state(tmp_path: Path, final_text: str) -> tuple[OpenCodeRuntime, AgenticRun]:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "result.txt"
    target.write_text("preuve déterministe", encoding="utf-8")
    run = AgenticRun.new(
        profile_id="default",
        origin="user",
        channel="api",
        runtime_id="opencode",
        title="Produire un résultat",
        workspace=str(workspace),
        category=AgenticRequestCategory.AGENTIC_REVERSIBLE,
    )
    context = AgenticContext(
        run_id=run.run_id,
        profile_id=run.profile_id,
        channel=run.channel,
        origin=run.origin,
    )
    state = _RunState(
        run=run,
        context=context,
        workspace=workspace.resolve(),
        runtime_layout=RuntimeLayout.from_integration_root(tmp_path / "integration"),
        process_manager=SimpleNamespace(),
        mcp_broker=None,
        client=_ArtifactsClient(path="result.txt", final_text=final_text),
        session_id="session-1",
        model=ModelSelection(provider_id="provider", model_id="model"),
        agent="jarvis-executor",
        system_prompt="system",
        request_prompt="request",
    )
    runtime = object.__new__(OpenCodeRuntime)
    runtime._states = {run.run_id: state}
    return runtime, run


@pytest.mark.asyncio
async def test_artifacts_hash_real_content_and_keep_only_redacted_final_output(
    tmp_path: Path,
) -> None:
    final = (
        '{"summary":"Résultat prêt avec api_key=secret-value",'
        '"voice_summary":"Résultat prêt.","evidence":[],"blocked":false}'
    )
    runtime, run = _state(tmp_path, final)

    artifacts = await runtime.get_artifacts(run.run_id)

    changed = next(item for item in artifacts if item.type == "changed_file")
    result = next(item for item in artifacts if item.type == "runtime_result")
    assert changed.sha256 == hashlib.sha256("preuve déterministe".encode()).hexdigest()
    assert changed.size_bytes == len("preuve déterministe".encode())
    assert "secret-value" not in result.metadata["summary"]
    assert result.metadata["voice_summary"] == "Résultat prêt."
    assert "raisonnement interne" not in result.metadata["summary"]
    assert (
        result.sha256 == hashlib.sha256(result.metadata["summary"].encode()).hexdigest()
    )


@pytest.mark.asyncio
async def test_empty_final_output_does_not_fabricate_a_runtime_result(
    tmp_path: Path,
) -> None:
    runtime, run = _state(tmp_path, "")
    artifacts = await runtime.get_artifacts(run.run_id)
    assert [item.type for item in artifacts] == ["changed_file"]


@pytest.mark.asyncio
async def test_artifact_collection_refuses_more_than_100_files(tmp_path: Path) -> None:
    runtime, run = _state(tmp_path, "")
    state = runtime._states[run.run_id]

    async def oversized_diff(*_args, **_kwargs):
        return tuple({"path": f"generated/file-{index:03d}.py"} for index in range(101))

    state.client.diff = oversized_diff

    with pytest.raises(RuntimeError, match="runtime_artifact_count_exceeded"):
        await runtime.get_artifacts(run.run_id)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("permissions", "expected"),
    [
        (
            (),
            {
                "read": False,
                "grep": False,
                "glob": False,
                "edit": False,
                "write": False,
                "bash": False,
            },
        ),
        (
            ("workspace.read",),
            {
                "read": True,
                "grep": True,
                "glob": True,
                "edit": False,
                "write": False,
                "bash": False,
            },
        ),
        (
            ("tests.run",),
            {
                "read": False,
                "grep": False,
                "glob": False,
                "edit": False,
                "write": False,
                "bash": False,
            },
        ),
    ],
)
async def test_native_tools_are_enabled_only_by_explicit_scope(
    tmp_path: Path, permissions: tuple[str, ...], expected: dict[str, bool]
) -> None:
    runtime, run = _state(tmp_path, "résultat")
    state = runtime._states[run.run_id]
    state.context = AgenticContext(
        run_id=run.run_id,
        profile_id=run.profile_id,
        channel=run.channel,
        origin=run.origin,
        permissions=permissions,
    )
    client = _PromptClient()
    state.client = client

    await runtime._send_prompt(state, "instruction")

    assert client.tools == expected


def test_mcp_is_not_mounted_without_task_scope_and_aliases_are_normalized(
    tmp_path: Path,
) -> None:
    runtime, run = _state(tmp_path, "résultat")
    state = runtime._states[run.run_id]

    broker, overlay = runtime._capability_overlay(run, state.context, state.workspace)
    assert broker is None
    assert overlay == {}

    integration_root = tmp_path / "provider"
    integration_root.mkdir()
    runtime.layout = RuntimeLayout.from_integration_root(integration_root)
    runtime.layout.ensure()
    task_context = AgenticContext(
        run_id=run.run_id,
        profile_id=run.profile_id,
        channel=run.channel,
        origin=run.origin,
        permissions=("tasks.read",),
    )

    broker, overlay = runtime._capability_overlay(run, task_context, state.workspace)

    assert broker is not None
    try:
        assert broker.capability.scopes == frozenset({"tasks:read"})
        command = overlay["mcp"]["jarvis"]["command"]
        serialized = " ".join(command)
        assert "--bootstrap-socket" in command
        assert "--token" not in command
        assert "--token-fd" not in command
        assert broker.endpoint.token not in serialized
        assert overlay["mcp"]["jarvis"]["environment"]["PYTHONPATH"] == str(
            Path(__file__).resolve().parents[3]
        )
        assert run.run_id not in serialized
        assert run.profile_id not in serialized
        assert str(state.workspace) not in serialized
    finally:
        broker.stop()


@pytest.mark.asyncio
async def test_health_allows_verified_first_run_provisioning() -> None:
    runtime = object.__new__(OpenCodeRuntime)
    runtime.install_manager = SimpleNamespace(
        verify=lambda **_kwargs: SimpleNamespace(valid=False, version=None)
    )
    runtime.manifest = SimpleNamespace(
        version="1.18.16", asset_for_current_platform=lambda: object()
    )

    health = await runtime.health()

    assert health.status is RuntimeHealthStatus.DEGRADED
    assert health.details == {"installed": False, "installable": True}


def test_result_voice_fallback_removes_code_blocks() -> None:
    detailed, voice = _result_summaries(
        "```python\nprint('ne pas lire')\n```\nLa tâche est terminée et vérifiée."
    )
    assert "print" in detailed
    assert voice == "La tâche est terminée et vérifiée."


def test_event_mapping_requires_exact_session_and_never_exposes_tool_arguments() -> (
    None
):
    unrelated = SSEEvent(
        event_id="event-1",
        event_type="session.error",
        data={"type": "session.error", "properties": {"message": "secret"}},
        source="stream",
    )
    tool = SSEEvent(
        event_id="event-2",
        event_type="message.part.updated",
        data={
            "type": "message.part.updated",
            "properties": {
                "sessionID": "session-1",
                "part": {
                    "type": "tool",
                    "tool": "bash",
                    "callID": "call-1",
                    "state": {
                        "status": "running",
                        "input": {"command": "cat ~/.ssh/id_ed25519"},
                        "output": "private-key",
                    },
                },
            },
        },
        source="stream",
    )

    assert (
        map_opencode_event(run_id="run-1", session_id="session-1", event=unrelated)
        is None
    )
    mapped = map_opencode_event(run_id="run-1", session_id="session-1", event=tool)
    assert mapped is not None
    assert mapped.type == "agent.tool.started"
    serialized = repr(dict(mapped.payload))
    assert "id_ed25519" not in serialized
    assert "private-key" not in serialized
