"""Lecture stricte du manifest de release OpenCode épinglé."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import platform
import re
from typing import Any, Mapping
from urllib.parse import urlsplit


DEFAULT_MANIFEST_PATH = Path(__file__).resolve().parents[1] / "release-manifest.json"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
_PINNED_VERSION = "1.18.16"
_MINIMUM_SAFE_VERSION = "1.1.10"
_PINNED_COMMIT = "a3647eb025c7615159d417dcc49fc39fdaeba65b"
_LICENSE_BLOB_SHA = "6439474beed8e0271df9862eff97ffd70ec2464c"
_EXPECTED_ASSET_NAMES = {
    "darwin-arm64": ("opencode-darwin-arm64.zip", "zip"),
    "darwin-x64": ("opencode-darwin-x64.zip", "zip"),
    "linux-arm64": ("opencode-linux-arm64.tar.gz", "tar.gz"),
    "linux-x64": ("opencode-linux-x64.tar.gz", "tar.gz"),
    "windows-x64": ("opencode-windows-x64.zip", "zip"),
}
_EXPECTED_ADVISORIES = [
    {
        "advisory": "GHSA-c83v-7274-4vgp",
        "cve": "CVE-2026-22813",
        "fixed_version": "1.1.10",
        "severity": "critical",
        "summary": "Web UI XSS leading to remote code execution",
    },
    {
        "advisory": "GHSA-vxw4-wv6m-9hhh",
        "cve": "CVE-2026-22812",
        "fixed_version": "1.0.216",
        "severity": "high",
        "summary": "Unauthenticated server exposure",
    },
]


class ManifestError(ValueError):
    pass


class UnsupportedPlatformError(ManifestError):
    pass


@dataclass(frozen=True, slots=True)
class ReleaseAsset:
    key: str
    filename: str
    archive: str
    url: str
    sha256: str
    size: int

    @classmethod
    def from_mapping(
        cls, key: str, value: Mapping[str, Any], *, tag: str
    ) -> "ReleaseAsset":
        expected = {"filename", "archive", "url", "sha256", "size"}
        if set(value) != expected:
            raise ManifestError(f"Champs d'asset invalides pour {key}")
        filename = value.get("filename")
        archive = value.get("archive")
        url = value.get("url")
        sha256 = value.get("sha256")
        size = value.get("size")
        if (
            not isinstance(filename, str)
            or not isinstance(archive, str)
            or not isinstance(url, str)
            or not isinstance(sha256, str)
        ):
            raise ManifestError(f"Types d'asset invalides pour {key}")
        if isinstance(size, bool) or not isinstance(size, int):
            raise ManifestError(f"Taille invalide pour {key}")
        asset = cls(
            key=key,
            filename=filename,
            archive=archive,
            url=url,
            sha256=sha256,
            size=size,
        )
        expected_name = _EXPECTED_ASSET_NAMES.get(key)
        if expected_name is None or (asset.filename, asset.archive) != expected_name:
            raise ManifestError(f"Format d'archive interdit: {asset.archive}")
        if not _SHA256_RE.fullmatch(asset.sha256):
            raise ManifestError(f"SHA-256 invalide pour {key}")
        if asset.size <= 0:
            raise ManifestError(f"Taille invalide pour {key}")
        expected_url = f"https://github.com/anomalyco/opencode/releases/download/{tag}/{asset.filename}"
        if asset.url != expected_url or urlsplit(asset.url).hostname != "github.com":
            raise ManifestError(f"URL d'asset inattendue pour {key}")
        suffix = ".zip" if asset.archive == "zip" else ".tar.gz"
        if not asset.filename.endswith(suffix):
            raise ManifestError(f"Extension incohérente pour {key}")
        return asset


@dataclass(frozen=True, slots=True)
class ReleaseManifest:
    path: Path
    schema_version: int
    version: str
    tag: str
    published_at: str
    minimum_secure_version: str
    repository: str
    license_name: str
    verified_at: str
    assets: Mapping[str, ReleaseAsset]
    digest: str

    @classmethod
    def load(cls, path: Path = DEFAULT_MANIFEST_PATH) -> "ReleaseManifest":
        try:
            raw = path.read_bytes()
            value = json.loads(raw)
        except (OSError, json.JSONDecodeError) as exc:
            raise ManifestError(f"Manifest illisible: {path}") from exc
        if not isinstance(value, dict) or value.get("schema_version") != 1:
            raise ManifestError("Version de schéma du manifest non supportée")
        if set(value) != {
            "assets",
            "license",
            "release",
            "schema_version",
            "upstream",
            "verified_at",
            "vulnerability_review",
        }:
            raise ManifestError("Sections racine inattendues dans le manifest")
        release = value.get("release")
        upstream = value.get("upstream")
        license_value = value.get("license")
        assets_value = value.get("assets")
        if (
            not isinstance(release, dict)
            or not isinstance(upstream, dict)
            or not isinstance(license_value, dict)
            or not isinstance(assets_value, dict)
        ):
            raise ManifestError("Sections obligatoires du manifest absentes")
        if (
            set(release)
            != {
                "minimum_safe_version",
                "minimum_secure_version",
                "published_at",
                "tag",
                "version",
            }
            or release.get("published_at") != "2026-08-10T06:07:08Z"
        ):
            raise ManifestError("Métadonnées de release inattendues")
        version = release.get("version")
        tag = release.get("tag")
        if (
            not isinstance(version, str)
            or not isinstance(tag, str)
            or not _VERSION_RE.fullmatch(version)
            or tag != f"v{version}"
            or version != _PINNED_VERSION
        ):
            raise ManifestError("Version ou tag du manifest invalide")
        minimum_safe_version = release.get("minimum_safe_version")
        minimum_secure_version = release.get("minimum_secure_version")
        if minimum_safe_version != minimum_secure_version:
            raise ManifestError("Les bornes minimale sûre et sécurité divergent")
        if not isinstance(minimum_secure_version, str) or not _VERSION_RE.fullmatch(
            minimum_secure_version
        ):
            raise ManifestError("Version minimale sûre invalide")
        if minimum_secure_version != _MINIMUM_SAFE_VERSION:
            raise ManifestError("Borne minimale sûre inattendue")
        if tuple(map(int, version.split("."))) < tuple(
            map(int, minimum_secure_version.split("."))
        ):
            raise ManifestError(
                "La version installée ne satisfait pas la version minimale sûre"
            )
        if upstream != {
            "advisories": "https://github.com/anomalyco/opencode/security/advisories",
            "commit": _PINNED_COMMIT,
            "release": f"https://github.com/anomalyco/opencode/releases/tag/v{_PINNED_VERSION}",
            "repository": "https://github.com/anomalyco/opencode",
        }:
            raise ManifestError("Dépôt upstream inattendu")
        if license_value != {
            "git_blob_sha": _LICENSE_BLOB_SHA,
            "name": "MIT",
            "source": f"https://raw.githubusercontent.com/anomalyco/opencode/v{_PINNED_VERSION}/LICENSE",
        }:
            raise ManifestError("Licence upstream inattendue")
        if value.get("vulnerability_review") != _EXPECTED_ADVISORIES:
            raise ManifestError("Revue de vulnérabilités upstream inattendue")
        assets = {
            key: ReleaseAsset.from_mapping(key, asset, tag=tag)
            for key, asset in assets_value.items()
            if isinstance(key, str) and isinstance(asset, dict)
        }
        if set(assets) != set(_EXPECTED_ASSET_NAMES):
            raise ManifestError("Allowlist plateforme/architecture inattendue")
        return cls(
            path=path.resolve(),
            schema_version=1,
            version=version,
            tag=tag,
            published_at=str(release.get("published_at", "")),
            minimum_secure_version=minimum_secure_version,
            repository=upstream["repository"],
            license_name=license_value["name"],
            verified_at=str(value.get("verified_at", "")),
            assets=assets,
            digest=hashlib.sha256(raw).hexdigest(),
        )

    @property
    def minimum_safe_version(self) -> str:
        """Borne sémantique documentée; alias lisible de la contrainte historique."""

        return self.minimum_secure_version

    def asset_for_current_platform(
        self,
        *,
        system: str | None = None,
        machine: str | None = None,
    ) -> ReleaseAsset:
        system_value = (system or platform.system()).strip().lower()
        machine_value = (machine or platform.machine()).strip().lower()
        systems = {"darwin": "darwin", "linux": "linux", "windows": "windows"}
        machines = {
            "arm64": "arm64",
            "aarch64": "arm64",
            "x86_64": "x64",
            "amd64": "x64",
            "x64": "x64",
        }
        os_name = systems.get(system_value)
        arch = machines.get(machine_value)
        if os_name is None or arch is None:
            raise UnsupportedPlatformError(
                f"Plateforme OpenCode non autorisée: {system_value}/{machine_value}"
            )
        key = f"{os_name}-{arch}"
        try:
            return self.assets[key]
        except KeyError as exc:
            raise UnsupportedPlatformError(
                f"Asset OpenCode non autorisé: {key}"
            ) from exc

    def asset(self, key: str) -> ReleaseAsset:
        try:
            return self.assets[key]
        except KeyError as exc:
            raise UnsupportedPlatformError(
                f"Asset OpenCode non autorisé: {key}"
            ) from exc
