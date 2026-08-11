"""Contrats d'entrée stricts des routes d'authentification."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _StrictAuthRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)


class SecretRequest(_StrictAuthRequest):
    secret: str = Field(min_length=1, max_length=1_024)


class ChangeSecretRequest(_StrictAuthRequest):
    current: str = Field(min_length=1, max_length=1_024)
    new: str = Field(min_length=1, max_length=1_024)


class ProfileCreateRequest(_StrictAuthRequest):
    display_name: str = Field(min_length=1, max_length=80)


class MobilePairingCompleteRequest(_StrictAuthRequest):
    code: str = Field(pattern=r"^\d{6}$")
    device_id: str = Field(min_length=1, max_length=128)
    name: str = Field(default="Samsung Galaxy", min_length=1, max_length=120)
    model: str = Field(default="", max_length=120)
    app_version: str = Field(default="", max_length=40)


class MobilePushTokenRequest(_StrictAuthRequest):
    token: str = Field(min_length=1, max_length=4_096)


class MobileCapabilitiesRequest(_StrictAuthRequest):
    push: bool | None = None
    background_location: bool | None = None
    wake_word: bool | None = None

    @model_validator(mode="after")
    def require_capability(self) -> "MobileCapabilitiesRequest":
        if not any(
            value is not None
            for value in (self.push, self.background_location, self.wake_word)
        ):
            raise ValueError("Au moins une capacité est requise")
        return self
