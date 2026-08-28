"""Rapport final — écrit par JARVIS, à partir de faits vérifiés.

Le runtime dit ce qu'il croit avoir fait. Le rapport, lui, n'énonce que ce que
JARVIS a pu constater : artefacts effectivement persistés, verdict du
vérificateur, autorisations réellement décidées, activité journalisée. Un
« terminé » annoncé par le fournisseur mais démenti par la vérification
apparaît ici comme un échec, pas comme un succès.

Le rapport est **immuable par version**. Reprendre une tâche produit une
version suivante ; on ne réécrit jamais un compte rendu déjà lu.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Mapping

from .models import (
    ControlTask,
    TaskActivity,
    TaskActivityType,
    TaskPlan,
    TaskReport,
    TaskStatus,
    clamp_text,
    new_id,
    utc_now,
)

#: Correspondance état de tâche → statut de résultat affiché et parlé.
_RESULT_BY_STATUS: dict[TaskStatus, str] = {
    TaskStatus.COMPLETED: "completed",
    TaskStatus.FAILED: "failed",
    TaskStatus.BLOCKED: "blocked",
    TaskStatus.CANCELLED: "cancelled",
}

_RESULT_LABELS = {
    "completed": "Terminée",
    "failed": "Échec",
    "blocked": "Bloquée",
    "cancelled": "Annulée",
    "unknown": "Indéterminée",
}

#: Types d'artefacts qui décrivent un lieu de livraison identifiable.
_DELIVERY_LABELS = {
    "file": "Fichier",
    "directory": "Dossier",
    "commit": "Commit",
    "branch": "Branche",
    "pull_request": "Pull request",
    "report": "Rapport",
    "draft": "Brouillon",
    "receipt": "Reçu",
}


def _bullets(values: Sequence[Any], *, empty: str = "_Aucun._") -> str:
    items = [clamp_text(item, 400) for item in values if str(item).strip()]
    if not items:
        return empty
    return "\n".join(f"- {item}" for item in items)


def result_status_for(task: ControlTask) -> str:
    return _RESULT_BY_STATUS.get(task.status, "unknown")


def _delivery_lines(artifacts: Sequence[Mapping[str, Any]]) -> list[str]:
    lines: list[str] = []
    for artifact in artifacts[:50]:
        kind = str(artifact.get("type") or "artifact")
        label = _DELIVERY_LABELS.get(kind, kind)
        reference = clamp_text(artifact.get("reference") or "", 400)
        if not reference:
            continue
        checksum = str(artifact.get("sha256") or "")
        suffix = f" · `{checksum[:12]}`" if checksum else ""
        lines.append(f"- **{label}** — `{reference}`{suffix}")
        metadata = artifact.get("metadata")
        details = metadata.get("details") if isinstance(metadata, Mapping) else None
        if kind == "jarvis_effect_receipt" and isinstance(details, Mapping):
            commit_sha = str(details.get("commit_sha") or "").strip()
            branch_name = clamp_text(details.get("branch_name") or "", 200)
            if commit_sha:
                lines.append(f"- **Commit** — `{commit_sha}`")
            if branch_name:
                lines.append(f"- **Branche** — `{branch_name}`")
    return lines


def _receipt_test_lines(artifacts: Sequence[Mapping[str, Any]]) -> list[str]:
    lines: list[str] = []
    for artifact in artifacts[:50]:
        if str(artifact.get("type") or "") != "jarvis_test_receipt":
            continue
        metadata = artifact.get("metadata")
        details = metadata.get("details") if isinstance(metadata, Mapping) else None
        validations = details.get("validations") if isinstance(details, Mapping) else None
        if not isinstance(validations, (list, tuple)):
            continue
        for validation in validations[:20]:
            if not isinstance(validation, Mapping):
                continue
            command = validation.get("command")
            if not isinstance(command, (list, tuple)):
                continue
            rendered = clamp_text(" ".join(str(item) for item in command), 350)
            returncode = int(validation.get("returncode", 1))
            lines.append(
                f"`{rendered}` — {'réussi' if returncode == 0 else 'échec'} "
                f"(code {returncode})"
            )
    return lines


def _activity_digest(activities: Sequence[TaskActivity]) -> dict[str, list[str]]:
    buckets: dict[str, list[str]] = {
        "steps": [],
        "files": [],
        "tests": [],
        "errors": [],
    }
    for item in activities:
        if item.event_type in {
            TaskActivityType.PLAN_STEP_STARTED,
            TaskActivityType.PLAN_STEP_COMPLETED,
        }:
            buckets["steps"].append(item.summary)
        elif item.event_type in {
            TaskActivityType.FILE_CHANGED,
            TaskActivityType.FILE_READ,
        }:
            buckets["files"].append(
                f"{item.summary}{f' — `{item.artifact_reference}`' if item.artifact_reference else ''}"
            )
        elif item.event_type in {
            TaskActivityType.TEST_STARTED,
            TaskActivityType.TEST_RESULT,
        }:
            buckets["tests"].append(item.summary)
        elif item.event_type in {
            TaskActivityType.ERROR,
            TaskActivityType.WARNING,
            TaskActivityType.BLOCKED,
        }:
            buckets["errors"].append(item.summary)
    for key, values in buckets.items():
        buckets[key] = list(dict.fromkeys(values))[:25]
    return buckets


def _approval_lines(approvals: Sequence[Mapping[str, Any]]) -> tuple[list[str], list[str], list[str]]:
    requested: list[str] = []
    used: list[str] = []
    refused: list[str] = []
    for approval in approvals[:50]:
        action = clamp_text(approval.get("action") or approval.get("tool") or "", 200)
        if not action:
            continue
        decision = str(approval.get("decision") or "pending")
        requested.append(action)
        if decision == "approved":
            used.append(action)
        elif decision in {"denied", "expired"}:
            refused.append(
                f"{action} ({'refusée' if decision == 'denied' else 'expirée'})"
            )
    return requested, used, refused


def build_report_markdown(
    task: ControlTask,
    *,
    plan: TaskPlan | None,
    activities: Sequence[TaskActivity],
    artifacts: Sequence[Mapping[str, Any]] = (),
    approvals: Sequence[Mapping[str, Any]] = (),
    verification: Mapping[str, Any] | None = None,
    duration_s: float | None = None,
    cost: float | None = None,
    error: str = "",
) -> tuple[str, str, dict[str, Any]]:
    """Retourne ``(markdown, résumé exécutif, données structurées)``."""

    result_status = result_status_for(task)
    label = _RESULT_LABELS.get(result_status, result_status)
    digest = _activity_digest(activities)
    digest["tests"] = list(
        dict.fromkeys([*digest["tests"], *_receipt_test_lines(artifacts)])
    )[:25]
    requested, used, refused = _approval_lines(approvals)
    deliveries = _delivery_lines(artifacts)

    verdict = str((verification or {}).get("verdict") or "")
    verification_summary = clamp_text((verification or {}).get("summary") or "", 400)

    if result_status == "completed":
        executive = f"Tâche terminée. {verification_summary or 'Résultat vérifié.'}"
    elif result_status == "blocked":
        executive = "Tâche bloquée : une intervention est nécessaire pour continuer."
    elif result_status == "failed":
        executive = f"Tâche en échec. {clamp_text(error, 200) or 'Voir les erreurs ci-dessous.'}"
    elif result_status == "cancelled":
        executive = "Tâche annulée à votre demande."
    else:
        executive = "Tâche close sans statut concluant."

    duration_text = (
        f"{int(duration_s // 60)} min {int(duration_s % 60)} s"
        if duration_s is not None
        else "non mesuré"
    )
    cost_text = f"{cost:.4f}" if isinstance(cost, (int, float)) else "non mesuré"

    sections = [
        f"# {task.title}",
        "",
        "## Résumé exécutif",
        executive,
        "",
        f"**Statut final** : {label}",
        "",
        "## Objectif",
        plan.objective if plan else task.description or task.title,
        "",
        "## Ce qui a été fait",
        _bullets(digest["steps"], empty="_Aucune étape journalisée._"),
        "",
        "## Étapes prévues au plan",
        (
            "\n".join(f"{step.index}. {step.title}" for step in plan.steps)
            if plan
            else "_Aucun plan associé._"
        ),
        "",
        "## Résultats et livrables",
        _bullets(
            list(plan.expected_deliverables) if plan else [],
            empty="_Aucun livrable annoncé au plan._",
        ),
        "",
        "## Lieu de livraison",
        "\n".join(deliveries) if deliveries else "_Aucun artefact attesté._",
        "",
        "## Fichiers lus ou modifiés",
        _bullets(digest["files"], empty="_Aucun fichier journalisé._"),
        "",
        "## Tests",
        _bullets(digest["tests"], empty="_Aucun test exécuté._"),
        "",
        "## Vérification",
        (
            f"Verdict : **{verdict or 'non exécutée'}**"
            + (f"\n\n{verification_summary}" if verification_summary else "")
        ),
        "",
        "## Autorisations",
        "**Demandées**",
        _bullets(requested, empty="_Aucune._"),
        "",
        "**Utilisées**",
        _bullets(used, empty="_Aucune._"),
        "",
        "**Refusées ou expirées**",
        _bullets(refused, empty="_Aucune._"),
        "",
        "## Erreurs rencontrées",
        _bullets(
            digest["errors"] + ([clamp_text(error, 400)] if error else []),
            empty="_Aucune._",
        ),
        "",
        "## Limites",
        _bullets(list(plan.known_limits) if plan else [], empty="_Aucune signalée._"),
        "",
        "## Coût et durée",
        f"- Durée totale : {duration_text}",
        f"- Coût estimé : {cost_text}",
        "",
        "## Prochaines actions recommandées",
        _bullets(_next_actions(result_status, refused), empty="_Aucune._"),
    ]

    data: dict[str, Any] = {
        "result_status": result_status,
        "verdict": verdict,
        "deliveries": [
            {
                "type": str(artifact.get("type") or "artifact"),
                "reference": clamp_text(artifact.get("reference") or "", 400),
                "sha256": str(artifact.get("sha256") or ""),
                "details": (
                    dict(artifact["metadata"].get("details") or {})
                    if isinstance(artifact.get("metadata"), Mapping)
                    and isinstance(artifact["metadata"].get("details"), Mapping)
                    else {}
                ),
            }
            for artifact in artifacts[:50]
            if str(artifact.get("reference") or "").strip()
        ],
        "approvals": {
            "requested": requested,
            "used": used,
            "refused": refused,
        },
        "steps": digest["steps"],
        "files": digest["files"],
        "tests": digest["tests"],
        "errors": digest["errors"],
        "duration_s": duration_s,
        "cost": cost,
    }
    return "\n".join(sections), clamp_text(executive, 500), data


def _next_actions(result_status: str, refused: Sequence[str]) -> list[str]:
    actions: list[str] = []
    if result_status == "blocked":
        actions.append("Lever le blocage indiqué, puis relancer la tâche.")
    if result_status == "failed":
        actions.append("Demander une révision du plan avant toute nouvelle tentative.")
    if refused:
        actions.append(
            "Réexaminer les autorisations refusées si l'effet reste souhaité."
        )
    if result_status == "completed":
        actions.append("Contrôler les livrables listés ci-dessus.")
    return actions


def build_report(
    task: ControlTask,
    *,
    version: int,
    plan: TaskPlan | None,
    activities: Sequence[TaskActivity],
    artifacts: Sequence[Mapping[str, Any]] = (),
    approvals: Sequence[Mapping[str, Any]] = (),
    verification: Mapping[str, Any] | None = None,
    duration_s: float | None = None,
    cost: float | None = None,
    error: str = "",
) -> TaskReport:
    markdown, summary, data = build_report_markdown(
        task,
        plan=plan,
        activities=activities,
        artifacts=artifacts,
        approvals=approvals,
        verification=verification,
        duration_s=duration_s,
        cost=cost,
        error=error,
    )
    return TaskReport(
        report_id=new_id("report"),
        task_id=task.task_id,
        version=version,
        result_status=str(data["result_status"]),
        markdown=markdown,
        summary=summary,
        data=data,
        created_at=utc_now(),
    )


__all__ = ["build_report", "build_report_markdown", "result_status_for"]
