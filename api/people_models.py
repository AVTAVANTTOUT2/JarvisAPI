"""Contrats d'entrée stricts pour les mutations du domaine People."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class _StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)


class PersonPatchRequest(_StrictRequest):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    relationship: str | None = Field(default=None, max_length=500)
    personality_notes: str | None = Field(default=None, max_length=8_000)
    dynamics: str | None = Field(default=None, max_length=8_000)
    patterns: str | None = Field(default=None, max_length=8_000)
    birthday: str | None = Field(default=None, max_length=40)


class PersonCreateRequest(_StrictRequest):
    name: str = Field(min_length=1, max_length=200)
    relationship: str | None = Field(default=None, max_length=500)
    personality_notes: str | None = Field(default=None, max_length=8_000)
    dynamics: str | None = Field(default=None, max_length=8_000)
    patterns: str | None = Field(default=None, max_length=8_000)


class PersonQuestionRequest(_StrictRequest):
    question: str = Field(min_length=1, max_length=8_000)


class PersonMessageRequest(_StrictRequest):
    text: str = Field(min_length=1, max_length=8_000)


class PersonReminderRequest(_StrictRequest):
    when: str = Field(default="bientôt", min_length=1, max_length=500)
