"""Fixtures loopback déterministes pour la preuve E2E du vrai binaire.

Ce module n'est jamais chargé par le runtime de production. Il fournit :

* un endpoint OpenAI-compatible HTTP/SSE strictement lié à ``127.0.0.1`` ;
* un petit serveur MCP stdio qui prouve l'appel réel d'un outil.

Aucune requête publique ni clé externe n'est nécessaire.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import threading
import time
from typing import Any, Mapping


FINAL_MARKER = "JARVIS_E2E_FINAL: MCP_ECHO_OK"


def _json_line(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(dict(payload), ensure_ascii=True, sort_keys=True) + "\n"
        )


def _contains_text(value: Any, needle: str) -> bool:
    if isinstance(value, str):
        return needle in value
    if isinstance(value, Mapping):
        return any(_contains_text(item, needle) for item in value.values())
    if isinstance(value, list):
        return any(_contains_text(item, needle) for item in value)
    return False


def _tool_names(payload: Mapping[str, Any]) -> tuple[str, ...]:
    names: list[str] = []
    tools = payload.get("tools")
    if not isinstance(tools, list):
        return ()
    for tool in tools:
        if not isinstance(tool, Mapping):
            continue
        function = tool.get("function")
        if isinstance(function, Mapping) and isinstance(function.get("name"), str):
            names.append(str(function["name"]))
    return tuple(names)


def _has_tool_result(payload: Mapping[str, Any]) -> bool:
    messages = payload.get("messages")
    return isinstance(messages, list) and any(
        isinstance(message, Mapping) and message.get("role") == "tool"
        for message in messages
    )


def _tool_result_count(payload: Mapping[str, Any]) -> int:
    messages = payload.get("messages")
    if not isinstance(messages, list):
        return 0
    return sum(
        isinstance(message, Mapping) and message.get("role") == "tool"
        for message in messages
    )


def _tool_call_count(value: Any, suffix: str) -> int:
    if isinstance(value, Mapping):
        count = 0
        function = value.get("function")
        if isinstance(function, Mapping):
            name = function.get("name")
            if isinstance(name, str) and name.endswith(suffix):
                count += 1
        return count + sum(_tool_call_count(item, suffix) for item in value.values())
    if isinstance(value, list):
        return sum(_tool_call_count(item, suffix) for item in value)
    return 0


def _select_tool(names: tuple[str, ...], suffix: str) -> str | None:
    return next(
        (name for name in names if name == suffix or name.endswith(suffix)),
        None,
    )


@dataclass
class ProviderTrace:
    requests: list[dict[str, Any]] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)
    abort_started: threading.Event = field(default_factory=threading.Event)
    abort_released: threading.Event = field(default_factory=threading.Event)

    def append(self, payload: dict[str, Any]) -> None:
        with self.lock:
            self.requests.append(payload)

    def snapshot(self) -> tuple[dict[str, Any], ...]:
        with self.lock:
            return tuple(dict(item) for item in self.requests)


class _LoopbackServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False


class LoopbackOpenAIProvider:
    """Provider OpenAI-compatible local, borné et observable sans prompts bruts."""

    def __init__(self) -> None:
        self.trace = ProviderTrace()
        trace = self.trace
        scenario_files: dict[str, tuple[Path, str, str]] = {}
        self._scenario_files = scenario_files

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, _format: str, *_args: Any) -> None:
                return

            def _send_json(self, status: int, payload: Mapping[str, Any]) -> None:
                encoded = json.dumps(dict(payload), separators=(",", ":")).encode(
                    "utf-8"
                )
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

            def _body(self) -> dict[str, Any]:
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    value = json.loads(self.rfile.read(length) or b"{}")
                except (ValueError, json.JSONDecodeError):
                    return {}
                return dict(value) if isinstance(value, Mapping) else {}

            def do_GET(self) -> None:  # noqa: N802 - API BaseHTTPRequestHandler
                if self.path.rstrip("/") == "/v1/models":
                    self._send_json(
                        200,
                        {
                            "object": "list",
                            "data": [
                                {
                                    "id": "fixture-model",
                                    "object": "model",
                                    "owned_by": "jarvis-loopback",
                                }
                            ],
                        },
                    )
                    return
                self._send_json(404, {"error": {"message": "fixture route not found"}})

            def do_POST(self) -> None:  # noqa: N802 - API BaseHTTPRequestHandler
                if self.path.rstrip("/") != "/v1/chat/completions":
                    self._send_json(
                        404, {"error": {"message": "fixture route not found"}}
                    )
                    return
                body = self._body()
                tool_names = _tool_names(body)
                has_tool_result = _has_tool_result(body)
                abort_mode = _contains_text(body.get("messages"), "ABORT_ME")
                scenario = next(
                    (
                        marker
                        for marker in (
                            "READONLY_E2E",
                            "CODING_E2E",
                            "GATE_E2E",
                            "APPROVAL_E2E",
                            "LOOP_E2E",
                        )
                        if _contains_text(body.get("messages"), marker)
                    ),
                    None,
                )
                trace.append(
                    {
                        "path": "/v1/chat/completions",
                        "stream": bool(body.get("stream")),
                        "model": str(body.get("model") or ""),
                        "tool_names": list(tool_names),
                        "has_tool_result": has_tool_result,
                        "abort_mode": abort_mode,
                        "scenario": scenario,
                    }
                )
                if abort_mode:
                    trace.abort_started.set()
                    self._stream_abort(body)
                    return
                if scenario is not None:
                    fixture = scenario_files.get(scenario)
                    if fixture is None:
                        self._completion(
                            body,
                            tool_name=None,
                            tool_arguments=None,
                            text=f"{FINAL_MARKER}: SCENARIO_NOT_REGISTERED",
                        )
                        return
                    file_path, initial, corrected = fixture
                    messages = body.get("messages")
                    read_calls = _tool_call_count(messages, "read")
                    edit_calls = _tool_call_count(messages, "edit")
                    gate_calls = _tool_call_count(messages, "fixture_gate")
                    if scenario == "LOOP_E2E":
                        # Reproduit l'incident de production : toujours le même
                        # appel, mêmes arguments, jamais de réponse finale. Le
                        # fournisseur ne s'arrête pas — c'est au garde
                        # anti-boucle de l'arrêter.
                        tool_name = _select_tool(tool_names, "read")
                        arguments = {"filePath": str(file_path)}
                    elif scenario == "READONLY_E2E" and read_calls == 0:
                        tool_name = _select_tool(tool_names, "read")
                        arguments = {"filePath": str(file_path)}
                    elif scenario in {"CODING_E2E", "APPROVAL_E2E"} and edit_calls == 0:
                        tool_name = _select_tool(tool_names, "edit")
                        arguments = {
                            "filePath": str(file_path),
                            "oldString": initial,
                            "newString": corrected,
                        }
                    elif scenario == "GATE_E2E" and gate_calls == 0:
                        tool_name = _select_tool(tool_names, "fixture_gate")
                        arguments = {
                            "path": file_path.name,
                            "expected": corrected.strip(),
                        }
                    elif scenario == "GATE_E2E" and edit_calls == 0:
                        tool_name = _select_tool(tool_names, "edit")
                        arguments = {
                            "filePath": str(file_path),
                            "oldString": initial,
                            "newString": corrected,
                        }
                    elif scenario == "GATE_E2E" and gate_calls == 1:
                        tool_name = _select_tool(tool_names, "fixture_gate")
                        arguments = {
                            "path": file_path.name,
                            "expected": corrected.strip(),
                        }
                    else:
                        tool_name = None
                        arguments = None
                    self._completion(
                        body,
                        tool_name=tool_name,
                        tool_arguments=arguments,
                        text=None
                        if tool_name is not None
                        else f"{FINAL_MARKER}: {scenario}_OK",
                    )
                    return
                fixture_tool = next(
                    (name for name in tool_names if name.endswith("fixture_echo")),
                    None,
                )
                if fixture_tool is not None and not has_tool_result:
                    self._completion(
                        body,
                        tool_name=fixture_tool,
                        tool_arguments={"text": "MCP_ECHO_OK"},
                        text=None,
                    )
                    return
                self._completion(
                    body,
                    tool_name=None,
                    tool_arguments=None,
                    text=FINAL_MARKER,
                )

            def _completion(
                self,
                body: Mapping[str, Any],
                *,
                tool_name: str | None,
                tool_arguments: Mapping[str, Any] | None,
                text: str | None,
            ) -> None:
                call_id = f"call_jarvis_fixture_{_tool_result_count(body) + 1}"
                if not body.get("stream"):
                    message: dict[str, Any] = {"role": "assistant", "content": text}
                    finish = "stop"
                    if tool_name is not None:
                        message["content"] = None
                        message["tool_calls"] = [
                            {
                                "id": call_id,
                                "type": "function",
                                "function": {
                                    "name": tool_name,
                                    "arguments": json.dumps(dict(tool_arguments or {})),
                                },
                            }
                        ]
                        finish = "tool_calls"
                    self._send_json(
                        200,
                        {
                            "id": "chatcmpl-jarvis-fixture",
                            "object": "chat.completion",
                            "created": 1,
                            "model": "fixture-model",
                            "choices": [
                                {
                                    "index": 0,
                                    "message": message,
                                    "finish_reason": finish,
                                }
                            ],
                            "usage": {
                                "prompt_tokens": 8,
                                "completion_tokens": 4,
                                "total_tokens": 12,
                            },
                        },
                    )
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "close")
                self.end_headers()
                if tool_name is not None:
                    delta: dict[str, Any] = {
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": call_id,
                                "type": "function",
                                "function": {
                                    "name": tool_name,
                                    "arguments": json.dumps(dict(tool_arguments or {})),
                                },
                            }
                        ],
                    }
                    self._chunk(delta, None)
                    self._chunk({}, "tool_calls")
                else:
                    self._chunk({"role": "assistant", "content": text}, None)
                    self._chunk({}, "stop")
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()

            def _stream_abort(self, body: Mapping[str, Any]) -> None:
                if not body.get("stream"):
                    time.sleep(30)
                    self._send_json(200, {"choices": []})
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "close")
                self.end_headers()
                try:
                    self._chunk({"role": "assistant", "content": "abort pending"}, None)
                    for _ in range(600):
                        self.wfile.write(b": fixture heartbeat\n\n")
                        self.wfile.flush()
                        time.sleep(0.05)
                except (BrokenPipeError, ConnectionResetError, OSError):
                    return
                finally:
                    trace.abort_released.set()

            def _chunk(self, delta: Mapping[str, Any], finish: str | None) -> None:
                payload = {
                    "id": "chatcmpl-jarvis-fixture",
                    "object": "chat.completion.chunk",
                    "created": 1,
                    "model": "fixture-model",
                    "choices": [
                        {"index": 0, "delta": dict(delta), "finish_reason": finish}
                    ],
                }
                self.wfile.write(
                    b"data: "
                    + json.dumps(payload, separators=(",", ":")).encode("utf-8")
                    + b"\n\n"
                )
                self.wfile.flush()

        self._server = _LoopbackServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="jarvis-opencode-e2e-provider",
            daemon=True,
        )

    @property
    def base_url(self) -> str:
        host, port = self._server.server_address
        return f"http://{host}:{port}"

    def register_file_scenario(
        self,
        marker: str,
        path: Path,
        *,
        initial: str,
        corrected: str,
    ) -> None:
        """Enregistre avant start un scénario borné sans tracer son chemin."""

        if marker not in {
            "READONLY_E2E",
            "CODING_E2E",
            "GATE_E2E",
            "APPROVAL_E2E",
            "LOOP_E2E",
        }:
            raise ValueError("marqueur de scénario E2E inconnu")
        candidate = path.resolve(strict=True)
        if not candidate.is_file() or candidate.is_symlink():
            raise ValueError("fichier de scénario E2E invalide")
        self._scenario_files[marker] = (candidate, initial, corrected)

    @property
    def abort_started(self) -> threading.Event:
        return self.trace.abort_started

    @property
    def abort_released(self) -> threading.Event:
        return self.trace.abort_released

    def start(self) -> "LoopbackOpenAIProvider":
        self._thread.start()
        return self

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=3)

    def __enter__(self) -> "LoopbackOpenAIProvider":
        return self.start()

    def __exit__(self, *_exc: object) -> None:
        self.close()


def serve_mcp(trace_path: Path, workspace: Path | None = None) -> int:
    """Sert un outil MCP minimal sur stdio et trace uniquement le contrat."""

    workspace_candidate = workspace or Path.cwd()
    if workspace_candidate.is_symlink():
        raise ValueError("workspace MCP E2E invalide")
    workspace_root = workspace_candidate.resolve(strict=True)
    if not workspace_root.is_dir():
        raise ValueError("workspace MCP E2E invalide")
    _json_line(trace_path, {"event": "started", "pid": os.getpid()})
    try:
        for raw_line in iter(input, ""):
            try:
                request = json.loads(raw_line)
            except json.JSONDecodeError:
                continue
            if not isinstance(request, Mapping):
                continue
            method = request.get("method")
            request_id = request.get("id")
            if method == "notifications/initialized" or request_id is None:
                continue
            if method == "initialize":
                result: dict[str, Any] = {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "jarvis-e2e-fixture", "version": "1"},
                }
            elif method == "tools/list":
                result = {
                    "tools": [
                        {
                            "name": "fixture_echo",
                            "description": "Return a deterministic loopback-only E2E marker.",
                            "inputSchema": {
                                "type": "object",
                                "properties": {"text": {"type": "string"}},
                                "required": ["text"],
                                "additionalProperties": False,
                            },
                        },
                        {
                            "name": "fixture_gate",
                            "description": "Return GATE_GREEN only when a confined fixture file matches.",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "path": {"type": "string"},
                                    "expected": {"type": "string"},
                                },
                                "required": ["path", "expected"],
                                "additionalProperties": False,
                            },
                        },
                    ]
                }
            elif method == "tools/call":
                params = request.get("params")
                name = params.get("name") if isinstance(params, Mapping) else None
                arguments = (
                    params.get("arguments") if isinstance(params, Mapping) else None
                )
                gate_result: str | None = None
                if isinstance(name, str) and name.endswith("fixture_gate"):
                    plain_arguments = (
                        arguments if isinstance(arguments, Mapping) else {}
                    )
                    relative = Path(str(plain_arguments.get("path") or ""))
                    if relative.is_absolute() or ".." in relative.parts:
                        gate_result = "GATE_RED"
                    else:
                        candidate = (workspace_root / relative).resolve(strict=False)
                        try:
                            candidate.relative_to(workspace_root)
                        except ValueError:
                            gate_result = "GATE_RED"
                        else:
                            expected = str(plain_arguments.get("expected") or "")
                            if (
                                candidate.is_file()
                                and not candidate.is_symlink()
                                and candidate.stat().st_size <= 64 * 1024
                                and candidate.read_text(encoding="utf-8").strip()
                                == expected
                            ):
                                gate_result = "GATE_GREEN"
                            else:
                                gate_result = "GATE_RED"
                _json_line(
                    trace_path,
                    {
                        "event": "tool_call",
                        "name": name,
                        "result": gate_result,
                    },
                )
                result = {
                    "content": [
                        {
                            "type": "text",
                            "text": gate_result or "MCP_ECHO_OK",
                        }
                    ],
                    "isError": False,
                }
            elif method == "ping":
                result = {}
            else:
                response = {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": -32601, "message": "Method not found"},
                }
                print(json.dumps(response, separators=(",", ":")), flush=True)
                continue
            response = {"jsonrpc": "2.0", "id": request_id, "result": result}
            print(json.dumps(response, separators=(",", ":")), flush=True)
    except EOFError:
        pass
    finally:
        _json_line(trace_path, {"event": "stopped", "pid": os.getpid()})
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="OpenCode real-binary E2E fixtures")
    subparsers = parser.add_subparsers(dest="command", required=True)
    mcp = subparsers.add_parser("mcp")
    mcp.add_argument("--trace", type=Path, required=True)
    mcp.add_argument("--workspace", type=Path)
    args = parser.parse_args(argv)
    if args.command == "mcp":
        return serve_mcp(args.trace, args.workspace)
    return 2


if __name__ == "__main__":  # pragma: no cover - exécuté par le vrai binaire
    raise SystemExit(main())
