"""Contexte client borné partagé par les transports WebSocket et voix."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Mapping

from jarvis.agentic.models import normalize_agentic_client_context


@dataclass(frozen=True)
class AgenticClientContext:
    device: str | None = None
    locale: str = "fr-FR"
    timezone_name: str = "Europe/Paris"
    device_locked: bool = False

    @classmethod
    def from_values(
        cls,
        *,
        device: object = None,
        locale: object = None,
        timezone_name: object = None,
        device_locked: bool = False,
    ) -> AgenticClientContext:
        normalized = normalize_agentic_client_context(
            device=device,
            locale=locale,
            timezone_name=timezone_name,
        )
        return cls(*normalized, device_locked=device_locked)

    def for_message(self, payload: Mapping[str, Any]) -> AgenticClientContext:
        device = (
            self.device if self.device_locked else payload.get("device", self.device)
        )
        return self.from_values(
            device=device,
            locale=payload.get("locale", self.locale),
            timezone_name=payload.get("timezone", self.timezone_name),
            device_locked=self.device_locked,
        )

    def agentic_kwargs(self) -> dict[str, str | None]:
        return {
            "device": self.device,
            "locale": self.locale,
            "timezone_name": self.timezone_name,
        }

    def voice_kwargs(self) -> dict[str, str | None]:
        return {
            "agentic_device": self.device,
            "agentic_locale": self.locale,
            "agentic_timezone": self.timezone_name,
        }


def parse_websocket_client_message(
    raw: str,
    context: AgenticClientContext,
) -> tuple[Mapping[str, Any], str, str | None, AgenticClientContext]:
    payload = json.loads(raw)
    if not isinstance(payload, Mapping):
        raise ValueError("le message WebSocket doit être un objet JSON")
    message_type = str(payload.get("type", "text"))
    client_message_id = (
        str(payload["client_message_id"]) if payload.get("client_message_id") else None
    )
    return payload, message_type, client_message_id, context.for_message(payload)


def websocket_client_context(
    ws: Any,
    mobile_device: Mapping[str, Any] | None,
) -> AgenticClientContext:
    trusted_device = mobile_device.get("device_id") if mobile_device else None
    return AgenticClientContext.from_values(
        device=trusted_device or ws.query_params.get("device"),
        locale=(mobile_device.get("locale") if mobile_device else None)
        or ws.query_params.get("locale"),
        timezone_name=(mobile_device.get("timezone") if mobile_device else None)
        or ws.query_params.get("timezone"),
        device_locked=bool(trusted_device),
    )


__all__ = [
    "AgenticClientContext",
    "parse_websocket_client_message",
    "websocket_client_context",
]
