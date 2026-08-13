"""Installation idempotente et vérifiable du binaire OpenCode épinglé."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tarfile
import tempfile
import time
from typing import Callable
from urllib.parse import urljoin, urlsplit
import zipfile

import httpx

from integrations.opencode.config import OpenCodeSettings, RuntimeLayout
from integrations.opencode.security.paths import (
    PathSecurityError,
    is_regular_file_without_links,
    safe_archive_member,
)

from ._files import (
    atomic_write_json,
    read_json_object,
    remove_tree_without_following_links,
)
from .release import ReleaseAsset, ReleaseManifest


class InstallationError(RuntimeError):
    pass


class ChecksumMismatchError(InstallationError):
    pass


class ArchiveSecurityError(InstallationError):
    pass


class TransientDownloadError(InstallationError):
    """Coupure réseau transitoire, seule classe d'erreur éligible au retry."""


@dataclass(frozen=True, slots=True)
class InstallResult:
    version: str
    asset_key: str
    binary_path: Path
    archive_sha256: str
    binary_sha256: str
    changed: bool


@dataclass(frozen=True, slots=True)
class VerificationReport:
    valid: bool
    version: str
    asset_key: str | None
    binary_path: Path
    errors: tuple[str, ...]


Downloader = Callable[[ReleaseAsset, Path, int], None]
_DOWNLOAD_ALLOWED_HOSTS = frozenset(
    {
        "github.com",
        "release-assets.githubusercontent.com",
        "objects.githubusercontent.com",
    }
)
_MAX_DOWNLOAD_REDIRECTS = 5
_MAX_DOWNLOAD_ATTEMPTS = 4
_DOWNLOAD_RETRY_BASE_SECONDS = 0.4
_DOWNLOAD_RETRY_MAX_SECONDS = 4.0
_DOWNLOAD_TOTAL_TIMEOUT_SECONDS = 90.0
_TRANSIENT_HTTP_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})
_TRANSIENT_HTTPX_TYPES = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
    httpx.WriteTimeout,
    httpx.PoolTimeout,
    httpx.RemoteProtocolError,
    httpx.ReadError,
    httpx.WriteError,
)


def _validated_download_url(url: str) -> str:
    parsed = urlsplit(url)
    try:
        port = parsed.port
    except ValueError as exc:
        raise InstallationError("URL de téléchargement OpenCode invalide") from exc
    if (
        parsed.scheme.lower() != "https"
        or parsed.hostname not in _DOWNLOAD_ALLOWED_HOSTS
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
    ):
        raise InstallationError("URL de téléchargement OpenCode non autorisée")
    return url


def download_retry_delay(attempt: int) -> float:
    """Backoff exponentiel borné avec jitter déterministe (0-based après échec)."""

    if attempt < 0:
        return 0.0
    exponential = _DOWNLOAD_RETRY_BASE_SECONDS * (2**attempt)
    capped = min(exponential, _DOWNLOAD_RETRY_MAX_SECONDS)
    jitter = 0.85 + 0.15 * (((attempt + 1) * 37) % 11) / 10.0
    return round(capped * jitter, 6)


def is_transient_download_error(exc: BaseException) -> bool:
    """True seulement pour les pannes réseau/HTTP transitoires, jamais 4xx stables."""

    if isinstance(exc, TransientDownloadError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        status = int(getattr(exc.response, "status_code", 0) or 0)
        return status in _TRANSIENT_HTTP_STATUS
    return isinstance(exc, _TRANSIENT_HTTPX_TYPES)


class InstallManager:
    def __init__(
        self,
        *,
        layout: RuntimeLayout | None = None,
        settings: OpenCodeSettings | None = None,
        manifest: ReleaseManifest | None = None,
    ) -> None:
        self.layout = layout or RuntimeLayout.default()
        self.settings = settings or OpenCodeSettings()
        self.manifest = manifest or ReleaseManifest.load()

    def install(
        self,
        *,
        archive_path: Path | None = None,
        platform_key: str | None = None,
        downloader: Downloader | None = None,
        verify_binary: bool = True,
    ) -> InstallResult:
        self.layout.ensure()
        asset = (
            self.manifest.asset(platform_key)
            if platform_key
            else self.manifest.asset_for_current_platform()
        )
        current = self.verify(execute_binary=verify_binary)
        state = read_json_object(self.layout.install_state_path)
        if current.valid and state.get("asset_key") == asset.key:
            return InstallResult(
                version=self.manifest.version,
                asset_key=asset.key,
                binary_path=self.layout.binary_path,
                archive_sha256=asset.sha256,
                binary_sha256=str(state["binary_sha256"]),
                changed=False,
            )

        owned_archive = False
        if archive_path is None:
            archive_path = self.layout.tmp_dir / asset.filename
            (downloader or self.download_asset)(
                asset, archive_path, self.settings.max_archive_bytes
            )
            owned_archive = True
        archive_path = archive_path.expanduser().resolve(strict=True)
        try:
            archive_digest = self._hash_file(
                archive_path, self.settings.max_archive_bytes
            )
            if archive_digest != asset.sha256:
                raise ChecksumMismatchError(
                    f"SHA-256 invalide pour {asset.filename}: attendu {asset.sha256}, obtenu {archive_digest}"
                )
            binary_source = self._extract_binary(archive_path, asset)
            if verify_binary:
                binary_source.chmod(0o700)
                self._verify_binary_version(binary_source)
            binary_digest = self._install_binary(binary_source)
            atomic_write_json(
                self.layout.install_state_path,
                {
                    "asset": asset.filename,
                    "asset_key": asset.key,
                    "archive_sha256": archive_digest,
                    "binary_path": str(self.layout.binary_path),
                    "binary_sha256": binary_digest,
                    "installed_at": datetime.now(timezone.utc).isoformat(),
                    "manifest_sha256": self.manifest.digest,
                    "version": self.manifest.version,
                },
            )
            return InstallResult(
                version=self.manifest.version,
                asset_key=asset.key,
                binary_path=self.layout.binary_path,
                archive_sha256=archive_digest,
                binary_sha256=binary_digest,
                changed=True,
            )
        finally:
            self._cleanup_extract_directories()
            if owned_archive:
                archive_path.unlink(missing_ok=True)

    def download_asset(
        self, asset: ReleaseAsset, destination: Path, max_bytes: int
    ) -> None:
        """Télécharge l'asset du manifest avec retry borné sur pannes transitoires."""

        started = time.monotonic()
        last_error: BaseException | None = None
        for attempt in range(_MAX_DOWNLOAD_ATTEMPTS):
            if time.monotonic() - started >= _DOWNLOAD_TOTAL_TIMEOUT_SECONDS:
                raise InstallationError(
                    "Délai total de téléchargement OpenCode dépassé"
                )
            try:
                self._download_asset_once(asset, destination, max_bytes)
                return
            except Exception as exc:
                last_error = exc
                remaining = _MAX_DOWNLOAD_ATTEMPTS - attempt - 1
                if remaining <= 0 or not is_transient_download_error(exc):
                    raise
                delay = download_retry_delay(attempt)
                if (
                    time.monotonic() - started + delay
                    >= _DOWNLOAD_TOTAL_TIMEOUT_SECONDS
                ):
                    raise InstallationError(
                        "Délai total de téléchargement OpenCode dépassé"
                    ) from exc
                time.sleep(delay)
        if last_error is not None:
            raise last_error
        raise InstallationError("Téléchargement OpenCode échoué")

    def _download_asset_once(
        self, asset: ReleaseAsset, destination: Path, max_bytes: int
    ) -> None:
        """Une tentative : fichier temporaire privé, remplacement atomique après validation."""

        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if destination.is_symlink() or destination.parent.is_symlink():
            raise InstallationError(
                "Destination de téléchargement symbolique interdite"
            )
        digest = hashlib.sha256()
        total = 0
        descriptor, temporary = tempfile.mkstemp(
            prefix=".download-", dir=destination.parent
        )
        os.chmod(temporary, 0o600)
        try:
            with (
                os.fdopen(descriptor, "wb") as handle,
                httpx.Client(
                    follow_redirects=False,
                    timeout=httpx.Timeout(60.0, connect=15.0),
                    trust_env=False,
                    headers={"User-Agent": "JARVIS-OpenCode-Installer/1"},
                ) as client,
            ):
                url = _validated_download_url(asset.url)
                for _ in range(_MAX_DOWNLOAD_REDIRECTS + 1):
                    with client.stream("GET", url) as response:
                        if response.is_redirect:
                            location = response.headers.get("location")
                            if not location:
                                raise InstallationError(
                                    "Redirection OpenCode sans destination"
                                )
                            url = _validated_download_url(
                                urljoin(str(response.url), location)
                            )
                            continue
                        try:
                            response.raise_for_status()
                        except httpx.HTTPStatusError as exc:
                            status = int(exc.response.status_code)
                            if status in _TRANSIENT_HTTP_STATUS:
                                raise TransientDownloadError(
                                    f"HTTP {status} transitoire pendant le téléchargement OpenCode"
                                ) from exc
                            raise InstallationError(
                                f"Téléchargement OpenCode refusé (HTTP {status})"
                            ) from exc
                        length = response.headers.get("content-length")
                        if length:
                            try:
                                declared_length = int(length)
                            except ValueError as exc:
                                raise InstallationError(
                                    "Content-Length OpenCode invalide"
                                ) from exc
                            if declared_length > max_bytes:
                                raise InstallationError(
                                    "Archive OpenCode trop volumineuse"
                                )
                        for chunk in response.iter_raw():
                            total += len(chunk)
                            if total > max_bytes:
                                raise InstallationError(
                                    "Archive OpenCode trop volumineuse"
                                )
                            digest.update(chunk)
                            handle.write(chunk)
                        handle.flush()
                        os.fsync(handle.fileno())
                        break
                else:
                    raise InstallationError(
                        "Trop de redirections de téléchargement OpenCode"
                    )
            if total != asset.size:
                raise InstallationError(
                    f"Taille d'asset inattendue: {total} au lieu de {asset.size}"
                )
            if digest.hexdigest() != asset.sha256:
                raise ChecksumMismatchError(
                    "Checksum du téléchargement OpenCode invalide"
                )
            os.replace(temporary, destination)
            if os.name != "nt":
                destination.chmod(0o600)
        except Exception:
            Path(temporary).unlink(missing_ok=True)
            raise
        finally:
            Path(temporary).unlink(missing_ok=True)

    def verify(self, *, execute_binary: bool = True) -> VerificationReport:
        errors: list[str] = []
        state = read_json_object(self.layout.install_state_path)
        asset_key = (
            state.get("asset_key") if isinstance(state.get("asset_key"), str) else None
        )
        binary = self.layout.binary_path
        if state.get("version") != self.manifest.version:
            errors.append("version installée absente ou inattendue")
        if state.get("manifest_sha256") != self.manifest.digest:
            errors.append("manifest installé différent du manifest versionné")
        if asset_key not in self.manifest.assets:
            errors.append("asset installé non autorisé")
        if not is_regular_file_without_links(binary):
            errors.append("binaire absent, symbolique ou avec hardlinks")
        elif self._hash_file(binary, self.settings.max_extracted_bytes) != state.get(
            "binary_sha256"
        ):
            errors.append("checksum du binaire installé invalide")
        elif execute_binary:
            try:
                self._verify_binary_version(binary)
            except InstallationError as exc:
                errors.append(str(exc))
        return VerificationReport(
            valid=not errors,
            version=self.manifest.version,
            asset_key=asset_key,
            binary_path=binary,
            errors=tuple(errors),
        )

    def clean(self) -> None:
        self.layout.ensure()
        for target in (
            self.layout.cache_dir,
            self.layout.logs_dir,
            self.layout.tmp_dir,
        ):
            remove_tree_without_following_links(
                target, boundary=self.layout.runtime_root
            )
            target.mkdir(mode=0o700)

    def uninstall(self) -> bool:
        runtime = self.layout.runtime_root
        if not runtime.exists() and not runtime.is_symlink():
            return False
        if runtime.is_symlink():
            raise InstallationError("Runtime symbolique: désinstallation refusée")
        remove_tree_without_following_links(runtime, boundary=runtime)
        return True

    def _hash_file(self, path: Path, max_bytes: int) -> str:
        digest = hashlib.sha256()
        total = 0
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                total += len(block)
                if total > max_bytes:
                    raise InstallationError(f"Fichier trop volumineux: {path.name}")
                digest.update(block)
        return digest.hexdigest()

    def _extract_binary(self, archive: Path, asset: ReleaseAsset) -> Path:
        extract_root = Path(
            tempfile.mkdtemp(prefix="extract-", dir=self.layout.tmp_dir)
        )
        try:
            if asset.archive == "zip":
                self._extract_zip(archive, extract_root)
            elif asset.archive == "tar.gz":
                self._extract_tar(archive, extract_root)
            else:
                raise ArchiveSecurityError(f"Archive non supportée: {asset.archive}")
        except (
            OSError,
            tarfile.TarError,
            zipfile.BadZipFile,
            PathSecurityError,
        ) as exc:
            raise ArchiveSecurityError(f"Archive OpenCode refusée: {exc}") from exc
        expected = "opencode.exe" if asset.key.startswith("windows-") else "opencode"
        candidates = [
            path
            for path in extract_root.rglob(expected)
            if path.is_file() and not path.is_symlink()
        ]
        if len(candidates) != 1:
            raise ArchiveSecurityError(
                f"L'archive doit contenir exactement un binaire {expected}"
            )
        return candidates[0]

    def _extract_zip(self, archive: Path, destination: Path) -> None:
        total = 0
        seen: set[Path] = set()
        with zipfile.ZipFile(archive) as bundle:
            for entry in bundle.infolist():
                member = safe_archive_member(entry.filename.rstrip("/"))
                target = destination.joinpath(*member.parts)
                if target in seen:
                    raise ArchiveSecurityError(f"Membre dupliqué: {entry.filename}")
                seen.add(target)
                mode = entry.external_attr >> 16
                if stat.S_ISLNK(mode):
                    raise ArchiveSecurityError(
                        f"Lien symbolique ZIP interdit: {entry.filename}"
                    )
                if entry.flag_bits & 0x1:
                    raise ArchiveSecurityError("Archive ZIP chiffrée interdite")
                if entry.is_dir():
                    target.mkdir(parents=True, exist_ok=True, mode=0o700)
                    continue
                file_type = stat.S_IFMT(mode)
                if file_type and file_type != stat.S_IFREG:
                    raise ArchiveSecurityError(
                        f"Type ZIP spécial interdit: {entry.filename}"
                    )
                total += entry.file_size
                if total > self.settings.max_extracted_bytes:
                    raise ArchiveSecurityError("Taille extraite excessive")
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                with bundle.open(entry, "r") as source, target.open("xb") as output:
                    shutil.copyfileobj(source, output, length=1024 * 1024)
                target.chmod(0o600)

    def _extract_tar(self, archive: Path, destination: Path) -> None:
        total = 0
        seen: set[Path] = set()
        with tarfile.open(archive, mode="r:gz") as bundle:
            for entry in bundle:
                member = safe_archive_member(entry.name.rstrip("/"))
                target = destination.joinpath(*member.parts)
                if target in seen:
                    raise ArchiveSecurityError(f"Membre dupliqué: {entry.name}")
                seen.add(target)
                if entry.isdir():
                    target.mkdir(parents=True, exist_ok=True, mode=0o700)
                    continue
                if not entry.isfile():
                    raise ArchiveSecurityError(
                        f"Lien ou type TAR spécial interdit: {entry.name}"
                    )
                total += entry.size
                if total > self.settings.max_extracted_bytes:
                    raise ArchiveSecurityError("Taille extraite excessive")
                source = bundle.extractfile(entry)
                if source is None:
                    raise ArchiveSecurityError(f"Membre TAR illisible: {entry.name}")
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                with source, target.open("xb") as output:
                    shutil.copyfileobj(source, output, length=1024 * 1024)
                target.chmod(0o600)

    def _install_binary(self, source: Path) -> str:
        destination = self.layout.binary_path
        descriptor, temporary = tempfile.mkstemp(
            prefix=".opencode-", dir=self.layout.bin_dir
        )
        try:
            with (
                source.open("rb") as input_handle,
                os.fdopen(descriptor, "wb") as output,
            ):
                shutil.copyfileobj(input_handle, output, length=1024 * 1024)
                output.flush()
                os.fsync(output.fileno())
            os.chmod(temporary, 0o700)
            os.replace(temporary, destination)
            if os.name != "nt":
                destination.chmod(0o700)
        finally:
            Path(temporary).unlink(missing_ok=True)
        return self._hash_file(destination, self.settings.max_extracted_bytes)

    def _verify_binary_version(self, binary: Path) -> None:
        try:
            completed = subprocess.run(
                [str(binary), "--version"],
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
                env={"PATH": os.environ.get("PATH", "")},
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise InstallationError(
                "Impossible d'exécuter le binaire OpenCode"
            ) from exc
        output = f"{completed.stdout}\n{completed.stderr}".strip()
        if completed.returncode != 0 or self.manifest.version not in output.split():
            raise InstallationError("Version du binaire OpenCode incompatible")

    def _cleanup_extract_directories(self) -> None:
        if not self.layout.tmp_dir.exists():
            return
        for child in self.layout.tmp_dir.iterdir():
            if child.name.startswith("extract-"):
                remove_tree_without_following_links(
                    child, boundary=self.layout.runtime_root
                )
