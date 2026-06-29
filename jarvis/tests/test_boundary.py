"""Tests unitaires de DataBoundary.

Vérifie que les signatures de fuite de données messages lèvent DataLeakError et
que les chunks RAG sont correctement débarrassés de leurs métadonnées DB.
"""

from __future__ import annotations

import pytest

from jarvis.exceptions import DataLeakError
from jarvis.pii.boundary import DataBoundary


@pytest.fixture()
def boundary() -> DataBoundary:
    return DataBoundary()


def test_message_id_triggers_leak(boundary: DataBoundary) -> None:
    with pytest.raises(DataLeakError):
        boundary.check("voici message_id=42 dans le payload")


def test_message_id_colon_triggers_leak(boundary: DataBoundary) -> None:
    with pytest.raises(DataLeakError):
        boundary.check("message_id: 1337")


def test_conversation_id_triggers_leak(boundary: DataBoundary) -> None:
    with pytest.raises(DataLeakError):
        boundary.check("conversation_id=7 ne doit pas sortir")


def test_select_from_messages_triggers_leak(boundary: DataBoundary) -> None:
    with pytest.raises(DataLeakError):
        boundary.check("SELECT text FROM messages WHERE id > 0")


def test_db_messages_access_triggers_leak(boundary: DataBoundary) -> None:
    with pytest.raises(DataLeakError):
        boundary.check("résultat de db.messages.find(...)")


def test_clean_payload_passes(boundary: DataBoundary) -> None:
    # Ne doit rien lever.
    boundary.check("Bonjour [PERSON_1], voici ma réponse polie.")


def test_check_rejects_non_string(boundary: DataBoundary) -> None:
    with pytest.raises(TypeError):
        boundary.check(123)  # type: ignore[arg-type]


def test_sanitize_strips_metadata_lines(boundary: DataBoundary) -> None:
    chunk = "message_id=42\nLe vrai contenu du document.\nconversation_id=7"
    cleaned = boundary.sanitize_chunks([chunk])
    assert cleaned == ["Le vrai contenu du document."]


def test_sanitize_strips_inline_timestamp(boundary: DataBoundary) -> None:
    chunk = "Note prise le 2024-05-12T08:30:00Z par le système."
    cleaned = boundary.sanitize_chunks([chunk])
    assert "2024-05-12T08:30:00Z" not in cleaned[0]
    assert "Note prise le" in cleaned[0]


def test_sanitize_drops_empty_chunks(boundary: DataBoundary) -> None:
    cleaned = boundary.sanitize_chunks(["message_id=1", "   ", "Texte utile"])
    assert cleaned == ["Texte utile"]


def test_sanitized_chunk_passes_boundary_check(boundary: DataBoundary) -> None:
    chunk = "conversation_id=99\nContenu légitime à indexer."
    cleaned = boundary.sanitize_chunks([chunk])
    # Le chunk nettoyé doit franchir le garde-fou sans erreur.
    for c in cleaned:
        boundary.check(c)


def test_sanitize_rejects_non_list(boundary: DataBoundary) -> None:
    with pytest.raises(TypeError):
        boundary.sanitize_chunks("pas une liste")  # type: ignore[arg-type]


def test_sanitize_rejects_non_string_chunk(boundary: DataBoundary) -> None:
    with pytest.raises(TypeError):
        boundary.sanitize_chunks(["ok", 42])  # type: ignore[list-item]
