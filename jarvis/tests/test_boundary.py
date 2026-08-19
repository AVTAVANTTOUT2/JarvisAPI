"""Tests unitaires de DataBoundary.

Le contenu personnel (téléphones, e-mails, extraits de messages) n'est plus
bloqué. Seul un payload non textuel est refusé.
"""

from __future__ import annotations

import pytest

from jarvis.pii.boundary import DataBoundary


@pytest.fixture()
def boundary() -> DataBoundary:
    return DataBoundary()


def test_message_id_is_allowed(boundary: DataBoundary) -> None:
    boundary.check("voici message_id=42 dans le payload")


def test_message_id_colon_is_allowed(boundary: DataBoundary) -> None:
    boundary.check("message_id: 1337")


def test_conversation_id_is_allowed(boundary: DataBoundary) -> None:
    boundary.check("conversation_id=7 peut sortir")


def test_select_from_messages_is_allowed(boundary: DataBoundary) -> None:
    boundary.check("SELECT text FROM messages WHERE id > 0")


def test_db_messages_access_is_allowed(boundary: DataBoundary) -> None:
    boundary.check("résultat de db.messages.find(...)")


def test_raw_phone_and_email_are_allowed(boundary: DataBoundary) -> None:
    boundary.check("Contacte +33612345678 ou marie.martin@gmail.com")


def test_chat_db_reference_is_allowed(boundary: DataBoundary) -> None:
    boundary.check("le scan de chat.db a trouvé 42 messages")


def test_clean_payload_passes(boundary: DataBoundary) -> None:
    boundary.check("Bonjour [PERSON_1], voici ma réponse polie.")


def test_check_rejects_non_string(boundary: DataBoundary) -> None:
    with pytest.raises(TypeError):
        boundary.check(123)  # type: ignore[arg-type]


def test_sanitize_keeps_metadata_lines(boundary: DataBoundary) -> None:
    chunk = "message_id=42\nLe vrai contenu du document.\nconversation_id=7"
    cleaned = boundary.sanitize_chunks([chunk])
    assert cleaned == [chunk]


def test_sanitize_keeps_inline_timestamp(boundary: DataBoundary) -> None:
    chunk = "Note prise le 2024-05-12T08:30:00Z par le système."
    cleaned = boundary.sanitize_chunks([chunk])
    assert cleaned == [chunk]


def test_sanitize_keeps_empty_chunks(boundary: DataBoundary) -> None:
    cleaned = boundary.sanitize_chunks(["message_id=1", "   ", "Texte utile"])
    assert cleaned == ["message_id=1", "   ", "Texte utile"]


def test_sanitized_chunk_passes_boundary_check(boundary: DataBoundary) -> None:
    chunk = "conversation_id=99\nContenu légitime à indexer."
    cleaned = boundary.sanitize_chunks([chunk])
    for c in cleaned:
        boundary.check(c)


def test_sanitize_rejects_non_list(boundary: DataBoundary) -> None:
    with pytest.raises(TypeError):
        boundary.sanitize_chunks("pas une liste")  # type: ignore[arg-type]


def test_sanitize_rejects_non_string_chunk(boundary: DataBoundary) -> None:
    with pytest.raises(TypeError):
        boundary.sanitize_chunks(["ok", 42])  # type: ignore[list-item]
