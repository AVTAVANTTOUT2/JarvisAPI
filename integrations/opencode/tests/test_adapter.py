"""Tests de frontière entre le plugin OpenCode et le domaine générique."""

from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from integrations.opencode import adapter as opencode_adapter
from integrations.opencode.adapter import OpenCodeRuntime, _RunState, _result_summaries
from integrations.opencode.client.models import (
    MessageEnvelope,
    ModelSelection,
    SSEEvent,
)
from integrations.opencode.config import RuntimeLayout
from integrations.opencode.event_mapper import map_opencode_event
from integrations.opencode.register import create_runtime
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


def _tool_message(
    *,
    session_id: str,
    path: str,
    tool: str = "edit",
    status: str = "completed",
) -> MessageEnvelope:
    return MessageEnvelope(
        info={"role": "assistant", "sessionID": session_id},
        parts=(
            {
                "type": "tool",
                "tool": tool,
                "state": {
                    "status": status,
                    "input": {"filePath": path},
                },
            },
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
async def test_artifacts_fall_back_to_completed_session_file_tools(
    tmp_path: Path,
) -> None:
    runtime, run = _state(tmp_path, "")
    state = runtime._states[run.run_id]

    async def empty_diff(*_args, **_kwargs):
        return ()

    async def messages(*_args, **_kwargs):
        return (
            _tool_message(
                session_id=state.session_id,
                path=str(state.workspace / "result.txt"),
            ),
        )

    state.client.diff = empty_diff
    state.client.messages = messages

    artifacts = await runtime.get_artifacts(run.run_id)

    assert [item.reference for item in artifacts] == ["result.txt"]
    assert (
        artifacts[0].sha256
        == hashlib.sha256("preuve déterministe".encode()).hexdigest()
    )
    assert artifacts[0].metadata == {
        "workspace_relative": True,
        "session_bound": True,
        "evidence_sources": ["completed_session_tool"],
        "content_digest": True,
    }


@pytest.mark.asyncio
async def test_artifact_fallback_rejects_unverified_or_escaping_tool_paths(
    tmp_path: Path,
) -> None:
    runtime, run = _state(tmp_path, "")
    state = runtime._states[run.run_id]
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    link = state.workspace / "linked.txt"
    link.symlink_to(outside)

    async def empty_diff(*_args, **_kwargs):
        return ()

    async def messages(*_args, **_kwargs):
        return (
            _tool_message(
                session_id="different-session",
                path=str(state.workspace / "result.txt"),
            ),
            MessageEnvelope(
                info={"role": "assistant"},
                parts=(
                    {
                        "type": "tool",
                        "tool": "edit",
                        "state": {
                            "status": "completed",
                            "input": {"filePath": str(state.workspace / "result.txt")},
                        },
                    },
                ),
            ),
            _tool_message(
                session_id=state.session_id,
                path=str(state.workspace / "result.txt"),
                status="error",
            ),
            _tool_message(
                session_id=state.session_id,
                path=str(state.workspace / "result.txt"),
                tool="read",
            ),
            _tool_message(session_id=state.session_id, path="../outside.txt"),
            _tool_message(session_id=state.session_id, path=str(outside)),
            _tool_message(session_id=state.session_id, path=str(link)),
        )

    state.client.diff = empty_diff
    state.client.messages = messages

    assert await runtime.get_artifacts(run.run_id) == []


@pytest.mark.asyncio
async def test_artifact_union_is_sorted_deduplicated_and_session_bound(
    tmp_path: Path,
) -> None:
    runtime, run = _state(tmp_path, "")
    state = runtime._states[run.run_id]
    second = state.workspace / "alpha.txt"
    second.write_text("alpha", encoding="utf-8")

    async def diff(*_args, **_kwargs):
        return ({"path": "result.txt"}, {"path": "alpha.txt"})

    async def messages(*_args, **_kwargs):
        return (_tool_message(session_id=state.session_id, path="result.txt"),)

    state.client.diff = diff
    state.client.messages = messages

    artifacts = await runtime.get_artifacts(run.run_id)

    assert [item.reference for item in artifacts] == ["alpha.txt", "result.txt"]
    assert artifacts[1].metadata["evidence_sources"] == [
        "completed_session_tool",
        "provider_session_diff",
    ]


@pytest.mark.asyncio
async def test_artifact_byte_budget_is_cumulative_and_never_overread(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, run = _state(tmp_path, "")
    state = runtime._states[run.run_id]
    (state.workspace / "alpha.txt").write_bytes(b"123456")
    (state.workspace / "result.txt").write_bytes(b"abcdef")
    state.run = replace(
        run,
        budget=replace(run.budget, max_artifact_bytes=10),
    )

    async def diff(*_args, **_kwargs):
        return ({"path": "result.txt"}, {"path": "alpha.txt"})

    state.client.diff = diff
    real_read = opencode_adapter.os.read
    bytes_read = 0

    def tracked_read(descriptor: int, size: int) -> bytes:
        nonlocal bytes_read
        chunk = real_read(descriptor, size)
        bytes_read += len(chunk)
        return chunk

    monkeypatch.setattr(opencode_adapter.os, "read", tracked_read)

    with pytest.raises(RuntimeError, match="runtime_artifact_bytes_exceeded"):
        await runtime.get_artifacts(run.run_id)

    assert bytes_read == 6


@pytest.mark.asyncio
async def test_artifact_byte_budget_includes_runtime_result(tmp_path: Path) -> None:
    runtime, run = _state(tmp_path, "12345")
    state = runtime._states[run.run_id]
    state.workspace.joinpath("result.txt").write_bytes(b"123456")
    state.run = replace(
        run,
        budget=replace(run.budget, max_artifact_bytes=10),
    )

    with pytest.raises(RuntimeError, match="runtime_artifact_bytes_exceeded"):
        await runtime.get_artifacts(run.run_id)


@pytest.mark.asyncio
async def test_artifact_hash_rejects_ctime_only_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, run = _state(tmp_path, "")
    real_fstat = opencode_adapter.os.fstat
    calls = 0

    def changed_ctime_fstat(descriptor: int):
        nonlocal calls
        observed = real_fstat(descriptor)
        calls += 1
        if calls != 2:
            return observed
        values = list(observed)
        values[9] = observed.st_ctime + 1
        return opencode_adapter.os.stat_result(values)

    monkeypatch.setattr(opencode_adapter.os, "fstat", changed_ctime_fstat)

    with pytest.raises(RuntimeError, match="runtime_artifact_changed_during_hash"):
        await runtime.get_artifacts(run.run_id)


def test_artifact_hash_rejects_fifo_without_blocking(tmp_path: Path) -> None:
    if not hasattr(opencode_adapter.os, "mkfifo"):
        pytest.skip("FIFO indisponible sur cette plateforme")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    fifo = workspace / "artifact.fifo"
    opencode_adapter.os.mkfifo(fifo)

    digest, size_bytes = opencode_adapter._hash_stable_artifact(
        fifo,
        workspace=workspace,
        max_bytes=1024,
    )

    assert digest is None
    assert size_bytes is None


@pytest.mark.asyncio
async def test_artifact_fallback_refuses_more_than_100_session_files(
    tmp_path: Path,
) -> None:
    runtime, run = _state(tmp_path, "")
    state = runtime._states[run.run_id]

    async def empty_diff(*_args, **_kwargs):
        return ()

    async def messages(*_args, **_kwargs):
        return tuple(
            _tool_message(
                session_id=state.session_id,
                path=f"generated/file-{index:03d}.py",
            )
            for index in range(101)
        )

    state.client.diff = empty_diff
    state.client.messages = messages

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
                "edit": False,
                "write": False,
                "bash": False,
            },
        ),
        (
            ("workspace.read", "workspace.edit"),
            {"bash": False},
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
    assert True not in client.tools.values()


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


def test_mcp_normalizes_all_read_scopes_for_personal_knowledge(
    tmp_path: Path,
) -> None:
    runtime, run = _state(tmp_path, "résultat")
    state = runtime._states[run.run_id]
    integration_root = tmp_path / "provider-knowledge"
    integration_root.mkdir()
    runtime.layout = RuntimeLayout.from_integration_root(integration_root)
    runtime.layout.ensure()
    context = AgenticContext(
        run_id=run.run_id,
        profile_id=run.profile_id,
        channel=run.channel,
        origin=run.origin,
        permissions=(
            "communications.read",
            "calendar:read",
            "conversations.read",
            "memory:read",
            "contacts.read",
            "media:read",
            "documents.read",
            "documentation:read",
            "tasks.read",
            "project_state.read",
            "workspace:read",
            "research.search",
        ),
    )

    broker, overlay = runtime._capability_overlay(run, context, state.workspace)

    assert broker is not None
    try:
        assert broker.capability.scopes == frozenset(
            {
                "communications:read",
                "calendar:read",
                "conversations:read",
                "memory:read",
                "contacts:read",
                "media:read",
                "documents:read",
                "documentation:read",
                "tasks:read",
                "project_state:read",
                "workspace:read",
                "research:search",
            }
        )
        names = {tool["name"] for tool in broker.registry.list_tools()}
        assert "jarvis_knowledge_search" in names
        assert "jarvis_knowledge_get" in names
        assert "jarvis_tasks_list" in names
        assert overlay["mcp"]["jarvis"]["enabled"] is True
    finally:
        broker.stop()


def test_media_profile_mounts_installed_apple_music_mcp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, run = _state(tmp_path, "résultat")
    state = runtime._states[run.run_id]
    runtime.layout = RuntimeLayout.from_integration_root(tmp_path / "provider-music")
    runtime.layout.ensure()
    context = AgenticContext(
        run_id=run.run_id,
        profile_id=run.profile_id,
        channel=run.channel,
        origin=run.origin,
        permissions=("media:read", "media:publish"),
        selected_context={"capability_profile_id": "media"},
    )
    monkeypatch.setattr(
        opencode_adapter, "_apple_music_mcp_path", lambda: "/opt/bin/apple-music-mcp"
    )

    read_only_broker, read_only_overlay = runtime._capability_overlay(
        run, replace(context, permissions=("media:read",)), state.workspace
    )
    assert read_only_broker is not None
    try:
        assert "apple-music" not in read_only_overlay["mcp"]
    finally:
        read_only_broker.stop()

    broker, overlay = runtime._capability_overlay(run, context, state.workspace)

    assert broker is not None
    try:
        assert overlay["mcp"]["apple-music"] == {
            "type": "local",
            "command": ["/opt/bin/apple-music-mcp", "serve"],
            "enabled": True,
            "timeout": 30_000,
        }
    finally:
        broker.stop()


def test_plugin_manifest_and_default_runtime_declare_knowledge_scopes() -> None:
    manifest_path = Path(__file__).resolve().parents[1] / "plugin.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    declared = {
        (item["name"], item["scope"]) for item in manifest["runtime"]["capabilities"]
    }
    runtime = create_runtime()
    registered = {(item.name, item.scope) for item in runtime.capabilities}

    assert declared == registered
    assert {
        ("communications.read", "communications:read"),
        ("calendar.read", "calendar:read"),
        ("project_state.read", "project_state:read"),
        ("conversations.read", "conversations:read"),
        ("memory.read", "memory:read"),
        ("contacts.read", "contacts:read"),
        ("media.read", "media:read"),
        ("documents.read", "documents:read"),
        ("documentation.read", "documentation:read"),
    }.issubset(declared)


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


def test_select_model_prefers_deepseek_and_skips_anonymous_opencode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-test-secret")
    monkeypatch.setattr(opencode_adapter, "_configured_model_ids", lambda **_: ())
    catalog = SimpleNamespace(
        connected=("opencode", "deepseek", "jarvis-e2e"),
        default={
            "opencode": "big-pickle",
            "deepseek": "deepseek-v4-pro",
            "jarvis-e2e": "fixture-model",
        },
    )
    selected = opencode_adapter._select_model(catalog)
    assert selected == ModelSelection(
        provider_id="deepseek", model_id="deepseek-v4-pro"
    )


def test_select_model_allows_fixture_provider_without_deepseek(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setattr(opencode_adapter, "_configured_model_ids", lambda **_: ())
    catalog = SimpleNamespace(
        connected=("opencode", "jarvis-e2e"),
        default={"opencode": "big-pickle", "jarvis-e2e": "fixture-model"},
    )
    selected = opencode_adapter._select_model(catalog)
    assert selected.provider_id == "jarvis-e2e"
    assert selected.model_id == "fixture-model"


def test_select_model_rejects_anonymous_opencode_without_deepseek_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setattr(opencode_adapter, "_configured_model_ids", lambda **_: ())
    catalog = SimpleNamespace(
        connected=("opencode",),
        default={"opencode": "big-pickle"},
    )
    with pytest.raises(RuntimeError, match="DEEPSEEK_API_KEY absente"):
        opencode_adapter._select_model(catalog)


def test_model_provider_environment_loads_jarvis_env_without_second_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_secret = "deepseek-test-secret"
    monkeypatch.setenv("DEEPSEEK_API_KEY", fake_secret)
    monkeypatch.setenv("OPENCODE_DEEPSEEK_API_KEY", "must-never-be-used")
    env = opencode_adapter._model_provider_environment()
    assert set(env) == {"DEEPSEEK_API_KEY"}
    assert env["DEEPSEEK_API_KEY"] == fake_secret
    assert "OPENCODE_DEEPSEEK_API_KEY" not in env
    assert opencode_adapter._MODEL_PROVIDER_ENV_ALLOWLIST == ("DEEPSEEK_API_KEY",)


def test_select_agent_uses_coding_when_workspace_write_granted() -> None:
    run = AgenticRun(
        run_id="run-coding",
        profile_id="default",
        origin="user",
        channel="api",
        runtime_id="opencode",
        title="corriger un typo",
        category=AgenticRequestCategory.AGENTIC_REVERSIBLE,
        permissions=("workspace:read", "workspace:write"),
    )
    context = AgenticContext(
        run_id=run.run_id,
        profile_id=run.profile_id,
        channel=run.channel,
        origin=run.origin,
        permissions=run.permissions,
    )
    assert opencode_adapter._select_agent(run, context) == "jarvis-coding"


def test_select_agent_keeps_executor_without_write_permission() -> None:
    run = AgenticRun(
        run_id="run-readonly",
        profile_id="default",
        origin="user",
        channel="api",
        runtime_id="opencode",
        title="résumer le dépôt",
        category=AgenticRequestCategory.AGENTIC_READONLY,
        permissions=("workspace:read",),
    )
    context = AgenticContext(
        run_id=run.run_id,
        profile_id=run.profile_id,
        channel=run.channel,
        origin=run.origin,
        permissions=run.permissions,
    )
    assert opencode_adapter._select_agent(run, context) == "jarvis-executor"


def test_select_model_uses_configured_id_present_in_catalog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-test-secret")
    monkeypatch.setattr(
        opencode_adapter,
        "_configured_model_ids",
        lambda *, coding: (
            ("deepseek-v4-flash",) if not coding else ("deepseek-v4-pro",)
        ),
    )
    catalog = SimpleNamespace(
        connected=("deepseek", "opencode"),
        default={"deepseek": "deepseek-v4-pro", "opencode": "big-pickle"},
        all=(
            {
                "id": "deepseek",
                "models": {"deepseek-v4-flash": {}, "deepseek-v4-pro": {}},
            },
        ),
    )
    fast = opencode_adapter._select_model(catalog, coding=False)
    coding = opencode_adapter._select_model(catalog, coding=True)
    assert fast.model_id == "deepseek-v4-flash"
    assert coding.model_id == "deepseek-v4-pro"
    assert fast.provider_id == "deepseek"
    assert coding.provider_id == "deepseek"


def test_select_model_rejects_configured_id_absent_from_catalog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-test-secret")
    monkeypatch.setattr(
        opencode_adapter,
        "_configured_model_ids",
        lambda **_: ("modele-disparu",),
    )
    catalog = SimpleNamespace(
        connected=("deepseek",),
        default={"deepseek": "deepseek-v4-pro"},
        all=({"id": "deepseek", "models": {"deepseek-v4-pro": {}}},),
    )
    with pytest.raises(RuntimeError, match="configuré absent"):
        opencode_adapter._select_model(catalog)


def test_select_model_ignores_hostile_catalog_and_anonymous_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-test-secret")
    monkeypatch.setattr(opencode_adapter, "_configured_model_ids", lambda **_: ())
    catalog = SimpleNamespace(
        connected=("opencode", "deepseek"),
        default={
            "opencode": "../../etc/passwd",
            "deepseek": "deepseek-v4-pro",
        },
        all=(
            {"id": "opencode", "models": {"sk-live-not-a-model": {}}},
            {"id": "deepseek", "models": {"deepseek-v4-pro": {}}},
        ),
    )
    selected = opencode_adapter._select_model(catalog)
    assert selected.provider_id == "deepseek"
    assert selected.model_id == "deepseek-v4-pro"
