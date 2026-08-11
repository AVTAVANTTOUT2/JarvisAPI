"""WebDAV : chiffrement obligatoire, isolation, rétention et restauration."""

from __future__ import annotations

import asyncio
import base64
import hashlib
from pathlib import Path

import httpx
import pytest

_MAGIC = b"JARVIS-BACKUP-V2\x00"


@pytest.fixture
def cloud_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    values = {
        "BACKUP_DIR": str(backup_dir),
        "BACKUP_ENCRYPTION_ENABLED": True,
        "BACKUP_CLOUD_ENABLED": True,
        "BACKUP_CLOUD_PROVIDER": "webdav",
        "BACKUP_CLOUD_URL": "https://dav.example.test/jarvis/",
        "BACKUP_CLOUD_USERNAME": "jarvis",
        "BACKUP_CLOUD_PASSWORD": "secret-test-only",
        "BACKUP_CLOUD_BEARER_TOKEN": "",
        "BACKUP_CLOUD_KEEP": 2,
        "BACKUP_CLOUD_TIMEOUT_SECONDS": 5.0,
        "BACKUP_CLOUD_RETRY_ATTEMPTS": 3,
        "BACKUP_CLOUD_RETRY_DELAY_SECONDS": 0.0,
        "BACKUP_CLOUD_MAX_DOWNLOAD_MB": 4,
    }
    for key, value in values.items():
        monkeypatch.setattr(f"config.{key}", value)
    return backup_dir


def _dav_listing(*entries: tuple[str, int, str]) -> bytes:
    rows = [
        "<d:response><d:href>/jarvis/</d:href><d:propstat><d:prop>"
        "<d:resourcetype><d:collection/></d:resourcetype>"
        "</d:prop></d:propstat></d:response>"
    ]
    for name, size, modified in entries:
        rows.append(
            f"<d:response><d:href>/jarvis/{name}</d:href><d:propstat><d:prop>"
            f"<d:getcontentlength>{size}</d:getcontentlength>"
            f"<d:getlastmodified>{modified}</d:getlastmodified>"
            f'<d:getetag>"etag-{name}"</d:getetag>'
            "</d:prop></d:propstat></d:response>"
        )
    return (
        '<?xml version="1.0"?><d:multistatus xmlns:d="DAV:">'
        + "".join(rows)
        + "</d:multistatus>"
    ).encode()


def test_upload_verifies_size_and_rotates_only_current_profile(cloud_config: Path) -> None:
    from scripts.cloud_backup import upload_cloud_backup

    name = "jarvis-20260811-101010.db.enc"
    payload = _MAGIC + b"encrypted payload"
    source = cloud_config / name
    source.write_bytes(payload)
    deleted: list[str] = []
    uploaded: dict[str, bytes | str] = {}
    listing = _dav_listing(
        (name, len(payload), "Tue, 11 Aug 2026 10:10:10 GMT"),
        ("jarvis-20260811-090909.db.enc", 20, "Tue, 11 Aug 2026 09:09:09 GMT"),
        ("jarvis-20260811-080808.db.enc", 20, "Tue, 11 Aug 2026 08:08:08 GMT"),
        ("jarvis-alice-20260811-120000.db.enc", 20, "Tue, 11 Aug 2026 12:00:00 GMT"),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "MKCOL":
            return httpx.Response(405)
        if request.method == "PUT":
            uploaded["body"] = request.content
            uploaded["digest"] = request.headers["Digest"]
            return httpx.Response(201)
        if request.method == "HEAD":
            return httpx.Response(200, headers={"Content-Length": str(len(payload))})
        if request.method == "PROPFIND":
            return httpx.Response(207, content=listing)
        if request.method == "DELETE":
            deleted.append(request.url.path.rsplit("/", 1)[-1])
            return httpx.Response(204)
        raise AssertionError(request.method)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        report = upload_cloud_backup(source, client=client)

    expected_digest = base64.b64encode(hashlib.sha256(payload).digest()).decode()
    assert uploaded == {"body": payload, "digest": f"sha-256={expected_digest}"}
    assert report["ok"] is True and report["uploaded"] is True
    assert report["sha256"] == hashlib.sha256(payload).hexdigest()
    assert deleted == ["jarvis-20260811-080808.db.enc"]
    assert report["removed"] == deleted


def test_upload_retries_transient_webdav_failure(cloud_config: Path) -> None:
    from scripts.cloud_backup import upload_cloud_backup

    payload = _MAGIC + b"retry payload"
    source = cloud_config / "jarvis-20260811-101011.db.enc"
    source.write_bytes(payload)
    put_attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal put_attempts
        if request.method == "MKCOL":
            return httpx.Response(405)
        if request.method == "PUT":
            put_attempts += 1
            return httpx.Response(503 if put_attempts == 1 else 201)
        if request.method == "HEAD":
            return httpx.Response(200, headers={"Content-Length": str(len(payload))})
        if request.method == "PROPFIND":
            return httpx.Response(207, content=_dav_listing())
        raise AssertionError(request.method)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        report = upload_cloud_backup(source, client=client)

    assert put_attempts == 2
    assert report["uploaded"] is True


def test_list_filters_other_profiles_and_plaintext(cloud_config: Path) -> None:
    from scripts.cloud_backup import list_cloud_backups

    listing = _dav_listing(
        ("jarvis-20260811-101010.db.enc", 42, "Tue, 11 Aug 2026 10:10:10 GMT"),
        ("jarvis-20260811-090909.db", 42, "Tue, 11 Aug 2026 09:09:09 GMT"),
        ("jarvis-alice-20260811-120000.db.enc", 42, "Tue, 11 Aug 2026 12:00:00 GMT"),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "PROPFIND" and request.headers["Depth"] == "1"
        return httpx.Response(207, content=listing)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        backups = list_cloud_backups(client=client)
    assert [item["name"] for item in backups] == ["jarvis-20260811-101010.db.enc"]
    assert backups[0]["encrypted"] is True


def test_propfind_response_is_bounded(cloud_config: Path) -> None:
    from scripts.cloud_backup import CloudBackupError, list_cloud_backups

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(207, content=b"x" * (2 * 1024 * 1024 + 1))

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(CloudBackupError, match="trop volumineuse"):
            list_cloud_backups(client=client)


def test_propfind_rejects_dtd_and_entities(cloud_config: Path) -> None:
    from scripts.cloud_backup import CloudBackupError, list_cloud_backups

    malicious = b'<!DOCTYPE multistatus [<!ENTITY x "boom">]><multistatus>&x;</multistatus>'

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(207, content=malicious)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(CloudBackupError, match="non sûre"):
            list_cloud_backups(client=client)


def test_unverified_upload_is_removed_from_webdav(cloud_config: Path) -> None:
    from scripts.cloud_backup import CloudBackupError, upload_cloud_backup

    source = cloud_config / "jarvis-20260811-101010.db.enc"
    source.write_bytes(_MAGIC + b"encrypted payload")
    methods: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        if request.method == "MKCOL":
            return httpx.Response(405)
        if request.method == "PUT":
            return httpx.Response(201)
        if request.method == "HEAD":
            return httpx.Response(200, headers={"Content-Length": "1"})
        if request.method == "DELETE":
            return httpx.Response(204)
        raise AssertionError(request.method)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(CloudBackupError, match="taille WebDAV diffère"):
            upload_cloud_backup(source, client=client)
    assert methods == ["MKCOL", "PUT", "HEAD", "DELETE"]


@pytest.mark.parametrize(
    ("name", "payload", "message"),
    [
        ("jarvis-20260811-101010.db", b"plaintext", "Seule une sauvegarde chiffrée"),
        ("jarvis-20260811-101010.db.enc", b"not-v2", "enveloppe JARVIS-BACKUP-V2"),
        (
            "jarvis-alice-20260811-101010.db.enc",
            _MAGIC + b"encrypted",
            "profil actif",
        ),
    ],
)
def test_upload_rejects_plaintext_legacy_and_other_profiles(
    cloud_config: Path,
    name: str,
    payload: bytes,
    message: str,
) -> None:
    from scripts.cloud_backup import CloudBackupError, upload_cloud_backup

    source = cloud_config / name
    source.write_bytes(payload)
    with pytest.raises(CloudBackupError, match=message):
        upload_cloud_backup(source)


def test_restore_downloads_v2_then_uses_local_authenticated_pipeline(
    cloud_config: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts.cloud_backup import restore_cloud_backup

    name = "jarvis-20260811-101010.db.enc"
    payload = _MAGIC + b"fernet ciphertext"
    restored: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET" and request.url.path.endswith(name)
        return httpx.Response(200, content=payload)

    def fake_restore(local_name: str) -> dict:
        restored.append(local_name)
        assert (cloud_config / local_name).read_bytes() == payload
        return {"ok": True, "restored_from": local_name, "safety_backup": "safe.enc"}

    monkeypatch.setattr("scripts.db_maintenance.restore_backup", fake_restore)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        report = restore_cloud_backup(name, client=client)

    assert report["ok"] is True and report["cloud_name"] == name
    assert report["download"]["sha256"] == hashlib.sha256(payload).hexdigest()
    assert len(restored) == 1
    assert not (cloud_config / restored[0]).exists()


def test_cloud_configuration_is_https_authenticated_and_encrypted(
    cloud_config: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts.cloud_backup import CloudBackupError, cloud_backup_settings

    monkeypatch.setattr("config.BACKUP_CLOUD_URL", "http://dav.example.test/jarvis")
    with pytest.raises(CloudBackupError, match="HTTPS"):
        cloud_backup_settings()

    monkeypatch.setattr("config.BACKUP_CLOUD_URL", "https://dav.example.test/jarvis")
    monkeypatch.setattr("config.BACKUP_CLOUD_BEARER_TOKEN", "also-configured")
    with pytest.raises(CloudBackupError, match="exactement une authentification"):
        cloud_backup_settings()

    monkeypatch.setattr("config.BACKUP_CLOUD_BEARER_TOKEN", "")
    monkeypatch.setattr("config.BACKUP_ENCRYPTION_ENABLED", False)
    with pytest.raises(CloudBackupError, match="BACKUP_ENCRYPTION_ENABLED"):
        cloud_backup_settings()


def test_cloud_status_never_exposes_credentials(
    cloud_config: Path,
) -> None:
    from scripts.cloud_backup import cloud_backup_status

    status = cloud_backup_status()
    assert status == {
        "ok": True,
        "enabled": True,
        "configured": True,
        "provider": "webdav",
        "host": "dav.example.test",
        "keep": 2,
    }
    serialized = repr(status)
    assert "secret-test-only" not in serialized and "jarvis@" not in serialized


def test_cloud_api_is_network_free_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    from api.misc_status import api_cloud_backups_list

    monkeypatch.setattr("config.BACKUP_CLOUD_ENABLED", False)
    report = asyncio.run(api_cloud_backups_list())
    assert report["backups"] == []
    assert report["cloud"]["ok"] is True
    assert report["cloud"]["enabled"] is False


def test_cloud_api_routes_fail_closed_when_disabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import config
    import database
    import main
    from fastapi.testclient import TestClient
    from tests.conftest import authenticate

    db_path = tmp_path / "jarvis.db"
    monkeypatch.setattr(config, "DB_PATH", str(db_path))
    monkeypatch.setattr(database, "DB_PATH", db_path)
    monkeypatch.setattr(config, "BACKUP_CLOUD_ENABLED", False)
    database.init_db()

    with TestClient(main.app) as client:
        authenticate(client)
        listing = client.get("/api/backups/cloud")
        assert listing.status_code == 200
        assert listing.json()["backups"] == []

        restore = client.post("/api/backups/cloud/jarvis-20260811-101010.db.enc/restore")
        assert restore.status_code == 400
        assert restore.json()["detail"]["code"] == "cloud_backup_restore_failed"


def test_startup_validation_rejects_unsafe_cloud_config(
    cloud_config: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import config

    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setattr(config, "BACKUP_CLOUD_URL", "http://dav.example.test/jarvis")
    with pytest.raises(config.ConfigurationError, match="HTTPS"):
        config.validate_required_runtime_config()


def test_local_backup_reports_cloud_failure_without_losing_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import config
    import database
    from scripts.cloud_backup import CloudBackupError
    from scripts.db_maintenance import run_backup

    db_path = tmp_path / "jarvis.db"
    backup_dir = tmp_path / "backups"
    monkeypatch.setattr(config, "DB_PATH", str(db_path))
    monkeypatch.setattr(database, "DB_PATH", db_path)
    monkeypatch.setattr(config, "BACKUP_DIR", str(backup_dir))
    monkeypatch.setattr(config, "BACKUP_ENCRYPTION_ENABLED", True)
    monkeypatch.setattr(config, "BACKUP_ENCRYPTION_PASSPHRASE", "cloud-backup-test-passphrase-long")
    monkeypatch.setattr(config, "BACKUP_CLOUD_ENABLED", True)
    monkeypatch.setattr(
        "scripts.cloud_backup.upload_cloud_backup",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(CloudBackupError("hors ligne")),
    )
    database.init_db()

    report = run_backup()
    assert report["ok"] is False and report["local_ok"] is True
    assert report["cloud"] == {"enabled": True, "ok": False, "error": "hors ligne"}
    assert Path(report["path"]).is_file()
