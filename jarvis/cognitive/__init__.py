"""Routage cognitif unifié — Flash / Main / Cursor / outils."""

from jarvis.cognitive.models import TaskIntent
from jarvis.cognitive.router import CognitiveRouter, route_request
from jarvis.cognitive.context_planner import ContextPlanner, plan_context
from jarvis.cognitive.capability_registry import CapabilityRegistry, get_capability_registry
from jarvis.cognitive.ollama_guard import (
    OLLAMA_ALLOWED_MODULES,
    assert_ollama_caller_allowed,
    ollama_reasoning_consumers,
)

__all__ = [
    "TaskIntent",
    "CognitiveRouter",
    "route_request",
    "ContextPlanner",
    "plan_context",
    "CapabilityRegistry",
    "get_capability_registry",
    "OLLAMA_ALLOWED_MODULES",
    "assert_ollama_caller_allowed",
    "ollama_reasoning_consumers",
]
