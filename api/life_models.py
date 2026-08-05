"""Contrats d'entrée stricts du profil de vie et du journal."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _StrictLifeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)


class LifeProfileCreateRequest(_StrictLifeRequest):
    category: Literal["values", "goals", "fears", "patterns", "strengths"]
    content: str = Field(min_length=1, max_length=10_000)


class LifeProfileUpdateRequest(_StrictLifeRequest):
    content: str = Field(min_length=1, max_length=10_000)


class LifeContextCreateRequest(_StrictLifeRequest):
    context_type: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=10_000)
    period_start: str | None = Field(default=None, max_length=100)
    period_end: str | None = Field(default=None, max_length=100)
    impact_on_mood: str | None = Field(default=None, max_length=2_000)
    impact_on_productivity: str | None = Field(default=None, max_length=2_000)


class JournalEntryRequest(_StrictLifeRequest):
    content: str = Field(min_length=1, max_length=50_000)
