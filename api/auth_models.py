"""Contrats d'entrée stricts des routes d'authentification."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class _StrictAuthRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)


class SecretRequest(_StrictAuthRequest):
    secret: str = Field(min_length=1, max_length=1_024)


class ChangeSecretRequest(_StrictAuthRequest):
    current: str = Field(min_length=1, max_length=1_024)
    new: str = Field(min_length=1, max_length=1_024)
