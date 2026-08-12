"""Construction générique du contexte et des budgets depuis la configuration."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from .models import AgenticContext, RunBudget
from .redaction import redact_mapping


def _config_value(source: Any, name: str, default: Any) -> Any:
    if isinstance(source, Mapping):
        return source.get(name, default)
    return getattr(source, name, default)


def _first_config_value(source: Any, names: tuple[str, ...], default: Any) -> Any:
    sentinel = object()
    for name in names:
        value = _config_value(source, name, sentinel)
        if value is not sentinel:
            return value
    return default


def build_run_budget(
    config_source: Any | None = None,
    *,
    overrides: Mapping[str, Any] | None = None,
    now: datetime | None = None,
) -> RunBudget:
    if config_source is None:
        import config as config_source  # import local, aucune dépendance runtime

    values: dict[str, Any] = {
        "max_duration_s": float(
            _first_config_value(
                config_source,
                ("AGENTIC_MAX_RUN_SECONDS", "AGENTIC_MAX_DURATION_S"),
                1800.0,
            )
        ),
        "max_steps": int(_config_value(config_source, "AGENTIC_MAX_STEPS", 50)),
        "max_tool_calls": int(
            _config_value(config_source, "AGENTIC_MAX_TOOL_CALLS", 100)
        ),
        "max_retries": int(_config_value(config_source, "AGENTIC_MAX_RETRIES", 3)),
        "model_token_budget": int(
            _config_value(config_source, "AGENTIC_MODEL_TOKEN_BUDGET", 200_000)
        ),
        "cost_budget": _config_value(config_source, "AGENTIC_COST_BUDGET", None),
        "concurrency_limit": int(
            _first_config_value(
                config_source,
                ("AGENTIC_MAX_CONCURRENT_RUNS", "AGENTIC_CONCURRENCY_LIMIT"),
                1,
            )
        ),
        "max_artifact_bytes": int(
            _config_value(config_source, "AGENTIC_MAX_ARTIFACT_BYTES", 50 * 1024 * 1024)
        ),
        "max_context_tokens": int(
            _config_value(config_source, "AGENTIC_MAX_CONTEXT_TOKENS", 128_000)
        ),
        "compaction_policy": str(
            _config_value(config_source, "AGENTIC_COMPACTION_POLICY", "checkpoint")
        ),
        "blocking_strategy": str(
            _config_value(config_source, "AGENTIC_BLOCKING_STRATEGY", "pause")
        ),
    }
    if values["cost_budget"] in {"", None}:
        values["cost_budget"] = None
    else:
        values["cost_budget"] = float(values["cost_budget"])
    values.update(dict(overrides or {}))
    if "deadline" not in values:
        base = now or datetime.now(timezone.utc)
        values["deadline"] = base + timedelta(seconds=float(values["max_duration_s"]))
    return RunBudget(**values)


def build_agentic_context(
    *,
    run_id: str,
    profile_id: str,
    conversation_id: str | None = None,
    task_id: str | None = None,
    channel: str = "api",
    device: str | None = None,
    locale: str = "fr-FR",
    timezone_name: str = "Europe/Paris",
    permissions: tuple[str, ...] | list[str] = (),
    selected_context: Mapping[str, Any] | None = None,
    origin: str = "user",
    bypass_agentic_reclassification: bool = False,
) -> AgenticContext:
    if origin == "agent_runtime" and not bypass_agentic_reclassification:
        raise ValueError(
            "origin=agent_runtime exige bypass_agentic_reclassification=true"
        )
    if origin != "agent_runtime" and bypass_agentic_reclassification:
        raise ValueError("le bypass est réservé aux appels agent_runtime")
    return AgenticContext(
        run_id=run_id,
        profile_id=profile_id,
        conversation_id=conversation_id,
        task_id=task_id,
        channel=channel,
        device=device,
        locale=locale,
        timezone=timezone_name,
        permissions=tuple(permissions),
        selected_context=redact_mapping(selected_context),
        origin=origin,
        bypass_agentic_reclassification=bypass_agentic_reclassification,
    )


__all__ = ["build_agentic_context", "build_run_budget"]
