"""Tests : contrat de santé — sonde publique, diagnostic authentifié, agrégation.

Ce fichier verrouille quatre propriétés du lot Health Dashboard :

1. la sonde de vie répond sans session **et** ne dit rien de plus que « ok » ;
2. le diagnostic détaillé suit le verrou de session existant ;
3. une panne partielle produit ``degraded`` sans empêcher de lire les autres
   composants ;
4. aucune raison hors vocabulaire, aucun chemin, aucun contenu d'exception ne
   franchit la frontière HTTP.
"""

from __future__ import annotations

import asyncio
import re
import sqlite3
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from jarvis import health  # noqa: E402
from tests.conftest import TEST_AUTH_SECRET, authenticate  # noqa: E402


@pytest.fixture
def tmp_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    db_path = tmp_path / "test_jarvis.db"
    monkeypatch.setattr("config.DB_PATH", str(db_path))
    monkeypatch.setattr("database.DB_PATH", db_path)
    monkeypatch.setattr("config.AUTH_PROGRESSIVE_DELAY_SECONDS", 0)
    monkeypatch.setattr("config.CSRF_ALLOWED_ORIGINS", "")
    from database import init_db

    init_db()
    return db_path


@pytest.fixture(autouse=True)
def _fresh_health_cache():
    """Le relevé est partagé : deux tests ne doivent pas se lire l'un l'autre."""
    health.reset_cache()
    yield
    health.reset_cache()


def _client():
    import main
    from fastapi.testclient import TestClient

    return TestClient(main.app)


# ── Sonde de vie publique ────────────────────────────────────


def test_liveness_answers_without_any_session(tmp_db):
    with _client() as client:
        response = client.get("/api/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_liveness_answers_even_before_the_lock_is_configured(tmp_db):
    """Un 428 dirait « pas configuré », pas « vivant » — c'est le but de la sonde."""
    import auth

    assert not auth.is_configured()
    with _client() as client:
        response = client.get("/api/health/live")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_liveness_payload_stays_minimal(tmp_db):
    """Aucune version, aucun hôte, aucun composant, aucun compteur."""
    with _client() as client:
        response = client.get("/api/health/live")

    payload = response.json()
    assert list(payload) == ["status"]
    assert response.headers["cache-control"] == "no-store"


def test_liveness_never_touches_the_database(tmp_db):
    """La sonde doit répondre même quand la base est en panne : c'est son rôle."""
    from unittest.mock import patch

    def _explode():  # pragma: no cover - ne doit jamais être appelé
        raise AssertionError("la sonde de vie a ouvert la base")

    # Le remplacement n'est armé qu'après le démarrage applicatif : `init_db()`
    # du lifespan ouvre légitimement la base, la sonde ne doit pas le faire.
    with _client() as client:
        with patch("database.core.get_connection", _explode):
            response = client.get("/api/health/live")

    assert response.status_code == 200


# ── Diagnostic authentifié ───────────────────────────────────


def test_only_the_liveness_path_is_public(tmp_db):
    """Le préfixe `/api/health` n'ouvre rien : seule l'égalité exacte passe."""
    from api.middleware import _bypasses_session_gate

    assert _bypasses_session_gate("GET", "/api/health/live")
    assert not _bypasses_session_gate("GET", "/api/health/detail")
    assert not _bypasses_session_gate("GET", "/api/health/live/../detail")
    assert not _bypasses_session_gate("POST", "/api/health/live")


def test_detail_requires_configuration(tmp_db):
    with _client() as client:
        response = client.get("/api/health/detail")

    assert response.status_code == 428
    assert response.json()["error"] == "setup_required"


def test_detail_requires_a_session(tmp_db):
    import auth

    auth.setup_secret(TEST_AUTH_SECRET)
    with _client() as client:
        response = client.get("/api/health/detail")

    assert response.status_code == 401
    assert "components" not in response.json()


def test_detail_returns_the_full_contract_once_authenticated(tmp_db):
    with _client() as client:
        authenticate(client)
        response = client.get("/api/health/detail")

    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {
        "status",
        "checked_at",
        "duration_ms",
        "summary",
        "components",
    }
    assert payload["status"] in health.VALID_STATES
    assert set(payload["summary"]) == set(health.VALID_STATES)

    names = [component["name"] for component in payload["components"]]
    assert names == [name for name, _ in health.PROBES]
    for component in payload["components"]:
        assert set(component) == {"name", "state", "critical", "reason", "details"}
        assert component["state"] in health.VALID_STATES
        assert component["reason"] is None or component["reason"] in health.PUBLIC_REASONS

    assert response.headers["cache-control"] == "no-store"


def test_detail_reports_backend_and_database_as_healthy(tmp_db):
    with _client() as client:
        authenticate(client)
        payload = client.get("/api/health/detail").json()

    states = {c["name"]: c["state"] for c in payload["components"]}
    assert states["backend"] == health.HEALTHY
    assert states["database"] == health.HEALTHY


def test_detail_refresh_bypasses_the_shared_snapshot(tmp_db, monkeypatch):
    calls = {"n": 0}
    real_collect = health.collect_health

    async def _counting_collect():
        calls["n"] += 1
        return await real_collect()

    monkeypatch.setattr(health, "collect_health", _counting_collect)

    with _client() as client:
        authenticate(client)
        client.get("/api/health/detail")
        client.get("/api/health/detail")
        assert calls["n"] == 1, "le relevé partagé doit servir le second appel"
        client.get("/api/health/detail?refresh=true")

    assert calls["n"] == 2


def test_detail_answers_503_when_a_critical_component_is_down(tmp_db, monkeypatch):
    def _broken_database() -> health.ComponentHealth:
        return health.ComponentHealth(
            name="database",
            state=health.UNAVAILABLE,
            reason="database_unreachable",
        )

    monkeypatch.setattr(
        health,
        "PROBES",
        tuple(
            (name, _broken_database if name == "database" else probe)
            for name, probe in health.PROBES
        ),
    )

    with _client() as client:
        authenticate(client)
        response = client.get("/api/health/detail")

    assert response.status_code == 503
    payload = response.json()
    assert payload["status"] == health.UNAVAILABLE
    # La panne d'un composant ne doit pas empêcher de diagnostiquer les autres.
    assert len(payload["components"]) == len(health.PROBES)
    assert any(c["name"] == "backend" and c["state"] == health.HEALTHY for c in payload["components"])


# ── Absence de fuite ─────────────────────────────────────────


def test_database_failure_never_leaks_the_exception_or_the_path(tmp_db, monkeypatch):
    secret_path = "/Users/nolann/Secrets/jarvis.db"

    def _explode():
        raise sqlite3.OperationalError(f"unable to open database file {secret_path}")

    monkeypatch.setattr("database.core.get_connection", _explode)

    component = health.probe_database()

    assert component.state == health.UNAVAILABLE
    assert component.reason == "database_query_failed"
    assert secret_path not in str(component.to_public_dict())


def test_probe_crash_is_reported_as_unknown_not_healthy():
    def _explode() -> health.ComponentHealth:
        raise RuntimeError("token=sk-live-should-never-surface")

    component = asyncio.run(health._run_probe("resources", _explode))

    assert component.state == health.UNKNOWN
    assert component.reason == "internal_error"
    assert "sk-live" not in str(component.to_public_dict())


def test_probe_timeout_is_reported_as_unknown(monkeypatch):
    monkeypatch.setattr(health, "PROBE_TIMEOUT_S", 0.01)

    def _slow() -> health.ComponentHealth:
        import time

        time.sleep(0.5)
        return health.ComponentHealth(name="resources", state=health.HEALTHY)

    component = asyncio.run(health._run_probe("resources", _slow))

    assert component.state == health.UNKNOWN
    assert component.reason == "probe_timeout"


def test_unknown_reason_codes_are_replaced():
    assert health.public_reason("database_unreachable") == "database_unreachable"
    assert health.public_reason("/Users/nolann/data/jarvis.db manquant") == "internal_error"
    assert health.public_reason(None) is None


def test_details_keep_only_short_scalars():
    cleaned = health.public_details(
        {
            "free_mb": 512.0,
            "engine": "x" * 500,
            "processes": [{"pid": 42, "cmdline": "/Users/nolann/venv/bin/python"}],
            "loop_bound": True,
            "nothing": None,
            "unexpected_secret": "sk-live-should-never-surface",
            42: "clé non textuelle",
        }
    )

    assert cleaned["free_mb"] == 512.0
    assert len(cleaned["engine"]) == 120
    assert cleaned["loop_bound"] is True
    assert "nothing" not in cleaned
    assert "processes" not in cleaned
    assert "unexpected_secret" not in cleaned
    assert 42 not in cleaned


def test_details_drop_private_paths_even_under_an_allowed_key():
    cleaned = health.public_details({"engine": "/Users/nolann/private/engine"})

    assert "engine" not in cleaned


def test_text_to_speech_never_exposes_model_or_voice_paths(tmp_db):
    component = health.probe_text_to_speech()

    assert component.state in (health.UNKNOWN, health.UNAVAILABLE)
    rendered = str(component.to_public_dict())
    # Les identifiants de bibliothèque (« mlx-audio/qwen3_tts ») sont des noms,
    # pas des chemins : ce qui est interdit ici, c'est un chemin de machine.
    assert not re.search(r"(?:^|[\s'\"])[~/]", rendered)
    assert "model" not in component.details
    assert "voice" not in component.details


# ── Agrégation ───────────────────────────────────────────────


def _c(name: str, state: str) -> health.ComponentHealth:
    return health.ComponentHealth(name=name, state=state)


def test_aggregate_all_healthy():
    assert health.aggregate_state([_c("backend", health.HEALTHY), _c("database", health.HEALTHY)]) == health.HEALTHY
    assert (
        health.aggregate_state(
            [
                _c("backend", health.HEALTHY),
                _c("database", health.HEALTHY),
                _c("claw3d", health.UNKNOWN),
                _c("agentic_plugin", health.UNKNOWN),
            ]
        )
        == health.HEALTHY
    )


def test_aggregate_partial_failure_is_degraded_not_unavailable():
    components = [
        _c("backend", health.HEALTHY),
        _c("database", health.HEALTHY),
        _c("speech_to_text", health.UNAVAILABLE),
    ]
    assert health.aggregate_state(components) == health.DEGRADED


def test_aggregate_critical_failure_is_unavailable():
    components = [_c("backend", health.HEALTHY), _c("database", health.UNAVAILABLE)]
    assert health.aggregate_state(components) == health.UNAVAILABLE


def test_aggregate_unknown_critical_component_is_degraded():
    components = [_c("backend", health.HEALTHY), _c("database", health.UNKNOWN)]
    assert health.aggregate_state(components) == health.DEGRADED


def test_aggregate_unknown_optional_component_does_not_fake_a_failure():
    """Ne rien avoir mesuré n'est pas une panne — mais reste compté à part."""
    components = [
        _c("backend", health.HEALTHY),
        _c("database", health.HEALTHY),
        _c("text_to_speech", health.UNKNOWN),
    ]
    assert health.aggregate_state(components) == health.HEALTHY
    assert health.summarize(components)[health.UNKNOWN] == 1


def test_aggregate_without_components_is_unknown():
    assert health.aggregate_state([]) == health.UNKNOWN


def test_no_component_defaults_to_healthy_without_evidence():
    """Chaque sonde doit décider ; aucune ne renvoie `healthy` par construction."""
    component = health.ComponentHealth(name="resources", state="bidon")
    assert component.to_public_dict()["state"] == health.UNKNOWN


def test_resources_reuses_the_guard_thresholds(monkeypatch):
    import config

    monkeypatch.setattr(config, "RESOURCE_GUARD_ENABLED", True)
    monkeypatch.setattr(config, "RESOURCE_GUARD_WARN_FREE_MB", 2048)
    monkeypatch.setattr(config, "RESOURCE_GUARD_CRITICAL_FREE_MB", 1024)
    monkeypatch.setattr("jarvis.resource_guard.read_memory_free_mb", lambda: 1500.0)

    component = health.probe_resources()

    assert component.state == health.DEGRADED
    assert component.reason == "memory_low"
    assert component.details["free_mb"] == 1500.0
    # L'inventaire des process reste l'affaire de /api/supervisor/resources.
    assert "processes" not in component.details


def test_resources_without_measurement_is_unknown_not_healthy(monkeypatch):
    import config

    monkeypatch.setattr(config, "RESOURCE_GUARD_ENABLED", True)
    monkeypatch.setattr("jarvis.resource_guard.read_memory_free_mb", lambda: None)

    component = health.probe_resources()

    assert component.state == health.UNKNOWN
    assert component.reason == "memory_probe_unavailable"


def test_resources_disabled_guard_is_unknown(monkeypatch):
    import config

    monkeypatch.setattr(config, "RESOURCE_GUARD_ENABLED", False)

    component = health.probe_resources()

    assert component.state == health.UNKNOWN
    assert component.reason == "resource_guard_disabled"


def test_event_bus_probe_reports_an_unbound_loop(monkeypatch):
    from jarvis.event_bus import event_bus

    monkeypatch.setattr(event_bus, "_loop", None, raising=False)

    component = health.probe_event_bus()

    assert component.state == health.DEGRADED
    assert component.reason == "event_bus_loop_unbound"
    assert component.details["subscribers"] >= 0


def test_optional_runtime_probes_never_mark_jarvis_unavailable(tmp_path, monkeypatch):
    monkeypatch.setattr(health, "_PROJECT_DIR", tmp_path)

    core = health.probe_agentic_core()
    plugin = health.probe_agentic_plugin()
    claw = health.probe_claw3d()

    assert core.state == health.HEALTHY
    assert plugin.state == health.UNKNOWN
    assert plugin.reason in {"optional_runtime_absent", "runtime_not_probed"}
    assert claw.state == health.UNKNOWN
    assert claw.reason == "optional_ui_absent"
    assert health.UNAVAILABLE not in {plugin.state, claw.state}
    assert health.aggregate_state([core, plugin, claw, _c("backend", health.HEALTHY), _c("database", health.HEALTHY)]) == health.HEALTHY
