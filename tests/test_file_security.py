"""Permissions 0600/0700 et refus des symlinks pour les secrets persistants."""

from __future__ import annotations

import stat
from pathlib import Path

import pytest

from core.file_security import (
    PRIVATE_DIRECTORY_MODE,
    PRIVATE_FILE_MODE,
    ensure_private_directory,
    ensure_private_file,
    write_private_bytes,
)


def test_ensure_private_directory_creates_700(tmp_path: Path) -> None:
    target = tmp_path / "secrets"
    result = ensure_private_directory(target)
    assert result == target
    assert target.is_dir()
    assert not target.is_symlink()
    assert stat.S_IMODE(target.stat().st_mode) == PRIVATE_DIRECTORY_MODE


def test_ensure_private_directory_rejects_symlink(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)
    with pytest.raises(RuntimeError, match="lien symbolique"):
        ensure_private_directory(link)


def test_ensure_private_file_forces_600(tmp_path: Path) -> None:
    path = tmp_path / "token"
    path.write_text("x" * 40, encoding="utf-8")
    path.chmod(0o644)
    result = ensure_private_file(path)
    assert result == path
    assert stat.S_IMODE(path.stat().st_mode) == PRIVATE_FILE_MODE


def test_ensure_private_file_rejects_symlink(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.write_text("secret", encoding="utf-8")
    link = tmp_path / "link"
    link.symlink_to(real)
    with pytest.raises(RuntimeError, match="lien symbolique"):
        ensure_private_file(link)


def test_write_private_bytes_writes_600_and_content(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "token"
    result = write_private_bytes(path, b"payload-bytes")
    assert result == path
    assert path.read_bytes() == b"payload-bytes"
    assert stat.S_IMODE(path.stat().st_mode) == PRIVATE_FILE_MODE
    assert stat.S_IMODE(path.parent.stat().st_mode) == PRIVATE_DIRECTORY_MODE


def test_write_private_bytes_rejects_symlink_destination(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.write_bytes(b"old")
    link = tmp_path / "link"
    link.symlink_to(real)
    with pytest.raises(RuntimeError, match="lien symbolique"):
        write_private_bytes(link, b"new")
    assert real.read_bytes() == b"old"


def test_write_private_bytes_exclusive_refuses_existing(tmp_path: Path) -> None:
    path = tmp_path / "token"
    write_private_bytes(path, b"first", exclusive=True)
    with pytest.raises(FileExistsError):
        write_private_bytes(path, b"second", exclusive=True)
    assert path.read_bytes() == b"first"


def test_write_private_bytes_overwrite_truncates(tmp_path: Path) -> None:
    path = tmp_path / "token"
    write_private_bytes(path, b"longer-initial-payload")
    write_private_bytes(path, b"short")
    assert path.read_bytes() == b"short"
