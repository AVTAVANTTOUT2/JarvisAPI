"""Installe le hook pre-commit qui déclenche la CI locale à chaque commit.

Usage : ``python scripts/install_git_hooks.py`` (idempotent — écrase un hook
précédemment installé par ce script, refuse d'écraser un hook tiers déjà en
place sans ``--force``).
"""

from __future__ import annotations

import stat
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
HOOK_MARKER = "# installed-by: scripts/install_git_hooks.py"

HOOK_TEMPLATE = f"""#!/bin/sh
{HOOK_MARKER}
# CI locale JARVIS — lint + tests avant chaque commit. Le build frontend
# (lent) n'est PAS lancé ici ; voir LOCAL_CI_RUN_FRONTEND_BUILD dans .env
# pour l'inclure, ou lancez-le manuellement avant un push.
python3 "$(git rev-parse --show-toplevel)/scripts/local_ci.py"
exit $?
"""


def hooks_dir() -> Path:
    return BASE_DIR / ".git" / "hooks"


def install(force: bool = False) -> dict:
    d = hooks_dir()
    if not d.is_dir():
        return {"ok": False, "reason": f"pas un dépôt git (dossier introuvable : {d})"}

    hook_path = d / "pre-commit"
    if hook_path.exists():
        existing = hook_path.read_text(encoding="utf-8", errors="ignore")
        if HOOK_MARKER not in existing and not force:
            return {
                "ok": False,
                "reason": f"un hook pre-commit tiers existe déjà ({hook_path}) — "
                          "utilisez force=True pour l'écraser.",
            }

    hook_path.write_text(HOOK_TEMPLATE, encoding="utf-8")
    hook_path.chmod(hook_path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return {"ok": True, "path": str(hook_path)}


def uninstall() -> dict:
    hook_path = hooks_dir() / "pre-commit"
    if not hook_path.exists():
        return {"ok": True, "removed": False}
    content = hook_path.read_text(encoding="utf-8", errors="ignore")
    if HOOK_MARKER not in content:
        return {"ok": False, "reason": "le hook pre-commit présent n'a pas été installé par ce script"}
    hook_path.unlink()
    return {"ok": True, "removed": True}


if __name__ == "__main__":
    force = "--force" in sys.argv
    uninstall_mode = "--uninstall" in sys.argv
    result = uninstall() if uninstall_mode else install(force=force)
    print(result)
    sys.exit(0 if result.get("ok") else 1)
