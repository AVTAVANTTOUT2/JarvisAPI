"""Client asynchrone OpenCode v1.18.16."""

from .auth import BasicAuthCredentials
from .client import ContractReport, OpenCodeClient
from .contract import ContractMetadata, PINNED_VERSION
from .models import (
    HealthInfo,
    ModelSelection,
    ReconciliationSnapshot,
    SSEEvent,
    Session,
    TextPart,
)

__all__ = [
    "BasicAuthCredentials",
    "ContractReport",
    "ContractMetadata",
    "HealthInfo",
    "ModelSelection",
    "OpenCodeClient",
    "PINNED_VERSION",
    "ReconciliationSnapshot",
    "SSEEvent",
    "Session",
    "TextPart",
]
