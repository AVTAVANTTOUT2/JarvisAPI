"""Régressions des allowlists pour les identifiants SQL dynamiques."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture
def tmp_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    db_path = tmp_path / "dynamic-field-allowlists.db"
    monkeypatch.setattr("config.DB_PATH", str(db_path))
    monkeypatch.setattr("database.DB_PATH", db_path)

    from database import init_db

    init_db()
    return db_path


def test_dynamic_field_allowlists_are_explicit_frozensets() -> None:
    from database.conversations import CONVERSATION_MUTABLE_FIELDS
    from database.people import PERSON_UPSERT_MUTABLE_FIELDS
    from database.relationships import RELATIONSHIP_PROFILE_MUTABLE_FIELDS

    assert isinstance(PERSON_UPSERT_MUTABLE_FIELDS, frozenset)
    assert isinstance(CONVERSATION_MUTABLE_FIELDS, frozenset)
    assert isinstance(RELATIONSHIP_PROFILE_MUTABLE_FIELDS, frozenset)


def test_legitimate_dynamic_fields_still_work(tmp_db: Path) -> None:
    from database import (
        create_conversation,
        get_db,
        get_person,
        get_relationship_profile,
        update_conversation,
        upsert_person,
        upsert_relationship_profile,
    )

    person_id = upsert_person(
        "Alice",
        relationship="amie",
        personality_notes="directe",
        dynamics="équilibrée",
        patterns="fiable",
    )
    upsert_person("Alice", relationship="collègue")
    person = get_person("Alice")
    assert person is not None
    assert person["id"] == person_id
    assert person["relationship"] == "collègue"
    assert person["personality_notes"] == "directe"

    profile_id = upsert_relationship_profile(
        person_id,
        handle="alice@example.test",
        communication_style="direct",
        sentiment="positif",
    )
    assert upsert_relationship_profile(person_id, trust_level="élevé") == profile_id
    profile = get_relationship_profile(person_id)
    assert profile is not None
    assert profile["communication_style"] == "direct"
    assert profile["trust_level"] == "élevé"

    conversation_id = create_conversation(agent="tests")
    assert update_conversation(
        conversation_id,
        title="Titre sûr",
        pinned=True,
        archived=False,
        tags='["sécurité"]',
    ) is True
    with get_db() as conn:
        conversation = conn.execute(
            "SELECT title, pinned, archived, tags FROM conversations WHERE id = ?",
            (conversation_id,),
        ).fetchone()
    assert dict(conversation) == {
        "title": "Titre sûr",
        "pinned": 1,
        "archived": 0,
        "tags": '["sécurité"]',
    }


def test_upsert_person_rejects_malicious_column_without_partial_update(
    tmp_db: Path,
) -> None:
    from database import get_person, upsert_person

    upsert_person("Alice", relationship="amie", patterns="initial")
    malicious_column = "patterns = ? WHERE id = ? --"

    with pytest.raises(ValueError, match="people non modifiable"):
        upsert_person(
            "Alice",
            relationship="compromise partiel",
            **{malicious_column: "compromis"},
        )

    person = get_person("Alice")
    assert person is not None
    assert person["relationship"] == "amie"
    assert person["patterns"] == "initial"

    with pytest.raises(ValueError, match="people non modifiable"):
        upsert_person("Mallory", **{malicious_column: "compromis"})
    assert get_person("Mallory") is None


def test_update_conversation_rejects_malicious_column_without_partial_update(
    tmp_db: Path,
) -> None:
    from database import create_conversation, get_db, update_conversation

    conversation_id = create_conversation(agent="tests")
    assert update_conversation(conversation_id, title="Initial", pinned=False) is True
    malicious_column = "archived = ?, pinned = 1 WHERE id = ? --"

    with pytest.raises(ValueError, match="conversation non modifiable"):
        update_conversation(
            conversation_id,
            title="Compromis",
            **{malicious_column: True},
        )

    with get_db() as conn:
        conversation = conn.execute(
            "SELECT title, pinned, archived FROM conversations WHERE id = ?",
            (conversation_id,),
        ).fetchone()
    assert dict(conversation) == {"title": "Initial", "pinned": 0, "archived": 0}


def test_relationship_profile_rejects_malicious_column_without_partial_update(
    tmp_db: Path,
) -> None:
    from database import (
        get_relationship_profile,
        upsert_person,
        upsert_relationship_profile,
    )

    person_id = upsert_person("Alice")
    upsert_relationship_profile(
        person_id,
        communication_style="direct",
        sentiment="neutre",
        trust_level="normal",
    )
    malicious_column = "sentiment = ?, trust_level = 'compromis' WHERE id = ? --"

    with pytest.raises(ValueError, match="profil relationnel non modifiable"):
        upsert_relationship_profile(
            person_id,
            communication_style="compromis",
            **{malicious_column: "hostile"},
        )

    profile = get_relationship_profile(person_id)
    assert profile is not None
    assert profile["communication_style"] == "direct"
    assert profile["sentiment"] == "neutre"
    assert profile["trust_level"] == "normal"

    second_person_id = upsert_person("Bob")
    with pytest.raises(ValueError, match="profil relationnel non modifiable"):
        upsert_relationship_profile(
            second_person_id,
            **{malicious_column: "hostile"},
        )
    assert get_relationship_profile(second_person_id) is None


def test_memory_agent_accepts_known_field_and_ignores_malicious_field(
    tmp_db: Path,
) -> None:
    from agents.memory import MemoryAgent
    from database import get_person, upsert_person

    upsert_person("Alice", relationship="amie")
    memory = MemoryAgent()

    legitimate = {
        "should_store": False,
        "updates": {
            "people": [
                {
                    "name": "Alice",
                    "action": "update",
                    "field": "relationship",
                    "value": "collègue",
                }
            ]
        },
    }
    applied = memory._parse_and_apply(
        f"```json\n{json.dumps(legitimate, ensure_ascii=False)}\n```"
    )
    assert applied is not None
    assert applied["people"] == 1
    assert get_person("Alice")["relationship"] == "collègue"

    malicious = {
        "should_store": False,
        "updates": {
            "people": [
                {
                    "name": "Alice",
                    "action": "update",
                    "field": "relationship = ?, name = 'compromis' WHERE id = ? --",
                    "value": "hostile",
                }
            ]
        },
    }
    applied = memory._parse_and_apply(
        f"```json\n{json.dumps(malicious, ensure_ascii=False)}\n```"
    )
    assert applied is not None
    assert applied["people"] == 0
    person = get_person("Alice")
    assert person is not None
    assert person["name"] == "Alice"
    assert person["relationship"] == "collègue"
