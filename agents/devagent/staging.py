"""Auto-déploiement staging pour un projet DevAgent après des tests verts.

Un projet DevAgent est arbitraire (API, CLI, script, intégration…) — il n'y a
pas de notion générique de « démarrer le service ». Le déploiement staging se
limite donc à ce qui est vérifiable de façon universelle et sûre :

1. Archiver le commit HEAD (``git archive``) dans un dossier staging séparé
   (``dev_projects/{slug}_staging/``) — jamais un simple ``cp`` du répertoire
   de travail, pour ne déployer QUE ce qui est commité.
2. Réinstaller l'environnement (venv + dépendances si ``requirements.txt``
   existe).
3. Relancer la suite de tests **dans la copie staging** — c'est la
   vérification que le déploiement est fonctionnellement identique à ce qui
   vient d'être validé, pas juste une copie de fichiers.
4. Enregistrer le résultat (``dev_deployments``) et notifier.

Un déploiement staging raté ne bloque jamais la boucle DevAgent (l'itération
elle-même reste un succès) — c'est un filet de sécurité en plus, pas une
porte de sortie.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from agents.devagent.executor import ExecutionTimeout, git_current_sha, run_isolated, setup_venv
from database import create_notification
from database.devagent import record_deployment

logger = logging.getLogger(__name__)


def _staging_path(project_path: Path) -> Path:
    return project_path.parent / f"{project_path.name}_staging"


def deploy_to_staging(
    project_id: int,
    project_path: Path,
    test_command: str = "python3 -m pytest -q",
) -> dict:
    """Déploie le commit HEAD dans une copie staging isolée et la valide.

    Retourne ``{ok, staging_path, commit_sha, log}``. N'échoue jamais
    bruyamment — les erreurs sont capturées dans le rapport.
    """
    commit_sha = git_current_sha(project_path)
    staging = _staging_path(project_path)

    try:
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(parents=True)

        archive_cmd = ["git", "archive", "HEAD"]
        archive_result = run_isolated(archive_cmd + ["--format=tar", "-o", str(staging / "_archive.tar")],
                                      cwd=project_path, timeout=30)
        if archive_result["returncode"] != 0:
            reason = f"git archive échoué : {archive_result['stderr'][:300]}"
            record_deployment(project_id, commit_sha, "failed", str(staging), reason)
            return {"ok": False, "staging_path": str(staging), "commit_sha": commit_sha, "log": reason}

        extract = run_isolated(["tar", "-xf", "_archive.tar"], cwd=staging, timeout=30)
        (staging / "_archive.tar").unlink(missing_ok=True)
        if extract["returncode"] != 0:
            reason = f"extraction archive échouée : {extract['stderr'][:300]}"
            record_deployment(project_id, commit_sha, "failed", str(staging), reason)
            return {"ok": False, "staging_path": str(staging), "commit_sha": commit_sha, "log": reason}

        setup_venv(staging, timeout=120)
        req = staging / "requirements.txt"
        if req.is_file():
            pip = staging / "venv" / "bin" / "pip"
            run_isolated([str(pip), "install", "-q", "-r", "requirements.txt"], cwd=staging, timeout=180)

        try:
            test_result = run_isolated(test_command, cwd=staging, timeout=120)
        except ExecutionTimeout as e:
            record_deployment(project_id, commit_sha, "failed", str(staging), str(e))
            return {"ok": False, "staging_path": str(staging), "commit_sha": commit_sha, "log": str(e)}

        ok = test_result["returncode"] == 0
        log = test_result["stdout"][-1000:] if ok else (test_result["stderr"] or test_result["stdout"])[-1000:]
        record_deployment(project_id, commit_sha, "success" if ok else "failed", str(staging), log)

        title = "Déploiement staging réussi" if ok else "Déploiement staging échoué"
        create_notification(
            source="devagent", title=title,
            content=f"Commit {commit_sha[:8] if commit_sha else '?'} — {staging.name}",
            priority="low" if ok else "medium",
        )
        logger.info("[devagent-staging] %s (%s)", title, staging)
        return {"ok": ok, "staging_path": str(staging), "commit_sha": commit_sha, "log": log}

    except Exception as e:
        logger.exception("[devagent-staging] erreur inattendue : %s", e)
        record_deployment(project_id, commit_sha, "failed", str(staging), str(e))
        return {"ok": False, "staging_path": str(staging), "commit_sha": commit_sha, "log": str(e)}
