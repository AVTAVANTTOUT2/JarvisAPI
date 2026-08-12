"""Contrats du gestionnaire optionnel Claw3D côté JarvisAPI."""

from __future__ import annotations

import json
import os
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
        ("https://localhost/", "https://localhost"),
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
        "https://jarvis.example",
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


def test_visual_credentials_are_scoped_private_and_stable(tmp_path: Path):
    jarvis_root = tmp_path / "jarvis"
    root = jarvis_root / ".jarvis" / "apps" / "claw3d"
    root.mkdir(parents=True)

    token_path, ca_path = claw3d.provision_visual_credentials(
        root,
        "http://127.0.0.1:8080",
    )
    original = token_path.read_text(encoding="ascii")
    repeated, repeated_ca = claw3d.provision_visual_credentials(
        root,
        "http://127.0.0.1:8080",
    )

    assert repeated == token_path
    assert repeated.read_text(encoding="ascii") == original
    assert token_path.stat().st_mode & 0o777 == 0o600
    assert ca_path is None and repeated_ca is None


def test_visual_state_root_is_private_enough_for_the_claw3d_connector(tmp_path: Path):
    """Le connecteur Claw3D refuse un état projet lisible hors du propriétaire.

    Créé au umask usuel, ``.claw3d`` naissait en 0755 et le relais visuel
    répondait ``visual_connector_unavailable`` sans indiquer la cause.
    """

    root = tmp_path / "jarvis" / ".jarvis" / "apps" / "claw3d"
    root.mkdir(parents=True)
    (root / ".claw3d").mkdir(mode=0o755)

    token_path, _ = claw3d.provision_visual_credentials(root, "http://127.0.0.1:8080")

    for directory in (root / ".claw3d", token_path.parent):
        assert directory.stat().st_mode & 0o077 == 0, directory


def test_visual_credentials_copy_only_public_ca_for_https(tmp_path: Path):
    jarvis_root = tmp_path / "jarvis"
    root = jarvis_root / ".jarvis" / "apps" / "claw3d"
    root.mkdir(parents=True)
    cert = jarvis_root / "certs" / "cert.pem"
    cert.parent.mkdir()
    cert.write_text("PUBLIC TEST CERTIFICATE\n", encoding="ascii")

    _, ca_path = claw3d.provision_visual_credentials(
        root,
        "https://127.0.0.1:8080",
    )

    assert ca_path is not None
    assert ca_path.read_text(encoding="ascii") == "PUBLIC TEST CERTIFICATE\n"
    assert ca_path.stat().st_mode & 0o777 == 0o600


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


def test_is_installed_and_running_helpers(tmp_path: Path, monkeypatch):
    assert claw3d.is_installed(tmp_path) is False
    root = _fake_installation(tmp_path)
    monkeypatch.setattr(claw3d, "apps_root", lambda jarvis_root=claw3d.JARVIS_ROOT: root.parent)
    assert claw3d.is_installed(tmp_path) is True
    assert claw3d.is_running(tmp_path) is False

    state_dir = root / ".claw3d" / "run"
    state_dir.mkdir(parents=True)
    state_file = state_dir / "claw3d.state"
    state_file.write_text(f"pid={os.getpid()}\n", encoding="utf-8")
    assert claw3d.running_pid(tmp_path) is None

    started = "Tue Aug 11 16:46:38 2026"
    state_file.write_text(
        f"pid={os.getpid()}\nroot={root.resolve()}\nport=3000\nstarted={started}\n",
        encoding="utf-8",
    )

    def capture(command, cwd=None):
        del cwd
        return started if command[-1] == "lstart=" else "next-server (v15.5.12)"

    monkeypatch.setattr(claw3d, "_capture", capture)
    assert claw3d.running_pid(tmp_path) == os.getpid()
    assert claw3d.is_running(tmp_path) is True

    monkeypatch.setattr(claw3d, "_capture", lambda command, cwd=None: "autre démarrage")
    assert claw3d.running_pid(tmp_path) is None


def test_sync_managed_configuration_rewrites_readonly_origin(tmp_path: Path, monkeypatch):
    root = _fake_installation(tmp_path)
    # HTTPS readonly exige le CA public JARVIS (même contrat que provision_visual_credentials).
    cert = tmp_path / "certs" / "cert.pem"
    cert.parent.mkdir()
    cert.write_text("PUBLIC TEST CERTIFICATE\n", encoding="ascii")
    monkeypatch.setattr(claw3d, "apps_root", lambda jarvis_root=claw3d.JARVIS_ROOT: root.parent)
    monkeypatch.setattr(
        claw3d,
        "validate_installation",
        lambda root, expected_parent=None, verify_commit=True: None,
    )

    claw3d.sync_managed_configuration(
        tmp_path,
        mode="jarvis-readonly",
        jarvis_origin="https://127.0.0.1:8081",
        host="127.0.0.1",
        port=3000,
    )
    content = (root / ".env").read_text(encoding="utf-8")
    token_path = root / claw3d.VISUAL_TOKEN_RELATIVE_PATH
    ca_path = root / claw3d.VISUAL_CA_RELATIVE_PATH
    assert "VISUAL_ADAPTER=jarvis-readonly" in content
    assert "JARVIS_ORIGIN=https://127.0.0.1:8081" in content
    assert "CLAW3D_PORT=3000" in content
    assert f"JARVIS_VISUAL_TOKEN_FILE={token_path.resolve()}" in content
    assert f"NODE_EXTRA_CA_CERTS={ca_path.resolve()}" in content
    assert token_path.is_file() and token_path.stat().st_mode & 0o777 == 0o600
    assert ca_path.read_text(encoding="ascii") == "PUBLIC TEST CERTIFICATE\n"
    assert ca_path.stat().st_mode & 0o777 == 0o600


def test_source_is_pinned_to_an_exact_public_commit():
    assert claw3d.CLAW3D_REPOSITORY == "https://github.com/AVTAVANTTOUT2/Claw3D.git"
    assert claw3d.CLAW3D_COMMIT == "202feaf0efd8ae92451368d408e387a507da0192"


def _prepare_update(tmp_path: Path, monkeypatch, *, head: str, dirty: str = ""):
    """Installation factice prête pour ``update_installation``.

    ``_capture`` est stubbé par commande : ``rev-parse`` suit l'état simulé du
    checkout (il change après le ``git checkout``), ``status`` décrit la
    propreté de l'arbre.
    """

    root = _fake_installation(tmp_path)
    monkeypatch.setattr(claw3d, "apps_root", lambda jarvis_root=claw3d.JARVIS_ROOT: root.parent)
    monkeypatch.setattr(claw3d, "_require_tool", lambda name: None)
    state = {"head": head}
    commands: list[tuple[tuple[str, ...], Path | None]] = []

    def capture(command, cwd=None):
        if "rev-parse" in command:
            return state["head"]
        if "status" in command:
            return dirty
        raise AssertionError(f"commande inattendue: {command}")

    def runner(command, cwd=None):
        commands.append((tuple(command), cwd))
        if "checkout" in command:
            state["head"] = claw3d.CLAW3D_COMMIT

    monkeypatch.setattr(claw3d, "_capture", capture)
    return root, commands, runner


def test_update_realigns_a_stale_checkout_on_the_pinned_commit(tmp_path: Path, monkeypatch):
    root, commands, runner = _prepare_update(tmp_path, monkeypatch, head="0" * 40)

    assert claw3d.update_installation(tmp_path, runner=runner) == claw3d.CLAW3D_COMMIT

    assert commands == [
        (
            (
                "git",
                "fetch",
                "--filter=blob:none",
                "--no-tags",
                claw3d.CLAW3D_REPOSITORY,
                claw3d.CLAW3D_COMMIT,
            ),
            root,
        ),
        (("git", "checkout", "--detach", claw3d.CLAW3D_COMMIT), root),
        ((str(root / "scripts" / "install.sh"),), root),
    ]


def test_update_never_moves_to_a_revision_other_than_the_pin(tmp_path: Path, monkeypatch):
    _, commands, runner = _prepare_update(tmp_path, monkeypatch, head="0" * 40)

    claw3d.update_installation(tmp_path, runner=runner)

    referenced = {argument for command, _ in commands for argument in command}
    assert claw3d.CLAW3D_COMMIT in referenced
    assert not any(
        argument in referenced
        for argument in ("main", "HEAD", "origin/main", "--force", "FETCH_HEAD")
    )


def test_update_is_a_noop_when_the_checkout_is_already_pinned(tmp_path: Path, monkeypatch):
    _, commands, runner = _prepare_update(tmp_path, monkeypatch, head=claw3d.CLAW3D_COMMIT)

    assert claw3d.update_installation(tmp_path, runner=runner) == claw3d.CLAW3D_COMMIT
    assert commands == []


def test_update_refuses_to_discard_local_modifications(tmp_path: Path, monkeypatch):
    _, commands, runner = _prepare_update(
        tmp_path, monkeypatch, head="0" * 40, dirty=" M src/app/page.tsx\n"
    )

    with pytest.raises(claw3d.Claw3DError, match="modifié localement"):
        claw3d.update_installation(tmp_path, runner=runner)
    assert commands == []


def test_update_refuses_a_missing_installation(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(claw3d, "_require_tool", lambda name: None)
    apps = tmp_path / ".jarvis" / "apps"
    monkeypatch.setattr(claw3d, "apps_root", lambda jarvis_root=claw3d.JARVIS_ROOT: apps)

    with pytest.raises(claw3d.Claw3DError, match="installation Claw3D absente"):
        claw3d.update_installation(tmp_path)
