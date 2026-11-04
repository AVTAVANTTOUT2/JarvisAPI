"""Cible Desktop bornée pour les runs agentiques demandés sur le Bureau."""

from pathlib import Path

from jarvis.agentic.desktop_workspace import resolve_desktop_workspace


def test_named_folder_is_created_under_desktop(tmp_path: Path) -> None:
    desktop = tmp_path / "Desktop"
    desktop.mkdir()

    target = resolve_desktop_workspace(
        "crée une todolist appelé todojarvis sur le bureau",
        home=tmp_path,
    )

    assert target == desktop / "todojarvis"
    assert target.is_dir()


def test_bureau_de_mon_mac_is_a_desktop_destination(tmp_path: Path) -> None:
    (tmp_path / "Desktop").mkdir()

    target = resolve_desktop_workspace(
        "dans le bureau de mon mac crée une todolist appelé todojarvis",
        home=tmp_path,
    )

    assert target is not None
    assert target.name == "todojarvis"


def test_rejects_path_escape_in_folder_name(tmp_path: Path) -> None:
    (tmp_path / "Desktop").mkdir()

    assert (
        resolve_desktop_workspace(
            "crée un projet appelé ../Secrets sur le bureau",
            home=tmp_path,
        )
        is None
    )


def test_returns_none_when_desktop_is_not_requested(tmp_path: Path) -> None:
    (tmp_path / "Desktop").mkdir()

    assert (
        resolve_desktop_workspace("corrige le bug dans le dépôt", home=tmp_path)
        is None
    )


def test_rejects_missing_desktop(tmp_path: Path) -> None:
    assert (
        resolve_desktop_workspace(
            "crée un dossier appelé projetx sur le bureau",
            home=tmp_path,
        )
        is None
    )
    assert not (tmp_path / "Desktop").exists()


def test_rejects_symlinked_desktop(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / "Desktop").symlink_to(outside)

    assert (
        resolve_desktop_workspace(
            "crée un dossier appelé projetx sur le bureau",
            home=tmp_path,
        )
        is None
    )
    assert not (outside / "projetx").exists()


def test_rejects_symlinked_candidate_folder(tmp_path: Path) -> None:
    desktop = tmp_path / "Desktop"
    desktop.mkdir()
    outside = tmp_path / "escape"
    outside.mkdir()
    (desktop / "projetx").symlink_to(outside)

    assert (
        resolve_desktop_workspace(
            "crée un dossier appelé projetx sur le bureau",
            home=tmp_path,
            create=False,
        )
        is None
    )


def test_create_false_does_not_mkdir_missing_folder(tmp_path: Path) -> None:
    desktop = tmp_path / "Desktop"
    desktop.mkdir()

    assert (
        resolve_desktop_workspace(
            "crée un dossier appelé projetx sur le bureau",
            home=tmp_path,
            create=False,
        )
        is None
    )
    assert not (desktop / "projetx").exists()
