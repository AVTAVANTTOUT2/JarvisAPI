"""Tests du pipeline message_intelligence et du DataBoundary étendu.

Couvre : blocage téléphones/emails bruts dans les payloads sortants,
round-trip anonymisation, et intégrité du garde-fou DataBoundary.
Aucun appel réseau — tous les tests sont unitaires et déterministes.
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pytest

from jarvis.exceptions import DataLeakError
from jarvis.pii.anonymizer import PIIAnonymizer
from jarvis.pii.boundary import DataBoundary


@pytest.fixture()
def boundary() -> DataBoundary:
    return DataBoundary()


@pytest.fixture()
def anonymizer() -> PIIAnonymizer:
    return PIIAnonymizer()


# ── DataBoundary — nouveaux patterns (téléphones, emails bruts) ──

def test_boundary_blocks_raw_phone(boundary: DataBoundary) -> None:
    with pytest.raises(DataLeakError):
        boundary.check("Contacte +33612345678 pour confirmer")


def test_boundary_blocks_raw_email(boundary: DataBoundary) -> None:
    with pytest.raises(DataLeakError):
        boundary.check("Re ponds a marie.martin@gmail.com")


def test_boundary_blocks_chat_db_reference(boundary: DataBoundary) -> None:
    with pytest.raises(DataLeakError):
        boundary.check("le scan de chat.db a trouve 42 messages")


def test_boundary_allows_anonymized_text(boundary: DataBoundary) -> None:
    # Ne doit pas lever — les tokens PII sont autorises.
    boundary.check("Re ponds a [PERSON_1] via [EMAIL_1]")


def test_boundary_allows_clean_text(boundary: DataBoundary) -> None:
    boundary.check("Bonjour, voici un message sans donnee sensible.")


# ── PIIAnonymizer — round-trip complet ──

def test_anonymizer_roundtrip_person_and_email(anonymizer: PIIAnonymizer) -> None:
    text = "Marie Martin a ecrit a marie@example.com pour confirmer le RDV."
    anonymized_obj = anonymizer.anonymize(text)
    assert "Marie Martin" not in anonymized_obj.anonymized_text
    assert "marie@example.com" not in anonymized_obj.anonymized_text
    assert "[PERSON_1]" in anonymized_obj.anonymized_text or any(
        tok.startswith("[PERSON_") for tok in anonymized_obj.mapping
    )
    restored = anonymizer.deanonymize(
        anonymized_obj.anonymized_text, dict(anonymized_obj.mapping)
    )
    assert "Marie Martin" in restored
    assert "marie@example.com" in restored


def test_anonymizer_roundtrip_phone(anonymizer: PIIAnonymizer) -> None:
    text = "Rappelle-moi au 06 12 34 56 78 stp."
    anonymized_obj = anonymizer.anonymize(text)
    assert "06 12 34 56 78" not in anonymized_obj.anonymized_text
    assert any(tok.startswith("[PHONE_") for tok in anonymized_obj.mapping)
    restored = anonymizer.deanonymize(
        anonymized_obj.anonymized_text, dict(anonymized_obj.mapping)
    )
    assert "06 12 34 56 78" in restored


def test_anonymizer_roundtrip_iban(anonymizer: PIIAnonymizer) -> None:
    iban = "FR7630006000011234567890189"
    text = f"Virement a faire sur {iban}."
    anonymized_obj = anonymizer.anonymize(text)
    assert iban not in anonymized_obj.anonymized_text
    assert any(tok.startswith("[FINANCIAL_") for tok in anonymized_obj.mapping)
    restored = anonymizer.deanonymize(
        anonymized_obj.anonymized_text, dict(anonymized_obj.mapping)
    )
    assert iban in restored


# ── Anti-régression : les anciens patterns fonctionnent toujours ──

def test_legacy_boundary_message_id(boundary: DataBoundary) -> None:
    with pytest.raises(DataLeakError):
        boundary.check("voici message_id=42 dans le payload")


def test_legacy_boundary_select_from_messages(boundary: DataBoundary) -> None:
    with pytest.raises(DataLeakError):
        boundary.check("SELECT text FROM messages WHERE id > 0")


# ── Migration DB : table message_insights ──

def test_message_insights_table_exists() -> None:
    """Verifie que la table message_insights est creee par init_db()."""
    import config
    import sqlite3

    db_path = Path(config.DB_PATH)
    if not db_path.exists():
        pytest.skip("DB inexistante — test d'integration saute.")
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='message_insights'"
        ).fetchall()
        assert len(rows) == 1, "Table message_insights absente — verifie init_db()."
    finally:
        conn.close()
