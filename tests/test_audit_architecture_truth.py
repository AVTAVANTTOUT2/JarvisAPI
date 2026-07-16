"""Tests unitaires du script d'audit architecture (non destructif)."""

from __future__ import annotations

import json
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
                "dependencies": {"next": "15.5.20", "react": "^19.2.5", "react-dom": "^19.2.5"},
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

    # web Vite
    web = tmp_path / "web"
    (web / "src").mkdir(parents=True)
    (web / "dist").mkdir(parents=True)
    (web / "package.json").write_text(
        json.dumps(
            {
                "dependencies": {"react": "^19.0.0", "react-dom": "^19.0.0"},
                "devDependencies": {"vite": "^6.3.0", "typescript": "^5.8.0"},
                "scripts": {"dev": "vite", "build": "vite build"},
            }
        ),
        encoding="utf-8",
    )
    (web / "src" / "sw.ts").write_text("// sw\n", encoding="utf-8")
    (web / "dist" / "index.html").write_text("<html></html>", encoding="utf-8")

    # pwa without out/
    pwa = tmp_path / "pwa"
    (pwa / "public").mkdir(parents=True)
    (pwa / "package.json").write_text(
        json.dumps(
            {
                "dependencies": {"next": "14.2.29", "react": "^18.3.1"},
                "scripts": {"build": "next build"},
            }
        ),
        encoding="utf-8",
    )
    (pwa / "package-lock.json").write_text(
        json.dumps(
            {
                "packages": {
                    "node_modules/next": {"version": "14.2.29"},
                    "node_modules/react": {"version": "18.3.1"},
                }
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
        "from .schema import SCHEMA\n"
        "def init_db():\n"
        "    run_migrations(conn)\n",
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
    (tmp_path / "core").mkdir()
    (tmp_path / "core" / "frontend_resolution.py").write_text(
        "def is_usable_next_build(p): ...\n"
        "next_canonical = 'next_canonical'\n"
        "vite_fallback = 'vite_fallback'\n",
        encoding="utf-8",
    )
    (tmp_path / "supervisor.py").write_text(
        "from core.frontend_resolution import resolve_desktop_frontend\n"
        "from core.frontend_static import register_desktop_frontend_routes\n"
        "FRONTEND_RESOLUTION = resolve_desktop_frontend(PROJECT_DIR)\n"
        "register_desktop_frontend_routes(app, FRONTEND_RESOLUTION)\n"
        "# frontend/out prioritaire, web/dist fallback\n",
        encoding="utf-8",
    )
    (tmp_path / "tv").mkdir()
    (tmp_path / "tv" / "server.py").write_text("print('tv')\n", encoding="utf-8")
    (tmp_path / "front_tv").mkdir()
    (tmp_path / "README.md").write_text(
        "26+ tables SQLite\n72 tables\n`web/` (SPA principale, Vite + React)\n",
        encoding="utf-8",
    )
    return tmp_path


def test_extract_create_tables_ignores_noise() -> None:
    text = "CREATE TABLE IF NOT EXISTS foo (id);\ncreate virtual table bar using fts5(x);"
    assert audit._extract_create_tables(text) == ["bar", "foo"]


def test_discover_frontends_classifies_projects(fake_repo: Path) -> None:
    projects = {p.path: p for p in audit.discover_frontends(fake_repo)}
    assert projects["frontend"].status == "actif_canonique_fastapi"
    assert projects["frontend"].locked_versions["next"] == "15.5.20"
    assert projects["frontend"].locked_versions["react"] == "19.2.7"
    assert projects["frontend"].output_present is True
    assert projects["web"].status == "fallback_actif"
    assert projects["pwa"].output_present is False
    assert projects["tv"].status == "actif_tv_5174"
    assert projects["front_tv"].status == "orphelin"


def test_analyze_tables_counts(fake_repo: Path) -> None:
    tables = audit.analyze_tables(fake_repo)
    assert tables["counts"]["schema_py"] == 2
    assert tables["counts"]["schema_sql_applicatives"] == 1
    assert tables["counts"]["persistantes_post_init"] == 4  # episodes, conversations, sessions, dev_projects
    assert tables["counts"]["fts_objects_if_available"] == 5
    assert tables["counts"]["physiques_max_default_fts_on"] == 9
    assert tables["init_pipeline"]["uses_schema_py"] is True


def test_doc_scan_flags_readme_errors(fake_repo: Path) -> None:
    tables = audit.analyze_tables(fake_repo)
    findings = audit.scan_doc_contradictions(fake_repo, tables)
    kinds = {f["kind"] for f in findings}
    assert "tables_26_plus" in kinds
    assert "tables_72" in kinds
    assert "web_as_spa_principale" in kinds
    assert any(f["severity"] == "error" for f in findings)


def test_build_report_and_cli(fake_repo: Path, tmp_path: Path) -> None:
    out = tmp_path / "out" / "architecture_truth.json"
    rc = audit.main(["--root", str(fake_repo), "--output", str(out)])
    assert rc == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert "canonical_formulation" in data
    assert data["resolution"]["supervisor_priority"] == "frontend/out>web/dist"
    assert data["resolution"]["fastapi_uses_unified_first"] is True
    assert data["tables"]["counts"]["schema_sql_applicatives"] == 1


def test_real_repo_smoke_counts_stable() -> None:
    """Garde-fou : le dépôt réel produit les comptages attendus (code only)."""
    tables = audit.analyze_tables(ROOT)
    assert tables["counts"]["schema_sql_applicatives"] == 44
    assert tables["counts"]["schema_py"] == 47
    assert tables["counts"]["persistantes_post_init"] == 70
    assert tables["counts"]["physiques_max_default_fts_on"] == 75
    assert tables["init_pipeline"]["does_not_execute_schema_sql"] is True

    resolution = audit.analyze_frontend_resolution(ROOT)
    assert resolution["fastapi_uses_unified_first"] is True
    assert resolution["supervisor_priority"] == "frontend/out>web/dist"
    assert resolution["supervisor_uses_shared_resolver"] is True
    assert resolution["priority_findings"] == []
