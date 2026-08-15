"""Registre SQLite des raccourcis Apple autorisés et journal d'exécution."""

from __future__ import annotations

import sqlite3
from typing import Any

from .core import get_db

ALLOWED_RISKS = frozenset({"low", "medium", "high"})


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "name": row["name"],
        "alias": row["alias"] or "",
        "description": row["description"] or "",
        "allow_input": bool(row["allow_input"]),
        "requires_confirmation": bool(row["requires_confirmation"]),
        "enabled": bool(row["enabled"]),
        "risk": row["risk"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "last_run_at": row["last_run_at"],
        "run_count": int(row["run_count"] or 0),
    }


def list_registered_shortcuts(*, enabled_only: bool = False) -> list[dict[str, Any]]:
    sql = "SELECT * FROM apple_shortcut_registry"
    if enabled_only:
        sql += " WHERE enabled = 1"
    sql += " ORDER BY lower(COALESCE(alias, name)), lower(name)"
    with get_db() as conn:
        rows = conn.execute(sql).fetchall()
    return [_row_to_dict(row) for row in rows]


def get_registered_shortcut(shortcut_id: int) -> dict[str, Any] | None:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM apple_shortcut_registry WHERE id = ?",
            (int(shortcut_id),),
        ).fetchone()
    return _row_to_dict(row) if row else None


def find_registered_shortcut(
    *,
    name: str | None = None,
    alias: str | None = None,
    enabled_only: bool = True,
) -> dict[str, Any] | None:
    """Résout un raccourci par nom exact ou alias (insensible à la casse)."""
    needle_name = (name or "").strip()
    needle_alias = (alias or "").strip()
    if not needle_name and not needle_alias:
        return None
    clauses: list[str] = []
    params: list[Any] = []
    if needle_name:
        clauses.append("lower(name) = lower(?)")
        params.append(needle_name)
    if needle_alias:
        clauses.append("lower(alias) = lower(?)")
        params.append(needle_alias)
    where = " OR ".join(clauses)
    if enabled_only:
        where = f"({where}) AND enabled = 1"
    with get_db() as conn:
        row = conn.execute(
            f"SELECT * FROM apple_shortcut_registry WHERE {where} LIMIT 1",
            params,
        ).fetchone()
    return _row_to_dict(row) if row else None


def register_shortcut(
    *,
    name: str,
    alias: str = "",
    description: str = "",
    allow_input: bool = False,
    requires_confirmation: bool = True,
    enabled: bool = True,
    risk: str = "medium",
) -> dict[str, Any]:
    cleaned_name = name.strip()
    if not cleaned_name:
        raise ValueError("name_required")
    cleaned_risk = (risk or "medium").strip().lower()
    if cleaned_risk not in ALLOWED_RISKS:
        raise ValueError("invalid_risk")
    cleaned_alias = (alias or "").strip()
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO apple_shortcut_registry (
                name, alias, description, allow_input, requires_confirmation,
                enabled, risk, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(name) DO UPDATE SET
                alias = excluded.alias,
                description = excluded.description,
                allow_input = excluded.allow_input,
                requires_confirmation = excluded.requires_confirmation,
                enabled = excluded.enabled,
                risk = excluded.risk,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                cleaned_name,
                cleaned_alias,
                (description or "").strip(),
                1 if allow_input else 0,
                1 if requires_confirmation else 0,
                1 if enabled else 0,
                cleaned_risk,
            ),
        )
        row = conn.execute(
            "SELECT * FROM apple_shortcut_registry WHERE lower(name) = lower(?)",
            (cleaned_name,),
        ).fetchone()
    assert row is not None
    return _row_to_dict(row)


def update_registered_shortcut(
    shortcut_id: int,
    *,
    alias: str | None = None,
    description: str | None = None,
    allow_input: bool | None = None,
    requires_confirmation: bool | None = None,
    enabled: bool | None = None,
    risk: str | None = None,
) -> dict[str, Any] | None:
    current = get_registered_shortcut(shortcut_id)
    if current is None:
        return None
    new_alias = current["alias"] if alias is None else alias.strip()
    new_description = (
        current["description"] if description is None else description.strip()
    )
    new_allow = current["allow_input"] if allow_input is None else bool(allow_input)
    new_confirm = (
        current["requires_confirmation"]
        if requires_confirmation is None
        else bool(requires_confirmation)
    )
    new_enabled = current["enabled"] if enabled is None else bool(enabled)
    new_risk = current["risk"] if risk is None else risk.strip().lower()
    if new_risk not in ALLOWED_RISKS:
        raise ValueError("invalid_risk")
    with get_db() as conn:
        conn.execute(
            """
            UPDATE apple_shortcut_registry
            SET alias = ?, description = ?, allow_input = ?,
                requires_confirmation = ?, enabled = ?, risk = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                new_alias,
                new_description,
                1 if new_allow else 0,
                1 if new_confirm else 0,
                1 if new_enabled else 0,
                new_risk,
                int(shortcut_id),
            ),
        )
    return get_registered_shortcut(shortcut_id)


def delete_registered_shortcut(shortcut_id: int) -> bool:
    with get_db() as conn:
        cursor = conn.execute(
            "DELETE FROM apple_shortcut_registry WHERE id = ?",
            (int(shortcut_id),),
        )
        return cursor.rowcount > 0


def record_shortcut_run(
    *,
    registry_id: int | None,
    shortcut_name: str,
    ok: bool,
    input_preview: str | None,
    output_preview: str | None,
    error: str | None,
    plan_id: str | None,
) -> int:
    with get_db() as conn:
        cursor = conn.execute(
            """
            INSERT INTO apple_shortcut_runs (
                registry_id, shortcut_name, ok, input_preview,
                output_preview, error, plan_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                registry_id,
                shortcut_name,
                1 if ok else 0,
                (input_preview or "")[:120] or None,
                (output_preview or "")[:500] or None,
                (error or "")[:500] or None,
                plan_id,
            ),
        )
        run_id = int(cursor.lastrowid)
        if registry_id is not None and ok:
            conn.execute(
                """
                UPDATE apple_shortcut_registry
                SET run_count = run_count + 1,
                    last_run_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (int(registry_id),),
            )
    return run_id


def list_shortcut_runs(*, limit: int = 20) -> list[dict[str, Any]]:
    capped = max(1, min(int(limit), 100))
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT id, registry_id, shortcut_name, ok, input_preview,
                   output_preview, error, plan_id, created_at
            FROM apple_shortcut_runs
            ORDER BY id DESC
            LIMIT ?
            """,
            (capped,),
        ).fetchall()
    return [
        {
            "id": int(row["id"]),
            "registry_id": row["registry_id"],
            "shortcut_name": row["shortcut_name"],
            "ok": bool(row["ok"]),
            "input_preview": row["input_preview"],
            "output_preview": row["output_preview"],
            "error": row["error"],
            "plan_id": row["plan_id"],
            "created_at": row["created_at"],
        }
        for row in rows
    ]
