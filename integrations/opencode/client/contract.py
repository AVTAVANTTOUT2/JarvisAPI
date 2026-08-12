"""Empreinte et opérations obligatoires du contrat OpenAPI épinglé."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Any, Mapping


DEFAULT_CONTRACT_PATH = Path(__file__).with_name("openapi") / "contract-v1.18.16.json"
PINNED_VERSION = "1.18.16"
_HEX_40 = re.compile(r"^[0-9a-f]{40}$")
_HEX_64 = re.compile(r"^[0-9a-f]{64}$")


class ContractMetadataError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ContractMetadata:
    provider_version: str
    source_commit: str
    source_sha256: str
    source_bytes: int
    operations: Mapping[str, str]

    @classmethod
    def load(cls, path: Path = DEFAULT_CONTRACT_PATH) -> "ContractMetadata":
        try:
            payload: Any = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ContractMetadataError(
                f"Métadonnées OpenAPI illisibles: {path}"
            ) from exc
        if not isinstance(payload, dict) or payload.get("schema_version") != 1:
            raise ContractMetadataError("Schéma de métadonnées OpenAPI invalide")
        operations = payload.get("required_operations")
        if not isinstance(operations, dict) or not operations:
            raise ContractMetadataError("Opérations OpenAPI obligatoires absentes")
        if not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in operations.items()
        ):
            raise ContractMetadataError("Opérations OpenAPI invalides")
        commit = payload.get("source_commit")
        digest = payload.get("source_sha256")
        size = payload.get("source_bytes")
        if not isinstance(commit, str) or not _HEX_40.fullmatch(commit):
            raise ContractMetadataError("Commit OpenAPI invalide")
        if not isinstance(digest, str) or not _HEX_64.fullmatch(digest):
            raise ContractMetadataError("SHA-256 OpenAPI invalide")
        if not isinstance(size, int) or size <= 0:
            raise ContractMetadataError("Taille OpenAPI invalide")
        provider_version = str(payload.get("provider_version", ""))
        if provider_version != PINNED_VERSION:
            raise ContractMetadataError(
                "Version provider des métadonnées OpenAPI invalide"
            )
        return cls(
            provider_version=provider_version,
            source_commit=commit,
            source_sha256=digest,
            source_bytes=size,
            operations=dict(operations),
        )
