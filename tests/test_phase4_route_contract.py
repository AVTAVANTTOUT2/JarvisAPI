"""Contrat de non-régression des routes pendant la Phase 4."""

from __future__ import annotations

import hashlib
import json


EXPECTED_ROUTE_COUNT = 182
EXPECTED_ROUTE_SIGNATURE = "6c120cf923e6d48c72b05fe3aaadf46de71e501fe3a5432aa6407d3ad2844f3d"
EXPECTED_OPENAPI_PATH_COUNT = 164
EXPECTED_OPENAPI_SIGNATURE = "0e79d4d6eb7a32c3fd45cbc1c3a409984fcb83b1417af566df74773d00ca9178"


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
