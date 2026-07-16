"""Planificateur de contexte — injecte uniquement ce qui est pertinent."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from jarvis.cognitive.models import TaskIntent

logger = logging.getLogger(__name__)

# Budget total (caractères) du contexte conditionnel injecté, par profil.
BUDGET_LIMITS = {
    "minimal": 1500,
    "contact": 2000,
    "tool": 2500,
    "standard": 5000,
    "briefing": 8000,
    "dev": 3500,
}


@dataclass
class ContextSlice:
    """Un fragment de contexte avec traçabilité."""

    key: str
    content: str
    source: str
    relevance: float
    reason: str
    freshness: str = "unknown"
    sensitive: bool = False


@dataclass
class PlannedContext:
    slices: list[ContextSlice] = field(default_factory=list)
    budget: str = "minimal"
    diagnostics: list[dict[str, Any]] = field(default_factory=list)

    def char_budget(self) -> int:
        return BUDGET_LIMITS.get(self.budget, BUDGET_LIMITS["standard"])

    def as_prompt_block(self, max_chars: int = 6000) -> str:
        parts: list[str] = []
        used = 0
        for s in sorted(self.slices, key=lambda x: -x.relevance):
            chunk = f"[{s.key}]\n{s.content}\n[/{s.key}]"
            if used + len(chunk) > max_chars:
                break
            parts.append(chunk)
            used += len(chunk)
        return "\n\n".join(parts)

    def to_diagnostic(self) -> list[dict[str, Any]]:
        return [
            {
                "key": s.key,
                "source": s.source,
                "relevance": s.relevance,
                "reason": s.reason,
                "freshness": s.freshness,
                "sensitive": s.sensitive,
                "chars": len(s.content),
            }
            for s in self.slices
        ]


class ContextPlanner:
    """Sélectionne les sources selon l'intent (budget + pertinence)."""

    BUDGET_LIMITS = BUDGET_LIMITS  # rétrocompatibilité attribut de classe

    def plan(self, intent: TaskIntent, available: dict[str, Any] | None = None) -> PlannedContext:
        available = available or {}
        planned = PlannedContext(budget=intent.context_budget)
        domain = intent.domain

        def add(key: str, content: Any, source: str, relevance: float, reason: str, **kw: Any) -> None:
            if content is None or content == "" or content == [] or content == {}:
                return
            text = content if isinstance(content, str) else str(content)
            text = text.strip()
            if not text:
                return
            planned.slices.append(
                ContextSlice(
                    key=key,
                    content=text[:4000],
                    source=source,
                    relevance=relevance,
                    reason=reason,
                    **kw,
                )
            )

        # Toujours : horodatage léger si fourni
        if available.get("horodatage"):
            add("TIME", available["horodatage"], "system", 1.0, "ancrage temporel")

        if domain == "contacts":
            add("CONTACT", available.get("contact"), "people", 1.0, "contact résolu", freshness="live")
            add("CONTACT_AMBIGUITY", available.get("ambiguity"), "people", 0.9, "ambiguïté de résolution")
            add("RECENT_CONTACTS", available.get("recent_contacts"), "people", 0.5, "derniers contacts utilisés")
        elif domain in ("productivity",) or intent.execution_type == "tool" and domain == "productivity":
            add("CALENDAR", available.get("calendar"), "calendar", 0.95, "agenda demandé", freshness="live")
            add("TASKS", available.get("tasks"), "tasks", 0.8, "tâches pertinentes")
        elif domain == "info":
            add("WEATHER", available.get("weather"), "weather", 0.95, "météo", freshness="live")
        elif domain == "location":
            add("LOCATION", available.get("location"), "location", 1.0, "position courante", freshness="live", sensitive=True)
        elif domain == "briefing":
            for key, rel, reason in (
                ("CALENDAR", 1.0, "agenda du jour"),
                ("TASKS", 0.95, "tâches / échéances"),
                ("EMAILS", 0.9, "mails à traiter"),
                ("NOTIFICATIONS", 0.9, "alertes"),
                ("WEATHER", 0.6, "météo"),
                ("CURSOR_JOBS", 0.85, "travaux Cursor"),
                ("COMMITMENTS", 0.7, "engagements"),
                ("SERVICES", 0.5, "état services"),
            ):
                add(key, available.get(key.lower()) or available.get(key), key.lower(), rel, reason)
        elif domain == "dev" or intent.execution_type == "cursor":
            add("INTENT", intent.reason, "router", 1.0, "intention technique")
            add("GIT_STATUS", available.get("git_status"), "git", 0.9, "état du dépôt")
            add("CURSOR_HISTORY", available.get("cursor_history"), "cursor_jobs", 0.7, "délégations récentes")
            add("CONSTRAINTS", available.get("constraints"), "config", 0.8, "règles du projet")
        elif domain == "strategy":
            add("TASKS", available.get("tasks"), "tasks", 0.9, "priorités")
            add("CALENDAR", available.get("calendar"), "calendar", 0.85, "contraintes agenda")
            add("FACTS", available.get("user_facts"), "memory", 0.6, "faits utiles")
        else:
            # Mémoire ciblée uniquement si mots-clés mémoire
            if available.get("memory_hits"):
                add("MEMORY", available["memory_hits"], "memory", 0.8, "souvenirs pertinents")
            if available.get("screen_context"):
                add("SCREEN", available["screen_context"], "screen", 0.4, "contexte écran récent")

        # Fallback : toute source déjà collectée (mots-clés) non couverte par le
        # domaine reste tracée + budgétée, avec une pertinence plus faible.
        _fallback_keys = {
            "calendar": ("CALENDAR", "calendar"),
            "tasks": ("TASKS", "tasks"),
            "emails": ("EMAILS", "mail"),
            "weather": ("WEATHER", "weather"),
            "location": ("LOCATION", "location"),
            "memory_hits": ("MEMORY", "memory"),
            "screen_context": ("SCREEN", "screen"),
        }
        existing = {s.key for s in planned.slices}
        for avail_key, (slice_key, source) in _fallback_keys.items():
            if slice_key in existing:
                continue
            if available.get(avail_key):
                add(
                    slice_key,
                    available[avail_key],
                    source,
                    0.3,
                    "mots-clés détectés dans la demande (hors domaine principal)",
                    sensitive=slice_key == "LOCATION",
                )

        planned.diagnostics = planned.to_diagnostic()
        return planned


_planner = ContextPlanner()


def plan_context(intent: TaskIntent, available: dict[str, Any] | None = None) -> PlannedContext:
    return _planner.plan(intent, available)
