"""Rebase Git avec résolution automatique des conflits triviaux uniquement.

Un conflit est « trivial » seulement dans ces cas, bloc par bloc :

- les deux côtés du conflit sont strictement identiques une fois les espaces
  de fin de ligne normalisés (patch déjà appliqué des deux côtés) ;
- un des deux côtés est vide (l'autre branche n'a rien changé sur ce
  fragment) — on garde le côté non vide.

Tout le reste => ``git rebase --abort`` immédiat, jamais de résolution
partielle appliquée. Le rapport indique précisément les fichiers qui
nécessitent une intervention humaine.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from agents.devagent.executor import run_isolated

logger = logging.getLogger(__name__)

_NO_EDITOR_ENV = {"GIT_EDITOR": "true", "EDITOR": "true"}

_CONFLICT_RE = re.compile(
    r"<<<<<<< [^\n]*\n((?:[^\n]*\n)*?)=======\n((?:[^\n]*\n)*?)>>>>>>> [^\n]*\n"
)


def _resolve_trivial_conflicts(text: str) -> tuple[str, bool]:
    """Retourne (texte résolu, tout_résolu).

    ``ours``/``theirs`` capturent des lignes complètes (chacune avec son
    ``\\n``) — pas de ``\\n`` à rajouter en recomposant, contrairement à un
    découpage naïf par ``.*?`` qui perdrait le compte sur un côté vide.
    """
    all_resolved = True

    def _sub(m: re.Match) -> str:
        nonlocal all_resolved
        ours, theirs = m.group(1), m.group(2)
        if ours.strip() == theirs.strip():
            return ours or theirs
        if ours.strip() == "":
            return theirs
        if theirs.strip() == "":
            return ours
        all_resolved = False
        return m.group(0)

    resolved = _CONFLICT_RE.sub(_sub, text)
    return resolved, all_resolved


def safe_rebase(project_path: Path, onto: str = "main") -> dict:
    """Rebase avec résolution des conflits triviaux uniquement.

    Retourne ``{ok, resolved_trivial, message, files_needing_manual_review?}``.
    ``ok=False`` signifie que le rebase a été annulé (``--abort``) — le dépôt
    est revenu exactement à son état initial.
    """
    start = run_isolated(["git", "rebase", onto], cwd=project_path, timeout=60, env=_NO_EDITOR_ENV)
    if start["returncode"] == 0:
        return {"ok": True, "resolved_trivial": False, "message": "rebase propre, aucun conflit"}

    status = run_isolated(
        ["git", "diff", "--name-only", "--diff-filter=U"], cwd=project_path, timeout=15,
    )
    conflicted = [f for f in status["stdout"].splitlines() if f.strip()]
    if not conflicted:
        run_isolated(["git", "rebase", "--abort"], cwd=project_path, timeout=15)
        return {
            "ok": False, "resolved_trivial": False,
            "message": "échec du rebase pour une raison autre qu'un conflit de contenu",
            "raw_stderr": start["stderr"][:500],
        }

    manual_needed: list[str] = []
    for rel in conflicted:
        full = project_path / rel
        if not full.exists():
            manual_needed.append(rel)  # conflit add/delete — jamais trivial
            continue
        text = full.read_text(encoding="utf-8", errors="ignore")
        if "<<<<<<<" not in text:
            continue  # déjà résolu par git (rare mais possible)
        resolved_text, all_resolved = _resolve_trivial_conflicts(text)
        if not all_resolved:
            manual_needed.append(rel)
            continue
        full.write_text(resolved_text, encoding="utf-8")
        run_isolated(["git", "add", rel], cwd=project_path, timeout=15)

    if manual_needed:
        run_isolated(["git", "rebase", "--abort"], cwd=project_path, timeout=15)
        return {
            "ok": False, "resolved_trivial": False,
            "message": "conflit(s) non trivial(aux) — rebase annulé, aucun changement partiel",
            "files_needing_manual_review": manual_needed,
        }

    cont = run_isolated(
        ["git", "rebase", "--continue"], cwd=project_path, timeout=60, env=_NO_EDITOR_ENV,
    )
    if cont["returncode"] != 0:
        run_isolated(["git", "rebase", "--abort"], cwd=project_path, timeout=15)
        return {
            "ok": False, "resolved_trivial": True,
            "message": "conflits triviaux résolus mais 'rebase --continue' a échoué — annulé par sécurité",
            "raw_stderr": cont["stderr"][:500],
        }

    logger.info("[devagent-git] rebase terminé avec résolution triviale : %s", conflicted)
    return {
        "ok": True, "resolved_trivial": True,
        "message": "conflit(s) trivial(aux) résolu(s) automatiquement",
        "files_resolved": conflicted,
    }
