from __future__ import annotations

import hashlib
from dataclasses import replace
import os
from pathlib import Path
import shutil
import tarfile
import zipfile

import pytest

from integrations.opencode.config import OpenCodeSettings, RuntimeLayout
import httpx

from integrations.opencode.lifecycle.install import (
    ArchiveSecurityError,
    ChecksumMismatchError,
    InstallManager,
    InstallationError,
    TransientDownloadError,
    _validated_download_url,
    download_retry_delay,
    is_transient_download_error,
)
from integrations.opencode.lifecycle.release import ReleaseAsset, ReleaseManifest


def _layout(tmp_path: Path) -> RuntimeLayout:
    root = tmp_path / "plugin"
    root.mkdir()
    return RuntimeLayout.from_integration_root(root)


def _manifest(
    archive: Path, *, digest: str | None = None, version: str = "9.9.9"
) -> ReleaseManifest:
    content = archive.read_bytes()
    asset = ReleaseAsset(
        key="linux-x64",
        filename="opencode-linux-x64.zip",
        archive="zip",
        url="https://github.com/example/opencode.zip",
        sha256=digest or hashlib.sha256(content).hexdigest(),
        size=len(content),
    )
    return ReleaseManifest(
        path=archive,
        schema_version=1,
        version=version,
        tag=f"v{version}",
        published_at="2026-01-01T00:00:00Z",
        minimum_secure_version="1.1.10",
        repository="https://github.com/anomalyco/opencode",
        license_name="MIT",
        verified_at="2026-01-01",
        assets={asset.key: asset},
        digest="manifest-digest",
    )


def _zip(path: Path, entries: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w") as bundle:
        for name, value in entries.items():
            bundle.writestr(name, value)


def test_install_is_checksum_verified_private_and_idempotent(tmp_path: Path) -> None:
    archive = tmp_path / "opencode.zip"
    _zip(archive, {"dist/opencode": b"#!/bin/sh\necho 9.9.9\n"})
    layout = _layout(tmp_path)
    manager = InstallManager(
        layout=layout,
        settings=OpenCodeSettings(),
        manifest=_manifest(archive),
    )

    first = manager.install(
        archive_path=archive, platform_key="linux-x64", verify_binary=os.name != "nt"
    )
    second = manager.install(platform_key="linux-x64", verify_binary=False)

    assert first.changed
    assert not second.changed
    assert layout.binary_path.read_bytes().startswith(b"#!/bin/sh")
    assert manager.verify(execute_binary=False).valid
    if os.name != "nt":
        assert layout.binary_path.stat().st_mode & 0o777 == 0o700
        assert layout.install_state_path.stat().st_mode & 0o777 == 0o600


def test_install_rejects_checksum_mismatch_before_extraction(tmp_path: Path) -> None:
    archive = tmp_path / "opencode.zip"
    _zip(archive, {"opencode": b"binary"})
    manager = InstallManager(
        layout=_layout(tmp_path), manifest=_manifest(archive, digest="0" * 64)
    )

    with pytest.raises(ChecksumMismatchError):
        manager.install(
            archive_path=archive, platform_key="linux-x64", verify_binary=False
        )

    assert not manager.layout.binary_path.exists()


@pytest.mark.skipif(os.name == "nt", reason="fixture exécutable POSIX")
def test_failed_version_check_preserves_the_previous_binary(tmp_path: Path) -> None:
    archive = tmp_path / "opencode.zip"
    _zip(archive, {"opencode": b"#!/bin/sh\necho 0.0.0\n"})
    layout = _layout(tmp_path)
    layout.ensure()
    layout.binary_path.write_bytes(b"previous-binary")
    layout.binary_path.chmod(0o700)
    manager = InstallManager(layout=layout, manifest=_manifest(archive))

    with pytest.raises(InstallationError, match="Version du binaire OpenCode"):
        manager.install(
            archive_path=archive, platform_key="linux-x64", verify_binary=True
        )

    assert layout.binary_path.read_bytes() == b"previous-binary"


def test_installer_download_boundary_is_injectable_and_stays_inside_runtime(
    tmp_path: Path,
) -> None:
    archive = tmp_path / "source.zip"
    _zip(archive, {"opencode": b"binary"})
    manager = InstallManager(layout=_layout(tmp_path), manifest=_manifest(archive))
    calls: list[Path] = []

    def offline_downloader(
        asset: ReleaseAsset, destination: Path, max_bytes: int
    ) -> None:
        calls.append(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(archive, destination)

    result = manager.install(
        platform_key="linux-x64",
        downloader=offline_downloader,
        verify_binary=False,
    )

    assert result.changed
    assert calls == [manager.layout.tmp_dir / "opencode-linux-x64.zip"]
    assert not calls[0].exists()


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/anomalyco/opencode/releases/download/v1.18.16/opencode.zip",
        "https://127.0.0.1/opencode.zip",
        "https://example.invalid/opencode.zip",
        "https://user:password@github.com/opencode.zip",
        "https://github.com:8443/opencode.zip",
    ],
)
def test_installer_rejects_non_allowlisted_download_hops(url: str) -> None:
    with pytest.raises(InstallationError):
        _validated_download_url(url)


def test_installer_accepts_only_official_download_hosts() -> None:
    url = "https://release-assets.githubusercontent.com/github-production-release-asset/opencode.zip"
    assert _validated_download_url(url) == url


def test_install_rejects_archive_traversal_even_with_valid_checksum(
    tmp_path: Path,
) -> None:
    archive = tmp_path / "opencode.zip"
    _zip(archive, {"../opencode": b"binary"})
    manager = InstallManager(layout=_layout(tmp_path), manifest=_manifest(archive))

    with pytest.raises(ArchiveSecurityError):
        manager.install(
            archive_path=archive, platform_key="linux-x64", verify_binary=False
        )

    assert not (tmp_path / "opencode").exists()


def test_install_rejects_zip_symlinks(tmp_path: Path) -> None:
    archive = tmp_path / "opencode.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        link = zipfile.ZipInfo("opencode")
        link.create_system = 3
        link.external_attr = 0o120777 << 16
        bundle.writestr(link, "../../outside")
    manager = InstallManager(layout=_layout(tmp_path), manifest=_manifest(archive))

    with pytest.raises(ArchiveSecurityError):
        manager.install(
            archive_path=archive, platform_key="linux-x64", verify_binary=False
        )


def test_tar_extraction_rejects_links_without_following_them(tmp_path: Path) -> None:
    archive = tmp_path / "opencode.tar.gz"
    with tarfile.open(archive, "w:gz") as bundle:
        link = tarfile.TarInfo("opencode")
        link.type = tarfile.SYMTYPE
        link.linkname = "../../outside"
        bundle.addfile(link)
    content = archive.read_bytes()
    asset = ReleaseAsset(
        key="linux-x64",
        filename="opencode-linux-x64.tar.gz",
        archive="tar.gz",
        url="https://github.com/example/opencode.tar.gz",
        sha256=hashlib.sha256(content).hexdigest(),
        size=len(content),
    )
    manifest = replace(_manifest(archive), assets={asset.key: asset})
    manager = InstallManager(layout=_layout(tmp_path), manifest=manifest)

    with pytest.raises(ArchiveSecurityError):
        manager.install(
            archive_path=archive, platform_key="linux-x64", verify_binary=False
        )


def test_uninstall_never_traverses_runtime_symlink(tmp_path: Path) -> None:
    root = tmp_path / "plugin"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "sentinel"
    sentinel.write_text("keep")
    (root / ".runtime").symlink_to(outside, target_is_directory=True)
    layout = RuntimeLayout(
        integration_root=root.resolve(),
        runtime_root=root / ".runtime",
    )
    manager = InstallManager(layout=layout, manifest=ReleaseManifest.load())

    with pytest.raises(Exception, match="symbolique"):
        manager.uninstall()

    assert sentinel.read_text() == "keep"


def _http_status_error(status: int) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://github.com/anomalyco/opencode/releases/x")
    response = httpx.Response(status, request=request)
    return httpx.HTTPStatusError("http error", request=request, response=response)


def test_download_retry_delay_is_bounded_and_deterministic() -> None:
    first = download_retry_delay(0)
    second = download_retry_delay(1)
    late = download_retry_delay(8)
    assert 0 < first < 1
    assert second > first
    assert late <= 4.0
    assert download_retry_delay(8) == late


def test_only_transient_network_errors_are_retryable() -> None:
    assert is_transient_download_error(httpx.RemoteProtocolError("disconnected"))
    assert is_transient_download_error(httpx.ConnectTimeout("timeout"))
    assert is_transient_download_error(TransientDownloadError("HTTP 503 transitoire"))
    assert is_transient_download_error(_http_status_error(503))
    assert is_transient_download_error(_http_status_error(429))
    assert not is_transient_download_error(_http_status_error(404))
    assert not is_transient_download_error(_http_status_error(401))
    assert not is_transient_download_error(ChecksumMismatchError("checksum"))
    assert not is_transient_download_error(
        InstallationError("URL de téléchargement OpenCode non autorisée")
    )
    assert not is_transient_download_error(
        InstallationError("Archive OpenCode trop volumineuse")
    )


def test_download_retries_transient_disconnect_then_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = {"n": 0}
    sleeps: list[float] = []

    def once(
        self: InstallManager, asset: ReleaseAsset, destination: Path, max_bytes: int
    ) -> None:
        calls["n"] += 1
        if calls["n"] < 3:
            raise httpx.RemoteProtocolError("Server disconnected")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"archive")

    monkeypatch.setattr(InstallManager, "_download_asset_once", once)
    monkeypatch.setattr(
        "integrations.opencode.lifecycle.install.time.sleep",
        lambda delay: sleeps.append(delay),
    )
    archive = tmp_path / "opencode.zip"
    _zip(archive, {"dist/opencode": b"ok"})
    asset = _manifest(archive).asset("linux-x64")
    destination = tmp_path / "out" / asset.filename
    InstallManager(layout=_layout(tmp_path)).download_asset(
        asset, destination, max_bytes=1_000_000
    )
    assert calls["n"] == 3
    assert sleeps == [download_retry_delay(0), download_retry_delay(1)]
    assert destination.read_bytes() == b"archive"


def test_download_does_not_retry_http_4xx(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = {"n": 0}

    def once(
        self: InstallManager, asset: ReleaseAsset, destination: Path, max_bytes: int
    ) -> None:
        calls["n"] += 1
        raise InstallationError("Téléchargement OpenCode refusé (HTTP 404)")

    monkeypatch.setattr(InstallManager, "_download_asset_once", once)
    monkeypatch.setattr(
        "integrations.opencode.lifecycle.install.time.sleep",
        lambda _delay: None,
    )
    archive = tmp_path / "opencode.zip"
    _zip(archive, {"dist/opencode": b"ok"})
    asset = _manifest(archive).asset("linux-x64")
    with pytest.raises(InstallationError, match="HTTP 404"):
        InstallManager(layout=_layout(tmp_path)).download_asset(
            asset, tmp_path / "out.zip", max_bytes=1_000_000
        )
    assert calls["n"] == 1


def test_download_gives_up_after_bounded_transient_attempts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = {"n": 0}

    def once(
        self: InstallManager, asset: ReleaseAsset, destination: Path, max_bytes: int
    ) -> None:
        calls["n"] += 1
        raise httpx.RemoteProtocolError("still down")

    monkeypatch.setattr(InstallManager, "_download_asset_once", once)
    monkeypatch.setattr(
        "integrations.opencode.lifecycle.install.time.sleep",
        lambda _delay: None,
    )
    archive = tmp_path / "opencode.zip"
    _zip(archive, {"dist/opencode": b"ok"})
    asset = _manifest(archive).asset("linux-x64")
    with pytest.raises(httpx.RemoteProtocolError, match="still down"):
        InstallManager(layout=_layout(tmp_path)).download_asset(
            asset, tmp_path / "out.zip", max_bytes=1_000_000
        )
    assert calls["n"] == 4


def test_failed_download_does_not_leave_partial_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def once(
        self: InstallManager, asset: ReleaseAsset, destination: Path, max_bytes: int
    ) -> None:
        raise httpx.RemoteProtocolError("cut")

    monkeypatch.setattr(InstallManager, "_download_asset_once", once)
    monkeypatch.setattr(
        "integrations.opencode.lifecycle.install.time.sleep",
        lambda _delay: None,
    )
    archive = tmp_path / "opencode.zip"
    _zip(archive, {"dist/opencode": b"ok"})
    asset = _manifest(archive).asset("linux-x64")
    destination = tmp_path / "partial.zip"
    with pytest.raises(httpx.RemoteProtocolError):
        InstallManager(layout=_layout(tmp_path)).download_asset(
            asset, destination, max_bytes=1_000_000
        )
    assert not destination.exists()
