"""Politique de sécurité HTTP partagée par FastAPI et le serveur E2E statique."""

from __future__ import annotations


# Le style OpenFreeMap Dark, son TileJSON, les tuiles vectorielles/raster,
# sprites et glyphes sont tous servis par cette origine unique.
OPENFREEMAP_TILE_ORIGIN = "https://tiles.openfreemap.org"

CONTENT_SECURITY_POLICY = (
    "default-src 'self'; "
    # Next.js export statique : bootstrap RSC/hydratation via <script> inline
    # dans index.html — script-src 'self' seul bloque le montage React.
    "script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
    "font-src 'self' data: https://fonts.gstatic.com; "
    "img-src 'self' data: blob: https://*.tile.openstreetmap.org "
    f"{OPENFREEMAP_TILE_ORIGIN}; "
    "media-src 'self' blob:; "
    f"connect-src 'self' ws: wss: {OPENFREEMAP_TILE_ORIGIN}; "
    # Le bundle npm MapLibre crée son worker depuis une URL blob. child-src
    # couvre le fallback des navigateurs qui ne prennent pas worker-src.
    "worker-src 'self' blob:; "
    "child-src blob:; "
    "frame-ancestors 'none'; "
    "base-uri 'self'; "
    "form-action 'self'"
)

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": (
        "geolocation=(self), microphone=(self), camera=(), payment=(), usb=()"
    ),
    "Content-Security-Policy": CONTENT_SECURITY_POLICY,
}
