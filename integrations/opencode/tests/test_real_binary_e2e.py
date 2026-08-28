"""Preuve opt-in du vrai serveur OpenCode contre des pairs loopback locaux.

Exécution explicite :

    pytest -m external_network integrations/opencode/tests/test_real_binary_e2e.py -q

Le marker garde la suite standard hors ligne. Malgré son nom historique, ce test
n'ouvre aucune connexion publique : serveur OpenCode, provider et MCP restent
sur la machine locale.
"""

from __future__ import annotations

import asyncio
from contextlib import aclosing, suppress
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
from typing import Any, AsyncGenerator, Sequence, cast

import httpx
import pytest

from integrations.opencode.adapter import OpenCodeRuntime
from integrations.opencode.client import (
    BasicAuthCredentials,
    ModelSelection,
    OpenCodeClient,
    TextPart,
)
from integrations.opencode.config import OpenCodeSettings, RuntimeLayout
from integrations.opencode.config import settings as config_settings
from integrations.opencode.lifecycle.install import VerificationReport
from integrations.opencode.lifecycle.process import OpenCodeProcessManager
from integrations.opencode.lifecycle.release import ReleaseManifest
from integrations.opencode.tools.e2e_fixture import FINAL_MARKER, LoopbackOpenAIProvider
from jarvis.agentic.models import (
    AgenticContext,
    AgenticRequestCategory,
    AgenticRun,
    AgenticRunStatus,
    ApprovalDecision,
    ApprovalRequest,
    Artifact,
    RuntimeEvent,
    ToolCapability,
)


pytestmark = pytest.mark.external_network

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = PLUGIN_ROOT.parents[1]
REAL_BINARY = (
    PLUGIN_ROOT
    / ".runtime"
    / "bin"
    / ("opencode.exe" if os.name == "nt" else "opencode")
)


class _InstalledBinary:
    def __init__(self, binary: Path, manifest: ReleaseManifest) -> None:
        self.binary = binary
        self.manifest = manifest

    def verify(self, *, execute_binary: bool = True) -> VerificationReport:
        return VerificationReport(
            True,
            self.manifest.version,
            "real-binary-e2e",
            self.binary,
            (),
        )


def _install_real_binary(layout: RuntimeLayout) -> None:
    assert REAL_BINARY.is_file(), (
        "Le test opt-in exige le binaire épinglé déjà installé via "
        "integrations/opencode/scripts/manager.py install"
    )
    layout.ensure()
    # Une copie garde l'instance de preuve hermétique : chmod, état ou cleanup
    # du tmp_path ne peuvent jamais modifier l'inode de l'installation réelle.
    shutil.copy2(REAL_BINARY, layout.binary_path)
    layout.binary_path.chmod(0o700)


def _provider_config(
    base: dict[str, Any],
    provider_url: str,
    *,
    edit_permission: str | None = None,
    mcp_trace: Path | None = None,
    mcp_workspace: Path | None = None,
) -> dict[str, Any]:
    config = json.loads(json.dumps(base))
    config["provider"] = {
        "jarvis-e2e": {
            "npm": "@ai-sdk/openai-compatible",
            "name": "JARVIS deterministic loopback fixture",
            "options": {"baseURL": f"{provider_url}/v1", "apiKey": "public"},
            "models": {
                "fixture-model": {
                    "name": "JARVIS deterministic fixture model",
                    "tool_call": True,
                }
            },
        }
    }
    # OpenCode ships built-in providers that can appear connected even without
    # credentials. The generic adapter deliberately selects from that catalog,
    # so the deterministic proof must make the fixture the only eligible
    # provider instead of relying on catalog iteration order.
    config["enabled_providers"] = ["jarvis-e2e"]
    config.setdefault("agent", {})["jarvis-e2e"] = {
        "description": "Agent de preuve E2E local et déterministe.",
        "mode": "subagent",
        "prompt": "Utilise l'outil fixture_echo une fois puis rends sa valeur.",
        "permission": {
            "*": "allow",
            "bash": "deny",
            "edit": "deny",
            "external_directory": "deny",
            "task": "deny",
            "webfetch": "deny",
            "websearch": "deny",
        },
    }
    config["model"] = "jarvis-e2e/fixture-model"
    if edit_permission is not None:
        executor = config.setdefault("agent", {}).setdefault("jarvis-executor", {})
        # OpenCode applique la dernière règle correspondante. Le wildcard doit
        # donc précéder les règles spécifiques, et ``write`` est bien couvert
        # par la permission native ``edit`` dans le schéma 1.18.16.
        executor["permission"] = {
            "*": "allow",
            "edit": edit_permission,
            "bash": "deny",
            "external_directory": "deny",
            "task": "deny",
            "webfetch": "deny",
            "websearch": "deny",
        }
    if mcp_trace is not None:
        command = [
            sys.executable,
            "-m",
            "integrations.opencode.tools.e2e_fixture",
            "mcp",
            "--trace",
            str(mcp_trace),
        ]
        if mcp_workspace is not None:
            command.extend(("--workspace", str(mcp_workspace)))
        config.setdefault("mcp", {})["jarvis-e2e"] = {
            "type": "local",
            "command": command,
            "enabled": True,
            "environment": {"PYTHONPATH": str(REPOSITORY_ROOT)},
        }
    return config


async def _wait_for_mcp(client: OpenCodeClient, workspace: Path) -> dict[str, Any]:
    last: dict[str, Any] = {}
    for _ in range(100):
        last = await client.mcp_status(directory=str(workspace))
        if "connected" in json.dumps(last, sort_keys=True).casefold():
            return last
        await asyncio.sleep(0.1)
    pytest.fail(f"Le MCP local n'est pas connecté: {last!r}")


def _message_text(messages: tuple[Any, ...]) -> str:
    values: list[str] = []
    for message in messages:
        for part in message.parts:
            text = part.get("text") if isinstance(part, dict) else None
            if isinstance(text, str):
                values.append(text)
    return "\n".join(values)


async def _wait_for_final(
    client: OpenCodeClient,
    session_id: str,
    workspace: Path,
) -> str:
    last = ""
    for _ in range(200):
        last = _message_text(
            await client.messages(session_id, directory=str(workspace), limit=100)
        )
        if FINAL_MARKER in last:
            return last
        await asyncio.sleep(0.1)
    pytest.fail(f"Réponse finale déterministe absente: {last[-500:]!r}")


def _runtime_settings() -> OpenCodeSettings:
    return OpenCodeSettings(
        startup_timeout_seconds=30,
        shutdown_timeout_seconds=10,
        request_timeout_seconds=10,
        sse_connect_timeout_seconds=10,
        sse_read_timeout_seconds=45,
        reconnect_attempts=1,
    )


def _write_provider_template(
    path: Path,
    provider: LoopbackOpenAIProvider,
    *,
    edit_permission: str,
    mcp_trace: Path | None = None,
    mcp_workspace: Path | None = None,
) -> None:
    base_config = json.loads(
        config_settings.OPENCODE_CONFIG_TEMPLATE.read_text(encoding="utf-8")
    )
    path.write_text(
        json.dumps(
            _provider_config(
                base_config,
                provider.base_url,
                edit_permission=edit_permission,
                mcp_trace=mcp_trace,
                mcp_workspace=mcp_workspace,
            ),
            indent=2,
        ),
        encoding="utf-8",
    )


def _real_runtime(
    layout: RuntimeLayout,
    settings: OpenCodeSettings,
    manifest: ReleaseManifest,
    *,
    capabilities: Sequence[ToolCapability] = (),
) -> OpenCodeRuntime:
    return OpenCodeRuntime(
        capabilities=capabilities,
        layout=layout,
        settings=settings,
        manifest=manifest,
        install_manager=cast(Any, _InstalledBinary(layout.binary_path, manifest)),
    )


def _agentic_case(
    workspace: Path,
    *,
    run_id: str,
    request: str,
    category: AgenticRequestCategory,
    permissions: tuple[str, ...],
) -> tuple[AgenticRun, AgenticContext]:
    run = AgenticRun(
        run_id=run_id,
        profile_id="default",
        origin="user",
        channel="api",
        runtime_id="opencode",
        title=request,
        status=AgenticRunStatus.QUEUED,
        category=category,
        workspace=str(workspace),
        permissions=permissions,
    )
    context = AgenticContext(
        run_id=run.run_id,
        profile_id=run.profile_id,
        channel=run.channel,
        origin=run.origin,
        permissions=permissions,
        selected_context={"request": request},
    )
    return run, context


def _init_git_worktree(workspace: Path, tracked_file: Path) -> None:
    subprocess.run(
        ["git", "init", "--quiet"],
        cwd=workspace,
        check=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    subprocess.run(
        ["git", "add", tracked_file.name],
        cwd=workspace,
        check=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    subprocess.run(
        [
            "git",
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "user.name=JARVIS E2E",
            "-c",
            "user.email=jarvis-e2e@invalid",
            "commit",
            "--quiet",
            "--no-verify",
            "-m",
            "fixture baseline",
        ],
        cwd=workspace,
        check=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )


async def _collect_facade_terminal(
    runtime: OpenCodeRuntime,
    run: AgenticRun,
    *,
    approval_flow: str | None = None,
    provider: LoopbackOpenAIProvider | None = None,
) -> tuple[list[RuntimeEvent], tuple[Artifact, ...], bool]:
    events: list[RuntimeEvent] = []
    artifacts: tuple[Artifact, ...] = ()
    approval_seen = False
    terminal_seen = False
    stream = cast(AsyncGenerator[RuntimeEvent, None], runtime.stream_events(run.run_id))
    try:
        async with asyncio.timeout(45):
            async with aclosing(stream):
                async for event in stream:
                    events.append(event)
                    if event.type == "agent.approval.requested":
                        approval_seen = True
                        approval = ApprovalRequest(
                            approval_id=str(
                                event.payload.get("approval_id") or "permission"
                            ),
                            run_id=run.run_id,
                            action=str(event.payload.get("action") or "edit"),
                            tool=str(event.payload.get("tool") or "edit"),
                            summary="Modifier le fichier de fixture confiné",
                            sanitized_arguments={},
                            expires_at=datetime.now(timezone.utc)
                            + timedelta(minutes=1),
                        )
                        if approval_flow == "deny":
                            await runtime.answer_approval(
                                run.run_id,
                                replace(approval, decision=ApprovalDecision.DENIED),
                            )
                        else:
                            pytest.fail(
                                "Approbation inattendue dans le scénario vrai-binaire"
                            )
                    if event.type in {"agent.run.completed", "agent.run.failed"}:
                        artifacts = tuple(await runtime.get_artifacts(run.run_id))
                        terminal_seen = True
                        break
    except TimeoutError:
        state = runtime._states.get(run.run_id)
        pump = state.pump if state is not None else None
        pump_result = "missing"
        if pump is not None:
            pump_result = "running"
            if pump.done():
                pump_result = repr(pump.exception())
        provider_trace = provider.trace.snapshot() if provider is not None else []
        model = (
            f"{state.model.provider_id}/{state.model.model_id}"
            if state is not None
            else "missing"
        )
        pytest.fail(
            "timeout vrai-binaire; "
            f"events={[event.type for event in events]!r}; "
            f"pump={pump_result}; model={model}; provider={provider_trace!r}"
        )
    assert terminal_seen, [event.type for event in events]
    return events, artifacts, approval_seen


def _trace_records(path: Path) -> tuple[dict[str, Any], ...]:
    if not path.is_file():
        return ()
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            records.append(value)
    return tuple(records)


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


async def _wait_pid_gone(pid: int) -> None:
    for _ in range(100):
        if not _pid_alive(pid):
            return
        await asyncio.sleep(0.05)
    pytest.fail(f"Processus fixture résiduel détecté: pid={pid}")


@pytest.mark.asyncio
async def test_real_binary_private_server_prompt_mcp_abort_and_clean_stop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exerce le contrat critique sans Internet, compte ni clé externe."""

    manifest = ReleaseManifest.load()
    integration_root = tmp_path / "isolated-opencode-plugin"
    integration_root.mkdir()
    layout = RuntimeLayout.from_integration_root(integration_root)
    _install_real_binary(layout)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    mcp_trace = tmp_path / "mcp-trace.jsonl"
    template_path = tmp_path / "opencode-e2e.json"
    provider = LoopbackOpenAIProvider().start()
    manager: OpenCodeProcessManager | None = None
    client: OpenCodeClient | None = None
    state = None
    mcp_pid: int | None = None
    try:
        base_config = json.loads(
            config_settings.OPENCODE_CONFIG_TEMPLATE.read_text(encoding="utf-8")
        )
        template_path.write_text(
            json.dumps(_provider_config(base_config, provider.base_url), indent=2),
            encoding="utf-8",
        )
        monkeypatch.setattr(config_settings, "OPENCODE_CONFIG_TEMPLATE", template_path)
        settings = OpenCodeSettings(
            startup_timeout_seconds=30,
            shutdown_timeout_seconds=10,
            request_timeout_seconds=10,
            sse_connect_timeout_seconds=10,
            sse_read_timeout_seconds=30,
            reconnect_attempts=1,
        )
        manager = OpenCodeProcessManager(
            layout=layout,
            settings=settings,
            manifest=manifest,
            install_manager=_InstalledBinary(layout.binary_path, manifest),
        )
        state = manager.start(
            workspace=workspace,
            explicit_environment={"PYTHONPATH": str(REPOSITORY_ROOT)},
            additional_environment_allowlist=("PYTHONPATH",),
            runtime_config_overlay={
                "mcp": {
                    "jarvis-e2e": {
                        "type": "local",
                        "command": [
                            sys.executable,
                            "-m",
                            "integrations.opencode.tools.e2e_fixture",
                            "mcp",
                            "--trace",
                            str(mcp_trace),
                        ],
                        "enabled": True,
                        "environment": {"PYTHONPATH": str(REPOSITORY_ROOT)},
                    }
                }
            },
        )
        assert state.hostname == "127.0.0.1"
        assert state.port > 0
        auth_payload = json.loads(layout.auth_state_path.read_text(encoding="utf-8"))
        credentials = BasicAuthCredentials(
            str(auth_payload["username"]),
            str(auth_payload["password"]),
        )

        async with httpx.AsyncClient(base_url=state.base_url, trust_env=False) as raw:
            accepted = await raw.get("/global/health", auth=credentials.as_httpx())
            missing = await raw.get("/global/health")
            rejected = await raw.get(
                "/global/health",
                auth=httpx.BasicAuth(credentials.username, "x" * 32),
            )
            foreign_origin = await raw.get(
                "/global/health",
                auth=credentials.as_httpx(),
                headers={"Origin": "https://attacker.invalid"},
            )
        assert accepted.status_code == 200
        assert missing.status_code == 401
        assert rejected.status_code == 401
        assert foreign_origin.status_code == 200
        assert "access-control-allow-origin" not in foreign_origin.headers
        assert credentials.password not in repr(credentials)

        client = OpenCodeClient(
            state.base_url,
            credentials,
            expected_version=manifest.version,
            settings=settings,
        )
        health = await client.health()
        assert health.healthy is True
        assert health.version == manifest.version
        mcp_status = await _wait_for_mcp(client, workspace)
        assert "jarvis-e2e" in mcp_status
        started_records = _trace_records(mcp_trace)
        started = next(
            record for record in started_records if record.get("event") == "started"
        )
        mcp_pid = int(started["pid"])

        events: list[Any] = []

        async def collect_events() -> None:
            async for event in client.stream_events(directory=str(workspace)):
                events.append(event)

        collector = asyncio.create_task(collect_events())
        await asyncio.sleep(0.25)
        session = await client.create_session(
            title="JARVIS real-binary E2E",
            directory=str(workspace),
        )
        await client.prompt_async(
            session.id,
            [TextPart("Call fixture_echo with MCP_ECHO_OK, then return the result.")],
            model=ModelSelection("jarvis-e2e", "fixture-model"),
            agent="jarvis-e2e",
            tools={},
            system="Local deterministic integration proof. Never access the public network.",
            directory=str(workspace),
        )
        final_text = await _wait_for_final(client, session.id, workspace)
        assert FINAL_MARKER in final_text
        await asyncio.sleep(0.25)
        collector.cancel()
        with suppress(asyncio.CancelledError):
            await collector
        assert events
        assert any(
            session.id in json.dumps(event.data, default=str) for event in events
        )

        provider_requests = provider.trace.snapshot()
        assert any(
            any(name.endswith("fixture_echo") for name in request["tool_names"])
            for request in provider_requests
        )
        assert any(request["has_tool_result"] for request in provider_requests)
        assert any(
            record.get("event") == "tool_call" and record.get("name") == "fixture_echo"
            for record in _trace_records(mcp_trace)
        )

        abort_session = await client.create_session(
            title="JARVIS real-binary abort E2E",
            directory=str(workspace),
        )
        await client.prompt_async(
            abort_session.id,
            [TextPart("ABORT_ME")],
            model=ModelSelection("jarvis-e2e", "fixture-model"),
            agent="jarvis-e2e",
            tools={},
            system="Local deterministic abort proof.",
            directory=str(workspace),
        )
        provider_started_abort = await asyncio.to_thread(
            provider.abort_started.wait,
            10,
        )
        assert provider_started_abort is True
        assert await client.abort(abort_session.id, directory=str(workspace)) is True
        provider_released_abort = await asyncio.to_thread(
            provider.abort_released.wait,
            5,
        )
        assert provider_released_abort is True
    finally:
        if client is not None:
            await client.close()
        if manager is not None and state is not None:
            manager.stop()
        provider.close()

    assert state is not None
    await _wait_pid_gone(state.pid)
    if mcp_pid is not None:
        await _wait_pid_gone(mcp_pid)
    assert not layout.process_state_path.exists()
    assert not layout.auth_state_path.exists()
    with pytest.raises(OSError):
        with socket.create_connection((state.hostname, state.port), timeout=0.2):
            pass


@pytest.mark.asyncio
async def test_real_binary_generic_facade_readonly_and_coding_worktree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """La façade générique lit sans effet puis modifie réellement un worktree."""

    manifest = ReleaseManifest.load()
    integration_root = tmp_path / "generic-facade-plugin"
    integration_root.mkdir()
    layout = RuntimeLayout.from_integration_root(integration_root)
    _install_real_binary(layout)
    readonly_workspace = tmp_path / "readonly-worktree"
    readonly_workspace.mkdir()
    readonly_file = readonly_workspace / "evidence.txt"
    readonly_file.write_text("READONLY_SOURCE\n", encoding="utf-8")
    _init_git_worktree(readonly_workspace, readonly_file)
    coding_workspace = tmp_path / "coding-worktree"
    coding_workspace.mkdir()
    coding_file = coding_workspace / "app.txt"
    coding_file.write_text("VALUE=before\n", encoding="utf-8")
    _init_git_worktree(coding_workspace, coding_file)
    provider = LoopbackOpenAIProvider()
    provider.register_file_scenario(
        "READONLY_E2E",
        readonly_file,
        initial="READONLY_SOURCE\n",
        corrected="READONLY_SOURCE\n",
    )
    provider.register_file_scenario(
        "CODING_E2E",
        coding_file,
        initial="VALUE=before\n",
        corrected="VALUE=after\n",
    )
    provider.start()
    template_path = tmp_path / "generic-opencode.json"
    _write_provider_template(
        template_path,
        provider,
        edit_permission="allow",
    )
    monkeypatch.setattr(config_settings, "OPENCODE_CONFIG_TEMPLATE", template_path)
    runtime = _real_runtime(layout, _runtime_settings(), manifest)
    try:
        readonly_run, readonly_context = _agentic_case(
            readonly_workspace,
            run_id="real-readonly",
            request="READONLY_E2E",
            category=AgenticRequestCategory.AGENTIC_READONLY,
            permissions=("workspace.read",),
        )
        await runtime.create_run(readonly_run, readonly_context)
        assert runtime._states[readonly_run.run_id].model == ModelSelection(
            "jarvis-e2e", "fixture-model"
        )
        await runtime.start(readonly_run)
        (
            readonly_events,
            readonly_artifacts,
            readonly_approval,
        ) = await _collect_facade_terminal(runtime, readonly_run, provider=provider)

        coding_run, coding_context = _agentic_case(
            coding_workspace,
            run_id="real-coding",
            request="CODING_E2E",
            category=AgenticRequestCategory.AGENTIC_REVERSIBLE,
            permissions=("workspace.read", "workspace.edit"),
        )
        await runtime.create_run(coding_run, coding_context)
        assert runtime._states[coding_run.run_id].model == ModelSelection(
            "jarvis-e2e", "fixture-model"
        )
        await runtime.start(coding_run)
        (
            coding_events,
            coding_artifacts,
            coding_approval,
        ) = await _collect_facade_terminal(runtime, coding_run, provider=provider)
    finally:
        try:
            await runtime.dispose()
        finally:
            provider.close()

    assert readonly_file.read_text(encoding="utf-8") == "READONLY_SOURCE\n"
    assert readonly_approval is False
    assert any(
        event.type == "agent.tool.completed"
        and str(event.payload.get("tool") or "").endswith("read")
        for event in readonly_events
    )
    assert not any(item.type == "changed_file" for item in readonly_artifacts)
    assert coding_file.read_text(encoding="utf-8") == "VALUE=after\n"
    assert coding_approval is False
    assert any(
        event.type == "agent.tool.completed"
        and str(event.payload.get("tool") or "").endswith("edit")
        for event in coding_events
    )
    assert any(
        item.type == "changed_file"
        and item.reference == coding_file.name
        and item.sha256 == hashlib.sha256(b"VALUE=after\n").hexdigest()
        and item.size_bytes == len(b"VALUE=after\n")
        and item.metadata.get("evidence_sources") == ["completed_session_tool"]
        for item in coding_artifacts
    )
    scenarios = {record.get("scenario") for record in provider.trace.snapshot()}
    assert {"READONLY_E2E", "CODING_E2E"} <= scenarios


@pytest.mark.asyncio
async def test_real_binary_generic_facade_gate_red_fix_and_verify_green(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Le provider observe un vrai gate rouge, corrige, puis obtient le vert."""

    manifest = ReleaseManifest.load()
    integration_root = tmp_path / "generic-gate-plugin"
    integration_root.mkdir()
    layout = RuntimeLayout.from_integration_root(integration_root)
    _install_real_binary(layout)
    workspace = tmp_path / "gate-worktree"
    workspace.mkdir()
    gate_file = workspace / "gate.txt"
    gate_file.write_text("GATE_RED\n", encoding="utf-8")
    _init_git_worktree(workspace, gate_file)
    mcp_trace = tmp_path / "gate-mcp-trace.jsonl"
    provider = LoopbackOpenAIProvider()
    provider.register_file_scenario(
        "GATE_E2E",
        gate_file,
        initial="GATE_RED\n",
        corrected="GATE_GREEN\n",
    )
    provider.start()
    template_path = tmp_path / "gate-opencode.json"
    _write_provider_template(
        template_path,
        provider,
        edit_permission="allow",
        mcp_trace=mcp_trace,
        mcp_workspace=workspace,
    )
    monkeypatch.setattr(config_settings, "OPENCODE_CONFIG_TEMPLATE", template_path)
    runtime = _real_runtime(layout, _runtime_settings(), manifest)
    try:
        run, context = _agentic_case(
            workspace,
            run_id="real-gate-correction",
            request="GATE_E2E",
            category=AgenticRequestCategory.AGENTIC_REVERSIBLE,
            permissions=("workspace.read", "workspace.edit"),
        )
        await runtime.create_run(run, context)
        assert runtime._states[run.run_id].model == ModelSelection(
            "jarvis-e2e", "fixture-model"
        )
        mcp_status = await _wait_for_mcp(runtime._states[run.run_id].client, workspace)
        assert "jarvis-e2e" in mcp_status
        await runtime.start(run)
        events, artifacts, approval_seen = await _collect_facade_terminal(
            runtime,
            run,
            provider=provider,
        )
    finally:
        try:
            await runtime.dispose()
        finally:
            provider.close()

    assert approval_seen is False
    assert gate_file.read_text(encoding="utf-8") == "GATE_GREEN\n"
    gate_results = [
        record.get("result")
        for record in _trace_records(mcp_trace)
        if record.get("event") == "tool_call"
        and str(record.get("name") or "").endswith("fixture_gate")
    ]
    assert gate_results == ["GATE_RED", "GATE_GREEN"]
    assert any(
        event.type == "agent.tool.completed"
        and str(event.payload.get("tool") or "").endswith("edit")
        for event in events
    )
    assert any(
        item.type == "changed_file"
        and item.reference == gate_file.name
        and item.sha256 == hashlib.sha256(b"GATE_GREEN\n").hexdigest()
        and item.size_bytes == len(b"GATE_GREEN\n")
        and item.metadata.get("evidence_sources") == ["completed_session_tool"]
        for item in artifacts
    )


@pytest.mark.asyncio
async def test_real_binary_generic_facade_denies_native_edit_without_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Le refus natif réel passe uniquement par l'endpoint OpenCode prévu.

    OpenCode 1.18.16 émet ``permission.asked`` pour son outil ``edit``. Même
    avec un broker MCP mutateur actif, cet ID natif ne doit jamais être traité
    comme un effet ``mcp:`` exécuté par JARVIS. Le test refuse la demande via
    l'endpoint de permission fournisseur et vérifie l'absence de mutation.

    En production, ``workspace:write`` sélectionne ``jarvis-coding`` (edit
    allow). Ce scénario force ``jarvis-executor`` pour verrouiller le chemin
    ``edit=ask`` + refus natif.
    """

    from integrations.opencode import adapter as adapter_module

    monkeypatch.setattr(
        adapter_module, "_select_agent", lambda *_args, **_kwargs: "jarvis-executor"
    )
    manifest = ReleaseManifest.load()
    integration_root = tmp_path / "generic-approval-plugin"
    integration_root.mkdir()
    layout = RuntimeLayout.from_integration_root(integration_root)
    _install_real_binary(layout)
    workspace = tmp_path / "approval-worktree"
    workspace.mkdir()
    target = workspace / "approval.txt"
    target.write_text("APPROVAL=before\n", encoding="utf-8")
    _init_git_worktree(workspace, target)
    provider = LoopbackOpenAIProvider()
    provider.register_file_scenario(
        "APPROVAL_E2E",
        target,
        initial="APPROVAL=before\n",
        corrected="APPROVAL=after\n",
    )
    provider.start()
    template_path = tmp_path / "approval-opencode.json"
    _write_provider_template(
        template_path,
        provider,
        edit_permission="ask",
    )
    template = json.loads(template_path.read_text(encoding="utf-8"))
    permission_items = list(template["agent"]["jarvis-executor"]["permission"].items())
    assert permission_items[0] == ("*", "allow")
    assert permission_items[1] == ("edit", "ask")
    monkeypatch.setattr(config_settings, "OPENCODE_CONFIG_TEMPLATE", template_path)
    runtime = _real_runtime(layout, _runtime_settings(), manifest)
    try:
        run, context = _agentic_case(
            workspace,
            run_id="real-approval-denied",
            request="APPROVAL_E2E",
            category=AgenticRequestCategory.AGENTIC_REVERSIBLE,
            permissions=("workspace.read", "workspace.edit", "tasks.write"),
        )
        await runtime.create_run(run, context)
        assert runtime._states[run.run_id].model == ModelSelection(
            "jarvis-e2e", "fixture-model"
        )
        assert runtime._states[run.run_id].agent == "jarvis-executor"
        broker = runtime._states[run.run_id].mcp_broker
        assert broker is not None

        def forbid_mcp_routing(**_kwargs: Any) -> None:
            pytest.fail("une permission native ne doit jamais traverser le broker MCP")

        monkeypatch.setattr(broker, "grant_approval", forbid_mcp_routing)
        monkeypatch.setattr(
            broker,
            "approve_and_execute_pending",
            forbid_mcp_routing,
        )
        monkeypatch.setattr(broker, "revoke_approval", forbid_mcp_routing)
        await runtime.start(run)
        events, artifacts, approval_seen = await _collect_facade_terminal(
            runtime,
            run,
            approval_flow="deny",
            provider=provider,
        )
    finally:
        try:
            await runtime.dispose()
        finally:
            provider.close()

    assert approval_seen is True
    assert target.read_text(encoding="utf-8") == "APPROVAL=before\n"
    requested = [event for event in events if event.type == "agent.approval.requested"]
    resolved = [event for event in events if event.type == "agent.approval.resolved"]
    assert len(requested) == 1
    assert len(resolved) == 1
    approval_id = str(requested[0].payload["approval_id"])
    assert not approval_id.startswith("mcp:")
    assert resolved[0].payload["approval_id"] == approval_id
    assert not any(item.type == "changed_file" for item in artifacts)


@pytest.mark.asyncio
async def test_real_binary_repeated_identical_tool_call_is_stopped_early(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Le vrai binaire boucle sur un appel identique ; le garde l'arrête tôt.

    Le fournisseur loopback rejoue la forme exacte de l'incident : il redemande
    indéfiniment ``read`` avec les mêmes arguments et ne rend jamais de réponse
    finale. Sans garde, la session tournerait jusqu'à épuiser le budget — dix
    appels en production. Ici l'arrêt doit tomber sur la troisième répétition.
    """

    manifest = ReleaseManifest.load()
    integration_root = tmp_path / "loop-plugin"
    integration_root.mkdir()
    layout = RuntimeLayout.from_integration_root(integration_root)
    _install_real_binary(layout)
    workspace = tmp_path / "loop-worktree"
    workspace.mkdir()
    looped_file = workspace / "notes.txt"
    looped_file.write_text("LOOP_SOURCE\n", encoding="utf-8")
    _init_git_worktree(workspace, looped_file)

    provider = LoopbackOpenAIProvider()
    provider.register_file_scenario(
        "LOOP_E2E",
        looped_file,
        initial="LOOP_SOURCE\n",
        corrected="LOOP_SOURCE\n",
    )
    provider.start()
    template_path = tmp_path / "loop-opencode.json"
    _write_provider_template(template_path, provider, edit_permission="deny")
    monkeypatch.setattr(config_settings, "OPENCODE_CONFIG_TEMPLATE", template_path)
    runtime = _real_runtime(layout, _runtime_settings(), manifest)
    try:
        run, context = _agentic_case(
            workspace,
            run_id="real-loop",
            request="LOOP_E2E",
            category=AgenticRequestCategory.AGENTIC_READONLY,
            permissions=("workspace.read",),
        )
        await runtime.create_run(run, context)
        await runtime.start(run)
        # `_collect_facade_terminal` relit les artefacts après le terminal ;
        # ici l'échec purge déjà l'état du run, donc on draine simplement le
        # flux d'événements jusqu'au terminal.
        events = []
        stream = cast(
            AsyncGenerator[RuntimeEvent, None], runtime.stream_events(run.run_id)
        )
        async with asyncio.timeout(45):
            async with aclosing(stream):
                async for event in stream:
                    events.append(event)
                    if event.type in {"agent.run.completed", "agent.run.failed"}:
                        break
    finally:
        try:
            await runtime.dispose()
        finally:
            provider.close()

    failure = events[-1]
    assert failure.type == "agent.run.failed"
    assert failure.payload["violation"] == "doom_loop_same_action"
    assert failure.payload["repetitions"] == 3
    assert failure.payload["abort_acknowledged"] is True
    # Le fichier n'a pas bougé : la boucle était une lecture, et l'arrêt
    # n'introduit aucun effet.
    assert looped_file.read_text(encoding="utf-8") == "LOOP_SOURCE\n"
    starts = [event for event in events if event.type == "agent.tool.started"]
    assert len(starts) <= 4, "l'incident réel produisait dix démarrages"
    assert str(failure.payload.get("tool") or "").endswith("read")


@pytest.mark.asyncio
@pytest.mark.skipif(
    sys.platform != "darwin",
    reason="la validation de livraison exige le sandbox macOS fail-closed",
)
async def test_real_binary_task_control_delivery_runs_jarvis_pytest_and_commits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Preuve C1: demande -> plan approuvé -> édition -> gate/commit/rapport."""

    import config
    import database
    from agents.devagent.finalizer import process_engineering_finalizers_once
    from jarvis.agentic import AgenticService, AgenticRunStatus
    from jarvis.agentic.models import RuntimePluginManifest
    from jarvis.event_bus import EventBus
    from jarvis.task_control.detection import TaskCandidateDetector
    from jarvis.task_control.models import (
        PlanDecision,
        PlanStep,
        TaskPlan,
        TaskStatus,
        new_id,
    )
    from jarvis.task_control.service import TaskControlService

    database_path = tmp_path / "task-control-real.db"
    monkeypatch.setattr(config, "DB_PATH", str(database_path))
    monkeypatch.setattr(database, "DB_PATH", database_path)
    database.init_db()

    initial = "def add(left: int, right: int) -> int:\n    return left - right\n"
    corrected = "def add(left: int, right: int) -> int:\n    return left + right\n"
    repo = tmp_path / "task-control-repo"
    repo.mkdir()
    source = repo / "calculator.py"
    source.write_text(initial, encoding="utf-8")
    _init_git_worktree(repo, source)
    tests_dir = repo / "tests"
    tests_dir.mkdir()
    test_file = tests_dir / "test_calculator.py"
    test_file.write_text(
        "from calculator import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n",
        encoding="utf-8",
    )
    for command in (
        ("git", "config", "user.name", "JARVIS E2E"),
        ("git", "config", "user.email", "jarvis-e2e@invalid"),
        ("git", "add", "tests/test_calculator.py"),
        ("git", "commit", "--quiet", "-m", "fixture tests"),
    ):
        subprocess.run(
            command,
            cwd=repo,
            check=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )

    manifest = ReleaseManifest.load()
    runtime_root = tmp_path / "task-control-runtime"
    runtime_root.mkdir()
    layout = RuntimeLayout.from_integration_root(runtime_root)
    _install_real_binary(layout)
    capabilities = (
        ToolCapability("workspace.read", "workspace:read"),
        ToolCapability("workspace.edit", "workspace:write"),
        ToolCapability("tests.run", "tests:run"),
    )
    runtime = _real_runtime(
        layout,
        _runtime_settings(),
        manifest,
        capabilities=capabilities,
    )
    plugin_manifest = RuntimePluginManifest(
        runtime_id="opencode",
        name="OpenCode real C1 fixture",
        version=manifest.version,
        entrypoint="unused:fixture",
        root=PLUGIN_ROOT,
        capabilities=capabilities,
    )

    class _Registry:
        manifests = (plugin_manifest,)

        async def get(self, runtime_id: str) -> OpenCodeRuntime | None:
            return runtime if runtime_id == "opencode" else None

        def manifest(self, runtime_id: str) -> RuntimePluginManifest | None:
            return plugin_manifest if runtime_id == "opencode" else None

    class _Notifications:
        def create(self, **_kwargs: Any) -> int:
            return 1

    provider = LoopbackOpenAIProvider()
    provider.start()
    template_path = tmp_path / "task-control-opencode.json"
    _write_provider_template(template_path, provider, edit_permission="allow")
    monkeypatch.setattr(config_settings, "OPENCODE_CONFIG_TEMPLATE", template_path)
    bus = EventBus()

    class _AgenticService(AgenticService):
        async def create_and_start(self, **kwargs: Any):
            workspace = Path(kwargs["workspace"])
            provider.register_file_scenario(
                "CODING_E2E",
                workspace / "calculator.py",
                initial=initial,
                corrected=corrected,
            )
            return await super().create_and_start(**kwargs)

    agentic = _AgenticService(
        registry=cast(Any, _Registry()),
        bus=bus,
        notifications=cast(Any, _Notifications()),
    )

    async def planner(task, *, version: int, context=None) -> TaskPlan:
        return TaskPlan(
            plan_id=new_id("plan"),
            task_id=task.task_id,
            version=version,
            objective=task.description,
            summary="Corriger le calcul puis laisser JARVIS valider et committer.",
            steps=(PlanStep(index=1, title="Corriger calculator.add"),),
            success_criteria=("calculator.add(2, 3) retourne 5",),
        )

    control = TaskControlService(
        agentic_service=agentic,
        notifications=cast(Any, _Notifications()),
        bus=bus,
        planner=planner,
        detector=TaskCandidateDetector(),
    )
    control.bind_runtime_events()

    async def wait_until(predicate, *, timeout: float = 60.0) -> bool:
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            if predicate():
                return True
            await asyncio.sleep(0.05)
        return predicate()

    task = await control.create_engineering_task(
        title="Corriger calculator.add",
        user_request="CODING_E2E Corrige la soustraction en addition.",
        repo_root=repo,
        required_tests=("python3 -m pytest tests/test_calculator.py -q",),
        acceptance_criteria=("calculator.add(2, 3) retourne 5",),
        idempotency_key="real-task-control-opencode-delivery",
        runtime_id="opencode",
        runtime_version=manifest.version,
    )
    plan = control.repository.get_plan(task.task_id, 1)
    assert plan is not None
    assert f"opencode@{manifest.version}" in plan.tools_expected
    assert task.status is TaskStatus.AWAITING_PLAN_APPROVAL
    assert not (repo / ".jarvis").exists()
    assert provider.trace.snapshot() == ()

    try:
        task = await control.decide_plan(
            task.task_id,
            1,
            decision=PlanDecision.APPROVED,
            actor="session:e2e",
        )
        assert task.agentic_run_id is not None
        run_id = task.agentic_run_id
        reached_review = await wait_until(
            lambda: agentic.get(run_id).status
            in {
                AgenticRunStatus.REVIEWING,
                AgenticRunStatus.FAILED,
                AgenticRunStatus.BLOCKED,
            }
        )
        assert reached_review is True
        assert agentic.get(run_id).status is AgenticRunStatus.REVIEWING

        finalized = await process_engineering_finalizers_once(service=agentic)
        assert len(finalized) == 1
        assert finalized[0]["ok"] is True, json.dumps(
            finalized[0], ensure_ascii=False, sort_keys=True
        )
        assert finalized[0]["status"] == "committed"
        assert len(finalized[0]["validations"]) == 1
        validation = finalized[0]["validations"][0]
        assert validation["command"] == [
            "python3",
            "-m",
            "pytest",
            "tests/test_calculator.py",
            "-q",
        ]
        assert validation["returncode"] == 0
        assert "1 passed" in validation["stdout"]

        task_completed = await wait_until(
            lambda: control.repository.require_task(task.task_id).status
            is TaskStatus.COMPLETED,
            timeout=10,
        )
        assert task_completed is True
        completed = control.repository.require_task(task.task_id)
        report = control.repository.latest_report(task.task_id)
        assert completed.final_report_id is not None
        assert report is not None
        assert report.result_status == "completed"
        assert {
            "changed_file",
            "jarvis_test_receipt",
            "jarvis_effect_receipt",
        } <= {item["type"] for item in report.data["deliveries"]}
        assert "jarvis://receipts/" in report.markdown

        workspace = Path(finalized[0]["worktree_path"])
        assert (workspace / "calculator.py").read_text(encoding="utf-8") == corrected
        head_message = subprocess.run(
            ("git", "log", "-1", "--pretty=%s"),
            cwd=workspace,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        assert head_message == "Corriger calculator.add"
        assert subprocess.run(
            ("git", "status", "--porcelain"),
            cwd=workspace,
            check=True,
            capture_output=True,
            text=True,
        ).stdout == ""

        artifacts = agentic.artifacts(run_id)
        assert any(item.type == "changed_file" for item in artifacts)
        assert any(item.type == "jarvis_test_receipt" for item in artifacts)
        assert any(item.type == "jarvis_effect_receipt" for item in artifacts)
        tool_events = [
            event
            for event in agentic.events(run_id)
            if event.type.startswith("agent.tool.")
        ]
        assert any(
            str(event.payload.get("tool") or "").endswith("edit")
            for event in tool_events
        )
        assert not any(
            "pytest"
            in json.dumps(dict(event.payload), sort_keys=True, default=str).casefold()
            for event in tool_events
        )
        assert {item.get("scenario") for item in provider.trace.snapshot()} == {
            "CODING_E2E"
        }
    finally:
        try:
            await runtime.dispose()
        finally:
            provider.close()
