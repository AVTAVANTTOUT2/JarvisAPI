"""Gardes pures du jeton de contrôle supervisor (canal local)."""

from __future__ import annotations

import stat
from pathlib import Path
from unittest.mock import patch

import pytest

from core.file_security import PRIVATE_FILE_MODE, write_private_bytes
from core.supervisor_auth import (
    SUPERVISOR_CONTROL_HEADER,
    load_supervisor_control_token,
    supervisor_control_headers,
    verify_supervisor_control_token,
)

_VALID = "a" * 40
_OTHER = "b" * 40


@pytest.fixture
def token_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    import config

    path = tmp_path / ".supervisor_control_token"
    monkeypatch.setattr(config, "SUPERVISOR_CONTROL_TOKEN_FILE", str(path), raising=False)
    return path


def test_verify_rejects_missing_and_empty_provided(token_file: Path) -> None:
    write_private_bytes(token_file, _VALID.encode("utf-8"), exclusive=True)
    assert verify_supervisor_control_token(None) is False
    assert verify_supervisor_control_token("") is False


def test_verify_rejects_when_token_file_absent(token_file: Path) -> None:
    assert not token_file.exists()
    assert verify_supervisor_control_token(_VALID) is False


def test_verify_rejects_wrong_token(token_file: Path) -> None:
    write_private_bytes(token_file, _VALID.encode("utf-8"), exclusive=True)
    assert verify_supervisor_control_token(_OTHER) is False


def test_verify_accepts_exact_token(token_file: Path) -> None:
    write_private_bytes(token_file, _VALID.encode("utf-8"), exclusive=True)
    assert verify_supervisor_control_token(_VALID) is True


def test_verify_fail_closed_on_short_token_file(token_file: Path) -> None:
    token_file.write_text("too-short", encoding="utf-8")
    token_file.chmod(0o600)
    assert verify_supervisor_control_token("too-short") is False


def test_load_raises_on_short_token(token_file: Path) -> None:
    token_file.write_text("x" * 39, encoding="utf-8")
    token_file.chmod(0o600)
    with pytest.raises(RuntimeError, match="trop court"):
        load_supervisor_control_token(create=False)


def test_load_without_create_returns_none_when_absent(token_file: Path) -> None:
    assert load_supervisor_control_token(create=False) is None


def test_load_create_writes_private_token(token_file: Path) -> None:
    token = load_supervisor_control_token(create=True)
    assert token is not None
    assert len(token) >= 40
    assert token_file.read_text(encoding="utf-8").strip() == token
    assert stat.S_IMODE(token_file.stat().st_mode) == PRIVATE_FILE_MODE


def test_load_create_race_reloads_winner(token_file: Path) -> None:
    assert not token_file.exists()

    def _race_write(path: Path, _data: bytes, exclusive: bool = False) -> Path:
        # Un autre processus a déjà frappé le fichier exclusif.
        write_private_bytes(path, _VALID.encode("utf-8"), exclusive=True)
        raise FileExistsError

    with patch("core.supervisor_auth.write_private_bytes", side_effect=_race_write):
        assert load_supervisor_control_token(create=True) == _VALID


def test_supervisor_control_headers_include_token(token_file: Path) -> None:
    headers = supervisor_control_headers()
    assert set(headers) == {SUPERVISOR_CONTROL_HEADER}
    assert verify_supervisor_control_token(headers[SUPERVISOR_CONTROL_HEADER]) is True
