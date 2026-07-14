"""Routes de gestion des tâches."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from database import (
    create_task,
    delete_all_tasks,
    delete_task,
    get_task,
    get_tasks,
    update_task_status,
)

router = APIRouter()
logger = logging.getLogger("jarvis")


@router.get("/api/tasks")
async def api_tasks_list(status: str | None = None):
    """Liste les tâches. Filtre optionnel : `all`, `todo`, `doing`, `done`.
    Sans filtre = todo + doing (pas les `done`).
    """
    if status and status not in ("all", "todo", "doing", "done"):
        raise HTTPException(400, "`status` invalide. Valeurs acceptées : all, todo, doing, done")
    return {"tasks": get_tasks(status=status)}


@router.post("/api/tasks")
async def api_tasks_create(payload: dict):
    """Crée une tâche.

    Body JSON : `{title, description?, priority?, due_date?, category?}`.
    """
    title = (payload.get("title") or "").strip()
    if not title:
        raise HTTPException(400, "`title` requis")

    try:
        task_id = create_task(
            title=title,
            description=payload.get("description"),
            priority=payload.get("priority", "medium"),
            due_date=payload.get("due_date"),
            category=payload.get("category"),
        )
    except Exception as e:
        logger.error(f"Erreur create_task : {e}")
        raise HTTPException(500, str(e))

    return {"task": get_task(task_id)}


@router.patch("/api/tasks/{task_id}")
async def api_tasks_update(task_id: int, payload: dict):
    """Met à jour le status d'une tâche (`todo` → `doing` → `done`)."""
    status = (payload.get("status") or "").strip().lower()
    if status not in ("todo", "doing", "done"):
        raise HTTPException(400, "`status` doit être todo / doing / done")

    if not update_task_status(task_id, status):
        raise HTTPException(404, "Tâche introuvable")

    return {"task": get_task(task_id)}


@router.delete("/api/tasks/{task_id}")
async def api_tasks_delete(task_id: int):
    """Supprime une tâche individuelle."""
    if not delete_task(task_id):
        raise HTTPException(404, "Tâche introuvable")
    return {"ok": True, "deleted_id": task_id}


@router.delete("/api/tasks")
async def api_tasks_delete_all():
    """Supprime TOUTES les tâches (tous statuts confondus)."""
    deleted_count = delete_all_tasks()
    logger.info(f"[tasks] {deleted_count} tâche(s) supprimée(s) — purge totale")
    return {"ok": True, "deleted_count": deleted_count}
