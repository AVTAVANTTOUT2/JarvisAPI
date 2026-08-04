"""Contrats d'entrée stricts des endpoints Insights."""

from __future__ import annotations

from datetime import date as Date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class _StrictInsightRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)


class JarvisJournalGenerateRequest(_StrictInsightRequest):
    date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")

    @field_validator("date")
    @classmethod
    def validate_calendar_date(cls, value: str | None) -> str | None:
        if value is not None:
            Date.fromisoformat(value)
        return value


class SelfHealingDiagnoseRequest(_StrictInsightRequest):
    log_tail: str = Field(min_length=1, max_length=200_000)


class CommitmentStatusRequest(_StrictInsightRequest):
    status: Literal["open", "kept", "dropped"]
