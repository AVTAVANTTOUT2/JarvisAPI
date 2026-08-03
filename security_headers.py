"""Politique de sécurité HTTP partagée par FastAPI et le serveur E2E statique."""

from __future__ import annotations

import base64
import hashlib
import re
from collections.abc import Iterable


# Le style OpenFreeMap Dark, son TileJSON, les tuiles vectorielles/raster,
# sprites et glyphes sont tous servis par cette origine unique.
OPENFREEMAP_TILE_ORIGIN = "https://tiles.openfreemap.org"

_SCRIPT_TAG_RE = re.compile(
    br"<script\b(?P<attrs>[^>]*)>(?P<body>.*?)</script\s*>",
    re.IGNORECASE | re.DOTALL,
)
_STYLE_TAG_RE = re.compile(
    br"<style\b[^>]*>(?P<body>.*?)</style\s*>",
    re.IGNORECASE | re.DOTALL,
)
_SRC_ATTR_RE = re.compile(br"\bsrc\s*=", re.IGNORECASE)


def _sha256_source(content: bytes) -> str:
    # Le tokenizer HTML normalise CRLF/CR en LF avant l'évaluation CSP.
    # Hasher la même représentation évite un faux blocage sur des exports
    # construits depuis un checkout Windows.
    content = content.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    digest = base64.b64encode(hashlib.sha256(content).digest()).decode("ascii")
    return f"'sha256-{digest}'"


def inline_csp_hashes(html: bytes | str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Calcule les sources CSP exactes des ``script``/``style`` inline d'une page."""
    payload = html.encode("utf-8") if isinstance(html, str) else bytes(html)
    script_hashes = {
        _sha256_source(match.group("body"))
        for match in _SCRIPT_TAG_RE.finditer(payload)
        if match.group("body") and not _SRC_ATTR_RE.search(match.group("attrs"))
    }
    style_hashes = {
        _sha256_source(match.group("body"))
        for match in _STYLE_TAG_RE.finditer(payload)
        if match.group("body")
    }
    return tuple(sorted(script_hashes)), tuple(sorted(style_hashes))


def build_content_security_policy(
    *,
    script_hashes: Iterable[str] = (),
    style_hashes: Iterable[str] = (),
) -> str:
    """Construit la politique partagée, éventuellement liée à un HTML précis."""
    scripts = " ".join(("'self'", *tuple(script_hashes)))
    styles = " ".join(("'self'", *tuple(style_hashes)))
    return (
        "default-src 'self'; "
        f"script-src {scripts}; "
        f"style-src {styles}; "
        # React/MapLibre positionnent des styles d'élément au runtime. Cette
        # permission est isolée des balises <style>, protégées par hash.
        "style-src-attr 'unsafe-inline'; "
        "font-src 'self' data:; "
        "img-src 'self' data: blob: https://*.tile.openstreetmap.org "
        f"{OPENFREEMAP_TILE_ORIGIN}; "
        "media-src 'self' blob:; "
        f"connect-src 'self' {OPENFREEMAP_TILE_ORIGIN}; "
        # Le bundle npm MapLibre crée son worker depuis une URL blob. child-src
        # couvre le fallback des navigateurs qui ne prennent pas worker-src.
        "worker-src 'self' blob:; "
        "child-src blob:; "
        "object-src 'none'; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self'; "
        "manifest-src 'self'"
    )


def content_security_policy_for_html(html: bytes | str) -> str:
    script_hashes, style_hashes = inline_csp_hashes(html)
    return build_content_security_policy(
        script_hashes=script_hashes,
        style_hashes=style_hashes,
    )


CONTENT_SECURITY_POLICY = build_content_security_policy()

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": (
        "geolocation=(self), microphone=(self), camera=(), payment=(), usb=()"
    ),
    "Content-Security-Policy": CONTENT_SECURITY_POLICY,
}
