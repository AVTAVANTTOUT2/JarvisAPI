"""Modèles tolérants aux ajouts compatibles du contrat OpenCode."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from typing import Any, Literal, Mapping, Sequence


JsonObject = dict[str, Any]
PermissionReply = Literal["once", "always", "reject"]


class ModelValidationError(ValueError):
    pass


def _object(value: Any, label: str) -> JsonObject:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ModelValidationError(f"{label} doit être un objet JSON")
    return dict(value)


def _identifier(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or any(ord(char) < 32 for char in value):
        raise ModelValidationError(f"{label} invalide")
    return value


@dataclass(frozen=True, slots=True)
class HealthInfo:
    healthy: bool
    version: str

    @classmethod
    def from_payload(cls, value: Any) -> "HealthInfo":
        payload = _object(value, "health")
        if payload.get("healthy") is not True:
            raise ModelValidationError("Le serveur OpenCode n'est pas sain")
        return cls(
            healthy=True, version=_identifier(payload.get("version"), "health.version")
        )


@dataclass(frozen=True, slots=True)
class ModelSelection:
    provider_id: str
    model_id: str
    variant: str | None = None

    def __post_init__(self) -> None:
        _identifier(self.provider_id, "provider_id")
        _identifier(self.model_id, "model_id")
        if self.variant is not None:
            _identifier(self.variant, "variant")

    def for_session(self) -> JsonObject:
        value: JsonObject = {"providerID": self.provider_id, "id": self.model_id}
        if self.variant is not None:
            value["variant"] = self.variant
        return value

    def for_prompt(self) -> JsonObject:
        return {"providerID": self.provider_id, "modelID": self.model_id}


@dataclass(frozen=True, slots=True)
class TextPart:
    text: str
    synthetic: bool | None = None

    def to_payload(self) -> JsonObject:
        value: JsonObject = {"type": "text", "text": self.text}
        if self.synthetic is not None:
            value["synthetic"] = self.synthetic
        return value


@dataclass(frozen=True, slots=True)
class Session:
    id: str
    title: str | None
    parent_id: str | None
    directory: str | None
    raw: JsonObject = field(repr=False)

    @classmethod
    def from_payload(cls, value: Any) -> "Session":
        payload = _object(value, "session")
        return cls(
            id=_identifier(payload.get("id"), "session.id"),
            title=payload.get("title")
            if isinstance(payload.get("title"), str)
            else None,
            parent_id=payload.get("parentID")
            if isinstance(payload.get("parentID"), str)
            else None,
            directory=payload.get("directory")
            if isinstance(payload.get("directory"), str)
            else None,
            raw=payload,
        )


@dataclass(frozen=True, slots=True)
class MessageEnvelope:
    info: JsonObject
    parts: tuple[JsonObject, ...]

    @classmethod
    def from_payload(cls, value: Any) -> "MessageEnvelope":
        payload = _object(value, "message")
        info = _object(payload.get("info"), "message.info")
        parts_value = payload.get("parts")
        if not isinstance(parts_value, list):
            raise ModelValidationError("message.parts doit être un tableau")
        return cls(
            info=info,
            parts=tuple(_object(part, "message.part") for part in parts_value),
        )


@dataclass(frozen=True, slots=True)
class PermissionRequest:
    id: str
    session_id: str | None
    permission: str | None
    raw: JsonObject = field(repr=False)

    @classmethod
    def from_payload(cls, value: Any) -> "PermissionRequest":
        payload = _object(value, "permission")
        return cls(
            id=_identifier(payload.get("id"), "permission.id"),
            session_id=payload.get("sessionID")
            if isinstance(payload.get("sessionID"), str)
            else None,
            permission=payload.get("permission")
            if isinstance(payload.get("permission"), str)
            else None,
            raw=payload,
        )


_PROVIDER_SECRET_FIELDS = frozenset(
    {"key", "apiKey", "api_key", "token", "secret", "password", "authorization"}
)


def _sanitize_provider_entry(value: Any) -> JsonObject:
    """Conserve les métadonnées utiles et retire tout credential fournisseur."""

    from integrations.opencode.security.redaction import REDACTED, redact_mapping

    provider = _object(value, "provider")
    secrets = tuple(
        item
        for field_name in _PROVIDER_SECRET_FIELDS
        if isinstance((item := provider.get(field_name)), str) and item.strip()
    )
    sanitized = redact_mapping(provider, secrets)
    if not isinstance(sanitized, dict):
        raise ModelValidationError("Provider OpenCode invalide après redaction")
    for field_name in _PROVIDER_SECRET_FIELDS:
        if field_name in sanitized and sanitized[field_name] not in (None, ""):
            sanitized[field_name] = REDACTED
    return sanitized


@dataclass(frozen=True, slots=True)
class ProviderCatalog:
    all: tuple[JsonObject, ...]
    default: Mapping[str, str]
    connected: tuple[str, ...]

    @classmethod
    def from_payload(cls, value: Any) -> "ProviderCatalog":
        payload = _object(value, "providers")
        providers = payload.get("all")
        defaults = payload.get("default")
        connected = payload.get("connected")
        if (
            not isinstance(providers, list)
            or not isinstance(defaults, dict)
            or not isinstance(connected, list)
        ):
            raise ModelValidationError("Catalogue provider OpenCode invalide")
        if not all(
            isinstance(key, str) and isinstance(item, str)
            for key, item in defaults.items()
        ):
            raise ModelValidationError("Providers par défaut invalides")
        if not all(isinstance(item, str) for item in connected):
            raise ModelValidationError("Providers connectés invalides")
        return cls(
            all=tuple(_sanitize_provider_entry(item) for item in providers),
            default=dict(defaults),
            connected=tuple(connected),
        )


@dataclass(frozen=True, slots=True)
class SSEEvent:
    event_id: str
    event_type: str
    data: Any
    source: str
    retry_ms: int | None = None
    resume_id: str | None = None

    @classmethod
    def create(
        cls,
        *,
        event_id: str | None,
        event_type: str | None,
        data: Any,
        source: str,
        retry_ms: int | None,
        resume_id: str | None = None,
    ) -> "SSEEvent":
        if event_type:
            resolved_type = event_type
        elif isinstance(data, dict) and isinstance(data.get("type"), str):
            resolved_type = data["type"]
        else:
            resolved_type = "message"
        resolved_id = event_id or _event_identifier(data, resolved_type, source)
        return cls(resolved_id, resolved_type, data, source, retry_ms, resume_id)


def _event_identifier(data: Any, event_type: str, source: str) -> str:
    if isinstance(data, dict):
        for key in ("id", "event_id", "eventID"):
            if isinstance(data.get(key), str) and data[key]:
                return data[key]
        properties = data.get("properties")
        if isinstance(properties, dict):
            for key in ("id", "event_id", "eventID"):
                if isinstance(properties.get(key), str) and properties[key]:
                    return properties[key]
    canonical = json.dumps(
        {"source": source, "type": event_type, "data": data},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class ReconciliationSnapshot:
    session: Session
    status: JsonObject
    messages: tuple[MessageEnvelope, ...]
    permissions: tuple[PermissionRequest, ...]


def serialize_parts(parts: Sequence[TextPart | Mapping[str, Any]]) -> list[JsonObject]:
    if not parts:
        raise ModelValidationError("Un prompt doit contenir au moins une part")
    serialized: list[JsonObject] = []
    for part in parts:
        if isinstance(part, TextPart):
            value = part.to_payload()
        else:
            value = _object(part, "prompt.part")
        if not isinstance(value.get("type"), str):
            raise ModelValidationError("prompt.part.type est obligatoire")
        serialized.append(value)
    return serialized
