"""API publique lazy du domaine agentique JARVIS.

Le chargement différé évite toute dépendance de démarrage envers un plugin ou
la base, et garantit que supprimer ``integrations/<runtime>`` reste sûr.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any


_EXPORTS: dict[str, tuple[str, str]] = {
    "AgenticService": (".service", "AgenticService"),
    "get_agentic_service": (".service", "get_agentic_service"),
    "reset_agentic_service_for_tests": (".service", "reset_agentic_service_for_tests"),
    "discover_runtime_plugins": (".registry", "discover_runtime_plugins"),
    "RuntimeRegistry": (".registry", "RuntimeRegistry"),
    "RuntimePluginError": (".registry", "RuntimePluginError"),
    "RuntimePluginManifestError": (".registry", "RuntimePluginManifestError"),
    "AgenticRuntime": (".runtime", "AgenticRuntime"),
    "classify_agentic_request": (".classifier", "classify_agentic_request"),
    "AgenticRecursionError": (".classifier", "AgenticRecursionError"),
    "build_agentic_context": (".context", "build_agentic_context"),
    "build_run_budget": (".context", "build_run_budget"),
    "CAPABILITY_PROFILE_CONTEXT_KEY": (
        ".profiles",
        "CAPABILITY_PROFILE_CONTEXT_KEY",
    ),
    "CAPABILITY_PROFILES": (".profiles", "CAPABILITY_PROFILES"),
    "CapabilityProfile": (".profiles", "CapabilityProfile"),
    "capability_profile_id_from_context": (
        ".profiles",
        "capability_profile_id_from_context",
    ),
    "get_capability_profile": (".profiles", "get_capability_profile"),
    "select_capability_profile": (".profiles", "select_capability_profile"),
    "BudgetUsage": (".guards", "BudgetUsage"),
    "DoomLoopDetector": (".guards", "DoomLoopDetector"),
    "check_budget": (".guards", "check_budget"),
    "CompletionVerifier": (".verifier", "CompletionVerifier"),
    "DEFAULT_VERIFIER_REGISTRY": (".verifier", "DEFAULT_VERIFIER_REGISTRY"),
    "DEFAULT_RUNTIME_VERIFIER": (".verifier", "DEFAULT_RUNTIME_VERIFIER"),
    "DeterministicRuntimeVerifier": (".verifier", "DeterministicRuntimeVerifier"),
    "FAIL_CLOSED_VERIFIER": (".verifier", "FAIL_CLOSED_VERIFIER"),
    "VerifierRegistry": (".verifier", "VerifierRegistry"),
    "build_jarvis_receipt_artifact": (".verifier", "build_jarvis_receipt_artifact"),
    "verify_runtime_completion": (".verifier", "verify_runtime_completion"),
}

for _name in (
    "ALLOWED_RUN_TRANSITIONS",
    "TERMINAL_RUN_STATUSES",
    "AgenticClassification",
    "AgenticContext",
    "AgenticError",
    "AgenticErrorCode",
    "AgenticRequestCategory",
    "AgenticRun",
    "AgenticRunStatus",
    "ApprovalDecision",
    "ApprovalRequest",
    "Artifact",
    "InvalidRunTransition",
    "RiskLevel",
    "RunBudget",
    "RuntimeEvent",
    "RuntimeHealth",
    "RuntimeHealthStatus",
    "RuntimePluginManifest",
    "ToolCapability",
    "VerificationEvidence",
    "VerificationResult",
    "VerificationVerdict",
    "Verifier",
    "validate_run_transition",
):
    _EXPORTS[_name] = (".models", _name)


def __getattr__(name: str) -> Any:
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(name)
    module_name, attribute = target
    value = getattr(import_module(module_name, __name__), attribute)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted({*globals(), *_EXPORTS})


__all__ = sorted(_EXPORTS)
