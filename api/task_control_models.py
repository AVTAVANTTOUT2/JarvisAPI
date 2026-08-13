"""Contrats d'entrée du pilotage de tâches — stricts, bornés, sans champ libre.

``extra="forbid"`` partout : un client qui envoie un champ inconnu reçoit une
erreur plutôt qu'un silence. C'est ce qui empêche un client d'inventer sa
propre autorisation en glissant une clé que le serveur ignorerait poliment.

Aucun modèle ne laisse le client déclarer un état, un digest de plan, une
permission ou une décision d'exécution : ces valeurs viennent du serveur.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_TITLE = 300
MAX_DESCRIPTION = 8_000
MAX_COMMENT = 4_000


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class TaskControlCreateRequest(_Strict):
    """Création manuelle ou par un connecteur autorisé.

    Le client ne choisit pas l'état : toute tâche naît `created` et part en
    planification. Il n'existe pas de champ pour demander un démarrage.
    """

    title: str = Field(min_length=1, max_length=MAX_TITLE)
    description: str = Field(default="", max_length=MAX_DESCRIPTION)
    priority: Literal["high", "medium", "low"] = "medium"
    source_type: Literal[
        "manual", "user_request", "message", "email", "scheduler"
    ] = "manual"
    source_channel: Literal[
        "macos", "web", "voice", "mobile", "imessage", "email", "api", "scheduler"
    ] = "api"
    project_id: str | None = Field(default=None, max_length=128)
    conversation_id: str | None = Field(default=None, max_length=128)
    due_at: datetime | None = None
    comment: str = Field(default="", max_length=MAX_COMMENT)
    autoplan: bool = True

    @field_validator("project_id", "conversation_id")
    @classmethod
    def _identifier(cls, value: str | None) -> str | None:
        if value is None:
            return None
        candidate = value.strip()
        if not candidate:
            return None
        if not candidate.replace("-", "").replace("_", "").replace(":", "").isalnum():
            raise ValueError("identifiant invalide")
        return candidate


class TaskControlPatchRequest(_Strict):
    """Édition des seuls champs descriptifs.

    `status`, `approved_plan_version` et `approved_plan_digest` sont
    volontairement absents : les faire figurer ici donnerait au client le
    pouvoir de se déclarer approuvé.
    """

    title: str | None = Field(default=None, min_length=1, max_length=MAX_TITLE)
    description: str | None = Field(default=None, max_length=MAX_DESCRIPTION)
    priority: Literal["high", "medium", "low"] | None = None
    due_at: datetime | None = None
    project_id: str | None = Field(default=None, max_length=128)


class PlanDecisionRequest(_Strict):
    decision: Literal["approved", "rejected", "revision_requested"]
    comment: str = Field(default="", max_length=MAX_COMMENT)
    #: Digest affiché par le client. Fourni, il doit correspondre au plan en
    #: base — sinon l'écran validait un autre texte que celui qui s'exécutera.
    plan_digest: str | None = Field(default=None, min_length=64, max_length=64)

    @field_validator("plan_digest")
    @classmethod
    def _hex(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if any(char not in "0123456789abcdefABCDEF" for char in value):
            raise ValueError("digest invalide")
        return value.lower()


class ApprovalDecisionRequest(_Strict):
    decision: Literal["approved", "denied"]


class TaskCommentRequest(_Strict):
    body: str = Field(min_length=1, max_length=MAX_COMMENT)
    #: Une précision qui change le périmètre ne doit pas étendre en silence ce
    #: qui a été autorisé : le client demande explicitement la révision.
    request_plan_revision: bool = False


class CandidateDecisionRequest(_Strict):
    decision: Literal["accepted", "ignored", "rejected", "false_positive", "merged"]
    merge_into: str | None = Field(default=None, max_length=128)


class TaskCancelRequest(_Strict):
    reason: str = Field(default="", max_length=500)


class TaskPlanRequest(_Strict):
    """Relance une planification (nouvelle version)."""

    comment: str = Field(default="", max_length=MAX_COMMENT)


__all__ = [
    "ApprovalDecisionRequest",
    "CandidateDecisionRequest",
    "PlanDecisionRequest",
    "TaskCancelRequest",
    "TaskCommentRequest",
    "TaskControlCreateRequest",
    "TaskControlPatchRequest",
    "TaskPlanRequest",
]
