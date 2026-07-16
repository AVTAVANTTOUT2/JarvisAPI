"""Contrat de non-régression des routes pendant la Phase 4."""

from __future__ import annotations

import hashlib
import json


EXPECTED_ROUTE_COUNT = 204
EXPECTED_ROUTE_SIGNATURE = "173b9b57ab8f58af0f7eaf629990ec226c6c39efa00aa97c5c769f6d29d761c0"
EXPECTED_OPENAPI_PATH_COUNT = 185
# Vague 2 chat + Vague 2B location batch + diagnostics mobile + control service
# detail + routage cognitif (cursor jobs, briefings, voice metrics, autonomy).
EXPECTED_OPENAPI_SIGNATURE = "9a9cbaa31afcf1513032169e9454a7bf237a1abd7312361b5b5c4982cda2be8b"


def _digest(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def test_phase4_preserves_http_and_websocket_route_contract():
    import main

    routes = sorted(
        (method, route.path, route.name)
        for route in main.app.routes
        if route.path.startswith("/api/") or route.path in {"/upload", "/ws"}
        for method in sorted(getattr(route, "methods", None) or {"WEBSOCKET"})
    )

    assert len(routes) == EXPECTED_ROUTE_COUNT
    assert len(routes) == len(set(routes))
    assert _digest(routes) == EXPECTED_ROUTE_SIGNATURE


def test_phase4_preserves_generated_openapi_contract():
    import main

    schema = main.app.openapi()

    assert len(schema["paths"]) == EXPECTED_OPENAPI_PATH_COUNT
    assert _digest(schema) == EXPECTED_OPENAPI_SIGNATURE
