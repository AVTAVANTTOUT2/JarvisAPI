#!/usr/bin/env python3
"""Audit non destructif — vérité frontends + schéma SQLite (code only).

Produit ``artifacts/architecture_truth.json`` sans :
- modifier de fichiers (hors écriture du rapport demandé) ;
- démarrer de services ;
- ouvrir ``data/jarvis.db`` ;
- exécuter de migrations sur une base de production.

Usage::

    python tools/audit_architecture_truth.py
    python tools/audit_architecture_truth.py --output artifacts/architecture_truth.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent

CREATE_TABLE_RE = re.compile(
    r"CREATE\s+(?:VIRTUAL\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[\"']?(\w+)[\"']?",
    re.IGNORECASE,
)

DOC_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("tables_26_plus", re.compile(r"26\+\s*tables", re.I)),
    ("tables_44", re.compile(r"\b44\s+tables?\b", re.I)),
    ("tables_72", re.compile(r"\b72\s+tables?\b", re.I)),
    ("tables_73", re.compile(r"\b73\s+tables?\b|\b73e\s+table\b", re.I)),
    ("nextjs_14_as_primary", re.compile(r"frontend\s+canonique[^\n]{0,40}Next\.js\s*14", re.I)),
    ("web_as_spa_principale", re.compile(r"`web/`\s*\(SPA principale", re.I)),
    ("schema_sql_as_runtime", re.compile(r"schema\.sql[^\n]{0,60}init_db", re.I)),
    (
        "supervisor_vite_only",
        re.compile(
            r"supervisor[^\n]{0,80}(sert\s+encore\s+web/dist|sert\s+uniquement\s+web/dist|"
            r"web/dist\s+\(pas\s+frontend/out\))",
            re.I,
        ),
    ),
]

SCAN_DOCS = (
    "README.md",
    "CLAUDE.md",
    "Architecture/INDEX.md",
    "Architecture/01_CARTOGRAPHIE.md",
    "Architecture/28_VALIDATION_COHERENCE.md",
    "Architecture/adr/ADR-017-sqlite-base-unique.md",
)


@dataclass
class FrontendProject:
    path: str
    framework: str
    package_versions: dict[str, str] = field(default_factory=dict)
    locked_versions: dict[str, str] = field(default_factory=dict)
    scripts: dict[str, str] = field(default_factory=dict)
    output_dir: str | None = None
    output_present: bool = False
    has_service_worker: bool = False
    has_manifest: bool = False
    status: str = "indetermine"
    notes: list[str] = field(default_factory=list)


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _extract_create_tables(text: str) -> list[str]:
    return sorted(set(CREATE_TABLE_RE.findall(text)))


def _pnpm_importer_versions(lock_text: str, packages: tuple[str, ...]) -> dict[str, str]:
    """Lit les versions résolues dans le bloc importers. de pnpm lockfile v9."""
    found: dict[str, str] = {}
    for pkg in packages:
        # next:\n        specifier: ...\n        version: 15.5.20(...)
        pattern = re.compile(
            rf"(?m)^      {re.escape(pkg)}:\n"
            rf"(?:        .*\n)*?"
            rf"        version:\s*([^\s(]+)",
        )
        match = pattern.search(lock_text)
        if match:
            found[pkg] = match.group(1)
    return found


def _npm_lock_versions(lock: dict[str, Any], packages: tuple[str, ...]) -> dict[str, str]:
    pkgs = lock.get("packages") or {}
    found: dict[str, str] = {}
    for pkg in packages:
        entry = pkgs.get(f"node_modules/{pkg}") or {}
        version = entry.get("version")
        if version:
            found[pkg] = version
    return found


def discover_frontends(root: Path) -> list[FrontendProject]:
    interest = [
        ("frontend", "next", "frontend/out", "actif_canonique_fastapi"),
        ("web", "vite", "web/dist", "fallback_actif"),
        ("pwa", "next", "pwa/out", "fallback_historique_m"),
        ("jarvis_auth", "react-lib", None, "sdk_partage"),
    ]
    results: list[FrontendProject] = []
    for dirname, framework, out_rel, status in interest:
        dir_path = root / dirname
        if not dir_path.is_dir():
            continue
        pkg = _read_json(dir_path / "package.json") or {}
        deps = {**(pkg.get("dependencies") or {}), **(pkg.get("devDependencies") or {})}
        keys = (
            "next",
            "react",
            "react-dom",
            "vite",
            "typescript",
            "vite-plugin-pwa",
            "next-pwa",
            "workbox-precaching",
            "tailwindcss",
            "@tanstack/react-query",
            "react-router-dom",
        )
        package_versions = {k: str(deps[k]) for k in keys if k in deps}
        locked: dict[str, str] = {}
        pnpm_lock = dir_path / "pnpm-lock.yaml"
        npm_lock = dir_path / "package-lock.json"
        if pnpm_lock.is_file():
            locked = _pnpm_importer_versions(
                pnpm_lock.read_text(encoding="utf-8"),
                ("next", "react", "react-dom", "vite", "typescript", "tailwindcss"),
            )
        elif npm_lock.is_file():
            lock_data = _read_json(npm_lock) or {}
            locked = _npm_lock_versions(
                lock_data,
                ("next", "react", "react-dom", "typescript", "next-pwa", "tailwindcss"),
            )

        out_dir = root / out_rel if out_rel else None
        sw_candidates = [
            dir_path / "public" / "sw.js",
            dir_path / "src" / "sw.ts",
            dir_path / "public" / "sw.js",
        ]
        manifest_candidates = [
            dir_path / "public" / "manifest.webmanifest",
            dir_path / "public" / "manifest.json",
        ]
        project = FrontendProject(
            path=str(dir_path.relative_to(root)),
            framework=framework,
            package_versions=package_versions,
            locked_versions=locked,
            scripts=dict(pkg.get("scripts") or {}),
            output_dir=out_rel,
            output_present=bool(out_dir and (out_dir / "index.html").is_file()),
            has_service_worker=any(p.is_file() for p in sw_candidates),
            has_manifest=any(p.is_file() for p in manifest_candidates),
            status=status,
        )
        results.append(project)

    # TV / orphan (pas de package.json)
    if (root / "tv" / "server.py").is_file():
        results.append(
            FrontendProject(
                path="tv",
                framework="fastapi_jinja_vanilla_js",
                package_versions={},
                locked_versions={},
                scripts={"dev": "python tv/server.py"},
                output_dir=None,
                output_present=True,
                has_service_worker=False,
                has_manifest=False,
                status="actif_tv_5174",
                notes=["Processus séparé (port 5174), hors api/frontend.py"],
            )
        )
    if (root / "front_tv").is_dir():
        results.append(
            FrontendProject(
                path="front_tv",
                framework="html_bundle",
                status="orphelin",
                notes=["Aucune référence runtime dans le dépôt"],
            )
        )
    return results


def analyze_frontend_resolution(root: Path) -> dict[str, Any]:
    frontend_py = (root / "api" / "frontend.py").read_text(encoding="utf-8")
    supervisor = (root / "supervisor.py").read_text(encoding="utf-8")
    shared = ""
    shared_path = root / "core" / "frontend_resolution.py"
    if shared_path.is_file():
        shared = shared_path.read_text(encoding="utf-8")

    supervisor_uses_resolver = (
        "resolve_desktop_frontend" in supervisor
        and "register_desktop_frontend_routes" in supervisor
    )
    supervisor_references_canonical = (
        "frontend/out" in supervisor
        or "next_canonical" in supervisor
        or "resolve_desktop_frontend" in supervisor
    )
    # Ancien mode : catch-all SPA limité à web/dist sans résolution Next
    supervisor_vite_only = (
        'DIST_DIR = PROJECT_DIR / "web" / "dist"' in supervisor
        and "resolve_desktop_frontend" not in supervisor
        and "FRONTEND_RESOLUTION" not in supervisor
    )
    fastapi_aligned = (
        "resolve_desktop_frontend_roots" in frontend_py
        or (
            "_setup_unified_frontend" in frontend_py
            and "is_usable_next_build" in frontend_py
        )
    )
    shared_priority_ok = (
        "next_canonical" in shared
        and "vite_fallback" in shared
        and "is_usable_next_build" in shared
    )

    findings: list[dict[str, str]] = []
    if supervisor_vite_only:
        findings.append(
            {
                "severity": "error",
                "kind": "supervisor_vite_priority",
                "note": "supervisor.py priorise encore web/dist sans résolution Next",
            }
        )
    if not supervisor_references_canonical:
        findings.append(
            {
                "severity": "error",
                "kind": "supervisor_missing_frontend_out",
                "note": "supervisor.py ne référence pas frontend/out / resolve_desktop_frontend",
            }
        )
    if not supervisor_uses_resolver:
        findings.append(
            {
                "severity": "warning",
                "kind": "supervisor_resolver_missing",
                "note": "supervisor.py n'utilise pas resolve_desktop_frontend",
            }
        )

    return {
        "canonical_order": [
            "PWA /m/ montée si PWA_ENABLED et pwa/out",
            "frontend/out prioritaire (Next.js 15 unifié)",
            "web/dist fallback Vite + redirect mobile optionnelle",
            "web/templates Jinja legacy",
            "warning aucun frontend",
        ],
        "fastapi_uses_unified_first": fastapi_aligned,
        "supervisor_priority": (
            "frontend/out>web/dist"
            if supervisor_uses_resolver and not supervisor_vite_only
            else ("web/dist_only" if supervisor_vite_only else "unknown")
        ),
        "supervisor_uses_shared_resolver": supervisor_uses_resolver,
        "shared_resolution_module": shared_priority_ok,
        "priority_findings": findings,
        "paths": {
            "FRONTEND_DIST_DIR_default": "frontend/out",
            "WEB_DIST_DIR_default": "web/dist",
            "PWA_DIR_default": "pwa/out",
            "TV_PORT_default": 5174,
            "BACKEND_PORT_typical": 8081,
            "SUPERVISOR_PORT_default": 9000,
            "VITE_DEV_PORT": 5173,
        },
        "build_presence": {
            "frontend/out": (root / "frontend" / "out" / "index.html").is_file(),
            "web/dist": (root / "web" / "dist" / "index.html").is_file(),
            "pwa/out": (root / "pwa" / "out" / "index.html").is_file(),
        },
    }


def analyze_tables(root: Path) -> dict[str, Any]:
    schema_py = (root / "database" / "schema.py").read_text(encoding="utf-8")
    schema_sql = (root / "database" / "schema.sql").read_text(encoding="utf-8")
    migrations = (root / "database" / "migrations.py").read_text(encoding="utf-8")
    devagent = (root / "database" / "devagent.py").read_text(encoding="utf-8")
    core = (root / "database" / "core.py").read_text(encoding="utf-8")

    t_schema = _extract_create_tables(schema_py)
    t_sql = [t for t in _extract_create_tables(schema_sql) if t != "sqlite_sequence"]
    t_mig = _extract_create_tables(migrations)
    t_dev = _extract_create_tables(devagent)

    mig_only = sorted(set(t_mig) - set(t_schema) - set(t_dev) - {"messages_fts"})
    persistantes = sorted(set(t_schema) | set(mig_only) | set(t_dev))
    fts_declared = "messages_fts" in t_mig

    imessage_mirror = sorted(t for t in persistantes if t.startswith("imessage_") and t != "imessage_analysis_cache")
    # imessage_analysis_cache is app meta, not mirror of chat.db structure
    imessage_mirror = [
        t
        for t in persistantes
        if t
        in {
            "imessage_handles",
            "imessage_chats",
            "imessage_chat_handles",
            "imessage_messages",
            "imessage_attachments",
            "imessage_message_attachments",
            "imessage_reactions",
            "imessage_sync_cursor",
            "imessage_consumer_cursors",
        }
    ]
    tech = sorted(
        set(persistantes)
        & {
            "sessions",
            "mobile_devices",
            "mobile_pairing_codes",
            "push_subscriptions",
            "schema_migrations",
            "perf_benchmarks",
            "security_findings",
            "duplicate_findings",
            "app_settings",
            "event_log",
            "llm_action_logs",
            "voice_debug_log",
            "screen_activity",
            "app_usage",
            "devices",
            "work_sessions",
            "agentic_workflows",
        }
    )

    return {
        "init_pipeline": {
            "uses_schema_py": "from .schema import SCHEMA" in core,
            "runs_migrations": "run_migrations(conn)" in core,
            "does_not_execute_schema_sql": True,
        },
        "counts": {
            "schema_py": len(t_schema),
            "schema_sql_applicatives": len(t_sql),
            "migrations_unique_excluding_schema_and_fts": len(mig_only),
            "devagent": len(t_dev),
            "persistantes_post_init": len(persistantes),
            "fts_objects_if_available": 5 if fts_declared else 0,
            "physiques_max_default_fts_on": len(persistantes) + (5 if fts_declared else 0),
            "imessage_mirror": len(imessage_mirror),
            "technique_estime": len(tech),
            "metier_estime": len(persistantes) - len(imessage_mirror) - len(t_dev) - len(tech),
        },
        "lists": {
            "schema_py": t_schema,
            "schema_sql": t_sql,
            "migrations_only": mig_only,
            "devagent": t_dev,
            "persistantes": persistantes,
            "imessage_mirror": imessage_mirror,
            "technique": tech,
        },
        "explanations": {
            "44": "Nombre de tables applicatives dans database/schema.sql (dump), hors sqlite_sequence.",
            "73": "Inventaire Architecture juillet 2026, légèrement en retard sur le runtime actuel.",
            "75": "len(sqlite_master tables) après init_db() avec FTS5 (70 persistantes + 5 FTS).",
            "70": "Tables persistantes créées par schema.py + migrations + DevAgent, hors objets FTS.",
        },
    }


def scan_doc_contradictions(root: Path, tables: dict[str, Any]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    expected_physical = tables["counts"]["physiques_max_default_fts_on"]
    expected_persist = tables["counts"]["persistantes_post_init"]
    schema_sql_count = tables["counts"]["schema_sql_applicatives"]

    for rel in SCAN_DOCS:
        path = root / rel
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        for kind, pattern in DOC_PATTERNS:
            for match in pattern.finditer(text):
                line_no = text.count("\n", 0, match.start()) + 1
                severity = "warning"
                note = match.group(0)
                if kind == "tables_44" and schema_sql_count == 44:
                    # Mention historique acceptable si contextualisée
                    severity = "info"
                    note = f"{match.group(0)} — cohérent avec schema.sql={schema_sql_count} si contextualisé"
                if kind in {"tables_72", "tables_26_plus"}:
                    severity = "error"
                    note = (
                        f"{match.group(0)} contredit persistantes={expected_persist} "
                        f"/ physiques={expected_physical}"
                    )
                if kind == "tables_73" and expected_physical != 73:
                    severity = "warning"
                    note = (
                        f"{match.group(0)} — attendu actuel physiques={expected_physical} "
                        f"(persistantes={expected_persist})"
                    )
                if kind == "web_as_spa_principale":
                    severity = "error"
                    note = "`web/` n'est plus le frontend canonique (Phase 6 → frontend/)"
                if kind == "supervisor_vite_only":
                    severity = "error"
                    note = (
                        "La documentation affirme encore que le supervisor sert "
                        "uniquement web/dist — attendu : frontend/out > web/dist"
                    )
                findings.append(
                    {
                        "file": rel,
                        "line": line_no,
                        "kind": kind,
                        "severity": severity,
                        "excerpt": match.group(0),
                        "note": note,
                    }
                )
    return findings


def build_report(root: Path) -> dict[str, Any]:
    frontends = discover_frontends(root)
    resolution = analyze_frontend_resolution(root)
    tables = analyze_tables(root)
    contradictions = scan_doc_contradictions(root, tables)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "repo_root": str(root),
        "canonical_formulation": {
            "database": (
                f"Le projet crée {tables['counts']['persistantes_post_init']} tables persistantes "
                f"après init_db() + migrations, plus jusqu'à "
                f"{tables['counts']['fts_objects_if_available']} objets FTS5, soit "
                f"{tables['counts']['physiques_max_default_fts_on']} tables physiques "
                "lorsque FTS5 est disponible."
            ),
            "frontends": (
                "Le frontend canonique est frontend/ (Next.js 15 → frontend/out). "
                "web/dist reste le fallback actif. pwa/out est la PWA historique sous /m/. "
                "tv/ (5174) est réservé à la TV. "
                "FastAPI (8081) et le supervisor (9000) servent frontend/out en priorité, "
                "puis web/dist en fallback (core.frontend_resolution)."
            ),
        },
        "frontends": [asdict(f) for f in frontends],
        "resolution": resolution,
        "tables": tables,
        "documentation_findings": contradictions
        + [
            {
                "file": "supervisor.py",
                "line": 0,
                "kind": f["kind"],
                "severity": f["severity"],
                "excerpt": f["kind"],
                "note": f["note"],
            }
            for f in resolution.get("priority_findings", [])
        ],
        "source_of_truth_doc": "Architecture/32_FRONTEND_DATABASE_SOURCE_OF_TRUTH.md",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=ROOT,
        help="Racine du dépôt JARVIS",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "artifacts" / "architecture_truth.json",
        help="Chemin du rapport JSON",
    )
    parser.add_argument(
        "--stdout",
        action="store_true",
        help="Écrire aussi le JSON sur stdout",
    )
    args = parser.parse_args(argv)
    root = args.root.resolve()
    report = build_report(root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    args.output.write_text(payload, encoding="utf-8")
    if args.stdout:
        sys.stdout.write(payload)
    errors = [f for f in report["documentation_findings"] if f["severity"] == "error"]
    print(
        f"[audit_architecture_truth] wrote {args.output} "
        f"(persistantes={report['tables']['counts']['persistantes_post_init']}, "
        f"physiques_max={report['tables']['counts']['physiques_max_default_fts_on']}, "
        f"doc_errors={len(errors)})",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
