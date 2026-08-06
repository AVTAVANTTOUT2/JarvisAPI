from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_BLOCKED_ENV = {
    "ANTHROPIC_API_KEY",
    "CLAUDE_API_KEY",
    "OPENAI_API_KEY",
    "CODEX_API_KEY",
    "AZURE_OPENAI_API_KEY",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX",
}
_SAFE_ENV = {
    "CODEX_HOME",
    "HOME",
    "LANG",
    "LC_ALL",
    "LOGNAME",
    "PATH",
    "SHELL",
    "SSH_AUTH_SOCK",
    "TERM",
    "TMPDIR",
    "TZ",
    "USER",
    "XDG_CONFIG_HOME",
}


def subscription_environment(parent: dict[str, str] | None = None) -> dict[str, str]:
    """Environnement minimal qui force l'usage des sessions d'abonnement locales."""
    source = parent if parent is not None else dict(os.environ)
    env = {key: value for key, value in source.items() if key in _SAFE_ENV}
    for key in _BLOCKED_ENV:
        env.pop(key, None)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["NO_OPEN_BROWSER"] = "1"
    env.setdefault("TERM", "dumb")
    return env


@dataclass(slots=True)
class ProviderResult:
    ok: bool
    output: str
    structured: dict[str, Any] | None
    returncode: int
    stderr: str = ""


class SubscriptionProviders:
    def __init__(self, runtime_dir: Path) -> None:
        self.runtime_dir = runtime_dir

    @staticmethod
    def _status(command: list[str]) -> tuple[bool, str]:
        if not shutil.which(command[0]):
            return False, f"{command[0]} introuvable"
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=15,
            env=subscription_environment(),
            check=False,
        )
        detail = (result.stdout or result.stderr or "").strip()[:500]
        return result.returncode == 0, detail

    def doctor(self) -> dict[str, Any]:
        codex_ok, codex_detail = self._status(["codex", "login", "status"])
        claude_ok, claude_detail = self._status(["claude", "auth", "status", "--json"])
        if claude_detail.startswith("{"):
            try:
                claude_payload = json.loads(claude_detail)
                claude_ok = bool(claude_payload.get("loggedIn"))
                claude_detail = (
                    f"loggedIn={claude_ok}, subscription="
                    f"{claude_payload.get('subscriptionType') or 'unknown'}"
                )
            except json.JSONDecodeError:
                pass
        return {
            "codex": {
                "available": shutil.which("codex") is not None,
                "logged_in": codex_ok,
                "detail": codex_detail,
            },
            "claude": {
                "available": shutil.which("claude") is not None,
                "logged_in": claude_ok,
                "detail": claude_detail,
            },
            "auth_mode": "local_subscription_only",
        }

    def run_codex(
        self,
        prompt: str,
        *,
        cwd: Path,
        writable: bool,
        schema: dict[str, Any] | None = None,
        timeout: int = 3600,
    ) -> ProviderResult:
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="codex-run-", dir=self.runtime_dir
        ) as tmp:
            tmp_path = Path(tmp)
            output_path = tmp_path / "last-message.txt"
            command = [
                "codex",
                "--ask-for-approval",
                "never",
                "--sandbox",
                "workspace-write" if writable else "read-only",
                "exec",
                "--ephemeral",
                "--output-last-message",
                str(output_path),
            ]
            if schema is not None:
                schema_path = tmp_path / "schema.json"
                schema_path.write_text(json.dumps(schema), encoding="utf-8")
                command.extend(["--output-schema", str(schema_path)])
            command.append("-")
            result = subprocess.run(
                command,
                cwd=str(cwd),
                input=prompt,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=subscription_environment(),
                check=False,
            )
            output = (
                output_path.read_text(encoding="utf-8")
                if output_path.exists()
                else result.stdout
            )
            structured = None
            if schema is not None and output.strip():
                try:
                    structured = json.loads(output)
                except json.JSONDecodeError:
                    structured = None
            return ProviderResult(
                ok=result.returncode == 0,
                output=output.strip()[:100_000],
                structured=structured,
                returncode=result.returncode,
                stderr=(result.stderr or "")[-10_000:],
            )

    def run_claude_review(
        self,
        prompt: str,
        *,
        cwd: Path,
        schema: dict[str, Any],
        timeout: int = 1800,
    ) -> ProviderResult:
        command = [
            "claude",
            "--print",
            "--output-format",
            "json",
            "--permission-mode",
            "plan",
            "--tools",
            "Read,Glob,Grep,Bash",
            "--allowedTools",
            "Read,Glob,Grep,Bash(git status *),Bash(git diff *),Bash(git log *)",
            "--json-schema",
            json.dumps(schema),
        ]
        result = subprocess.run(
            command,
            cwd=str(cwd),
            input=prompt,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=subscription_environment(),
            check=False,
        )
        output = (result.stdout or "").strip()
        structured = None
        if output:
            try:
                envelope = json.loads(output)
                candidate = envelope.get("structured_output") or envelope.get("result")
                structured = (
                    candidate if isinstance(candidate, dict) else json.loads(candidate)
                )
            except (json.JSONDecodeError, TypeError):
                structured = None
        return ProviderResult(
            ok=result.returncode == 0 and structured is not None,
            output=output[:100_000],
            structured=structured,
            returncode=result.returncode,
            stderr=(result.stderr or "")[-10_000:],
        )
