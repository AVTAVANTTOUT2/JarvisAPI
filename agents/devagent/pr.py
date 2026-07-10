"""Génération automatique de description de PR + changelog pour un projet DevAgent.

Un projet DevAgent est un dépôt git **local et isolé** (``dev_projects/{slug}/``),
pas nécessairement relié à un remote GitHub. La partie systématiquement
disponible — description + changelog générés depuis l'historique git réel —
est donc toujours produite et écrite dans ``PR_DESCRIPTION.md`` à la racine
du projet.

L'ouverture effective d'une PR sur GitHub est **best-effort** : seulement si
la CLI ``gh`` est installée et un remote configuré. Sans ça, la fonction
retourne la description générée avec des instructions manuelles claires.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from pathlib import Path

from agents.devagent.executor import git_current_sha, git_diff_stat, git_log_range, run_isolated
from agents.devagent.utils import parse_json_response
from integrations.deepseek_client import call_deepseek

logger = logging.getLogger(__name__)

PR_PROMPT = """Role: tech lead qui rédige une pull request.
Nom du projet : {project_name}
Log des commits ({base}..HEAD) :
{log}

Statistiques de diff :
{diff_stat}

Rédige une description de PR complète en français, professionnelle et
concrète (pas de généralités). Retourne UNIQUEMENT ce JSON :
{{
  "title": "titre court (< 70 caractères)",
  "summary": "résumé en 2-4 phrases de ce que fait cette PR",
  "changelog": ["entrée de changelog 1", "entrée de changelog 2", "..."],
  "test_plan": ["étape de vérification 1", "..."]
}}
"""


def _first_commit_sha(project_path: Path) -> str | None:
    """SHA du tout premier commit — sert de `base` par défaut si aucun n'est fourni."""
    result = run_isolated(
        ["git", "log", "--reverse", "--format=%H"], cwd=project_path, timeout=15,
    )
    if result["returncode"] != 0 or not result["stdout"].strip():
        return None
    return result["stdout"].splitlines()[0].strip()


def _render_markdown(title: str, summary: str, changelog: list[str], test_plan: list[str]) -> str:
    lines = [f"# {title}", "", "## Résumé", summary, "", "## Changelog"]
    lines += [f"- {c}" for c in changelog] or ["- (aucune entrée)"]
    lines += ["", "## Plan de test"]
    lines += [f"- [ ] {t}" for t in test_plan] or ["- [ ] (à définir)"]
    return "\n".join(lines) + "\n"


async def generate_pr_description(project_path: Path, project_name: str, base: str | None = None) -> dict:
    """Génère description + changelog depuis l'historique git réel. Écrit PR_DESCRIPTION.md.

    Retourne ``{ok, title, summary, changelog, test_plan, path, reason?}``.
    """
    base = base or _first_commit_sha(project_path)
    log = git_log_range(project_path, base=base or "")
    if not log.strip():
        return {"ok": False, "reason": "aucun commit à décrire (historique vide ou base == HEAD)"}

    diff_stat = git_diff_stat(project_path, base=base or "")

    try:
        response = await call_deepseek(
            system=PR_PROMPT.format(
                project_name=project_name, base=base or "(racine)", log=log, diff_stat=diff_stat,
            ),
            user="Génère la description de PR.",
            json_mode=True,
        )
        payload = parse_json_response(response["content"])
    except Exception as e:
        logger.warning("[devagent-pr] génération LLM indisponible, fallback brut : %s", e)
        payload = {
            "title": f"{project_name} — mise à jour",
            "summary": "Généré automatiquement (LLM indisponible) depuis l'historique git brut.",
            "changelog": [line.strip() for line in log.splitlines() if line.strip()],
            "test_plan": [],
        }

    title = payload.get("title") or project_name
    summary = payload.get("summary") or ""
    changelog = payload.get("changelog") or []
    test_plan = payload.get("test_plan") or []
    if not isinstance(changelog, list):
        changelog = [str(changelog)]
    if not isinstance(test_plan, list):
        test_plan = [str(test_plan)]

    markdown = _render_markdown(title, summary, changelog, test_plan)
    pr_path = project_path / "PR_DESCRIPTION.md"
    pr_path.write_text(markdown, encoding="utf-8")

    logger.info("[devagent-pr] description générée : %s", pr_path)
    return {
        "ok": True, "title": title, "summary": summary,
        "changelog": changelog, "test_plan": test_plan, "path": str(pr_path),
    }


def _gh_available() -> bool:
    return shutil.which("gh") is not None


def open_pull_request(project_path: Path, title: str, body: str) -> dict:
    """Ouvre la PR sur GitHub si possible (best-effort), sinon instructions manuelles.

    Nécessite : CLI ``gh`` installée, authentifiée, et un remote git configuré
    sur le projet. Rien de tout cela n'est garanti pour un projet DevAgent
    (isolé par défaut) — c'est pourquoi ceci est une étape optionnelle
    distincte de la génération de description.
    """
    if not _gh_available():
        return {
            "ok": False, "opened": False,
            "reason": "CLI 'gh' introuvable — description générée dans PR_DESCRIPTION.md, "
                      "à publier manuellement (`gh pr create` une fois un remote configuré).",
        }

    remote = run_isolated(["git", "remote"], cwd=project_path, timeout=10)
    if not remote["stdout"].strip():
        return {
            "ok": False, "opened": False,
            "reason": "aucun remote git configuré sur ce projet — description disponible dans "
                      "PR_DESCRIPTION.md, à publier manuellement.",
        }

    try:
        result = subprocess.run(
            ["gh", "pr", "create", "--title", title, "--body", body],
            cwd=str(project_path), capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as e:
        return {"ok": False, "opened": False, "reason": f"échec appel gh : {e}"}

    if result.returncode != 0:
        return {"ok": False, "opened": False, "reason": result.stderr[:500] or result.stdout[:500]}

    url = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else None
    return {"ok": True, "opened": True, "url": url}
