"""Vérification déterministe minimale des fins de run agentique.

Le runtime fournit des faits observables (fin de flux et artefacts), mais ne
peut jamais s'auto-attribuer un succès. Ce module transforme ces faits en un
verdict JARVIS explicite et testable.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Protocol, runtime_checkable
from urllib.parse import urlsplit
import uuid

from .models import (
    AgenticRun,
    AgenticRunStatus,
    AgenticRequestCategory,
    Artifact,
    VerificationEvidence,
    VerificationResult,
    VerificationVerdict,
    Verifier,
)


_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_FILE_ARTIFACT_TYPES = frozenset(
    {
        "changed_file",
        "facture",
        "file",
        "invoice",
        "recording",
        "report_file",
        "screenshot",
        "video",
        "video_file",
    }
)
_RECEIPT_ARTIFACT_TYPES = {
    "jarvis_test_receipt": "test",
    "jarvis_effect_receipt": "effect",
}
_BROWSER_OPERATIONS = frozenset({"open", "see", "search"})
_BROWSER_SNAPSHOT_KEYS = frozenset(
    {
        "approval_arguments_sha256",
        "approval_verified",
        "content_sha256",
        "issuer",
        "observed_at",
        "operation",
        "policy_result",
        "run_id",
        "schema_version",
        "snapshot_id",
        "title",
        "url",
    }
)


def _canonical_receipt_bytes(metadata: Mapping[str, Any]) -> bytes:
    return json.dumps(
        dict(metadata),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def build_jarvis_receipt_artifact(
    *,
    run_id: str,
    kind: str,
    subject: str,
    status: str = "passed",
    observed_at: datetime | None = None,
    artifact_id: str | None = None,
    details: Mapping[str, Any] | None = None,
) -> Artifact:
    """Construit un reçu typé que JARVIS doit persister avant vérification."""

    normalized_kind = kind.strip().lower()
    if normalized_kind not in {"test", "effect"}:
        raise ValueError("kind de reçu JARVIS invalide")
    normalized_status = status.strip().lower()
    if normalized_status not in {"passed", "succeeded"}:
        raise ValueError("seul un reçu positif peut établir une preuve")
    timestamp = observed_at or datetime.now(timezone.utc)
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    metadata = {
        "issuer": "jarvis",
        "kind": normalized_kind,
        "status": normalized_status,
        "subject": subject.strip(),
        "observed_at": timestamp.astimezone(timezone.utc).isoformat(),
        "run_id": run_id,
        "details": dict(details or {}),
    }
    if not metadata["subject"]:
        raise ValueError("subject de reçu JARVIS requis")
    encoded = _canonical_receipt_bytes(metadata)
    identifier = artifact_id or f"receipt:{normalized_kind}:{uuid.uuid4()}"
    return Artifact(
        artifact_id=identifier,
        run_id=run_id,
        type=f"jarvis_{normalized_kind}_receipt",
        reference=f"jarvis://receipts/{identifier}",
        sha256=hashlib.sha256(encoded).hexdigest(),
        size_bytes=len(encoded),
        metadata=metadata,
    )


def _verify_file_artifact(run: AgenticRun, artifact: Artifact) -> tuple[str, str]:
    if artifact.sha256 is None or artifact.size_bytes is None:
        return "insufficient", "digest et taille de fichier obligatoires"
    if not run.workspace:
        return "insufficient", "workspace JARVIS absent"
    reference = artifact.reference.strip()
    if "://" in reference:
        return "insufficient", "référence de fichier non locale"
    workspace_input = Path(run.workspace).expanduser()
    if workspace_input.is_symlink():
        return "invalid", "workspace symbolique refusé"
    try:
        workspace = workspace_input.resolve(strict=True)
    except OSError:
        return "insufficient", "workspace inexistant"
    if not workspace.is_dir():
        return "insufficient", "workspace non répertoire"
    candidate = Path(reference)
    if ".." in candidate.parts:
        return "invalid", "traversée de répertoire refusée"
    unresolved = candidate if candidate.is_absolute() else workspace / candidate
    current = unresolved
    while current != workspace and workspace in current.parents:
        if current.is_symlink():
            return "invalid", "lien symbolique refusé"
        current = current.parent
    try:
        resolved = unresolved.resolve(strict=True)
        resolved.relative_to(workspace)
    except (OSError, ValueError):
        return "insufficient", "chemin inexistant ou hors workspace"
    if not resolved.is_file():
        return "insufficient", "preuve fichier non régulière"
    stat = resolved.stat()
    if stat.st_size != artifact.size_bytes:
        return "invalid", "taille de fichier différente du manifeste"
    hasher = hashlib.sha256()
    with resolved.open("rb") as handle:
        for chunk in iter(lambda: handle.read(64 * 1024), b""):
            hasher.update(chunk)
    if hasher.hexdigest() != artifact.sha256.lower():
        return "invalid", "digest de fichier différent du manifeste"
    return "valid", "fichier local vérifié par JARVIS"


def _verify_receipt_artifact(
    artifact: Artifact,
    *,
    trusted_artifact_ids: frozenset[str],
) -> tuple[str, str]:
    if artifact.artifact_id not in trusted_artifact_ids:
        return "insufficient", "reçu non persisté par JARVIS"
    if artifact.sha256 is None or artifact.size_bytes is None:
        return "insufficient", "digest et taille du reçu obligatoires"
    expected_kind = _RECEIPT_ARTIFACT_TYPES[artifact.type]
    metadata = dict(artifact.metadata)
    if (
        metadata.get("issuer") != "jarvis"
        or metadata.get("kind") != expected_kind
        or metadata.get("status") not in {"passed", "succeeded"}
        or metadata.get("run_id") != artifact.run_id
        or not isinstance(metadata.get("subject"), str)
        or not metadata["subject"].strip()
        or not isinstance(metadata.get("observed_at"), str)
        or not artifact.reference.startswith("jarvis://receipts/")
    ):
        return "invalid", "schéma du reçu JARVIS invalide"
    try:
        observed = datetime.fromisoformat(
            metadata["observed_at"].replace("Z", "+00:00")
        )
        encoded = _canonical_receipt_bytes(metadata)
    except (TypeError, ValueError):
        return "invalid", "reçu JARVIS non canonique"
    if observed.tzinfo is None:
        return "invalid", "horodatage du reçu sans fuseau"
    if len(encoded) != artifact.size_bytes:
        return "invalid", "taille du reçu différente du manifeste"
    if hashlib.sha256(encoded).hexdigest() != artifact.sha256.lower():
        return "invalid", "digest du reçu différent du manifeste"
    return "valid", f"reçu JARVIS {expected_kind} vérifié"


def _verify_browser_snapshot_artifact(
    run: AgenticRun, artifact: Artifact
) -> tuple[str, str]:
    """Valide une preuve navigateur émise par le parent, sans contenu de page."""

    if artifact.sha256 is None or artifact.size_bytes is None:
        return "insufficient", "digest et taille du snapshot navigateur obligatoires"
    metadata = dict(artifact.metadata)
    if set(metadata) != _BROWSER_SNAPSHOT_KEYS:
        return "invalid", "champs du snapshot navigateur invalides"
    snapshot_id = metadata.get("snapshot_id")
    if (
        metadata.get("schema_version") != 1
        or metadata.get("issuer") != "jarvis_browser"
        or metadata.get("run_id") != run.run_id
        or metadata.get("policy_result") != "allowed"
        or metadata.get("approval_verified") is not True
        or metadata.get("operation") not in _BROWSER_OPERATIONS
        or not isinstance(snapshot_id, str)
        or not re.fullmatch(r"[0-9a-f]{32}", snapshot_id)
        or not isinstance(metadata.get("content_sha256"), str)
        or not _SHA256_RE.fullmatch(metadata["content_sha256"])
        or not isinstance(metadata.get("approval_arguments_sha256"), str)
        or not _SHA256_RE.fullmatch(metadata["approval_arguments_sha256"])
        or not isinstance(metadata.get("title"), str)
        or len(metadata["title"]) > 200
        or "\x00" in metadata["title"]
    ):
        return "invalid", "schéma du snapshot navigateur invalide"
    if artifact.reference != f"jarvis://browser/{run.run_id}/{snapshot_id}":
        return "invalid", "référence du snapshot navigateur invalide"
    try:
        observed = datetime.fromisoformat(
            str(metadata["observed_at"]).replace("Z", "+00:00")
        )
        parsed = urlsplit(str(metadata["url"]))
        port = parsed.port
        encoded = _canonical_receipt_bytes(metadata)
    except (TypeError, ValueError):
        return "invalid", "snapshot navigateur non canonique"
    if observed.tzinfo is None or observed.utcoffset() is None:
        return "invalid", "horodatage du snapshot navigateur invalide"
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
        or parsed.path != "/"
        or parsed.query
        or parsed.fragment
    ):
        return "invalid", "origine du snapshot navigateur invalide"
    if artifact.size_bytes != len(encoded):
        return "invalid", "taille du snapshot navigateur différente"
    if artifact.sha256.lower() != hashlib.sha256(encoded).hexdigest():
        return "invalid", "digest du snapshot navigateur différent"
    return "valid", "snapshot navigateur approuvé et vérifié par JARVIS"


@runtime_checkable
class CompletionVerifier(Protocol):
    """Contrat d'un vérificateur JARVIS de fin de run."""

    @property
    def descriptor(self) -> Verifier: ...

    def verify(
        self,
        *,
        run: AgenticRun,
        artifacts: Sequence[object],
        collection_error_code: str | None = None,
    ) -> VerificationResult: ...


@dataclass(frozen=True)
class ArtifactFamilyVerifier:
    """Renforce le baseline par la présence d'une preuve propre à une famille."""

    descriptor: Verifier
    artifact_types: frozenset[str]

    def verify(
        self,
        *,
        run: AgenticRun,
        artifacts: Sequence[object],
        collection_error_code: str | None = None,
    ) -> VerificationResult:
        matched = sum(
            isinstance(artifact, Artifact) and artifact.type in self.artifact_types
            for artifact in artifacts
        )
        passed = collection_error_code is None and matched > 0
        return VerificationResult(
            verdict=(
                VerificationVerdict.PASS if passed else VerificationVerdict.BLOCKED
            ),
            verifier=self.descriptor.name,
            summary=(
                "Preuve spécialisée présente."
                if passed
                else "Vérification bloquée : preuve spécialisée absente."
            ),
            evidence=(
                VerificationEvidence(
                    check="artifact_family",
                    passed=passed,
                    summary=(
                        "Famille d'artefact reconnue."
                        if passed
                        else "Aucun artefact reconnu pour cette famille."
                    ),
                    metadata={"matched_count": matched},
                ),
            ),
        )


@dataclass(frozen=True)
class FailClosedVerifier:
    descriptor: Verifier = Verifier(
        name="jarvis.verifier.unregistered.v1",
        task_types=("*",),
        checks=("registered_verifier",),
        description="Bloque toute famille sans vérificateur enregistré.",
    )

    def verify(
        self,
        *,
        run: AgenticRun,
        artifacts: Sequence[object],
        collection_error_code: str | None = None,
    ) -> VerificationResult:
        return VerificationResult(
            verdict=VerificationVerdict.BLOCKED,
            verifier=self.descriptor.name,
            summary="Vérification bloquée : aucun vérificateur enregistré.",
            evidence=(
                VerificationEvidence(
                    check="registered_verifier",
                    passed=False,
                    summary="La famille de tâche n'est pas enregistrée.",
                ),
            ),
        )


class VerifierRegistry:
    """Sélectionne un vérificateur par type explicite, artefact, puis catégorie."""

    def __init__(
        self,
        verifiers: Sequence[CompletionVerifier],
        *,
        wildcard: CompletionVerifier,
        artifact_families: Mapping[str, str] | None = None,
    ) -> None:
        if "*" not in wildcard.descriptor.task_types:
            raise ValueError("le vérificateur wildcard doit déclarer '*'")
        entries: dict[str, CompletionVerifier] = {}
        for verifier in verifiers:
            for task_type in verifier.descriptor.task_types:
                key = task_type.strip().lower()
                if not key or key == "*" or key in entries:
                    raise ValueError(
                        f"type de vérificateur invalide ou dupliqué: {key}"
                    )
                entries[key] = verifier
        self._entries = entries
        self._wildcard = wildcard
        self._artifact_families = {
            key.strip().lower(): value.strip().lower()
            for key, value in dict(artifact_families or {}).items()
        }

    def resolve(
        self,
        *,
        run: AgenticRun,
        artifacts: Sequence[object] = (),
    ) -> CompletionVerifier:
        for context_key in (
            "verification_type",
            "verification_category",
            "task_type",
        ):
            value = run.selected_context.get(context_key)
            if isinstance(value, str) and value.strip():
                return self._entries.get(value.strip().lower(), self._wildcard)
        inferred = {
            self._artifact_families[artifact.type.lower()]
            for artifact in artifacts
            if isinstance(artifact, Artifact)
            and artifact.type.lower() in self._artifact_families
        }
        if len(inferred) == 1:
            return self._entries.get(inferred.pop(), self._wildcard)
        return self._entries.get(run.category.value.lower(), self._wildcard)


@dataclass(frozen=True)
class DeterministicRuntimeVerifier:
    """Valide le contrat de fin et le manifeste d'artefacts sans effet de bord."""

    descriptor: Verifier = Verifier(
        name="jarvis.runtime_contract.v1",
        task_types=("*",),
        checks=(
            "runtime_completion_observed",
            "artifact_collection",
            "artifact_contract",
            "artifact_budget",
            "structured_proof",
        ),
        description="Vérification générique et déterministe d'une fin de runtime.",
    )

    def verify(
        self,
        *,
        run: AgenticRun,
        artifacts: Sequence[object],
        collection_error_code: str | None = None,
        trusted_artifact_ids: frozenset[str] = frozenset(),
    ) -> VerificationResult:
        completion_observed = run.status in {
            AgenticRunStatus.VERIFYING,
            AgenticRunStatus.REVIEWING,
        }
        evidence: list[VerificationEvidence] = [
            VerificationEvidence(
                check="runtime_completion_observed",
                passed=completion_observed,
                summary=(
                    "Fin du runtime observée par JARVIS."
                    if completion_observed
                    else "Aucune fin de runtime vérifiable."
                ),
            )
        ]
        if not completion_observed:
            return VerificationResult(
                verdict=VerificationVerdict.FAIL,
                verifier=self.descriptor.name,
                summary="Le contrat de fin du runtime n'est pas satisfait.",
                evidence=tuple(evidence),
            )

        collection_ok = collection_error_code is None and bool(artifacts)
        effective_collection_error = collection_error_code or (
            None if artifacts else "artifact_manifest_empty"
        )
        evidence.append(
            VerificationEvidence(
                check="artifact_collection",
                passed=collection_ok,
                summary=(
                    "Manifeste d'artefacts collecté."
                    if collection_ok
                    else "Manifeste d'artefacts indisponible."
                ),
                metadata=(
                    {"artifact_count": len(artifacts)}
                    if collection_ok
                    else {"error_code": effective_collection_error}
                ),
            )
        )
        if not collection_ok:
            return VerificationResult(
                verdict=VerificationVerdict.BLOCKED,
                verifier=self.descriptor.name,
                summary="Vérification bloquée : preuves d'artefacts indisponibles.",
                evidence=tuple(evidence),
            )

        seen_ids: set[str] = set()
        structural_violations = 0
        tampered = 0
        insufficient = 0
        validated = 0
        reliable = 0
        file_evidence = 0
        receipt_evidence = 0
        browser_evidence = 0
        effect_receipts = 0
        test_receipts = 0
        narrative_only = 0
        known_bytes = 0
        digests = 0
        for candidate in artifacts:
            if not isinstance(candidate, Artifact):
                structural_violations += 1
                continue
            identifier = candidate.artifact_id.strip()
            structurally_valid = bool(
                identifier
                and candidate.run_id == run.run_id
                and candidate.type.strip()
                and candidate.reference.strip()
                and identifier not in seen_ids
            )
            seen_ids.add(identifier)
            if candidate.sha256 is not None:
                digests += 1
                structurally_valid = structurally_valid and bool(
                    _SHA256_RE.fullmatch(candidate.sha256)
                )
            if candidate.size_bytes is not None:
                known_bytes += candidate.size_bytes
                structurally_valid = (
                    structurally_valid
                    and candidate.size_bytes <= run.budget.max_artifact_bytes
                )
            if not structurally_valid:
                structural_violations += 1
                continue
            validated += 1
            if candidate.type == "browser_snapshot":
                outcome, _reason = _verify_browser_snapshot_artifact(run, candidate)
                browser_evidence += outcome == "valid"
                receipt_evidence += outcome == "valid"
            elif candidate.type in _FILE_ARTIFACT_TYPES:
                outcome, _reason = _verify_file_artifact(run, candidate)
                file_evidence += outcome == "valid"
            elif candidate.type in _RECEIPT_ARTIFACT_TYPES:
                outcome, _reason = _verify_receipt_artifact(
                    candidate,
                    trusted_artifact_ids=trusted_artifact_ids,
                )
                receipt_evidence += outcome == "valid"
                if outcome == "valid" and candidate.type == "jarvis_effect_receipt":
                    effect_receipts += 1
                if outcome == "valid" and candidate.type == "jarvis_test_receipt":
                    test_receipts += 1
            else:
                outcome = "narrative"
                narrative_only += 1
            if outcome == "valid":
                reliable += 1
            elif outcome == "invalid":
                tampered += 1
            elif outcome == "insufficient":
                insufficient += 1

        contract_ok = structural_violations == 0 and tampered == 0
        evidence.append(
            VerificationEvidence(
                check="artifact_contract",
                passed=contract_ok,
                summary=(
                    "Contrat des artefacts valide."
                    if contract_ok
                    else "Contrat des artefacts invalide."
                ),
                metadata={
                    "artifact_count": len(artifacts),
                    "validated_count": validated,
                    "violation_count": structural_violations,
                    "tampered_count": tampered,
                    "insufficient_count": insufficient,
                    "digest_count": digests,
                },
            )
        )
        budget_ok = known_bytes <= run.budget.max_artifact_bytes
        evidence.append(
            VerificationEvidence(
                check="artifact_budget",
                passed=budget_ok,
                summary=(
                    "Budget d'artefacts respecté."
                    if budget_ok
                    else "Budget d'artefacts dépassé."
                ),
                metadata={
                    "known_bytes": known_bytes,
                    "max_artifact_bytes": run.budget.max_artifact_bytes,
                },
            )
        )
        effect_receipt_required = run.category in {
            AgenticRequestCategory.AGENTIC_EXTERNAL_EFFECT,
            AgenticRequestCategory.AGENTIC_HIGH_RISK,
        }
        test_receipt_required = bool(run.selected_context.get("jarvis_owns_delivery"))
        structured_ok = (
            reliable > 0
            and insufficient == 0
            and (not effect_receipt_required or effect_receipts > 0)
            and (not test_receipt_required or test_receipts > 0)
        )
        evidence.append(
            VerificationEvidence(
                check="structured_proof",
                passed=structured_ok,
                summary=(
                    "Preuves structurées et observables validées par JARVIS."
                    if structured_ok
                    else "Preuves structurées fiables insuffisantes."
                ),
                metadata={
                    "reliable_count": reliable,
                    "file_evidence_count": file_evidence,
                    "browser_evidence_count": browser_evidence,
                    "receipt_evidence_count": receipt_evidence,
                    "effect_receipt_count": effect_receipts,
                    "test_receipt_count": test_receipts,
                    "effect_receipt_required": effect_receipt_required,
                    "test_receipt_required": test_receipt_required,
                    "narrative_only_count": narrative_only,
                    "insufficient_count": insufficient,
                },
            )
        )
        if not contract_ok or not budget_ok:
            return VerificationResult(
                verdict=VerificationVerdict.FAIL,
                verifier=self.descriptor.name,
                summary="La vérification déterministe des artefacts a échoué.",
                evidence=tuple(evidence),
            )
        if not structured_ok:
            return VerificationResult(
                verdict=VerificationVerdict.BLOCKED,
                verifier=self.descriptor.name,
                summary="Vérification bloquée : preuves structurées fiables insuffisantes.",
                evidence=tuple(evidence),
            )
        return VerificationResult(
            verdict=VerificationVerdict.PASS,
            verifier=self.descriptor.name,
            summary="Vérification déterministe réussie.",
            evidence=tuple(evidence),
        )


DEFAULT_RUNTIME_VERIFIER = DeterministicRuntimeVerifier()
FAIL_CLOSED_VERIFIER = FailClosedVerifier()
DEFAULT_VERIFIER_REGISTRY = VerifierRegistry(
    (
        ArtifactFamilyVerifier(
            descriptor=Verifier(
                name="jarvis.verifier.code.v1",
                task_types=("code",),
                checks=("artifact_family",),
            ),
            artifact_types=frozenset({"changed_file", "file", "report_file"}),
        ),
        ArtifactFamilyVerifier(
            descriptor=Verifier(
                name="jarvis.verifier.email_invoice.v1",
                task_types=(
                    "email",
                    "email_facture",
                    "email_invoice",
                    "facture",
                    "invoice",
                ),
                checks=("artifact_family",),
            ),
            artifact_types=frozenset(
                {"email_receipt", "facture", "invoice", "invoice_receipt"}
            ),
        ),
        ArtifactFamilyVerifier(
            descriptor=Verifier(
                name="jarvis.verifier.browser.v1",
                task_types=("browser",),
                checks=("artifact_family",),
            ),
            artifact_types=frozenset(
                {"browser_receipt", "browser_snapshot", "screenshot"}
            ),
        ),
        ArtifactFamilyVerifier(
            descriptor=Verifier(
                name="jarvis.verifier.obs_video.v1",
                task_types=("obs", "video", "obs_video"),
                checks=("artifact_family",),
            ),
            artifact_types=frozenset(
                {"obs_receipt", "recording", "video", "video_file"}
            ),
        ),
    ),
    wildcard=FAIL_CLOSED_VERIFIER,
    artifact_families={
        "changed_file": "code",
        "file": "code",
        "report_file": "code",
        "email_receipt": "email",
        "facture": "facture",
        "invoice": "invoice",
        "invoice_receipt": "invoice",
        "browser_receipt": "browser",
        "browser_snapshot": "browser",
        "screenshot": "browser",
        "obs_receipt": "obs",
        "recording": "video",
        "video": "video",
        "video_file": "video",
    },
)


def verify_runtime_completion(
    *,
    run: AgenticRun,
    artifacts: Sequence[object],
    collection_error_code: str | None = None,
    trusted_artifact_ids: frozenset[str] = frozenset(),
) -> VerificationResult:
    """Façade fonctionnelle stable du vérificateur générique."""

    return DEFAULT_RUNTIME_VERIFIER.verify(
        run=run,
        artifacts=artifacts,
        collection_error_code=collection_error_code,
        trusted_artifact_ids=trusted_artifact_ids,
    )


__all__ = [
    "ArtifactFamilyVerifier",
    "CompletionVerifier",
    "DEFAULT_VERIFIER_REGISTRY",
    "DEFAULT_RUNTIME_VERIFIER",
    "DeterministicRuntimeVerifier",
    "FAIL_CLOSED_VERIFIER",
    "FailClosedVerifier",
    "VerifierRegistry",
    "build_jarvis_receipt_artifact",
    "verify_runtime_completion",
]
