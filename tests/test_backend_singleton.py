"""Régressions du verrou d'instance acquis avant les imports lourds."""

from __future__ import annotations

import os

import pytest


def test_backend_instance_lock_rejects_a_second_owner(tmp_path) -> None:
    from main import _acquire_backend_instance_lock

    lock_path = tmp_path / "runtime" / "backend.lock"
    first = _acquire_backend_instance_lock(lock_path)
    try:
        assert lock_path.read_text(encoding="utf-8") == f"pid={os.getpid()}\n"
        with pytest.raises(SystemExit, match="déjà active"):
            _acquire_backend_instance_lock(lock_path)
    finally:
        first.close()

    replacement = _acquire_backend_instance_lock(lock_path)
    replacement.close()
