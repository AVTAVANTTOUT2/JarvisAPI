"""Registre central des capacités JARVIS — disponible / risque / exécuteur."""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from typing import Any, Literal

import config

logger = logging.getLogger(__name__)

Executor = Literal["jarvis_tool", "cursor", "deepseek", "system"]


@dataclass(frozen=True)
class Capability:
    name: str
    available: bool
    risk: str
    requires_confirmation: bool
    executor: Executor
    description: str
    dependencies: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class CapabilityRegistry:
    """Catalogue observable pour le routeur et l'UI."""

    def __init__(self) -> None:
        self._caps: dict[str, Capability] = {}
        self.refresh()

    def refresh(self) -> None:
        cursor_on = bool(getattr(config, "CURSOR_DELEGATION_ENABLED", True))
        computer_on = bool(getattr(config, "COMPUTER_ACCESS", True))
        code_exec = bool(getattr(config, "CODE_EXECUTOR_ENABLED", False))
        self._caps = {
            "calendar.create": Capability(
                "calendar.create", True, "medium", True, "jarvis_tool",
                "Créer un événement Calendar.app",
            ),
            "calendar.read": Capability(
                "calendar.read", True, "low", False, "jarvis_tool",
                "Lire l'agenda Calendar.app",
            ),
            "mail.read": Capability(
                "mail.read", True, "low", False, "jarvis_tool",
                "Lire les emails via Mail.app",
            ),
            "mail.send": Capability(
                "mail.send", True, "high", True, "jarvis_tool",
                "Envoyer un email",
            ),
            "tasks.create": Capability(
                "tasks.create", True, "low", False, "jarvis_tool",
                "Créer une tâche",
            ),
            "weather.read": Capability(
                "weather.read", bool(getattr(config, "WEATHER_API_KEY", "")), "low", False,
                "jarvis_tool", "Météo OpenWeatherMap",
                ("WEATHER_API_KEY",),
            ),
            "contacts.resolve": Capability(
                "contacts.resolve", True, "low", False, "jarvis_tool",
                "Résoudre un contact (Apple Contacts + people)",
            ),
            "imessage.send": Capability(
                "imessage.send", bool(getattr(config, "IMESSAGE_TARGET", "")), "high", True,
                "jarvis_tool", "Envoyer un iMessage",
            ),
            "computer.terminal": Capability(
                "computer.terminal", computer_on, "high", True, "jarvis_tool",
                "Exécuter une commande shell sécurisée",
            ),
            "cursor.delegate": Capability(
                "cursor.delegate", cursor_on, "medium", False, "cursor",
                "Déléguer une tâche technique à Cursor CLI (worktree isolé)",
            ),
            "code_executor": Capability(
                "code_executor", code_exec and not cursor_on, "high", True, "system",
                "Fallback Open Interpreter (désactivé si Cursor disponible)",
            ),
            "screen_watcher.vision": Capability(
                "screen_watcher.vision",
                bool(getattr(config, "SCREEN_WATCHER_ENABLED", True)),
                "low", False, "system",
                "Analyse visuelle locale via Ollama (seul usage Ollama autorisé)",
                ("OLLAMA_URL",),
            ),
            "voice.stt": Capability(
                "voice.stt", True, "low", False, "system",
                "STT local (faster-whisper / whisper.cpp)",
            ),
            "voice.tts": Capability(
                "voice.tts", True, "low", False, "system",
                "TTS Edge / Kokoro / macOS",
            ),
            "briefing.morning": Capability(
                "briefing.morning", True, "low", False, "deepseek",
                "Briefing du matin structuré",
            ),
            "self_repair": Capability(
                "self_repair",
                bool(getattr(config, "SELF_REPAIR_ENABLED", True)),
                "high", True, "cursor",
                "Auto-réparation via Cursor (PR only par défaut)",
            ),
            "self_improvement": Capability(
                "self_improvement",
                bool(getattr(config, "SELF_IMPROVEMENT_ENABLED", True)),
                "medium", True, "cursor",
                "Propositions d'amélioration basées sur des preuves",
            ),
        }

    def get(self, name: str) -> Capability | None:
        return self._caps.get(name)

    def list_all(self) -> list[dict[str, Any]]:
        return [c.to_dict() for c in sorted(self._caps.values(), key=lambda c: c.name)]

    def available_names(self) -> list[str]:
        return [n for n, c in self._caps.items() if c.available]

    def can(self, name: str) -> bool:
        cap = self._caps.get(name)
        return bool(cap and cap.available)


_registry: CapabilityRegistry | None = None


def get_capability_registry() -> CapabilityRegistry:
    global _registry
    if _registry is None:
        _registry = CapabilityRegistry()
    return _registry
