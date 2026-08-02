"""Contrats de suppression d'Open Interpreter.

La bibliothèque a été retirée : plus aucun chemin runtime ne doit l'importer,
plus aucune capacité ne doit l'annoncer, et le backend doit démarrer dans un
environnement où le module `interpreter` est introuvable.

Le garde-fou d'import est volontairement un `MetaPathFinder` plutôt qu'un
`monkeypatch` de `builtins.__import__` : il intercepte aussi les imports
déclenchés depuis du code compilé ou depuis un thread, et il enregistre la
tentative même si l'appelant l'avale dans un `except ImportError`.
"""

from __future__ import annotations

import importlib.util
import logging
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from tests.conftest import authenticate  # noqa: E402

# Le module Python `open_interpreter` n'est pas listé ici : il est couvert par
# `FORBIDDEN_IMPORT_RE`, et l'interdire comme simple sous-chaîne ferait tomber
# toute référence au nom de ce fichier de contrats.
RETIRED_SYMBOLS = (
    "open-interpreter",
    "CODE_EXECUTOR_ENABLED",
    "CODE_EXECUTOR_TIMEOUT",
    "CODE_EXECUTOR_MODEL",
    "code_executor",
    "CodeExecutor",
)

# Les rapports d'audit et le changelog sont des archives datées : les réécrire
# falsifierait la trace de ce qui était vrai au moment de l'audit. Ils sont
# exclus du scan, contrairement à la documentation vivante.
ARCHIVED_PREFIXES = (
    "Architecture/audit/",
    "Architecture/AUDIT_SECURITE_2026-08.md",
    "CHANGELOG_HISTORIQUE.md",
)

# `config.py` nomme les variables retirées pour avertir l'utilisateur dont le
# `.env` les définit encore : c'est la seule mention autorisée, et un test
# dédié vérifie qu'elle ne relit jamais leur valeur.
ALLOWED_MENTIONS: dict[str, frozenset[str]] = {
    "config.py": frozenset(
        {"CODE_EXECUTOR_ENABLED", "CODE_EXECUTOR_TIMEOUT", "CODE_EXECUTOR_MODEL"}
    ),
}

FORBIDDEN_IMPORT_RE = re.compile(
    r"^\s*(?:import\s+(?:interpreter|open_interpreter)\b"
    r"|from\s+(?:interpreter|open_interpreter)(?:\.\S+)?\s+import\b)",
    re.MULTILINE,
)

SCANNED_SUFFIXES = (
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".json",
    ".md",
    ".txt",
    ".yml",
    ".yaml",
    ".example",
    ".sh",
    ".toml",
    ".cfg",
)

BOOT_SCRIPT = """
import sys

FORBIDDEN = {"interpreter", "open_interpreter"}
attempts = []


class ForbiddenModuleFinder:
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".", 1)[0] in FORBIDDEN:
            attempts.append(fullname)
            raise ModuleNotFoundError(
                f"{fullname} est retire de JARVIS", name=fullname
            )
        return None


sys.meta_path.insert(0, ForbiddenModuleFinder())

import main

paths = set(main.app.openapi().get("paths", {}))
paths.update(
    route.path
    for route in main.app.routes
    if "WebSocket" in type(route).__name__ and getattr(route, "path", None)
)
missing = {"/api/status", "/api/integrations", "/ws"} - paths
if missing:
    raise SystemExit(f"routes absentes du backend demarre : {sorted(missing)}")
if attempts:
    raise SystemExit(f"import interdit tente au demarrage : {attempts}")
print("BOOT_OK")
"""


@pytest.fixture
def tmp_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    db_path = tmp_path / "test_jarvis.db"
    monkeypatch.setattr("config.DB_PATH", str(db_path))
    monkeypatch.setattr("database.DB_PATH", db_path)
    from database import init_db

    init_db()
    return db_path


def _client():
    import main
    from fastapi.testclient import TestClient

    return TestClient(main.app)


def _tracked_files() -> list[Path]:
    listing = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    return [PROJECT_ROOT / name for name in listing.stdout.split("\0") if name]


# ── Le backend démarre sans la bibliothèque ─────────────────────────────


def test_backend_boots_when_open_interpreter_is_unimportable(tmp_path: Path) -> None:
    """Le module `interpreter` rendu introuvable ne doit pas empêcher le boot.

    Le sous-processus reçoit une base et un dossier d'upload jetables : importer
    `main` ouvre une connexion SQLite, ce qui créerait sinon une base vide dans
    le dépôt et ferait échouer les tests d'intégration voisins.
    """
    env = {
        **os.environ,
        "DB_PATH": str(tmp_path / "boot.db"),
        "UPLOAD_DIR": str(tmp_path / "uploads"),
    }
    completed = subprocess.run(
        [sys.executable, "-c", BOOT_SCRIPT],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=600,
        env=env,
    )

    assert completed.returncode == 0, (
        f"Le backend n'a pas démarré sans open-interpreter.\n"
        f"stdout: {completed.stdout}\nstderr: {completed.stderr}"
    )
    assert "BOOT_OK" in completed.stdout


def test_no_runtime_module_imports_open_interpreter() -> None:
    """Aucun module JARVIS ne référence encore le paquet dans ses imports."""
    offenders: list[str] = []
    self_relative = Path(__file__).resolve().relative_to(PROJECT_ROOT).as_posix()

    for path in _tracked_files():
        if path.suffix != ".py" or not path.is_file():
            continue
        relative = path.relative_to(PROJECT_ROOT).as_posix()
        if relative.startswith(ARCHIVED_PREFIXES) or relative == self_relative:
            continue
        source = path.read_text(encoding="utf-8", errors="ignore")
        offenders.extend(
            f"{relative}: {match.group(0).strip()}"
            for match in FORBIDDEN_IMPORT_RE.finditer(source)
        )

    assert offenders == [], f"Imports résiduels : {offenders}"


def test_code_executor_module_no_longer_exists() -> None:
    assert not (PROJECT_ROOT / "integrations" / "code_executor.py").exists()
    assert importlib.util.find_spec("integrations.code_executor") is None


# ── Plus aucune capacité annoncée ───────────────────────────────────────


def test_status_endpoint_no_longer_announces_code_executor(tmp_db: Path) -> None:
    with _client() as client:
        authenticate(client)
        response = client.get("/api/status")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert "code_executor" not in payload
    assert "computer" in payload, "les capacités restantes doivent rester exposées"


def test_integrations_endpoint_no_longer_announces_code_executor(tmp_db: Path) -> None:
    with _client() as client:
        authenticate(client)
        response = client.get("/api/integrations")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert "code_executor" not in payload
    assert "computer" in payload


def test_capability_registry_drops_the_code_executor_entry() -> None:
    from jarvis.cognitive.capability_registry import get_capability_registry

    registry = get_capability_registry()
    registry.refresh()

    assert registry.get("code_executor") is None
    assert "code_executor" not in registry.available_names()
    descriptions = " ".join(cap["description"] for cap in registry.list_all())
    assert "Open Interpreter" not in descriptions


def test_terminal_capability_survives_the_removal() -> None:
    """La suppression ne doit pas emporter l'action terminal confinée."""
    from jarvis.cognitive.capability_registry import get_capability_registry

    registry = get_capability_registry()
    terminal = registry.get("computer.terminal")

    assert terminal is not None
    assert terminal.action_type == "terminal"
    assert terminal.requires_confirmation is True


# ── Configuration ───────────────────────────────────────────────────────


def test_config_no_longer_exposes_code_executor_settings() -> None:
    import config

    for name in ("CODE_EXECUTOR_ENABLED", "CODE_EXECUTOR_TIMEOUT", "CODE_EXECUTOR_MODEL"):
        assert not hasattr(config, name), f"{name} devrait avoir disparu de config"


def test_retired_env_vars_are_reported_not_silently_ignored(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    import config

    monkeypatch.setenv("CODE_EXECUTOR_ENABLED", "true")
    with caplog.at_level(logging.WARNING, logger="config"):
        found = config.warn_retired_env_vars()

    assert found == ["CODE_EXECUTOR_ENABLED"]
    assert any("CODE_EXECUTOR_ENABLED" in record.message for record in caplog.records)


def test_no_retired_env_var_warning_when_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    import config

    for name in config.RETIRED_ENV_VARS:
        monkeypatch.delenv(name, raising=False)

    assert config.warn_retired_env_vars() == []


# ── Aucune référence résiduelle ─────────────────────────────────────────


def test_no_residual_reference_outside_archived_reports() -> None:
    offenders: list[str] = []
    self_relative = Path(__file__).resolve().relative_to(PROJECT_ROOT).as_posix()

    for path in _tracked_files():
        if not path.is_file() or path.suffix not in SCANNED_SUFFIXES:
            continue
        relative = path.relative_to(PROJECT_ROOT).as_posix()
        if relative.startswith(ARCHIVED_PREFIXES) or relative == self_relative:
            continue
        allowed = ALLOWED_MENTIONS.get(relative, frozenset())
        content = path.read_text(encoding="utf-8", errors="ignore")
        offenders.extend(
            f"{relative}: {symbol}"
            for symbol in RETIRED_SYMBOLS
            if symbol not in allowed and symbol in content
        )

    assert offenders == [], f"Références obsolètes restantes : {offenders}"


def test_config_never_reads_the_retired_variables() -> None:
    """Les nommer pour avertir, oui ; relire leur valeur, non."""
    source = (PROJECT_ROOT / "config.py").read_text(encoding="utf-8")

    for name in ("CODE_EXECUTOR_ENABLED", "CODE_EXECUTOR_TIMEOUT", "CODE_EXECUTOR_MODEL"):
        assert f'_get("{name}"' not in source
        assert f"{name} =" not in source


def test_requirements_no_longer_ship_open_interpreter() -> None:
    requirements = (PROJECT_ROOT / "requirements.txt").read_text(encoding="utf-8")

    assert "open-interpreter" not in requirements
    # La borne `setuptools<82` n'existait que pour le `pkg_resources` d'Open
    # Interpreter ; torch porte lui-même le plancher `setuptools>=77.0.3`.
    assert "setuptools" not in requirements


def test_ci_no_longer_smoke_imports_open_interpreter() -> None:
    workflow = (PROJECT_ROOT / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )

    assert '"interpreter"' not in workflow
    assert "open-interpreter" not in workflow
