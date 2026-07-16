"""Contrat de non-régression des routes pendant la Phase 4."""

from __future__ import annotations

import hashlib
import json


EXPECTED_ROUTE_COUNT = 183
EXPECTED_ROUTE_SIGNATURE = "3f414d61c0e5ac51942efad2e277352a1ca4df876eae23b61d470ea763d35e27"
EXPECTED_OPENAPI_PATH_COUNT = 165
# Vague 2B : schéma réponse `/api/location/batch` (accepted/duplicates/rejected)
EXPECTED_OPENAPI_SIGNATURE = "623a5e4986cfec20d05800308164126a88e50547215b4f7ba9414e60b1c65194"


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
