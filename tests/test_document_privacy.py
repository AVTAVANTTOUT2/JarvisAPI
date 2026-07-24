"""Consentement cloud et anonymisation des documents privés."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import authenticate


@pytest.fixture
def privacy_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    db_path = tmp_path / "privacy.db"
    upload_root = tmp_path / "uploads"
    monkeypatch.setattr("config.DB_PATH", str(db_path))
    monkeypatch.setattr("database.DB_PATH", db_path)
    monkeypatch.setattr("config.UPLOAD_DIR", str(upload_root))
    monkeypatch.setattr("config.UPLOAD_MAX_BYTES", 4096)
    monkeypatch.setattr("config.UPLOAD_QUOTA_BYTES", 16384)
    monkeypatch.setattr("config.DOCUMENT_STRICT_LOCAL", True)
    monkeypatch.setattr("config.DOCUMENT_CLOUD_MAX_CHARS", 5000)

    from database import init_db

    init_db()
    return db_path, upload_root


def _client():
    import main
    from fastapi.testclient import TestClient

    return TestClient(main.app)


def _long_private_text() -> str:
    return (
        "Le dossier de jean@example.com contient des informations privées. "
        "Ce contenu doit être résumé sans exposer son adresse. "
        + ("Donnée locale confidentielle. " * 30)
    )


def test_privacy_policy_is_strict_local_by_default(privacy_env):
    from jarvis.document_privacy import get_document_privacy_policy

    policy = get_document_privacy_policy()

    assert policy["mode"] == "strict_local"
    assert policy["strict_local"] is True
    assert policy["cloud_summary_available"] is False
    assert policy["explicit_consent_required"] is True
    assert policy["features"]["school_upload"]["data_leaving_device"] == "none"
    assert policy["features"]["conversation_document"]["cloud_summary"] == "blocked"


def test_strict_local_setting_can_be_changed_dynamically(privacy_env):
    from jarvis.document_privacy import (
        document_strict_local_enabled,
        get_document_privacy_policy,
        set_document_strict_local,
    )

    set_document_strict_local(False)

    assert document_strict_local_enabled() is False
    policy = get_document_privacy_policy()
    assert policy["mode"] == "hybrid"
    assert policy["cloud_summary_available"] is True
    assert "per_upload_consent" in policy["features"]["conversation_document"]["cloud_summary"]


@pytest.mark.asyncio
async def test_no_consent_uses_local_summary_without_cloud(privacy_env):
    from jarvis.document_privacy import summarize_document

    result = await summarize_document(_long_private_text(), cloud_consent=False)

    assert result.summary
    assert result.processing_mode == "local"
    assert result.cloud_request_attempted is False
    assert result.data_left_device is False
    assert result.cloud_payload_chars == 0


@pytest.mark.asyncio
async def test_cloud_consent_anonymizes_pii_and_restores_summary(privacy_env):
    from jarvis.document_privacy import set_document_strict_local, summarize_document
    from jarvis.pii.anonymizer import PIIAnonymizer
    from jarvis.pii.boundary import DataBoundary
    from jarvis.router import JARVISRouter

    class FakeDeepSeek:
        def __init__(self):
            self.prompt = ""

        async def generate(self, *, prompt: str, system: str | None = None):
            self.prompt = prompt
            assert "jean@example.com" not in prompt
            assert "[EMAIL_1]" in prompt
            assert system and "tokens" in system
            return "Résumé privé pour [EMAIL_1]."

    class UnusedLocal:
        async def generate(self, *args, **kwargs):
            raise AssertionError("Le backend local LLM ne doit pas être appelé")

    set_document_strict_local(False)
    deepseek = FakeDeepSeek()
    router = JARVISRouter(
        local=UnusedLocal(),
        deepseek=deepseek,
        anonymizer=PIIAnonymizer(),
        boundary=DataBoundary(),
    )

    result = await summarize_document(
        _long_private_text(),
        cloud_consent=True,
        router=router,
    )

    assert result.processing_mode == "cloud_anonymized"
    assert result.cloud_request_attempted is True
    assert result.data_left_device is True
    assert result.pii_entities_masked >= 1
    assert result.cloud_payload_chars == len(_long_private_text())
    assert result.summary == "Résumé privé pour jean@example.com."


@pytest.mark.asyncio
async def test_cloud_failure_falls_back_locally_and_reports_attempt(privacy_env):
    from jarvis.document_privacy import set_document_strict_local, summarize_document
    from jarvis.pii.anonymizer import PIIAnonymizer
    from jarvis.pii.boundary import DataBoundary
    from jarvis.router import JARVISRouter

    class FailingDeepSeek:
        async def generate(self, **_kwargs):
            raise RuntimeError("réseau indisponible")

    class UnusedLocal:
        pass

    set_document_strict_local(False)
    router = JARVISRouter(
        local=UnusedLocal(),
        deepseek=FailingDeepSeek(),
        anonymizer=PIIAnonymizer(),
        boundary=DataBoundary(),
    )

    result = await summarize_document(
        _long_private_text(),
        cloud_consent=True,
        router=router,
    )

    assert result.summary
    assert result.processing_mode == "local_fallback"
    assert result.cloud_request_attempted is True
    assert result.data_left_device is True


def test_api_rejects_cloud_consent_before_storing_in_strict_mode(privacy_env):
    from database import create_conversation

    _, upload_root = privacy_env
    conversation_id = create_conversation()

    with _client() as client:
        authenticate(client)
        response = client.post(
            f"/api/conversations/{conversation_id}/upload",
            data={"cloud_consent": "true"},
            files={"file": ("privé.txt", _long_private_text().encode(), "text/plain")},
        )

    assert response.status_code == 409
    assert "strictement local" in response.json()["detail"]
    assert not upload_root.exists() or not any(path.is_file() for path in upload_root.rglob("*"))


def test_api_defaults_to_local_and_exposes_processing_metadata(privacy_env):
    from database import create_conversation

    conversation_id = create_conversation()
    with _client() as client:
        authenticate(client)
        response = client.post(
            f"/api/conversations/{conversation_id}/upload",
            files={"file": ("privé.txt", _long_private_text().encode(), "text/plain")},
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["processing_mode"] == "local"
    assert body["cloud_consent"] is False
    assert body["cloud_request_attempted"] is False
    assert body["data_left_device"] is False
    assert body["summary"]


def test_privacy_api_requires_explicit_per_upload_consent(privacy_env, monkeypatch):
    from database import create_conversation, get_conversation_documents
    from jarvis.document_privacy import DocumentSummaryResult
    import api.router_conversations as conversation_routes

    calls: list[bool] = []

    async def _fake_summary(_text: str, *, cloud_consent: bool):
        calls.append(cloud_consent)
        return DocumentSummaryResult(
            summary="Résumé sûr",
            processing_mode="cloud_anonymized" if cloud_consent else "local",
            cloud_consent=cloud_consent,
            cloud_request_attempted=cloud_consent,
            data_left_device=cloud_consent,
            pii_entities_masked=2 if cloud_consent else 0,
            cloud_payload_chars=100 if cloud_consent else 0,
        )

    monkeypatch.setattr(conversation_routes, "summarize_document", _fake_summary)
    conversation_id = create_conversation()

    with _client() as client:
        authenticate(client)
        policy = client.get("/api/privacy/documents")
        enabled = client.put("/api/privacy/documents", json={"strict_local": False})
        no_consent = client.post(
            f"/api/conversations/{conversation_id}/upload",
            files={"file": ("local.txt", _long_private_text().encode(), "text/plain")},
        )
        with_consent = client.post(
            f"/api/conversations/{conversation_id}/upload",
            data={"cloud_consent": "true"},
            files={"file": ("cloud.txt", _long_private_text().encode(), "text/plain")},
        )

    assert policy.status_code == 200 and policy.json()["strict_local"] is True
    assert enabled.status_code == 200 and enabled.json()["strict_local"] is False
    assert no_consent.status_code == 200
    assert no_consent.json()["data_left_device"] is False
    assert with_consent.status_code == 200
    assert with_consent.json()["data_left_device"] is True
    assert with_consent.json()["pii_entities_masked"] == 2
    assert calls == [False, True]
    documents = get_conversation_documents(conversation_id)
    assert [bool(document["cloud_consent"]) for document in documents] == [False, True]


@pytest.mark.asyncio
async def test_chat_context_excludes_unconsented_docs_and_masks_consented_pii(privacy_env):
    from api.chat_context import _build_enriched_context
    from database import create_conversation, save_conversation_document
    from jarvis.document_privacy import set_document_strict_local

    conversation_id = create_conversation()
    save_conversation_document(
        conversation_id,
        "a.txt",
        "nom-secret-alice.txt",
        "/tmp/a.txt",
        "txt",
        10,
        "NE_DOIT_PAS_SORTIR alice@example.com",
        cloud_consent=False,
    )
    save_conversation_document(
        conversation_id,
        "b.txt",
        "nom-secret-bob.txt",
        "/tmp/b.txt",
        "txt",
        10,
        "CONTENU_AUTORISE bob@example.com",
        cloud_consent=True,
    )
    set_document_strict_local(False)

    context = await _build_enriched_context("question neutre", conversation_id)

    document_context = context["documents_context"]
    assert "CONTENU_AUTORISE" in document_context
    assert "NE_DOIT_PAS_SORTIR" not in document_context
    assert "bob@example.com" not in document_context
    assert "nom-secret-bob.txt" not in document_context
    assert "[EMAIL_1]" in document_context


@pytest.mark.asyncio
async def test_strict_local_excludes_even_previously_consented_docs_from_chat(privacy_env):
    from api.chat_context import _build_enriched_context
    from database import create_conversation, save_conversation_document

    conversation_id = create_conversation()
    save_conversation_document(
        conversation_id,
        "b.txt",
        "document.txt",
        "/tmp/b.txt",
        "txt",
        10,
        "Contenu privé",
        cloud_consent=True,
    )

    context = await _build_enriched_context("question neutre", conversation_id)

    assert "documents_context" not in context


@pytest.mark.asyncio
async def test_data_boundary_blocks_forbidden_document_signatures(privacy_env):
    from api.chat_context import _build_enriched_context
    from database import create_conversation, save_conversation_document
    from jarvis.document_privacy import set_document_strict_local

    conversation_id = create_conversation()
    save_conversation_document(
        conversation_id,
        "audit.txt",
        "audit.txt",
        "/tmp/audit.txt",
        "txt",
        10,
        "Copie brute de chat.db",
        cloud_consent=True,
    )
    set_document_strict_local(False)

    context = await _build_enriched_context("question neutre", conversation_id)

    assert "documents_context" not in context


def test_migration_marks_historical_documents_local_only():
    import sqlite3

    from database.migrations import _migrate_conversation_document_consent

    conn = sqlite3.connect(":memory:")
    conn.execute(
        """CREATE TABLE conversation_documents (
            id INTEGER PRIMARY KEY,
            original_name TEXT,
            extracted_text TEXT
        )"""
    )
    conn.execute(
        "INSERT INTO conversation_documents (id, original_name, extracted_text) "
        "VALUES (1, 'ancien.txt', 'privé')"
    )

    _migrate_conversation_document_consent(conn)
    _migrate_conversation_document_consent(conn)

    row = conn.execute(
        "SELECT cloud_consent FROM conversation_documents WHERE id = 1"
    ).fetchone()
    columns = {
        info[1] for info in conn.execute("PRAGMA table_info(conversation_documents)")
    }
    conn.close()
    assert row[0] == 0
    assert "cloud_consent" in columns
