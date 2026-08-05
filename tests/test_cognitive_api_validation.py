"""Validation d'entrée de l'API cognitive / délégation Cursor.

Couvre le durcissement de ``api/router_cognitive.py`` : métacaractères shell
refusés dans ``RequiredTestSpec``, format strict des ``job_id``, critères
d'acceptation bornés, et statut de liste borné à ``VALID_STATUSES``.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


@pytest.mark.parametrize(
    "payload",
    [
        {"executable": "pytest; rm -rf /"},
        {"executable": "pytest", "args": ["--foo", "a && b"]},
        {"executable": "pytest", "cwd": "/tmp/`id`"},
        {"executable": "pytest", "args": ["$(reboot)"]},
    ],
)
def test_required_test_spec_rejects_shell_metacharacters(payload):
    from api.router_cognitive import RequiredTestSpec

    with pytest.raises(ValidationError) as exc:
        RequiredTestSpec(**payload)
    assert "métacaractères shell interdits" in str(exc.value) or (
        "argument interdit" in str(exc.value)
    )


def test_required_test_spec_accepts_safe_argv():
    from api.router_cognitive import RequiredTestSpec

    spec = RequiredTestSpec(
        executable="pytest",
        args=["tests/test_cognitive_api_validation.py", "-q"],
        cwd=".",
        timeout_seconds=60,
    )
    assert spec.executable == "pytest"
    assert spec.args == ["tests/test_cognitive_api_validation.py", "-q"]


def test_acceptance_criteria_item_length_capped():
    from api.router_cognitive import CursorEnqueueRequest

    with pytest.raises(ValidationError) as exc:
        CursorEnqueueRequest(
            title="x",
            user_request="y",
            acceptance_criteria=["a" * 501],
        )
    assert "critère d'acceptation trop long" in str(exc.value)


def test_validate_job_id_accepts_canonical_format():
    from api.router_cognitive import _validate_job_id

    job_id = "job-20260805-101500-abc123"
    assert _validate_job_id(job_id) == job_id


@pytest.mark.parametrize(
    "job_id",
    [
        "job-short",
        "JOB-20260805-101500-abc123",
        "job-20260805-101500-ABCDEF",
        "../etc/passwd",
        "job-20260805-101500-abc123;rm",
        "job-2026-08-05-101500-abc123",
    ],
)
def test_validate_job_id_rejects_non_canonical(job_id):
    from api.router_cognitive import _validate_job_id

    with pytest.raises(HTTPException) as exc:
        _validate_job_id(job_id)
    assert exc.value.status_code == 400
    assert "job_id invalide" in str(exc.value.detail)


def test_cursor_jobs_rejects_unknown_status(monkeypatch):
    from api import router_cognitive
    from integrations.cursor_delegation import cursor_delegation

    monkeypatch.setattr(
        cursor_delegation,
        "list_jobs",
        MagicMock(side_effect=AssertionError("list_jobs ne doit pas être appelé")),
    )
    app = FastAPI()
    app.include_router(router_cognitive.router)

    with TestClient(app) as client:
        response = client.get("/api/cursor/jobs", params={"status": "exploded"})

    assert response.status_code == 400
    assert "statut invalide" in response.json()["detail"]


def test_briefing_filter_priority_is_literal_enum():
    from api.router_cognitive import BriefingRequest

    BriefingRequest(filter_priority="urgent")
    with pytest.raises(ValidationError):
        BriefingRequest(filter_priority="sometime")
