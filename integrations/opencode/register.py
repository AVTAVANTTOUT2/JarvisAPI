"""Point d'entrée lazy chargé exclusivement depuis ``plugin.json``."""

from __future__ import annotations

from jarvis.agentic.models import RiskLevel, RuntimePluginManifest, ToolCapability

from .adapter import OpenCodeRuntime


_DEFAULT_CAPABILITIES = (
    ToolCapability("workspace.read", "workspace:read", "Lecture confinée du workspace"),
    ToolCapability(
        "project_state.read",
        "project_state:read",
        "Lecture de l'état des projets et exécutions agentiques du profil JARVIS",
    ),
    ToolCapability(
        "communications.read",
        "communications:read",
        "Lecture des emails, iMessages et notifications du profil JARVIS",
    ),
    ToolCapability(
        "calendar.read",
        "calendar:read",
        "Lecture du calendrier du profil JARVIS",
    ),
    ToolCapability(
        "conversations.read",
        "conversations:read",
        "Lecture des conversations et messages du profil JARVIS",
    ),
    ToolCapability(
        "memory.read",
        "memory:read",
        "Lecture de la mémoire personnelle structurée du profil JARVIS",
    ),
    ToolCapability(
        "contacts.read",
        "contacts:read",
        "Lecture des personnes et relations du profil JARVIS",
    ),
    ToolCapability(
        "media.read",
        "media:read",
        "Lecture des enregistrements et transcriptions du profil JARVIS",
    ),
    ToolCapability(
        "documents.read",
        "documents:read",
        "Lecture des documents personnels du profil JARVIS",
    ),
    ToolCapability(
        "documentation.read",
        "documentation:read",
        "Lecture des documents utiles au runtime de développement",
    ),
    ToolCapability(
        "research.search",
        "research:search",
        "Recherche externe sans accès implicite aux données personnelles",
    ),
    ToolCapability(
        "browser.control",
        "browser:control",
        "Ouvrir une page HTTPS publique, la voir, puis cliquer ou taper",
        risk_level=RiskLevel.MEDIUM,
    ),
    ToolCapability(
        "browser.download",
        "browser:download",
        "Téléchargement confiné annoncé pour le profil navigateur",
        risk_level=RiskLevel.MEDIUM,
    ),
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
