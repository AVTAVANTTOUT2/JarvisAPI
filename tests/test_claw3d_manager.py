"""Contrats du gestionnaire optionnel Claw3D côté JarvisAPI."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import claw3d


def _fake_installation(jarvis_root: Path) -> Path:
    root = claw3d.claw3d_root(jarvis_root)
    (root / "scripts").mkdir(parents=True)
    (root / ".claw3d-root").write_text(claw3d.CLAW3D_MARKER + "\n", encoding="utf-8")
    (root / "package.json").write_text(json.dumps({"name": "claw3d"}), encoding="utf-8")
    for name in ("install.sh", "start.sh", "stop.sh", "uninstall.sh", "verify-containment.sh"):
        (root / "scripts" / name).write_text("#!/bin/sh\n", encoding="utf-8")
    return root


@pytest.mark.parametrize(
    "value,expected",
    (
        ("http://127.0.0.1:8080", "http://127.0.0.1:8080"),
        ("https://jarvis.example/", "https://jarvis.example"),
    ),
)
def test_normalize_jarvis_origin_accepts_origins(value: str, expected: str):
    assert claw3d.normalize_jarvis_origin(value) == expected


@pytest.mark.parametrize(
    "value",
    (
        "",
        "ftp://jarvis.example",
        "https://user:secret@jarvis.example",
        "https://jarvis.example/api/status",
        "https://jarvis.example?token=secret",
        "https://jarvis.example/#fragment",
        "https://jarvis.example;touch",
        " https://jarvis.example",
    ),
)
def test_normalize_jarvis_origin_rejects_unsafe_values(value: str):
    with pytest.raises(claw3d.Claw3DError):
        claw3d.normalize_jarvis_origin(value)


def test_mock_configuration_is_closed_and_secret_free():
    configuration = claw3d.render_configuration("mock", None, "127.0.0.1", 3000)

    assert "VISUAL_ADAPTER=mock" in configuration
    assert "JARVIS_CONNECTOR_ENABLED=false" in configuration
    assert "JARVIS_ORIGIN=" in configuration
    assert "TOKEN" not in configuration
    assert "PASSWORD" not in configuration


@pytest.mark.parametrize("host", ("127.0.0.1;touch", "$(touch)", "bad host"))
def test_configuration_rejects_shell_metacharacters_in_host(host: str):
    with pytest.raises(claw3d.Claw3DError):
        claw3d.render_configuration("mock", None, host, 3000)


def test_readonly_configuration_requires_an_explicit_origin():
    with pytest.raises(claw3d.Claw3DError):
        claw3d.render_configuration("jarvis-readonly", None, "127.0.0.1", 3000)

    configuration = claw3d.render_configuration(
        "jarvis-readonly", "http://127.0.0.1:8080", "127.0.0.1", 3000
    )
    assert "JARVIS_CONNECTOR_ENABLED=true" in configuration
    assert "JARVIS_ORIGIN=http://127.0.0.1:8080" in configuration


def test_configuration_preserves_existing_env_unless_replace_is_explicit(tmp_path: Path):
    root = tmp_path / "claw3d"
    root.mkdir()
    env_path = root / ".env"
    env_path.write_text("VISUAL_ADAPTER=null\n", encoding="utf-8")

    assert not claw3d.write_configuration(root, "VISUAL_ADAPTER=mock\n", replace=False)
    assert env_path.read_text(encoding="utf-8") == "VISUAL_ADAPTER=null\n"

    assert claw3d.write_configuration(root, "VISUAL_ADAPTER=mock\n", replace=True)
    assert env_path.read_text(encoding="utf-8") == "VISUAL_ADAPTER=mock\n"
    assert env_path.stat().st_mode & 0o777 == 0o600


def test_configuration_rejects_a_directory_as_env(tmp_path: Path):
    root = tmp_path / "claw3d"
    (root / ".env").mkdir(parents=True)

    with pytest.raises(claw3d.Claw3DError, match="fichier régulier"):
        claw3d.write_configuration(root, "VISUAL_ADAPTER=mock\n", replace=True)


def test_install_root_is_confined_inside_jarvis(tmp_path: Path):
    root = claw3d.claw3d_root(tmp_path)

    assert root == tmp_path.resolve() / ".jarvis" / "apps" / "claw3d"
    root.relative_to(tmp_path.resolve())


def test_install_directory_rejects_a_symlinked_state_parent(tmp_path: Path):
    jarvis_root = tmp_path / "jarvis"
    external = tmp_path / "external"
    jarvis_root.mkdir()
    external.mkdir()
    (jarvis_root / ".jarvis").symlink_to(external, target_is_directory=True)

    with pytest.raises(claw3d.Claw3DError, match="lien symbolique refusé"):
        claw3d._ensure_local_directory(
            claw3d.apps_root(jarvis_root),
            jarvis_root.resolve(),
        )

    assert list(external.iterdir()) == []


def test_clone_rejects_a_broken_target_symlink(tmp_path: Path):
    target = tmp_path / "claw3d"
    target.symlink_to(tmp_path / "missing", target_is_directory=True)

    with pytest.raises(claw3d.Claw3DError, match="déjà présente"):
        claw3d._clone_pinned_claw3d(target)


def test_lifecycle_uses_an_exact_script_without_shell(tmp_path: Path, monkeypatch):
    root = _fake_installation(tmp_path)
    commands: list[tuple[tuple[str, ...], Path | None]] = []

    monkeypatch.setattr(claw3d, "apps_root", lambda jarvis_root=claw3d.JARVIS_ROOT: root.parent)
    monkeypatch.setattr(claw3d, "_capture", lambda command, cwd=None: claw3d.CLAW3D_COMMIT)

    def runner(command, cwd=None):
        commands.append((tuple(command), cwd))

    claw3d.run_lifecycle(tmp_path, "uninstall.sh", ("--dry-run",), runner=runner)

    assert commands == [((str(root / "scripts" / "uninstall.sh"), "--dry-run"), root)]


def test_validation_rejects_a_symlinked_installation(tmp_path: Path, monkeypatch):
    real_root = tmp_path / "real"
    real_root.mkdir()
    linked_root = tmp_path / "linked"
    linked_root.symlink_to(real_root, target_is_directory=True)
    monkeypatch.setattr(claw3d, "apps_root", lambda jarvis_root=claw3d.JARVIS_ROOT: tmp_path)

    with pytest.raises(claw3d.Claw3DError, match="installation Claw3D invalide"):
        claw3d.validate_installation(linked_root, verify_commit=False)


def test_source_is_pinned_to_an_exact_public_commit():
    assert claw3d.CLAW3D_REPOSITORY == "https://github.com/AVTAVANTTOUT2/Claw3D.git"
    assert claw3d.CLAW3D_BRANCH == "codex/jarvis-visual-ui"
    assert claw3d.CLAW3D_COMMIT == "f66ee199223fbee51a3506c6f50f0a68db487cad"
