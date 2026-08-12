"""Point d'entrée lazy chargé exclusivement depuis ``plugin.json``."""

from __future__ import annotations

from jarvis.agentic.models import RiskLevel, RuntimePluginManifest, ToolCapability

from .adapter import OpenCodeRuntime


_DEFAULT_CAPABILITIES = (
    ToolCapability("workspace.read", "workspace:read", "Lecture confinée du workspace"),
    ToolCapability(
        "workspace.edit",
        "workspace:write",
        "Édition réversible confinée au workspace",
        risk_level=RiskLevel.MEDIUM,
        requires_approval=True,
    ),
    ToolCapability("tests.run", "tests:run", "Exécution bornée des tests du workspace"),
    ToolCapability("tasks.read", "tasks:read", "Lecture des tâches du profil JARVIS"),
    ToolCapability(
        "tasks.write",
        "tasks:write",
        "Création réversible et idempotente de tâches JARVIS",
        risk_level=RiskLevel.MEDIUM,
        requires_approval=True,
    ),
)


def create_runtime(*, manifest: RuntimePluginManifest | None = None) -> OpenCodeRuntime:
    """Construit le runtime sans démarrer de processus ni faire d'I/O réseau."""
    capabilities = (
        manifest.capabilities if manifest is not None else _DEFAULT_CAPABILITIES
    )
    return OpenCodeRuntime(capabilities=capabilities)


__all__ = ["create_runtime"]
