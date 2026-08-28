"""Contrat interne pour une livraison de code pilotée par Task Control.

Le contrat est construit par du code JARVIS de confiance, jamais depuis le
payload générique de l'API des tâches. Son empreinte est ajoutée au contenu
signé du plan ; modifier le dépôt, les validations ou la version du runtime
après approbation rend donc la tâche inexécutable.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

from .models import PlanStep, TaskExecutionRefused, TaskPlan, clamp_text


ENGINEERING_DELIVERY_METADATA_KEY = "jarvis_engineering_delivery"
ENGINEERING_DELIVERY_SCHEMA_VERSION = 1
_JOB_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9-]{0,63}$")
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_RUNTIME_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")
_MAX_REQUIRED_TESTS = 8
_MAX_ACCEPTANCE_CRITERIA = 8
_EXPECTED_FIELDS = frozenset(
    {
        "schema_version",
        "repo_root",
        "job_id",
        "idempotency_key",
        "runtime_id",
        "runtime_version",
        "required_tests",
        "acceptance_criteria",
        "commit_message",
        "digest",
    }
)


def _digest_payload(value: Mapping[str, Any]) -> str:
    payload = {key: item for key, item in value.items() if key != "digest"}
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _canonical_tests(
    commands: Sequence[str | Sequence[str]], repo_root: Path
) -> tuple[tuple[str, ...], ...]:
    from agents.devagent.agentic_runtime import _validation_argv

    if not commands:
        raise ValueError("au moins une validation JARVIS est requise")
    canonical = tuple(_validation_argv(command, repo_root) for command in commands)
    if len(canonical) > _MAX_REQUIRED_TESTS:
        raise ValueError("trop de validations JARVIS")
    return canonical


@dataclass(frozen=True, slots=True)
class EngineeringDeliveryContract:
    repo_root: Path
    job_id: str
    idempotency_key: str
    runtime_id: str
    runtime_version: str
    required_tests: tuple[tuple[str, ...], ...]
    acceptance_criteria: tuple[str, ...]
    commit_message: str
    digest: str

    @property
    def approval_marker(self) -> str:
        return f"Contrat de livraison JARVIS sha256:{self.digest}"

    @property
    def runtime_label(self) -> str:
        return f"{self.runtime_id}@{self.runtime_version}"

    def to_metadata(self) -> dict[str, Any]:
        return {
            "schema_version": ENGINEERING_DELIVERY_SCHEMA_VERSION,
            "repo_root": str(self.repo_root),
            "job_id": self.job_id,
            "idempotency_key": self.idempotency_key,
            "runtime_id": self.runtime_id,
            "runtime_version": self.runtime_version,
            "required_tests": [list(command) for command in self.required_tests],
            "acceptance_criteria": list(self.acceptance_criteria),
            "commit_message": self.commit_message,
            "digest": self.digest,
        }


def build_engineering_delivery_contract(
    *,
    repo_root: Path,
    required_tests: Sequence[str | Sequence[str]],
    acceptance_criteria: Sequence[str] = (),
    commit_message: str,
    idempotency_key: str,
    runtime_id: str,
    runtime_version: str,
) -> EngineeringDeliveryContract:
    """Construit et valide le contrat sans créer de worktree ni de processus."""

    requested = repo_root.expanduser()
    if requested.is_symlink():
        raise ValueError("racine Git symbolique refusée")
    resolved = requested.resolve(strict=True)
    if not resolved.is_dir():
        raise ValueError("racine Git invalide")
    stable_key = str(idempotency_key or "").strip()
    if not stable_key or len(stable_key.encode("utf-8")) > 500:
        raise ValueError("clé d'idempotence de livraison invalide")
    stable_runtime_id = str(runtime_id or "").strip()
    stable_runtime_version = str(runtime_version or "").strip()
    if (
        not _RUNTIME_ID_RE.fullmatch(stable_runtime_id)
        or not stable_runtime_version
        or len(stable_runtime_version) > 100
        or any(ord(char) < 0x20 for char in stable_runtime_version)
    ):
        raise ValueError("identité de runtime invalide")
    job_id = hashlib.sha256(stable_key.encode("utf-8")).hexdigest()[:32]
    criteria = tuple(
        dict.fromkeys(
            clamp_text(item, 300)
            for item in acceptance_criteria
            if str(item).strip()
        )
    )[:_MAX_ACCEPTANCE_CRITERIA]
    message = clamp_text(commit_message, 120)
    if not message:
        raise ValueError("message de commit requis")
    tests = _canonical_tests(required_tests, resolved)
    payload: dict[str, Any] = {
        "schema_version": ENGINEERING_DELIVERY_SCHEMA_VERSION,
        "repo_root": str(resolved),
        "job_id": job_id,
        "idempotency_key": stable_key,
        "runtime_id": stable_runtime_id,
        "runtime_version": stable_runtime_version,
        "required_tests": [list(command) for command in tests],
        "acceptance_criteria": list(criteria),
        "commit_message": message,
    }
    return EngineeringDeliveryContract(
        repo_root=resolved,
        job_id=job_id,
        idempotency_key=stable_key,
        runtime_id=stable_runtime_id,
        runtime_version=str(payload["runtime_version"]),
        required_tests=tests,
        acceptance_criteria=criteria,
        commit_message=message,
        digest=_digest_payload(payload),
    )


def engineering_delivery_contract_from_metadata(
    metadata: Mapping[str, Any],
) -> EngineeringDeliveryContract | None:
    """Relit strictement le contrat persisté, ou refuse avant tout effet."""

    raw = metadata.get(ENGINEERING_DELIVERY_METADATA_KEY)
    if raw is None:
        return None
    if not isinstance(raw, Mapping) or set(raw) != _EXPECTED_FIELDS:
        raise TaskExecutionRefused("contrat de livraison JARVIS invalide")
    try:
        schema_version = raw["schema_version"]
        repo_text = raw["repo_root"]
        job_id = raw["job_id"]
        idempotency_key = raw["idempotency_key"]
        runtime_id = raw["runtime_id"]
        runtime_version = raw["runtime_version"]
        required_tests = raw["required_tests"]
        acceptance_criteria = raw["acceptance_criteria"]
        commit_message = raw["commit_message"]
        digest = raw["digest"]
        if (
            schema_version != ENGINEERING_DELIVERY_SCHEMA_VERSION
            or not isinstance(repo_text, str)
            or not isinstance(job_id, str)
            or not _JOB_ID_RE.fullmatch(job_id)
            or not isinstance(idempotency_key, str)
            or not idempotency_key
            or len(idempotency_key.encode("utf-8")) > 500
            or not isinstance(runtime_id, str)
            or not _RUNTIME_ID_RE.fullmatch(runtime_id)
            or not isinstance(runtime_version, str)
            or not runtime_version
            or len(runtime_version) > 100
            or any(ord(char) < 0x20 for char in runtime_version)
            or not isinstance(required_tests, list)
            or not isinstance(acceptance_criteria, list)
            or not isinstance(commit_message, str)
            or not commit_message
            or len(commit_message) > 120
            or not isinstance(digest, str)
            or not _DIGEST_RE.fullmatch(digest)
        ):
            raise ValueError
        requested = Path(repo_text).expanduser()
        if requested.is_symlink():
            raise ValueError
        repo = requested.resolve(strict=True)
        if str(repo) != repo_text or not repo.is_dir():
            raise ValueError
        if job_id != hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()[:32]:
            raise ValueError
        tests = _canonical_tests(required_tests, repo)
        criteria = tuple(
            clamp_text(item, 300)
            for item in acceptance_criteria
            if isinstance(item, str) and item.strip()
        )
        if (
            list(criteria) != acceptance_criteria
            or len(criteria) > _MAX_ACCEPTANCE_CRITERIA
        ):
            raise ValueError
        normalized = dict(raw)
        if _digest_payload(normalized) != digest:
            raise ValueError
    except (KeyError, OSError, TypeError, ValueError) as exc:
        raise TaskExecutionRefused("contrat de livraison JARVIS invalide") from exc
    return EngineeringDeliveryContract(
        repo_root=repo,
        job_id=job_id,
        idempotency_key=idempotency_key,
        runtime_id=runtime_id,
        runtime_version=runtime_version,
        required_tests=tests,
        acceptance_criteria=criteria,
        commit_message=commit_message,
        digest=digest,
    )


def bind_engineering_contract_to_plan(
    plan: TaskPlan, contract: EngineeringDeliveryContract
) -> TaskPlan:
    """Ajoute au plan les éléments de livraison que l'utilisateur approuve."""

    validation_labels = tuple(" ".join(command) for command in contract.required_tests)
    validation_tools = tuple(
        command[2]
        if command[0] in {"python", "python3"}
        and len(command) > 2
        and command[1] == "-m"
        else command[0]
        for command in contract.required_tests
    )
    criteria = tuple(
        dict.fromkeys(
            (
                contract.approval_marker,
                f"Dépôt borné: {contract.repo_root}",
                f"Commit local JARVIS: {contract.commit_message}",
                "Publication externe: interdite",
                *(f"JARVIS exécute: {command}" for command in validation_labels),
                *contract.acceptance_criteria,
                *plan.success_criteria,
            )
        )
    )[:20]
    deliverables = tuple(
        dict.fromkeys(
            (
                "Modification confinée dans un worktree Git isolé",
                "Reçu de tests JARVIS et commit local",
                *plan.expected_deliverables,
            )
        )
    )[:20]
    tools = tuple(
        dict.fromkeys(
            (contract.runtime_label, *validation_tools, "git", *plan.tools_expected)
        )
    )[:20]
    steps = plan.steps
    if len(steps) < 40:
        steps = (
            *steps,
            PlanStep(
                index=len(steps) + 1,
                title="Valider et finaliser la livraison locale",
                detail=(
                    f"Dépôt {contract.repo_root}; runtime {contract.runtime_label}. "
                    "Le runtime agentique modifie uniquement le worktree. JARVIS "
                    "exécute les validations approuvées, vérifie le manifeste de "
                    "fichiers et crée le commit local ; aucune publication externe."
                ),
                expected_result=(
                    "Tests verts, reçu persistant et commit JARVIS: "
                    f"{contract.commit_message}"
                ),
                tools=("run_tests", "git"),
                permissions=("workspace:write",),
            ),
        )
    return replace(
        plan,
        steps=steps,
        expected_deliverables=deliverables,
        tools_expected=tools,
        success_criteria=criteria,
        digest="",
    )


def ensure_engineering_contract_approved(
    plan: TaskPlan, contract: EngineeringDeliveryContract
) -> None:
    if contract.approval_marker not in plan.success_criteria:
        raise TaskExecutionRefused(
            "le contrat de livraison n'est pas couvert par le plan approuvé"
        )


__all__ = [
    "ENGINEERING_DELIVERY_METADATA_KEY",
    "EngineeringDeliveryContract",
    "bind_engineering_contract_to_plan",
    "build_engineering_delivery_contract",
    "engineering_delivery_contract_from_metadata",
    "ensure_engineering_contract_approved",
]
