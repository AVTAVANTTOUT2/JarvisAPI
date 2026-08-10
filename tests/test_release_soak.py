from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools.run_release_soak import (
    Probe,
    _parse_probe,
    _runtime_defaults,
    probe_once,
    run_campaign,
    summarize,
)


class _Response:
    status = 200

    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def getcode(self) -> int:
        return self.status

    def read(self) -> bytes:
        return json.dumps(self._payload).encode()


def test_runtime_defaults_follow_local_supervisor_tls(tmp_path, monkeypatch) -> None:
    cert = tmp_path / "cert.pem"
    monkeypatch.setitem(
        sys.modules,
        "config",
        SimpleNamespace(WEB_USE_HTTPS=True, SUPERVISOR_PORT=9443, SSL_CERT_PATH=cert),
    )

    probes, ca_file = _runtime_defaults()

    assert probes == (
        ("backend_liveness", "https://localhost:9443/api/auth/status"),
    )
    assert ca_file == cert


def test_probe_keeps_only_public_supervisor_facts() -> None:
    def opener(_request, *, timeout):
        assert timeout == 2.0
        return _Response({
            "supervisor": {"uptime_s": 42, "backend_restart_count": 1, "pid": 999},
            "services": [
                {"id": "backend", "status": "running", "secret": "never-store"},
                {"id": "audio", "status": "degraded"},
            ],
            "private": "never-store",
        })

    ticks = iter((10.0, 10.025))
    result = probe_once(
        Probe("supervisor_status", "http://127.0.0.1/status"),
        timeout_s=2.0,
        opener=opener,
        monotonic=lambda: next(ticks),
    )

    assert result == {
        "name": "supervisor_status",
        "ok": True,
        "elapsed_ms": 25.0,
        "facts": {
            "uptime_s": 42,
            "backend_restart_count": 1,
            "service_count": 2,
            "unhealthy_services": 1,
        },
    }
    assert "private" not in json.dumps(result)
    assert "secret" not in json.dumps(result)


def test_summary_counts_failed_samples_and_latency() -> None:
    samples = [
        {"results": [{"name": "status", "ok": True, "elapsed_ms": 10}]},
        {"results": [{"name": "status", "ok": False, "elapsed_ms": 20}]},
        {"results": [{"name": "status", "ok": True, "elapsed_ms": 30}]},
    ]

    report = summarize(samples)

    assert report["sample_count"] == 3
    assert report["failed_samples"] == 1
    assert report["probes"]["status"] == {
        "samples": 3,
        "successes": 2,
        "failures": 1,
        "latency_ms": {"median": 20.0, "p95": 30.0, "max": 30.0},
    }


def test_once_campaign_writes_evidence_and_enforces_failure_budget(tmp_path: Path) -> None:
    output = tmp_path / "soak.json"

    def failed_probe(probe, *, timeout_s):
        assert timeout_s == 1.0
        return {"name": probe.name, "ok": False, "elapsed_ms": 1.0, "error": "offline"}

    exit_code = run_campaign(
        probes=(Probe("status", "http://127.0.0.1/status"),),
        duration_s=3600,
        interval_s=30,
        timeout_s=1.0,
        output=output,
        max_failed_samples=0,
        once=True,
        probe_runner=failed_probe,
    )

    payload = json.loads(output.read_text())
    assert exit_code == 1
    assert payload["schema_version"] == 1
    assert payload["summary"]["failed_samples"] == 1
    assert payload["samples"][0]["results"][0]["error"] == "offline"


def test_parse_probe_rejects_ambiguous_values() -> None:
    assert _parse_probe("status=http://127.0.0.1/status") == Probe(
        "status", "http://127.0.0.1/status",
    )
    with pytest.raises(argparse.ArgumentTypeError, match="format attendu"):
        _parse_probe("missing-url")
