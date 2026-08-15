"""Logique métier des routes Apple Shortcuts."""

from __future__ import annotations

import hmac
import logging
import math
import threading
import time
from collections import deque
from typing import Any

from fastapi import HTTPException, Request

import config
from database.apple_shortcuts import (
    delete_registered_shortcut,
    find_registered_shortcut,
    get_registered_shortcut,
    list_registered_shortcuts,
    list_shortcut_runs,
    record_shortcut_run,
    register_shortcut,
    update_registered_shortcut,
)
from integrations.apple_shortcuts import (
    AppleShortcutsError,
    consume_plan,
    create_plan,
    list_folders_async,
    list_installed_async,
    peek_plan,
    revoke_plan,
    run_shortcut_async,
    status as shortcuts_status,
)
from integrations.apple_shortcuts_recipes import get_recipe, list_recipes

logger = logging.getLogger("jarvis")

_ingest_rate_buckets: dict[str, deque[float]] = {}
_ingest_rate_lock = threading.Lock()


def _client_host(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def enforce_ingest_rate_limit(request: Request) -> None:
    limit = max(1, int(getattr(config, "APPLE_SHORTCUTS_INGEST_RATE_LIMIT", 30)))
    window = max(
        1.0,
        float(getattr(config, "APPLE_SHORTCUTS_INGEST_RATE_WINDOW_SECONDS", 60)),
    )
    client = _client_host(request)
    now = time.monotonic()
    cutoff = now - window
    with _ingest_rate_lock:
        bucket = _ingest_rate_buckets.setdefault(client, deque())
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()
        if len(bucket) >= limit:
            retry_after = max(1, math.ceil(window - (now - bucket[0])))
            raise HTTPException(
                429,
                "Trop de requêtes Shortcuts",
                headers={"Retry-After": str(retry_after)},
            )
        bucket.append(now)
        if len(_ingest_rate_buckets) > 1024:
            stale = [
                key
                for key, values in _ingest_rate_buckets.items()
                if not values or values[-1] <= cutoff
            ]
            for key in stale:
                _ingest_rate_buckets.pop(key, None)


def require_ingest_token(request: Request) -> None:
    expected = str(getattr(config, "APPLE_SHORTCUTS_INGEST_TOKEN", "") or "").strip()
    if not expected:
        raise HTTPException(503, "Authentification Shortcuts non configurée")
    authorization = request.headers.get("authorization", "")
    scheme, _, token = authorization.partition(" ")
    provided = token.strip() if scheme.lower() == "bearer" else ""
    header_token = request.headers.get("x-apple-shortcuts-token", "").strip()
    candidate = provided or header_token
    if not candidate or not hmac.compare_digest(candidate, expected):
        logger.warning(
            "Ingest Shortcuts refusé client=%s",
            _client_host(request),
        )
        raise HTTPException(401, "Jeton Shortcuts invalide")


def integration_status() -> dict[str, Any]:
    base = shortcuts_status()
    registry = list_registered_shortcuts()
    base["registry_count"] = len(registry)
    base["registry_enabled"] = sum(1 for row in registry if row["enabled"])
    base["ingest_configured"] = bool(
        str(getattr(config, "APPLE_SHORTCUTS_INGEST_TOKEN", "") or "").strip()
    )
    return base


async def installed_shortcuts(*, folder: str | None = None) -> dict[str, Any]:
    try:
        items = await list_installed_async(folder=folder)
        folders = await list_folders_async()
    except AppleShortcutsError as exc:
        raise HTTPException(400, exc.message) from exc
    registered = {
        row["name"].casefold(): row for row in list_registered_shortcuts()
    }
    enriched = []
    for item in items:
        name = item["name"]
        reg = registered.get(name.casefold())
        enriched.append(
            {
                "name": name,
                "registered": reg is not None,
                "registry_id": reg["id"] if reg else None,
                "enabled": reg["enabled"] if reg else False,
            }
        )
    return {"shortcuts": enriched, "folders": folders, "count": len(enriched)}


def registry_list(*, enabled_only: bool = False) -> dict[str, Any]:
    items = list_registered_shortcuts(enabled_only=enabled_only)
    return {"shortcuts": items, "count": len(items)}


def registry_create(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        row = register_shortcut(
            name=str(payload["name"]),
            alias=str(payload.get("alias") or ""),
            description=str(payload.get("description") or ""),
            allow_input=bool(payload.get("allow_input", False)),
            requires_confirmation=bool(payload.get("requires_confirmation", True)),
            enabled=bool(payload.get("enabled", True)),
            risk=str(payload.get("risk") or "medium"),
        )
    except ValueError as exc:
        code = str(exc)
        if code == "invalid_risk":
            raise HTTPException(400, "risk doit être low, medium ou high") from exc
        raise HTTPException(400, "Nom de raccourci requis") from exc
    return row


def registry_update(shortcut_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    try:
        row = update_registered_shortcut(
            shortcut_id,
            alias=payload.get("alias"),
            description=payload.get("description"),
            allow_input=payload.get("allow_input"),
            requires_confirmation=payload.get("requires_confirmation"),
            enabled=payload.get("enabled"),
            risk=payload.get("risk"),
        )
    except ValueError as exc:
        raise HTTPException(400, "risk doit être low, medium ou high") from exc
    if row is None:
        raise HTTPException(404, "Raccourci non enregistré")
    return row


def registry_delete(shortcut_id: int) -> dict[str, str]:
    if not delete_registered_shortcut(shortcut_id):
        raise HTTPException(404, "Raccourci non enregistré")
    return {"status": "deleted"}


def _resolve_target(
    *,
    name: str | None,
    alias: str | None,
    registry_id: int | None,
) -> dict[str, Any]:
    if registry_id is not None:
        row = get_registered_shortcut(int(registry_id))
        if row is None or not row["enabled"]:
            raise HTTPException(404, "Raccourci non enregistré ou désactivé")
        return row
    row = find_registered_shortcut(name=name, alias=alias, enabled_only=True)
    if row is None:
        raise HTTPException(
            404,
            "Raccourci inconnu du registre. Enregistre-le depuis /shortcuts "
            "ou POST /api/apple/shortcuts/registry.",
        )
    return row


async def prepare_run(payload: dict[str, Any]) -> dict[str, Any]:
    row = _resolve_target(
        name=payload.get("name"),
        alias=payload.get("alias"),
        registry_id=payload.get("registry_id"),
    )
    input_text = payload.get("input")
    if input_text is not None:
        input_text = str(input_text)
    try:
        plan = create_plan(
            shortcut_name=row["name"],
            registry_id=int(row["id"]),
            input_text=input_text,
            allow_input=bool(row["allow_input"]),
            risk=str(row["risk"]),
        )
    except AppleShortcutsError as exc:
        status_code = 503 if exc.code in {"disabled", "unavailable"} else 400
        raise HTTPException(status_code, exc.message) from exc
    return {
        "ok": True,
        "needs_confirmation": True,
        "message": (
            f"Raccourci « {row['name']} » prêt. Confirme pour l'exécuter."
        ),
        **plan.to_public_dict(),
    }


async def confirm_run(plan_id: str) -> dict[str, Any]:
    try:
        plan = consume_plan(plan_id)
    except AppleShortcutsError as exc:
        raise HTTPException(404, exc.message) from exc
    try:
        result = await run_shortcut_async(
            plan.shortcut_name,
            input_text=plan.input_text,
        )
    except AppleShortcutsError as exc:
        record_shortcut_run(
            registry_id=plan.registry_id,
            shortcut_name=plan.shortcut_name,
            ok=False,
            input_preview=plan.input_text,
            output_preview=None,
            error=exc.message,
            plan_id=plan.plan_id,
        )
        raise HTTPException(400, exc.message) from exc
    record_shortcut_run(
        registry_id=plan.registry_id,
        shortcut_name=plan.shortcut_name,
        ok=True,
        input_preview=plan.input_text,
        output_preview=result.get("output"),
        error=None,
        plan_id=plan.plan_id,
    )
    return result


def cancel_run(plan_id: str) -> dict[str, Any]:
    revoked = revoke_plan(plan_id)
    if not revoked and peek_plan(plan_id) is None:
        raise HTTPException(404, "Plan introuvable")
    return {"ok": True, "revoked": revoked}


def recipes_payload() -> dict[str, Any]:
    items = list_recipes()
    return {"recipes": items, "count": len(items)}


def recipe_payload(recipe_id: str) -> dict[str, Any]:
    recipe = get_recipe(recipe_id)
    if recipe is None:
        raise HTTPException(404, "Recette inconnue")
    return recipe


def runs_payload(*, limit: int = 20) -> dict[str, Any]:
    items = list_shortcut_runs(limit=limit)
    return {"runs": items, "count": len(items)}


async def ask_jarvis(text: str, *, source: str = "shortcut") -> dict[str, Any]:
    """Ingestion Shortcuts → pipeline conversationnel unifié."""
    cleaned = (text or "").strip()
    if not cleaned:
        raise HTTPException(400, "Texte manquant")
    if len(cleaned) > 4000:
        raise HTTPException(400, "Texte trop long (max 4000)")
    from database import create_conversation
    from api.chat_processing import _process_message_internal

    conversation_id = create_conversation(agent="info")
    prefixed = f"[SHORTCUT:{source}]\n{cleaned}"
    result = await _process_message_internal(prefixed, conversation_id)
    return {
        "ok": True,
        "conversation_id": conversation_id,
        "reply": result.get("text") or result.get("response") or "",
        "agent": result.get("agent"),
    }


def create_quick_task(
    *,
    title: str,
    priority: str = "medium",
    category: str = "shortcut",
) -> dict[str, Any]:
    cleaned = (title or "").strip()
    if not cleaned:
        raise HTTPException(400, "Titre manquant")
    if len(cleaned) > 200:
        raise HTTPException(400, "Titre trop long")
    prio = (priority or "medium").strip().lower()
    if prio not in {"high", "medium", "low"}:
        raise HTTPException(400, "priority invalide")
    from database import create_task

    task_id = create_task(
        title=cleaned,
        priority=prio,
        category=(category or "shortcut")[:40],
    )
    return {
        "ok": True,
        "task_id": task_id,
        "title": cleaned,
        "priority": prio,
        "message": f"Tâche créée : {cleaned}",
    }
