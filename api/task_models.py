"""Contrats d'entrée stricts des mutations du domaine Tasks."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _StrictTaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)


class TaskCreateRequest(_StrictTaskRequest):
    title: str = Field(min_length=1, max_length=500)
    description: str | None = Field(default=None, max_length=10_000)
    priority: Literal["high", "medium", "low"] = "medium"
    due_date: str | None = Field(default=None, max_length=100)
    category: str | None = Field(default=None, max_length=200)


class TaskStatusRequest(_StrictTaskRequest):
    status: Literal["todo", "doing", "done"]
