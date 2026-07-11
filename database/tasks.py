"""Opérations de persistance du domaine des tâches."""

from __future__ import annotations

from .core import get_db


def get_tasks(status: str | None = None) -> list[dict]:
    """Liste les tâches actives, toutes les tâches ou celles d'un statut donné."""
    priority_case = "CASE priority WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END"
    with get_db() as conn:
        if status == "all":
            rows = conn.execute(
                f"SELECT * FROM tasks ORDER BY {priority_case}, due_date IS NULL, due_date"
            ).fetchall()
        elif status:
            rows = conn.execute(
                f"SELECT * FROM tasks WHERE status = ? ORDER BY {priority_case}, due_date IS NULL, due_date",
                (status,),
            ).fetchall()
        else:
            rows = conn.execute(
                f"SELECT * FROM tasks WHERE status != 'done' "
                f"ORDER BY {priority_case}, due_date IS NULL, due_date"
            ).fetchall()
    return [dict(row) for row in rows]


def create_task(
    title: str,
    description: str | None = None,
    priority: str = "medium",
    due_date: str | None = None,
    category: str | None = None,
) -> int:
    """Crée une tâche et retourne son identifiant."""
    if priority not in ("high", "medium", "low"):
        priority = "medium"
    with get_db() as conn:
        cursor = conn.execute(
            """INSERT INTO tasks (title, description, priority, due_date, category)
               VALUES (?, ?, ?, ?, ?)""",
            (title, description, priority, due_date, category),
        )
        return int(cursor.lastrowid)


def update_task_status(task_id: int, status: str) -> bool:
    """Met à jour le statut et maintient `completed_at` de façon cohérente."""
    if status not in ("todo", "doing", "done"):
        return False
    with get_db() as conn:
        if status == "done":
            cursor = conn.execute(
                "UPDATE tasks SET status = ?, completed_at = CURRENT_TIMESTAMP WHERE id = ?",
                (status, task_id),
            )
        else:
            cursor = conn.execute(
                "UPDATE tasks SET status = ?, completed_at = NULL WHERE id = ?",
                (status, task_id),
            )
    return cursor.rowcount > 0


def get_task(task_id: int) -> dict | None:
    """Retourne une tâche par identifiant."""
    with get_db() as conn:
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    return dict(row) if row else None


def delete_task(task_id: int) -> bool:
    """Supprime une tâche et indique si une ligne existait."""
    with get_db() as conn:
        cursor = conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    return cursor.rowcount > 0


def delete_all_tasks() -> int:
    """Supprime toutes les tâches et retourne le nombre de lignes supprimées."""
    with get_db() as conn:
        cursor = conn.execute("DELETE FROM tasks")
    return cursor.rowcount
