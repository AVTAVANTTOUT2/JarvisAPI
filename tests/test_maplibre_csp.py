"""Contrat CSP strict nécessaire à MapLibre et OpenFreeMap."""

from __future__ import annotations

from security_headers import (
    CONTENT_SECURITY_POLICY,
    OPENFREEMAP_TILE_ORIGIN,
    content_security_policy_for_html,
    inline_csp_hashes,
)


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


def test_inline_scripts_and_styles_are_bound_to_exact_sha256_hashes():
    html = b"""<!doctype html><style>body { color: red }</style>
    <script src="/bundle.js"></script><script>self.__next_f.push([1,\"x\"])</script>"""

    script_hashes, style_hashes = inline_csp_hashes(html)
    policy = content_security_policy_for_html(html)

    assert len(script_hashes) == 1
    assert len(style_hashes) == 1
    assert script_hashes[0] in policy
    assert style_hashes[0] in policy
    assert "'unsafe-inline'" not in _directive(policy, "script-src")
    assert "'unsafe-inline'" not in _directive(policy, "style-src")
    assert _directive(policy, "style-src-attr") == "'unsafe-inline'"


def test_hash_changes_when_inline_bootstrap_changes():
    first, _ = inline_csp_hashes("<script>window.value = 1</script>")
    second, _ = inline_csp_hashes("<script>window.value = 2</script>")

    assert first != second


def test_hashes_follow_html_newline_normalization():
    crlf_hashes = inline_csp_hashes("<script>first\r\nsecond\rthird</script>")
    lf_hashes = inline_csp_hashes("<script>first\nsecond\nthird</script>")

    assert crlf_hashes == lf_hashes


def _directive(policy: str, name: str) -> str:
    for raw in policy.split(";"):
        parts = raw.strip().split(maxsplit=1)
        if parts and parts[0] == name:
            return parts[1] if len(parts) > 1 else ""
    raise AssertionError(f"directive CSP absente: {name}")


def test_default_policy_has_no_global_inline_or_websocket_escape_hatches():
    assert _directive(CONTENT_SECURITY_POLICY, "script-src") == "'self'"
    assert _directive(CONTENT_SECURITY_POLICY, "style-src") == "'self'"
    assert "ws:" not in _directive(CONTENT_SECURITY_POLICY, "connect-src")
    assert "wss:" not in _directive(CONTENT_SECURITY_POLICY, "connect-src")
    assert "fonts.googleapis.com" not in CONTENT_SECURITY_POLICY
    assert "fonts.gstatic.com" not in CONTENT_SECURITY_POLICY
