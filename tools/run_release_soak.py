#!/usr/bin/env python3
"""Campagne de stabilité locale pour la validation release JARVIS.

Le script sonde uniquement des endpoints de diagnostic explicitement fournis et
ne conserve jamais leur réponse complète. Le rapport JSON contient les temps de
réponse, les erreurs bornées et quelques faits publics utiles à une preuve 24 h.
"""

from __future__ import annotations

import argparse
import functools
import json
import math
import ssl
import statistics
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Iterable

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = ROOT / "artifacts" / "release_soak.json"
DEFAULT_PROBES = (
    ("backend_liveness", "http://127.0.0.1:9000/api/auth/status"),
)


@dataclass(frozen=True)
class Probe:
    name: str
    url: str


def _runtime_defaults() -> tuple[tuple[tuple[str, str], ...], Path | None]:
    """Aligne les sondes par défaut sur le port et le TLS du déploiement local."""
    root_text = str(ROOT)
    if root_text not in sys.path:
        sys.path.insert(0, root_text)
    try:
        import config as jarvis_config
    except (ImportError, RuntimeError, ValueError):
        return DEFAULT_PROBES, None

    scheme = "https" if jarvis_config.WEB_USE_HTTPS else "http"
    base = f"{scheme}://localhost:{jarvis_config.SUPERVISOR_PORT}"
    probes = (
        ("backend_liveness", f"{base}/api/auth/status"),
    )
    ca_file = Path(jarvis_config.SSL_CERT_PATH) if scheme == "https" else None
    return probes, ca_file


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _bounded_error(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"[:300]


def _public_facts(name: str, payload: Any) -> dict[str, Any]:
    """Extrait uniquement les indicateurs publics nécessaires au soak."""
    if not isinstance(payload, dict):
        return {}
    if name == "supervisor_status":
        supervisor = payload.get("supervisor")
        services = payload.get("services")
        facts: dict[str, Any] = {}
        if isinstance(supervisor, dict):
            for key in ("uptime_s", "backend_restart_count"):
                value = supervisor.get(key)
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    facts[key] = value
        if isinstance(services, list):
            facts["service_count"] = len(services)
            facts["unhealthy_services"] = sum(
                1
                for service in services
                if isinstance(service, dict)
                and service.get("status") not in {"running", "healthy", "ok"}
            )
        return facts
    if name == "supervisor_resources":
        facts = {}
        for key in ("level", "enabled", "read_only", "dry_run"):
            value = payload.get(key)
            if isinstance(value, (str, bool, int, float)):
                facts[key] = value
        processes = payload.get("processes")
        actions = payload.get("actions")
        if isinstance(processes, list):
            facts["process_count"] = len(processes)
        if isinstance(actions, list):
            facts["planned_action_count"] = len(actions)
        return facts
    return {}


def probe_once(
    probe: Probe,
    *,
    timeout_s: float,
    ssl_context: ssl.SSLContext | None = None,
    opener: Callable[..., Any] = urllib.request.urlopen,
    monotonic: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    started = monotonic()
    try:
        request = urllib.request.Request(
            probe.url,
            headers={"Accept": "application/json", "User-Agent": "jarvis-release-soak/1"},
        )
        open_kwargs: dict[str, Any] = {"timeout": timeout_s}
        if ssl_context is not None:
            open_kwargs["context"] = ssl_context
        with opener(request, **open_kwargs) as response:
            response_status = getattr(response, "status", None)
            status = int(response_status if response_status is not None else response.getcode())
            payload = json.loads(response.read().decode("utf-8"))
        if status < 200 or status >= 300:
            raise RuntimeError(f"HTTP {status}")
        return {
            "name": probe.name,
            "ok": True,
            "elapsed_ms": round((monotonic() - started) * 1000, 1),
            "facts": _public_facts(probe.name, payload),
        }
    except (OSError, RuntimeError, ValueError, urllib.error.URLError) as exc:
        return {
            "name": probe.name,
            "ok": False,
            "elapsed_ms": round((monotonic() - started) * 1000, 1),
            "error": _bounded_error(exc),
        }


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(percentile * len(ordered)) - 1))
    return round(ordered[index], 1)


def summarize(samples: Iterable[dict[str, Any]]) -> dict[str, Any]:
    sample_list = list(samples)
    by_probe: dict[str, list[dict[str, Any]]] = {}
    for sample in sample_list:
        for result in sample.get("results", []):
            if isinstance(result, dict) and isinstance(result.get("name"), str):
                by_probe.setdefault(result["name"], []).append(result)

    probes: dict[str, Any] = {}
    for name, results in by_probe.items():
        latencies = [
            float(result["elapsed_ms"])
            for result in results
            if result.get("ok") and isinstance(result.get("elapsed_ms"), (int, float))
        ]
        probes[name] = {
            "samples": len(results),
            "successes": sum(result.get("ok") is True for result in results),
            "failures": sum(result.get("ok") is not True for result in results),
            "latency_ms": {
                "median": round(statistics.median(latencies), 1) if latencies else None,
                "p95": _percentile(latencies, 0.95),
                "max": round(max(latencies), 1) if latencies else None,
            },
        }

    failed_samples = sum(
        any(result.get("ok") is not True for result in sample.get("results", []))
        for sample in sample_list
    )
    return {
        "sample_count": len(sample_list),
        "failed_samples": failed_samples,
        "probes": probes,
    }


def _write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _parse_probe(value: str) -> Probe:
    name, separator, url = value.partition("=")
    if not separator or not name.strip() or not url.startswith(("http://", "https://")):
        raise argparse.ArgumentTypeError("format attendu: nom=http://hôte/chemin")
    return Probe(name.strip(), url)


def run_campaign(
    *,
    probes: tuple[Probe, ...],
    duration_s: float,
    interval_s: float,
    timeout_s: float,
    output: Path,
    max_failed_samples: int,
    once: bool,
    probe_runner: Callable[..., dict[str, Any]] = probe_once,
    monotonic: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
) -> int:
    started_at = _utc_now()
    started = monotonic()
    deadline = started + duration_s
    samples: list[dict[str, Any]] = []

    while True:
        sample = {
            "timestamp": _utc_now(),
            "results": [probe_runner(probe, timeout_s=timeout_s) for probe in probes],
        }
        samples.append(sample)
        report = {
            "schema_version": 1,
            "started_at": started_at,
            "finished_at": _utc_now(),
            "requested_duration_s": duration_s,
            "elapsed_s": round(monotonic() - started, 1),
            "interval_s": interval_s,
            "summary": summarize(samples),
            "samples": samples,
        }
        _write_report(output, report)
        if once or monotonic() >= deadline:
            break
        sleeper(min(interval_s, max(0.0, deadline - monotonic())))

    failures = int(report["summary"]["failed_samples"])
    return 0 if failures <= max_failed_samples else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--duration-hours", type=float, default=24.0)
    parser.add_argument("--interval-seconds", type=float, default=30.0)
    parser.add_argument("--timeout-seconds", type=float, default=5.0)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--max-failed-samples", type=int, default=0)
    parser.add_argument(
        "--ca-file",
        type=Path,
        help="Certificat CA pour les sondes HTTPS (détecté automatiquement pour JARVIS)",
    )
    parser.add_argument("--once", action="store_true", help="Exécute un seul relevé")
    parser.add_argument(
        "--probe",
        action="append",
        type=_parse_probe,
        help="Sonde additionnelle ou de remplacement: nom=http://hôte/chemin",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.duration_hours <= 0 or args.interval_seconds <= 0 or args.timeout_seconds <= 0:
        raise SystemExit("durées et intervalle doivent être strictement positifs")
    if args.max_failed_samples < 0:
        raise SystemExit("--max-failed-samples doit être positif ou nul")
    default_probes, default_ca_file = _runtime_defaults()
    probes = tuple(args.probe or (Probe(name, url) for name, url in default_probes))
    ca_file = args.ca_file or default_ca_file
    ssl_context = ssl.create_default_context(cafile=str(ca_file)) if ca_file else None
    probe_runner = functools.partial(probe_once, ssl_context=ssl_context)
    try:
        return run_campaign(
            probes=probes,
            duration_s=args.duration_hours * 3600,
            interval_s=args.interval_seconds,
            timeout_s=args.timeout_seconds,
            output=args.output,
            max_failed_samples=args.max_failed_samples,
            once=args.once,
            probe_runner=probe_runner,
        )
    except KeyboardInterrupt:
        print("[release-soak] interrompu par l'utilisateur", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
