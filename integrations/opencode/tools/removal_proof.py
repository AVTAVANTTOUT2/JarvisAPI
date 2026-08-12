"""Preuve reproductible que le runtime peut être retiré sans casser JARVIS.

Le worktree source est uniquement lu. La seule suppression récursive cible une
copie créée sous :class:`tempfile.TemporaryDirectory` et validée avant usage.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
PLUGIN_RELATIVE_PATH = Path("integrations") / "opencode"
_COPY_EXCLUDED_NAMES = frozenset(
    {
        ".git",
        ".env",
        ".jarvis",
        ".mypy_cache",
        ".next",
        ".pytest_cache",
        ".runtime",
        ".serena",
        ".ssh",
        ".venv",
        ".worktrees",
        "__pycache__",
        "artifacts",
        "build",
        "coverage",
        "credentials",
        "dist",
        "node_modules",
        "secrets",
    }
)
_PRODUCTION_EXTENSIONS = frozenset(
    {
        ".gradle",
        ".js",
        ".json",
        ".jsx",
        ".kt",
        ".kts",
        ".md",
        ".py",
        ".pyi",
        ".sh",
        ".swift",
        ".toml",
        ".ts",
        ".tsx",
        ".yaml",
        ".yml",
    }
)
_TEST_NAME_MARKERS = (".spec.", ".test.")

# Les huit premières étapes et le contrôle de résidus sont hermétiques et
# exécutés par défaut. Les gates multiplateformes sont disponibles via
# ``--full`` afin que la preuve de livraison puisse réellement s'exécuter dans
# la copie où le plugin a été supprimé, sans ralentir chaque test unitaire.
REMOVAL_PROOF_STEPS = (
    "copy_repository",
    "remove_plugin_directory",
    "scan_provider_references",
    "compile_python_in_memory",
    "initialize_fresh_database",
    "import_core_voice_imessage_api",
    "generate_openapi",
    "prove_provider_unavailable",
    "run_core_voice_tests",
    "run_python_lint",
    "validate_web_client",
    "validate_unified_frontend",
    "validate_android_clients",
    "validate_macos_clients",
    "validate_generated_contracts",
    "verify_no_runtime_residue",
)

_CORE_VOICE_TESTS = (
    "tests/test_agentic_domain.py",
    "tests/test_agentic_persistence.py",
    "tests/test_agentic_registry.py",
    "tests/test_agentic_api.py",
    "tests/test_agentic_processing.py",
    "tests/test_agentic_notifications.py",
    "tests/test_ws_agentic.py",
    "tests/test_imessage_agentic_followup.py",
    "tests/test_voice_pipeline_e2e.py",
    "tests/test_voice_turn_integrity.py",
)


class RemovalProofError(RuntimeError):
    """La preuve de suppression n'a pas satisfait son contrat."""


def _copy_ignore(directory: str, names: list[str]) -> set[str]:
    """Exclut secrets, artefacts lourds et liens de la copie temporaire."""

    base = Path(directory)
    ignored = set(_COPY_EXCLUDED_NAMES & set(names))
    # Seul le répertoire de données runtime à la racine est exclu. Les paquets
    # applicatifs légitimes nommés ``data`` (notamment Android) doivent rester
    # dans la copie afin que la preuve reconstruise réellement les clients.
    try:
        if base.resolve(strict=True) == REPOSITORY_ROOT.resolve(strict=True):
            ignored.update({"data"} & set(names))
    except OSError:
        pass
    for name in names:
        candidate = base / name
        if (
            name.startswith(".env")
            or name in {".netrc", ".npmrc", ".pypirc"}
            or candidate.suffix.casefold() in {".key", ".p12", ".pfx"}
        ):
            ignored.add(name)
            continue
        try:
            if candidate.is_symlink():
                ignored.add(name)
        except OSError:
            ignored.add(name)
    return ignored


def _is_test_path(relative: Path) -> bool:
    lowered_parts = {part.casefold() for part in relative.parts}
    name = relative.name.casefold()
    return (
        bool(lowered_parts & {"test", "tests", "__tests__"})
        or name.startswith("test_")
        or name.endswith("_test.py")
        or any(marker in name for marker in _TEST_NAME_MARKERS)
    )


def _production_provider_references(root: Path, provider_name: str) -> list[str]:
    """Retourne les références fournisseur dans le code/contrat de production."""

    needle = provider_name.casefold()
    plugin_root = (root / PLUGIN_RELATIVE_PATH).resolve(strict=False)
    violations: list[str] = []
    for path in root.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        try:
            relative = path.relative_to(root)
            path.resolve().relative_to(plugin_root)
            continue
        except ValueError:
            pass
        if any(part in _COPY_EXCLUDED_NAMES for part in relative.parts) or (
            relative.parts and relative.parts[0] == "data"
        ):
            continue
        if _is_test_path(relative):
            continue
        if path.suffix.casefold() not in _PRODUCTION_EXTENSIONS and path.name not in {
            "Dockerfile",
            "Makefile",
        }:
            continue
        if needle in relative.as_posix().casefold():
            violations.append(relative.as_posix())
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        if needle in content.casefold():
            violations.append(relative.as_posix())
    return sorted(set(violations))


def _remove_temporary_plugin(repo_copy: Path, temporary_root: Path) -> None:
    """Supprime seulement le plugin copié, après validation stricte des chemins."""

    resolved_temp = temporary_root.resolve(strict=True)
    resolved_repo = repo_copy.resolve(strict=True)
    plugin_copy = (resolved_repo / PLUGIN_RELATIVE_PATH).resolve(strict=True)
    if resolved_repo.parent != resolved_temp:
        raise RemovalProofError(
            "la copie du dépôt n'est pas un enfant direct du temporaire"
        )
    if plugin_copy.parent != resolved_repo / "integrations":
        raise RemovalProofError("cible de suppression inattendue")
    if plugin_copy == (REPOSITORY_ROOT / PLUGIN_RELATIVE_PATH).resolve(strict=True):
        raise RemovalProofError("refus de supprimer le plugin du worktree source")
    shutil.rmtree(plugin_copy)
    if plugin_copy.exists() or plugin_copy.is_symlink():
        raise RemovalProofError("le plugin temporaire existe encore après suppression")


_CHILD_PROOF = r"""
import asyncio
import json
import os
from pathlib import Path
import sqlite3
import sys

repo = Path(os.environ["REMOVAL_PROOF_REPO"]).resolve(strict=True)
provider = "open" + "code"
spawn_events = []
network_events = []
sys.path.insert(0, str(repo))

def audit(event, args):
    if event in {"subprocess.Popen", "os.system", "os.posix_spawn", "os.posix_spawnp"}:
        spawn_events.append(event)
    if event == "socket.connect":
        network_events.append(event)

sys.addaudithook(audit)

# Contrat de build Python sans produire de bytecode dans la copie.
compiled = 0
for area in ("agents", "api", "audio", "core", "database", "integrations", "jarvis", "jarvis_auth"):
    base = repo / area
    if not base.exists():
        continue
    for source in base.rglob("*.py"):
        if any(part in {"tests", "__pycache__"} for part in source.relative_to(repo).parts):
            continue
        compile(source.read_text(encoding="utf-8"), str(source), "exec", dont_inherit=True)
        compiled += 1

import database
database.init_db()
db_path = Path(os.environ["DB_PATH"])
with sqlite3.connect(db_path) as connection:
    tables = {
        row[0]
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
required_tables = {
    "agent_runs",
    "agent_events",
    "agent_steps",
    "agent_approvals",
    "agent_artifacts",
    "agent_capability_grants",
    "agent_checkpoints",
    "agent_metrics",
}
assert required_tables <= tables, sorted(required_tables - tables)

from jarvis.agentic.registry import RuntimeRegistry, discover_runtime_plugins
from jarvis.agentic.service import AgenticService

manifests = discover_runtime_plugins()
assert provider not in {manifest.runtime_id for manifest in manifests}
registry = RuntimeRegistry(manifests)
assert registry.manifest(provider) is None

# API, OpenAPI et tous les canaux restent importables sans le fournisseur.
import api.agentic_processing as agentic_processing
import api.chat_processing
import api.router_agentic
import api.router_mobile_chat
import api.voice_processing
import api.ws_messages
import integrations.imessage
import main

schema = main.app.openapi()
json.dumps(schema)
assert "/api/agentic/runtime/status" in schema["paths"]
assert "/api/agentic/runs" in schema["paths"]

async def verify_absence():
    service = AgenticService(registry=registry)
    assert service.resolve_runtime_id() is None
    assert all(item["runtime_id"] != provider for item in await service.runtime_status())
    run = await service.create_run(title="preuve de suppression", channel="removal-proof")
    terminal = await service.start_run(run.run_id)
    assert terminal.runtime_id == "unavailable"
    assert terminal.status.value == "provider_unavailable"
    direct = await agentic_processing.maybe_start_agentic_run(
        "bonjour",
        1,
        channel="removal-proof",
        voice_mode=False,
        persist_assistant=False,
    )
    assert direct is None
    await service.dispose()

asyncio.run(verify_absence())

assert not any(name == "integrations." + provider or name.startswith("integrations." + provider + ".") for name in sys.modules)
assert not spawn_events, spawn_events
assert not network_events, network_events
residuals = []
for candidate in repo.rglob("*"):
    relative = candidate.relative_to(repo).as_posix().casefold()
    if provider in relative or relative.endswith("/.runtime/state/process.json") or relative.endswith("/.runtime/state/server-auth.json"):
        residuals.append(relative)
assert not residuals, residuals

print("REMOVAL_PROOF=" + json.dumps({
    "api_paths": 2,
    "compiled_python_files": compiled,
    "database_tables": len(tables),
    "provider_discovered": False,
    "provider_status": "provider_unavailable",
    "network_events": network_events,
    "spawn_events": spawn_events,
}))
"""


def _run_checked_gate(
    *,
    step: str,
    commands: tuple[tuple[str, ...], ...],
    cwd: Path,
    environment: dict[str, str],
    timeout: int,
) -> dict[str, Any]:
    """Exécute une gate bornée sans shell et ne conserve qu'un résumé sûr."""

    started = time.monotonic()
    rendered: list[list[str]] = []
    for command in commands:
        if (
            not command
            or shutil.which(command[0], path=environment.get("PATH")) is None
        ):
            raise RemovalProofError(f"{step}: exécutable indisponible: {command[0]}")
        rendered.append(list(command))
        completed = subprocess.run(
            command,
            cwd=cwd,
            env=environment,
            capture_output=True,
            check=False,
            text=True,
            timeout=timeout,
        )
        if completed.returncode != 0:
            diagnostic = (completed.stderr or completed.stdout)[-4_000:]
            raise RemovalProofError(
                f"{step}: commande échouée (code {completed.returncode}): {diagnostic}"
            )
    return {
        "status": "passed",
        "commands": rendered,
        "duration_ms": round((time.monotonic() - started) * 1_000),
    }


def _full_gate_environment(
    base: dict[str, str], temporary_root: Path, repo_copy: Path
) -> dict[str, str]:
    """Construit un environnement de validation sans jetons fournisseur/GitHub."""

    allowed = {
        "ANDROID_HOME",
        "ANDROID_SDK_ROOT",
        "DEVELOPER_DIR",
        "GRADLE_USER_HOME",
        "HOME",
        "JAVA_HOME",
        "LANG",
        "LC_ALL",
        "LOGNAME",
        "PATH",
        "PNPM_HOME",
        "TMPDIR",
        "USER",
        "XCODE_VERSION_ACTUAL",
    }
    environment = {key: value for key, value in base.items() if key in allowed}
    environment.update(
        {
            "AGENTIC_RUNTIME": "auto",
            "AGENTIC_RUNTIME_FALLBACK": "disabled",
            "CI": "true",
            "DB_PATH": str(temporary_root / "full-proof.db"),
            "GIT_CONFIG_NOSYSTEM": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONPATH": str(repo_copy),
            "UPLOAD_DIR": str(temporary_root / "full-proof-uploads"),
        }
    )
    return environment


def _run_full_gates(
    repo_copy: Path, temporary_root: Path, environment: dict[str, str]
) -> dict[str, dict[str, Any]]:
    """Valide réellement les consommateurs après retrait, dans la copie."""

    results: dict[str, dict[str, Any]] = {}
    results["run_core_voice_tests"] = _run_checked_gate(
        step="run_core_voice_tests",
        commands=((sys.executable, "-m", "pytest", "-q", *_CORE_VOICE_TESTS),),
        cwd=repo_copy,
        environment=environment,
        timeout=900,
    )
    results["run_python_lint"] = _run_checked_gate(
        step="run_python_lint",
        commands=((sys.executable, "-m", "ruff", "check", "."),),
        cwd=repo_copy,
        environment=environment,
        timeout=300,
    )
    for step, directory, commands in (
        (
            "validate_web_client",
            "web",
            (
                ("pnpm", "install", "--offline", "--frozen-lockfile"),
                ("pnpm", "run", "test"),
                ("pnpm", "run", "typecheck"),
            ),
        ),
        (
            "validate_unified_frontend",
            "frontend",
            (
                ("pnpm", "install", "--offline", "--frozen-lockfile"),
                ("pnpm", "run", "test"),
                ("pnpm", "run", "typecheck"),
                ("pnpm", "run", "build"),
            ),
        ),
    ):
        results[step] = _run_checked_gate(
            step=step,
            commands=commands,
            cwd=repo_copy / directory,
            environment=environment,
            timeout=1_200,
        )
    results["validate_android_clients"] = _run_checked_gate(
        step="validate_android_clients",
        commands=(
            (
                str(repo_copy / "android" / "gradlew"),
                "--offline",
                "--no-daemon",
                "testDebugUnitTest",
                "testReleaseUnitTest",
                "lintDebug",
                "lintRelease",
                "assembleDebug",
                "assembleRelease",
            ),
        ),
        cwd=repo_copy / "android",
        environment=environment,
        timeout=1_800,
    )
    results["validate_macos_clients"] = _run_checked_gate(
        step="validate_macos_clients",
        commands=(
            (
                sys.executable,
                "-m",
                "pytest",
                "-q",
                str(repo_copy / "tests/test_macos_runtime.py"),
                str(repo_copy / "tests/test_apple_data.py"),
                str(repo_copy / "tests/test_calendar_no_foreground.py"),
                str(repo_copy / "tests/test_imessage_consumer_cursor.py"),
            ),
            ("xcodegen", "generate"),
            (
                "xcodebuild",
                "-project",
                "JarvisMac.xcodeproj",
                "-scheme",
                "JarvisMac",
                "-configuration",
                "Release",
                "-destination",
                "generic/platform=macOS",
                "-derivedDataPath",
                str(temporary_root / "DerivedData"),
                "CODE_SIGNING_ALLOWED=NO",
                "CODE_SIGNING_REQUIRED=NO",
                "build",
            ),
        ),
        cwd=repo_copy / "native_mac",
        environment=environment,
        timeout=1_800,
    )
    results["validate_generated_contracts"] = _run_checked_gate(
        step="validate_generated_contracts",
        commands=(
            (
                sys.executable,
                "tools/audit_architecture_truth.py",
                "--check",
                "--output",
                "artifacts/architecture_truth.json",
                "--schema-output",
                "database/schema.sql",
            ),
            (sys.executable, "tools/export_openapi.py", "--check"),
            (sys.executable, "tools/generate_python_sdk.py", "--check"),
            (sys.executable, "tools/audit_technical_debt.py", "--check"),
        ),
        cwd=repo_copy,
        environment=environment,
        timeout=600,
    )
    return results


def run_removal_proof(
    source_root: Path = REPOSITORY_ROOT, *, full: bool = False
) -> dict[str, Any]:
    """Exécute la preuve dans une copie temporaire et retourne son résumé."""

    source = source_root.expanduser().resolve(strict=True)
    plugin_source = (source / PLUGIN_RELATIVE_PATH).resolve(strict=True)
    if not plugin_source.is_dir() or plugin_source.parent != source / "integrations":
        raise RemovalProofError("racine du plugin source invalide")
    references = _production_provider_references(source, plugin_source.name)
    if references:
        raise RemovalProofError(
            "références fournisseur hors plugin: " + ", ".join(references)
        )

    with tempfile.TemporaryDirectory(prefix="jarvis-runtime-removal-") as temp_name:
        temporary_root = Path(temp_name).resolve(strict=True)
        repo_copy = temporary_root / "repository"
        shutil.copytree(source, repo_copy, ignore=_copy_ignore)
        if full:
            # ``artifacts/`` reste globalement exclu. Seules les preuves
            # canoniques exigées par les audits sont recopiées explicitement.
            required_artifacts = (
                "architecture_truth.json",
                "voice_latency_2026-08-05.json",
            )
            copied_artifacts = repo_copy / "artifacts"
            copied_artifacts.mkdir(mode=0o700)
            for artifact_name in required_artifacts:
                artifact = source / "artifacts" / artifact_name
                if not artifact.is_file() or artifact.is_symlink():
                    raise RemovalProofError(
                        f"artefact canonique indisponible: {artifact_name}"
                    )
                shutil.copyfile(artifact, copied_artifacts / artifact_name)
        if any(path.is_symlink() for path in repo_copy.rglob("*")):
            raise RemovalProofError("la copie temporaire contient un lien symbolique")

        # Ces marqueurs prouvent que les fichiers runtime sont emportés avec le dossier.
        runtime_state = repo_copy / PLUGIN_RELATIVE_PATH / ".runtime" / "state"
        runtime_bin = repo_copy / PLUGIN_RELATIVE_PATH / ".runtime" / "bin"
        runtime_state.mkdir(parents=True, mode=0o700)
        runtime_bin.mkdir(parents=True, mode=0o700)
        (runtime_state / "process.json").write_text("{}\n", encoding="utf-8")
        (runtime_state / "server-auth.json").write_text("{}\n", encoding="utf-8")
        (runtime_bin / plugin_source.name).write_bytes(b"temporary-removal-marker")

        _remove_temporary_plugin(repo_copy, temporary_root)
        references_after_removal = _production_provider_references(
            repo_copy, plugin_source.name
        )
        if references_after_removal:
            raise RemovalProofError(
                "références résiduelles: " + ", ".join(references_after_removal)
            )

        environment = {
            key: value
            for key, value in os.environ.items()
            if plugin_source.name.casefold() not in key.casefold()
        }
        environment.update(
            {
                "AGENTIC_RUNTIME": "auto",
                "AGENTIC_RUNTIME_FALLBACK": "disabled",
                "DB_PATH": str(temporary_root / "proof.db"),
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONPATH": str(repo_copy),
                "REMOVAL_PROOF_REPO": str(repo_copy),
                "UPLOAD_DIR": str(temporary_root / "uploads"),
            }
        )
        completed = subprocess.run(
            [sys.executable, "-I", "-c", _CHILD_PROOF],
            cwd=repo_copy,
            env=environment,
            capture_output=True,
            check=False,
            text=True,
            timeout=120,
        )
        marker = "REMOVAL_PROOF="
        line = next(
            (
                item
                for item in reversed(completed.stdout.splitlines())
                if item.startswith(marker)
            ),
            None,
        )
        if completed.returncode != 0 or line is None:
            diagnostic = (completed.stderr or completed.stdout)[-4_000:]
            raise RemovalProofError(
                f"preuve enfant échouée (code {completed.returncode}): {diagnostic}"
            )
        result = json.loads(line[len(marker) :])
        steps: dict[str, dict[str, Any]] = {
            name: {"status": "passed"} for name in REMOVAL_PROOF_STEPS[:8]
        }
        if full:
            full_environment = _full_gate_environment(
                environment, temporary_root, repo_copy
            )
            steps.update(_run_full_gates(repo_copy, temporary_root, full_environment))
        else:
            for name in REMOVAL_PROOF_STEPS[8:15]:
                steps[name] = {
                    "status": "delegated_to_delivery_gates",
                    "required": True,
                }
        residuals = [
            candidate.relative_to(repo_copy).as_posix()
            for candidate in repo_copy.rglob("*")
            if plugin_source.name.casefold()
            in candidate.relative_to(repo_copy).as_posix().casefold()
        ]
        if residuals:
            raise RemovalProofError(
                "résidus fournisseur après validations: " + ", ".join(residuals[:20])
            )
        steps["verify_no_runtime_residue"] = {"status": "passed"}
        result.update(
            {
                "full_delivery_gates": full,
                "plugin_copy_removed": True,
                "production_references": [],
                "source_worktree_untouched": plugin_source.is_dir(),
                "steps": [
                    {"id": index, "name": name, **steps[name]}
                    for index, name in enumerate(REMOVAL_PROOF_STEPS, start=1)
                ],
            }
        )
        return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Prouve la suppression sûre du plugin dans une copie temporaire"
    )
    parser.add_argument(
        "--source-root",
        type=Path,
        default=REPOSITORY_ROOT,
        help="racine du dépôt à copier (lecture seule)",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help=(
            "exécute aussi tests core/voix, lint, Web, frontend, Android, "
            "macOS et audits dans la copie sans plugin"
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = run_removal_proof(args.source_root, full=args.full)
    except (OSError, RemovalProofError, subprocess.SubprocessError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps({"ok": True, **result}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
