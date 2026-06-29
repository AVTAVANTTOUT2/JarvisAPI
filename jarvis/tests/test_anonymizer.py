"""Tests unitaires de PIIAnonymizer.

Couvre : masquage par type, stabilité des tokens, round-trip, robustesse de la
dé-anonymisation au reformatage du LLM, unicité du session_id, destruction du
mapping. Aucun I/O, aucun appel réseau.
"""

from __future__ import annotations

import re
import uuid

import pytest

from jarvis.pii.anonymizer import (
    AnonymizationResult,
    PIIAnonymizer,
    _normalize_token_key,
)

_TOKEN_PATTERN = re.compile(r"\[[A-Z]+_\d+\]")


@pytest.fixture()
def anonymizer() -> PIIAnonymizer:
    return PIIAnonymizer()


def test_anonymize_masks_email(anonymizer: PIIAnonymizer) -> None:
    result = anonymizer.anonymize("Contacte-moi sur marie@acme.com stp.")
    assert "marie@acme.com" not in result.anonymized_text
    assert "[EMAIL_1]" in result.anonymized_text
    assert result.mapping["[EMAIL_1]"] == "marie@acme.com"


def test_anonymize_masks_phone(anonymizer: PIIAnonymizer) -> None:
    result = anonymizer.anonymize("Mon numéro est le 06 12 34 56 78.")
    assert "06 12 34 56 78" not in result.anonymized_text
    assert any(tok.startswith("[PHONE_") for tok in result.mapping)


def test_anonymize_masks_iban(anonymizer: PIIAnonymizer) -> None:
    iban = "FR7630006000011234567890189"
    result = anonymizer.anonymize(f"Vire sur {iban} avant lundi.")
    assert iban not in result.anonymized_text
    assert any(tok.startswith("[FINANCIAL_") for tok in result.mapping)


def test_same_entity_yields_same_token(anonymizer: PIIAnonymizer) -> None:
    result = anonymizer.anonymize(
        "Écris à jean@x.com puis relance jean@x.com demain."
    )
    tokens = _TOKEN_PATTERN.findall(result.anonymized_text)
    assert tokens.count("[EMAIL_1]") == 2
    assert len(result.mapping) == 1


def test_distinct_entities_yield_distinct_tokens(anonymizer: PIIAnonymizer) -> None:
    result = anonymizer.anonymize("De a@x.com à b@y.com.")
    assert result.mapping["[EMAIL_1]"] == "a@x.com"
    assert result.mapping["[EMAIL_2]"] == "b@y.com"


def test_round_trip_restores_original(anonymizer: PIIAnonymizer) -> None:
    original = "Réponds à marie@acme.com avant le 12/05/2024."
    result = anonymizer.anonymize(original)
    # DeepSeek renvoie une phrase contenant les tokens intacts.
    llm_reply = f"J'ai répondu à {result.anonymized_text}"
    restored = anonymizer.deanonymize(llm_reply, dict(result.mapping))
    assert "marie@acme.com" in restored
    assert "12/05/2024" in restored


def test_deanonymize_tolerates_reformatted_token(anonymizer: PIIAnonymizer) -> None:
    result = anonymizer.anonymize("Bonjour marie@acme.com")
    # Le LLM reformate la casse / l'espacement du token.
    llm_reply = "Réponse pour [Email_1] et aussi [ email 1 ]."
    restored = anonymizer.deanonymize(llm_reply, dict(result.mapping))
    assert restored.count("marie@acme.com") == 2


def test_deanonymize_destroys_mapping(anonymizer: PIIAnonymizer) -> None:
    result = anonymizer.anonymize("marie@acme.com")
    mapping = dict(result.mapping)
    anonymizer.deanonymize("[EMAIL_1]", mapping)
    assert mapping == {}


def test_session_id_is_uuid4_and_unique(anonymizer: PIIAnonymizer) -> None:
    first = anonymizer.anonymize("a@x.com")
    second = anonymizer.anonymize("a@x.com")
    assert uuid.UUID(first.session_id).version == 4
    assert uuid.UUID(second.session_id).version == 4
    assert first.session_id != second.session_id


def test_empty_text_returns_empty_result(anonymizer: PIIAnonymizer) -> None:
    result = anonymizer.anonymize("")
    assert result.anonymized_text == ""
    assert result.mapping == {}
    assert uuid.UUID(result.session_id).version == 4


def test_anonymize_rejects_non_string(anonymizer: PIIAnonymizer) -> None:
    with pytest.raises(TypeError):
        anonymizer.anonymize(None)  # type: ignore[arg-type]


def test_deanonymize_rejects_none_mapping(anonymizer: PIIAnonymizer) -> None:
    with pytest.raises(ValueError):
        anonymizer.deanonymize("texte", None)  # type: ignore[arg-type]


def test_deanonymize_with_empty_mapping_is_identity(anonymizer: PIIAnonymizer) -> None:
    assert anonymizer.deanonymize("rien à restaurer", {}) == "rien à restaurer"


def test_entities_masked_count(anonymizer: PIIAnonymizer) -> None:
    result = anonymizer.anonymize("a@x.com et b@y.com")
    assert result.entities_masked == 2


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("[PERSON_1]", "PERSON1"),
        ("[Person_1]", "PERSON1"),
        ("[ person 1 ]", "PERSON1"),
        ("[EMAIL-2]", "EMAIL2"),
    ],
)
def test_normalize_token_key(raw: str, expected: str) -> None:
    assert _normalize_token_key(raw) == expected


def test_result_is_dataclass_instance(anonymizer: PIIAnonymizer) -> None:
    result = anonymizer.anonymize("a@x.com")
    assert isinstance(result, AnonymizationResult)
