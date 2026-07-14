"""Audit sécurité — secrets exposés et patterns dangereux, avec correction
directe limitée aux cas mécaniquement sûrs.

Deux catégories de constats :

- **Secrets exposés** (sévérité high) : clés API / tokens en dur dans le
  code source. Le correctif direct (opt-in, ``SECURITY_AUTO_FIX_ENABLED``)
  remplace UNIQUEMENT le littéral détecté par un placeholder — jamais de
  tentative de deviner comment le câbler correctement (trop risqué). Le
  résultat : le secret n'est plus live dans le dépôt, et si le code
  l'utilisait vraiment, il échoue bruyamment (auth error) plutôt que de
  continuer à fuiter silencieusement.
- **Patterns dangereux** (sévérité medium) : ``eval``/``exec``,
  ``shell=True``, SQL construit par concaténation/f-string, ``pickle.loads``.
  Jamais de correctif automatique — corriger correctement exige de
  comprendre l'intention du code, ce qui n'est pas du ressort d'un scanner.

Le scan périodique (job hebdomadaire) ne fait JAMAIS de correctif — seuls
les appels explicites (endpoint / CLI) avec le flag activé peuvent muter le
code source.
"""

from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

import config
from database import get_security_findings, upsert_security_finding
from jarvis.notification_service import notification_service

logger = logging.getLogger(__name__)

DEFAULT_EXCLUDE_DIRS = frozenset({
    "__pycache__", "node_modules", "venv", ".venv", ".git", "dist", "build",
    "dev_projects", "data", ".pytest_cache", "generated",
})
EXCLUDE_FILES = frozenset({".env.example", ".env"})

# (nom de règle, pattern, sévérité)
SECRET_PATTERNS: list[tuple[str, re.Pattern, str]] = [
    ("secret_deepseek_key", re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"), "high"),
    ("secret_aws_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "high"),
    ("secret_github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"), "high"),
    ("secret_slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"), "high"),
    ("secret_generic_assignment", re.compile(
        r"""(?i)\b(api[_-]?key|secret|password|token)\s*=\s*['"][A-Za-z0-9_\-/+=]{12,}['"]"""), "high"),
]

DANGEROUS_PATTERNS: list[tuple[str, re.Pattern, str]] = [
    ("eval_usage", re.compile(r"\beval\s*\("), "medium"),
    ("exec_usage", re.compile(r"\bexec\s*\("), "medium"),
    ("shell_true", re.compile(r"shell\s*=\s*True"), "medium"),
    ("os_system", re.compile(r"\bos\.system\s*\("), "medium"),
    ("pickle_loads", re.compile(r"\bpickle\.loads?\s*\("), "medium"),
    ("sql_injection_fstring", re.compile(r"""execute\s*\(\s*f['"]"""), "high"),
    ("sql_injection_percent", re.compile(r"""execute\s*\(\s*['"][^'"]*['"]\s*%\s*"""), "high"),
    ("sql_injection_concat", re.compile(r"""execute\s*\(\s*['"][^'"]*['"]\s*\+"""), "high"),
]

ALL_PATTERNS = SECRET_PATTERNS + DANGEROUS_PATTERNS
_SECRET_RULE_NAMES = {name for name, _, _ in SECRET_PATTERNS}


@dataclass(frozen=True)
class Finding:
    file: str
    line: int
    rule: str
    severity: str
    snippet: str


def _redact(line: str) -> str:
    """Redacte toute chaîne quotée de 6+ caractères — ne stocke jamais le secret en clair."""
    return re.sub(r"""(['"])[^'"]{6,}\1""", r"\1«redacted»\1", line)


def _iter_source_files(root: Path, dirs: list[str], exclude_dirs: frozenset[str]) -> list[Path]:
    files: list[Path] = []
    for d in dirs:
        target = root / d
        if target.is_dir():
            files.extend(
                p for p in target.rglob("*.py")
                if not any(part in exclude_dirs for part in p.parts) and p.name not in EXCLUDE_FILES
            )
        elif target.is_file() and target.suffix == ".py":
            files.append(target)
    return files


def scan_file(path: Path, root: Path) -> list[Finding]:
    if path.name in EXCLUDE_FILES:
        return []
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return []
    rel = str(path.relative_to(root)) if path.is_absolute() else str(path)
    findings = []
    for lineno, line in enumerate(lines, start=1):
        for rule, pattern, severity in ALL_PATTERNS:
            if pattern.search(line):
                is_secret = rule in _SECRET_RULE_NAMES
                snippet = _redact(line.strip())[:200] if is_secret else line.strip()[:200]
                findings.append(Finding(file=rel, line=lineno, rule=rule, severity=severity, snippet=snippet))
    return findings


def scan_repo(root: Path, dirs: list[str], exclude_dirs: frozenset[str] = DEFAULT_EXCLUDE_DIRS) -> list[Finding]:
    findings: list[Finding] = []
    for f in _iter_source_files(root, dirs, exclude_dirs):
        findings.extend(scan_file(f, root))
    return findings


def scan_and_report(root: Path | None = None) -> dict:
    """Scan complet, persiste les nouveaux constats, notifie sur les nouveaux 'high'."""
    if not config.SECURITY_AUDIT_ENABLED:
        return {"ok": False, "reason": "disabled"}

    root = root or config.BASE_DIR
    dirs = [d.strip() for d in config.SECURITY_AUDIT_DIRS.split(",") if d.strip()]
    findings = scan_repo(root, dirs)

    new_high = []
    new_count = 0
    for f in findings:
        is_new = upsert_security_finding(f.file, f.line, f.rule, f.severity, f.snippet)
        if is_new:
            new_count += 1
            if f.severity == "high":
                new_high.append(f)

    if new_high:
        lines = [f"{f.file}:{f.line} ({f.rule})" for f in new_high[:5]]
        notification_service.create(
            source="system",
            title=f"Audit sécurité — {len(new_high)} constat(s) critique(s)",
            content="; ".join(lines),
            priority="high",
        )
        logger.warning("[security-audit] %d nouveau(x) constat(s) high", len(new_high))

    return {"ok": True, "total_findings": len(findings), "new_findings": new_count, "new_high": len(new_high)}


def list_open_findings(limit: int = 200) -> list[dict]:
    return get_security_findings(status="open", limit=limit)


def _is_git_tracked(path: Path, root: Path) -> bool:
    try:
        result = subprocess.run(
            ["git", "ls-files", "--error-unmatch", str(path.relative_to(root))],
            cwd=root, capture_output=True, text=True, timeout=10,
        )
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def apply_safe_fix(finding: dict, root: Path | None = None) -> dict:
    """Redaction mécanique d'un secret détecté — jamais pour les patterns dangereux.

    Remplace le littéral quoté détecté par ``"REDACTED_BY_SECURITY_AUDIT"`` sur
    la ligne exacte. N'agit que si ``SECURITY_AUTO_FIX_ENABLED`` est activé,
    que la règle est une catégorie 'secret_*', et que le fichier est suivi par
    git (donc trivialement réversible via ``git diff`` / ``git checkout``).
    """
    if not config.SECURITY_AUTO_FIX_ENABLED:
        return {"applied": False, "reason": "SECURITY_AUTO_FIX_ENABLED désactivé"}
    if finding["rule"] not in _SECRET_RULE_NAMES:
        return {"applied": False, "reason": "correctif automatique réservé aux secrets (pas aux patterns dangereux)"}

    root = root or config.BASE_DIR
    path = root / finding["file"]
    if not path.is_file():
        return {"applied": False, "reason": "fichier introuvable"}
    if not _is_git_tracked(path, root):
        return {"applied": False, "reason": "fichier non suivi par git — correctif refusé par sécurité"}

    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    idx = finding["line"] - 1
    if idx < 0 or idx >= len(lines):
        return {"applied": False, "reason": "numéro de ligne invalide (fichier modifié depuis le scan)"}

    original = lines[idx]
    fixed = re.sub(r"""(['"])[^'"]{6,}\1""", r'\1REDACTED_BY_SECURITY_AUDIT\1', original)
    if fixed == original:
        return {"applied": False, "reason": "aucun littéral trouvé à cette ligne (déjà corrigé ?)"}

    lines[idx] = fixed
    path.write_text("".join(lines), encoding="utf-8")
    from database import update_security_finding_status

    update_security_finding_status(finding["id"], "fixed")
    logger.warning("[security-audit] secret redacté : %s:%d", finding["file"], finding["line"])
    return {"applied": True, "file": finding["file"], "line": finding["line"]}
