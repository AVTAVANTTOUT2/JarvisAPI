"""Tests de l'audit sécurité (détection + correctif mécanique opt-in)."""

from __future__ import annotations

import subprocess
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


def test_detects_hardcoded_secret(tmp_path):
    from scripts.security_audit import scan_repo

    (tmp_path / "a.py").write_text(
        'DEEPSEEK_API_KEY = "sk-abcdefghijklmnopqrstuvwxyz123456"\n', encoding="utf-8",
    )
    findings = scan_repo(tmp_path, ["."])
    assert any(f.rule == "secret_deepseek_key" for f in findings)
    # le secret ne doit JAMAIS apparaître en clair dans le snippet stocké
    secret_finding = next(f for f in findings if f.rule == "secret_deepseek_key")
    assert "abcdefghijklmnopqrstuvwxyz123456" not in secret_finding.snippet
    assert "«redacted»" in secret_finding.snippet


def test_detects_dangerous_patterns(tmp_path):
    from scripts.security_audit import scan_repo

    (tmp_path / "a.py").write_text(
        "eval(user_input)\n"
        "subprocess.run(cmd, shell=True)\n"
        'cursor.execute(f"SELECT * FROM t WHERE id={x}")\n',
        encoding="utf-8",
    )
    findings = scan_repo(tmp_path, ["."])
    rules = {f.rule for f in findings}
    assert "eval_usage" in rules
    assert "shell_true" in rules
    assert "sql_injection_fstring" in rules


def test_clean_file_has_no_findings(tmp_path):
    from scripts.security_audit import scan_repo

    (tmp_path / "clean.py").write_text(
        "def add(a, b):\n    return a + b\n", encoding="utf-8",
    )
    assert scan_repo(tmp_path, ["."]) == []


def test_env_example_never_flagged(tmp_path):
    from scripts.security_audit import scan_repo

    (tmp_path / ".env.example").write_text('API_KEY="sk-fakeplaceholder1234567890"\n', encoding="utf-8")
    # .env.example n'a pas d'extension .py donc de toute façon hors scope, mais
    # vérifions aussi un cas où le nom de fichier est explicitement exclu.
    assert scan_repo(tmp_path, ["."]) == []


def test_excludes_pycache_dir(tmp_path):
    from scripts.security_audit import scan_repo

    pycache = tmp_path / "__pycache__"
    pycache.mkdir()
    (pycache / "a.py").write_text('TOKEN = "ghp_abcdefghijklmnopqrstuvwxyz"\n', encoding="utf-8")
    assert scan_repo(tmp_path, ["."]) == []


def test_scan_and_report_persists_and_notifies_high_only(tmp_db, tmp_path, monkeypatch):
    from database import get_security_findings, get_unread_notifications
    from scripts.security_audit import scan_and_report

    monkeypatch.setattr("config.SECURITY_AUDIT_ENABLED", True)
    monkeypatch.setattr("config.SECURITY_AUDIT_DIRS", ".")
    (tmp_path / "a.py").write_text(
        'API_KEY = "sk-abcdefghijklmnopqrstuvwxyz123456"\n'
        "eval(x)\n",
        encoding="utf-8",
    )
    report = scan_and_report(root=tmp_path)
    assert report["ok"] is True
    assert report["new_high"] >= 1

    findings = get_security_findings("open")
    # la ligne API_KEY="sk-..." matche à la fois secret_deepseek_key et
    # secret_generic_assignment (deux règles indépendantes, légitimement) + eval_usage
    assert len(findings) == 3
    assert {f["rule"] for f in findings} == {"secret_deepseek_key", "secret_generic_assignment", "eval_usage"}
    notifs = [n for n in get_unread_notifications(10) if "Audit sécurité" in n["title"]]
    assert len(notifs) == 1

    # rejouer : rien de nouveau
    report2 = scan_and_report(root=tmp_path)
    assert report2["new_findings"] == 0


def test_scan_disabled(tmp_db, tmp_path, monkeypatch):
    from scripts.security_audit import scan_and_report

    monkeypatch.setattr("config.SECURITY_AUDIT_ENABLED", False)
    assert scan_and_report(root=tmp_path) == {"ok": False, "reason": "disabled"}


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "add", "-A"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, check=True, capture_output=True)


def test_apply_safe_fix_redacts_secret_only(tmp_db, tmp_path, monkeypatch):
    from database import get_security_findings, upsert_security_finding
    from scripts.security_audit import apply_safe_fix

    (tmp_path / "a.py").write_text('API_KEY = "sk-abcdefghijklmnopqrstuvwxyz123456"\n', encoding="utf-8")
    _init_git_repo(tmp_path)

    upsert_security_finding("a.py", 1, "secret_deepseek_key", "high", "redacted")
    finding = get_security_findings("open")[0]

    monkeypatch.setattr("config.SECURITY_AUTO_FIX_ENABLED", True)
    result = apply_safe_fix(finding, root=tmp_path)
    assert result["applied"] is True

    content = (tmp_path / "a.py").read_text(encoding="utf-8")
    assert "sk-abcdefghijklmnopqrstuvwxyz123456" not in content
    assert "REDACTED_BY_SECURITY_AUDIT" in content
    assert get_security_findings("open") == []  # marqué 'fixed'


def test_apply_safe_fix_refuses_dangerous_pattern(tmp_db, tmp_path, monkeypatch):
    from database import upsert_security_finding, get_security_findings
    from scripts.security_audit import apply_safe_fix

    (tmp_path / "a.py").write_text("eval(x)\n", encoding="utf-8")
    _init_git_repo(tmp_path)
    upsert_security_finding("a.py", 1, "eval_usage", "medium", "eval(x)")
    finding = get_security_findings("open")[0]

    monkeypatch.setattr("config.SECURITY_AUTO_FIX_ENABLED", True)
    result = apply_safe_fix(finding, root=tmp_path)
    assert result["applied"] is False
    assert "secrets" in result["reason"]


def test_apply_safe_fix_disabled_by_default(tmp_db, tmp_path):
    from database import upsert_security_finding, get_security_findings
    from scripts.security_audit import apply_safe_fix

    (tmp_path / "a.py").write_text('API_KEY = "sk-abcdefghijklmnopqrstuvwxyz123456"\n', encoding="utf-8")
    _init_git_repo(tmp_path)
    upsert_security_finding("a.py", 1, "secret_deepseek_key", "high", "x")
    finding = get_security_findings("open")[0]

    result = apply_safe_fix(finding, root=tmp_path)
    assert result["applied"] is False
    assert "SECURITY_AUTO_FIX_ENABLED" in result["reason"]


def test_apply_safe_fix_refuses_untracked_file(tmp_db, tmp_path, monkeypatch):
    from database import upsert_security_finding, get_security_findings
    from scripts.security_audit import apply_safe_fix

    (tmp_path / "a.py").write_text('API_KEY = "sk-abcdefghijklmnopqrstuvwxyz123456"\n', encoding="utf-8")
    # pas de dépôt git ici : le fichier n'est pas "tracké"
    upsert_security_finding("a.py", 1, "secret_deepseek_key", "high", "x")
    finding = get_security_findings("open")[0]

    monkeypatch.setattr("config.SECURITY_AUTO_FIX_ENABLED", True)
    result = apply_safe_fix(finding, root=tmp_path)
    assert result["applied"] is False
    assert "git" in result["reason"]
