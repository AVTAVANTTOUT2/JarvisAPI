"""Contrats stricts pour le pairage et les captures des appareils distants."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class _StrictDeviceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)


class DeviceRegistrationRequest(_StrictDeviceRequest):
    device_id: str = Field(min_length=1, max_length=128)
    device_name: str | None = Field(default=None, max_length=120)
    device_type: str = Field(default="desktop", min_length=1, max_length=40)
    ip_tailscale: str | None = Field(default=None, max_length=64)
    pairing_code: str = Field(min_length=1, max_length=128)


class RemoteScreenRequest(_StrictDeviceRequest):
    image_b64: str = Field(min_length=1)
    app: str = Field(default="unknown", min_length=1, max_length=200)
    change_pct: float = Field(default=0.0, ge=0.0, le=100.0, allow_inf_nan=False)
