"""Contrats de sécurité et de cycle de vie des uploads."""

from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path

import pytest

from tests.conftest import authenticate


@pytest.fixture
def upload_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    db_path = tmp_path / "uploads.db"
    upload_root = tmp_path / "uploads"
    monkeypatch.setattr("config.DB_PATH", str(db_path))
    monkeypatch.setattr("database.DB_PATH", db_path)
    monkeypatch.setattr("config.UPLOAD_DIR", str(upload_root))
    monkeypatch.setattr("config.UPLOAD_MAX_BYTES", 1024)
    monkeypatch.setattr("config.UPLOAD_QUOTA_BYTES", 4096)
    monkeypatch.setattr("config.UPLOAD_ORPHAN_GRACE_SECONDS", 0)

    from database import init_db

    init_db()
    return db_path, upload_root


def _client():
    import main
    from fastapi.testclient import TestClient

    return TestClient(main.app)


def _files_under(root: Path) -> list[Path]:
    return [path for path in root.rglob("*") if path.is_file()]


def test_conversation_upload_normalizes_name_uses_uuid_and_deletes_file(upload_env):
    from database import create_conversation, get_db

    _, upload_root = upload_env
    conversation_id = create_conversation()

    with _client() as client:
        authenticate(client)
        uploaded = client.post(
            f"/api/conversations/{conversation_id}/upload",
            files={"file": ("../../notes privées.txt", b"contenu local", "text/plain")},
        )

        assert uploaded.status_code == 200, uploaded.text
        assert uploaded.json()["filename"] == "notes privées.txt"

        with get_db() as conn:
            row = conn.execute(
                "SELECT * FROM conversation_documents WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()
        stored_path = Path(row["file_path"])
        assert stored_path.read_bytes() == b"contenu local"
        assert stored_path.parent == upload_root / "conversations" / str(conversation_id)
        assert re.fullmatch(r"[0-9a-f]{32}\.txt", row["filename"])
        assert row["original_name"] == "notes privées.txt"

        deleted = client.delete(f"/api/conversations/{conversation_id}")

    assert deleted.status_code == 200
    assert not stored_path.exists()
    assert _files_under(upload_root) == []


def test_upload_name_normalization_handles_windows_paths_and_controls():
    from jarvis.uploads import normalize_upload_name

    name, extension = normalize_upload_name("C:\\temp\\notes\u0000 privées.TXT")

    assert name == "notes privées.txt"
    assert extension == ".txt"


def test_upload_reader_is_consumed_in_bounded_chunks(upload_env):
    from jarvis.uploads import CHUNK_SIZE, CONVERSATION_EXTENSIONS, store_upload

    class ChunkOnlyUpload:
        filename = "notes.txt"
        content_type = "text/plain"

        def __init__(self):
            self.parts = iter((b"abc", b"def", b""))
            self.read_sizes: list[int] = []

        async def read(self, size: int = -1):
            self.read_sizes.append(size)
            assert size == CHUNK_SIZE
            return next(self.parts)

    upload = ChunkOnlyUpload()
    stored = asyncio.run(
        store_upload(
            upload,
            namespace="conversations/1",
            allowed_extensions=CONVERSATION_EXTENSIONS,
        )
    )

    assert stored.path.read_bytes() == b"abcdef"
    assert upload.read_sizes == [CHUNK_SIZE, CHUNK_SIZE, CHUNK_SIZE]


def test_oversized_upload_is_rejected_without_file(upload_env, monkeypatch):
    from database import create_conversation

    _, upload_root = upload_env
    monkeypatch.setattr("config.UPLOAD_MAX_BYTES", 5)
    conversation_id = create_conversation()

    with _client() as client:
        authenticate(client)
        response = client.post(
            f"/api/conversations/{conversation_id}/upload",
            files={"file": ("notes.txt", b"123456", "text/plain")},
        )

    assert response.status_code == 413
    assert _files_under(upload_root) == []


@pytest.mark.parametrize(
    ("filename", "content", "content_type"),
    [
        ("archive.exe", b"MZ-not-allowed", "application/octet-stream"),
        ("faux.pdf", b"%PDF-pas un document", "application/pdf"),
        ("notes.txt", b"\x00\x01binary", "text/plain"),
        ("notes.txt", b"texte", "image/png"),
    ],
)
def test_unknown_binary_invalid_signature_and_mime_are_rejected(
    upload_env,
    filename,
    content,
    content_type,
):
    from database import create_conversation

    _, upload_root = upload_env
    conversation_id = create_conversation()

    with _client() as client:
        authenticate(client)
        response = client.post(
            f"/api/conversations/{conversation_id}/upload",
            files={"file": (filename, content, content_type)},
        )

    assert response.status_code == 415
    assert _files_under(upload_root) == []


def test_quota_is_enforced_separately_from_per_file_limit(upload_env, monkeypatch):
    from database import create_conversation

    _, upload_root = upload_env
    monkeypatch.setattr("config.UPLOAD_MAX_BYTES", 20)
    monkeypatch.setattr("config.UPLOAD_QUOTA_BYTES", 5)
    conversation_id = create_conversation()

    with _client() as client:
        authenticate(client)
        response = client.post(
            f"/api/conversations/{conversation_id}/upload",
            files={"file": ("notes.txt", b"123456", "text/plain")},
        )

    assert response.status_code == 507
    assert _files_under(upload_root) == []


def test_db_failure_rolls_back_physical_file(upload_env, monkeypatch):
    from database import create_conversation
    import api.router_conversations as conversation_routes

    _, upload_root = upload_env
    conversation_id = create_conversation()

    def _fail_save(*_args, **_kwargs):
        raise RuntimeError("DB indisponible")

    monkeypatch.setattr(conversation_routes, "save_conversation_document", _fail_save)
    with _client() as client:
        authenticate(client)
        response = client.post(
            f"/api/conversations/{conversation_id}/upload",
            files={"file": ("notes.txt", b"contenu", "text/plain")},
        )

    assert response.status_code == 500
    assert response.json()["detail"] == "Enregistrement du document impossible"
    assert _files_under(upload_root) == []


def test_school_upload_uses_same_bounded_uuid_storage(upload_env):
    from database import get_db

    _, upload_root = upload_env
    with _client() as client:
        authenticate(client)
        response = client.post(
            "/upload",
            files={"file": ("../../cours.md", b"# Chapitre", "text/markdown")},
        )

    assert response.status_code == 200, response.text
    assert response.json()["filename"] == "cours.md"
    with get_db() as conn:
        row = conn.execute(
            "SELECT file_path FROM school_documents WHERE id = ?",
            (response.json()["doc_id"],),
        ).fetchone()
    stored_path = Path(row["file_path"])
    assert stored_path.parent == upload_root / "school"
    assert re.fullmatch(r"[0-9a-f]{32}\.md", stored_path.name)


def test_maintenance_purges_old_orphans_and_preserves_db_references(upload_env):
    from database import create_conversation, save_conversation_document
    from scripts.db_maintenance import run_maintenance

    _, upload_root = upload_env
    conversation_id = create_conversation()
    managed_dir = upload_root / "conversations" / str(conversation_id)
    managed_dir.mkdir(parents=True)
    referenced = managed_dir / f"{'a' * 32}.txt"
    orphan = managed_dir / f"{'b' * 32}.txt"
    referenced.write_text("conservé", encoding="utf-8")
    orphan.write_text("orphelin", encoding="utf-8")
    os.utime(referenced, (1, 1))
    os.utime(orphan, (1, 1))
    save_conversation_document(
        conversation_id,
        referenced.name,
        "notes.txt",
        str(referenced),
        "txt",
        referenced.stat().st_size,
    )

    report = run_maintenance()

    assert report["upload_orphans"]["removed"] == 1
    assert referenced.exists()
    assert not orphan.exists()


def test_managed_deletion_refuses_paths_outside_upload_root(upload_env, tmp_path):
    from jarvis.uploads import remove_managed_upload

    outside = tmp_path / "outside.txt"
    outside.write_text("ne pas supprimer", encoding="utf-8")

    assert remove_managed_upload(outside) is False
    assert outside.exists()
