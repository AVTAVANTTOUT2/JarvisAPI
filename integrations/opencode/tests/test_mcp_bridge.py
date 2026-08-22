"""Tests de la frontière de confiance du broker MCP privé."""

from __future__ import annotations

from contextlib import contextmanager
import json
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Mapping

import pytest

from integrations.opencode.mcp import capabilities as capability_module
from integrations.opencode.mcp import approvals as approval_module
from integrations.opencode.mcp import idempotency as idempotency_module
from integrations.opencode.mcp import server as server_module
from integrations.opencode.mcp.capabilities import CapabilityEnvelope, CapabilityError
from integrations.opencode.mcp.idempotency import IdempotencyJournal
from integrations.opencode.mcp.registry import (
    KNOWLEDGE_SOURCE_TYPES_BY_SCOPE,
    ToolRegistry,
)
from integrations.opencode.mcp.server import BrokerEndpoint, MCPBroker, MCPServer

PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    workspace.mkdir(mode=0o700)
    return workspace


def _capability(
    tmp_path: Path,
    *,
    scopes: tuple[str, ...] = ("tasks:read",),
    audience: str = "jarvis-opencode-mcp",
) -> CapabilityEnvelope:
    return CapabilityEnvelope.issue(
        run_id="run-1",
        profile_id="default",
        scopes=scopes,
        workspace=_workspace(tmp_path),
        audience=audience,
    )


def _connect(endpoint: BrokerEndpoint) -> socket.socket:
    if endpoint.transport == "unix":
        assert endpoint.socket_path is not None
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        connection.connect(os.fspath(endpoint.socket_path))
        return connection
    assert endpoint.host == "127.0.0.1"
    assert endpoint.port is not None
    return socket.create_connection((endpoint.host, endpoint.port), timeout=2.0)


def _broker_request(
    endpoint: BrokerEndpoint,
    message: Mapping[str, Any],
    *,
    token: str | None = None,
) -> dict[str, Any]:
    with _connect(endpoint) as connection:
        connection.sendall(((token or endpoint.token) + "\n").encode("ascii"))
        connection.sendall(
            json.dumps(message, separators=(",", ":")).encode("utf-8") + b"\n"
        )
        stream = connection.makefile("rb")
        response = stream.readline()
    return json.loads(response)


def _process_argv_listing(pid: int) -> str:
    """Lit l'argv complet d'un processus sans dépendre de la largeur de `ps`.

    Sous Linux CI, `ps -o command=` tronque souvent autour de ``COLUMNS`` (ex.
    80) et coupe ``--bootstrap-socket`` au milieu. ``/proc/<pid>/cmdline`` et
    ``ps -ww`` exposent la ligne complète ; le token ne doit toujours pas y
    apparaître.
    """
    if pid <= 0:
        raise ValueError(f"pid invalide pour lecture argv: {pid}")
    proc_cmdline = Path(f"/proc/{pid}/cmdline")
    if proc_cmdline.is_file():
        raw = proc_cmdline.read_bytes()
        if raw:
            return raw.replace(b"\x00", b" ").decode("utf-8", errors="replace")
    result = subprocess.run(
        ["ps", "-ww", "-o", "args=", "-p", str(pid)],
        check=True,
        capture_output=True,
        text=True,
        timeout=2.0,
        env={**os.environ, "COLUMNS": "512"},
    )
    return result.stdout


def test_capability_file_rejects_same_uid_forgery(tmp_path: Path) -> None:
    key = os.urandom(32)
    parent = tmp_path / "capabilities"
    parent.mkdir(mode=0o700)
    path = _capability(tmp_path).write_private(parent / "run-1.json", integrity_key=key)
    document = json.loads(path.read_text(encoding="utf-8"))
    document["envelope"]["scopes"].append("tasks:write")
    path.write_text(json.dumps(document), encoding="utf-8")
    path.chmod(0o600)

    with pytest.raises(CapabilityError, match="capability_integrity_invalid"):
        CapabilityEnvelope.load_private(path, integrity_key=key)


def test_capability_file_rejects_attacker_key_and_wrong_bindings(
    tmp_path: Path,
) -> None:
    trusted_key = os.urandom(32)
    attacker_key = os.urandom(32)
    parent = tmp_path / "capabilities"
    parent.mkdir(mode=0o700)
    path = _capability(tmp_path).write_private(
        parent / "run-1.json", integrity_key=attacker_key
    )

    with pytest.raises(CapabilityError, match="capability_integrity_invalid"):
        CapabilityEnvelope.load_private(path, integrity_key=trusted_key)
    with pytest.raises(CapabilityError, match="capability_run_mismatch"):
        CapabilityEnvelope.load_private(
            path,
            integrity_key=attacker_key,
            expected_run_id="run-other",
        )


def test_capability_validates_audience_expiration_and_workspace_identity(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    capability = CapabilityEnvelope.issue(
        run_id="run-1",
        profile_id="default",
        scopes=("tasks:read",),
        workspace=workspace,
        now=1_000,
        ttl_seconds=60,
    )

    with pytest.raises(CapabilityError, match="capability_audience_mismatch"):
        capability.validate(expected_audience="other", now=1_001)
    with pytest.raises(CapabilityError, match="capability_expired"):
        capability.validate(now=1_060)

    original = tmp_path / "workspace-original"
    workspace.rename(original)
    workspace.mkdir(mode=0o700)
    with pytest.raises(CapabilityError, match="capability_workspace_replaced"):
        capability.validate(now=1_001)


def test_private_capability_rejects_bad_owner_mode_and_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    key = os.urandom(32)
    parent = tmp_path / "capabilities"
    parent.mkdir(mode=0o700)
    path = _capability(tmp_path).write_private(parent / "run-1.json", integrity_key=key)
    path.chmod(0o644)
    with pytest.raises(CapabilityError, match="capability_file_permissions"):
        CapabilityEnvelope.load_private(path, integrity_key=key)
    path.chmod(0o600)

    link = parent / "forged.json"
    link.symlink_to(path)
    with pytest.raises(CapabilityError):
        CapabilityEnvelope.load_private(link, integrity_key=key)

    actual_uid = path.stat().st_uid
    monkeypatch.setattr(
        capability_module.os, "getuid", lambda: actual_uid + 1, raising=False
    )
    with pytest.raises(CapabilityError, match="capability_(parent|file)_owner"):
        CapabilityEnvelope.load_private(path, integrity_key=key)


def test_private_idempotency_journal_rejects_owner_mode_and_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "journal.json"
    path.write_text(json.dumps({"version": 1, "records": {}}), encoding="utf-8")
    path.chmod(0o644)
    with pytest.raises(CapabilityError, match="idempotency_file_permissions"):
        IdempotencyJournal(path)
    path.chmod(0o600)

    link = tmp_path / "journal-link.json"
    link.symlink_to(path)
    with pytest.raises(CapabilityError):
        IdempotencyJournal(link)

    actual_uid = path.stat().st_uid
    monkeypatch.setattr(
        idempotency_module.os, "getuid", lambda: actual_uid + 1, raising=False
    )
    with pytest.raises(
        CapabilityError, match="idempotency_(parent|lock_file|file)_owner"
    ):
        IdempotencyJournal(path)


def test_idempotency_reserves_before_effect_and_replays_completed_result(
    tmp_path: Path,
) -> None:
    journal = IdempotencyJournal(tmp_path / "journal.json")
    observed_state: list[str] = []

    def operation() -> dict[str, Any]:
        observed_state.append(journal.inspect()["call-1"]["state"])
        return {"task_id": 42}

    first, replayed = journal.execute(
        key="call-1", payload={"title": "A"}, operation=operation
    )
    second, replayed_second = journal.execute(
        key="call-1",
        payload={"title": "A"},
        operation=lambda: pytest.fail("un effet complété ne doit pas être rejoué"),
    )

    assert observed_state == ["pending"]
    assert first == second == {"task_id": 42}
    assert replayed is False
    assert replayed_second is True
    assert journal.inspect()["call-1"]["state"] == "completed"


def test_idempotency_crash_after_effect_fails_closed_until_explicit_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "journal.json"
    journal = IdempotencyJournal(path)
    original_persist = journal._persist_unlocked
    persist_calls = 0
    effect_calls = 0

    def crash_on_completion(records: Mapping[str, Mapping[str, Any]]) -> None:
        nonlocal persist_calls
        persist_calls += 1
        if persist_calls == 2:
            raise OSError("simulated crash after external effect")
        original_persist(records)

    def operation() -> dict[str, Any]:
        nonlocal effect_calls
        effect_calls += 1
        return {"task_id": 7}

    monkeypatch.setattr(journal, "_persist_unlocked", crash_on_completion)
    with pytest.raises(OSError, match="simulated crash"):
        journal.execute(key="call-1", payload={"title": "A"}, operation=operation)

    restarted = IdempotencyJournal(path)
    with pytest.raises(CapabilityError, match="idempotency_operation_pending"):
        restarted.execute(key="call-1", payload={"title": "A"}, operation=operation)
    assert effect_calls == 1

    restarted.recover_pending(
        key="call-1",
        payload={"title": "A"},
        result={"task_id": 7},
    )
    result, replayed = restarted.execute(
        key="call-1",
        payload={"title": "A"},
        operation=operation,
    )
    assert result == {"task_id": 7}
    assert replayed is True
    assert effect_calls == 1
    assert restarted.inspect()["call-1"]["recovered"] is True


def test_idempotency_concurrent_duplicate_never_runs_twice(tmp_path: Path) -> None:
    path = tmp_path / "journal.json"
    first = IdempotencyJournal(path)
    second = IdempotencyJournal(path)
    entered = threading.Event()
    release = threading.Event()
    outcomes: list[object] = []
    effect_calls = 0

    def slow_effect() -> dict[str, Any]:
        nonlocal effect_calls
        effect_calls += 1
        entered.set()
        assert release.wait(timeout=2.0)
        return {"task_id": 9}

    def run_first() -> None:
        try:
            outcomes.append(
                first.execute(
                    key="call-1", payload={"title": "A"}, operation=slow_effect
                )
            )
        except Exception as exc:  # pragma: no cover - assertion détaillée ci-dessous
            outcomes.append(exc)

    thread = threading.Thread(target=run_first)
    thread.start()
    assert entered.wait(timeout=2.0)
    with pytest.raises(CapabilityError, match="idempotency_operation_pending"):
        second.execute(
            key="call-1",
            payload={"title": "A"},
            operation=lambda: pytest.fail(
                "l'appel concurrent ne doit pas produire d'effet"
            ),
        )
    release.set()
    thread.join(timeout=2.0)

    assert not thread.is_alive()
    assert effect_calls == 1
    assert outcomes == [({"task_id": 9}, False)]


def test_idempotency_lock_rejects_symlink_and_permissive_mode(tmp_path: Path) -> None:
    path = tmp_path / "journal.json"
    lock_path = path.with_suffix(".json.lock")
    target = tmp_path / "attacker-lock"
    target.write_text("", encoding="utf-8")
    target.chmod(0o600)
    lock_path.symlink_to(target)
    with pytest.raises(CapabilityError, match="idempotency_lock_open_failed"):
        IdempotencyJournal(path)

    lock_path.unlink()
    lock_path.write_text("", encoding="utf-8")
    lock_path.chmod(0o644)
    with pytest.raises(CapabilityError, match="idempotency_lock_file_permissions"):
        IdempotencyJournal(path)


def test_tool_schema_and_arguments_never_delegate_authority_to_model(
    tmp_path: Path,
) -> None:
    capability = _capability(tmp_path)
    registry = ToolRegistry(
        capability, journal=IdempotencyJournal(tmp_path / "journal.json")
    )

    tools = registry.list_tools()
    names = [tool["name"] for tool in tools]
    assert "jarvis_tasks_list" in names
    assert "jarvis_tasks_create" in names
    listed = next(tool for tool in tools if tool["name"] == "jarvis_tasks_list")
    assert listed["inputSchema"]["additionalProperties"] is False
    assert "_jarvis" not in listed["inputSchema"]["properties"]
    assert listed["annotations"] == {
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    }

    trusted = {
        "run_id": capability.run_id,
        "tool_call_id": "mcp:trusted",
        "origin": "agent_runtime",
        "bypass_agentic_reclassification": True,
    }
    with pytest.raises(CapabilityError, match="tool_approval_required"):
        registry.call(
            "jarvis_tasks_create",
            {"title": "A", "idempotency_key": "call-1", "_jarvis": trusted},
        )

    with pytest.raises(ValueError, match="tool_arguments_invalid"):
        registry.call(
            "jarvis_tasks_list",
            {"status": "todo", "unexpected": True, "_jarvis": trusted},
        )


def test_knowledge_tools_scope_profile_and_uid_hydration_are_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import database
    import jarvis.retrieval as retrieval_module

    capability = _capability(tmp_path, scopes=("communications:read",))
    registry = ToolRegistry(
        capability, journal=IdempotencyJournal(tmp_path / "knowledge-journal.json")
    )
    tools = {tool["name"]: tool for tool in registry.list_tools()}
    assert {"jarvis_knowledge_search", "jarvis_knowledge_get"}.issubset(tools)
    for name in ("jarvis_knowledge_search", "jarvis_knowledge_get"):
        schema = tools[name]
        assert "profile_id" not in schema["inputSchema"]["properties"]
        assert schema["inputSchema"]["additionalProperties"] is False
        assert schema["annotations"]["readOnlyHint"] is True

    trusted = {
        "run_id": capability.run_id,
        "tool_call_id": "mcp:knowledge",
        "origin": "agent_runtime",
        "bypass_agentic_reclassification": True,
    }
    with pytest.raises(CapabilityError, match="knowledge_uid_not_authorized"):
        registry.call(
            "jarvis_knowledge_get",
            {"uid": "email:1", "_jarvis": trusted},
        )
    with pytest.raises(CapabilityError, match="knowledge_source_scope_denied"):
        registry.call(
            "jarvis_knowledge_search",
            {
                "query": "demain",
                "source_types": ["calendar"],
                "_jarvis": trusted,
            },
        )
    with pytest.raises(ValueError, match="tool_arguments_invalid"):
        registry.call(
            "jarvis_knowledge_search",
            {
                "query": "Grégoire",
                "profile_id": "other",
                "_jarvis": trusted,
            },
        )

    active_profiles: list[str] = []
    captured_requests: list[Any] = []

    @contextmanager
    def recording_profile(profile_id: str):
        active_profiles.append(profile_id)
        try:
            yield
        finally:
            active_profiles.pop()

    class FakeRequest:
        def __init__(self, **kwargs: Any) -> None:
            vars(self).update(kwargs)

    class FakeHit:
        def __init__(
            self,
            uid: str,
            source_type: str,
            content: str,
            *,
            cloud_policy: str = "redact",
        ) -> None:
            self.uid = uid
            self.source_type = source_type
            self.source_id = uid
            self.content = content
            self.cloud_policy = cloud_policy
            self.metadata = {"content_completeness": "complete"}

        def as_dict(self) -> dict[str, Any]:
            return {
                "uid": self.uid,
                "source_type": self.source_type,
                "source_id": self.source_id,
                "title": "Résultat",
                "excerpt": self.content[:40],
                "content": self.content,
                "metadata": {"private": self.content},
                "cloud_policy": self.cloud_policy,
            }

    class FakeResult:
        def __init__(self, hits: tuple[FakeHit, ...]) -> None:
            self.hits = hits

        def as_dict(self) -> dict[str, Any]:
            return {
                "status": "degraded",
                "query": "hostile replacement",
                "hits": [hit.as_dict() for hit in self.hits],
                "candidate_count": 99,
                "verified_sources": ["email", "calendar"],
                "unavailable_sources": ["imessage", "calendar"],
                "source_coverage": [
                    {"source_type": "email", "status": "partial"},
                    {"source_type": "imessage", "status": "unavailable"},
                ],
                "index_freshness_at": None,
                "diagnostics": {},
            }

    sensitive = (
        "Bearer sk-test-secret-123456 gregoire@example.test "
        "+33612345678 /Users/alice/private "
    )

    def fake_search(request: Any) -> FakeResult:
        assert active_profiles == [capability.profile_id]
        captured_requests.append(request)
        return FakeResult(
            (
                FakeHit("email:1", "email", sensitive + "Message complet"),
                FakeHit(
                    "email:local",
                    "email",
                    "Secret strictement local",
                    cloud_policy="local_only",
                ),
                FakeHit("calendar:forged", "calendar", "Source hors scope"),
            )
        )

    def fake_get(uid: str, *, max_chars: int = 12_000) -> FakeHit:
        assert active_profiles == [capability.profile_id]
        assert uid == "email:1"
        assert max_chars == 12_000
        return FakeHit(uid, "email", sensitive + ("M" * 5_000))

    monkeypatch.setattr(database, "use_profile", recording_profile)
    monkeypatch.setattr(
        retrieval_module, "RetrievalRequest", FakeRequest, raising=False
    )
    monkeypatch.setattr(
        retrieval_module, "search_knowledge", fake_search, raising=False
    )
    monkeypatch.setattr(retrieval_module, "get_knowledge_item", fake_get, raising=False)

    search_response = registry.call(
        "jarvis_knowledge_search",
        {"query": "mail de Grégoire", "_jarvis": trusted},
    )
    assert set(captured_requests[0].source_types) == {
        "email",
        "imessage",
        "notification",
    }
    assert captured_requests[0].interaction_mode == "agentic"
    assert search_response["data"]["query"] == "mail de Grégoire"
    assert search_response["data"]["status"] == "degraded"
    assert "imessage" in search_response["data"]["unavailable_sources"]
    assert search_response["data"]["live_sources"] == {
        "email": "partial",
        "imessage": "unavailable",
    }
    assert search_response["data"]["candidate_count"] == 2
    assert [hit["uid"] for hit in search_response["data"]["hits"]] == [
        "email:1",
        "email:local",
    ]
    assert "content" not in search_response["data"]["hits"][0]
    search_serialized = json.dumps(search_response["data"], ensure_ascii=False)
    assert "sk-test-secret-123456" not in search_serialized
    assert "gregoire@example.test" not in search_serialized
    assert "+33612345678" not in search_serialized
    assert "/Users/alice/private" not in search_serialized
    assert search_response["data"]["verified_sources"] == ["email"]
    assert search_response["data"]["hits"][1] == {
        "uid": "email:local",
        "source_type": "email",
        "source_id": "email:local",
        "local_only": True,
    }
    with pytest.raises(CapabilityError, match="knowledge_local_only"):
        registry.call(
            "jarvis_knowledge_get",
            {
                "uid": "email:local",
                "_jarvis": {**trusted, "tool_call_id": "mcp:get-local"},
            },
        )

    get_response = registry.call(
        "jarvis_knowledge_get",
        {"uid": "email:1", "_jarvis": {**trusted, "tool_call_id": "mcp:get"}},
    )
    get_serialized = json.dumps(get_response["data"], ensure_ascii=False)
    assert "sk-test-secret-123456" not in get_serialized
    assert "gregoire@example.test" not in get_serialized
    assert "+33612345678" not in get_serialized
    assert "/Users/alice/private" not in get_serialized
    assert "[LOCAL_HOME]" in get_serialized

    monkeypatch.setattr(
        retrieval_module,
        "get_knowledge_item",
        lambda _uid, *, max_chars=12_000: FakeHit(
            "email:1",
            "email",
            "Policy changed after search",
            cloud_policy="local_only",
        ),
    )
    with pytest.raises(CapabilityError, match="knowledge_local_only"):
        registry.call(
            "jarvis_knowledge_get",
            {
                "uid": "email:1",
                "_jarvis": {**trusted, "tool_call_id": "mcp:get-policy-change"},
            },
        )

    second_registry = ToolRegistry(
        capability,
        journal=IdempotencyJournal(tmp_path / "second-knowledge-journal.json"),
    )
    with pytest.raises(CapabilityError, match="knowledge_uid_not_authorized"):
        second_registry.call(
            "jarvis_knowledge_get",
            {"uid": "email:1", "_jarvis": trusted},
        )


def test_research_scope_alone_exposes_no_personal_knowledge_tool(
    tmp_path: Path,
) -> None:
    registry = ToolRegistry(
        _capability(tmp_path, scopes=("research:search",)),
        journal=IdempotencyJournal(tmp_path / "research-journal.json"),
    )

    names = {tool["name"] for tool in registry.list_tools()}
    assert "jarvis_knowledge_search" not in names
    assert "jarvis_knowledge_get" not in names
    with pytest.raises(CapabilityError, match="capability_scope_denied"):
        registry.call(
            "jarvis_knowledge_search",
            {
                "query": "private data",
                "_jarvis": {
                    "run_id": registry.capability.run_id,
                    "tool_call_id": "mcp:research-only",
                    "origin": "agent_runtime",
                    "bypass_agentic_reclassification": True,
                },
            },
        )


def test_browser_scope_exposes_jarvis_browser_and_starts_the_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from integrations.browser import BrowserElement, close_session, set_driver_factory

    class _Driver:
        url = ""
        elements = [BrowserElement("e1", "link", "Hotel Casa", 0)]

        async def open(self, url: str) -> None:
            self.url = url

        async def observe(self):
            return self.url, "Hotels", "Casa 120 EUR", list(self.elements)

        async def click(self, element: BrowserElement) -> None:
            return None

        async def fill(self, element: BrowserElement, text: str) -> None:
            return None

        async def press(self, element: BrowserElement, key: str) -> None:
            return None

        async def close(self) -> None:
            return None

    set_driver_factory(lambda: _Driver())
    monkeypatch.setattr(
        "integrations.browser.validate_browser_target",
        lambda url, **_kwargs: url,
    )
    registry = ToolRegistry(
        _capability(tmp_path, scopes=("browser:control",)),
        journal=IdempotencyJournal(tmp_path / "browser-journal.json"),
    )
    names = {tool["name"] for tool in registry.list_tools()}
    assert "jarvis_browser" in names
    assert "jarvis_knowledge_search" not in names

    trusted = {
        "run_id": registry.capability.run_id,
        "tool_call_id": "mcp:browser-open",
        "origin": "agent_runtime",
        "bypass_agentic_reclassification": True,
    }
    result = registry.call(
        "jarvis_browser",
        {"op": "open", "url": "https://hotels.example/search", "_jarvis": trusted},
    )
    assert result["ok"] is True
    assert result["data"]["started"] is True
    assert result["data"]["url"] == "https://hotels.example/search"
    close_session(registry.capability.run_id)
    set_driver_factory(None)

    denied_root = tmp_path / "denied"
    denied_root.mkdir()
    readonly = ToolRegistry(
        _capability(denied_root, scopes=("research:search",)),
        journal=IdempotencyJournal(tmp_path / "browser-denied.json"),
    )
    assert "jarvis_browser" not in {tool["name"] for tool in readonly.list_tools()}
    with pytest.raises(CapabilityError, match="capability_scope_denied"):
        readonly.call(
            "jarvis_browser",
            {
                "op": "see",
                "_jarvis": {
                    "run_id": readonly.capability.run_id,
                    "tool_call_id": "mcp:browser-denied",
                    "origin": "agent_runtime",
                    "bypass_agentic_reclassification": True,
                },
            },
        )


def test_knowledge_source_types_are_partitioned_by_exact_read_scope() -> None:
    from jarvis.retrieval.models import CANONICAL_SOURCE_TYPES

    project_sources = {
        "project",
        "agent_run",
        "agent_step",
        "agent_approval",
        "agent_artifact",
        "agentic_workflow",
        "cursor_job",
        "scheduler_job",
        "work_session",
    }
    expected = {
        "communications:read": {"email", "imessage", "notification"},
        "calendar:read": {"calendar"},
        "conversations:read": {"conversation", "message"},
        "memory:read": {
            "episode",
            "note",
            "journal",
            "fact",
            "life_context",
            "pattern",
            "insight",
            "briefing",
            "commitment",
            "location",
            "wellbeing",
            "activity",
        },
        "contacts:read": {
            "person",
            "people_event",
            "relationship",
            "relationship_event",
        },
        "media:read": {"recording", "conversation_turn"},
        "documents:read": {"school_document", "conversation_document"},
        "documentation:read": {"school_document", "conversation_document"},
        "tasks:read": {
            "task",
            "control_task",
            "control_plan",
            "control_comment",
            "control_report",
            "control_activity",
        },
        "project_state:read": project_sources,
        "workspace:read": project_sources,
    }
    assert {
        scope: set(source_types)
        for scope, source_types in KNOWLEDGE_SOURCE_TYPES_BY_SCOPE.items()
    } == expected
    assert "research:search" not in KNOWLEDGE_SOURCE_TYPES_BY_SCOPE
    scoped_source_types = {
        source_type
        for source_types in KNOWLEDGE_SOURCE_TYPES_BY_SCOPE.values()
        for source_type in source_types
    }
    assert scoped_source_types.issubset(CANONICAL_SOURCE_TYPES)


def test_tasks_write_never_mutates_without_explicit_jarvis_approval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import database

    write_capability = CapabilityEnvelope.issue(
        run_id="run-write",
        profile_id="default",
        scopes=("tasks:write",),
        workspace=_workspace(tmp_path),
    )
    write_registry = ToolRegistry(
        write_capability,
        journal=IdempotencyJournal(tmp_path / "write-journal.json"),
    )
    mutated = False

    def forbidden_mutation(**_kwargs: Any) -> int:
        nonlocal mutated
        mutated = True
        return 1

    monkeypatch.setattr(database, "create_task", forbidden_mutation)
    seen: list[Mapping[str, Any]] = []
    write_registry.bind_approval_callback(lambda payload: seen.append(payload))
    assert "jarvis_tasks_create" in {
        tool["name"] for tool in write_registry.list_tools()
    }
    with pytest.raises(CapabilityError, match="tool_approval_required"):
        write_registry.call(
            "jarvis_tasks_create",
            {
                "title": "A",
                "idempotency_key": "call-1",
                "_jarvis": {
                    "run_id": write_capability.run_id,
                    "tool_call_id": "mcp:trusted-write",
                    "origin": "agent_runtime",
                    "bypass_agentic_reclassification": True,
                },
            },
        )
    assert mutated is False
    assert seen and str(seen[0]["approval_id"]).startswith("mcp:")
    assert seen[0]["tool"] == "jarvis_tasks_create"
    assert write_registry.journal.inspect() == {}


def test_parent_approval_is_exact_one_effect_and_idempotent_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import database

    capability = _capability(tmp_path, scopes=("tasks:write",))
    state = tmp_path / "state"
    broker = MCPBroker(
        capability,
        journal_path=state / "journal.json",
        ipc_directory=state,
    )
    endpoint = broker.start()
    arguments = {"title": "Autorisé", "idempotency_key": "approved-call-1"}
    mutations: list[str] = []

    def create_task(**kwargs: Any) -> int:
        mutations.append(str(kwargs["title"]))
        return 41

    monkeypatch.setattr(database, "create_task", create_task)
    monkeypatch.setattr(database, "get_task", lambda task_id: {"id": task_id})

    def call(call_arguments: Mapping[str, Any], request_id: str) -> dict[str, Any]:
        return _broker_request(
            endpoint,
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "tools/call",
                "params": {
                    "name": "jarvis_tasks_create",
                    "arguments": dict(call_arguments),
                },
            },
        )

    try:
        denied = call(arguments, "before-approval")
        assert (
            denied["result"]["structuredContent"]["error"] == "tool_approval_required"
        )
        assert mutations == []
        assert broker.journal.inspect() == {}

        forged = call({**arguments, "approval_id": "parent-approval"}, "forged")
        assert (
            forged["result"]["structuredContent"]["error"] == "tool_approval_required"
        )
        assert mutations == []

        with pytest.raises(CapabilityError, match="approval_run_mismatch"):
            broker.grant_approval(
                approval_id="parent-approval",
                run_id="another-run",
                tool_name="jarvis_tasks_create",
                arguments=arguments,
                expires_at=time.time() + 60,
            )
        broker.grant_approval(
            approval_id="parent-approval",
            run_id=capability.run_id,
            tool_name="jarvis_tasks_create",
            arguments=arguments,
            expires_at=time.time() + 60,
        )
        listed = _broker_request(
            endpoint,
            {"jsonrpc": "2.0", "id": "list", "method": "tools/list", "params": {}},
        )
        assert [tool["name"] for tool in listed["result"]["tools"]] == [
            "jarvis_tasks_create"
        ]

        modified = call({**arguments, "title": "Altéré"}, "modified")
        assert modified["result"]["structuredContent"]["error"] == (
            "tool_approval_arguments_mismatch"
        )
        assert mutations == []

        first = call(arguments, "effect-1")
        replay = call(arguments, "effect-replay")
        assert (
            first["result"]["structuredContent"]["data"]["idempotent_replay"] is False
        )
        assert (
            replay["result"]["structuredContent"]["error"] == "tool_approval_consumed"
        )
        assert mutations == ["Autorisé"]
        assert "jarvis_tasks_create" in {
            tool["name"] for tool in broker.registry.list_tools()
        }

        assert broker.revoke_approval(
            approval_id="parent-approval", run_id=capability.run_id
        )
        revoked = call(arguments, "revoked")
        assert (
            revoked["result"]["structuredContent"]["error"] == "tool_approval_required"
        )
        assert mutations == ["Autorisé"]
    finally:
        broker.stop()
    assert broker.registry.list_tools() == []


def test_completed_approval_refuses_local_replay_without_reentering_operation(
    tmp_path: Path,
) -> None:
    capability = _capability(tmp_path, scopes=("tasks:write",))
    ledger = approval_module.ApprovalLedger(capability)
    arguments = {"title": "Autorisé", "idempotency_key": "local-once"}
    calls = 0

    def operation() -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return {"created": True}

    ledger.grant(
        approval_id="local-approval",
        run_id=capability.run_id,
        tool_name="jarvis_tasks_create",
        arguments=arguments,
        expires_at=time.time() + 60,
    )
    assert ledger.execute(
        tool_name="jarvis_tasks_create",
        arguments=arguments,
        operation=operation,
    ) == {"created": True}

    with pytest.raises(CapabilityError, match="tool_approval_consumed"):
        ledger.execute(
            tool_name="jarvis_tasks_create",
            arguments=arguments,
            operation=operation,
        )
    assert calls == 1


def test_process_identity_ignores_ambient_path_ps_hijack(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    marker = tmp_path / "fake-ps-ran"
    fake_ps = fake_bin / "ps"
    fake_ps.write_text(
        f"#!/bin/sh\ntouch {marker}\nprintf '1 0\\n'\n", encoding="utf-8"
    )
    fake_ps.chmod(0o755)
    monkeypatch.setenv("PATH", str(fake_bin))
    observed: dict[str, Any] = {}

    def fake_run(argv, **kwargs):
        observed["argv"] = tuple(argv)
        observed["env"] = dict(kwargs["env"])
        return subprocess.CompletedProcess(
            argv, 0, f"{os.getppid()} {os.getuid()}\n", ""
        )

    monkeypatch.setattr(server_module.subprocess, "run", fake_run)

    parent_pid, uid = server_module._process_parent_and_uid(os.getpid())

    assert parent_pid > 1
    assert uid == os.getuid()
    assert Path(observed["argv"][0]).is_absolute()
    assert observed["argv"][0] != str(fake_ps)
    assert observed["env"]["PATH"] == "/usr/bin:/bin"
    assert not marker.exists()


def test_effectful_approval_expiry_and_crash_ambiguity_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import database

    capability = _capability(tmp_path, scopes=("tasks:write",))
    state = tmp_path / "state"
    broker = MCPBroker(
        capability,
        journal_path=state / "journal.json",
        ipc_directory=state,
    )
    endpoint = broker.start()
    expired_arguments = {"title": "Expiré", "idempotency_key": "expired-call"}
    clock = capability.issued_at + 1
    monkeypatch.setattr(approval_module.time, "time", lambda: clock)
    try:
        broker.grant_approval(
            approval_id="expires",
            run_id=capability.run_id,
            tool_name="jarvis_tasks_create",
            arguments=expired_arguments,
            expires_at=clock + 1,
        )
        clock += 2
        expired = _broker_request(
            endpoint,
            {
                "jsonrpc": "2.0",
                "id": "expired",
                "method": "tools/call",
                "params": {
                    "name": "jarvis_tasks_create",
                    "arguments": expired_arguments,
                },
            },
        )
        assert (
            expired["result"]["structuredContent"]["error"] == "tool_approval_expired"
        )

        crash_arguments = {"title": "Ambigu", "idempotency_key": "crash-call"}
        broker.grant_approval(
            approval_id="crash",
            run_id=capability.run_id,
            tool_name="jarvis_tasks_create",
            arguments=crash_arguments,
            expires_at=clock + 60,
        )
        mutations = 0

        def crash_after_effect(**_kwargs: Any) -> int:
            nonlocal mutations
            mutations += 1
            raise RuntimeError("simulated crash after side effect")

        monkeypatch.setattr(database, "create_task", crash_after_effect)
        request = {
            "jsonrpc": "2.0",
            "id": "crash-effect",
            "method": "tools/call",
            "params": {
                "name": "jarvis_tasks_create",
                "arguments": crash_arguments,
            },
        }
        first = _broker_request(endpoint, request)
        second = _broker_request(endpoint, request)
        assert first["result"]["structuredContent"]["error"] == "tool_execution_failed"
        assert second["result"]["structuredContent"]["error"] == (
            "approval_effect_in_progress_or_ambiguous"
        )
        assert mutations == 1
        assert broker.journal.inspect()["crash-call"]["state"] == "pending"
    finally:
        broker.stop()

    broker.start()
    try:
        assert broker.registry.list_tools() == []
    finally:
        broker.stop()


class _RecordingRegistry:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def list_tools(self) -> list[dict[str, Any]]:
        return []

    def call(self, name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        self.calls.append((name, dict(arguments)))
        return {"ok": True}


def test_server_injects_stable_trusted_metadata_and_rejects_spoofing(
    tmp_path: Path,
) -> None:
    recorder = _RecordingRegistry()
    server = MCPServer(_capability(tmp_path), registry=recorder)
    request = {
        "jsonrpc": "2.0",
        "id": "tool-call-7",
        "method": "tools/call",
        "params": {"name": "jarvis_tasks_list", "arguments": {"status": "todo"}},
    }

    server.dispatch(request)
    server.dispatch(request)

    assert len(recorder.calls) == 2
    first_metadata = recorder.calls[0][1]["_jarvis"]
    assert first_metadata == recorder.calls[1][1]["_jarvis"]
    assert first_metadata["run_id"] == "run-1"
    assert first_metadata["origin"] == "agent_runtime"
    assert first_metadata["bypass_agentic_reclassification"] is True
    assert first_metadata["tool_call_id"].startswith("mcp:")

    spoofed = {
        **request,
        "id": "tool-call-8",
        "params": {
            "name": "jarvis_tasks_list",
            "arguments": {"_jarvis": {"run_id": "attacker"}},
        },
    }
    response = server.dispatch(spoofed)

    assert len(recorder.calls) == 2
    assert response is not None
    assert response["error"]["message"] == "Reserved tool metadata"


def test_broker_exposes_only_exact_opaque_capability_and_rejects_wrong_bearer(
    tmp_path: Path,
) -> None:
    capability = _capability(tmp_path)
    state = tmp_path / "state"
    broker = MCPBroker(
        capability,
        journal_path=state / "journal.json",
        ipc_directory=state,
    )
    endpoint = broker.start()
    try:
        config = endpoint.opencode_config(repository_root=PROJECT_ROOT)
        serialized = json.dumps(config)
        command = endpoint.proxy_command(python_executable=sys.executable)
        assert endpoint.token not in serialized
        assert endpoint.token not in command
        assert "--token" not in command
        assert "--bootstrap-socket" in command
        assert endpoint.inherited_fds == ()
        assert endpoint.bootstrap_path.parent.stat().st_mode & 0o777 == 0o700
        assert endpoint.bootstrap_path.stat().st_mode & 0o777 == 0o600
        assert capability.run_id not in serialized
        assert capability.profile_id not in serialized
        assert str(capability.workspace) not in serialized
        assert all(scope not in serialized for scope in capability.scopes)

        with _connect(endpoint) as connection:
            connection.settimeout(1.0)
            connection.sendall(b"attacker-token-that-is-long-enough-000000\n")
            assert connection.recv(1) == b""

        response = _broker_request(
            endpoint,
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        )
        assert [tool["name"] for tool in response["result"]["tools"]] == [
            "jarvis_knowledge_search",
            "jarvis_knowledge_get",
            "jarvis_tasks_list",
            "jarvis_tasks_create",
        ]
    finally:
        socket_path = endpoint.socket_path
        broker.stop()
    if socket_path is not None:
        assert not socket_path.exists()


def test_broker_rejects_wrong_audience_before_binding(tmp_path: Path) -> None:
    capability = _capability(tmp_path, audience="attacker-runtime")

    with pytest.raises(CapabilityError, match="capability_audience_mismatch"):
        MCPBroker(capability, journal_path=tmp_path / "journal.json")


def test_broker_fails_closed_without_secure_peer_transport(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    broker = MCPBroker(
        _capability(tmp_path),
        journal_path=tmp_path / "journal.json",
    )
    with monkeypatch.context() as isolated:
        isolated.setattr(server_module.sys, "platform", "win32")
        with pytest.raises(CapabilityError, match="unsupported_secure_peer_transport"):
            broker.start()

    with pytest.raises(CapabilityError, match="mcp_broker_not_started"):
        _ = broker.endpoint
    assert broker.bootstrap_diagnostic()["started"] is False


def test_bootstrap_is_peer_bound_single_claim_and_unlinked(tmp_path: Path) -> None:
    state = tmp_path / "state"
    broker = MCPBroker(
        _capability(tmp_path),
        journal_path=state / "journal.json",
        ipc_directory=state,
    )
    endpoint = broker.start()
    socket_directory = endpoint.bootstrap_path.parent
    broker.bind_server_pid(os.getpid())
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as bootstrap:
            bootstrap.settimeout(2.0)
            bootstrap.connect(os.fspath(endpoint.bootstrap_path))
            token = bootstrap.makefile("rb").readline().decode("ascii").strip()
        assert token == endpoint.token
        assert not endpoint.bootstrap_path.exists()

        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as second:
            with pytest.raises(OSError):
                second.connect(os.fspath(endpoint.bootstrap_path))

        response = _broker_request(
            endpoint,
            {"jsonrpc": "2.0", "id": "claimed", "method": "ping", "params": {}},
            token=token,
        )
        assert response["result"] == {}
    finally:
        broker.stop()
    assert not socket_directory.exists()


def test_bootstrap_rejects_same_uid_peer_outside_bound_process_tree(
    tmp_path: Path,
) -> None:
    owner = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(5)"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    state = tmp_path / "state"
    broker = MCPBroker(
        _capability(tmp_path),
        journal_path=state / "journal.json",
        ipc_directory=state,
    )
    endpoint = broker.start()
    try:
        broker.bind_server_pid(owner.pid)
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as forged:
            forged.settimeout(2.0)
            forged.connect(os.fspath(endpoint.bootstrap_path))
            assert forged.recv(1) == b""
        assert endpoint.bootstrap_path.exists()
    finally:
        broker.stop()
        owner.terminate()
        owner.wait(timeout=2.0)


def test_process_argv_listing_survives_narrow_ps_columns() -> None:
    """Régression CI : un `ps` étroit coupe ``--bootstrap-socket`` au milieu."""
    long_bootstrap = (
        "/tmp/pytest-of-runner/pytest-2/test_stdio_proxy_relays_withou0/"
        "state/sockets/bootstrap.sock"
    )
    long_socket = long_bootstrap.replace("bootstrap.sock", "broker.sock")
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import time; time.sleep(5)",
            "proxy",
            "--transport",
            "unix",
            "--bootstrap-socket",
            long_bootstrap,
            "--socket-path",
            long_socket,
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            try:
                narrow = subprocess.run(
                    ["ps", "-o", "command=", "-p", str(process.pid)],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=2.0,
                    env={**os.environ, "COLUMNS": "80"},
                ).stdout
                break
            except subprocess.CalledProcessError:
                time.sleep(0.01)
        else:
            raise AssertionError("processus de régression argv introuvable dans ps")
        listing = _process_argv_listing(process.pid)
        # La troncature d'un `ps` étroit est une propriété de l'OS, pas un
        # contrat que ce dépôt tient : GNU coreutils la produit, le `ps` BSD de
        # macOS non. L'asserter ferait échouer le travail sur la machine cible
        # pour un comportement qui n'est pas le nôtre. On se contente de
        # constater le cas quand il se présente.
        if "--bootstrap-socket" in narrow:
            assert narrow.strip() != "", "lecture naïve vide, cas non représentatif"
        # Ce qui nous appartient : le lecteur robuste rend l'argv complet, sans
        # jamais exposer le jeton.
        assert "--bootstrap-socket" in listing
        assert long_bootstrap in listing
        assert "secret-token-must-not-leak" not in listing
    finally:
        process.terminate()
        process.wait(timeout=2.0)


def test_stdio_proxy_relays_without_receiving_capability_fields(tmp_path: Path) -> None:
    state = tmp_path / "state"
    broker = MCPBroker(
        _capability(tmp_path),
        journal_path=state / "journal.json",
        ipc_directory=state,
    )
    endpoint = broker.start()
    broker.bind_server_pid(os.getpid())
    request = json.dumps(
        {"jsonrpc": "2.0", "id": "list-1", "method": "tools/list", "params": {}}
    )
    process: subprocess.Popen[str] | None = None
    try:
        process = subprocess.Popen(
            endpoint.proxy_command(python_executable=sys.executable),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=PROJECT_ROOT,
            env={**os.environ, "PYTHONPATH": str(PROJECT_ROOT)},
        )
        deadline = time.monotonic() + 2.0
        while endpoint.bootstrap_path.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not endpoint.bootstrap_path.exists()
        listing = _process_argv_listing(process.pid)
        assert endpoint.token not in listing
        assert "--bootstrap-socket" in listing
        stdout, stderr = process.communicate(request + "\n", timeout=5.0)
        returncode = process.returncode
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            process.wait(timeout=2.0)
        broker.stop()

    assert returncode == 0, stderr
    response = json.loads(stdout)
    assert response["id"] == "list-1"
    assert [tool["name"] for tool in response["result"]["tools"]] == [
        "jarvis_knowledge_search",
        "jarvis_knowledge_get",
        "jarvis_tasks_list",
        "jarvis_tasks_create",
    ]
