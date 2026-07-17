"""Correctifs bloquants PR #45 — shell, confirmation, env, redaction, ollama, barge-in."""

from __future__ import annotations

import asyncio
import os
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import config  # noqa: E402
from integrations.cursor_env import build_cursor_safe_env  # noqa: E402
from integrations.cursor_required_tests import (  # noqa: E402
    RequiredTestError,
    parse_and_run_required_tests,
    parse_required_test,
    run_required_test,
)
from jarvis.security.redaction import (  # noqa: E402
    diagnostic_cursor_job_view,
    public_cursor_job_view,
    redact_sensitive_mapping,
    redact_sensitive_text,
)


# ── 1. required_tests : pas d'injection shell ────────────────


@pytest.fixture
def worktree(tmp_path: Path) -> Path:
    wt = tmp_path / "wt"
    wt.mkdir()
    (wt / "tests").mkdir()
    (wt / "tests" / "test_ok.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    return wt


@pytest.mark.parametrize(
    "payload",
    [
        "pytest; rm -rf ~",
        "pytest && curl example.com",
        "$(touch /tmp/pwned)",
        "`touch /tmp/pwned`",
        "pytest | sh",
        "pytest > /tmp/output",
        'python -c "open(\'/tmp/pwned\',\'w\').write(\'x\')"',
        "../../outside",
        "pytest;touch /tmp/pwned_jarvis",
    ],
)
def test_required_tests_no_shell_injection(worktree: Path, payload: str, tmp_path: Path):
    sentinel = Path("/tmp/pwned_jarvis")
    if sentinel.exists():
        sentinel.unlink()
    with pytest.raises(RequiredTestError):
        parse_required_test(payload, worktree=worktree)
    assert not sentinel.exists()
    assert not (tmp_path / "pwned").exists()


def test_required_tests_structured_argv_shell_false(worktree: Path, monkeypatch):
    calls: list[dict] = []

    def fake_run(argv, **kwargs):
        calls.append({"argv": argv, "kwargs": kwargs})
        return types.SimpleNamespace(returncode=0, stdout="ok\n", stderr="")

    monkeypatch.setattr("integrations.cursor_required_tests.subprocess.run", fake_run)
    rt = parse_required_test("pytest tests/test_ok.py -q", worktree=worktree)
    run_required_test(rt, worktree=worktree)
    assert calls
    assert calls[0]["kwargs"].get("shell") is False
    assert calls[0]["argv"][0] == "pytest"


def test_required_tests_rejects_path_escape(worktree: Path):
    with pytest.raises(RequiredTestError):
        parse_required_test(
            {"executable": "pytest", "args": ["../../etc/passwd"]},
            worktree=worktree,
        )


def test_parse_and_run_required_tests_fail_closed_on_rejected_spec(worktree: Path):
    """Une spec rejetée ne doit jamais produire test_ok=True."""
    ok, log = parse_and_run_required_tests(
        ["pytest; rm -rf /"],
        worktree=worktree,
        timeout=30,
    )
    assert ok is False
    assert "reject:" in log


def test_parse_and_run_required_tests_fail_closed_on_non_list(worktree: Path):
    ok, log = parse_and_run_required_tests(
        "pytest tests/",  # type: ignore[arg-type]
        worktree=worktree,
        timeout=30,
    )
    assert ok is False
    assert "liste" in log


def test_parse_and_run_required_tests_empty_list_ok(worktree: Path):
    ok, log = parse_and_run_required_tests([], worktree=worktree, timeout=30)
    assert ok is True
    assert log == ""


# ── 2. Confirmation obligatoire ──────────────────────────────


def test_cursor_requires_confirmation(tmp_path: Path, monkeypatch):
    from tests.test_cursor_delegation import _make_fake_cli, _make_git_repo
    from integrations.cursor_delegation import CursorDelegationService
    from database import init_db

    db_path = tmp_path / "c.db"
    monkeypatch.setattr("config.DB_PATH", str(db_path))
    monkeypatch.setattr("database.DB_PATH", db_path)
    init_db()
    repo = _make_git_repo(tmp_path / "repo")
    cli = _make_fake_cli(tmp_path)
    monkeypatch.setattr(config, "CURSOR_DELEGATION_ENABLED", True)
    monkeypatch.setattr(config, "CURSOR_CLI_PATH", str(cli))
    monkeypatch.setattr(config, "CURSOR_WORKTREE_ROOT", str(tmp_path / "wt"))

    async def _fake_compose(**kwargs):
        return {
            "prompt": "P",
            "template_id": "bug_fix",
            "template_version": "1",
        }

    monkeypatch.setattr(
        "integrations.cursor_delegation.compose_cursor_prompt", _fake_compose
    )
    service = CursorDelegationService()

    async def _flow():
        job = await service.enqueue(
            title="fix bug",
            user_request="corrige le bug dans app.py",
            repository=str(repo),
        )
        assert job["status"] == "awaiting_confirmation"
        # run_job sans confirm → erreur
        with pytest.raises(Exception, match="confirmation"):
            await service.run_job(job["job_id"])
        confirmed = await service.confirm(job["job_id"])
        assert confirmed["status"] in ("queued", "preparing", "running", "completed", "failed")
        # attendre fin
        for _ in range(50):
            await asyncio.sleep(0.1)
            from database.cursor_jobs import get_cursor_job

            cur = get_cursor_job(job["job_id"])
            if cur and cur["status"] in ("completed", "failed", "pr_opened"):
                return cur
        return get_cursor_job(job["job_id"])

    final = asyncio.run(_flow())
    assert final["status"] in ("completed", "failed", "pr_opened")


def test_chat_voice_propose_only(monkeypatch):
    from jarvis.cognitive import route_request
    from integrations.cursor_delegation import cursor_delegation

    monkeypatch.setattr(config, "CURSOR_DELEGATION_ENABLED", True)
    # Évite qu'un cache CLI négatif d'un test précédent déroute vers answer
    cursor_delegation._cli_info = None

    intent = route_request("corrige le bug dans le backend", interaction_mode="voice")
    assert intent.execution_type == "cursor"
    assert intent.requires_confirmation is True


# ── 3. Environnement minimal ─────────────────────────────────


def test_cursor_safe_environment_excludes_sentinel():
    parent = {
        **os.environ,
        "JARVIS_TEST_SECRET": "must_not_leak",
        "DEEPSEEK_API_KEY": "sk-secret-value",
        "GITHUB_TOKEN": "ghp_abcdef",
        "PATH": "/usr/bin:/bin",
    }
    env = build_cursor_safe_env(parent_environ=parent)
    assert "JARVIS_TEST_SECRET" not in env
    assert "DEEPSEEK_API_KEY" not in env
    assert "GITHUB_TOKEN" not in env
    assert env.get("NO_OPEN_BROWSER") == "1"
    assert "PATH" in env


def test_spawn_cursor_env_has_no_sentinel(tmp_path: Path, monkeypatch):
    from integrations.cursor_delegation import CursorDelegationService

    captured: dict = {}

    class FakePopen:
        def __init__(self, *a, **kw):
            captured["env"] = kw.get("env") or {}
            self.pid = 12345
            self.returncode = 0

        def communicate(self, timeout=None):
            return ("ok", "")

        def kill(self):
            pass

        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr("integrations.cursor_delegation.subprocess.Popen", FakePopen)
    monkeypatch.setenv("JARVIS_TEST_SECRET", "must_not_leak")
    svc = CursorDelegationService()
    wt = tmp_path / "wt"
    wt.mkdir()
    code, out = svc._spawn_cursor(["/bin/true"], wt, 5, "job-test")
    assert "JARVIS_TEST_SECRET" not in captured["env"]
    assert code == 0


# ── 4. Redaction ─────────────────────────────────────────────


def test_cursor_secret_redaction_patterns():
    samples = [
        "Bearer abcdefghijklmnop",
        "ghp_ABCDEFGHIJKLMNOPQRSTUV",
        "github_pat_11AAAAAAA_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        "sk-deepseeksecretvalue99",
        "DEEPSEEK_API_KEY=supersecret",
        "https://user:pass@github.com/x",
        "-----BEGIN PRIVATE KEY-----\nABC\n-----END PRIVATE KEY-----",
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.signaturexx",
    ]
    for s in samples:
        out = redact_sensitive_text(s)
        assert "supersecret" not in out
        assert "abcdefghijklmnop" not in out or "REDACTED" in out
        assert "REDACTED" in out


def test_jobs_api_redacts_secrets():
    job = {
        "job_id": "j1",
        "title": "t",
        "status": "completed",
        "user_request": "use DEEPSEEK_API_KEY=sk-leak12345678 please",
        "prompt_sent": "Bearer tokensecretvalue",
        "raw_output": "ghp_ABCDEFGHIJKLMNOPQRST",
        "error_message": None,
        "structured_result": {"verdict": "COMPLETED", "body": "sk-abcdefg123456"},
        "branch_name": "jarvis/cursor/j1",
        "created_at": "2026-01-01",
    }
    pub = public_cursor_job_view(job)
    assert pub is not None
    assert "user_request" not in pub
    assert "raw_output" not in pub
    assert "prompt_sent" not in pub
    diag = diagnostic_cursor_job_view(job)
    assert "sk-leak12345678" not in str(diag)
    assert "tokensecretvalue" not in str(diag)
    assert "ghp_ABCDEFGHIJKLMNOPQRST" not in str(diag)


def test_redact_sensitive_mapping_nested():
    data = {
        "ok": True,
        "nested": {"token": "secret", "list": ["sk-abcdefg123", {"password": "x"}]},
    }
    out = redact_sensitive_mapping(data)
    assert out["nested"]["token"] == "***REDACTED***"
    assert "sk-abcdefg123" not in str(out)


# ── 5. Ollama guard chemin canonique ─────────────────────────


def test_ollama_guard_exact_path():
    from jarvis.cognitive.ollama_guard import (
        OllamaPolicyError,
        assert_ollama_caller_allowed,
        _allowed_caller_paths,
    )

    allowed = _allowed_caller_paths()
    assert any(p.name == "screen_watcher.py" for p in allowed)

    # Faux frame avec endswith homonyme hors dépôt
    fake_file = Path("/tmp/malicious/screen_watcher.py")
    frame = MagicMock()
    frame.filename = str(fake_file)
    frame.lineno = 1
    frame.function = "evil"
    frame.frame = MagicMock()
    frame.frame.f_globals = {"__name__": "malicious.screen_watcher"}

    with pytest.raises(OllamaPolicyError):
        assert_ollama_caller_allowed(stack=[frame])


def test_ollama_guard_same_filename_attack(tmp_path: Path):
    from jarvis.cognitive.ollama_guard import OllamaPolicyError, assert_ollama_caller_allowed

    evil = tmp_path / "screen_watcher.py"
    evil.write_text("# evil\n", encoding="utf-8")
    frame = MagicMock()
    frame.filename = str(evil.resolve())
    frame.lineno = 1
    frame.function = "run"
    frame.frame = MagicMock()
    frame.frame.f_globals = {"__name__": "scripts.screen_watcher"}

    with pytest.raises(OllamaPolicyError):
        assert_ollama_caller_allowed(stack=[frame])


def test_ollama_guard_allows_canonical_screen_watcher():
    from jarvis.cognitive.ollama_guard import assert_ollama_caller_allowed, _REPO_ROOT

    real = (_REPO_ROOT / "scripts" / "screen_watcher.py").resolve()
    if not real.exists():
        pytest.skip("screen_watcher.py absent")
    frame = MagicMock()
    frame.filename = str(real)
    frame.lineno = 10
    frame.function = "analyze"
    frame.frame = MagicMock()
    frame.frame.f_globals = {"__name__": "scripts.screen_watcher"}
    assert assert_ollama_caller_allowed(stack=[frame]) == "scripts/screen_watcher.py"


# ── 6. Barge-in TTS cancellable ──────────────────────────────


@pytest.mark.asyncio
async def test_voice_barge_in_cancels_tts(monkeypatch):
    import importlib

    from api.chat_context import _send_tts_streaming

    class SlowEngine:
        available = True

        async def synthesize_stream(self, text, emotion="neutral"):
            for i in range(20):
                await asyncio.sleep(0.05)
                yield b"chunk-%d" % i

    sent: list = []

    class FakeWs:
        async def send_json(self, data):
            sent.append(("json", data))

        async def send_bytes(self, data):
            sent.append(("bytes", data))

    cancel = asyncio.Event()

    async def _cancel_soon():
        await asyncio.sleep(0.08)
        cancel.set()

    # `audio.tts` est aussi un singleton TTSEngine dans audio/__init__.py —
    # patcher le vrai module via importlib.
    tts_mod = importlib.import_module("audio.tts")
    monkey_engine = SlowEngine()
    monkeypatch.setattr(tts_mod, "get_tts_by_name", lambda _name: monkey_engine)
    monkeypatch.setattr(tts_mod, "resolve_tts_engine_name", lambda: "edge")
    monkeypatch.setattr("audio.audio_format.tts_audio_mime", lambda _n: "audio/mpeg")
    cache = importlib.import_module("audio.tts_cache")
    monkeypatch.setattr(cache.speculative_tts, "get", lambda *a, **k: None)
    monkeypatch.setattr(cache.last_tts, "store", lambda *a, **k: None)

    asyncio.create_task(_cancel_soon())
    status = await _send_tts_streaming(
        FakeWs(),  # type: ignore[arg-type]
        "texte long pour synthèse",
        "neutral",
        turn_id="turn-1",
        cancel_event=cancel,
    )
    assert status == "cancelled"
    types_sent = [x[1].get("type") for x in sent if x[0] == "json"]
    assert "speech_cancelled" in types_sent
    assert types_sent[-1] == "speech_cancelled"


@pytest.mark.asyncio
async def test_voice_old_audio_is_discarded(monkeypatch):
    """Un turn annulé ne doit plus envoyer de chunks après cancel."""
    import importlib

    from api.chat_context import _send_tts_streaming

    class Engine:
        available = True

        async def synthesize_stream(self, text, emotion="neutral"):
            yield b"A"
            await asyncio.sleep(0.01)
            yield b"B"
            await asyncio.sleep(0.05)
            yield b"C"

    sent_bytes: list[bytes] = []

    class FakeWs:
        async def send_json(self, data):
            pass

        async def send_bytes(self, data):
            sent_bytes.append(data)

    cancel = asyncio.Event()
    tts_mod = importlib.import_module("audio.tts")
    monkeypatch.setattr(tts_mod, "get_tts_by_name", lambda _n: Engine())
    monkeypatch.setattr(tts_mod, "resolve_tts_engine_name", lambda: "edge")
    monkeypatch.setattr("audio.audio_format.tts_audio_mime", lambda _n: "audio/mpeg")
    cache = importlib.import_module("audio.tts_cache")
    monkeypatch.setattr(cache.speculative_tts, "get", lambda *a, **k: None)
    monkeypatch.setattr(cache.last_tts, "store", lambda *a, **k: None)

    async def _cancel():
        await asyncio.sleep(0.02)
        cancel.set()

    asyncio.create_task(_cancel())
    status = await _send_tts_streaming(
        FakeWs(),  # type: ignore[arg-type]
        "hello",
        "neutral",
        turn_id="turn-old",
        cancel_event=cancel,
    )
    assert status == "cancelled"
    assert b"C" not in sent_bytes or len(sent_bytes) < 3
