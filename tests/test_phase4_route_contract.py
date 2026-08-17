"""Contrat de non-régression des routes pendant la Phase 4."""

from __future__ import annotations

import hashlib
import json

EXPECTED_ROUTE_COUNT = 315
EXPECTED_ROUTE_SIGNATURE = (
    "bae015b6d1063a5e2e65ed7cd9355f161bb018c296acac0f7ed3cfb7a5c48b39"
)
EXPECTED_OPENAPI_PATH_COUNT = 281
# Empreinte stable : chemins + méthodes uniquement (indépendante de la version
# FastAPI/Pydantic qui fait varier les composants du schéma complet).
EXPECTED_OPENAPI_PATHS_SIGNATURE = (
    "bc7ae3fb45346853b08d6aa768aa8aa04e4a612aba526e09ce45ee0449e9d45f"
)


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
            nested = (
                getattr(getattr(route, "original_router", None), "routes", None) or []
            )
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


def _openapi_paths_contract(schema: dict) -> dict[str, list[str]]:
    """Réduit OpenAPI aux chemins + verbes HTTP (contrat stable)."""
    out: dict[str, list[str]] = {}
    for path, methods in (schema.get("paths") or {}).items():
        if not isinstance(methods, dict):
            continue
        verbs = sorted(
            k
            for k in methods
            if k in {"get", "post", "put", "patch", "delete", "head", "options"}
        )
        out[path] = verbs
    return out


def test_phase4_preserves_http_and_websocket_route_contract():
    import main

    routes = sorted(set(_iter_app_routes(main.app.routes)))

    assert len(routes) == EXPECTED_ROUTE_COUNT
    assert len(routes) == len(set(routes))
    assert _digest(routes) == EXPECTED_ROUTE_SIGNATURE


def test_phase4_preserves_generated_openapi_contract():
    import main

    schema = main.app.openapi()
    paths_contract = _openapi_paths_contract(schema)

    assert len(schema["paths"]) == EXPECTED_OPENAPI_PATH_COUNT
    assert len(paths_contract) == EXPECTED_OPENAPI_PATH_COUNT
    assert _digest(paths_contract) == EXPECTED_OPENAPI_PATHS_SIGNATURE
    assert "/api/debug/resolve/{name}" not in paths_contract
