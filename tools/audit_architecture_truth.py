#!/usr/bin/env python3
"""Génère et vérifie la vérité frontends + API + schéma SQLite (code only).

Produit ``artifacts/architecture_truth.json`` et, sur demande, le schéma
SQLite déterministe d'une base fraîche ainsi que la vue humaine du registre,
sans :
- démarrer de services ;
- ouvrir ``data/jarvis.db`` ;
- exécuter de migrations ailleurs que dans une base ``:memory:``.

Usage::

    python tools/audit_architecture_truth.py
    python tools/audit_architecture_truth.py --schema-output database/schema.sql \
      --status-output Architecture/28_VALIDATION_COHERENCE.md
    python tools/audit_architecture_truth.py --check \
      --schema-output database/schema.sql \
      --status-output Architecture/28_VALIDATION_COHERENCE.md
"""

from __future__ import annotations

import argparse
import ast
import copy
import hashlib
import importlib.util
import ipaddress
import json
import re
import sqlite3
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
TRUTH_REGISTRY_PATH = "Architecture/project_truth_registry.json"
TRUTH_STATUS_PATH = "Architecture/28_VALIDATION_COHERENCE.md"
TRUTH_STATUSES = {
    "IMPLEMENTED_VERIFIED",
    "IMPLEMENTED_NEEDS_REAL_VALIDATION",
    "PARTIAL",
    "NOT_IMPLEMENTED",
    "SUPERSEDED",
    "HISTORICAL_ONLY",
}
TRUTH_CLAIMS = {"database", "api_surface", "api_structure", "frontend"}

PUBLIC_TEXT_SUFFIXES = {
    ".cjs",
    ".conf",
    ".css",
    ".go",
    ".gradle",
    ".html",
    ".ini",
    ".java",
    ".js",
    ".jsx",
    ".json",
    ".kt",
    ".md",
    ".mjs",
    ".plist",
    ".properties",
    ".py",
    ".sh",
    ".sql",
    ".swift",
    ".toml",
    ".txt",
    ".ts",
    ".tsx",
    ".xml",
    ".yaml",
    ".yml",
}
ABSOLUTE_MAC_USER_PATH_RE = re.compile(r"/Users/(?P<user>[A-Za-z0-9._-]+)/")
PUBLIC_PERSONAL_TOKEN_RE = re.compile(r"(?i)\b" + "zeld" + r"ris(?:dash)?\b")
IPV4_RE = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")
PUBLIC_PRIVATE_IP_ALLOWLIST = {
    "10.0.0.0",
    "10.0.2.2",
    "127.0.0.1",
    "172.16.0.0",
    "192.168.0.0",
}
DOCUMENT_SCAN_EXCLUDED_PARTS = {
    ".git",
    ".next",
    ".venv",
    "build",
    "dist",
    "node_modules",
    "venv",
}

CREATE_TABLE_RE = re.compile(
    r"CREATE\s+(?:VIRTUAL\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[\"']?(\w+)[\"']?",
    re.IGNORECASE,
)

CANONICAL_FRONTEND_DOCS = (
    "README.md",
    "CLAUDE.md",
    "STARTUP_PROTOCOL.md",
    "Architecture/INDEX.md",
    "prompts/cursor/release_build.md",
    "prompts/cursor/frontend_feature.md",
)

RETIRED_FRONTEND_DOC_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("retired_web_dist", re.compile(r"web/dist", re.I)),
    ("retired_pwa_out", re.compile(r"pwa/out", re.I)),
    ("retired_vite_dev", re.compile(r"\bvite(?:_|[ -])dev\b", re.I)),
    (
        "retired_vite_port",
        re.compile(r"(?:localhost|127\.0\.0\.1):5173", re.I),
    ),
)

FTS_SHADOW_SUFFIXES = {"config", "content", "data", "docsize", "idx"}

HTTP_ROUTE_METHODS = {
    "get": ("GET",),
    "post": ("POST",),
    "put": ("PUT",),
    "patch": ("PATCH",),
    "delete": ("DELETE",),
    "head": ("HEAD",),
    "options": ("OPTIONS",),
    "websocket": ("WEBSOCKET",),
}
API_ROUTE_SOURCE_ROOTS = ("api", "app")
API_ROUTE_SPECIAL_PATHS = {"/upload", "/ws"}
API_ROUTE_OWNERSHIP_POLICY = "Architecture/api_route_ownership.json"
NON_FRONTEND_AUDIENCES = {
    "automation",
    "developer",
    "device-agent",
    "indirect-client",
    "integration-client",
    "operator",
}
SOURCE_SUFFIXES = {
    ".cjs",
    ".html",
    ".js",
    ".jsx",
    ".kt",
    ".mjs",
    ".py",
    ".swift",
    ".ts",
    ".tsx",
}
IGNORED_SOURCE_PARTS = {
    ".git",
    ".gradle",
    ".next",
    ".pytest_cache",
    ".venv",
    "DerivedData",
    "build",
    "coverage",
    "dist",
    "node_modules",
    "out",
}
PUBLIC_PRIVACY_IGNORED_PARTS = IGNORED_SOURCE_PARTS | {
    "androidTest",
    "locks",
    "test",
    "tests",
}
CONSUMER_SURFACES = {
    "frontend_next": ("frontend",),
    "frontend_vite": ("web",),
    "mobile_web": ("web_mobile",),
    "android": ("android/app/src/main",),
    "macos": ("native_mac",),
    "tv": ("tv",),
    "shared_auth_sdk": ("jarvis_auth/src",),
}
TEST_SOURCE_ROOTS = (
    "tests",
    "jarvis/tests",
    "agents/devagent",
    "frontend",
    "web",
    "web_mobile",
    "android/app/src/test",
    "android/app/src/androidTest",
    "native_mac",
    "jarvis_auth",
)
NON_CONSUMER_REFERENCE_MARKER = "architecture-audit: non-consumer-reference"


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


@dataclass(frozen=True, order=True)
class ApiRoute:
    method: str
    path: str
    source: str
    line: int


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _truth_finding(
    note: str,
    *,
    kind: str = "truth_registry_invalid",
    file: str = TRUTH_REGISTRY_PATH,
) -> dict[str, Any]:
    return {
        "file": file,
        "line": 0,
        "kind": kind,
        "severity": "error",
        "excerpt": kind,
        "note": note,
    }


def load_truth_registry(root: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Charge et valide le registre manuel qui borne les affirmations courantes.

    Les mini-dépôts des tests unitaires peuvent ne pas embarquer Architecture/.
    Un dépôt complet, reconnaissable à son document 32, doit en revanche toujours
    fournir le registre.
    """
    path = root / TRUTH_REGISTRY_PATH
    if not path.is_file():
        if not (root / "Architecture/32_FRONTEND_DATABASE_SOURCE_OF_TRUTH.md").exists():
            return {}, []
        return {}, [_truth_finding(f"registre absent : {TRUTH_REGISTRY_PATH}")]

    try:
        raw = path.read_bytes()
        registry = json.loads(raw)
    except (OSError, json.JSONDecodeError) as error:
        return {}, [_truth_finding(f"registre JSON illisible : {error}")]
    if not isinstance(registry, dict):
        return {}, [_truth_finding("la racine du registre doit être un objet JSON")]

    findings: list[dict[str, Any]] = []
    if registry.get("schema_version") != 1:
        findings.append(_truth_finding("schema_version doit valoir 1"))
    reviewed_at = registry.get("reviewed_at")
    if not isinstance(reviewed_at, str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}", reviewed_at
    ):
        findings.append(_truth_finding("reviewed_at doit être une date YYYY-MM-DD"))

    documentation = registry.get("documentation")
    if not isinstance(documentation, dict):
        findings.append(_truth_finding("documentation doit être un objet"))
        documentation = {}
    governed_roots = documentation.get("governed_roots", [])
    if not isinstance(governed_roots, list) or not governed_roots or any(
        not isinstance(item, str) or not item for item in governed_roots
    ):
        findings.append(
            _truth_finding("documentation.governed_roots doit être une liste non vide")
        )
        governed_roots = []
    governed_markdown: set[str] = set()
    for relative_root in governed_roots:
        governed_root = root / relative_root
        if not governed_root.is_dir():
            findings.append(
                _truth_finding(f"racine documentaire introuvable : {relative_root}")
            )
            continue
        if relative_root == "." and (root / ".git").exists():
            tracked = subprocess.run(
                ["git", "-C", str(root), "ls-files", "*.md"],
                capture_output=True,
                check=False,
                text=True,
            )
            if tracked.returncode == 0:
                governed_markdown.update(
                    item for item in tracked.stdout.splitlines() if item
                )
                continue
        governed_markdown.update(
            path.relative_to(root).as_posix()
            for path in governed_root.rglob("*.md")
            if path.is_file()
            and not DOCUMENT_SCAN_EXCLUDED_PARTS.intersection(
                path.relative_to(root).parts
            )
        )
    classified_paths: dict[str, str] = {}
    for classification in ("current", "historical", "superseded"):
        documents = documentation.get(classification, [])
        if not isinstance(documents, list):
            findings.append(
                _truth_finding(f"documentation.{classification} doit être une liste")
            )
            continue
        for index, document in enumerate(documents):
            location = f"documentation.{classification}[{index}]"
            if not isinstance(document, dict) or not isinstance(
                document.get("path"), str
            ):
                findings.append(_truth_finding(f"{location}.path est obligatoire"))
                continue
            relative = document["path"]
            previous = classified_paths.get(relative)
            if previous is not None:
                findings.append(
                    _truth_finding(
                        f"{relative} est classé à la fois {previous} et {classification}"
                    )
                )
            classified_paths[relative] = classification
            if not (root / relative).is_file():
                findings.append(_truth_finding(f"document introuvable : {relative}"))
            if classification == "current":
                claims = document.get("required_claims", [])
                if not isinstance(claims, list) or any(
                    claim not in TRUTH_CLAIMS for claim in claims
                ):
                    findings.append(
                        _truth_finding(
                            f"{location}.required_claims contient une valeur inconnue"
                        )
                    )
            elif classification == "historical" and not document.get("snapshot_at"):
                findings.append(_truth_finding(f"{location}.snapshot_at est obligatoire"))
            elif classification == "superseded":
                replacement = document.get("superseded_by")
                if not isinstance(replacement, str) or not (root / replacement).is_file():
                    findings.append(
                        _truth_finding(
                            f"{location}.superseded_by doit pointer vers un fichier existant"
                        )
                    )

    compact_current = documentation.get("current_paths", [])
    if not isinstance(compact_current, list) or any(
        not isinstance(item, str) or not item for item in compact_current
    ):
        findings.append(
            _truth_finding("documentation.current_paths doit être une liste de chemins")
        )
        compact_current = []
    for relative in compact_current:
        previous = classified_paths.get(relative)
        if previous is not None:
            findings.append(
                _truth_finding(
                    f"{relative} est classé à la fois {previous} et current_paths"
                )
            )
        classified_paths[relative] = "current"
        if not (root / relative).is_file():
            findings.append(_truth_finding(f"document introuvable : {relative}"))

    for relative in sorted(governed_markdown - classified_paths.keys()):
        findings.append(
            _truth_finding(
                f"document Markdown non classé : {relative}",
                kind="truth_document_unclassified",
                file=relative,
            )
        )

    status_document = registry.get("generated_status_document")
    if status_document != TRUTH_STATUS_PATH:
        findings.append(
            _truth_finding(
                f"generated_status_document doit valoir {TRUTH_STATUS_PATH}"
            )
        )
    if classified_paths.get(TRUTH_STATUS_PATH) != "current":
        findings.append(
            _truth_finding(f"{TRUTH_STATUS_PATH} doit être classé current")
        )

    entries = registry.get("entries")
    if not isinstance(entries, list) or not entries:
        findings.append(_truth_finding("entries doit être une liste non vide"))
        entries = []
    ids: set[str] = set()
    for index, entry in enumerate(entries):
        location = f"entries[{index}]"
        if not isinstance(entry, dict):
            findings.append(_truth_finding(f"{location} doit être un objet"))
            continue
        entry_id = entry.get("id")
        if not isinstance(entry_id, str) or not entry_id:
            findings.append(_truth_finding(f"{location}.id est obligatoire"))
        elif entry_id in ids:
            findings.append(_truth_finding(f"id dupliqué : {entry_id}"))
        else:
            ids.add(entry_id)
        status = entry.get("status")
        if status not in TRUTH_STATUSES:
            findings.append(_truth_finding(f"{location}.status est inconnu : {status}"))
        for field_name in ("domain", "scope", "summary"):
            if not isinstance(entry.get(field_name), str) or not entry[field_name]:
                findings.append(_truth_finding(f"{location}.{field_name} est obligatoire"))
        evidence = entry.get("evidence", [])
        if not isinstance(evidence, list):
            findings.append(_truth_finding(f"{location}.evidence doit être une liste"))
            evidence = []
        evidence_kinds: set[str] = set()
        for evidence_index, item in enumerate(evidence):
            if not isinstance(item, dict) or not isinstance(item.get("path"), str):
                findings.append(
                    _truth_finding(
                        f"{location}.evidence[{evidence_index}] doit fournir un path"
                    )
                )
                continue
            evidence_kinds.add(str(item.get("kind", "")))
            if not (root / item["path"]).exists():
                findings.append(
                    _truth_finding(f"preuve introuvable : {item['path']}")
                )
        gaps = entry.get("gaps", [])
        gates = entry.get("validation_gates", [])
        if not isinstance(gaps, list) or not isinstance(gates, list):
            findings.append(
                _truth_finding(f"{location}.gaps et validation_gates doivent être des listes")
            )
            continue
        if status == "IMPLEMENTED_VERIFIED":
            if not evidence_kinds.intersection({"code", "artifact", "config"}):
                findings.append(
                    _truth_finding(f"{location} vérifié requiert une preuve de code")
                )
            if not evidence_kinds.intersection({"test", "ci"}):
                findings.append(
                    _truth_finding(f"{location} vérifié requiert une preuve test/CI")
                )
            if gates:
                findings.append(
                    _truth_finding(f"{location} vérifié ne peut avoir de gate pending")
                )
        if status == "IMPLEMENTED_NEEDS_REAL_VALIDATION" and not gates:
            findings.append(
                _truth_finding(f"{location} requiert au moins une validation réelle")
            )
        if status in {"PARTIAL", "NOT_IMPLEMENTED"} and not gaps:
            findings.append(_truth_finding(f"{location} requiert au moins un écart"))
        if status == "SUPERSEDED" and not entry.get("superseded_by"):
            findings.append(_truth_finding(f"{location}.superseded_by est obligatoire"))
        if status == "HISTORICAL_ONLY" and not entry.get("snapshot_at"):
            findings.append(_truth_finding(f"{location}.snapshot_at est obligatoire"))

    registry["sha256"] = hashlib.sha256(raw).hexdigest()
    registry["path"] = TRUTH_REGISTRY_PATH
    return registry, findings


def scan_public_privacy(root: Path) -> list[dict[str, Any]]:
    """Refuse les identifiants locaux dans les surfaces versionnées publiques.

    Les fichiers ``.env*`` sont volontairement hors lecture. Les réseaux et
    chemins synthétiques des tests restent permis, mais les identifiants
    personnels connus sont recherchés dans tout fichier texte suivi.
    Les captures de validation dynamiques restent hors Git : elles peuvent
    contenir des contacts, messages, noms de machine ou numéros de téléphone.
    """

    findings: list[dict[str, Any]] = []
    tracked_paths: set[str] | None = None
    if (root / ".git").exists():
        result = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z"],
            capture_output=True,
            check=False,
        )
        if result.returncode == 0:
            tracked_paths = {
                item.decode("utf-8")
                for item in result.stdout.split(b"\0")
                if item
            }
    if tracked_paths is None:
        possible_paths = root.rglob("*")
    else:
        possible_paths = (root / relative for relative in tracked_paths)

    candidates: set[Path] = set()
    for path in possible_paths:
        if not path.is_file() or path.suffix.lower() not in PUBLIC_TEXT_SUFFIXES:
            continue
        if path.name.startswith(".env"):
            continue
        candidates.add(path)

    private_networks = (
        ipaddress.ip_network("10.0.0.0/8"),
        ipaddress.ip_network("172.16.0.0/12"),
        ipaddress.ip_network("192.168.0.0/16"),
    )
    for path in sorted(candidates):
        relative = path.relative_to(root).as_posix()
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        fixture_path = any(
            part in PUBLIC_PRIVACY_IGNORED_PARTS
            for part in path.relative_to(root).parts
        ) or ".test." in path.name or ".spec." in path.name
        for line_number, line in enumerate(lines, 1):
            if PUBLIC_PERSONAL_TOKEN_RE.search(line):
                findings.append(
                    {
                        "file": relative,
                        "line": line_number,
                        "kind": "public_personal_identifier",
                        "severity": "error",
                        "excerpt": "personal identifier",
                        "note": "remplacer l'identifiant personnel par une fixture synthétique",
                    }
                )
            if fixture_path:
                continue
            if ABSOLUTE_MAC_USER_PATH_RE.search(line):
                findings.append(
                    {
                        "file": relative,
                        "line": line_number,
                        "kind": "public_absolute_user_path",
                        "severity": "error",
                        "excerpt": "absolute macOS user path",
                        "note": "remplacer le chemin utilisateur par un placeholder portable",
                    }
                )
            for match in IPV4_RE.finditer(line):
                value = match.group(0)
                if value in PUBLIC_PRIVATE_IP_ALLOWLIST:
                    continue
                try:
                    address = ipaddress.ip_address(value)
                except ValueError:
                    continue
                if any(address in network for network in private_networks):
                    findings.append(
                        {
                            "file": relative,
                            "line": line_number,
                            "kind": "public_private_ipv4",
                            "severity": "error",
                            "excerpt": "private IPv4 address",
                            "note": "remplacer l'adresse privée par un placeholder explicite",
                        }
                    )

    screenshot_root = root / "artifacts" / "validation_screenshots"
    if screenshot_root.is_dir():
        for path in sorted(screenshot_root.rglob("*")):
            relative = path.relative_to(root).as_posix()
            if (
                path.is_file()
                and path.suffix.lower() in {".jpeg", ".jpg", ".png", ".webp"}
                and (tracked_paths is None or relative in tracked_paths)
            ):
                findings.append(
                    {
                        "file": relative,
                        "line": 0,
                        "kind": "public_validation_screenshot",
                        "severity": "error",
                        "excerpt": "dynamic validation screenshot",
                        "note": (
                            "retirer la capture dynamique de Git ou la remplacer par "
                            "une fixture entièrement synthétique"
                        ),
                    }
                )
    complement_report = root / "artifacts" / "complement_report.json"
    report = _read_json(complement_report)
    if isinstance(report, dict):
        pending: list[Any] = [report]
        while pending:
            value = pending.pop()
            if isinstance(value, dict):
                screenshot = value.get("screenshot")
                if isinstance(screenshot, str) and screenshot.strip():
                    findings.append(
                        {
                            "file": "artifacts/complement_report.json",
                            "line": 0,
                            "kind": "public_missing_screenshot_reference",
                            "severity": "error",
                            "excerpt": "removed screenshot reference",
                            "note": "mettre la référence à null et documenter le retrait PII",
                        }
                    )
                pending.extend(value.values())
            elif isinstance(value, list):
                pending.extend(value)
    return findings


def _current_truth_documents(
    registry: dict[str, Any], claim: str | None = None
) -> list[str]:
    documents = registry.get("documentation", {}).get("current", [])
    result: list[str] = []
    for document in documents:
        if not isinstance(document, dict) or not isinstance(document.get("path"), str):
            continue
        if claim is None or claim in document.get("required_claims", []):
            result.append(document["path"])
    if claim is None:
        result.extend(
            item
            for item in registry.get("documentation", {}).get("current_paths", [])
            if isinstance(item, str)
        )
    return result


def analyze_events(root: Path) -> dict[str, Any]:
    """Inventorie statiquement le contrat fermé du bus d'événements."""

    source = root / "jarvis/event_bus.py"
    if not source.is_file():
        return {
            "source": "jarvis/event_bus.py",
            "count": 0,
            "types": [],
            "agentic_count": 0,
            "task_control_count": 0,
        }
    module = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    values: tuple[str, ...] = ()
    for node in module.body:
        target = node.target if isinstance(node, ast.AnnAssign) else None
        if isinstance(target, ast.Name) and target.id == "EVENT_TYPES":
            raw = ast.literal_eval(node.value)
            if isinstance(raw, tuple) and all(isinstance(item, str) for item in raw):
                values = raw
            break
    return {
        "source": "jarvis/event_bus.py",
        "count": len(values),
        "types": list(values),
        "agentic_count": sum(item.startswith("agent.") for item in values),
        "task_control_count": sum(item.startswith("task.control.") for item in values),
    }


def analyze_plugins(root: Path) -> dict[str, Any]:
    """Inventorie les manifests découverts sans importer leur code."""

    plugins: list[dict[str, Any]] = []
    for path in sorted((root / "integrations").glob("*/plugin.json")):
        raw = _read_json(path)
        if not isinstance(raw, dict):
            continue
        runtime = raw.get("runtime") if isinstance(raw.get("runtime"), dict) else raw
        capabilities = runtime.get("capabilities", raw.get("capabilities", []))
        plugins.append(
            {
                "path": path.relative_to(root).as_posix(),
                "runtime_id": str(runtime.get("id") or runtime.get("runtime_id") or ""),
                "version": str(runtime.get("version") or ""),
                "enabled": bool(runtime.get("enabled", raw.get("enabled", True))),
                "entrypoint": str(runtime.get("entrypoint") or ""),
                "capability_count": len(capabilities) if isinstance(capabilities, list) else 0,
            }
        )
    return {
        "discovery_glob": "integrations/*/plugin.json",
        "count": len(plugins),
        "plugins": plugins,
    }


def _extract_create_tables(text: str) -> list[str]:
    return sorted(set(CREATE_TABLE_RE.findall(text)))


def _static_string(node: ast.AST | None, constants: dict[str, str]) -> str | None:
    """Évalue les chemins de route statiques sans exécuter le module."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return constants.get(node.id)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _static_string(node.left, constants)
        right = _static_string(node.right, constants)
        return left + right if left is not None and right is not None else None
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
            elif isinstance(value, ast.FormattedValue):
                expression = ast.unparse(value.value) if hasattr(ast, "unparse") else "value"
                parts.append("{" + expression + "}")
            else:
                return None
        return "".join(parts)
    return None


def _string_constants(tree: ast.Module) -> dict[str, str]:
    constants: dict[str, str] = {}
    for statement in tree.body:
        if not isinstance(statement, (ast.Assign, ast.AnnAssign)):
            continue
        value = _static_string(statement.value, constants)
        if value is None:
            continue
        targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
        for target in targets:
            if isinstance(target, ast.Name):
                constants[target.id] = value
    return constants


def _imported_string_constants(
    root: Path,
    tree: ast.Module,
) -> dict[str, str]:
    """Résout les constantes importées depuis un module Python du dépôt."""
    constants: dict[str, str] = {}
    for statement in tree.body:
        if not isinstance(statement, ast.ImportFrom) or statement.level != 0:
            continue
        module = statement.module
        if not module:
            continue
        module_path = root.joinpath(*module.split(".")).with_suffix(".py")
        if not module_path.is_file():
            continue
        try:
            imported_tree = ast.parse(
                module_path.read_text(encoding="utf-8"),
                filename=str(module_path),
            )
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue
        imported_constants = _string_constants(imported_tree)
        for alias in statement.names:
            value = imported_constants.get(alias.name)
            if value is not None:
                constants[alias.asname or alias.name] = value
    return constants


def _call_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _router_prefixes(tree: ast.Module, constants: dict[str, str]) -> dict[str, str]:
    prefixes = {"app": ""}
    for statement in tree.body:
        if not isinstance(statement, (ast.Assign, ast.AnnAssign)):
            continue
        call = statement.value
        if not isinstance(call, ast.Call) or _call_name(call.func) != "APIRouter":
            continue
        prefix = ""
        for keyword in call.keywords:
            if keyword.arg == "prefix":
                prefix = _static_string(keyword.value, constants) or ""
                break
        targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
        for target in targets:
            if isinstance(target, ast.Name):
                prefixes[target.id] = prefix.rstrip("/")
    return prefixes


def _route_path(prefix: str, raw_path: str) -> str:
    path = f"{prefix}/{raw_path.lstrip('/')}" if prefix else raw_path
    if not path.startswith("/"):
        path = "/" + path
    return path.rstrip("/") or "/"


def _api_path_in_scope(path: str) -> bool:
    return (
        path == "/api"
        or path.startswith("/api/")
        or path == "/ws"
        or path.startswith("/ws/")
        or path in API_ROUTE_SPECIAL_PATHS
    )


def _literal_methods(node: ast.AST | None) -> tuple[str, ...]:
    if not isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return ()
    methods = [
        element.value.upper()
        for element in node.elts
        if isinstance(element, ast.Constant) and isinstance(element.value, str)
    ]
    return tuple(sorted(set(methods)))


def _routes_from_python(root: Path, path: Path) -> list[ApiRoute]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return []
    constants = _imported_string_constants(root, tree)
    constants.update(_string_constants(tree))
    prefixes = _router_prefixes(tree, constants)
    relative = path.relative_to(root).as_posix()
    routes: list[ApiRoute] = []

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call) or not isinstance(
                    decorator.func, ast.Attribute
                ):
                    continue
                receiver = decorator.func.value
                if not isinstance(receiver, ast.Name) or receiver.id not in prefixes:
                    continue
                method_name = decorator.func.attr.lower()
                methods = HTTP_ROUTE_METHODS.get(method_name)
                if method_name == "api_route":
                    methods_node = next(
                        (kw.value for kw in decorator.keywords if kw.arg == "methods"),
                        None,
                    )
                    methods = _literal_methods(methods_node)
                if not methods or not decorator.args:
                    continue
                raw_path = _static_string(decorator.args[0], constants)
                if raw_path is None:
                    continue
                route_path = _route_path(prefixes[receiver.id], raw_path)
                if not _api_path_in_scope(route_path):
                    continue
                routes.extend(
                    ApiRoute(method, route_path, relative, decorator.lineno)
                    for method in methods
                )

        if not isinstance(node, ast.Expr) or not isinstance(node.value, ast.Call):
            continue
        call = node.value
        # Enregistrement fonctionnel : app.websocket("/ws")(handler).
        if isinstance(call.func, ast.Call) and isinstance(call.func.func, ast.Attribute):
            registration = call.func
            receiver = registration.func.value
            if isinstance(receiver, ast.Name) and receiver.id in prefixes:
                methods = HTTP_ROUTE_METHODS.get(registration.func.attr.lower())
                raw_path = (
                    _static_string(registration.args[0], constants)
                    if registration.args
                    else None
                )
                if methods and raw_path is not None:
                    route_path = _route_path(prefixes[receiver.id], raw_path)
                    if _api_path_in_scope(route_path):
                        routes.extend(
                            ApiRoute(method, route_path, relative, registration.lineno)
                            for method in methods
                        )
            continue
        if not isinstance(call.func, ast.Attribute):
            continue
        receiver = call.func.value
        if not isinstance(receiver, ast.Name) or receiver.id not in prefixes or not call.args:
            continue
        call_kind = call.func.attr
        if call_kind not in {"add_api_route", "add_websocket_route"}:
            continue
        raw_path = _static_string(call.args[0], constants)
        if raw_path is None:
            continue
        route_path = _route_path(prefixes[receiver.id], raw_path)
        if not _api_path_in_scope(route_path):
            continue
        if call_kind == "add_websocket_route":
            methods = ("WEBSOCKET",)
        else:
            methods_node = next(
                (kw.value for kw in call.keywords if kw.arg == "methods"),
                None,
            )
            methods = _literal_methods(methods_node) or ("GET",)
        routes.extend(
            ApiRoute(method, route_path, relative, call.lineno) for method in methods
        )
    return routes


def discover_api_routes(root: Path) -> list[ApiRoute]:
    """Inventorie statiquement les routes publiques montées par JARVIS."""
    candidates = [root / "main.py"]
    for source_root in API_ROUTE_SOURCE_ROOTS:
        directory = root / source_root
        if directory.is_dir():
            candidates.extend(sorted(directory.rglob("*.py")))
    routes = {
        route
        for candidate in candidates
        if candidate.is_file()
        for route in _routes_from_python(root, candidate)
    }
    return sorted(routes)


def _is_test_source(path: Path, root: Path) -> bool:
    relative = path.relative_to(root)
    lowered_parts = tuple(part.lower() for part in relative.parts)
    name = path.name.lower()
    return (
        "tests" in lowered_parts
        or "test" in lowered_parts
        or "androidtest" in lowered_parts
        or name.startswith("test_")
        or ".test." in name
        or ".spec." in name
        or name.endswith("tests.swift")
    )


def _iter_reference_sources(
    root: Path,
    source_roots: tuple[str, ...],
    *,
    tests: bool,
) -> list[Path]:
    sources: set[Path] = set()
    for source_root in source_roots:
        directory = root / source_root
        if not directory.exists():
            continue
        candidates = [directory] if directory.is_file() else directory.rglob("*")
        for path in candidates:
            if not path.is_file() or path.suffix.lower() not in SOURCE_SUFFIXES:
                continue
            relative = path.relative_to(root)
            if any(part in IGNORED_SOURCE_PARTS for part in relative.parts):
                continue
            if _is_test_source(path, root) != tests:
                continue
            sources.add(path)
    return sorted(sources)


def _route_reference_pattern(path: str) -> re.Pattern[str]:
    """Accepte `{id}`, `${id}` ou une valeur concrète pour chaque paramètre."""
    pieces: list[str] = []
    for part in path.split("/"):
        if not part:
            continue
        if re.fullmatch(r"\{[^}/]+(?::[^}]+)?\}", part):
            pieces.append(r"[^/?#\s\"'`]+")
        else:
            pieces.append(re.escape(part))
    body = "/" + "/".join(pieces)
    # Retrofit emploie des chemins relatifs (`@POST("api/...")`) tandis que
    # fetch/OkHttp et les tests emploient généralement `/api/...`.
    if body == "/api" or body.startswith("/api/"):
        body = "/?" + body.lstrip("/")
    return re.compile(
        rf"(?<![A-Za-z0-9_-]){body}(?=$|[?#\s\"'`),;\]])"
    )


def _reference_map(
    root: Path,
    route_paths: set[str],
    source_roots: tuple[str, ...],
    *,
    tests: bool,
) -> dict[str, list[str]]:
    patterns = {path: _route_reference_pattern(path) for path in sorted(route_paths)}
    references: dict[str, set[str]] = {path: set() for path in route_paths}
    for source in _iter_reference_sources(root, source_roots, tests=tests):
        try:
            text = source.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        text = "\n".join(
            line
            for line in text.splitlines()
            if NON_CONSUMER_REFERENCE_MARKER not in line
        )
        relative = source.relative_to(root).as_posix()
        for path, pattern in patterns.items():
            if pattern.search(text):
                references[path].add(relative)
    return {path: sorted(files) for path, files in references.items()}


def _load_api_ownership_policy(root: Path) -> dict[str, Any] | None:
    policy = _read_json(root / API_ROUTE_OWNERSHIP_POLICY)
    if not isinstance(policy, dict):
        return None
    return policy


def _ownership_for_route(
    route: ApiRoute,
    rules: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    for rule in rules:
        methods = rule.get("methods")
        paths = rule.get("paths")
        if not isinstance(methods, list) or not isinstance(paths, list):
            continue
        if route.method in methods and route.path in paths:
            matches.append(rule)
    return matches


def _validate_ownership_policy(
    routes: list[ApiRoute],
    consumers_by_route: dict[ApiRoute, dict[str, list[str]]],
    policy: dict[str, Any] | None,
) -> tuple[dict[ApiRoute, dict[str, Any]], list[dict[str, Any]]]:
    """Exige une attribution exacte pour toute opération sans client direct."""
    if policy is None:
        return {}, []
    raw_rules = policy.get("rules")
    rules = raw_rules if isinstance(raw_rules, list) else []
    findings: list[dict[str, Any]] = []
    if policy.get("schema_version") != 1:
        findings.append(
            {
                "file": API_ROUTE_OWNERSHIP_POLICY,
                "line": 0,
                "kind": "invalid_api_ownership_schema",
                "severity": "error",
                "excerpt": str(policy.get("schema_version")),
                "note": "schema_version doit être égal à 1.",
            }
        )
    if not isinstance(raw_rules, list):
        findings.append(
            {
                "file": API_ROUTE_OWNERSHIP_POLICY,
                "line": 0,
                "kind": "invalid_api_ownership_schema",
                "severity": "error",
                "excerpt": "rules",
                "note": "rules doit être une liste JSON.",
            }
        )
    valid_rules: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    required_text = ("id", "owner", "audience", "rationale")
    for index, raw_rule in enumerate(rules):
        if not isinstance(raw_rule, dict):
            findings.append(
                {
                    "file": API_ROUTE_OWNERSHIP_POLICY,
                    "line": 0,
                    "kind": "invalid_api_ownership_rule",
                    "severity": "error",
                    "excerpt": f"rules[{index}]",
                    "note": "Chaque règle doit être un objet JSON.",
                }
            )
            continue
        missing = [
            key
            for key in required_text
            if not isinstance(raw_rule.get(key), str) or not raw_rule[key].strip()
        ]
        methods = raw_rule.get("methods")
        paths = raw_rule.get("paths")
        if not isinstance(methods, list) or not methods or not all(
            isinstance(method, str) and method for method in methods
        ):
            missing.append("methods")
        if not isinstance(paths, list) or not paths or not all(
            isinstance(path, str) and path.startswith("/") for path in paths
        ):
            missing.append("paths")
        rule_id = str(raw_rule.get("id") or "")
        if rule_id in seen_ids:
            missing.append("id unique")
        if missing:
            findings.append(
                {
                    "file": API_ROUTE_OWNERSHIP_POLICY,
                    "line": 0,
                    "kind": "invalid_api_ownership_rule",
                    "severity": "error",
                    "excerpt": rule_id or f"rules[{index}]",
                    "note": "Champs absents ou invalides : " + ", ".join(missing),
                }
            )
            continue
        if raw_rule["audience"] not in NON_FRONTEND_AUDIENCES:
            findings.append(
                {
                    "file": API_ROUTE_OWNERSHIP_POLICY,
                    "line": 0,
                    "kind": "invalid_api_ownership_rule",
                    "severity": "error",
                    "excerpt": rule_id,
                    "note": f"Audience non reconnue : {raw_rule['audience']}",
                }
            )
            continue
        seen_ids.add(rule_id)
        valid_rules.append(raw_rule)

    ownership: dict[ApiRoute, dict[str, Any]] = {}
    used_rule_ids: set[str] = set()
    for route in routes:
        matches = _ownership_for_route(route, valid_rules)
        has_consumers = bool(consumers_by_route[route])
        operation = f"{route.method} {route.path}"
        if has_consumers and matches:
            findings.append(
                {
                    "file": API_ROUTE_OWNERSHIP_POLICY,
                    "line": 0,
                    "kind": "ownership_rule_masks_client_route",
                    "severity": "error",
                    "excerpt": operation,
                    "note": (
                        "Une route consommée par un client ne doit pas rester classée "
                        "comme surface non-frontend."
                    ),
                }
            )
            continue
        if has_consumers:
            continue
        if len(matches) != 1:
            findings.append(
                {
                    "file": API_ROUTE_OWNERSHIP_POLICY,
                    "line": 0,
                    "kind": "unowned_api_operation" if not matches else "ambiguous_api_ownership",
                    "severity": "error",
                    "excerpt": operation,
                    "note": (
                        "Aucune attribution non-frontend documentée."
                        if not matches
                        else "Plusieurs règles attribuent la même opération."
                    ),
                }
            )
            continue
        rule = matches[0]
        used_rule_ids.add(rule["id"])
        ownership[route] = {
            "rule_id": rule["id"],
            "owner": rule["owner"],
            "audience": rule["audience"],
            "rationale": rule["rationale"],
        }

    for rule in valid_rules:
        matching_routes = [
            route
            for route in routes
            if route.method in rule["methods"] and route.path in rule["paths"]
        ]
        stale_paths = sorted(
            path
            for path in rule["paths"]
            if not any(route.path == path for route in matching_routes)
        )
        stale_methods = sorted(
            method
            for method in rule["methods"]
            if not any(route.method == method for route in matching_routes)
        )
        if stale_paths or stale_methods:
            details = []
            if stale_paths:
                details.append("chemins=" + ", ".join(stale_paths))
            if stale_methods:
                details.append("méthodes=" + ", ".join(stale_methods))
            findings.append(
                {
                    "file": API_ROUTE_OWNERSHIP_POLICY,
                    "line": 0,
                    "kind": "stale_api_ownership_entry",
                    "severity": "error",
                    "excerpt": rule["id"],
                    "note": "Entrées sans opération correspondante : " + "; ".join(details),
                }
            )
        if rule["id"] not in used_rule_ids:
            findings.append(
                {
                    "file": API_ROUTE_OWNERSHIP_POLICY,
                    "line": 0,
                    "kind": "stale_api_ownership_rule",
                    "severity": "error",
                    "excerpt": rule["id"],
                    "note": "La règle ne correspond à aucune opération non-frontend.",
                }
            )
    return ownership, findings


def analyze_api_surface(root: Path) -> dict[str, Any]:
    routes = discover_api_routes(root)
    route_paths = {route.path for route in routes}
    surface_references = {
        surface: _reference_map(root, route_paths, roots, tests=False)
        for surface, roots in CONSUMER_SURFACES.items()
    }
    test_references = _reference_map(
        root,
        route_paths,
        TEST_SOURCE_ROOTS,
        tests=True,
    )
    consumers_by_route = {
        route: {
            surface: references[route.path]
            for surface, references in surface_references.items()
            if references[route.path]
        }
        for route in routes
    }
    policy = _load_api_ownership_policy(root)
    ownership_by_route, ownership_findings = _validate_ownership_policy(
        routes,
        consumers_by_route,
        policy,
    )
    inventory: list[dict[str, Any]] = []
    classification_counts: dict[str, int] = {}
    for route in routes:
        consumers = consumers_by_route[route]
        tests = test_references[route.path]
        ownership = ownership_by_route.get(route)
        if consumers and tests:
            classification = "consumer_and_tested"
        elif consumers:
            classification = "consumer_without_path_test"
        elif ownership and tests:
            classification = "owned_non_frontend_and_tested"
        elif ownership:
            classification = "owned_non_frontend_without_path_test"
        elif tests:
            classification = "server_only_tested"
        else:
            classification = "unreferenced"
        classification_counts[classification] = classification_counts.get(classification, 0) + 1
        inventory.append(
            {
                "method": route.method,
                "path": route.path,
                "source": route.source,
                "line": route.line,
                "consumers": consumers,
                "tests": tests,
                "non_frontend_ownership": ownership,
                "classification": classification,
            }
        )
    main_path = root / "main.py"
    mounted_routers = 0
    if main_path.is_file():
        main_tree = ast.parse(
            main_path.read_text(encoding="utf-8"),
            filename=str(main_path),
        )
        mounted_routers = sum(
            1
            for node in ast.walk(main_tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "include_router"
        )
    websocket_operations = sum(route.method == "WEBSOCKET" for route in routes)
    structure = {
        "http_operations": len(routes) - websocket_operations,
        "websocket_operations": websocket_operations,
        "openapi_paths": len(
            {route.path for route in routes if route.method != "WEBSOCKET"}
        ),
        "domain_router_modules": len(list((root / "api").glob("router_*.py"))),
        "mounted_routers": mounted_routers,
        "main_lines": (
            len(main_path.read_text(encoding="utf-8").splitlines())
            if main_path.is_file()
            else 0
        ),
    }
    return {
        "coverage_scope": (
            "Références statiques par chemin : la méthode HTTP et les assertions "
            "comportementales restent vérifiées par les suites de tests."
        ),
        "counts": {
            "operations": len(routes),
            "paths": len(route_paths),
            **dict(sorted(classification_counts.items())),
        },
        "consumer_surfaces": sorted(CONSUMER_SURFACES),
        "structure": structure,
        "ownership_policy": (
            {
                "path": API_ROUTE_OWNERSHIP_POLICY,
                "rules": len(policy.get("rules") or []),
                "findings": ownership_findings,
            }
            if policy is not None
            else {"path": None, "rules": 0, "findings": []}
        ),
        "routes": inventory,
    }


def _pnpm_importer_versions(
    lock_text: str, packages: tuple[str, ...]
) -> dict[str, str]:
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


def _npm_lock_versions(
    lock: dict[str, Any], packages: tuple[str, ...]
) -> dict[str, str]:
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
        ("web", "react-component-library", None, "bibliotheque_vues_desktop"),
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

    # Dashboard TV autonome (pas de package.json).
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
    fastapi_aligned = "resolve_desktop_frontend_roots" in frontend_py and (
        "_setup_unified_frontend" in frontend_py
        and "is_usable_next_build" in frontend_py
    )
    shared_priority_ok = (
        "next_canonical" in shared
        and "is_usable_next_build" in shared
        and "vite_fallback" not in shared
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
            "web_mobile/ monté sous /mobile/, téléphones redirigés",
            "frontend/out (Next.js 15 bureau)",
            "503 explicite si le build bureau manque",
        ],
        "fastapi_uses_unified_first": fastapi_aligned,
        "supervisor_priority": (
            "frontend/out_only"
            if supervisor_uses_resolver and not supervisor_vite_only
            else ("web/dist_only" if supervisor_vite_only else "unknown")
        ),
        "supervisor_uses_shared_resolver": supervisor_uses_resolver,
        "shared_resolution_module": shared_priority_ok,
        "priority_findings": findings,
        "paths": {
            "FRONTEND_DIST_DIR_default": "frontend/out",
            "WEB_MOBILE_DIR_default": "web_mobile",
            "TV_PORT_default": 5174,
            "BACKEND_PORT_typical": 8081,
            "SUPERVISOR_PORT_default": 9000,
        },
        "build_presence": {
            "frontend/out": (root / "frontend" / "out" / "index.html").is_file(),
            "web_mobile": (root / "web_mobile" / "index.html").is_file(),
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

    imessage_mirror = sorted(
        t
        for t in persistantes
        if t.startswith("imessage_") and t != "imessage_analysis_cache"
    )
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
            "device_pairing_codes",
            "device_pairing_attempts",
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
            "physiques_max_default_fts_on": len(persistantes)
            + (5 if fts_declared else 0),
            "imessage_mirror": len(imessage_mirror),
            "technique_estime": len(tech),
            "metier_estime": len(persistantes)
            - len(imessage_mirror)
            - len(t_dev)
            - len(tech),
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
            "schema_sql": (
                f"{len(t_sql)} tables applicatives dans database/schema.sql "
                "(dump non exécuté), hors sqlite_sequence."
            ),
            "persistantes": (
                f"{len(persistantes)} tables créées par schema.py + migrations "
                "+ DevAgent, hors objets FTS."
            ),
            "physiques_fts": (
                f"{len(persistantes) + (5 if fts_declared else 0)} entrées sqlite_master "
                "avec les cinq objets FTS5 disponibles."
            ),
            "historique": (
                "Les totaux 44, 70, 71, 72, 73, 75, 76 et 78 décrivent "
                "des snapshots antérieurs, pas le runtime courant."
            ),
        },
    }


def render_runtime_schema(root: Path) -> str:
    """Construit le DDL déterministe d'une base fraîche entièrement migrée."""
    schema_namespace: dict[str, Any] = {}
    schema_path = root / "database" / "schema.py"
    exec(
        compile(schema_path.read_text(encoding="utf-8"), str(schema_path), "exec"),
        schema_namespace,
    )
    schema = schema_namespace.get("SCHEMA")
    if not isinstance(schema, str):
        raise RuntimeError("database/schema.py ne définit pas SCHEMA")

    root_value = str(root)
    inserted_path = root_value not in sys.path
    if inserted_path:
        sys.path.insert(0, root_value)

    migration_path = root / "database" / "migrations.py"
    spec = importlib.util.spec_from_file_location(
        "database._jarvis_architecture_truth_migrations",
        migration_path,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Impossible de charger database/migrations.py")
    migrations = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migrations)

    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript(schema)
        migrations.run_migrations(conn)
        conn.commit()
        rows = conn.execute("""
            SELECT type, name, tbl_name, sql
            FROM sqlite_master
            WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%'
            ORDER BY CASE type
                WHEN 'table' THEN 0
                WHEN 'index' THEN 1
                WHEN 'trigger' THEN 2
                ELSE 3
            END, name
            """).fetchall()
    finally:
        conn.close()
        if inserted_path:
            sys.path.remove(root_value)

    virtual_tables = {
        name
        for type_name, name, _table_name, sql in rows
        if type_name == "table"
        and sql.lstrip().upper().startswith("CREATE VIRTUAL TABLE")
    }
    shadow_tables = {
        f"{virtual_name}_{suffix}"
        for virtual_name in virtual_tables
        for suffix in FTS_SHADOW_SUFFIXES
    }
    statements = [
        sql.rstrip().rstrip(";") + ";"
        for type_name, name, table_name, sql in rows
        if name not in shadow_tables and table_name not in shadow_tables
    ]
    return (
        "-- GENERATED FILE — DO NOT EDIT.\n"
        "-- Source: database/schema.py + database/migrations.py + database/devagent.py.\n"
        "-- Regenerate: python tools/audit_architecture_truth.py "
        "--schema-output database/schema.sql\n"
        "-- This artifact is not executed by init_db(); it mirrors a fresh runtime schema.\n\n"
        + "\n\n".join(statements)
        + "\n"
    )


def _stable_report(report: dict[str, Any]) -> dict[str, Any]:
    """Retire les seules valeurs propres au checkout ou à l'instant d'exécution."""
    stable = copy.deepcopy(report)
    stable.pop("generated_at", None)
    for frontend in stable.get("frontends", []):
        frontend.pop("output_present", None)
    stable.get("resolution", {}).pop("build_presence", None)
    return stable


def _canonical_count_line(tables: dict[str, Any]) -> str:
    counts = tables["counts"]
    return (
        "Runtime SQLite canonique : "
        f"**{counts['persistantes_post_init']} tables persistantes**, "
        f"**{counts['physiques_max_default_fts_on']} tables physiques avec FTS5**, "
        f"schéma généré : **{counts['schema_sql_applicatives']} déclarations de tables**."
    )


def _canonical_api_line(api_surface: dict[str, Any]) -> str:
    counts = api_surface["counts"]
    return (
        "Surface API canonique : "
        f"**{counts['operations']} opérations**, **{counts['paths']} chemins**, "
        f"**{counts.get('consumer_and_tested', 0)} consommées et testées**, "
        f"**{counts.get('consumer_without_path_test', 0)} consommées sans référence de test**, "
        f"**{counts.get('owned_non_frontend_and_tested', 0)} non-frontend documentées et testées**, "
        f"**{counts.get('owned_non_frontend_without_path_test', 0)} non-frontend documentées sans référence de test**, "
        f"**{counts.get('server_only_tested', 0) + counts.get('unreferenced', 0)} non attribuées**."
    )


def _canonical_api_structure_line(api_surface: dict[str, Any]) -> str:
    structure = api_surface["structure"]
    return (
        "Structure API canonique : "
        f"**{structure['http_operations']} opérations HTTP + "
        f"{structure['websocket_operations']} WebSockets**, "
        f"**{structure['openapi_paths']} chemins OpenAPI**, "
        f"**{structure['domain_router_modules']} routeurs api/router_*.py + "
        f"Fitness = {structure['mounted_routers']} montés**, "
        f"main.py **{structure['main_lines']} lignes**."
    )


def scan_canonical_count_docs(
    root: Path, tables: dict[str, Any], registry: dict[str, Any]
) -> list[dict[str, Any]]:
    """Exige la formulation calculée dans chaque document de référence courant."""
    expected = _canonical_count_line(tables)
    findings: list[dict[str, Any]] = []
    for rel in _current_truth_documents(registry, "database"):
        path = root / rel
        text = path.read_text(encoding="utf-8") if path.is_file() else ""
        if expected not in text:
            findings.append(
                {
                    "file": rel,
                    "line": 0,
                    "kind": "canonical_sqlite_counts",
                    "severity": "error",
                    "excerpt": "formulation canonique absente ou périmée",
                    "note": f"Attendu exactement : {expected}",
                }
            )
    return findings


def scan_canonical_api_doc(
    root: Path, api_surface: dict[str, Any], registry: dict[str, Any]
) -> list[dict[str, Any]]:
    """Synchronise le résumé humain avec l'inventaire API généré."""
    expected = _canonical_api_line(api_surface)
    findings: list[dict[str, Any]] = []
    for relative in _current_truth_documents(registry, "api_surface"):
        path = root / relative
        text = path.read_text(encoding="utf-8") if path.is_file() else ""
        if expected not in text:
            findings.append(
                {
                    "file": relative,
                    "line": 0,
                    "kind": "canonical_api_surface_counts",
                    "severity": "error",
                    "excerpt": "formulation canonique API absente ou périmée",
                    "note": f"Attendu exactement : {expected}",
                }
            )
    return findings


def scan_api_structure_docs(
    root: Path, api_surface: dict[str, Any], registry: dict[str, Any]
) -> list[dict[str, Any]]:
    """Exige le fingerprint API calculé dans ses documents courants."""
    expected = _canonical_api_structure_line(api_surface)
    findings: list[dict[str, Any]] = []
    for relative in _current_truth_documents(registry, "api_structure"):
        path = root / relative
        text = path.read_text(encoding="utf-8") if path.is_file() else ""
        if expected not in text:
            findings.append(
                {
                    "file": relative,
                    "line": 0,
                    "kind": "canonical_api_structure",
                    "severity": "error",
                    "excerpt": "formulation canonique API absente ou périmée",
                    "note": f"Attendu exactement : {expected}",
                }
            )

    return findings


def scan_canonical_frontend_docs(
    root: Path, registry: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    """Interdit les anciens runtimes frontend dans les documents courants.

    Les rapports historiques datés ne font volontairement pas partie de ce
    périmètre. Ils peuvent conserver leur photographie si elle est explicitement
    marquée comme archive.
    """
    findings: list[dict[str, Any]] = []
    relatives = (
        _current_truth_documents(registry, "frontend")
        if registry
        else list(CANONICAL_FRONTEND_DOCS)
    )
    for relative in relatives:
        path = root / relative
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        for kind, pattern in RETIRED_FRONTEND_DOC_PATTERNS:
            for match in pattern.finditer(text):
                findings.append(
                    {
                        "file": relative,
                        "line": text.count("\n", 0, match.start()) + 1,
                        "kind": kind,
                        "severity": "error",
                        "excerpt": match.group(0),
                        "note": (
                            "Runtime frontend retiré dans un document courant ; "
                            "attendu : frontend/out uniquement pour le bureau, "
                            "web/src comme bibliothèque et web_mobile sous /mobile/."
                        ),
                    }
                )
    return findings


def _numeric_claim_finding(
    relative: str,
    line_number: int,
    kind: str,
    excerpt: str,
    actual: int,
    expected: set[int],
) -> dict[str, Any]:
    expected_text = " ou ".join(str(value) for value in sorted(expected))
    return {
        "file": relative,
        "line": line_number,
        "kind": kind,
        "severity": "error",
        "excerpt": excerpt.strip(),
        "note": f"assertion {actual} contradictoire ; attendu {expected_text}",
    }


def scan_numeric_claims(
    root: Path,
    tables: dict[str, Any],
    api_surface: dict[str, Any],
    registry: dict[str, Any],
) -> list[dict[str, Any]]:
    """Détecte toute valeur typée contradictoire dans les documents courants.

    Contrairement à une blacklist de nombres historiques, les attentes viennent
    du code audité. Une nouvelle valeur périmée échoue donc sans modifier ce
    scanner. Les documents historiques et superseded sont exclus par registre.
    """
    findings: list[dict[str, Any]] = []
    counts = tables["counts"]
    structure = api_surface["structure"]
    surface_counts = api_surface["counts"]
    database_patterns: tuple[tuple[str, re.Pattern[str], set[int]], ...] = (
        (
            "sqlite_persistent_tables",
            re.compile(r"(?P<value>\d+)\s+(?:tables?\s+)?persistantes?", re.I),
            {counts["persistantes_post_init"]},
        ),
        (
            "sqlite_physical_tables",
            re.compile(r"(?P<value>\d+)\s+(?:tables?\s+)?physiques?", re.I),
            {counts["physiques_max_default_fts_on"]},
        ),
        (
            "sqlite_schema_declarations",
            re.compile(r"(?P<value>\d+)\s+déclarations?\s+de\s+tables?", re.I),
            {counts["schema_sql_applicatives"]},
        ),
        (
            "sqlite_table_count",
            re.compile(r"(?P<value>\d+)\+?\s+tables?\s+(?:SQLite|SQL)\b", re.I),
            {
                counts["persistantes_post_init"],
                counts["physiques_max_default_fts_on"],
            },
        ),
    )
    api_surface_patterns: tuple[tuple[str, re.Pattern[str], set[int]], ...] = (
        (
            "api_operations",
            re.compile(
                r"(?P<value>\d+)\s+opérations?\b"
                r"(?!\s+(?:HTTP|sans|avec|consommées?|attribuées?|distinctes?)\b)",
                re.I,
            ),
            {surface_counts["operations"]},
        ),
        (
            "api_paths",
            re.compile(
                r"(?P<value>\d+)\s+chemins?\b"
                r"(?!\s+(?:OpenAPI|sans|avec|consommés?|référencés?|distincts?)\b)",
                re.I,
            ),
            {surface_counts["paths"]},
        ),
    )
    api_structure_patterns: tuple[tuple[str, re.Pattern[str], set[int]], ...] = (
        (
            "api_http_operations",
            re.compile(r"(?P<value>\d+)\s+opérations?\s+HTTP\b", re.I),
            {structure["http_operations"]},
        ),
        (
            "api_openapi_paths",
            re.compile(r"(?P<value>\d+)\s+chemins?\s+OpenAPI\b", re.I),
            {structure["openapi_paths"]},
        ),
        (
            "api_websockets",
            re.compile(r"(?P<value>\d+)\s+WebSockets?\b", re.I),
            {structure["websocket_operations"]},
        ),
        (
            "api_routers",
            re.compile(r"(?P<value>\d+)\s+routeurs?\b", re.I),
            {structure["domain_router_modules"], structure["mounted_routers"]},
        ),
        (
            "api_main_lines",
            re.compile(r"main\.py[^\d\n]{0,24}(?P<value>\d+)\s+lignes?", re.I),
            {structure["main_lines"]},
        ),
        (
            "api_main_lines",
            re.compile(r"(?P<value>\d+)\s+lignes?[^\n]{0,24}main\.py", re.I),
            {structure["main_lines"]},
        ),
    )

    for claim, patterns in (
        ("database", database_patterns),
        ("api_surface", api_surface_patterns),
        ("api_structure", api_structure_patterns),
    ):
        for relative in _current_truth_documents(registry, claim):
            path = root / relative
            if not path.is_file():
                continue
            seen: set[tuple[int, int, int, str]] = set()
            for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                for kind, pattern, expected in patterns:
                    for match in pattern.finditer(line):
                        key = (line_number, match.start(), match.end(), kind)
                        if key in seen:
                            continue
                        seen.add(key)
                        actual = int(match.group("value"))
                        if actual not in expected:
                            findings.append(
                                _numeric_claim_finding(
                                    relative,
                                    line_number,
                                    kind,
                                    match.group(0),
                                    actual,
                                    expected,
                                )
                            )
    return findings


def _markdown_cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


def render_truth_status_markdown(report: dict[str, Any]) -> str:
    """Rend la vue humaine déterministe du registre et des métriques calculées."""
    registry = report.get("project_truth", {})
    tables = report["tables"]
    api_surface = report["api_surface"]
    lines = [
        "# 28 — État de vérité du projet",
        "",
        "<!-- GENERATED FILE — DO NOT EDIT. -->",
        "<!-- Source: Architecture/project_truth_registry.json + code runtime. -->",
        "<!-- Regenerate: python tools/audit_architecture_truth.py --schema-output database/schema.sql --status-output Architecture/28_VALIDATION_COHERENCE.md -->",
        "",
        f"**Revue du registre** : {registry.get('reviewed_at', 'inconnue')}",
        "",
        _canonical_count_line(tables),
        "",
        _canonical_api_line(api_surface),
        "",
        _canonical_api_structure_line(api_surface),
        "",
        "Les statuts décrivent ce qui est démontré sur `main`. "
        "`IMPLEMENTED_VERIFIED` signifie qu'une preuve de code et une preuve "
        "automatisée existent ; ce statut ne remplace pas une validation matérielle "
        "lorsqu'elle est explicitement demandée.",
        "",
        "## Matrice de vérité",
        "",
        "| Domaine | Portée | Statut | État et preuves |",
        "|---|---|---|---|",
    ]
    for entry in registry.get("entries", []):
        evidence = ", ".join(
            f"`{item['path']}` ({item.get('kind', 'preuve')})"
            for item in entry.get("evidence", [])
            if isinstance(item, dict) and item.get("path")
        )
        details = entry.get("summary", "")
        if evidence:
            details += f" Preuves : {evidence}."
        gaps = entry.get("gaps", [])
        if gaps:
            details += " Écarts : " + " ".join(str(gap) for gap in gaps)
        gates = entry.get("validation_gates", [])
        if gates:
            details += " Validation restante : " + " ".join(
                str(gate) for gate in gates
            )
        lines.append(
            "| "
            + " | ".join(
                _markdown_cell(str(value))
                for value in (
                    entry.get("domain", ""),
                    entry.get("scope", ""),
                    entry.get("status", ""),
                    details,
                )
            )
            + " |"
        )

    lines.extend(
        [
            "",
            "## Périmètre documentaire",
            "",
            "Chaque Markdown des racines gouvernées doit être classé. Dans les "
            "documents `current`, seuls les types déclarés dans `required_claims` "
            "portent des assertions numériques opposables. Les documents "
            "`historical` conservent une photographie datée ; les documents "
            "`superseded` renvoient vers leur remplacement.",
            "",
            "### Current",
            "",
        ]
    )
    documentation = registry.get("documentation", {})
    for document in documentation.get("current", []):
        claims = ", ".join(document.get("required_claims", [])) or "aucun comptage"
        lines.append(f"- `{document['path']}` — {claims}")
    for path in documentation.get("current_paths", []):
        lines.append(f"- `{path}` — aucun comptage")
    lines.extend(["", "### Historical", ""])
    for document in documentation.get("historical", []):
        lines.append(f"- `{document['path']}` — snapshot {document['snapshot_at']}")
    lines.extend(["", "### Superseded", ""])
    for document in documentation.get("superseded", []):
        lines.append(
            f"- `{document['path']}` → `{document['superseded_by']}`"
        )
    lines.extend(
        [
            "",
            "## Contrôle",
            "",
            "Le job CI recalcule les métriques depuis le code, valide le registre, "
            "refuse tout Markdown gouverné non classé, recherche les assertions "
            "typées contradictoires et les identifiants locaux dans les surfaces "
            "publiques, puis compare ce rendu, `database/schema.sql` et "
            "`artifacts/architecture_truth.json`.",
            "",
        ]
    )
    return "\n".join(lines)


def build_report(root: Path) -> dict[str, Any]:
    frontends = discover_frontends(root)
    resolution = analyze_frontend_resolution(root)
    tables = analyze_tables(root)
    api_surface = analyze_api_surface(root)
    events = analyze_events(root)
    plugins = analyze_plugins(root)
    registry, registry_findings = load_truth_registry(root)
    frontend_inventory = [asdict(frontend) for frontend in frontends]
    for frontend in frontend_inventory:
        frontend.pop("output_present", None)
    stable_resolution = copy.deepcopy(resolution)
    stable_resolution.pop("build_presence", None)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        # Le rapport est versionné : garder une racine stable, indépendante du
        # checkout local, du worktree ou du runner CI.
        "repo_root": ".",
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
                "web/src est uniquement sa bibliothèque de vues et n'est plus "
                "une application exécutable. web_mobile/ est l'interface mobile "
                "autonome servie sous /mobile/, sans build. Si frontend/out manque, "
                "le bureau répond explicitement 503. "
                "tv/ (5174) est réservé à la TV. "
                "FastAPI (8081) et le supervisor (9000) servent uniquement frontend/out "
                "via core.frontend_resolution."
            ),
        },
        "frontends": frontend_inventory,
        "resolution": stable_resolution,
        "tables": tables,
        "api_surface": api_surface,
        "events": events,
        "plugins": plugins,
        "project_truth": registry,
        "documentation_findings": registry_findings
        + scan_public_privacy(root)
        + scan_numeric_claims(root, tables, api_surface, registry)
        + scan_canonical_count_docs(root, tables, registry)
        + scan_canonical_api_doc(root, api_surface, registry)
        + scan_api_structure_docs(root, api_surface, registry)
        + scan_canonical_frontend_docs(root, registry)
        + api_surface["ownership_policy"]["findings"]
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
        "--schema-output",
        type=Path,
        help="Écrire ou vérifier le miroir DDL du schéma runtime frais",
    )
    parser.add_argument(
        "--status-output",
        type=Path,
        help="Écrire ou vérifier la vue Markdown générée du registre de vérité",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Ne rien écrire et échouer si les artefacts ou documents divergent",
    )
    parser.add_argument(
        "--stdout",
        action="store_true",
        help="Écrire aussi le JSON sur stdout",
    )
    args = parser.parse_args(argv)
    root = args.root.resolve()
    schema_output = args.schema_output
    if schema_output is not None and not schema_output.is_absolute():
        schema_output = root / schema_output
    status_output = args.status_output
    if status_output is not None and not status_output.is_absolute():
        status_output = root / status_output

    expected_schema: str | None = None
    if schema_output is not None:
        expected_schema = render_runtime_schema(root)
        if not args.check:
            schema_output.parent.mkdir(parents=True, exist_ok=True)
            schema_output.write_text(expected_schema, encoding="utf-8")

    report = build_report(root)
    expected_status: str | None = None
    if status_output is not None:
        expected_status = render_truth_status_markdown(report)
        if not args.check:
            status_output.parent.mkdir(parents=True, exist_ok=True)
            status_output.write_text(expected_status, encoding="utf-8")
            # Le rapport versionné doit décrire le corpus après régénération,
            # pas les contradictions de l'ancien rendu remplacé à l'instant.
            report = build_report(root)
            expected_status = render_truth_status_markdown(report)
    payload = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    if args.stdout:
        sys.stdout.write(payload)
    errors = [f for f in report["documentation_findings"] if f["severity"] == "error"]

    if args.check:
        failures: list[str] = []
        if not args.output.is_file():
            failures.append(f"artefact absent : {args.output}")
        else:
            try:
                current_report = json.loads(args.output.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as error:
                failures.append(f"artefact JSON illisible : {error}")
            else:
                if _stable_report(current_report) != _stable_report(report):
                    failures.append(
                        "artifacts/architecture_truth.json diverge du code runtime"
                    )
        if schema_output is not None:
            current_schema = (
                schema_output.read_text(encoding="utf-8")
                if schema_output.is_file()
                else None
            )
            if current_schema != expected_schema:
                failures.append(f"{schema_output} diverge du schéma runtime frais")
        if status_output is not None:
            current_status = (
                status_output.read_text(encoding="utf-8")
                if status_output.is_file()
                else None
            )
            if current_status != expected_status:
                failures.append(f"{status_output} diverge du registre de vérité")
        failures.extend(f"{finding['file']}: {finding['note']}" for finding in errors)
        if failures:
            for failure in failures:
                print(f"[audit_architecture_truth] ERROR {failure}", file=sys.stderr)
            return 1
        print(
            "[audit_architecture_truth] artefacts et documentation synchronisés",
            file=sys.stderr,
        )
        return 0

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(payload, encoding="utf-8")
    print(
        f"[audit_architecture_truth] wrote {args.output} "
        f"(persistantes={report['tables']['counts']['persistantes_post_init']}, "
        f"physiques_max={report['tables']['counts']['physiques_max_default_fts_on']}, "
        f"api_operations={report['api_surface']['counts']['operations']}, "
        f"doc_errors={len(errors)})",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
