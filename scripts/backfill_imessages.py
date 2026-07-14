#!/usr/bin/env python3
"""
Backfill iMessage — rattrape les messages manquants dans jarvis.db
depuis chat.db macOS. Traitement local uniquement, zéro appel réseau.

Usage:
    cd ~/JarvisAPI && source venv/bin/activate
    python scripts/backfill_imessages.py --since 2026-05-29
    python scripts/backfill_imessages.py --since 2026-05-29 --dry-run
"""
import argparse
import sqlite3
import os
import sys
import logging
from datetime import datetime, timezone
from typing import Any

# Ajouter le projet au path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from integrations.apple_data import (
    apple_data,
    apple_epoch_to_datetime,
    datetime_to_apple_epoch,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("backfill_imessages")

# ── Chemins ──
JARVIS_DB = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "jarvis.db"
)

# ── Constantes d'insertion ──
INSERT_BATCH_SIZE: int = 500
MAX_TEXT_LENGTH: int = 50000  # cap anti-lignes trop longues


def apple_ts_to_datetime(apple_ts: int | float | None) -> datetime | None:
    """Compatibilité du backfill délégant la conversion au service central."""
    converted = apple_epoch_to_datetime(apple_ts, zero_is_none=False)
    return converted.replace(tzinfo=timezone.utc) if converted else None


def apple_ts_from_date(date_str: str) -> int:
    """Convertit 'YYYY-MM-DD' en timestamp Apple nanosecondes."""
    dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return datetime_to_apple_epoch(dt)


def read_chat_db(since_date: str) -> list[dict[str, Any]]:
    """Lit les messages depuis chat.db en mode lecture seule.

    Returns:
        Liste de dicts {guid, text, sender, is_from_me, timestamp, service, chat_name, has_attachments}.
    """
    service = apple_data
    if not service.db_path.exists():
        logger.error("chat.db introuvable : %s", service.db_path)
        logger.error("Vérifier Full Disk Access pour Terminal.app")
        sys.exit(1)

    since_ts = apple_ts_from_date(since_date)

    conn = service.connect_readonly()

    query: str = """
    SELECT
        m.ROWID as message_id,
        m.guid,
        m.text,
        m.date as apple_date,
        m.is_from_me,
        m.service,
        m.cache_has_attachments,
        COALESCE(h.id, 'me') as handle_id,
        COALESCE(
            c.display_name,
            c.chat_identifier,
            h.id
        ) as chat_name
    FROM message m
    LEFT JOIN handle h ON m.handle_id = h.ROWID
    LEFT JOIN chat_message_join cmj ON cmj.message_id = m.ROWID
    LEFT JOIN chat c ON cmj.chat_id = c.ROWID
    WHERE m.date >= ?
      AND m.text IS NOT NULL
      AND m.text != ''
    ORDER BY m.date ASC
    """

    cursor = conn.execute(query, (since_ts,))
    messages: list[dict[str, Any]] = []
    for row in cursor:
        dt = apple_ts_to_datetime(row["apple_date"])
        text = row["text"]
        if text and len(text) > MAX_TEXT_LENGTH:
            text = text[:MAX_TEXT_LENGTH]
        messages.append(
            {
                "guid": row["guid"],
                "text": text,
                "sender": "me" if row["is_from_me"] else row["handle_id"],
                "is_from_me": bool(row["is_from_me"]),
                "timestamp": dt.isoformat() if dt else None,
                "service": row["service"] or "unknown",
                "chat_name": row["chat_name"] or row["handle_id"] or "unknown",
                "has_attachments": bool(row["cache_has_attachments"]),
            }
        )

    conn.close()
    logger.info(
        "%d messages lus depuis chat.db (depuis %s)", len(messages), since_date
    )
    return messages


def get_existing_guids(jarvis_conn: sqlite3.Connection) -> set[str]:
    """Récupère les GUIDs déjà en base pour éviter les doublons.

    Retourne un set vide si la colonne external_id n'existe pas encore.
    """
    try:
        cursor = jarvis_conn.execute(
            "SELECT external_id FROM messages WHERE source = 'imessage' AND external_id IS NOT NULL"
        )
        return {row[0] for row in cursor if row[0]}
    except sqlite3.OperationalError:
        return set()


def ensure_schema(jarvis_conn: sqlite3.Connection) -> None:
    """Ajoute les colonnes nécessaires à la table messages si absentes.

    Colonnes ajoutées (idempotent) :
      - external_id TEXT    : GUID iMessage (déduplication)
      - source TEXT         : 'chat' | 'imessage'
      - sender TEXT         : expéditeur (numéro/email)
      - chat_name TEXT      : nom de la conversation iMessage
      - is_from_me INTEGER  : 1 si envoyé par l'utilisateur
    """
    cursor = jarvis_conn.execute("PRAGMA table_info(messages)")
    columns = {row[1] for row in cursor}

    migrations: list[tuple[str, str]] = [
        ("external_id", "TEXT"),
        ("source", "TEXT DEFAULT 'chat'"),
        ("sender", "TEXT"),
        ("chat_name", "TEXT"),
        ("is_from_me", "INTEGER DEFAULT 0"),
    ]

    for col_name, col_type in migrations:
        if col_name not in columns:
            sql = f"ALTER TABLE messages ADD COLUMN {col_name} {col_type}"
            jarvis_conn.execute(sql)
            logger.info("Colonne %s ajoutée à messages", col_name)

    # Index sur external_id pour les recherches de doublons rapides
    try:
        jarvis_conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_messages_external_id ON messages(external_id)"
        )
    except sqlite3.OperationalError:
        pass

    jarvis_conn.commit()


def _get_or_create_conversation(
    jarvis_conn: sqlite3.Connection, chat_name: str
) -> int | None:
    """Récupère ou crée une conversation virtuelle pour un chat iMessage.

    La conversation est identifiée par le titre 'iMessage: {chat_name}'.
    Retourne l'ID de la conversation, ou None si le chat_name est vide.
    """
    if not chat_name or not chat_name.strip():
        return None

    title = f"iMessage: {chat_name}"
    row = jarvis_conn.execute(
        "SELECT id FROM conversations WHERE title = ?", (title,)
    ).fetchone()
    if row:
        return row[0]

    cur = jarvis_conn.execute(
        "INSERT INTO conversations (title, agent) VALUES (?, 'imessage')",
        (title,),
    )
    return cur.lastrowid


def insert_messages(
    messages: list[dict[str, Any]], dry_run: bool = False
) -> dict[str, int]:
    """Insère les messages manquants dans jarvis.db.

    Returns:
        dict avec {inserted, skipped, total}.
    """
    jarvis_conn = sqlite3.connect(JARVIS_DB)
    jarvis_conn.execute("PRAGMA journal_mode=WAL")
    jarvis_conn.execute("PRAGMA foreign_keys=OFF")  # FK off pour insertion batch

    ensure_schema(jarvis_conn)
    existing = get_existing_guids(jarvis_conn)
    logger.info("%d messages iMessage déjà en base", len(existing))

    # Cache de conversations pour éviter N queries
    conv_cache: dict[str, int | None] = {}

    inserted: int = 0
    skipped: int = 0

    for msg in messages:
        if msg["guid"] in existing:
            skipped += 1
            continue

        if dry_run:
            inserted += 1
            continue

        chat_name = msg.get("chat_name") or msg["sender"] or "unknown"
        if chat_name not in conv_cache:
            conv_cache[chat_name] = _get_or_create_conversation(jarvis_conn, chat_name)
        conv_id = conv_cache[chat_name]

        # role: 'user' pour les messages envoyés, 'system' pour les reçus
        role = "user" if msg["is_from_me"] else "system"

        jarvis_conn.execute(
            """
            INSERT INTO messages (
                role, content, sender, created_at, source, external_id,
                chat_name, is_from_me, conversation_id
            ) VALUES (?, ?, ?, ?, 'imessage', ?, ?, ?, ?)
            """,
            (
                role,
                msg["text"],
                msg["sender"],
                msg["timestamp"],
                msg["guid"],
                chat_name,
                1 if msg["is_from_me"] else 0,
                conv_id,
            ),
        )
        inserted += 1

        if inserted % INSERT_BATCH_SIZE == 0:
            jarvis_conn.commit()
            logger.info("  ... %d insérés", inserted)

    jarvis_conn.commit()
    jarvis_conn.close()

    return {"inserted": inserted, "skipped": skipped, "total": len(messages)}


def print_stats(messages: list[dict[str, Any]]) -> None:
    """Affiche les statistiques du lot de messages."""
    if not messages:
        return

    from_me = sum(1 for m in messages if m["is_from_me"])
    received = len(messages) - from_me
    senders = {m["sender"] for m in messages if not m["is_from_me"]}
    chats = {m.get("chat_name", "?") for m in messages}

    logger.info("  Envoyés: %d | Reçus: %d | Contacts uniques: %d | Chats: %d",
                from_me, received, len(senders), len(chats))

    for rank, chat in enumerate(sorted(chats)[:10], 1):
        count = sum(1 for m in messages if m.get("chat_name") == chat)
        logger.info("    #%d %s: %d messages", rank, chat[:40], count)

    first = messages[0]["timestamp"][:10] if messages else "?"
    last = messages[-1]["timestamp"][:10] if messages else "?"
    logger.info("  Période: %s → %s", first, last)


def verify_backfill(since_date: str) -> dict[str, Any]:
    """Vérifie l'état post-backfill sans modifier la base."""
    conn = sqlite3.connect(JARVIS_DB)
    conn.row_factory = sqlite3.Row

    # Dernier message iMessage
    last_row = conn.execute(
        "SELECT MAX(created_at) as last_date FROM messages WHERE source='imessage'"
    ).fetchone()

    # Total
    total = conn.execute(
        "SELECT COUNT(*) as cnt FROM messages WHERE source='imessage'"
    ).fetchone()

    # Distribution par mois
    rows = conn.execute("""
        SELECT strftime('%Y-%m', created_at) as mois, COUNT(*) as cnt
        FROM messages WHERE source='imessage'
        GROUP BY mois ORDER BY mois
    """).fetchall()

    # Conversations virtuelles
    convs = conn.execute(
        "SELECT COUNT(*) as cnt FROM conversations WHERE agent = 'imessage'"
    ).fetchone()

    conn.close()

    return {
        "last_date": last_row["last_date"] if last_row else None,
        "total": total["cnt"] if total else 0,
        "monthly": [(r["mois"], r["cnt"]) for r in rows],
        "conversations": convs["cnt"] if convs else 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill iMessage depuis chat.db dans jarvis.db"
    )
    parser.add_argument(
        "--since", required=True, help="Date de début (YYYY-MM-DD)"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Compter sans insérer"
    )
    args = parser.parse_args()

    logger.info("=== Backfill iMessage depuis %s ===", args.since)
    if args.dry_run:
        logger.info("MODE DRY-RUN — aucune écriture en base")

    messages = read_chat_db(args.since)

    if not messages:
        logger.info("Aucun message trouvé entre %s et aujourd'hui.", args.since)
        logger.info("Vérifier Full Disk Access dans Réglages Système → Confidentialité.")
        return

    print_stats(messages)

    result = insert_messages(messages, dry_run=args.dry_run)
    logger.info(
        "=== Résultat: %d insérés, %d doublons, %d total ===",
        result["inserted"], result["skipped"], result["total"],
    )

    # Vérification
    if not args.dry_run:
        stats = verify_backfill(args.since)
        logger.info("=== Vérification post-backfill ===")
        logger.info("  Dernier message iMessage : %s", stats["last_date"])
        logger.info("  Total messages iMessage  : %d", stats["total"])
        logger.info("  Conversations virtuelles : %d", stats["conversations"])
        logger.info("  Distribution mensuelle :")
        for mois, count in stats["monthly"]:
            bar = "█" * min(count // 50, 40)
            logger.info("    %s: %5d %s", mois, count, bar)


if __name__ == "__main__":
    main()
