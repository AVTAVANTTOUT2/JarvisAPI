"""Client HTTP/SSE minimal couvrant le contrat OpenCode v1.18.16."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
import inspect
import time
from typing import Any
from urllib.parse import quote

import httpx

from integrations.opencode.config import OpenCodeSettings
from integrations.opencode.security.paths import validate_loopback_url

from .auth import BasicAuthCredentials
from .contract import PINNED_VERSION
from .errors import (
    OpenCodeNetworkError,
    OpenCodeProtocolError,
    OpenCodeTimeoutError,
    OpenCodeVersionMismatchError,
    exception_for_response,
)
from .models import (
    HealthInfo,
    JsonObject,
    MessageEnvelope,
    ModelSelection,
    ModelValidationError,
    PermissionReply,
    PermissionRequest,
    ProviderCatalog,
    ReconciliationSnapshot,
    SSEEvent,
    Session,
    TextPart,
    serialize_parts,
)
from .sse import EventDeduplicator, RetryPolicy, SSEDecoder, SSELineDecoder


@dataclass(frozen=True, slots=True)
class ContractReport:
    health: HealthInfo
    agent_count: int
    mcp_servers: tuple[str, ...]
    connected_providers: tuple[str, ...]


ReconcileCallback = Callable[[ReconciliationSnapshot], Awaitable[None] | None]


class OpenCodeClient:
    def __init__(
        self,
        base_url: str,
        credentials: BasicAuthCredentials,
        *,
        expected_version: str = PINNED_VERSION,
        settings: OpenCodeSettings | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        random_value: Callable[[], float] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.base_url = validate_loopback_url(base_url)
        self.credentials = credentials
        self.expected_version = expected_version
        self.settings = settings or OpenCodeSettings()
        timeout = httpx.Timeout(
            self.settings.request_timeout_seconds,
            connect=self.settings.request_timeout_seconds,
        )
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            auth=credentials.as_httpx(),
            timeout=timeout,
            transport=transport,
            trust_env=False,
            headers={
                "Accept": "application/json",
                "User-Agent": "JARVIS-OpenCode-Client/1",
            },
        )
        retry_kwargs: dict[str, Any] = {}
        if random_value is not None:
            retry_kwargs["random_value"] = random_value
        self._retry = RetryPolicy(
            base_seconds=self.settings.reconnect_base_seconds,
            max_seconds=self.settings.reconnect_max_seconds,
            jitter_seconds=self.settings.reconnect_jitter_seconds,
            **retry_kwargs,
        )
        self._sleep = sleep
        self._monotonic = monotonic
        self._closed = False

    async def __aenter__(self) -> "OpenCodeClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    async def close(self) -> None:
        self._closed = True
        await self._client.aclose()

    async def health(self) -> HealthInfo:
        payload = await self._request_json("GET", "/global/health")
        try:
            info = HealthInfo.from_payload(payload)
        except ModelValidationError as exc:
            raise OpenCodeProtocolError("Réponse health OpenCode invalide") from exc
        if info.version != self.expected_version:
            raise OpenCodeVersionMismatchError(
                f"Version OpenCode incompatible: {info.version} au lieu de {self.expected_version}"
            )
        return info

    async def verify_contract(
        self, *, directory: str | None = None, workspace: str | None = None
    ) -> ContractReport:
        health, agents, mcp, providers = await asyncio.gather(
            self.health(),
            self.agents(directory=directory, workspace=workspace),
            self.mcp_status(directory=directory, workspace=workspace),
            self.providers(directory=directory, workspace=workspace),
        )
        return ContractReport(
            health=health,
            agent_count=len(agents),
            mcp_servers=tuple(sorted(mcp)),
            connected_providers=providers.connected,
        )

    async def list_sessions(
        self,
        *,
        directory: str | None = None,
        workspace: str | None = None,
        limit: int | None = None,
        search: str | None = None,
    ) -> tuple[Session, ...]:
        params = self._scope_params(
            directory, workspace, {"limit": limit, "search": search}
        )
        payload = await self._request_json("GET", "/session", params=params)
        if not isinstance(payload, list):
            raise OpenCodeProtocolError("session.list doit retourner un tableau")
        try:
            return tuple(Session.from_payload(item) for item in payload)
        except ModelValidationError as exc:
            raise OpenCodeProtocolError("Session OpenCode invalide") from exc

    async def create_session(
        self,
        *,
        title: str | None = None,
        parent_id: str | None = None,
        agent: str | None = None,
        model: ModelSelection | None = None,
        metadata: Mapping[str, Any] | None = None,
        permission: Mapping[str, Any] | None = None,
        directory: str | None = None,
        workspace: str | None = None,
    ) -> Session:
        body: JsonObject = {}
        if title is not None:
            body["title"] = title
        if parent_id is not None:
            body["parentID"] = parent_id
        if agent is not None:
            body["agent"] = agent
        if model is not None:
            body["model"] = model.for_session()
        if metadata is not None:
            body["metadata"] = dict(metadata)
        if permission is not None:
            body["permission"] = dict(permission)
        payload = await self._request_json(
            "POST",
            "/session",
            params=self._scope_params(directory, workspace),
            json_body=body,
        )
        try:
            return Session.from_payload(payload)
        except ModelValidationError as exc:
            raise OpenCodeProtocolError("Session créée invalide") from exc

    async def get_session(
        self,
        session_id: str,
        *,
        directory: str | None = None,
        workspace: str | None = None,
    ) -> Session:
        payload = await self._request_json(
            "GET",
            f"/session/{self._segment(session_id)}",
            params=self._scope_params(directory, workspace),
        )
        try:
            return Session.from_payload(payload)
        except ModelValidationError as exc:
            raise OpenCodeProtocolError("Session OpenCode invalide") from exc

    async def session_status(
        self,
        *,
        directory: str | None = None,
        workspace: str | None = None,
    ) -> JsonObject:
        payload = await self._request_json(
            "GET", "/session/status", params=self._scope_params(directory, workspace)
        )
        return self._json_object(payload, "session.status")

    async def prompt_async(
        self,
        session_id: str,
        parts: Sequence[TextPart | Mapping[str, Any]],
        *,
        model: ModelSelection,
        agent: str,
        tools: Mapping[str, bool],
        system: str,
        message_id: str | None = None,
        variant: str | None = None,
        no_reply: bool = False,
        directory: str | None = None,
        workspace: str | None = None,
    ) -> None:
        if not agent or not system:
            raise ValueError("Agent et contexte système explicites obligatoires")
        if not all(
            isinstance(key, str) and isinstance(value, bool)
            for key, value in tools.items()
        ):
            raise ValueError("La capability map explicite est invalide")
        body: JsonObject = {
            "agent": agent,
            "model": model.for_prompt(),
            "noReply": no_reply,
            "parts": serialize_parts(parts),
            "system": system,
            "tools": dict(tools),
        }
        if message_id is not None:
            body["messageID"] = message_id
        if variant is not None:
            body["variant"] = variant
        await self._request(
            "POST",
            f"/session/{self._segment(session_id)}/prompt_async",
            params=self._scope_params(directory, workspace),
            json_body=body,
            expected_statuses={204},
        )

    async def messages(
        self,
        session_id: str,
        *,
        limit: int | None = None,
        before: str | None = None,
        directory: str | None = None,
        workspace: str | None = None,
    ) -> tuple[MessageEnvelope, ...]:
        params = self._scope_params(
            directory, workspace, {"limit": limit, "before": before}
        )
        payload = await self._request_json(
            "GET", f"/session/{self._segment(session_id)}/message", params=params
        )
        if not isinstance(payload, list):
            raise OpenCodeProtocolError("session.messages doit retourner un tableau")
        try:
            return tuple(MessageEnvelope.from_payload(item) for item in payload)
        except ModelValidationError as exc:
            raise OpenCodeProtocolError("Message OpenCode invalide") from exc

    async def diff(
        self,
        session_id: str,
        *,
        message_id: str | None = None,
        directory: str | None = None,
        workspace: str | None = None,
    ) -> tuple[JsonObject, ...]:
        params = self._scope_params(directory, workspace, {"messageID": message_id})
        payload = await self._request_json(
            "GET", f"/session/{self._segment(session_id)}/diff", params=params
        )
        if not isinstance(payload, list):
            raise OpenCodeProtocolError("session.diff doit retourner un tableau")
        return tuple(self._json_object(item, "diff") for item in payload)

    async def abort(
        self,
        session_id: str,
        *,
        directory: str | None = None,
        workspace: str | None = None,
    ) -> bool:
        payload = await self._request_json(
            "POST",
            f"/session/{self._segment(session_id)}/abort",
            params=self._scope_params(directory, workspace),
        )
        if not isinstance(payload, bool):
            raise OpenCodeProtocolError("session.abort doit retourner un booléen")
        return payload

    async def list_permissions(
        self,
        *,
        directory: str | None = None,
        workspace: str | None = None,
    ) -> tuple[PermissionRequest, ...]:
        payload = await self._request_json(
            "GET", "/permission", params=self._scope_params(directory, workspace)
        )
        if not isinstance(payload, list):
            raise OpenCodeProtocolError("permission.list doit retourner un tableau")
        try:
            return tuple(PermissionRequest.from_payload(item) for item in payload)
        except ModelValidationError as exc:
            raise OpenCodeProtocolError("Permission OpenCode invalide") from exc

    async def reply_permission(
        self,
        request_id: str,
        reply: PermissionReply,
        *,
        message: str | None = None,
        allow_persistent: bool = False,
        directory: str | None = None,
        workspace: str | None = None,
    ) -> bool:
        if reply not in {"once", "always", "reject"}:
            raise ValueError("Réponse de permission invalide")
        if reply == "always" and not allow_persistent:
            raise ValueError(
                "L'autorisation persistante exige une décision explicite de la policy JARVIS"
            )
        body: JsonObject = {"reply": reply}
        if message is not None:
            body["message"] = message
        payload = await self._request_json(
            "POST",
            f"/permission/{self._segment(request_id)}/reply",
            params=self._scope_params(directory, workspace),
            json_body=body,
        )
        if not isinstance(payload, bool):
            raise OpenCodeProtocolError("permission.reply doit retourner un booléen")
        return payload

    async def agents(
        self, *, directory: str | None = None, workspace: str | None = None
    ) -> tuple[JsonObject, ...]:
        payload = await self._request_json(
            "GET", "/agent", params=self._scope_params(directory, workspace)
        )
        if not isinstance(payload, list):
            raise OpenCodeProtocolError("agent.list doit retourner un tableau")
        return tuple(self._json_object(item, "agent") for item in payload)

    async def mcp_status(
        self, *, directory: str | None = None, workspace: str | None = None
    ) -> JsonObject:
        payload = await self._request_json(
            "GET", "/mcp", params=self._scope_params(directory, workspace)
        )
        return self._json_object(payload, "mcp.status")

    async def providers(
        self, *, directory: str | None = None, workspace: str | None = None
    ) -> ProviderCatalog:
        payload = await self._request_json(
            "GET", "/provider", params=self._scope_params(directory, workspace)
        )
        try:
            return ProviderCatalog.from_payload(payload)
        except ModelValidationError as exc:
            raise OpenCodeProtocolError("Catalogue provider OpenCode invalide") from exc

    async def reconcile(
        self,
        session_id: str,
        *,
        directory: str | None = None,
        workspace: str | None = None,
    ) -> ReconciliationSnapshot:
        session, statuses, messages, permissions = await asyncio.gather(
            self.get_session(session_id, directory=directory, workspace=workspace),
            self.session_status(directory=directory, workspace=workspace),
            self.messages(session_id, directory=directory, workspace=workspace),
            self.list_permissions(directory=directory, workspace=workspace),
        )
        status = statuses.get(session_id, {})
        if not isinstance(status, dict):
            status = {}
        relevant_permissions = tuple(
            item for item in permissions if item.session_id in {None, session_id}
        )
        return ReconciliationSnapshot(
            session, dict(status), messages, relevant_permissions
        )

    async def stream_events(
        self,
        *,
        global_events: bool = False,
        directory: str | None = None,
        workspace: str | None = None,
        reconcile_session_id: str | None = None,
        on_reconcile: ReconcileCallback | None = None,
    ) -> AsyncIterator[SSEEvent]:
        path = "/global/event" if global_events else "/event"
        source = "global" if global_events else "workspace"
        deduplicator = EventDeduplicator()
        reconnects = 0
        reconnect_deadline: float | None = None
        last_event_id: str | None = None
        server_retry_ms: int | None = None
        while not self._closed:
            if (
                reconnect_deadline is not None
                and self._monotonic() >= reconnect_deadline
            ):
                raise OpenCodeNetworkError(
                    "Budget global de reconnexion SSE OpenCode épuisé"
                )
            if reconnects and reconcile_session_id is not None:
                snapshot = await self.reconcile(
                    reconcile_session_id, directory=directory, workspace=workspace
                )
                if on_reconcile is not None:
                    result = on_reconcile(snapshot)
                    if inspect.isawaitable(result):
                        await result
            headers = {"Accept": "text/event-stream", "Cache-Control": "no-cache"}
            if last_event_id:
                headers["Last-Event-ID"] = last_event_id
            params = {} if global_events else self._scope_params(directory, workspace)
            decoder = SSEDecoder(source=source)
            line_decoder = SSELineDecoder()
            try:
                remaining = (
                    None
                    if reconnect_deadline is None
                    else max(0.001, reconnect_deadline - self._monotonic())
                )
                timeout = httpx.Timeout(
                    min(self.settings.sse_read_timeout_seconds, remaining)
                    if remaining is not None
                    else self.settings.sse_read_timeout_seconds,
                    connect=(
                        min(self.settings.sse_connect_timeout_seconds, remaining)
                        if remaining is not None
                        else self.settings.sse_connect_timeout_seconds
                    ),
                    write=self.settings.request_timeout_seconds,
                    pool=self.settings.request_timeout_seconds,
                )
                async with self._client.stream(
                    "GET", path, params=params, headers=headers, timeout=timeout
                ) as response:
                    if response.status_code != 200:
                        await response.aread()
                        raise exception_for_response(response, method="GET", path=path)
                    content_type = response.headers.get("content-type", "").lower()
                    if "text/event-stream" not in content_type:
                        raise OpenCodeProtocolError(
                            "Content-Type SSE OpenCode invalide"
                        )
                    async for chunk in response.aiter_bytes():
                        for line in line_decoder.feed(chunk):
                            event = decoder.feed_line(line)
                            if event is None:
                                continue
                            server_retry_ms = event.retry_ms
                            if event.resume_id is not None:
                                last_event_id = event.resume_id
                            if deduplicator.accept(event):
                                yield event
                    for line in line_decoder.finish():
                        event = decoder.feed_line(line)
                        if event is None:
                            continue
                        server_retry_ms = event.retry_ms
                        if event.resume_id is not None:
                            last_event_id = event.resume_id
                        if deduplicator.accept(event):
                            yield event
                    final_event = decoder.finish()
                    if final_event is not None and deduplicator.accept(final_event):
                        if final_event.resume_id is not None:
                            last_event_id = final_event.resume_id
                        yield final_event
            except httpx.TimeoutException as exc:
                failure: Exception = OpenCodeTimeoutError(
                    "Timeout du flux SSE OpenCode"
                )
                failure.__cause__ = exc
            except httpx.RequestError as exc:
                failure = OpenCodeNetworkError("Connexion SSE OpenCode interrompue")
                failure.__cause__ = exc
            else:
                failure = OpenCodeNetworkError("Flux SSE OpenCode fermé")
            reconnects += 1
            if reconnect_deadline is None:
                reconnect_deadline = self._monotonic() + (
                    (self.settings.reconnect_attempts + 1)
                    * self.settings.sse_connect_timeout_seconds
                    + self.settings.reconnect_attempts
                    * self.settings.reconnect_max_seconds
                )
            if reconnects > self.settings.reconnect_attempts:
                raise failure
            remaining = reconnect_deadline - self._monotonic()
            if remaining <= 0:
                raise OpenCodeNetworkError(
                    "Budget global de reconnexion SSE OpenCode épuisé"
                ) from failure
            delay = min(self._retry.delay(reconnects - 1, server_retry_ms), remaining)
            await self._sleep(delay)

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json_body: Mapping[str, Any] | None = None,
    ) -> Any:
        response = await self._request(method, path, params=params, json_body=json_body)
        try:
            return response.json()
        except ValueError as exc:
            raise OpenCodeProtocolError(
                f"Réponse JSON OpenCode invalide pour {path}"
            ) from exc

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json_body: Mapping[str, Any] | None = None,
        expected_statuses: set[int] | None = None,
    ) -> httpx.Response:
        if self._closed:
            raise OpenCodeNetworkError("Client OpenCode fermé")
        try:
            response = await self._client.request(
                method, path, params=params, json=json_body
            )
        except httpx.TimeoutException as exc:
            raise OpenCodeTimeoutError(f"Timeout OpenCode sur {method} {path}") from exc
        except httpx.RequestError as exc:
            raise OpenCodeNetworkError(
                f"Erreur réseau OpenCode sur {method} {path}"
            ) from exc
        allowed = expected_statuses or {200}
        if response.status_code not in allowed:
            raise exception_for_response(response, method=method, path=path)
        return response

    @staticmethod
    def _scope_params(
        directory: str | None,
        workspace: str | None,
        extra: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"directory": directory, "workspace": workspace}
        if extra:
            params.update(extra)
        return {key: value for key, value in params.items() if value is not None}

    @staticmethod
    def _segment(value: str) -> str:
        if not value or any(ord(char) < 32 for char in value):
            raise ValueError("Identifiant OpenCode invalide")
        return quote(value, safe="")

    @staticmethod
    def _json_object(value: Any, label: str) -> JsonObject:
        if not isinstance(value, dict) or not all(
            isinstance(key, str) for key in value
        ):
            raise OpenCodeProtocolError(f"{label} doit être un objet JSON")
        return dict(value)
