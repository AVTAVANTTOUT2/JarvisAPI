"""Contrat CSP strict nécessaire à MapLibre et OpenFreeMap."""

from __future__ import annotations

from security_headers import CONTENT_SECURITY_POLICY, OPENFREEMAP_TILE_ORIGIN


def _directives() -> dict[str, set[str]]:
    parsed: dict[str, set[str]] = {}
    for raw_directive in CONTENT_SECURITY_POLICY.split(";"):
        parts = raw_directive.strip().split()
        if parts:
            parsed[parts[0]] = set(parts[1:])
    return parsed


def test_openfreemap_is_allowed_only_on_required_resource_directives():
    directives = _directives()

    assert directives["connect-src"] == {
        "'self'",
        "ws:",
        "wss:",
        OPENFREEMAP_TILE_ORIGIN,
    }
    assert directives["img-src"] == {
        "'self'",
        "data:",
        "blob:",
        "https://*.tile.openstreetmap.org",
        OPENFREEMAP_TILE_ORIGIN,
    }
    assert OPENFREEMAP_TILE_ORIGIN not in directives["script-src"]
    assert OPENFREEMAP_TILE_ORIGIN not in directives["style-src"]


def test_maplibre_worker_permissions_are_narrow_and_explicit():
    directives = _directives()

    assert directives["worker-src"] == {"'self'", "blob:"}
    assert directives["child-src"] == {"blob:"}
    assert "*" not in directives["connect-src"]
    assert "https:" not in directives["connect-src"]
