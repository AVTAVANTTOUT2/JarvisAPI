"""Contrat de non-régression des routes pendant la Phase 4."""

from __future__ import annotations

import hashlib
import json


EXPECTED_ROUTE_COUNT = 186
EXPECTED_ROUTE_SIGNATURE = "d3b2c63ec09d562801eaf962b0940744aae85b65f0e459de2b2e6b2c3c06d446"
EXPECTED_OPENAPI_PATH_COUNT = 168
# Vague 2 chat + Vague 2B location batch response schema
EXPECTED_OPENAPI_SIGNATURE = "b40459cf2af58127259a5c8768f0e02fe8a1902185ee5cb990dba0b80938be42"


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
