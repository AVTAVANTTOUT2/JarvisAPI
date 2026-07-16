"""Modèles du routage cognitif JARVIS."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

InteractionMode = Literal["voice", "chat", "imessage", "scheduled", "loop", "android"]
Complexity = Literal["instant", "standard", "heavy"]
ExecutionType = Literal["answer", "tool", "cursor", "workflow"]
RiskLevel = Literal["low", "medium", "high", "critical"]


@dataclass(slots=True)
class TaskIntent:
    """Représentation structurée d'une demande utilisateur."""

    interaction_mode: InteractionMode
    domain: str
    complexity: Complexity
    execution_type: ExecutionType
    reasoning_model: str
    requires_confirmation: bool = False
    expected_duration: str = "seconds"
    risk_level: RiskLevel = "low"
    reason: str = ""
    prompt_model: str | None = None
    voice_ack: str | None = None
    template_id: str | None = None
    context_budget: str = "minimal"
    extras: dict[str, Any] = field(default_factory=dict)

    def to_diagnostic(self) -> dict[str, Any]:
        """JSON visible dans les diagnostics / UI Intelligence."""
        return {
            "interaction_mode": self.interaction_mode,
            "domain": self.domain,
            "complexity": self.complexity,
            "execution_type": self.execution_type,
            "initial_model": self.reasoning_model,
            "prompt_model": self.prompt_model or self.reasoning_model,
            "reason": self.reason,
            "risk_level": self.risk_level,
            "requires_confirmation": self.requires_confirmation,
            "expected_duration": self.expected_duration,
            "template_id": self.template_id,
            "context_budget": self.context_budget,
            "voice_ack": self.voice_ack,
            **self.extras,
        }

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)
