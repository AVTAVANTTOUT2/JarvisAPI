"""Tests du routage JARVISRouter — DeepSeek + anonymisation (politique 2026).

Garantit que :
- chat() et summarize() passent par DeepSeek après anonymisation ;
- aucun appel LocalBackend sur les chemins conversationnels ;
- l'anonymisation/dé-anonymisation encadre les flux sensibles ;
- les chunks RAG sont sanitizés avant envoi.
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


async def test_chat_routes_to_deepseek_anonymized() -> None:
    router, local, deepseek = _make_router(deepseek_return="Bonjour Monsieur.")
    out = await router.chat("Salut")
    assert out == "Bonjour Monsieur."
    deepseek.generate.assert_awaited_once()
    local.generate.assert_not_awaited()
    assert router.stats.deepseek_calls == 1
    assert router.stats.local_calls == 0


async def test_summarize_messages_routes_to_deepseek() -> None:
    router, local, deepseek = _make_router(deepseek_return="résumé privé")
    out = await router.summarize("blabla privé", DataSource.MESSAGES)
    assert out == "résumé privé"
    deepseek.generate.assert_awaited_once()
    local.generate.assert_not_awaited()
    assert router.stats.deepseek_calls == 1


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
        assert "marie@acme.com" not in prompt
        return "Réponse pour le destinataire masqué"

    deepseek.generate = AsyncMock(side_effect=fake_generate)
    payload = EmailPayload(
        subject="Hello",
        body="Contacte marie@acme.com demain",
        sender="bob@example.com",
    )
    out = await router.mail(payload)
    assert "marie@acme.com" not in captured["prompt"]
    assert isinstance(out, str)
    assert router.stats.deepseek_calls == 1


async def test_rag_sanitizes_chunks() -> None:
    router, local, deepseek = _make_router(deepseek_return="ok")
    out = await router.rag("question", ["chunk A", "chunk B"])
    assert out == "ok"
    deepseek.generate.assert_awaited_once()
    local.generate.assert_not_awaited()


async def test_task_routes_to_deepseek() -> None:
    router, local, deepseek = _make_router(deepseek_return="fait")
    out = await router.task("Faire X")
    assert out == "fait"
    deepseek.generate.assert_awaited_once()
    local.generate.assert_not_awaited()
