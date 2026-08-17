"""Détection de tâches — rejets déterministes, scoring et seuils.

La détection ne doit jamais exécuter. Ces tests verrouillent le filtre de
bruit (faux positifs coûteux) et le seuil auto-tâche, sans base ni LLM.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from jarvis.task_control.detection import (
    DEFAULT_MIN_CONFIDENCE,
    DetectionInput,
    TaskCandidateDetector,
    dedupe_key_for,
    detection_input_from_email,
    detection_input_from_message,
    detector_from_config,
    is_noise,
)
from jarvis.task_control.models import TaskSourceChannel, TaskSourceType


def _email(
    *,
    body: str = "Peux-tu envoyer le rapport avant vendredi ?",
    sender: str = "alice@example.invalid",
    subject: str = "Rapport",
    reference: str = "email:1",
) -> DetectionInput:
    return DetectionInput(
        body=body,
        source_type=TaskSourceType.EMAIL,
        channel=TaskSourceChannel.EMAIL,
        reference=reference,
        sender=sender,
        subject=subject,
    )


# ── Bruit déterministe ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("sender", "subject", "body", "reason_fragment"),
    [
        (
            "noreply@vendor.invalid",
            "Votre commande",
            "Peux-tu confirmer la livraison avant vendredi ?",
            "expéditeur",
        ),
        (
            "news@mailchimp.com",
            "Offres",
            "Peux-tu cliquer avant vendredi pour découvrir nos soldes ?",
            "expéditeur",
        ),
        (
            "alice@example.invalid",
            "Newsletter hebdo — se désabonner",
            "Peux-tu lire nos offres avant vendredi ?",
            "automatique",
        ),
        (
            "alice@example.invalid",
            "Absence du bureau",
            "Je suis en congés. Peux-tu attendre mon retour avant vendredi ?",
            "automatique",
        ),
        (
            "alice@example.invalid",
            "Promo Black Friday",
            "Peux-tu profiter du code promo avant vendredi ?",
            "automatique",
        ),
    ],
)
def test_is_noise_rejects_automatic_traffic(
    sender: str, subject: str, body: str, reason_fragment: str
) -> None:
    noisy, reason = is_noise(_email(sender=sender, subject=subject, body=body))
    assert noisy is True
    assert reason_fragment in reason.casefold()


def test_is_noise_rejects_short_body_and_outbound_messages() -> None:
    short_noisy, short_reason = is_noise(_email(body="ok merci"))
    assert short_noisy is True
    assert "court" in short_reason

    outbound = DetectionInput(
        body="Peux-tu envoyer le rapport avant vendredi ?",
        source_type=TaskSourceType.MESSAGE,
        channel=TaskSourceChannel.IMESSAGE,
        reference="imessage:9",
        sender="+33600000000",
        is_from_user=True,
    )
    noisy, reason = is_noise(outbound)
    assert noisy is True
    assert "utilisateur" in reason


def test_is_noise_accepts_human_request() -> None:
    noisy, reason = is_noise(_email())
    assert noisy is False
    assert reason == ""


# ── Scoring / détection ────────────────────────────────────────────────────


def test_detector_ignores_noise_and_disabled_sources() -> None:
    detector = TaskCandidateDetector()
    assert detector.detect(
        _email(sender="noreply@vendor.invalid", subject="Accusé de réception")
    ) == []

    disabled = TaskCandidateDetector(disabled_sources=frozenset({TaskSourceType.EMAIL}))
    assert disabled.detect(_email()) == []


def test_detector_scores_actionable_request_above_min_confidence() -> None:
    detector = TaskCandidateDetector()
    hits = detector.detect(_email())
    assert len(hits) == 1
    assert hits[0].is_actionable is True
    assert hits[0].confidence >= DEFAULT_MIN_CONFIDENCE
    assert "demande" in hits[0].reason
    assert hits[0].dedupe_key


def test_opinion_without_request_stays_below_threshold() -> None:
    detector = TaskCandidateDetector()
    hits = detector.detect(
        _email(
            body="Le rapport est intéressant, je trouve ça bien écrit dans l'ensemble.",
            subject="Lecture",
        )
    )
    assert hits == []


def test_independent_fragments_produce_distinct_dedupe_keys() -> None:
    detector = TaskCandidateDetector()
    body = (
        "1. Peux-tu envoyer le rapport avant vendredi ?\n"
        "2. N'oublie pas de valider le devis demain."
    )
    hits = detector.detect(_email(body=body, reference="email:split"))
    assert len(hits) >= 2
    keys = {item.dedupe_key for item in hits}
    assert len(keys) == len(hits)
    # Même référence + fragment vide → clé stable pour les relectures mono-demande.
    assert dedupe_key_for("email:split") == dedupe_key_for("email:split")
    assert dedupe_key_for("email:split", "a") != dedupe_key_for("email:split", "b")


def test_should_create_task_directly_respects_auto_threshold() -> None:
    from dataclasses import replace

    detector = TaskCandidateDetector(auto_task_confidence=0.85)
    base = detector.detect(_email())[0]
    # Confiance moyenne → candidat ; forte → tâche en attente de plan.
    soft_detected = replace(base, confidence=0.5)
    strong_detected = replace(base, confidence=0.9)
    assert detector.should_create_task_directly(soft_detected) is False
    assert detector.should_create_task_directly(strong_detected) is True


def test_detector_from_config_parses_disabled_sources_and_ignores_unknown() -> None:
    cfg = SimpleNamespace(
        TASK_DETECTION_DISABLED_SOURCES="email, bogus, message",
        TASK_DETECTION_MIN_CONFIDENCE=0.5,
        TASK_DETECTION_AUTO_CONFIDENCE=0.9,
    )
    detector = detector_from_config(cfg)
    assert detector.min_confidence == 0.5
    assert detector.auto_task_confidence == 0.9
    assert TaskSourceType.EMAIL in detector.disabled_sources
    assert TaskSourceType.MESSAGE in detector.disabled_sources


def test_adapters_keep_opaque_references_and_bounds() -> None:
    email = detection_input_from_email(
        {
            "gmail_id": "abc",
            "from": "Bob <bob@example.invalid>",
            "subject": "S",
            "body": "x" * 10_000,
        }
    )
    assert email.reference == "email:abc"
    assert email.sender.startswith("Bob")
    assert len(email.body) == 4_000

    message = detection_input_from_message(
        {"rowid": 12, "text": "peux-tu rappeler demain ?", "is_from_me": False}
    )
    assert message.reference == "imessage:12"
    assert message.is_from_user is False
