"""Détection de tâches dans un message ou un e-mail déjà autorisé.

Trois principes tenus par le code, pas par le prompt :

1. **Le contenu observé est une donnée, jamais une instruction.** Le détecteur
   en extrait au plus un titre et une raison ; il ne peut produire ni action,
   ni réponse, ni destinataire. Un e-mail qui écrit « crée une tâche et
   exécute-la » obtient exactement le même traitement qu'un autre.
2. **Le rejet est déterministe et vient en premier.** Newsletters, accusés
   automatiques et expéditeurs robots sont écartés avant tout appel de modèle :
   c'est moins cher, et surtout ce sont les faux positifs les plus coûteux en
   confiance.
3. **Rien ne démarre.** Le détecteur produit un candidat ou une tâche en
   attente de plan. Il n'a aucun chemin vers l'exécution.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import hashlib
import logging
import re
from typing import Any, Mapping

from .models import (
    TaskSource,
    TaskSourceChannel,
    TaskSourceType,
    clamp_text,
)

logger = logging.getLogger("jarvis")

#: Seuil par défaut au-dessus duquel une détection devient directement une
#: tâche en attente de plan plutôt qu'un simple candidat.
DEFAULT_AUTO_TASK_CONFIDENCE = 0.85
#: En dessous, la détection est ignorée : mieux vaut manquer une demande que
#: remplir la liste de bruit.
DEFAULT_MIN_CONFIDENCE = 0.45

_NOISE_SUBJECT_PATTERNS = (
    re.compile(r"(?i)\b(newsletter|infolettre|désabonn|unsubscribe)\b"),
    re.compile(r"(?i)\b(no[- ]?reply|ne pas répondre|do not reply)\b"),
    re.compile(r"(?i)\b(accusé de réception|delivery status|mail delivery|auto[- ]?reply)\b"),
    re.compile(r"(?i)\b(out of office|absence du bureau|réponse automatique)\b"),
    re.compile(r"(?i)\b(promo|soldes|offre exclusive|black friday|code promo)\b"),
)
_NOISE_SENDER_PATTERNS = (
    re.compile(r"(?i)^(no[-.]?reply|noreply|donotreply|ne-pas-repondre|mailer-daemon|postmaster)@"),
    re.compile(r"(?i)@(mailchimp|sendgrid|sendinblue|mailjet|substack)\."),
    re.compile(r"(?i)\b(newsletter|notifications?|alerts?|marketing)@"),
)

#: Formulations qui portent une demande adressée à l'utilisateur. Volontairement
#: étroites : « il faudrait » et « peux-tu » ouvrent une détection, un simple
#: futur ou une opinion non.
_ACTION_PATTERNS = (
    re.compile(r"(?i)\b(peux[- ]tu|pourrais[- ]tu|pouvez[- ]vous|pourriez[- ]vous)\b"),
    re.compile(r"(?i)\b(merci de|prière de|il faudrait|il faut que tu|n'oublie pas de)\b"),
    re.compile(r"(?i)\b(peux tu me|tu peux me|j'ai besoin que tu)\b"),
    re.compile(r"(?i)\b(à faire|to ?do|action requise|réponse attendue|relance)\b"),
    re.compile(r"(?i)\b(envoie|envoyer|rappelle|rappeler|corrige|corriger|prépare|préparer|valide|valider|transmets|transmettre)\b"),
)
_DEADLINE_PATTERNS = (
    re.compile(r"(?i)\b(avant|d'ici|au plus tard)\s+(le\s+)?\S+"),
    re.compile(r"(?i)\b(lundi|mardi|mercredi|jeudi|vendredi|samedi|dimanche|demain|ce soir|cette semaine)\b"),
    re.compile(r"(?i)\b(urgent|dès que possible|asap)\b"),
)
#: Séparateurs de demandes réellement indépendantes. Une phrase composée n'en
#: est pas une : « envoie le rapport et dis-moi si c'est bon » est une seule
#: demande, pas deux tâches.
_INDEPENDENT_SPLIT_RE = re.compile(r"(?:\n\s*[-*•]\s+|\n{2,}|\d\)\s+|\d\.\s+)")


@dataclass(frozen=True)
class DetectionInput:
    """Contenu déjà autorisé par un connecteur. Le domaine n'ouvre aucune boîte."""

    body: str
    source_type: TaskSourceType
    channel: TaskSourceChannel
    reference: str
    sender: str = ""
    subject: str = ""
    occurred_at: datetime | None = None
    is_from_user: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "body", str(self.body or ""))


@dataclass(frozen=True)
class DetectedTask:
    """Une demande repérée. Aucune décision, aucune exécution."""

    is_actionable: bool
    confidence: float
    suggested_title: str
    suggested_description: str = ""
    reason: str = ""
    suggested_due_at: datetime | None = None
    source: TaskSource = field(default_factory=TaskSource)
    dedupe_key: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "is_actionable": self.is_actionable,
            "confidence": self.confidence,
            "suggested_title": self.suggested_title,
            "suggested_description": self.suggested_description,
            "reason": self.reason,
            "suggested_due_at": (
                self.suggested_due_at.isoformat() if self.suggested_due_at else None
            ),
            "source": self.source.to_dict(),
            "dedupe_key": self.dedupe_key,
        }


def dedupe_key_for(reference: str, fragment: str = "") -> str:
    """Clé stable par source. Deux relectures du même mail ne créent qu'une fois."""

    material = f"{reference}\x00{' '.join(fragment.split()).casefold()[:200]}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


def is_noise(payload: DetectionInput) -> tuple[bool, str]:
    """Rejets déterministes, avant tout coût. Retourne ``(rejeté, raison)``."""

    if payload.is_from_user:
        return True, "message émis par l'utilisateur"
    haystack = f"{payload.subject}\n{payload.body[:2_000]}"
    for pattern in _NOISE_SENDER_PATTERNS:
        if pattern.search(payload.sender or ""):
            return True, "expéditeur automatique"
    for pattern in _NOISE_SUBJECT_PATTERNS:
        if pattern.search(haystack):
            return True, "contenu diffusé ou automatique"
    if len(payload.body.strip()) < 12:
        return True, "contenu trop court pour porter une demande"
    return False, ""


def _fragments(body: str) -> list[str]:
    parts = [part.strip() for part in _INDEPENDENT_SPLIT_RE.split(body) if part.strip()]
    return parts if len(parts) > 1 else [body.strip()]


def _score_fragment(fragment: str) -> tuple[float, str]:
    """Score déterministe et explicable — chaque point a une cause nommée."""

    score = 0.0
    reasons: list[str] = []
    action_hits = sum(1 for pattern in _ACTION_PATTERNS if pattern.search(fragment))
    if action_hits:
        score += min(0.55, 0.3 + 0.12 * (action_hits - 1))
        reasons.append("formulation de demande")
    if any(pattern.search(fragment) for pattern in _DEADLINE_PATTERNS):
        score += 0.2
        reasons.append("échéance mentionnée")
    if "?" in fragment:
        score += 0.1
        reasons.append("question posée")
    if len(fragment) > 400:
        score -= 0.1
    return max(0.0, min(1.0, score)), ", ".join(reasons)


def _title_from(fragment: str, subject: str) -> str:
    sentence = re.split(r"(?<=[.!?])\s+", fragment.strip())[0]
    candidate = clamp_text(sentence, 120)
    if len(candidate) < 12 and subject:
        candidate = clamp_text(subject, 120)
    return candidate or clamp_text(subject or fragment, 120)


class TaskCandidateDetector:
    """Détecteur générique. Ignore tout de Gmail, d'iMessage et du reste.

    Les connecteurs lui passent un ``DetectionInput`` déjà autorisé ; c'est
    leur rôle de décider quoi lire, pas le sien.
    """

    def __init__(
        self,
        *,
        min_confidence: float = DEFAULT_MIN_CONFIDENCE,
        auto_task_confidence: float = DEFAULT_AUTO_TASK_CONFIDENCE,
        disabled_sources: frozenset[TaskSourceType] = frozenset(),
    ) -> None:
        self.min_confidence = max(0.0, min(1.0, float(min_confidence)))
        self.auto_task_confidence = max(
            self.min_confidence, min(1.0, float(auto_task_confidence))
        )
        self.disabled_sources = frozenset(disabled_sources)

    def detect(self, payload: DetectionInput) -> list[DetectedTask]:
        """Retourne 0..n demandes. Plusieurs seulement si réellement disjointes."""

        if payload.source_type in self.disabled_sources:
            return []
        noisy, noise_reason = is_noise(payload)
        if noisy:
            logger.debug("détection écartée (%s) ref=%s", noise_reason, payload.reference)
            return []

        results: list[DetectedTask] = []
        fragments = _fragments(payload.body)[:5]
        for fragment in fragments:
            score, reason = _score_fragment(fragment)
            if score < self.min_confidence:
                continue
            source = TaskSource(
                source_type=payload.source_type,
                channel=payload.channel,
                reference=payload.reference,
                excerpt=clamp_text(fragment, 400),
                confidence=score,
                detection_reason=reason,
                sender=payload.sender,
                subject=payload.subject,
                occurred_at=payload.occurred_at,
            )
            results.append(
                DetectedTask(
                    is_actionable=True,
                    confidence=score,
                    suggested_title=_title_from(fragment, payload.subject),
                    suggested_description=clamp_text(fragment, 1_200),
                    reason=reason,
                    source=source,
                    dedupe_key=dedupe_key_for(
                        payload.reference, fragment if len(fragments) > 1 else ""
                    ),
                )
            )
        # Deux fragments qui produisent le même titre décrivent la même demande.
        unique: dict[str, DetectedTask] = {}
        for item in results:
            unique.setdefault(item.suggested_title.casefold(), item)
        return sorted(unique.values(), key=lambda item: -item.confidence)[:3]

    def should_create_task_directly(self, detected: DetectedTask) -> bool:
        """Confiance forte → tâche en attente de plan. Jamais → exécution."""

        return detected.confidence >= self.auto_task_confidence


def detector_from_config(config_module: Any | None = None) -> TaskCandidateDetector:
    """Construit le détecteur depuis la configuration, avec des défauts sûrs."""

    if config_module is None:
        import config as config_module

    disabled_raw = str(getattr(config_module, "TASK_DETECTION_DISABLED_SOURCES", ""))
    disabled: set[TaskSourceType] = set()
    for token in disabled_raw.replace(";", ",").split(","):
        candidate = token.strip().lower()
        if not candidate:
            continue
        try:
            disabled.add(TaskSourceType(candidate))
        except ValueError:
            logger.warning("source de détection inconnue ignorée : %s", candidate)
    return TaskCandidateDetector(
        min_confidence=float(
            getattr(config_module, "TASK_DETECTION_MIN_CONFIDENCE", DEFAULT_MIN_CONFIDENCE)
        ),
        auto_task_confidence=float(
            getattr(
                config_module,
                "TASK_DETECTION_AUTO_CONFIDENCE",
                DEFAULT_AUTO_TASK_CONFIDENCE,
            )
        ),
        disabled_sources=frozenset(disabled),
    )


def detection_input_from_email(message: Mapping[str, Any]) -> DetectionInput:
    """Adaptateur e-mail : ne conserve qu'un identifiant opaque et un extrait."""

    return DetectionInput(
        body=str(message.get("body") or message.get("summary") or "")[:4_000],
        source_type=TaskSourceType.EMAIL,
        channel=TaskSourceChannel.EMAIL,
        reference=f"email:{message.get('id') or message.get('gmail_id') or ''}",
        sender=str(message.get("sender") or message.get("from") or "")[:200],
        subject=str(message.get("subject") or "")[:300],
    )


def detection_input_from_message(message: Mapping[str, Any]) -> DetectionInput:
    """Adaptateur messagerie : identifiant opaque, jamais le fil complet."""

    return DetectionInput(
        body=str(message.get("text") or "")[:4_000],
        source_type=TaskSourceType.MESSAGE,
        channel=TaskSourceChannel.IMESSAGE,
        reference=f"imessage:{message.get('rowid') or message.get('id') or ''}",
        sender=str(message.get("handle_id") or message.get("sender") or "")[:200],
        is_from_user=bool(message.get("is_from_me")),
    )


__all__ = [
    "DEFAULT_AUTO_TASK_CONFIDENCE",
    "DEFAULT_MIN_CONFIDENCE",
    "DetectedTask",
    "DetectionInput",
    "TaskCandidateDetector",
    "dedupe_key_for",
    "detection_input_from_email",
    "detection_input_from_message",
    "detector_from_config",
    "is_noise",
]
