"""Versioning optimiste et idempotence des reprises hors ligne.

Le middleware ne rejoue que des mutations de données explicitement bornées.
Les commandes système, paiements, messages et autres effets externes n'entrent
jamais dans ce protocole générique.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import sqlite3
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import Request
from starlette.responses import JSONResponse, Response

from database.core import get_db

SYNC_MARKER_HEADER = "X-Jarvis-Sync-Operation"
OPERATION_ID_HEADER = "X-Idempotency-Key"
CHECKSUM_HEADER = "X-Jarvis-Operation-Checksum"
ENTITY_VERSION_HEADER = "X-Jarvis-Entity-Version"
ENTITY_KEY_HEADER = "X-Jarvis-Entity-Key"
CONFLICT_STRATEGY_HEADER = "X-Jarvis-Conflict-Strategy"
IDEMPOTENT_REPLAY_HEADER = "X-Jarvis-Idempotent-Replay"

_MUTATION_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_SYNC_MUTATION_PATTERNS = (
    re.compile(r"^/api/tasks(?:/\d+)?$"),
    re.compile(r"^/api/notifications/(?:\d+/read|read-all)$"),
    re.compile(r"^/api/settings/tts$"),
    re.compile(
        r"^/api/fitness/(?:sessions/\d+/progress|meals(?:/from-text)?|water|weights|"
        r"program(?:/sessions/\d+)?)$"
    ),
    re.compile(r"^/api/life-profile(?:/\d+)?$"),
    re.compile(r"^/api/people(?:/[^/]+)?$"),
    re.compile(r"^/api/journal$"),
    re.compile(r"^/api/location(?:/(?:batch|name-current))?$"),
    re.compile(r"^/api/places(?:/\d+)?$"),
    re.compile(r"^/api/conversations/\d+(?:/archive)?$"),
    re.compile(r"^/api/privacy/documents$"),
)
_ENTITY_ROOTS = frozenset(
    {
        "tasks",
        "notifications",
        "settings",
        "fitness",
        "life-profile",
        "people",
        "journal",
        "location",
        "places",
        "conversations",
        "privacy",
    }
)
_ENTITY_LOCKS: dict[str, asyncio.Lock] = {}


def sync_entity_key(path: str) -> str | None:
    """Retourne une racine de version stable et volontairement conservative."""
    clean_path = path.split("?", 1)[0]
    parts = [part for part in clean_path.split("/") if part]
    if len(parts) < 2 or parts[0] != "api" or parts[1] not in _ENTITY_ROOTS:
        return None
    if parts[1] == "settings" and parts[2:3] != ["tts"]:
        return None
    if parts[1] == "privacy" and parts[2:3] != ["documents"]:
        return None
    return f"/api/{parts[1]}" + (
        "/tts" if parts[1] == "settings" else "/documents" if parts[1] == "privacy" else ""
    )


def is_sync_mutation_allowed(path: str, method: str) -> bool:
    clean_path = path.split("?", 1)[0]
    return method in _MUTATION_METHODS and any(
        pattern.fullmatch(clean_path) is not None for pattern in _SYNC_MUTATION_PATTERNS
    )


def operation_checksum(method: str, path: str, body: bytes) -> str:
    canonical = method.upper().encode() + b"\n" + path.encode() + b"\n" + body
    return hashlib.sha256(canonical).hexdigest()


def _entity_lock(entity_key: str) -> asyncio.Lock:
    return _ENTITY_LOCKS.setdefault(entity_key, asyncio.Lock())


def _current_version(entity_key: str) -> int:
    with get_db() as conn:
        row = conn.execute(
            "SELECT version FROM sync_entity_versions WHERE entity_key = ?",
            (entity_key,),
        ).fetchone()
    return int(row["version"]) if row else 0


def _load_operation(operation_id: str) -> sqlite3.Row | None:
    with get_db() as conn:
        return conn.execute(
            "SELECT * FROM sync_operations WHERE operation_id = ?",
            (operation_id,),
        ).fetchone()


def _reserve_operation(
    operation_id: str,
    checksum: str,
    entity_key: str,
    base_version: int | None,
    current_version: int,
) -> None:
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO sync_operations (
                operation_id, checksum, entity_key, base_version,
                resolved_version, status_code
            ) VALUES (?, ?, ?, ?, ?, 0)
            """,
            (operation_id, checksum, entity_key, base_version, current_version),
        )


def _discard_reservation(operation_id: str) -> None:
    with get_db() as conn:
        conn.execute(
            "DELETE FROM sync_operations WHERE operation_id = ? AND status_code = 0",
            (operation_id,),
        )


def _complete_mutation(
    *,
    entity_key: str,
    checksum: str | None,
    operation_id: str | None,
    base_version: int | None,
    status_code: int,
    response_body: bytes,
    response_headers: dict[str, str],
) -> int:
    with get_db() as conn:
        row = conn.execute(
            "SELECT version FROM sync_entity_versions WHERE entity_key = ?",
            (entity_key,),
        ).fetchone()
        version = (int(row["version"]) if row else 0) + 1
        conn.execute(
            """
            INSERT INTO sync_entity_versions (entity_key, version, checksum, updated_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(entity_key) DO UPDATE SET
                version = excluded.version,
                checksum = excluded.checksum,
                updated_at = CURRENT_TIMESTAMP
            """,
            (entity_key, version, checksum),
        )
        if operation_id:
            conn.execute(
                """
                UPDATE sync_operations SET
                    base_version = ?, resolved_version = ?, status_code = ?,
                    response_body = ?, response_content_type = ?, response_headers_json = ?
                WHERE operation_id = ? AND checksum = ?
                """,
                (
                    base_version,
                    version,
                    status_code,
                    response_body,
                    response_headers.get("content-type"),
                    json.dumps(response_headers, sort_keys=True),
                    operation_id,
                    checksum,
                ),
            )
        conn.execute(
            "DELETE FROM sync_operations WHERE created_at < datetime('now', '-30 days')"
        )
    return version


async def _response_body(response: Response) -> bytes:
    body = getattr(response, "body", None)
    if isinstance(body, bytes):
        return body
    chunks: list[bytes] = []
    async for chunk in response.body_iterator:  # type: ignore[attr-defined]
        chunks.append(chunk.encode() if isinstance(chunk, str) else chunk)
    return b"".join(chunks)


def _version_headers(response: Response, entity_key: str, version: int) -> None:
    response.headers[ENTITY_VERSION_HEADER] = str(version)
    response.headers[ENTITY_KEY_HEADER] = entity_key
    response.headers.setdefault("ETag", f'W/"jarvis-{version}"')


def _error(code: str, status_code: int, **details: Any) -> JSONResponse:
    return JSONResponse({"error": code, **details}, status_code=status_code)


def _replay(operation: sqlite3.Row) -> Response:
    headers = json.loads(operation["response_headers_json"] or "{}")
    headers.pop("content-length", None)
    headers[IDEMPOTENT_REPLAY_HEADER] = "true"
    headers[ENTITY_VERSION_HEADER] = str(operation["resolved_version"])
    headers[ENTITY_KEY_HEADER] = str(operation["entity_key"])
    return Response(
        content=operation["response_body"] or b"",
        status_code=int(operation["status_code"]),
        headers=headers,
    )


async def sync_versioning_middleware(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    path = request.url.path
    method = request.method.upper()
    entity_key = sync_entity_key(path)
    if entity_key is None:
        return await call_next(request)

    if method in {"GET", "HEAD"}:
        response = await call_next(request)
        if response.status_code < 400:
            try:
                _version_headers(response, entity_key, _current_version(entity_key))
            except sqlite3.Error:
                pass
        return response

    if not is_sync_mutation_allowed(path, method):
        return await call_next(request)

    is_sync = request.headers.get(SYNC_MARKER_HEADER) == "1"
    async with _entity_lock(entity_key):
        operation_id: str | None = None
        checksum: str | None = None
        base_version: int | None = None
        if is_sync:
            operation_id = request.headers.get(OPERATION_ID_HEADER)
            checksum = request.headers.get(CHECKSUM_HEADER)
            try:
                if operation_id is None:
                    raise ValueError
                uuid.UUID(operation_id)
            except ValueError:
                return _error("sync_operation_id_invalid", 400)
            body = await request.body()
            expected_checksum = operation_checksum(method, path, body)
            if checksum != expected_checksum:
                return _error("sync_checksum_mismatch", 400, expected_checksum=expected_checksum)

            client_wins = request.headers.get(CONFLICT_STRATEGY_HEADER) == "client_wins"
            existing = _load_operation(operation_id)
            if existing is not None:
                if existing["checksum"] != checksum:
                    return _error("sync_operation_reused", 409, entity_key=entity_key)
                if int(existing["status_code"]) == 0:
                    if client_wins:
                        _discard_reservation(operation_id)
                    else:
                        return _error(
                            "sync_operation_outcome_unknown",
                            409,
                            entity_key=entity_key,
                            server_version=_current_version(entity_key),
                        )
                else:
                    return _replay(existing)

            version_header = request.headers.get(ENTITY_VERSION_HEADER)
            try:
                base_version = int(version_header) if version_header is not None else None
            except ValueError:
                return _error("sync_entity_version_invalid", 400, entity_key=entity_key)
            current_version = _current_version(entity_key)
            if base_version is None and not client_wins:
                return _error(
                    "sync_base_version_required",
                    409,
                    entity_key=entity_key,
                    server_version=current_version,
                )
            if base_version != current_version and not client_wins:
                return _error(
                    "sync_version_conflict",
                    409,
                    entity_key=entity_key,
                    client_version=base_version,
                    server_version=current_version,
                )
            _reserve_operation(operation_id, checksum, entity_key, base_version, current_version)

        response = await call_next(request)
        if not 200 <= response.status_code < 400:
            if operation_id:
                _discard_reservation(operation_id)
            return response

        if is_sync:
            body = await _response_body(response)
            headers = dict(response.headers)
            version = _complete_mutation(
                entity_key=entity_key,
                checksum=checksum,
                operation_id=operation_id,
                base_version=base_version,
                status_code=response.status_code,
                response_body=body,
                response_headers=headers,
            )
            headers.pop("content-length", None)
            response = Response(
                content=body,
                status_code=response.status_code,
                headers=headers,
                background=response.background,
            )
        else:
            version = _complete_mutation(
                entity_key=entity_key,
                checksum=None,
                operation_id=None,
                base_version=None,
                status_code=response.status_code,
                response_body=b"",
                response_headers={},
            )
        _version_headers(response, entity_key, version)
        return response
