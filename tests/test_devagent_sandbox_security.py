"""Régressions P12 : sandbox DevAgent, environnement et redaction DB."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.parametrize(
    "malicious_path",
    [
        "../../../agents/foo.py",
        "/tmp/devagent-escape.py",
        r"C:\\Windows\\devagent-escape.py",
        "%2e%2e/%2e%2e/agents/foo.py",
        "%252e%252e%252f%252e%252e%252fagents%252ffoo.py",
        "..%2F..%2Fagents%2Ffoo.py",
        "%2Ftmp%2Fdevagent-escape.py",
    ],
)
def test_loop_rejects_traversal_absolute_and_encoded_paths(
    tmp_path: Path,
    malicious_path: str,
) -> None:
    from agents.devagent.executor import GeneratedPathError
    from agents.devagent.loop import _write_generated_files

    project = tmp_path / "project"
    (project / "src").mkdir(parents=True)

    with pytest.raises(GeneratedPathError):
        _write_generated_files(project, {malicious_path: "malicious"})


def test_loop_rejects_outgoing_symlink(tmp_path: Path) -> None:
    from agents.devagent.executor import GeneratedPathError
    from agents.devagent.loop import _write_generated_files

    project = tmp_path / "project"
    src = project / "src"
    outside = tmp_path / "outside"
    src.mkdir(parents=True)
    outside.mkdir()
    (src / "linked").symlink_to(outside, target_is_directory=True)

    with pytest.raises(GeneratedPathError):
        _write_generated_files(project, {"linked/escape.py": "malicious"})
    assert not (outside / "escape.py").exists()


def test_loop_preserves_valid_generated_write(tmp_path: Path) -> None:
    from agents.devagent.loop import _write_generated_files

    project = tmp_path / "project"
    (project / "src").mkdir(parents=True)

    _write_generated_files(project, {"package/app.py": "VALUE = 1\n"})

    assert (project / "src" / "package" / "app.py").read_text(
        encoding="utf-8"
    ) == "VALUE = 1\n"


@pytest.mark.asyncio
async def test_refactor_uses_same_generated_path_policy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agents.devagent.executor import GeneratedPathError, git_init
    from agents.devagent.refactor import refactor_top_duplicate

    duplicate = """def compute(x, y):
    total = x + y
    total *= 2
    total -= 1
    result = total / 3
    return result
"""
    project = tmp_path / "project"
    src = project / "src"
    src.mkdir(parents=True)
    (src / "a.py").write_text(duplicate, encoding="utf-8")
    (src / "b.py").write_text(duplicate, encoding="utf-8")
    git_init(project)
    monkeypatch.setattr("config.AGENTIC_RUNTIME", "disabled")
    monkeypatch.setattr("config.AGENTIC_RUNTIME_FALLBACK", "legacy")
    fake_response = {
        "content": json.dumps({"files": {"../escape.py": "malicious"}}),
        "tokens_total": 1,
    }

    with patch(
        "agents.devagent.refactor.call_deepseek",
        new=AsyncMock(return_value=fake_response),
    ), pytest.raises(GeneratedPathError):
        await refactor_top_duplicate(project, test_command="true")

    assert not (project / "escape.py").exists()


def test_devagent_child_environment_excludes_api_keys_and_tokens(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agents.devagent.executor import run_isolated

    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-parent-secret-123456")
    monkeypatch.setenv("GITHUB_TOKEN", "github-parent-token-123456")
    monkeypatch.setenv("DEVICE_ACCESS_TOKEN", "device-parent-token-123456")

    result = run_isolated(["/usr/bin/env"], cwd=tmp_path, timeout=10)
    child_env = str(result["stdout"])

    for forbidden in (
        "DEEPSEEK_API_KEY",
        "GITHUB_TOKEN",
        "DEVICE_ACCESS_TOKEN",
        "sk-parent-secret-123456",
        "github-parent-token-123456",
        "device-parent-token-123456",
    ):
        assert forbidden not in child_env
    assert "PATH=" in child_env


@pytest.fixture
def redaction_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db_path = tmp_path / "redaction.db"
    monkeypatch.setattr("config.DB_PATH", str(db_path))
    monkeypatch.setattr("database.DB_PATH", db_path)
    monkeypatch.setattr("config.ACTION_LOG_MAX_PAYLOAD_CHARS", 4096)
    from database import init_db

    init_db()
    return db_path


def test_secrets_and_pii_are_absent_from_devagent_and_cursor_tables(
    redaction_db: Path,
) -> None:
    from database import devagent as devagent_db
    from database import get_db
    from database.cursor_jobs import create_cursor_job, update_cursor_job

    api_secret = "sk-persisted-secret-123456"
    bearer_secret = "Bearer persisted-token-123456"
    email = "alice.security@example.com"
    phone = "06 12 34 56 78"

    project_id = devagent_db.create_dev_project(
        "privacy-p12", "Privacy P12", "/tmp/privacy-p12"
    )
    devagent_db.save_interview_context(
        project_id,
        {
            "qa_history": [{"q": "Contact ?", "a": f"{email} {phone}"}],
            "api_key": api_secret,
        },
    )
    devagent_db.save_spec(
        project_id,
        json.dumps(
            {
                "project_name": "Privacy",
                "owner_email": email,
                "constraints": [f"DEEPSEEK_API_KEY={api_secret}"],
            }
        ),
    )
    devagent_db.update_loop_state(
        project_id,
        {
            "iteration": 1,
            "phase": "test",
            "last_error": f"stdout {bearer_secret} pour {email}",
            "consecutive_failures": 1,
            "tokens_used": 10,
        },
    )
    devagent_db.log_iteration(
        project_id,
        1,
        "test",
        f"stdout {api_secret} {bearer_secret} {email}",
        False,
    )
    devagent_db.record_deployment(
        project_id,
        "abc123",
        "failed",
        "/tmp/privacy-p12-staging",
        f"deploy stdout {api_secret} {bearer_secret} {email} {phone}",
    )

    create_cursor_job(
        {
            "job_id": "p12-redaction-job",
            "title": f"Corriger pour {email}",
            "user_request": f"prompt utilisateur {api_secret} {email} {phone}",
            "prompt_sent": f"DEEPSEEK_API_KEY={api_secret} {bearer_secret} {email}",
            "status": "queued",
            "acceptance_criteria": [f"Notifier {email}"],
            "required_tests": [f"token={api_secret}"],
        }
    )
    update_cursor_job(
        "p12-redaction-job",
        raw_output=f"stdout {api_secret} {bearer_secret} {email} {phone}",
        structured_result={"body": f"résultat pour {email}", "token": api_secret},
        error_message=f"erreur {bearer_secret} {email}",
    )

    with get_db() as conn:
        persisted_values = [
            conn.execute(
                "SELECT context_json FROM dev_interview_sessions WHERE project_id = ?",
                (project_id,),
            ).fetchone()[0],
            conn.execute(
                "SELECT spec_json FROM dev_spec WHERE project_id = ?",
                (project_id,),
            ).fetchone()[0],
            conn.execute(
                "SELECT last_error FROM dev_loop_state WHERE project_id = ?",
                (project_id,),
            ).fetchone()[0],
            conn.execute(
                "SELECT content FROM dev_loop_log WHERE project_id = ?",
                (project_id,),
            ).fetchone()[0],
            conn.execute(
                "SELECT log FROM dev_deployments WHERE project_id = ?",
                (project_id,),
            ).fetchone()[0],
        ]
        cursor_row = conn.execute(
            """SELECT title, user_request, prompt_sent, acceptance_criteria,
                      required_tests, raw_output, structured_result, error_message
               FROM cursor_delegation_jobs WHERE job_id = ?""",
            ("p12-redaction-job",),
        ).fetchone()
        persisted_values.extend(cursor_row)

    persisted = "\n".join(str(value) for value in persisted_values)
    for forbidden in (api_secret, "persisted-token-123456", email, phone):
        assert forbidden not in persisted
    assert "REDACTED" in persisted or "[EMAIL_" in persisted


def test_cursor_update_fails_closed_when_redaction_errors(
    redaction_db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from database import cursor_jobs, get_db

    cursor_jobs.create_cursor_job(
        {
            "job_id": "p12-fail-closed",
            "title": "Fail closed",
            "user_request": "safe request",
            "status": "queued",
        }
    )

    def _fail_redaction(_value: str | None) -> str:
        raise RuntimeError("redaction unavailable")

    monkeypatch.setattr(cursor_jobs, "redact_persisted_text", _fail_redaction)
    with pytest.raises(RuntimeError, match="redaction unavailable"):
        cursor_jobs.update_cursor_job(
            "p12-fail-closed",
            raw_output="must never be stored",
        )

    with get_db() as conn:
        raw_output = conn.execute(
            "SELECT raw_output FROM cursor_delegation_jobs WHERE job_id = ?",
            ("p12-fail-closed",),
        ).fetchone()[0]
    assert raw_output is None
