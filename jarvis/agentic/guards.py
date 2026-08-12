"""Contrôles déterministes de budgets et de boucles sans progression."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import hashlib
import json
from typing import Any, Mapping

from .models import AgenticError, AgenticErrorCode, RunBudget
from .redaction import redact_mapping


@dataclass(frozen=True)
class BudgetUsage:
    elapsed_s: float = 0.0
    steps: int = 0
    tool_calls: int = 0
    retries: int = 0
    model_tokens: int = 0
    cost: float = 0.0
    artifact_bytes: int = 0


def check_budget(budget: RunBudget, usage: BudgetUsage) -> AgenticError | None:
    limits = (
        ("duration", usage.elapsed_s, budget.max_duration_s),
        ("steps", usage.steps, budget.max_steps),
        ("tool_calls", usage.tool_calls, budget.max_tool_calls),
        ("retries", usage.retries, budget.max_retries),
        ("model_tokens", usage.model_tokens, budget.model_token_budget),
        ("artifact_bytes", usage.artifact_bytes, budget.max_artifact_bytes),
    )
    for name, consumed, limit in limits:
        if consumed > limit:
            return AgenticError(
                AgenticErrorCode.BUDGET_EXCEEDED,
                f"budget {name} dépassé",
                details={"budget": name, "consumed": consumed, "limit": limit},
            )
    if budget.cost_budget is not None and usage.cost > budget.cost_budget:
        return AgenticError(
            AgenticErrorCode.BUDGET_EXCEEDED,
            "budget cost dépassé",
            details={
                "budget": "cost",
                "consumed": usage.cost,
                "limit": budget.cost_budget,
            },
        )
    return None


@dataclass(frozen=True)
class ToolAttempt:
    tool: str
    arguments_hash: str
    error_code: str | None
    progress: bool


class DoomLoopDetector:
    """Détecte répétition exacte, erreurs identiques, stagnation et alternance."""

    def __init__(self, *, window_size: int = 6, repeat_limit: int = 3) -> None:
        if window_size < 4 or repeat_limit < 2:
            raise ValueError("fenêtre ou seuil de boucle invalide")
        self._attempts: deque[ToolAttempt] = deque(maxlen=window_size)
        self.repeat_limit = repeat_limit

    @staticmethod
    def _arguments_hash(arguments: Mapping[str, Any] | None) -> str:
        payload = json.dumps(
            redact_mapping(arguments),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def record(
        self,
        *,
        tool: str,
        arguments: Mapping[str, Any] | None = None,
        error_code: str | None = None,
        progress: bool = False,
    ) -> str | None:
        self._attempts.append(
            ToolAttempt(
                tool=tool,
                arguments_hash=self._arguments_hash(arguments),
                error_code=error_code,
                progress=progress,
            )
        )
        attempts = tuple(self._attempts)
        tail = attempts[-self.repeat_limit :]
        if (
            len(tail) == self.repeat_limit
            and len({(item.tool, item.arguments_hash) for item in tail}) == 1
        ):
            return "same_tool_arguments"
        if (
            len(tail) == self.repeat_limit
            and tail[0].error_code is not None
            and len({item.error_code for item in tail}) == 1
        ):
            return "same_error"
        if len(attempts) == self._attempts.maxlen and not any(
            item.progress for item in attempts
        ):
            return "no_progress"
        if len(attempts) >= 6:
            pattern = [(item.tool, item.arguments_hash) for item in attempts[-6:]]
            if (
                pattern[0] == pattern[2] == pattern[4]
                and pattern[1] == pattern[3] == pattern[5]
            ):
                if pattern[0] != pattern[1]:
                    return "alternating_pattern"
        return None

    def reset(self) -> None:
        self._attempts.clear()


__all__ = ["BudgetUsage", "DoomLoopDetector", "ToolAttempt", "check_budget"]
