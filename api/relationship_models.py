"""Contrats d'entrée stricts du calendrier et des analyses relationnelles."""

from __future__ import annotations

from datetime import datetime

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator


class _StrictRelationshipRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)


class CalendarEventCreateRequest(_StrictRelationshipRequest):
    title: str = Field(
        min_length=1,
        max_length=500,
        validation_alias=AliasChoices("title", "summary"),
    )
    start: str = Field(min_length=1, max_length=100)
    end: str = Field(default="", max_length=100)
    calendar: str | None = Field(default=None, max_length=200)
    location: str = Field(default="", max_length=1_000)
    notes: str = Field(default="", max_length=20_000)

    @model_validator(mode="after")
    def validate_iso_interval(self) -> "CalendarEventCreateRequest":
        try:
            start = datetime.fromisoformat(self.start)
        except ValueError as exc:
            raise ValueError("start doit être une date ISO 8601") from exc

        if not self.end:
            return self

        try:
            end = datetime.fromisoformat(self.end)
        except ValueError as exc:
            raise ValueError("end doit être une date ISO 8601") from exc

        if (start.tzinfo is None) != (end.tzinfo is None):
            raise ValueError("start et end doivent utiliser le même type de timezone")
        if end <= start:
            raise ValueError("end doit être postérieur à start")
        return self


class AnalyzeContactRequest(_StrictRelationshipRequest):
    name: str = Field(min_length=1, max_length=200)
