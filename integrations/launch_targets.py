"""Résolution des cibles ``launch`` — URL, schéma, fichier $HOME, nom d'app.

Pas une intégration YouTube : un catalogue d'hôtes et de schémas, plus
``/usr/bin/open``. Refuser plutôt qu'inventer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import unquote, urlsplit

DEFAULT_LAUNCH_SCHEMES = frozenset(
    {
        "https",
        "http",
        "file",
        "youtube",
        "spotify",
        "maps",
        "x-apple.systempreferences",
        "shortcuts",
        "mailto",
    }
)
BLOCKED_LAUNCH_SCHEMES = frozenset(
    {
        "javascript",
        "data",
        "smb",
        "afp",
        "ssh",
        "telnet",
        "ftp",
    }
)
MAX_LAUNCH_TARGET_CHARS = 2048
_HANDLE_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_HTTP_HOST_RE = re.compile(r"^[A-Za-z0-9.-]{1,253}$")
YOUTUBE_HOSTS = frozenset(
    {
        "youtube.com",
        "www.youtube.com",
        "m.youtube.com",
        "youtu.be",
        "music.youtube.com",
    }
)
YOUTUBE_APP_NAMES = frozenset({"youtube", "youtube.app"})


@dataclass(frozen=True)
class LaunchSpec:
    """Cible déjà validée, prête pour ``open``."""

    kind: Literal["app", "url", "path"]
    target: str
    app: str | None = None

    def argv(self, open_bin: str) -> tuple[str, ...]:
        if self.kind == "app":
            return (open_bin, "-a", self.target)
        if self.app:
            return (open_bin, "-a", self.app, self.target)
        return (open_bin, self.target)


def launch_schemes() -> frozenset[str]:
    """Schémas autorisés ; ``javascript:`` ne peut pas revenir par config."""
    try:
        import config as cfg

        configured = getattr(cfg, "LAUNCH_URL_SCHEMES", None)
    except Exception:
        configured = None
    if isinstance(configured, (set, frozenset)) and configured:
        schemes = frozenset(
            str(item).strip().lower() for item in configured if str(item).strip()
        )
    else:
        schemes = DEFAULT_LAUNCH_SCHEMES
    return schemes - BLOCKED_LAUNCH_SCHEMES


def _decoded_file_path(raw_path: str) -> str:
    """Décode les pourcent-encodings avant validation (évite %2e%2e → ..)."""
    path = raw_path or ""
    for _ in range(3):
        decoded = unquote(path)
        if decoded == path:
            break
        path = decoded
    return path


def is_safe_app_name(name: str) -> bool:
    cleaned = (name or "").strip()
    return (
        0 < len(cleaned) <= 128
        and not any(ord(char) < 32 for char in cleaned)
        and "/" not in cleaned
        and "\\" not in cleaned
    )


def is_allowed_home_path(raw: str, *, home: str) -> tuple[bool, str]:
    """Fichier ou dossier sous ``home`` uniquement (après résolution des liens)."""
    text = _decoded_file_path((raw or "").strip())
    if not text:
        return False, "chemin vide"
    if any(ord(char) < 32 for char in text):
        return False, "chemin invalide"
    try:
        target = Path(text).expanduser()
        if not target.is_absolute():
            return False, "chemin relatif interdit"
        resolved = target.resolve()
        home_path = Path(home).expanduser().resolve()
        if resolved != home_path and not resolved.is_relative_to(home_path):
            return False, "chemin hors du home"
    except (OSError, RuntimeError, ValueError):
        return False, "chemin invalide"
    return True, str(resolved)


def is_allowed_launch_url(url: str, *, home: str) -> tuple[bool, str]:
    """Valide une URL ou un schéma. ``file:`` retombe sur le home."""
    text = (url or "").strip()
    if not text:
        return False, "url vide"
    if len(text) > MAX_LAUNCH_TARGET_CHARS:
        return False, "cible trop longue"
    if any(ord(char) < 32 for char in text):
        return False, "url invalide"
    parsed = urlsplit(text)
    scheme = (parsed.scheme or "").lower()
    if not scheme:
        return False, "schéma manquant"
    if scheme in BLOCKED_LAUNCH_SCHEMES or scheme not in launch_schemes():
        return False, "schéma interdit"
    if scheme == "shortcuts":
        return False, "schéma réservé aux raccourcis enregistrés"
    if parsed.username is not None or parsed.password is not None:
        return False, "identifiants interdits dans l'url"
    if scheme == "file":
        path = _decoded_file_path(parsed.path or "")
        ok, detail = is_allowed_home_path(path, home=home)
        if not ok:
            return False, detail
        return True, text
    if scheme in {"http", "https"}:
        host = (parsed.hostname or "").strip().lower()
        if not host or _HTTP_HOST_RE.fullmatch(host) is None:
            return False, "hôte invalide"
        return True, text
    return True, text


def open_target_allowed(raw: str, *, home: str) -> tuple[bool, str]:
    """Cible d'un argv ``open`` : URL/schéma ou chemin $HOME."""
    text = (raw or "").strip()
    if not text:
        return False, "cible vide"
    if len(text) > MAX_LAUNCH_TARGET_CHARS:
        return False, "cible trop longue"
    if any(ord(char) < 32 for char in text):
        return False, "cible invalide"
    looks_like_url = "://" in text or (
        ":" in text and not text.startswith("/") and not text.startswith("~")
    )
    if looks_like_url:
        return is_allowed_launch_url(text, home=home)
    return is_allowed_home_path(text, home=home)


def _youtube_hint(app: str | None) -> bool:
    return (app or "").strip().lower() in YOUTUBE_APP_NAMES


def _youtube_channel_url(slug: str) -> str | None:
    handle = slug.strip().lstrip("@")
    if _HANDLE_RE.fullmatch(handle) is None:
        return None
    return f"https://www.youtube.com/@{handle}"


def resolve_launch_target(
    *,
    url: str | None = None,
    name: str | None = None,
    path: str | None = None,
    app: str | None = None,
    query: str | None = None,
    home: str,
) -> tuple[LaunchSpec | None, str]:
    """Retourne ``(spec, "")`` ou ``(None, raison)``. N'invente pas de cible."""
    url_text = (url or "").strip() or None
    path_text = (path or "").strip() or None
    query_text = (query or "").strip() or None
    app_text = (app or "").strip() or None
    name_text = (name or "").strip() or None
    host_app = app_text or name_text

    if host_app is not None and not is_safe_app_name(host_app):
        return None, "nom d'application invalide"

    if url_text and "://" not in url_text and ":" not in url_text and _youtube_hint(host_app):
        expanded = _youtube_channel_url(url_text)
        if expanded is None:
            return None, "identifiant youtube invalide"
        url_text = expanded

    if query_text and not url_text and not path_text:
        if not _youtube_hint(host_app):
            return None, "cible ambiguë"
        expanded = _youtube_channel_url(query_text)
        if expanded is None:
            return None, "identifiant youtube invalide"
        url_text = expanded

    if url_text:
        ok, detail = is_allowed_launch_url(url_text, home=home)
        if not ok:
            return None, detail
        return LaunchSpec(kind="url", target=url_text, app=host_app), ""

    if path_text:
        ok, detail = is_allowed_home_path(path_text, home=home)
        if not ok:
            return None, detail
        return LaunchSpec(kind="path", target=detail, app=host_app), ""

    if name_text or app_text:
        target_app = name_text or app_text
        assert target_app is not None
        return LaunchSpec(kind="app", target=target_app), ""

    return None, "cible manquante"
