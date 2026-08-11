"""Réplication WebDAV des seules sauvegardes Fernet V2 chiffrées."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Iterator
from urllib.parse import quote, unquote, urlsplit
from xml.etree import ElementTree

import httpx

import config
from core.file_security import ensure_private_directory, ensure_private_file
from database import current_profile_id

_BACKUP_V2_MAGIC = b"JARVIS-BACKUP-V2\x00"
_MAX_DAV_LIST_BYTES = 2 * 1024 * 1024
_DAV = "{DAV:}"


class CloudBackupError(RuntimeError):
    """Échec cloud exploitable sans exposer de credential ni de réponse brute."""


class CloudBackupTransientError(CloudBackupError):
    """Échec réseau ou HTTP temporaire qui autorise une nouvelle tentative."""


@dataclass(frozen=True)
class CloudBackupSettings:
    enabled: bool
    provider: str
    collection_url: str
    username: str
    password: str
    bearer_token: str
    keep: int
    timeout_seconds: float
    retry_attempts: int
    retry_delay_seconds: float
    max_download_bytes: int


def cloud_backup_settings() -> CloudBackupSettings:
    """Charge et valide la configuration sans réaliser d'I/O réseau."""
    enabled = bool(config.BACKUP_CLOUD_ENABLED)
    provider = str(config.BACKUP_CLOUD_PROVIDER or "").strip().lower()
    raw_url = str(config.BACKUP_CLOUD_URL or "").strip()
    username = str(config.BACKUP_CLOUD_USERNAME or "")
    password = str(config.BACKUP_CLOUD_PASSWORD or "")
    bearer_token = str(config.BACKUP_CLOUD_BEARER_TOKEN or "")
    settings = CloudBackupSettings(
        enabled=enabled,
        provider=provider,
        collection_url=raw_url.rstrip("/") + "/" if raw_url else "",
        username=username,
        password=password,
        bearer_token=bearer_token,
        keep=max(0, int(config.BACKUP_CLOUD_KEEP)),
        timeout_seconds=max(1.0, float(config.BACKUP_CLOUD_TIMEOUT_SECONDS)),
        retry_attempts=max(1, int(config.BACKUP_CLOUD_RETRY_ATTEMPTS)),
        retry_delay_seconds=max(0.0, float(config.BACKUP_CLOUD_RETRY_DELAY_SECONDS)),
        max_download_bytes=max(1, int(config.BACKUP_CLOUD_MAX_DOWNLOAD_MB)) * 1024 * 1024,
    )
    if not enabled:
        return settings
    if provider != "webdav":
        raise CloudBackupError("BACKUP_CLOUD_PROVIDER doit être 'webdav'")
    parsed = urlsplit(settings.collection_url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise CloudBackupError("BACKUP_CLOUD_URL doit être une URL HTTPS absolue")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise CloudBackupError(
            "BACKUP_CLOUD_URL ne doit contenir ni credentials, ni query, ni fragment"
        )
    has_basic = bool(username or password)
    if has_basic and not (username and password):
        raise CloudBackupError("Utilisateur et mot de passe WebDAV doivent être fournis ensemble")
    if has_basic == bool(bearer_token):
        raise CloudBackupError(
            "Configurez exactement une authentification WebDAV : Basic ou Bearer"
        )
    if not config.BACKUP_ENCRYPTION_ENABLED:
        raise CloudBackupError(
            "Le cloud exige BACKUP_ENCRYPTION_ENABLED=true (Fernet V2)"
        )
    return settings


def cloud_backup_status() -> dict:
    """Expose uniquement une configuration non secrète."""
    try:
        settings = cloud_backup_settings()
    except CloudBackupError as exc:
        return {"ok": False, "enabled": True, "configured": False, "error": str(exc)}
    return {
        "ok": True,
        "enabled": settings.enabled,
        "configured": settings.enabled,
        "provider": settings.provider or None,
        "host": urlsplit(settings.collection_url).hostname if settings.collection_url else None,
        "keep": settings.keep,
    }


def _auth(settings: CloudBackupSettings) -> httpx.Auth | None:
    if settings.bearer_token:
        return None
    return httpx.BasicAuth(settings.username, settings.password)


@contextmanager
def _webdav_client(
    settings: CloudBackupSettings,
    client: httpx.Client | None,
) -> Iterator[httpx.Client]:
    if client is not None:
        yield client
        return
    headers = {"User-Agent": "JARVIS-Encrypted-Backup/1"}
    if settings.bearer_token:
        headers["Authorization"] = f"Bearer {settings.bearer_token}"
    timeout = httpx.Timeout(
        settings.timeout_seconds,
        connect=settings.timeout_seconds,
        read=settings.timeout_seconds,
        write=settings.timeout_seconds,
        pool=settings.timeout_seconds,
    )
    with httpx.Client(
        auth=_auth(settings),
        headers=headers,
        timeout=timeout,
        follow_redirects=False,
    ) as owned:
        yield owned


def _remote_url(settings: CloudBackupSettings, name: str) -> str:
    return settings.collection_url + quote(name, safe="")


def _request(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    expected: set[int],
    **kwargs,
) -> httpx.Response:
    try:
        response = client.request(method, url, **kwargs)
    except httpx.HTTPError as exc:
        raise CloudBackupTransientError(
            f"WebDAV {method} inaccessible ({type(exc).__name__})"
        ) from exc
    if response.status_code not in expected:
        error_type = (
            CloudBackupTransientError
            if response.status_code in {408, 425, 429, 500, 502, 503, 504}
            else CloudBackupError
        )
        raise error_type(f"WebDAV {method} a répondu HTTP {response.status_code}")
    return response


def _ensure_collection(client: httpx.Client, settings: CloudBackupSettings) -> None:
    _request(
        client,
        "MKCOL",
        settings.collection_url,
        expected={200, 201, 204, 405},
    )


def _profile_backup_name(name: str, profile_id: str) -> bool:
    from scripts.db_maintenance import _is_profile_backup

    return _is_profile_backup(Path(name), profile_id)


def _encrypted_backup_path(path: str | Path, profile_id: str) -> Path:
    candidate = Path(path).expanduser().resolve()
    backup_dir = Path(config.BACKUP_DIR).expanduser().resolve()
    if candidate.parent != backup_dir or not candidate.is_file():
        raise CloudBackupError("Sauvegarde locale introuvable")
    if candidate.suffix != ".enc" or not _profile_backup_name(candidate.name, profile_id):
        raise CloudBackupError("Seule une sauvegarde chiffrée du profil actif est exportable")
    with candidate.open("rb") as stream:
        if stream.read(len(_BACKUP_V2_MAGIC)) != _BACKUP_V2_MAGIC:
            raise CloudBackupError("Le cloud exige une enveloppe JARVIS-BACKUP-V2")
    return candidate


def _file_chunks(path: Path, chunk_size: int = 1024 * 1024) -> Iterator[bytes]:
    with path.open("rb") as stream:
        while chunk := stream.read(chunk_size):
            yield chunk


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    for chunk in _file_chunks(path):
        digest.update(chunk)
    return digest.hexdigest()


def _parse_webdav_list(payload: bytes, profile_id: str) -> list[dict]:
    if len(payload) > _MAX_DAV_LIST_BYTES:
        raise CloudBackupError("Réponse WebDAV PROPFIND trop volumineuse")
    lowered = payload.lower()
    if b"<!doctype" in lowered or b"<!entity" in lowered:
        raise CloudBackupError("Réponse WebDAV PROPFIND non sûre")
    try:
        root = ElementTree.fromstring(payload)
    except ElementTree.ParseError as exc:
        raise CloudBackupError("Réponse WebDAV PROPFIND invalide") from exc
    backups: list[dict] = []
    for item in root.findall(f".//{_DAV}response"):
        href = item.findtext(f"{_DAV}href") or ""
        name = unquote(urlsplit(href).path.rstrip("/").rsplit("/", 1)[-1])
        if not _profile_backup_name(name, profile_id) or not name.endswith(".enc"):
            continue
        length_text = item.findtext(f".//{_DAV}getcontentlength") or "0"
        modified_text = item.findtext(f".//{_DAV}getlastmodified") or ""
        etag = (item.findtext(f".//{_DAV}getetag") or "").strip('"')
        try:
            size_bytes = max(0, int(length_text))
        except ValueError:
            size_bytes = 0
        try:
            modified_at = parsedate_to_datetime(modified_text).isoformat()
        except (TypeError, ValueError, OverflowError):
            modified_at = None
        backups.append(
            {
                "name": name,
                "size_bytes": size_bytes,
                "modified_at": modified_at,
                "etag": etag or None,
                "encrypted": True,
            }
        )
    backups.sort(key=lambda item: (item["modified_at"] or "", item["name"]), reverse=True)
    return backups


def _propfind_backups(
    client: httpx.Client,
    settings: CloudBackupSettings,
    profile_id: str,
) -> list[dict]:
    body = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<propfind xmlns="DAV:"><prop><getcontentlength/>'
        "<getlastmodified/><getetag/></prop></propfind>"
    )
    payload = bytearray()
    try:
        with client.stream(
            "PROPFIND",
            settings.collection_url,
            headers={"Depth": "1"},
            content=body,
        ) as response:
            if response.status_code != 207:
                raise CloudBackupError(
                    f"WebDAV PROPFIND a répondu HTTP {response.status_code}"
                )
            for chunk in response.iter_bytes():
                payload.extend(chunk)
                if len(payload) > _MAX_DAV_LIST_BYTES:
                    raise CloudBackupError("Réponse WebDAV PROPFIND trop volumineuse")
    except httpx.HTTPError as exc:
        raise CloudBackupError(
            f"WebDAV PROPFIND inaccessible ({type(exc).__name__})"
        ) from exc
    return _parse_webdav_list(bytes(payload), profile_id)


def list_cloud_backups(
    *,
    profile_id: str | None = None,
    client: httpx.Client | None = None,
) -> list[dict]:
    settings = cloud_backup_settings()
    if not settings.enabled:
        return []
    selected = profile_id or current_profile_id()
    with _webdav_client(settings, client) as webdav:
        return _propfind_backups(webdav, settings, selected)


def _delete_cloud_backup(
    name: str,
    settings: CloudBackupSettings,
    client: httpx.Client,
) -> None:
    _request(
        client,
        "DELETE",
        _remote_url(settings, name),
        expected={200, 202, 204, 404},
    )


def upload_cloud_backup(
    path: str | Path,
    *,
    profile_id: str | None = None,
    client: httpx.Client | None = None,
) -> dict:
    """Téléverse une enveloppe V2 puis applique la rétention du profil."""
    settings = cloud_backup_settings()
    if not settings.enabled:
        return {"enabled": False, "ok": True, "uploaded": False}
    selected = profile_id or current_profile_id()
    source = _encrypted_backup_path(path, selected)
    size_bytes = source.stat().st_size
    digest = _sha256_file(source)
    removed: list[str] = []
    with _webdav_client(settings, client) as webdav:
        head: httpx.Response | None = None
        for attempt in range(settings.retry_attempts):
            try:
                _ensure_collection(webdav, settings)
                _request(
                    webdav,
                    "PUT",
                    _remote_url(settings, source.name),
                    expected={200, 201, 204},
                    headers={
                        "Content-Type": "application/octet-stream",
                        "Content-Length": str(size_bytes),
                        "Digest": "sha-256="
                        + base64.b64encode(bytes.fromhex(digest)).decode("ascii"),
                    },
                    content=_file_chunks(source),
                )
                head = _request(
                    webdav,
                    "HEAD",
                    _remote_url(settings, source.name),
                    expected={200, 204},
                )
                break
            except CloudBackupTransientError:
                if attempt + 1 >= settings.retry_attempts:
                    raise
                time.sleep(settings.retry_delay_seconds * (2**attempt))
        if head is None:  # garde statique : la boucle réussit ou lève avant ce point
            raise CloudBackupError("WebDAV n'a pas confirmé le téléversement")
        try:
            remote_size = int(head.headers["Content-Length"])
        except (KeyError, ValueError):
            _delete_cloud_backup(source.name, settings, webdav)
            raise CloudBackupError("WebDAV HEAD ne confirme pas la taille téléversée") from None
        if remote_size != size_bytes:
            _delete_cloud_backup(source.name, settings, webdav)
            raise CloudBackupError("La taille WebDAV diffère de la sauvegarde locale")

        if settings.keep > 0:
            backups = _propfind_backups(webdav, settings, selected)
            for item in backups[settings.keep :]:
                _delete_cloud_backup(item["name"], settings, webdav)
                removed.append(item["name"])
    return {
        "enabled": True,
        "ok": True,
        "uploaded": True,
        "name": source.name,
        "size_bytes": size_bytes,
        "sha256": digest,
        "removed": removed,
    }


def _download_cloud_backup(
    name: str,
    destination: Path,
    settings: CloudBackupSettings,
    client: httpx.Client,
) -> dict:
    digest = hashlib.sha256()
    size_bytes = 0
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd = os.open(destination, flags, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            try:
                with client.stream("GET", _remote_url(settings, name)) as response:
                    if response.status_code != 200:
                        raise CloudBackupError(
                            f"WebDAV GET a répondu HTTP {response.status_code}"
                        )
                    for chunk in response.iter_bytes(1024 * 1024):
                        size_bytes += len(chunk)
                        if size_bytes > settings.max_download_bytes:
                            raise CloudBackupError("Sauvegarde cloud trop volumineuse")
                        digest.update(chunk)
                        stream.write(chunk)
            except httpx.HTTPError as exc:
                raise CloudBackupError(
                    f"WebDAV GET inaccessible ({type(exc).__name__})"
                ) from exc
        ensure_private_file(destination)
        with destination.open("rb") as stream:
            if stream.read(len(_BACKUP_V2_MAGIC)) != _BACKUP_V2_MAGIC:
                raise CloudBackupError("La sauvegarde cloud n'est pas une enveloppe V2")
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    return {"size_bytes": size_bytes, "sha256": digest.hexdigest()}


def restore_cloud_backup(
    name: str,
    *,
    profile_id: str | None = None,
    client: httpx.Client | None = None,
) -> dict:
    """Télécharge puis restaure via le pipeline local authentifié existant."""
    settings = cloud_backup_settings()
    if not settings.enabled:
        raise CloudBackupError("Sauvegarde cloud désactivée")
    selected = profile_id or current_profile_id()
    if not _profile_backup_name(name, selected) or not name.endswith(".enc"):
        raise CloudBackupError("Sauvegarde cloud introuvable pour ce profil")
    backup_dir = ensure_private_directory(Path(config.BACKUP_DIR))
    prefix = "jarvis" if selected == "default" else f"jarvis-{selected}"
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    temporary = backup_dir / f"{prefix}-{stamp}-{secrets.randbelow(1_000_000_000)}.db.enc"
    try:
        with _webdav_client(settings, client) as webdav:
            download = _download_cloud_backup(name, temporary, settings, webdav)
        from scripts.db_maintenance import restore_backup

        report = restore_backup(temporary.name)
        if not report.get("ok"):
            raise CloudBackupError(str(report.get("error") or "Restauration locale impossible"))
        return {**report, "cloud_name": name, "download": download}
    finally:
        temporary.unlink(missing_ok=True)


def render_cloud_report(report: dict) -> str:
    """Sérialisation stable utilisée par le CLI et les diagnostics."""
    return json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
