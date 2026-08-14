"""Routes Apple Shortcuts — registre, exécution confirmée, recettes, ingest."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Path, Query, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator

from api.apple_shortcuts_support import (
    ask_jarvis,
    cancel_run,
    confirm_run,
    create_quick_task,
    enforce_ingest_rate_limit,
    installed_shortcuts,
    integration_status,
    prepare_run,
    recipe_payload,
    recipes_payload,
    registry_create,
    registry_delete,
    registry_list,
    registry_update,
    require_ingest_token,
    runs_payload,
)

router = APIRouter(tags=["apple-shortcuts"])

RiskLevel = Literal["low", "medium", "high"]
PriorityLevel = Literal["high", "medium", "low"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)


class RegistryCreateRequest(_Strict):
    name: str = Field(min_length=1, max_length=120)
    alias: str = Field(default="", max_length=120)
    description: str = Field(default="", max_length=500)
    allow_input: bool = False
    requires_confirmation: bool = True
    enabled: bool = True
    risk: RiskLevel = "medium"


class RegistryUpdateRequest(_Strict):
    alias: str | None = Field(default=None, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    allow_input: bool | None = None
    requires_confirmation: bool | None = None
    enabled: bool | None = None
    risk: RiskLevel | None = None

    @model_validator(mode="after")
    def require_field(self) -> RegistryUpdateRequest:
        if not self.model_fields_set:
            raise ValueError("Au moins un champ est requis")
        return self


class PrepareRunRequest(_Strict):
    name: str | None = Field(default=None, max_length=120)
    alias: str | None = Field(default=None, max_length=120)
    registry_id: int | None = Field(default=None, ge=1)
    input: str | None = Field(default=None, max_length=8000)

    @model_validator(mode="after")
    def require_target(self) -> PrepareRunRequest:
        if not self.name and not self.alias and self.registry_id is None:
            raise ValueError("name, alias ou registry_id requis")
        return self


class AskRequest(_Strict):
    text: str = Field(min_length=1, max_length=4000)
    source: str = Field(default="shortcut", max_length=40)


class TaskRequest(_Strict):
    title: str = Field(min_length=1, max_length=200)
    priority: PriorityLevel = "medium"
    category: str = Field(default="shortcut", max_length=40)
    source: str = Field(default="shortcut", max_length=40)


@router.get("/api/apple/shortcuts/status")
async def api_apple_shortcuts_status() -> dict[str, Any]:
    return integration_status()


@router.get("/api/apple/shortcuts/installed")
async def api_apple_shortcuts_installed(
    folder: Annotated[str | None, Query(max_length=120)] = None,
) -> dict[str, Any]:
    return await installed_shortcuts(folder=folder)


@router.get("/api/apple/shortcuts/registry")
async def api_apple_shortcuts_registry(
    enabled_only: Annotated[bool, Query()] = False,
) -> dict[str, Any]:
    return registry_list(enabled_only=enabled_only)


@router.post("/api/apple/shortcuts/registry", status_code=201)
async def api_apple_shortcuts_registry_create(
    body: RegistryCreateRequest,
) -> dict[str, Any]:
    return registry_create(body.model_dump())


@router.patch("/api/apple/shortcuts/registry/{shortcut_id}")
async def api_apple_shortcuts_registry_update(
    shortcut_id: Annotated[int, Path(ge=1)],
    body: RegistryUpdateRequest,
) -> dict[str, Any]:
    return registry_update(shortcut_id, body.model_dump(exclude_unset=True))


@router.delete("/api/apple/shortcuts/registry/{shortcut_id}")
async def api_apple_shortcuts_registry_delete(
    shortcut_id: Annotated[int, Path(ge=1)],
) -> dict[str, str]:
    return registry_delete(shortcut_id)


@router.post("/api/apple/shortcuts/prepare")
async def api_apple_shortcuts_prepare(body: PrepareRunRequest) -> dict[str, Any]:
    return await prepare_run(body.model_dump())


@router.get("/api/apple/shortcuts/runs")
async def api_apple_shortcuts_runs(
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict[str, Any]:
    return runs_payload(limit=limit)


@router.get("/api/apple/shortcuts/recipes")
async def api_apple_shortcuts_recipes() -> dict[str, Any]:
    return recipes_payload()


@router.get("/api/apple/shortcuts/recipes/{recipe_id}")
async def api_apple_shortcuts_recipe(
    recipe_id: Annotated[str, Path(min_length=1, max_length=80)],
) -> dict[str, Any]:
    return recipe_payload(recipe_id)


@router.post("/api/apple/shortcuts/ask")
async def api_apple_shortcuts_ask(
    request: Request,
    body: AskRequest,
) -> dict[str, Any]:
    enforce_ingest_rate_limit(request)
    require_ingest_token(request)
    return await ask_jarvis(body.text, source=body.source)


@router.post("/api/apple/shortcuts/task")
async def api_apple_shortcuts_task(
    request: Request,
    body: TaskRequest,
) -> dict[str, Any]:
    enforce_ingest_rate_limit(request)
    require_ingest_token(request)
    return create_quick_task(
        title=body.title,
        priority=body.priority,
        category=body.category,
    )


@router.post("/api/apple/shortcuts/{plan_id}/confirm")
async def api_apple_shortcuts_confirm(
    plan_id: Annotated[str, Path(min_length=8, max_length=128)],
) -> dict[str, Any]:
    return await confirm_run(plan_id)


@router.delete("/api/apple/shortcuts/{plan_id}")
async def api_apple_shortcuts_cancel(
    plan_id: Annotated[str, Path(min_length=8, max_length=128)],
) -> dict[str, Any]:
    return cancel_run(plan_id)
