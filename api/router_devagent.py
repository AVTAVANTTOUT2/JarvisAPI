"""Routes du cycle de développement autonome DevAgent."""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from agents.devagent import lock_spec, next_interview_step, run_loop, slugify, submit_answer
from agents.devagent.models import InterviewAnswer
from api.errors import api_error
from database import devagent as devagent_db

router = APIRouter()
logger = logging.getLogger("jarvis")


class DevAgentAutorunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)

    description: str = Field(min_length=1, max_length=50_000)
    name: str | None = Field(default=None, min_length=1, max_length=200)


@router.get("/api/devagent/{project_id}/deployments")
async def api_devagent_deployments(project_id: int):
    """Historique des déploiements staging du projet."""
    from database.devagent import get_deployments

    project = devagent_db.get_project(project_id)
    if not project:
        raise HTTPException(404, "Projet DevAgent introuvable.")
    return {"deployments": get_deployments(project_id)}


@router.post("/api/devagent/{project_id}/deploy")
async def api_devagent_deploy(project_id: int):
    """Déploie manuellement le commit HEAD en staging et valide avec la suite de tests."""
    from pathlib import Path

    from agents.devagent.staging import deploy_to_staging

    project = devagent_db.get_project(project_id)
    if not project:
        raise HTTPException(404, "Projet DevAgent introuvable.")
    return await asyncio.to_thread(deploy_to_staging, project_id, Path(project["isolation_path"]))


@router.post("/api/devagent/{project_id}/pr")
async def api_devagent_pr(project_id: int, open_pr: bool = False):
    """Génère description + changelog de PR ; ouvre la PR si `gh` + remote disponibles."""
    from pathlib import Path

    from agents.devagent.pr import generate_pr_description, open_pull_request

    project = devagent_db.get_project(project_id)
    if not project:
        raise HTTPException(404, "Projet DevAgent introuvable.")
    project_path = Path(project["isolation_path"])

    result = await generate_pr_description(project_path, project.get("name") or project["slug"])
    if not result.get("ok"):
        return result
    if open_pr:
        body = Path(result["path"]).read_text(encoding="utf-8")
        result["gh"] = await asyncio.to_thread(open_pull_request, project_path, result["title"], body)
    return result


@router.post("/api/devagent/{project_id}/rebase")
async def api_devagent_rebase(project_id: int, onto: str = "main"):
    """Rebase sûr : résout les conflits triviaux, abandonne sinon (jamais partiel)."""
    from pathlib import Path

    from agents.devagent.git_ops import safe_rebase

    project = devagent_db.get_project(project_id)
    if not project:
        raise HTTPException(404, "Projet DevAgent introuvable.")
    return await asyncio.to_thread(safe_rebase, Path(project["isolation_path"]), onto)


@router.post("/api/devagent/{project_id}/refactor")
async def api_devagent_refactor(project_id: int):
    """Refactore le plus gros bloc dupliqué du projet (tests-gated, réversible)."""
    from pathlib import Path

    from agents.devagent.refactor import refactor_top_duplicate

    project = devagent_db.get_project(project_id)
    if not project:
        raise HTTPException(404, "Projet DevAgent introuvable.")
    return await refactor_top_duplicate(Path(project["isolation_path"]))


# ── Exécution autonome ───────────────────────────────────────

@router.post("/api/devagent/autorun")
async def api_devagent_autorun(payload: DevAgentAutorunRequest):
    """Agent autonome bout en bout : interview auto-répondue → spec → boucle, zéro humain.

    Body : {"description": "...", "name": "..." (optionnel)}.
    """
    from agents.devagent.autorun import autorun_project

    try:
        return await autorun_project(payload.description, name=payload.name)
    except RuntimeError as e:
        logger.exception("[devagent/autorun] échec")
        raise api_error(502, "devagent_autorun_failed", "Démarrage autonome impossible") from e


@router.post("/api/devagent/start")
async def devagent_start(name: str):
    """Demarre un projet DevAgent et renvoie la premiere question d'interview."""
    if not name or not name.strip():
        raise HTTPException(400, "Le nom du projet est requis.")
    clean_name = name.strip()
    slug = slugify(clean_name)
    if devagent_db.get_project_by_slug(slug):
        raise HTTPException(409, f"Un projet avec le slug '{slug}' existe deja.")
    from agents.devagent.spec_builder import build_isolation_path

    isolation = str(build_isolation_path(slug))
    project_id = devagent_db.create_dev_project(slug, clean_name, isolation)
    first_question = await next_interview_step(project_id, {})
    return {"project_id": project_id, "first_question": first_question}


@router.post("/api/devagent/{project_id}/answer")
async def devagent_answer(project_id: int, payload: InterviewAnswer):
    """Soumet une reponse d'interview ; verrouille la spec si l'interview est terminee."""
    project = devagent_db.get_project(project_id)
    if not project:
        raise HTTPException(404, "Projet DevAgent introuvable.")
    if project.get("status") not in ("interviewing",):
        raise HTTPException(400, f"Interview deja terminee (status={project.get('status')}).")

    context = devagent_db.get_interview_context(project_id)
    result = await submit_answer(
        project_id, payload.question, payload.answer, context
    )

    if result.get("done"):
        spec_dict = result.get("spec")
        if not isinstance(spec_dict, dict):
            raise HTTPException(502, "Spec DeepSeek invalide.")
        spec = lock_spec(spec_dict)
        devagent_db.save_spec(project_id, spec.model_dump_json())
        devagent_db.complete_interview_session(project_id)
        devagent_db.save_interview_context(project_id, context)
        devagent_db.update_project_status(project_id, "spec_locked")
        return {"done": True, "spec": spec.model_dump()}

    devagent_db.save_interview_context(project_id, context)
    return {"done": False, "next_question": result}


@router.post("/api/devagent/{project_id}/run")
async def devagent_run(project_id: int, background_tasks: BackgroundTasks):
    """Lance la boucle autonome en arriere-plan."""
    project = devagent_db.get_project(project_id)
    if not project:
        raise HTTPException(404, "Projet DevAgent introuvable.")
    if project.get("status") not in ("spec_locked", "paused", "failed"):
        raise HTTPException(
            400,
            f"Impossible de lancer (status={project.get('status')}). Spec verrouillee requise.",
        )
    if not project.get("spec_json"):
        raise HTTPException(400, "Spec absente — terminez l'interview d'abord.")

    devagent_db.update_project_status(project_id, "running")
    background_tasks.add_task(run_loop, project_id)
    return {"status": "started"}


@router.get("/api/devagent/{project_id}/status")
async def devagent_status(project_id: int):
    """Etat du projet et de la boucle autonome."""
    payload = devagent_db.get_project_status_payload(project_id)
    if not payload:
        raise HTTPException(404, "Projet DevAgent introuvable.")
    return payload


@router.post("/api/devagent/{project_id}/pause")
async def devagent_pause(project_id: int):
    """Met en pause la boucle autonome."""
    project = devagent_db.get_project(project_id)
    if not project:
        raise HTTPException(404, "Projet DevAgent introuvable.")
    devagent_db.update_project_status(project_id, "paused")
    return {"status": "paused"}
