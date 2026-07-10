"""Tests du scanner de code dupliqué."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture
def tmp_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    db_path = tmp_path / "test_jarvis.db"
    monkeypatch.setattr("config.DB_PATH", str(db_path))
    monkeypatch.setattr("database.DB_PATH", db_path)
    from database import init_db

    init_db()
    return db_path


DUP_BLOCK = """def compute(x, y):
    total = x + y
    total *= 2
    total -= 1
    result = total / 3
    return result
"""


def test_finds_exact_duplicate_across_files(tmp_path):
    from scripts.duplicate_scanner import find_duplicates

    (tmp_path / "a.py").write_text(f"def unrelated():\n    pass\n\n{DUP_BLOCK}", encoding="utf-8")
    (tmp_path / "b.py").write_text(f"{DUP_BLOCK}\ndef other():\n    pass\n", encoding="utf-8")

    blocks = find_duplicates(tmp_path, ["."], min_lines=6)
    assert len(blocks) == 1
    b = blocks[0]
    assert {b.file_a, b.file_b} == {"a.py", "b.py"}
    assert b.lines >= 6


def test_no_duplicate_below_min_lines(tmp_path):
    from scripts.duplicate_scanner import find_duplicates

    (tmp_path / "a.py").write_text("x = 1\ny = 2\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("x = 1\ny = 2\n", encoding="utf-8")

    assert find_duplicates(tmp_path, ["."], min_lines=6) == []


def test_ignores_excluded_dirs(tmp_path):
    from scripts.duplicate_scanner import find_duplicates

    (tmp_path / "a.py").write_text(DUP_BLOCK, encoding="utf-8")
    pycache = tmp_path / "__pycache__"
    pycache.mkdir()
    (pycache / "b.py").write_text(DUP_BLOCK, encoding="utf-8")

    assert find_duplicates(tmp_path, ["."], min_lines=6) == []


def test_extends_block_across_adjacent_windows(tmp_path):
    from scripts.duplicate_scanner import find_duplicates

    long_block = "\n".join(f"line_{i} = {i}" for i in range(20))
    (tmp_path / "a.py").write_text(long_block + "\n", encoding="utf-8")
    (tmp_path / "b.py").write_text(long_block + "\n", encoding="utf-8")

    blocks = find_duplicates(tmp_path, ["."], min_lines=6)
    # un seul bloc fusionné de 20 lignes, pas 15 fenêtres qui se chevauchent
    assert len(blocks) == 1
    assert blocks[0].lines == 20


def test_scan_and_report_persists_and_notifies(tmp_db, tmp_path, monkeypatch):
    from database import get_duplicate_findings, get_unread_notifications
    from scripts.duplicate_scanner import scan_and_report

    monkeypatch.setattr("config.DUPLICATE_SCAN_ENABLED", True)
    monkeypatch.setattr("config.DUPLICATE_SCAN_DIRS", ".")
    monkeypatch.setattr("config.DUPLICATE_SCAN_MIN_LINES", 6)
    (tmp_path / "a.py").write_text(DUP_BLOCK, encoding="utf-8")
    (tmp_path / "b.py").write_text(DUP_BLOCK, encoding="utf-8")

    report = scan_and_report(root=tmp_path)
    assert report["ok"] is True
    assert report["new_findings"] == 1
    assert len(get_duplicate_findings("open")) == 1
    assert any(n["title"].startswith("Code dupliqué") for n in get_unread_notifications(10))

    # rejouer : mêmes fichiers, aucun nouveau constat, pas de nouvelle notif
    report2 = scan_and_report(root=tmp_path)
    assert report2["new_findings"] == 0


def test_scan_disabled_by_config(tmp_db, tmp_path, monkeypatch):
    from scripts.duplicate_scanner import scan_and_report

    monkeypatch.setattr("config.DUPLICATE_SCAN_ENABLED", False)
    assert scan_and_report(root=tmp_path) == {"ok": False, "reason": "disabled"}
