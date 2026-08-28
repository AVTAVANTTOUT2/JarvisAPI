"""Tests unitaires du script d'audit architecture (non destructif)."""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
sys.path.insert(0, str(TOOLS))

import audit_architecture_truth as audit  # noqa: E402


@pytest.fixture()
def fake_repo(tmp_path: Path) -> Path:
    """Mini dépôt synthétique pour tester la découverte sans toucher prod."""
    # frontend Next
    fe = tmp_path / "frontend"
    (fe / "public").mkdir(parents=True)
    (fe / "out" / "_next" / "static").mkdir(parents=True)
    (fe / "package.json").write_text(
        json.dumps(
            {
                "dependencies": {
                    "next": "15.5.20",
                    "react": "^19.2.5",
                    "react-dom": "^19.2.5",
                },
                "scripts": {"dev": "next dev", "build": "next build"},
            }
        ),
        encoding="utf-8",
    )
    (fe / "pnpm-lock.yaml").write_text(
        "importers:\n\n  .:\n    dependencies:\n"
        "      next:\n        specifier: 15.5.20\n        version: 15.5.20\n"
        "      react:\n        specifier: ^19.2.5\n        version: 19.2.7\n"
        "      react-dom:\n        specifier: ^19.2.5\n        version: 19.2.7\n",
        encoding="utf-8",
    )
    (fe / "public" / "sw.js").write_text("// sw\n", encoding="utf-8")
    (fe / "public" / "manifest.webmanifest").write_text("{}", encoding="utf-8")
    (fe / "out" / "index.html").write_text("<html></html>", encoding="utf-8")
    (fe / "src").mkdir()
    (fe / "src" / "api.ts").write_text(
        "fetch('/api/status'); fetch(`/api/jobs/${jobId}`);\n"
        "const operatorPolicy = '/api/operator' // architecture-audit: non-consumer-reference\n",
        encoding="utf-8",
    )

    # bibliothèque de vues desktop (compilée uniquement par Next)
    web = tmp_path / "web"
    (web / "src").mkdir(parents=True)
    (web / "package.json").write_text(
        json.dumps(
            {
                "dependencies": {"react": "^19.0.0", "react-dom": "^19.0.0"},
                "devDependencies": {"typescript": "^5.8.0", "vitest": "^4.1.10"},
                "scripts": {"typecheck": "tsc --noEmit", "test": "vitest run"},
            }
        ),
        encoding="utf-8",
    )

    # schema sources
    db = tmp_path / "database"
    db.mkdir()
    (db / "schema.py").write_text(
        "SCHEMA = '''CREATE TABLE IF NOT EXISTS episodes (id INTEGER);\n"
        "CREATE TABLE IF NOT EXISTS conversations (id INTEGER);'''\n",
        encoding="utf-8",
    )
    (db / "schema.sql").write_text(
        "CREATE TABLE episodes (id INTEGER);\nCREATE TABLE sqlite_sequence(name,seq);\n",
        encoding="utf-8",
    )
    (db / "migrations.py").write_text(
        "CREATE TABLE IF NOT EXISTS sessions (id TEXT);\n"
        "CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(content);\n",
        encoding="utf-8",
    )
    (db / "devagent.py").write_text(
        "CREATE TABLE IF NOT EXISTS dev_projects (id INTEGER);\n",
        encoding="utf-8",
    )
    (db / "core.py").write_text(
        "from .schema import SCHEMA\ndef init_db():\n    run_migrations(conn)\n",
        encoding="utf-8",
    )

    (tmp_path / "api").mkdir()
    (tmp_path / "api" / "frontend.py").write_text(
        "from core.frontend_resolution import is_usable_next_build, resolve_desktop_frontend_roots\n"
        "def _setup_frontend(app):\n"
        "    if desktop.kind == 'next_canonical' and _setup_unified_frontend(app):\n"
        "        return\n",
        encoding="utf-8",
    )
    (tmp_path / "api" / "router_jobs.py").write_text(
        "from fastapi import APIRouter\n"
        "EVENTS_PATH = '/ws/events'\n"
        "router = APIRouter(prefix='/api')\n"
        "@router.post('/jobs/{job_id}')\n"
        "def run_job(): ...\n"
        "router.add_api_route('/operator', lambda: None, methods=['GET'])\n",
        encoding="utf-8",
    )
    (tmp_path / "main.py").write_text(
        "from fastapi import FastAPI\n"
        "from api.router_jobs import EVENTS_PATH\n"
        "app = FastAPI()\n"
        "@app.get('/api/status')\n"
        "def status(): ...\n"
        "async def ws_handler(ws): ...\n"
        "app.websocket('/ws')(ws_handler)\n"
        "app.websocket(EVENTS_PATH)(ws_handler)\n",
        encoding="utf-8",
    )
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_api.py").write_text(
        "client.get('/api/status')\n"
        "client.post('/api/jobs/42')\n"
        "client.get('/api/operator')\n",
        encoding="utf-8",
    )
    android_main = tmp_path / "android" / "app" / "src" / "main"
    android_main.mkdir(parents=True)
    (android_main / "Api.kt").write_text(
        '@POST("api/jobs/{job_id}")\nfun runJob()\n',
        encoding="utf-8",
    )
    native_mac = tmp_path / "native_mac" / "JarvisMac"
    native_mac.mkdir(parents=True)
    (native_mac / "Api.swift").write_text(
        'let statusURL = "/api/status"\n',
        encoding="utf-8",
    )
    (tmp_path / "core").mkdir()
    (tmp_path / "core" / "frontend_resolution.py").write_text(
        "def is_usable_next_build(p): ...\n"
        "next_canonical = 'next_canonical'\n"
        "def resolve_desktop_frontend(p): ...\n",
        encoding="utf-8",
    )
    (tmp_path / "supervisor.py").write_text(
        "from core.frontend_resolution import resolve_desktop_frontend\n"
        "from core.frontend_static import register_desktop_frontend_routes\n"
        "FRONTEND_RESOLUTION = resolve_desktop_frontend(PROJECT_DIR)\n"
        "register_desktop_frontend_routes(app, FRONTEND_RESOLUTION)\n"
        "# frontend/out uniquement\n",
        encoding="utf-8",
    )
    (tmp_path / "tv").mkdir()
    (tmp_path / "tv" / "server.py").write_text("print('tv')\n", encoding="utf-8")
    (tmp_path / "README.md").write_text(
        "26+ tables SQLite\n72 tables\n`web/` (SPA principale, Vite + React)\n",
        encoding="utf-8",
    )
    return tmp_path


def test_extract_create_tables_ignores_noise() -> None:
    text = (
        "CREATE TABLE IF NOT EXISTS foo (id);\ncreate virtual table bar using fts5(x);"
    )
    assert audit._extract_create_tables(text) == ["bar", "foo"]


def test_discover_frontends_classifies_projects(fake_repo: Path) -> None:
    projects = {p.path: p for p in audit.discover_frontends(fake_repo)}
    assert projects["frontend"].status == "actif_canonique_fastapi"
    assert projects["frontend"].locked_versions["next"] == "15.5.20"
    assert projects["frontend"].locked_versions["react"] == "19.2.7"
    assert projects["frontend"].output_present is True
    assert projects["web"].status == "bibliotheque_vues_desktop"
    assert projects["web"].output_dir is None
    assert projects["web"].has_service_worker is False
    assert "pwa" not in projects
    assert projects["tv"].status == "actif_tv_5174"
    assert "front_tv" not in projects


def test_analyze_tables_counts(fake_repo: Path) -> None:
    tables = audit.analyze_tables(fake_repo)
    assert tables["counts"]["schema_py"] == 2
    assert tables["counts"]["schema_sql_applicatives"] == 1
    assert (
        tables["counts"]["persistantes_post_init"] == 4
    )  # episodes, conversations, sessions, dev_projects
    assert tables["counts"]["fts_objects_if_available"] == 5
    assert tables["counts"]["physiques_max_default_fts_on"] == 9
    assert tables["init_pipeline"]["uses_schema_py"] is True


def test_event_and_plugin_inventories_are_generated_from_sources(tmp_path: Path) -> None:
    jarvis = tmp_path / "jarvis"
    jarvis.mkdir()
    (jarvis / "event_bus.py").write_text(
        "EVENT_TYPES: tuple[str, ...] = "
        "('agent.run.started', 'task.control.completed', 'memory.updated')\n",
        encoding="utf-8",
    )
    plugin = tmp_path / "integrations" / "fixture"
    plugin.mkdir(parents=True)
    (plugin / "plugin.json").write_text(
        json.dumps(
            {
                "runtime": {
                    "id": "fixture",
                    "version": "1.2.3",
                    "entrypoint": "adapter:create_runtime",
                    "capabilities": ["read", "write"],
                }
            }
        ),
        encoding="utf-8",
    )

    events = audit.analyze_events(tmp_path)
    plugins = audit.analyze_plugins(tmp_path)

    assert events["count"] == 3
    assert events["agentic_count"] == 1
    assert events["task_control_count"] == 1
    assert plugins["count"] == 1
    assert plugins["plugins"][0] == {
        "path": "integrations/fixture/plugin.json",
        "runtime_id": "fixture",
        "version": "1.2.3",
        "enabled": True,
        "entrypoint": "adapter:create_runtime",
        "capability_count": 2,
    }


def test_api_surface_maps_routes_to_consumers_and_tests(fake_repo: Path) -> None:
    routes = audit.discover_api_routes(fake_repo)
    assert [(route.method, route.path) for route in routes] == [
        ("GET", "/api/operator"),
        ("GET", "/api/status"),
        ("POST", "/api/jobs/{job_id}"),
        ("WEBSOCKET", "/ws"),
        ("WEBSOCKET", "/ws/events"),
    ]

    surface = audit.analyze_api_surface(fake_repo)
    assert surface["counts"] == {
        "operations": 5,
        "paths": 5,
        "consumer_and_tested": 2,
        "server_only_tested": 1,
        "unreferenced": 2,
    }
    by_path = {route["path"]: route for route in surface["routes"]}
    assert by_path["/api/jobs/{job_id}"]["consumers"] == {
        "android": ["android/app/src/main/Api.kt"],
        "frontend_next": ["frontend/src/api.ts"],
    }
    assert by_path["/api/jobs/{job_id}"]["tests"] == ["tests/test_api.py"]
    assert by_path["/api/status"]["consumers"] == {
        "frontend_next": ["frontend/src/api.ts"],
        "macos": ["native_mac/JarvisMac/Api.swift"],
    }
    assert by_path["/api/operator"]["classification"] == "server_only_tested"
    assert by_path["/ws"]["classification"] == "unreferenced"


def test_api_ownership_policy_is_exact_and_rejects_client_masking(
    fake_repo: Path,
) -> None:
    policy_path = fake_repo / "Architecture" / "api_route_ownership.json"
    policy_path.parent.mkdir()
    policy = {
        "schema_version": 1,
        "rules": [
            {
                "id": "operator-tools",
                "owner": "operations",
                "audience": "operator",
                "methods": ["GET"],
                "paths": ["/api/operator"],
                "rationale": "Diagnostic manuel.",
            },
            {
                "id": "websocket-client",
                "owner": "realtime",
                "audience": "indirect-client",
                "methods": ["WEBSOCKET"],
                "paths": ["/ws", "/ws/events"],
                "rationale": "URL construite depuis l'origine serveur.",
            },
        ],
    }
    policy_path.write_text(json.dumps(policy), encoding="utf-8")

    surface = audit.analyze_api_surface(fake_repo)
    assert surface["ownership_policy"]["findings"] == []
    by_path = {route["path"]: route for route in surface["routes"]}
    assert by_path["/api/operator"]["classification"] == (
        "owned_non_frontend_and_tested"
    )
    assert by_path["/ws"]["classification"] == ("owned_non_frontend_without_path_test")

    policy["rules"].append(
        {
            "id": "masked-client",
            "owner": "operations",
            "audience": "operator",
            "methods": ["GET"],
            "paths": ["/api/status"],
            "rationale": "Cette règle est volontairement invalide.",
        }
    )
    policy_path.write_text(json.dumps(policy), encoding="utf-8")
    findings = audit.analyze_api_surface(fake_repo)["ownership_policy"]["findings"]
    assert any(
        finding["kind"] == "ownership_rule_masks_client_route" for finding in findings
    )


def test_numeric_claim_scan_rejects_new_contradictions_even_with_canonical_lines(
    fake_repo: Path,
) -> None:
    tables = audit.analyze_tables(fake_repo)
    api_surface = audit.analyze_api_surface(fake_repo)
    current = fake_repo / "CURRENT.md"
    current.write_text(
        audit._canonical_count_line(tables)
        + "\n"
        + audit._canonical_api_line(api_surface)
        + "\n"
        + audit._canonical_api_structure_line(api_surface)
        + "\n"
        + "Autre résumé courant : 999 tables persistantes et 888 tables physiques.\n"
        + "Surface API bis : 77 opérations, 66 chemins.\n"
        + "Structure bis : 55 opérations HTTP, 44 chemins OpenAPI, 12 routeurs, "
        + "main.py 175 lignes.\n",
        encoding="utf-8",
    )
    registry = {
        "documentation": {
            "current": [
                {
                    "path": "CURRENT.md",
                    "required_claims": [
                        "database",
                        "api_surface",
                        "api_structure",
                    ],
                }
            ]
        }
    }

    findings = audit.scan_numeric_claims(
        fake_repo, tables, api_surface, registry
    )
    kinds = {f["kind"] for f in findings}
    assert kinds == {
        "api_http_operations",
        "api_main_lines",
        "api_openapi_paths",
        "api_operations",
        "api_paths",
        "api_routers",
        "sqlite_persistent_tables",
        "sqlite_physical_tables",
    }
    assert all(finding["severity"] == "error" for finding in findings)


def test_numeric_claim_scan_ignores_registered_historical_snapshot(
    fake_repo: Path,
) -> None:
    historical = fake_repo / "HISTORICAL.md"
    historical.write_text(
        "Snapshot daté : 12 tables persistantes, 17 opérations HTTP.\n",
        encoding="utf-8",
    )
    registry = {
        "documentation": {
            "current": [],
            "historical": [
                {"path": "HISTORICAL.md", "snapshot_at": "2026-01-01"}
            ],
        }
    }

    assert (
        audit.scan_numeric_claims(
            fake_repo,
            audit.analyze_tables(fake_repo),
            audit.analyze_api_surface(fake_repo),
            registry,
        )
        == []
    )


def test_truth_registry_rejects_unclassified_governed_markdown(tmp_path: Path) -> None:
    architecture = tmp_path / "Architecture"
    architecture.mkdir()
    for name in (
        "28_VALIDATION_COHERENCE.md",
        "32_FRONTEND_DATABASE_SOURCE_OF_TRUTH.md",
        "GUIDE.md",
    ):
        (architecture / name).write_text(f"# {name}\n", encoding="utf-8")
    registry_path = architecture / "project_truth_registry.json"
    registry = {
        "schema_version": 1,
        "reviewed_at": "2026-08-27",
        "generated_status_document": "Architecture/28_VALIDATION_COHERENCE.md",
        "documentation": {
            "governed_roots": ["Architecture"],
            "current": [
                {"path": "Architecture/28_VALIDATION_COHERENCE.md"},
                {"path": "Architecture/32_FRONTEND_DATABASE_SOURCE_OF_TRUTH.md"},
            ],
            "historical": [],
            "superseded": [],
        },
        "entries": [
            {
                "id": "documentation",
                "domain": "Documentation",
                "scope": "main",
                "status": "PARTIAL",
                "summary": "Corpus gouverné.",
                "evidence": [],
                "gaps": ["Validation de fixture."],
                "validation_gates": [],
            }
        ],
    }
    registry_path.write_text(json.dumps(registry), encoding="utf-8")

    _, findings = audit.load_truth_registry(tmp_path)
    assert any(
        finding["kind"] == "truth_document_unclassified"
        and finding["file"] == "Architecture/GUIDE.md"
        for finding in findings
    )

    registry["documentation"]["current"].append({"path": "Architecture/GUIDE.md"})
    registry_path.write_text(json.dumps(registry), encoding="utf-8")
    _, findings = audit.load_truth_registry(tmp_path)
    assert not any(
        finding["kind"] == "truth_document_unclassified" for finding in findings
    )


def test_truth_registry_ignores_dependencies_without_git_metadata(tmp_path: Path) -> None:
    architecture = tmp_path / "Architecture"
    architecture.mkdir()
    required = (
        "Architecture/28_VALIDATION_COHERENCE.md",
        "Architecture/32_FRONTEND_DATABASE_SOURCE_OF_TRUTH.md",
    )
    for relative in required:
        (tmp_path / relative).write_text("# fixture\n", encoding="utf-8")
    dependency = tmp_path / "web/node_modules/package"
    dependency.mkdir(parents=True)
    (dependency / "README.md").write_text("# dependency\n", encoding="utf-8")
    registry = {
        "schema_version": 1,
        "reviewed_at": "2026-08-27",
        "generated_status_document": required[0],
        "documentation": {
            "governed_roots": ["."],
            "current": [{"path": relative} for relative in required],
            "historical": [],
            "superseded": [],
        },
        "entries": [],
    }
    (architecture / "project_truth_registry.json").write_text(
        json.dumps(registry), encoding="utf-8"
    )

    _, findings = audit.load_truth_registry(tmp_path)

    assert not any("node_modules" in finding["file"] for finding in findings)


def test_public_privacy_scan_rejects_local_identifiers_and_screenshots(
    tmp_path: Path,
) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "LEAK.md").write_text(
        "checkout /Users/private-account/JarvisAPI ; TV 192.168.44.9\n",
        encoding="utf-8",
    )
    package = tmp_path / "packages" / "public-client"
    package.mkdir(parents=True)
    (package / "PORTABILITY.md").write_text(
        "cache /Users/another-account/private-cache/\n",
        encoding="utf-8",
    )
    screenshots = tmp_path / "artifacts" / "validation_screenshots"
    screenshots.mkdir(parents=True)
    (screenshots / "contacts.png").write_bytes(b"not-a-real-image")

    findings = audit.scan_public_privacy(tmp_path)

    assert {
        (finding["file"], finding["kind"])
        for finding in findings
    } == {
        ("docs/LEAK.md", "public_absolute_user_path"),
        ("docs/LEAK.md", "public_private_ipv4"),
        (
            "packages/public-client/PORTABILITY.md",
            "public_absolute_user_path",
        ),
        (
            "artifacts/validation_screenshots/contacts.png",
            "public_validation_screenshot",
        ),
    }


def test_public_privacy_scan_checks_personal_tokens_inside_tests(tmp_path: Path) -> None:
    tests = tmp_path / "tests"
    tests.mkdir()
    personal = "zeld" + "ris"
    (tests / "test_fixture.py").write_text(
        f"MACHINE = 'mac-mini-de-{personal}'\n",
        encoding="utf-8",
    )

    findings = audit.scan_public_privacy(tmp_path)

    assert [(item["file"], item["kind"]) for item in findings] == [
        ("tests/test_fixture.py", "public_personal_identifier")
    ]


def test_public_privacy_scan_rejects_removed_screenshot_references(
    tmp_path: Path,
) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    (artifacts / "complement_report.json").write_text(
        json.dumps({"proof": {"screenshot": "removed-contact.png"}}),
        encoding="utf-8",
    )

    findings = audit.scan_public_privacy(tmp_path)

    assert [(item["file"], item["kind"]) for item in findings] == [
        ("artifacts/complement_report.json", "public_missing_screenshot_reference")
    ]


@pytest.mark.parametrize(
    ("token", "kind"),
    [
        ("web/dist", "retired_web_dist"),
        ("pwa/out", "retired_pwa_out"),
        ("vite_dev", "retired_vite_dev"),
        ("Vite Dev", "retired_vite_dev"),
        ("localhost:5173", "retired_vite_port"),
        ("127.0.0.1:5173", "retired_vite_port"),
    ],
)
def test_frontend_doc_scan_rejects_retired_runtime(
    tmp_path: Path,
    token: str,
    kind: str,
) -> None:
    (tmp_path / "README.md").write_text(
        f"Instruction frontend obsolète : {token}\n",
        encoding="utf-8",
    )

    findings = audit.scan_canonical_frontend_docs(tmp_path)

    assert [(finding["file"], finding["kind"]) for finding in findings] == [
        ("README.md", kind)
    ]


def test_frontend_doc_scan_accepts_current_runtime(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text(
        "frontend/out sert le bureau ; web/src est une bibliothèque ; "
        "web_mobile est servi sous /mobile/.\n",
        encoding="utf-8",
    )

    assert audit.scan_canonical_frontend_docs(tmp_path) == []


def test_build_report_and_cli(fake_repo: Path, tmp_path: Path) -> None:
    out = tmp_path / "out" / "architecture_truth.json"
    status = tmp_path / "out" / "status.md"
    rc = audit.main(
        [
            "--root",
            str(fake_repo),
            "--output",
            str(out),
            "--status-output",
            str(status),
        ]
    )
    assert rc == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert "canonical_formulation" in data
    assert data["resolution"]["supervisor_priority"] == "frontend/out_only"
    assert data["resolution"]["fastapi_uses_unified_first"] is True
    assert data["tables"]["counts"]["schema_sql_applicatives"] == 1
    assert status.read_text(encoding="utf-8").startswith(
        "# 28 — État de vérité du projet\n"
    )
    check_args = [
        "--root",
        str(fake_repo),
        "--output",
        str(out),
        "--status-output",
        str(status),
        "--check",
    ]
    assert audit.main(check_args) == 0
    status.write_text("rendu périmé\n", encoding="utf-8")
    assert audit.main(check_args) == 1


def test_check_mode_rejects_a_stale_report(fake_repo: Path, tmp_path: Path) -> None:
    (fake_repo / "README.md").write_text("Mini dépôt de test.\n", encoding="utf-8")
    out = tmp_path / "architecture_truth.json"
    args = ["--root", str(fake_repo), "--output", str(out)]

    assert audit.main(args) == 0
    assert audit.main([*args, "--check"]) == 0

    stale = json.loads(out.read_text(encoding="utf-8"))
    stale["tables"]["counts"]["persistantes_post_init"] = 999
    out.write_text(json.dumps(stale), encoding="utf-8")
    assert audit.main([*args, "--check"]) == 1


def test_real_repo_smoke_counts_stable() -> None:
    """Garde-fou : le dépôt réel produit les comptages attendus (code only)."""
    tables = audit.analyze_tables(ROOT)
    assert tables["counts"]["schema_sql_applicatives"] == 126
    # Le versionnement, l'historique des métriques et le registre de profils
    # sont tous inclus dans ces comptages cumulés.
    assert tables["counts"]["schema_py"] == 82
    assert tables["counts"]["persistantes_post_init"] == 119
    assert tables["counts"]["physiques_max_default_fts_on"] == 124
    assert tables["init_pipeline"]["does_not_execute_schema_sql"] is True

    resolution = audit.analyze_frontend_resolution(ROOT)
    assert resolution["fastapi_uses_unified_first"] is True
    assert resolution["supervisor_priority"] == "frontend/out_only"
    assert resolution["supervisor_uses_shared_resolver"] is True
    assert resolution["priority_findings"] == []

    api_surface = audit.analyze_api_surface(ROOT)
    assert api_surface["counts"] == {
        "operations": 324,
        "paths": 288,
        "consumer_and_tested": 150,
        "consumer_without_path_test": 68,
        "owned_non_frontend_and_tested": 53,
        "owned_non_frontend_without_path_test": 53,
    }
    assert api_surface["structure"] == {
        "http_operations": 322,
        "websocket_operations": 2,
        "openapi_paths": 286,
        "domain_router_modules": 22,
        "mounted_routers": 23,
        "main_lines": 269,
    }
    assert api_surface["ownership_policy"]["rules"] == 40
    assert api_surface["ownership_policy"]["findings"] == []

    registry, findings = audit.load_truth_registry(ROOT)
    assert findings == []
    assert len(registry["entries"]) == 14
    assert {entry["status"] for entry in registry["entries"]} <= audit.TRUTH_STATUSES


def test_generated_runtime_schema_replays_a_fresh_database() -> None:
    schema = audit.render_runtime_schema(ROOT)
    assert schema.startswith("-- GENERATED FILE — DO NOT EDIT.")
    assert len(audit._extract_create_tables(schema)) == 126

    conn = sqlite3.connect(":memory:")
    try:
        conn.executescript(schema)
        table_count = conn.execute("""
            SELECT COUNT(*) FROM sqlite_master
            WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
            """).fetchone()[0]
    finally:
        conn.close()
    assert table_count == 134


def test_versioned_architecture_artifacts_match_runtime() -> None:
    assert (
        audit.main(
            [
                "--root",
                str(ROOT),
                "--output",
                str(ROOT / "artifacts" / "architecture_truth.json"),
                "--schema-output",
                str(ROOT / "database" / "schema.sql"),
                "--status-output",
                str(ROOT / "Architecture" / "28_VALIDATION_COHERENCE.md"),
                "--check",
            ]
        )
        == 0
    )
