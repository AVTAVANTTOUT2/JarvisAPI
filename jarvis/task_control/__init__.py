"""Pilotage de tâches JARVIS — capture, plan, validation humaine, exécution.

Les exports sont résolus paresseusement : importer le domaine ne charge ni
backend LLM, ni runtime d'exécution, ni base. C'est la même discipline que
``jarvis.agentic`` et elle sert la même chose — supprimer un plugin
d'exécution laisse JARVIS importable.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

_EXPORTS: dict[str, tuple[str, str]] = {
    # Domaine
    "ControlTask": (".models", "ControlTask"),
    "TaskStatus": (".models", "TaskStatus"),
    "TaskPriority": (".models", "TaskPriority"),
    "TaskSource": (".models", "TaskSource"),
    "TaskSourceType": (".models", "TaskSourceType"),
    "TaskSourceChannel": (".models", "TaskSourceChannel"),
    "TaskPlan": (".models", "TaskPlan"),
    "PlanStep": (".models", "PlanStep"),
    "PlanDecision": (".models", "PlanDecision"),
    "ApprovalKind": (".models", "ApprovalKind"),
    "TaskActivity": (".models", "TaskActivity"),
    "TaskActivityType": (".models", "TaskActivityType"),
    "TaskActivityLevel": (".models", "TaskActivityLevel"),
    "TaskCandidate": (".models", "TaskCandidate"),
    "CandidateDecision": (".models", "CandidateDecision"),
    "TaskReport": (".models", "TaskReport"),
    "InvalidTaskTransition": (".models", "InvalidTaskTransition"),
    "TaskExecutionRefused": (".models", "TaskExecutionRefused"),
    "ensure_executable": (".models", "ensure_executable"),
    "validate_task_transition": (".models", "validate_task_transition"),
    "ALLOWED_TASK_TRANSITIONS": (".models", "ALLOWED_TASK_TRANSITIONS"),
    # Planification
    "generate_plan": (".planner", "generate_plan"),
    "build_plan": (".planner", "build_plan"),
    # Détection
    "DetectionInput": (".detection", "DetectionInput"),
    "DetectedTask": (".detection", "DetectedTask"),
    "TaskCandidateDetector": (".detection", "TaskCandidateDetector"),
    "detector_from_config": (".detection", "detector_from_config"),
    "detection_input_from_email": (".detection", "detection_input_from_email"),
    "detection_input_from_message": (".detection", "detection_input_from_message"),
    # Activité et rapports
    "build_activity": (".activity", "build_activity"),
    "build_report": (".reports", "build_report"),
    # Événements
    "TASK_CONTROL_EVENT_TYPES": (".events", "TASK_CONTROL_EVENT_TYPES"),
    "emit_task_control_event": (".events", "emit_task_control_event"),
    # Service
    "TaskControlService": (".service", "TaskControlService"),
    "get_task_control_service": (".service", "get_task_control_service"),
    "reset_task_control_service_for_tests": (
        ".service",
        "reset_task_control_service_for_tests",
    ),
}


def __getattr__(name: str) -> Any:
    try:
        module_name, attribute = _EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(f"module {__name__!r} n'expose pas {name!r}") from exc
    module = import_module(module_name, __name__)
    value = getattr(module, attribute)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_EXPORTS))


__all__ = sorted(_EXPORTS)
