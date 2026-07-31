"""Tests du suivi des jobs APScheduler (table + API + tracking)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.conftest import authenticate


@pytest.mark.asyncio
async def test_tracked_job_persists_ok_and_error(tmp_path, monkeypatch):
    import config
    import database
    from database import init_db
    from database.scheduler_runs import list_scheduler_runs
    from scripts.scheduler_tracking import ok, tracked

    db_path = tmp_path / "sched.db"
    monkeypatch.setattr(config, "DB_PATH", str(db_path))
    monkeypatch.setattr(database, "DB_PATH", db_path)
    init_db()

    @tracked("unit_ok")
    async def ok_job():
        return ok("bonjour")

    @tracked("unit_err")
    async def boom_job():
        raise RuntimeError("boom")

    await ok_job()
    await boom_job()

    ok_runs = list_scheduler_runs(job_id="unit_ok", days=7)
    err_runs = list_scheduler_runs(job_id="unit_err", days=7)
    assert len(ok_runs) == 1, ok_runs
    assert ok_runs[0]["status"] == "ok"
    assert ok_runs[0]["output"] == "bonjour"
    assert ok_runs[0]["trigger"] == "cron"
    assert len(err_runs) == 1, err_runs
    assert err_runs[0]["status"] == "error"
    assert "boom" in (err_runs[0]["error"] or "")


def test_scheduler_api_lists_catalog_and_blocks_frequent_manual(tmp_path, monkeypatch):
    import config
    import database
    from database import init_db
    import main

    db_path = tmp_path / "api_sched.db"
    monkeypatch.setattr(config, "DB_PATH", str(db_path))
    monkeypatch.setattr(database, "DB_PATH", db_path)
    init_db()

    with TestClient(main.app) as client:
        authenticate(client)
        listed = client.get("/api/scheduler/jobs?days=7")
        assert listed.status_code == 200
        payload = listed.json()
        assert payload["days"] == 7
        assert isinstance(payload["jobs"], list)
        assert len(payload["jobs"]) >= 20
        ids = {job["job_id"] for job in payload["jobs"]}
        assert "db_backup" in ids
        assert "presence_tick" in ids
        backup = next(j for j in payload["jobs"] if j["job_id"] == "db_backup")
        assert backup["manual_run"] is True
        presence = next(j for j in payload["jobs"] if j["job_id"] == "presence_tick")
        assert presence["manual_run"] is False
        assert presence["cadence"] == "frequent"

        blocked = client.post("/api/scheduler/jobs/presence_tick/run")
        assert blocked.status_code == 403

        unknown = client.get("/api/scheduler/jobs/does_not_exist/runs")
        assert unknown.status_code == 404


def test_scheduler_route_requires_auth(tmp_path, monkeypatch):
    import config
    import database
    from database import init_db
    import main

    db_path = tmp_path / "api_sched_locked.db"
    monkeypatch.setattr(config, "DB_PATH", str(db_path))
    monkeypatch.setattr(database, "DB_PATH", db_path)
    init_db()

    with TestClient(main.app) as client:
        response = client.get("/api/scheduler/jobs")
        assert response.status_code in {401, 403, 428}
