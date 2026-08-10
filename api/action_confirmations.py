"""Frontière serveur des propositions d'action à confirmer."""

from __future__ import annotations

from copy import deepcopy
import re
import secrets
import threading
import time

import config


class ProposalError(ValueError):
    """Une proposition est absente, expirée ou hors de la session attendue."""


_pending_proposals: dict[str, dict] = {}
_proposal_by_session_conversation: dict[tuple[str, int], str] = {}
_proposal_lock = threading.Lock()

_CONFIRMATION_PHRASES = frozenset({
    "oui",
    "vas-y",
    "vas y",
    "fais-le",
    "fais le",
    "ok",
    "okay",
    "ok lance",
    "d'accord",
    "go",
    "lance",
    "confirme",
    "démarre",
    "demarre",
    "execute",
    "exécute",
    "yes",
    "pourquoi pas",
    "je veux bien",
    "allez",
    "fonce",
    "oui vas-y",
    "oui vas y",
    "oui fais le",
    "oui stp",
    "oui merci",
})
_IMPERATIVE_CONFIRMATION_PHRASES = frozenset({
    "vas-y",
    "vas y",
    "fais-le",
    "fais le",
    "go",
    "lance",
    "execute",
    "exécute",
    "allez",
    "fonce",
    "oui vas-y",
    "oui vas y",
    "oui fais le",
})
_NEGATION_RE = re.compile(
    r"(?:^|[\s'-])(pas|jamais|non|annule|annuler|refuse|refuser|stop)(?:$|[\s'-])",
    re.IGNORECASE,
)
_PROPOSAL_ID_RE = re.compile(r"^[A-Za-z0-9_-]{43}$")


def _normalise_confirmation(text: str) -> str:
    # Les moteurs STT ponctuent naturellement « Oui, vas-y. ». La ponctuation
    # ne doit pas transformer une confirmation exacte en nouvelle intention.
    without_punctuation = re.sub(r"[.!?,;:]+", " ", str(text or "").strip().lower())
    return re.sub(r"\s+", " ", without_punctuation).strip()


def is_exact_confirmation(text: str) -> bool:
    """Vrai uniquement pour une phrase entière autorisée et sans négation."""
    normalised = _normalise_confirmation(text)
    return bool(normalised and not _NEGATION_RE.search(normalised)) and (
        normalised in _CONFIRMATION_PHRASES
    )


def is_imperative_confirmation(text: str) -> bool:
    """Vrai lorsque la phrase demande explicitement de lancer une action."""
    normalised = _normalise_confirmation(text)
    return normalised in _IMPERATIVE_CONFIRMATION_PHRASES


def unmatched_confirmation_reply() -> dict:
    """Réponse à une confirmation impérative sans proposition consommable.

    Une confirmation n'est jamais une nouvelle intention : sans proposition en
    attente, il faut le dire plutôt que laisser un modèle inventer une action
    puis en annoncer mensongèrement la réussite.
    """
    display_text = (
        "Je n’ai aucune action en attente à confirmer, Monsieur. "
        "Précisez l’action souhaitée."
    )
    return {
        "text": display_text,
        "emotion": "neutral",
        "action": None,
        "action_result": {
            "ok": False,
            "error": "no_pending_action",
            "message": display_text,
        },
        "agent": "orchestrator",
        "model": None,
        "cost": 0.0,
        "empty_response_cause": None,
    }


def is_valid_proposal_id(value: object) -> bool:
    """Valide le format exact produit par ``secrets.token_urlsafe(32)``."""
    return isinstance(value, str) and bool(_PROPOSAL_ID_RE.fullmatch(value))


def _revoke_action_plan(action: dict) -> bool:
    """Révoque le plan serveur adossé à une proposition abandonnée.

    Une proposition remplacée, annulée ou expirée ne doit laisser derrière elle
    aucun plan consommable : ni commande shell, ni panier prêt à payer.
    """
    revoked = False
    shell_plan_id = str(action.get("shell_plan_id") or "").strip()
    if shell_plan_id:
        from integrations.shell_safety import revoke_shell_plan

        revoked = revoke_shell_plan(shell_plan_id) or revoked

    if str(action.get("type") or "") == "food_order":
        food_plan_id = str(action.get("plan_id") or "").strip()
        if food_plan_id:
            from integrations.uber_eats import revoke_order_plan

            revoked = revoke_order_plan(food_plan_id) or revoked

    return revoked


def _drop_locked(proposal_id: str) -> dict | None:
    proposal = _pending_proposals.pop(proposal_id, None)
    if proposal:
        key = (proposal["session_id"], proposal["conversation_id"])
        if _proposal_by_session_conversation.get(key) == proposal_id:
            _proposal_by_session_conversation.pop(key, None)
    return proposal


def _public_proposal(proposal: dict) -> dict:
    action = deepcopy(proposal["action"])
    action.pop("confirmed", None)
    return {**action, "proposal_id": proposal["proposal_id"]}


def store_pending_proposal(
    action: dict,
    *,
    conversation_id: int,
    session_id: str,
) -> dict:
    """Fige une action serveur et retourne sa vue affichable au client."""
    action_type = str(action.get("type") or "").strip()
    session_id = str(session_id or "").strip()
    if not action_type or not session_id:
        raise ProposalError("action ou session de confirmation invalide")

    proposal_id = secrets.token_urlsafe(32)
    proposal = {
        "proposal_id": proposal_id,
        "session_id": session_id,
        "conversation_id": int(conversation_id),
        "action_type": action_type,
        "action": deepcopy(action),
        "expires_at": time.monotonic()
        + max(1, int(getattr(config, "LLM_SHELL_PLAN_TTL_SECONDS", 600))),
    }
    previous: dict | None = None
    key = (session_id, int(conversation_id))
    with _proposal_lock:
        previous_id = _proposal_by_session_conversation.get(key)
        if previous_id:
            previous = _drop_locked(previous_id)
        _pending_proposals[proposal_id] = proposal
        _proposal_by_session_conversation[key] = proposal_id
    if previous:
        _revoke_action_plan(previous["action"])
    return _public_proposal(proposal)


def peek_pending_proposal(*, conversation_id: int, session_id: str) -> dict | None:
    """Retourne la proposition courante de cette session sans la consommer."""
    expired: dict | None = None
    key = (str(session_id), int(conversation_id))
    with _proposal_lock:
        proposal_id = _proposal_by_session_conversation.get(key)
        proposal = _pending_proposals.get(proposal_id or "")
        if proposal and proposal["expires_at"] <= time.monotonic():
            expired = _drop_locked(proposal["proposal_id"])
            proposal = None
        public = _public_proposal(proposal) if proposal else None
    if expired:
        _revoke_action_plan(expired["action"])
    return public


def consume_pending_proposal(
    proposal_id: str,
    *,
    conversation_id: int,
    session_id: str,
) -> dict:
    """Consomme atomiquement l'action exacte liée à l'id/session/conversation."""
    proposal_id = str(proposal_id or "").strip()
    expired: dict | None = None
    with _proposal_lock:
        proposal = _pending_proposals.get(proposal_id)
        if not proposal:
            raise ProposalError("proposition inconnue, expirée ou déjà utilisée")
        if (
            proposal["session_id"] != str(session_id)
            or proposal["conversation_id"] != int(conversation_id)
        ):
            raise ProposalError("proposition non liée à cette session ou conversation")
        if proposal["expires_at"] <= time.monotonic():
            expired = _drop_locked(proposal_id)
        else:
            proposal = _drop_locked(proposal_id)
    if expired:
        _revoke_action_plan(expired["action"])
        raise ProposalError("proposition inconnue, expirée ou déjà utilisée")
    if not proposal:
        raise ProposalError("proposition inconnue, expirée ou déjà utilisée")
    return {**deepcopy(proposal["action"]), "confirmed": True}


def consume_text_confirmation(
    text: str,
    *,
    conversation_id: int,
    session_id: str,
) -> dict | None:
    """Confirme exactement, sinon révoque atomiquement la proposition courante."""
    key = (str(session_id), int(conversation_id))
    rejected: dict | None = None
    with _proposal_lock:
        proposal_id = _proposal_by_session_conversation.get(key)
        proposal = _pending_proposals.get(proposal_id or "")
        if not proposal:
            return None
        if (
            proposal["expires_at"] <= time.monotonic()
            or not is_exact_confirmation(text)
        ):
            rejected = _drop_locked(proposal["proposal_id"])
            proposal = None
        else:
            proposal = _drop_locked(proposal["proposal_id"])
    if rejected:
        _revoke_action_plan(rejected["action"])
        return None
    if not proposal:
        return None
    return {**deepcopy(proposal["action"]), "confirmed": True}


def cancel_pending_proposal(
    proposal_id: str,
    *,
    conversation_id: int,
    session_id: str,
) -> bool:
    """Révoque la proposition exacte et son éventuel plan shell."""
    with _proposal_lock:
        proposal = _pending_proposals.get(str(proposal_id or ""))
        if not proposal or (
            proposal["session_id"] != str(session_id)
            or proposal["conversation_id"] != int(conversation_id)
        ):
            return False
        proposal = _drop_locked(proposal["proposal_id"])
    if not proposal:
        return False
    _revoke_action_plan(proposal["action"])
    return True


def reset_pending_proposals_for_tests() -> None:
    """Vide le registre et révoque ses plans. Réservé aux tests."""
    with _proposal_lock:
        proposals = list(_pending_proposals.values())
        _pending_proposals.clear()
        _proposal_by_session_conversation.clear()
    for proposal in proposals:
        _revoke_action_plan(proposal["action"])
