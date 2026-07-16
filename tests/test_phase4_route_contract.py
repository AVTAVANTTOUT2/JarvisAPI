"""Contrat de non-régression des routes pendant la Phase 4."""

from __future__ import annotations

import hashlib
import json


EXPECTED_ROUTE_COUNT = 205
EXPECTED_ROUTE_SIGNATURE = "9a92bbfeb21613b10b711a640b580a48b8126ab6139f172771861a022259802d"
EXPECTED_OPENAPI_PATH_COUNT = 186
# Vague 2 chat + Vague 2B location batch + diagnostics mobile + control service
# detail + routage cognitif (cursor jobs + confirm, briefings, voice metrics, autonomy).
EXPECTED_OPENAPI_SIGNATURE = "5a2169573b27629e50a9eeddf8011605314e7fa29eae9a8c8ffa3bd0c002bca5"


def _digest(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _iter_app_routes(app_routes: list) -> list:
    """Aplatit les routes FastAPI (compat Starlette plat + `_IncludedRouter`)."""
    collected: list[tuple[str, str, str]] = []
    for route in app_routes:
        if type(route).__name__ == "_IncludedRouter":
            nested = getattr(getattr(route, "original_router", None), "routes", None) or []
            collected.extend(_iter_app_routes(nested))
            continue
        path = getattr(route, "path", None)
        if not path:
            continue
        if not (path.startswith("/api/") or path in {"/upload", "/ws"}):
            continue
        methods = getattr(route, "methods", None) or {"WEBSOCKET"}
        name = getattr(route, "name", "") or ""
        for method in sorted(methods):
            collected.append((method, path, name))
    return collected


def test_phase4_preserves_http_and_websocket_route_contract():
    import main

    routes = sorted(set(_iter_app_routes(main.app.routes)))

    assert len(routes) == EXPECTED_ROUTE_COUNT
    assert len(routes) == len(set(routes))
    assert _digest(routes) == EXPECTED_ROUTE_SIGNATURE


def test_phase4_preserves_generated_openapi_contract():
    import main

    schema = main.app.openapi()

    assert len(schema["paths"]) == EXPECTED_OPENAPI_PATH_COUNT
    assert _digest(schema) == EXPECTED_OPENAPI_SIGNATURE
