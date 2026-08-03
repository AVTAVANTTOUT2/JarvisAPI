"""Contrats d'entrée stricts des mutations de lieux."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, model_validator


class _StrictLocationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)


class PlaceCreateRequest(_StrictLocationRequest):
    name: str = Field(min_length=1, max_length=200)
    category: str = Field(default="other", min_length=1, max_length=80)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    radius: float | None = Field(default=None, gt=0, le=100_000)
    address: str | None = Field(default=None, max_length=1_000)
    notes: str | None = Field(default=None, max_length=8_000)


class PlaceUpdateRequest(_StrictLocationRequest):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    category: str | None = Field(default=None, min_length=1, max_length=80)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    radius: float | None = Field(default=None, gt=0, le=100_000)
    radius_meters: float | None = Field(default=None, gt=0, le=100_000)
    address: str | None = Field(default=None, max_length=1_000)
    notes: str | None = Field(default=None, max_length=8_000)

    @model_validator(mode="after")
    def validate_update(self) -> "PlaceUpdateRequest":
        provided = self.model_fields_set
        if not provided:
            raise ValueError("Au moins un champ modifiable est requis")
        if {"radius", "radius_meters"}.issubset(provided):
            raise ValueError("radius et radius_meters ne peuvent pas être fournis ensemble")
        if any(
            field in provided and getattr(self, field) is None
            for field in ("name", "category", "latitude", "longitude")
        ):
            raise ValueError("Les champs structurants d'un lieu ne peuvent pas être nuls")
        return self


class NameCurrentLocationRequest(_StrictLocationRequest):
    name: str = Field(min_length=1, max_length=200)
    category: str = Field(default="other", min_length=1, max_length=80)


class LocationPointRequest(_StrictLocationRequest):
    """Point unitaire strict ; les alias temporels restent compatibles."""

    latitude: FiniteFloat = Field(ge=-90, le=90)
    longitude: FiniteFloat = Field(ge=-180, le=180)
    altitude: FiniteFloat | None = None
    accuracy: FiniteFloat | None = Field(default=None, ge=0)
    speed: FiniteFloat | None = Field(default=None, ge=0)
    heading: FiniteFloat | None = Field(default=None, ge=0, le=360)
    bearing: FiniteFloat | None = Field(default=None, ge=0, le=360)
    source: str = Field(default="app", min_length=1, max_length=80)
    provider: str | None = Field(default=None, max_length=80)
    captured_at: int | float | str | None = None
    timestamp: int | float | str | None = None
    created_at: int | float | str | None = None
    point_time: int | float | str | None = None


class LocationBatchRequest(_StrictLocationRequest):
    """Enveloppe stricte ; chaque point garde le contrat de rejet partiel.

    Les points sont volontairement validés individuellement dans le routeur :
    un élément périmé ne doit pas faire rejouer les autres points offline.
    """

    points: list[Any] = Field(max_length=1_000)
