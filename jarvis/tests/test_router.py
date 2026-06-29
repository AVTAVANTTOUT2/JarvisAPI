"""Tests du routage JARVISRouter avec backends mockés.

Garantit que :
- chat() et summarize(MESSAGES) vont en LOCAL et JAMAIS en DeepSeek ;
- mail()/rag()/task()/summarize(autre) vont en DeepSeek ;
- l'anonymisation/dé-anonymisation encadre bien les flux DeepSeek sensibles ;
- les chunks RAG sont sanitizés avant envoi ;
- les compteurs RouterStats sont incrémentés correctement.
Aucun appel réseau ni subprocess réel (AsyncMock partout).
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from jarvis.models import DataSource, EmailPayload, RouterStats
from jarvis.pii.anonymizer import PIIAnonymizer
from jarvis.pii.boundary import DataBoundary
from jarvis.router import JARVISRouter


def _make_router(
    local_return: str = "réponse locale",
    deepseek_return: str = "réponse deepseek",
) -> tuple[JARVISRouter, AsyncMock, AsyncMock]:
    local = AsyncMock()
    local.generate = AsyncMock(return_value=local_return)
    deepseek = AsyncMock()
    deepseek.generate = AsyncMock(return_value=deepseek_return)
    stats = RouterStats()
    router = JARVISRouter(
        local=local,
        deepseek=deepseek,
        anonymizer=PIIAnonymizer(),
        boundary=DataBoundary(),
        stats=stats,
    )
    return router, local, deepseek


async def test_chat_routes_to_local_only() -> None:
    router, local, deepseek = _make_router(local_return="Bonjour Monsieur.")
    out = await router.chat("Salut")
    assert out == "Bonjour Monsieur."
    local.generate.assert_awaited_once()
    deepseek.generate.assert_not_awaited()
    assert router.stats.local_calls == 1
    assert router.stats.deepseek_calls == 0


async def test_summarize_messages_routes_to_local() -> None:
    router, local, deepseek = _make_router(local_return="résumé privé")
    out = await router.summarize("blabla privé", DataSource.MESSAGES)
    assert out == "résumé privé"
    local.generate.assert_awaited_once()
    deepseek.generate.assert_not_awaited()
    assert router.stats.local_calls == 1


async def test_summarize_document_routes_to_deepseek() -> None:
    router, local, deepseek = _make_router(deepseek_return="résumé public")
    out = await router.summarize("contenu document", DataSource.DOCUMENT)
    assert out == "résumé public"
    deepseek.generate.assert_awaited_once()
    local.generate.assert_not_awaited()
    assert router.stats.deepseek_calls == 1


async def test_mail_anonymizes_then_deepseek_then_deanonymizes() -> None:
    router, _local, deepseek = _make_router()

    captured: dict[str, str] = {}

    async def fake_generate(prompt: str, system=None, **kwargs):
        captured["prompt"] = prompt
        captured["system"] = system or ""
        # DeepSeek doit voir un token, pas l'email réel, et le renvoyer intact.
        assert "marie@acme.com" not in prompt
        assert "[EMAIL_1]" in prompt
        return "Réponse adressée à [EMAIL_1], dossier traité."

    deepseek.generate = AsyncMock(side_effect=fake_generate)

    payload = EmailPayload(
        subject="Demande",
        body="Bonjour, écrivez à marie@acme.com pour la suite.",
        sender="boss@acme.com",
        recipients=["elias@me.com"],
    )
    out = await router.mail(payload)

    assert "marie@acme.com" in out
    assert "[EMAIL_1]" not in out
    assert router.stats.deepseek_calls == 1
    assert router.stats.pii_entities_masked >= 1


async def test_rag_sanitizes_chunks_before_send() -> None:
    router, _local, deepseek = _make_router(deepseek_return="réponse rag")

    captured: dict[str, str] = {}

    async def fake_generate(prompt: str, system=None, **kwargs):
        captured["prompt"] = prompt
        return "réponse rag"

    deepseek.generate = AsyncMock(side_effect=fake_generate)

    chunks = ["message_id=42\nLe contenu réellement utile.", "conversation_id=7"]
    out = await router.rag("Que dit le doc ?", chunks)

    assert out == "réponse rag"
    assert "message_id=42" not in captured["prompt"]
    assert "conversation_id=7" not in captured["prompt"]
    assert "Le contenu réellement utile." in captured["prompt"]


async def test_task_routes_to_deepseek() -> None:
    router, _local, deepseek = _make_router(deepseek_return="tâche faite")
    out = await router.task("Crée une todo list de révisions")
    assert out == "tâche faite"
    deepseek.generate.assert_awaited_once()
    assert router.stats.deepseek_calls == 1


async def test_mail_rejects_wrong_type() -> None:
    router, _local, _deepseek = _make_router()
    with pytest.raises(TypeError):
        await router.mail("pas un EmailPayload")  # type: ignore[arg-type]


async def test_summarize_rejects_invalid_source() -> None:
    router, _local, _deepseek = _make_router()
    with pytest.raises(TypeError):
        await router.summarize("texte", "messages")  # type: ignore[arg-type]


async def test_rag_rejects_empty_query() -> None:
    router, _local, _deepseek = _make_router()
    with pytest.raises(ValueError):
        await router.rag("", ["chunk"])


async def test_task_rejects_empty_description() -> None:
    router, _local, _deepseek = _make_router()
    with pytest.raises(ValueError):
        await router.task("   ")


async def test_email_payload_forbids_messages_field() -> None:
    # Garantie structurelle : EmailPayload n'expose aucun champ messages.
    fields = set(EmailPayload.__dataclass_fields__.keys())
    assert "messages" not in fields
    assert "conversation" not in fields


async def test_email_payload_validates_types() -> None:
    with pytest.raises(TypeError):
        EmailPayload(subject=1, body="b", sender="s")  # type: ignore[arg-type]
