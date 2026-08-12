from __future__ import annotations

import base64
import json

import httpx
import pytest

from integrations.opencode.client import (
    BasicAuthCredentials,
    ModelSelection,
    OpenCodeClient,
    TextPart,
)
from integrations.opencode.client.errors import (
    OpenCodeAuthenticationError,
    OpenCodeModelError,
    OpenCodeProtocolError,
    OpenCodeServerError,
    OpenCodeVersionMismatchError,
)
from integrations.opencode.client.contract import ContractMetadata
from integrations.opencode.client.sse import (
    EventDeduplicator,
    RetryPolicy,
    SSEDecoder,
    SSELineDecoder,
)
from integrations.opencode.config import OpenCodeSettings


def _credentials() -> BasicAuthCredentials:
    return BasicAuthCredentials("jarvis-opencode", "s" * 32)


def test_openapi_contract_is_anchored_to_the_verified_release_commit() -> None:
    contract = ContractMetadata.load()

    assert contract.provider_version == "1.18.16"
    assert contract.source_commit == "a3647eb025c7615159d417dcc49fc39fdaeba65b"
    assert (
        contract.source_sha256
        == "5bbd6493a1a488ef4294889341c896e420f814ecea95822100aaa9f3f95ab2d1"
    )
    assert (
        contract.operations["POST /session/{sessionID}/prompt_async"]
        == "session.prompt_async"
    )


@pytest.mark.asyncio
async def test_health_uses_basic_auth_and_enforces_pinned_version() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"healthy": True, "version": "1.18.16"})

    async with OpenCodeClient(
        "http://127.0.0.1:43123",
        _credentials(),
        transport=httpx.MockTransport(handler),
    ) as client:
        info = await client.health()

    expected = base64.b64encode(b"jarvis-opencode:" + b"s" * 32).decode()
    assert info.version == "1.18.16"
    assert requests[0].url.path == "/global/health"
    assert requests[0].headers["authorization"] == f"Basic {expected}"
    assert "s" * 32 not in repr(_credentials())


@pytest.mark.asyncio
async def test_health_rejects_silent_version_drift() -> None:
    transport = httpx.MockTransport(
        lambda _: httpx.Response(200, json={"healthy": True, "version": "1.18.17"})
    )
    async with OpenCodeClient(
        "http://127.0.0.1:43123", _credentials(), transport=transport
    ) as client:
        with pytest.raises(OpenCodeVersionMismatchError):
            await client.health()


@pytest.mark.asyncio
async def test_session_prompt_and_permission_paths_match_v1_contract() -> None:
    seen: list[tuple[str, str, dict[str, object] | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        seen.append((request.method, request.url.path, body))
        if request.method == "POST" and request.url.path == "/session":
            return httpx.Response(200, json={"id": "s1", "title": "Run"})
        if request.url.path == "/session/s1/prompt_async":
            return httpx.Response(204)
        if request.url.path == "/permission/p1/reply":
            return httpx.Response(200, json=True)
        raise AssertionError(f"unexpected request {request.method} {request.url.path}")

    async with OpenCodeClient(
        "http://127.0.0.1:43123", _credentials(), transport=httpx.MockTransport(handler)
    ) as client:
        session = await client.create_session(
            title="Run",
            agent="jarvis-executor",
            model=ModelSelection("provider", "model", "fast"),
            metadata={"run_id": "r1"},
        )
        await client.prompt_async(
            session.id,
            [TextPart("Fais le travail")],
            model=ModelSelection("provider", "model"),
            agent="jarvis-executor",
            tools={"jarvis.read": True, "shell": False},
            system="JARVIS possède le run r1.",
        )
        await client.prompt_async(
            session.id,
            [TextPart("Sans outil")],
            model=ModelSelection("provider", "model"),
            agent="jarvis-executor",
            tools={},
            system="JARVIS possède le run r1.",
        )
        assert await client.reply_permission("p1", "once")
        with pytest.raises(ValueError, match="persistante"):
            await client.reply_permission("p1", "always")

    assert seen[0][2] == {
        "title": "Run",
        "agent": "jarvis-executor",
        "model": {"providerID": "provider", "id": "model", "variant": "fast"},
        "metadata": {"run_id": "r1"},
    }
    prompt = seen[1][2]
    assert prompt is not None
    assert prompt["model"] == {"providerID": "provider", "modelID": "model"}
    assert prompt["parts"] == [{"type": "text", "text": "Fais le travail"}]
    assert prompt["tools"] == {"jarvis.read": True, "shell": False}


@pytest.mark.asyncio
async def test_reconcile_collects_session_status_messages_and_permissions() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        routes = {
            "/session/s1": {"id": "s1", "title": "Run"},
            "/session/status": {"s1": {"type": "busy"}},
            "/session/s1/message": [{"info": {"id": "m1"}, "parts": []}],
            "/permission": [
                {"id": "p1", "sessionID": "s1", "permission": "read"},
                {"id": "p2", "sessionID": "s2", "permission": "read"},
            ],
        }
        return httpx.Response(200, json=routes[request.url.path])

    async with OpenCodeClient(
        "http://127.0.0.1:43123", _credentials(), transport=httpx.MockTransport(handler)
    ) as client:
        snapshot = await client.reconcile("s1")

    assert snapshot.session.id == "s1"
    assert snapshot.status == {"type": "busy"}
    assert [item.id for item in snapshot.permissions] == ["p1"]


@pytest.mark.asyncio
async def test_http_errors_are_structured_by_domain() -> None:
    responses = iter(
        [
            httpx.Response(401, json={"message": "bad auth"}),
            httpx.Response(
                400, json={"name": "ModelNotFoundError", "message": "missing"}
            ),
        ]
    )
    transport = httpx.MockTransport(lambda _: next(responses))
    async with OpenCodeClient(
        "http://127.0.0.1:43123", _credentials(), transport=transport
    ) as client:
        with pytest.raises(OpenCodeAuthenticationError) as auth:
            await client.health()
        assert auth.value.context is not None
        assert auth.value.context.status_code == 401
        with pytest.raises(OpenCodeModelError):
            await client.create_session(title="x")


@pytest.mark.asyncio
async def test_http_error_details_are_redacted_before_exposure() -> None:
    transport = httpx.MockTransport(
        lambda _: httpx.Response(
            500,
            json={
                "message": "Bearer super-secret-token",
                "api_key": "sk-proj-very-secret-value",
            },
        )
    )
    async with OpenCodeClient(
        "http://127.0.0.1:43123", _credentials(), transport=transport
    ) as client:
        with pytest.raises(OpenCodeServerError) as captured:
            await client.health()

    context = captured.value.context
    assert context is not None
    assert "super-secret-token" not in context.message
    assert context.details["api_key"] == "[REDACTED]"


def test_sse_decoder_deduplicates_replayed_events() -> None:
    decoder = SSEDecoder(source="workspace")
    assert decoder.feed_line("id: e1") is None
    assert decoder.feed_line("event: session.updated") is None
    assert (
        decoder.feed_line('data: {"type":"session.updated","properties":{"id":"s1"}}')
        is None
    )
    event = decoder.feed_line("")
    assert event is not None
    assert event.event_id == "e1"
    dedupe = EventDeduplicator(max_entries=2)
    assert dedupe.accept(event)
    assert not dedupe.accept(event)


def test_sse_decoders_reject_unbounded_lines_events_and_invalid_utf8() -> None:
    line_decoder = SSELineDecoder(max_line_bytes=8)
    assert line_decoder.feed(b"a\nbbbb\n") == ["a", "bbbb"]
    with pytest.raises(OpenCodeProtocolError, match="trop volumineuse"):
        line_decoder.feed(b"123456789")

    invalid_utf8 = SSELineDecoder()
    with pytest.raises(OpenCodeProtocolError, match="UTF-8"):
        invalid_utf8.feed(b"\xff\n")

    event_decoder = SSEDecoder(
        source="workspace",
        max_line_bytes=32,
        max_event_bytes=8,
    )
    event_decoder.feed_line("data: 1234")
    with pytest.raises(OpenCodeProtocolError, match="Événement"):
        event_decoder.feed_line("data: 5678")

    bounded_line = SSEDecoder(
        source="workspace",
        max_line_bytes=8,
        max_event_bytes=64,
    )
    with pytest.raises(OpenCodeProtocolError, match="Ligne"):
        bounded_line.feed_line("data: 1234")


def test_sse_last_event_id_persists_without_collapsing_distinct_events() -> None:
    decoder = SSEDecoder(source="workspace")
    decoder.feed_line("id: e1")
    decoder.feed_line('data: {"type":"first"}')
    first = decoder.feed_line("")
    decoder.feed_line('data: {"type":"second"}')
    second = decoder.feed_line("")

    assert first is not None and second is not None
    assert first.event_id == "e1"
    assert first.resume_id == "e1"
    assert second.event_id != "e1"
    assert second.resume_id == "e1"
    dedupe = EventDeduplicator(max_entries=2)
    assert dedupe.accept(first)
    assert dedupe.accept(second)


def test_sse_retry_directive_persists_until_reconnection() -> None:
    decoder = SSEDecoder(source="workspace")
    decoder.feed_line("retry: 2500")
    assert decoder.feed_line("") is None
    decoder.feed_line('data: {"type":"ready"}')
    event = decoder.feed_line("")

    assert event is not None
    assert event.retry_ms == 2500


@pytest.mark.asyncio
async def test_sse_stream_uses_documented_endpoint_and_content_type() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/event"
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=b'id: e1\nevent: ready\ndata: {"type":"ready"}\n\n',
        )

    async with OpenCodeClient(
        "http://127.0.0.1:43123", _credentials(), transport=httpx.MockTransport(handler)
    ) as client:
        stream = client.stream_events(directory="/workspace")
        event = await anext(stream)
        await stream.aclose()

    assert event.event_id == "e1"
    assert event.event_type == "ready"


@pytest.mark.asyncio
async def test_sse_reconnect_deduplicates_and_reconciles_before_continuing() -> None:
    event_calls = 0
    last_event_headers: list[str | None] = []
    reconciled: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal event_calls
        if request.url.path == "/event":
            event_calls += 1
            last_event_headers.append(request.headers.get("last-event-id"))
            if event_calls == 1:
                content = b'id: e1\nevent: progress\ndata: {"type":"progress"}\n\n'
            else:
                content = (
                    b'id: e1\nevent: progress\ndata: {"type":"progress"}\n\n'
                    b'id: e2\nevent: completed\ndata: {"type":"completed"}\n\n'
                )
            return httpx.Response(
                200, headers={"content-type": "text/event-stream"}, content=content
            )
        routes = {
            "/session/s1": {"id": "s1", "title": "Run"},
            "/session/status": {"s1": {"type": "busy"}},
            "/session/s1/message": [],
            "/permission": [],
        }
        return httpx.Response(200, json=routes[request.url.path])

    async def no_sleep(_: float) -> None:
        return None

    async with OpenCodeClient(
        "http://127.0.0.1:43123",
        _credentials(),
        transport=httpx.MockTransport(handler),
        sleep=no_sleep,
        random_value=lambda: 0.0,
    ) as client:
        stream = client.stream_events(
            reconcile_session_id="s1",
            on_reconcile=lambda snapshot: reconciled.append(snapshot.session.id),
        )
        first = await anext(stream)
        second = await anext(stream)
        await stream.aclose()

    assert (first.event_id, second.event_id) == ("e1", "e2")
    assert reconciled == ["s1"]
    assert last_event_headers == [None, "e1"]


@pytest.mark.asyncio
async def test_empty_sse_connections_stop_after_bounded_retries() -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, content=b""
        )

    async def no_sleep(_: float) -> None:
        return None

    settings = OpenCodeSettings(reconnect_attempts=2)
    async with OpenCodeClient(
        "http://127.0.0.1:43123",
        _credentials(),
        settings=settings,
        transport=httpx.MockTransport(handler),
        sleep=no_sleep,
        random_value=lambda: 0.0,
    ) as client:
        with pytest.raises(Exception, match="fermé"):
            await anext(client.stream_events())

    assert calls == 3


@pytest.mark.asyncio
async def test_sse_event_then_eof_cannot_reset_global_reconnect_budget() -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        content = (
            f'id: e{calls}\nevent: progress\ndata: {{"sequence":{calls}}}\n\n'.encode()
        )
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=content,
        )

    async def no_sleep(_: float) -> None:
        return None

    settings = OpenCodeSettings(reconnect_attempts=2)
    received: list[str | None] = []
    async with OpenCodeClient(
        "http://127.0.0.1:43123",
        _credentials(),
        settings=settings,
        transport=httpx.MockTransport(handler),
        sleep=no_sleep,
        random_value=lambda: 0.0,
    ) as client:
        with pytest.raises(Exception, match="fermé"):
            async for event in client.stream_events():
                received.append(event.event_id)

    assert received == ["e1", "e2", "e3"]
    assert calls == settings.reconnect_attempts + 1


@pytest.mark.asyncio
async def test_sse_global_reconnect_deadline_stops_before_another_connection() -> None:
    calls = 0
    clock = 0.0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=b'id: e1\nevent: progress\ndata: {"sequence":1}\n\n',
        )

    async def expire_budget(_: float) -> None:
        nonlocal clock
        clock = 10_000.0

    async with OpenCodeClient(
        "http://127.0.0.1:43123",
        _credentials(),
        settings=OpenCodeSettings(reconnect_attempts=100),
        transport=httpx.MockTransport(handler),
        sleep=expire_budget,
        random_value=lambda: 0.0,
        monotonic=lambda: clock,
    ) as client:
        stream = client.stream_events()
        assert (await anext(stream)).event_id == "e1"
        with pytest.raises(Exception, match="Budget global"):
            await anext(stream)

    assert calls == 1


def test_retry_policy_is_exponential_jittered_and_bounded() -> None:
    policy = RetryPolicy(
        base_seconds=0.5, max_seconds=2.0, jitter_seconds=0.25, random_value=lambda: 1.0
    )
    assert policy.delay(0) == 0.75
    assert policy.delay(1) == 1.25
    assert policy.delay(10) == 2.0
