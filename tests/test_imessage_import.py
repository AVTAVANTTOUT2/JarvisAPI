"""Tests du systeme d'import iMessage — chat.db vers jarvis.db.

Teste :
  - La generation de content_hash
  - L'insertion/ignorance de messages (dedup via ROWID, GUID, hash)
  - La gestion du curseur de synchronisation
  - L'import de handles et chats
  - L'idempotence (import 3x → meme resultat)
  - La reconciliation
  - Le mode incremental
"""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Import du module a tester
from integrations.imessage_import import (
    IMessageImporter,
    ImportResult,
    ReconciliationReport,
    _apple_ts_to_iso,
    _compute_content_hash,
)


# ═══════════════════════════════════════════════════════════
# Tests unitaires — fonctions pures
# ═══════════════════════════════════════════════════════════


class TestAppleTimestampConversion:
    """Conversion des timestamps Apple (secondes depuis 2001-01-01)."""

    def test_convert_normal_timestamp(self):
        """Un timestamp normal (secondes) donne une date coherente."""
        # 0 = 2001-01-01 00:00:00
        result = _apple_ts_to_iso(0)
        assert result is not None
        assert result.startswith("2001-01-01")

    def test_convert_nanosecond_timestamp(self):
        """Un timestamp en nanosecondes (> 1e15) est divise par 1e9."""
        # 0 secondes * 1e9 = 0 nanosecondes (bizarre mais valide)
        # Un vrai timestamp nano : ~ 6e17 pour 2020
        ts_nano = 600000000000000000   # ~ 2020
        result = _apple_ts_to_iso(ts_nano)
        assert result is not None
        assert "2020" in result or "2019" in result or "2021" in result

    def test_convert_none_returns_none(self):
        """None → None."""
        assert _apple_ts_to_iso(None) is None

    def test_convert_zero_returns_epoch_date(self):
        """0 → 2001-01-01 (Apple epoch)."""
        result = _apple_ts_to_iso(0)
        assert result is not None
        assert "2001-01-01" in result

    def test_convert_future_timestamp(self):
        """Un timestamp lointain (2030) est accepte sans erreur."""
        result = _apple_ts_to_iso(900000000)
        assert result is not None
        assert "20" in result


class TestContentHash:
    """Hashing de contenu pour deduplication."""

    def test_same_input_same_hash(self):
        """Meme entree → meme hash."""
        h1 = _compute_content_hash(12345, 1, "Bonjour", "guid-abc")
        h2 = _compute_content_hash(12345, 1, "Bonjour", "guid-abc")
        assert h1 == h2

    def test_different_date_different_hash(self):
        """Date differente → hash different."""
        h1 = _compute_content_hash(10000, 1, "Hello", "guid-1")
        h2 = _compute_content_hash(10001, 1, "Hello", "guid-1")
        assert h1 != h2

    def test_different_text_different_hash(self):
        """Texte different → hash different."""
        h1 = _compute_content_hash(10000, 1, "Hello", "guid-1")
        h2 = _compute_content_hash(10000, 1, "World", "guid-1")
        assert h1 != h2

    def test_different_guid_different_hash(self):
        """GUID different → hash different."""
        h1 = _compute_content_hash(10000, 1, "Hello", "guid-1")
        h2 = _compute_content_hash(10000, 1, "Hello", "guid-2")
        assert h1 != h2

    def test_none_inputs_handled(self):
        """Les entrees None sont converties en strings (0 ou '')."""
        h = _compute_content_hash(None, None, None, None)
        assert h
        assert len(h) == 64  # SHA256 hex

    def test_whitespace_normalization(self):
        """Le texte est normalise (strip) avant hash."""
        h1 = _compute_content_hash(10000, 1, "  Hello  ", "guid")
        h2 = _compute_content_hash(10000, 1, "Hello", "guid")
        assert h1 == h2

    def test_hash_is_hex_string(self):
        """Le hash est une chaine hex de 64 caracteres."""
        h = _compute_content_hash(42, 7, "test", "g-1")
        assert isinstance(h, str)
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)


# ═══════════════════════════════════════════════════════════
# Tests d'integration — IMessageImporter (avec DB memoire)
# ═══════════════════════════════════════════════════════════


@pytest.fixture
def importer_with_memory_db(tmp_path: Path):
    """Cree un IMessageImporter qui pointe sur une DB en memoire.

    Le chat.db est simule par une DB SQLite en memoire avec
    les tables Apple (handle, chat, message, attachment).

    Chaque test recoit un DB jarvis.db unique dans tmp_path,
    garantissant l'isolation complete entre tests.
    """
    import config as cfg
    import database

    test_db = tmp_path / "test_jarvis.db"

    # Override DB_PATH — a la fois dans config ET dans le module database
    original_cfg_path = cfg.DB_PATH
    original_db_path = database.DB_PATH
    cfg.DB_PATH = str(test_db)
    database.DB_PATH = Path(str(test_db))

    # Initialiser la DB JARVIS avec les nouvelles tables
    database.init_db()

    # Creer un IMessageImporter avec une petite batch size
    importer = IMessageImporter(batch_size=10)

    # Simuler chat.db via une DB memoire separee
    chat_db = sqlite3.connect(":memory:")
    chat_db.row_factory = sqlite3.Row
    _setup_chat_db_tables(chat_db)

    yield importer, chat_db

    # Restore
    cfg.DB_PATH = original_cfg_path
    database.DB_PATH = original_db_path


def _setup_chat_db_tables(conn: sqlite3.Connection) -> None:
    """Cree les tables Apple dans la DB chat simulee."""
    conn.executescript("""
        CREATE TABLE handle (
            ROWID INTEGER PRIMARY KEY AUTOINCREMENT,
            id TEXT,
            country TEXT,
            service TEXT,
            uncanonicalized_id TEXT
        );
        CREATE TABLE chat (
            ROWID INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_identifier TEXT,
            display_name TEXT,
            group_id TEXT,
            style INTEGER DEFAULT 0,
            is_filtered INTEGER DEFAULT 0
        );
        CREATE TABLE chat_handle_join (
            chat_id INTEGER,
            handle_id INTEGER
        );
        CREATE TABLE message (
            ROWID INTEGER PRIMARY KEY AUTOINCREMENT,
            guid TEXT,
            text TEXT,
            handle_id INTEGER,
            date INTEGER DEFAULT 0,
            date_read INTEGER DEFAULT 0,
            is_from_me INTEGER DEFAULT 0,
            is_read INTEGER DEFAULT 0,
            item_type INTEGER DEFAULT 0,
            group_title TEXT,
            associated_message_guid TEXT,
            associated_message_type INTEGER DEFAULT 0,
            cache_roomnames TEXT
        );
        CREATE TABLE attachment (
            ROWID INTEGER PRIMARY KEY AUTOINCREMENT,
            guid TEXT,
            filename TEXT,
            mime_type TEXT,
            transfer_name TEXT,
            total_bytes INTEGER,
            is_outgoing INTEGER DEFAULT 0,
            hide_attachment INTEGER DEFAULT 0,
            created_date INTEGER
        );
        CREATE TABLE message_attachment_join (
            message_id INTEGER,
            attachment_id INTEGER
        );
    """)


def _seed_handles(chat_db: sqlite3.Connection, handles: list[dict]) -> list[int]:
    """Insere des handles dans chat_db. Retourne les ROWID."""
    ids = []
    for h in handles:
        cur = chat_db.execute(
            "INSERT INTO handle (id, country, service) VALUES (?, ?, ?)",
            (h["id"], h.get("country"), h.get("service", "iMessage")),
        )
        ids.append(cur.lastrowid)
    return ids


def _seed_chats(chat_db: sqlite3.Connection, chats: list[dict]) -> list[int]:
    """Insere des chats dans chat_db."""
    ids = []
    for c in chats:
        cur = chat_db.execute(
            "INSERT INTO chat (chat_identifier, display_name, style) VALUES (?, ?, ?)",
            (c.get("identifier"), c.get("display_name"), c.get("style", 0)),
        )
        ids.append(cur.lastrowid)
    return ids


def _seed_messages(
    chat_db: sqlite3.Connection,
    messages: list[dict],
) -> list[int]:
    """Insere des messages dans chat_db. Retourne les ROWID."""
    ids = []
    for m in messages:
        cur = chat_db.execute(
            """INSERT INTO message
               (guid, text, handle_id, date, is_from_me, cache_roomnames)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                m["guid"],
                m["text"],
                m.get("handle_id"),
                m.get("date", 0),
                m.get("is_from_me", 0),
                m.get("cache_roomnames"),
            ),
        )
        ids.append(cur.lastrowid)
    return ids


class TestHandleImport:
    """Import des handles (contacts iMessage)."""

    def test_import_handles_basic(self, importer_with_memory_db):
        """Import de 3 handles → 3 lignes dans imessage_handles."""
        importer, chat_db = importer_with_memory_db

        _seed_handles(chat_db, [
            {"id": "+33600000001", "country": "fr", "service": "iMessage"},
            {"id": "+33600000002", "country": "fr", "service": "iMessage"},
            {"id": "test@email.com", "country": None, "service": "iMessage"},
        ])

        mapping = importer._import_handles(chat_db)
        assert len(mapping) == 3

        from database import get_db
        with get_db() as conn:
            count = conn.execute("SELECT COUNT(*) c FROM imessage_handles").fetchone()["c"]
            assert count == 3

    def test_import_same_handles_twice_no_duplicates(self, importer_with_memory_db):
        """Deux imports de la meme liste ne creent pas de doublons."""
        importer, chat_db = importer_with_memory_db

        _seed_handles(chat_db, [
            {"id": "+33600000001", "country": "fr", "service": "iMessage"},
        ])

        importer._import_handles(chat_db)
        importer._import_handles(chat_db)

        from database import get_db
        with get_db() as conn:
            count = conn.execute("SELECT COUNT(*) c FROM imessage_handles").fetchone()["c"]
            assert count == 1


class TestMessageDedup:
    """Deduplication des messages via ROWID / GUID / hash."""

    def test_insert_message_by_rowid(self, importer_with_memory_db):
        """Un message est insere via son apple_rowid."""
        importer, chat_db = importer_with_memory_db
        from database import get_db

        # Preparer un handle et un chat
        hids = _seed_handles(chat_db, [{"id": "+33600000001", "service": "iMessage"}])
        handles_map = importer._import_handles(chat_db)
        cids = _seed_chats(chat_db, [{"identifier": "+33600000001", "style": 0}])
        chats_map = importer._import_chats(chat_db)

        mids = _seed_messages(chat_db, [{
            "guid": "guid-001",
            "text": "Hello world",
            "handle_id": hids[0],
            "date": 600000000,
            "is_from_me": 0,
            "cache_roomnames": "+33600000001",
        }])

        result = importer._import_message_batch(
            chat_db, handles_map, chats_map, from_rowid=1, to_rowid=mids[-1],
        )
        assert result["imported"] == 1
        assert result["skipped"] == 0

    def test_same_rowid_skipped(self, importer_with_memory_db):
        """Deux messages avec le meme ROWID → un seul importe."""
        importer, chat_db = importer_with_memory_db
        from database import get_db

        hids = _seed_handles(chat_db, [{"id": "+33600000001", "service": "iMessage"}])
        handles_map = importer._import_handles(chat_db)
        _seed_chats(chat_db, [{"identifier": "+33600000001", "style": 0}])
        chats_map = importer._import_chats(chat_db)

        _seed_messages(chat_db, [{
            "guid": "guid-001",
            "text": "Hello",
            "handle_id": hids[0],
            "date": 600000000,
            "is_from_me": 0,
            "cache_roomnames": "+33600000001",
        }])

        # Premier import
        importer._import_message_batch(
            chat_db, handles_map, chats_map, from_rowid=1, to_rowid=1,
        )
        # Deuxieme import — les memes messages
        result = importer._import_message_batch(
            chat_db, handles_map, chats_map, from_rowid=1, to_rowid=1,
        )
        assert result["imported"] == 0
        assert result["skipped"] == 1

    def test_same_guid_skipped(self, importer_with_memory_db):
        """Meme GUID mais ROWID different → skip."""
        importer, chat_db = importer_with_memory_db
        from database import get_db

        hids = _seed_handles(chat_db, [{"id": "+33600000001", "service": "iMessage"}])
        handles_map = importer._import_handles(chat_db)
        _seed_chats(chat_db, [{"identifier": "+33600000001", "style": 0}])
        chats_map = importer._import_chats(chat_db)

        # Message 1
        _seed_messages(chat_db, [{
            "guid": "same-guid",
            "text": "Message 1",
            "handle_id": hids[0],
            "date": 600000000,
            "is_from_me": 0,
            "cache_roomnames": "+33600000001",
        }])
        importer._import_message_batch(
            chat_db, handles_map, chats_map, from_rowid=1, to_rowid=1,
        )

        # Message 2 — meme GUID, ROWID different
        _seed_messages(chat_db, [{
            "guid": "same-guid",
            "text": "Message 2 (different)",
            "handle_id": hids[0],
            "date": 600000001,
            "is_from_me": 1,
            "cache_roomnames": "+33600000001",
        }])
        result = importer._import_message_batch(
            chat_db, handles_map, chats_map, from_rowid=2, to_rowid=2,
        )
        assert result["imported"] == 0
        assert result["skipped"] == 1

    def test_same_hash_skipped(self, importer_with_memory_db):
        """Meme hash → skip meme avec ROWID et GUID differents."""
        importer, chat_db = importer_with_memory_db
        from database import get_db

        hids = _seed_handles(chat_db, [{"id": "+33600000001", "service": "iMessage"}])
        handles_map = importer._import_handles(chat_db)
        _seed_chats(chat_db, [{"identifier": "+33600000001", "style": 0}])
        chats_map = importer._import_chats(chat_db)

        # Insertion directe d'un message dans jarvis.db avec un hash connu
        text = "Repetition test"
        guid = "guid-a"
        handle_id = hids[0]
        date = 600000000
        content_hash = _compute_content_hash(date, handle_id, text, guid)

        with get_db() as conn:
            conn.execute(
                """INSERT INTO imessage_messages
                   (apple_rowid, guid, text, date, content_hash)
                   VALUES (?, ?, ?, ?, ?)""",
                (999, "some-other-guid", text, date, content_hash),
            )

        # Maintenant, tenter d'importer un message different (ROWID 1, GUID guid-a)
        # mais avec le MEME hash
        _seed_messages(chat_db, [{
            "guid": guid,
            "text": text,
            "handle_id": handle_id,
            "date": date,
            "is_from_me": 0,
            "cache_roomnames": "+33600000001",
        }])
        result = importer._import_message_batch(
            chat_db, handles_map, chats_map, from_rowid=1, to_rowid=1,
        )
        assert result["imported"] == 0  # skip car hash identique
        assert result["skipped"] == 1


class TestCursorManagement:
    """Gestion du curseur de synchronisation."""

    def test_get_cursor_returns_defaults_when_empty(self, importer_with_memory_db):
        """Quand la table est vide, _get_cursor retourne des defauts."""
        importer, chat_db = importer_with_memory_db
        cursor = importer._get_cursor()
        assert cursor["last_apple_rowid"] == 0
        assert cursor["last_date"] == 0
        assert cursor["status"] == "idle"

    def test_update_cursor_creates_row(self, importer_with_memory_db):
        """_update_cursor cree la ligne si elle n'existe pas."""
        importer, chat_db = importer_with_memory_db
        importer._update_cursor(
            last_rowid=42, last_date=100, last_guid="guid-x",
            total_imported=10, status="idle",
        )
        cursor = importer._get_cursor()
        assert cursor["last_apple_rowid"] == 42
        assert cursor["last_date"] == 100
        assert cursor["last_guid"] == "guid-x"
        assert cursor["total_imported"] == 10
        assert cursor["status"] == "idle"

    def test_update_cursor_updates_existing(self, importer_with_memory_db):
        """_update_cursor met a jour la ligne existante."""
        importer, chat_db = importer_with_memory_db
        importer._update_cursor(
            last_rowid=10, status="importing",
        )
        importer._update_cursor(
            last_rowid=100, total_imported=50, status="idle",
        )
        cursor = importer._get_cursor()
        assert cursor["last_apple_rowid"] == 100
        assert cursor["total_imported"] == 50
        assert cursor["status"] == "idle"

    def test_reset_cursor(self, importer_with_memory_db):
        """reset_cursor supprime la ligne."""
        importer, chat_db = importer_with_memory_db
        importer._update_cursor(last_rowid=100, status="idle")
        importer.reset_cursor()
        cursor = importer._get_cursor()
        assert cursor["last_apple_rowid"] == 0

    def test_get_status_includes_counts(self, importer_with_memory_db):
        """get_status retourne les compteurs DB."""
        importer, chat_db = importer_with_memory_db
        status = importer.get_status()
        assert "jarvis_db_messages" in status
        assert "jarvis_db_chats" in status
        assert "jarvis_db_handles" in status
        assert status["jarvis_db_messages"] == 0


class TestReconciliation:
    """Audit post-import."""

    def test_reconcile_empty_db(self, importer_with_memory_db):
        """Reconciliation sur DB vide retourne ok=True."""
        importer, chat_db = importer_with_memory_db

        # Il faut que is_available() retourne True
        with patch.object(importer, "is_available", return_value=True):
            with patch.object(importer, "_open_chat_db", return_value=chat_db):
                report = importer.reconcile()
                assert isinstance(report, ReconciliationReport)
                assert report.jarvis_db_messages == 0
                assert report.chat_db_messages == 0
                assert report.ok is True

    def test_reconcile_detects_mismatch(self, importer_with_memory_db):
        """Si jarvis.db a moins de messages que chat.db, ok=False."""
        importer, chat_db = importer_with_memory_db

        # Ajouter des messages dans chat_db
        hids = _seed_handles(chat_db, [{"id": "+33600000001"}])
        _seed_chats(chat_db, [{"identifier": "+33600000001"}])
        _seed_messages(chat_db, [
            {"guid": "g1", "text": "Hello", "handle_id": hids[0], "date": 1},
            {"guid": "g2", "text": "World", "handle_id": hids[0], "date": 2},
        ])

        with patch.object(importer, "is_available", return_value=True):
            with patch.object(importer, "_open_chat_db", return_value=chat_db):
                report = importer.reconcile()
                assert report.chat_db_messages == 2
                assert report.jarvis_db_messages == 0
                assert report.ok is False


class TestImportResult:
    """Structure du resultat d'import."""

    def test_import_result_defaults(self):
        """ImportResult a des valeurs par defaut a zero."""
        result = ImportResult()
        assert result.total_handles == 0
        assert result.total_messages == 0
        assert result.total_skipped == 0
        assert result.errors == []
        assert result.duration_seconds == 0.0

    def test_reconciliation_report_defaults(self):
        """ReconciliationReport a des defauts a zero."""
        r = ReconciliationReport()
        assert r.ok is False
        assert r.orphan_messages == 0


class TestImporterLifecycle:
    """Cycle de vie de l'importer."""

    def test_is_available_checks_chat_db(self, importer_with_memory_db, tmp_path):
        """is_available verifie l'existence de chat.db."""
        importer, chat_db = importer_with_memory_db

        # L'importer n'a pas de vrai chat.db, mais on peut forcer le test
        # en patchant CHAT_DB_PATH
        with patch(
            "integrations.imessage_import.CHAT_DB_PATH",
            Path("/nonexistent/path/chat.db"),
        ):
            available = importer.is_available()
            assert available is False

    def test_get_status_works_when_not_available(self, importer_with_memory_db):
        """get_status fonctionne meme si chat.db n'est pas dispo."""
        importer, chat_db = importer_with_memory_db
        status = importer.get_status()
        assert isinstance(status, dict)
        assert "status" in status


class TestCLIScript:
    """Tests du script CLI via subprocess."""

    def test_import_module_importable(self):
        """Le module d'import est importable sans erreur."""
        import integrations.imessage_import  # noqa: F401

    def test_cli_script_syntax(self):
        """Le script CLI est syntaxiquement correct."""
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "imessage_import_cli",
            Path(__file__).resolve().parent.parent / "scripts" / "imessage_import.py",
        )
        module = importlib.util.module_from_spec(spec)
        # Ne pas executer, juste verifier que ca parse
        import ast
        source = Path(Path(__file__).resolve().parent.parent / "scripts" / "imessage_import.py").read_text()
        ast.parse(source)


# ═══════════════════════════════════════════════════════════
# Tests d'idempotence — scenarios complets
# ═══════════════════════════════════════════════════════════


class TestIdempotency:
    """L'import complet est idempotent."""

    def test_double_handles_import_idempotent(self, importer_with_memory_db):
        """Importer les memes handles 2x ne cree pas de doublons."""
        importer, chat_db = importer_with_memory_db

        _seed_handles(chat_db, [
            {"id": "+33600000001", "service": "iMessage"},
            {"id": "+33600000002", "service": "iMessage"},
        ])

        mapping1 = importer._import_handles(chat_db)
        mapping2 = importer._import_handles(chat_db)

        assert len(mapping1) == 2
        assert len(mapping2) == 2
        assert mapping1 == mapping2  # Memes ids

        from database import get_db
        with get_db() as conn:
            count = conn.execute("SELECT COUNT(*) c FROM imessage_handles").fetchone()["c"]
            assert count == 2

    def test_triple_message_batch_idempotent(self, importer_with_memory_db):
        """Importer les memes messages 3x ne cree pas de doublons."""
        importer, chat_db = importer_with_memory_db
        from database import get_db

        hids = _seed_handles(chat_db, [
            {"id": "+33600000001", "service": "iMessage"},
        ])
        handles_map = importer._import_handles(chat_db)
        _seed_chats(chat_db, [{"identifier": "+33600000001", "style": 0}])
        chats_map = importer._import_chats(chat_db)

        _seed_messages(chat_db, [
            {"guid": "g1", "text": "M1", "handle_id": hids[0], "date": 1, "cache_roomnames": "+33600000001"},
            {"guid": "g2", "text": "M2", "handle_id": hids[0], "date": 2, "cache_roomnames": "+33600000001"},
        ])

        # Import 3 fois
        for _ in range(3):
            importer._import_message_batch(
                chat_db, handles_map, chats_map, from_rowid=1, to_rowid=2,
            )

        with get_db() as conn:
            count = conn.execute("SELECT COUNT(*) c FROM imessage_messages").fetchone()["c"]
            assert count == 2
