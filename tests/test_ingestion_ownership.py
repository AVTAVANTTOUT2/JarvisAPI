"""Un seul propriétaire runtime pour les connecteurs et workers d'ingestion."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_web_lifespan_starts_no_ingestion_worker_or_apple_watcher() -> None:
    source = (ROOT / "api" / "lifespan.py").read_text(encoding="utf-8")
    forbidden = (
        "run_knowledge_worker",
        "email_watcher.start",
        "imessage_reader.periodic_scan",
        "scripts/imessage_daemon.py",
        "subprocess.Popen",
    )
    assert [token for token in forbidden if token in source] == []


def test_launchagent_owns_the_single_ingestion_entrypoint() -> None:
    from scripts.launchagents import build_launch_agent_payloads

    payloads = build_launch_agent_payloads(
        repo_root=ROOT,
        venv_dir=ROOT / ".venv",
    )
    ingestion = payloads["ingestion"]
    assert ingestion["Label"] == "com.jarvis.ingestion"
    assert ingestion["ProgramArguments"][1] == str(
        ROOT / "scripts" / "ingestion_service.py"
    )
    assert "imessage-daemon" not in payloads


def test_voice_daemon_does_not_poll_owned_ingestion_sources() -> None:
    source = (ROOT / "scripts" / "jarvis_daemon.py").read_text(encoding="utf-8")
    scheduled = (
        'self._notification_loop(), name="daemon_notif"',
        'self._calendar_reminder_loop(), name="daemon_calendar"',
    )
    assert [token for token in scheduled if token in source] == []


def test_ingestion_service_owns_knowledge_maintenance() -> None:
    source = (ROOT / "jarvis" / "ingestion" / "service.py").read_text(encoding="utf-8")
    assert "run_knowledge_maintenance_once" in source


def test_api_cannot_restart_the_legacy_email_watcher() -> None:
    service_control = (ROOT / "api" / "service_control.py").read_text()
    misc_integrations = (ROOT / "api" / "misc_integrations.py").read_text()

    assert "_ew.start()" not in service_control
    assert "_ew.stop()" not in service_control
    assert "email_watcher.run_catchup_cycle" not in misc_integrations
    assert "request_ingestion_freshness" in misc_integrations


def test_manual_catchup_only_enqueues_durable_ingestion() -> None:
    source = (ROOT / "scripts" / "catchup_after_downtime.py").read_text(
        encoding="utf-8"
    )

    assert "request_ingestion_freshness" in source
    assert "EmailWatcher" not in source
    assert "run_force_full_mac_sync" not in source
    assert "run_daily_update" not in source
