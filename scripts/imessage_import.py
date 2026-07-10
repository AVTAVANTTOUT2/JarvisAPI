#!/usr/bin/env python3
"""CLI d'import iMessage — importe chat.db dans jarvis.db.

Usage :
    python scripts/imessage_import.py              # import initial complet
    python scripts/imessage_import.py --sync       # sync incrementale
    python scripts/imessage_import.py --reset      # reset le curseur
    python scripts/imessage_import.py --check      # audit sans import
    python scripts/imessage_import.py --status     # etat du curseur
    python scripts/imessage_import.py --reconcile  # reconciliation seule
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

# Permet l'execution directe : python scripts/imessage_import.py
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from database import init_db
from integrations.imessage_import import IMessageImporter, imessage_importer


def _setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    logging.basicConfig(level=level, format=fmt)


def cmd_import(importer: IMessageImporter, args: argparse.Namespace) -> int:
    """Import initial complet de chat.db."""
    if not importer.is_available():
        print("ERREUR : chat.db inaccessible.")
        print("  → Verifier : Reglages Systeme > Confidentialite > Acces complet au disque")
        print(f"  → Chemin : ~/Library/Messages/chat.db")
        return 1

    cursor = importer.get_status()
    if cursor.get("total_imported", 0) > 0 and not args.force:
        print(f"Un import precedent existe ({cursor['total_imported']} messages deja importes).")
        print("Utilisez --force pour reimporter completement, ou --sync pour la sync incrementale.")
        return 1

    print("Demarrage de l'import complet...")
    print(f"  Chat DB : ~/Library/Messages/chat.db")
    print(f"  JARVIS DB : jarvis.db")
    print(f"  Batch size : {importer.batch_size}")
    print()

    t0 = time.time()
    result = importer.import_all()
    elapsed = time.time() - t0

    print()
    print("=" * 60)
    print("IMPORT TERMINE")
    print("=" * 60)
    print(f"  Duree : {elapsed:.1f}s")
    print(f"  Mode : {result.mode}")
    print(f"  Handles : {result.total_handles}")
    print(f"  Chats : {result.total_chats}")
    print(f"  Messages importes : {result.total_messages}")
    print(f"  Messages skippes : {result.total_skipped}")
    print(f"  Messages echoues : {result.total_failed}")
    print(f"  Attachments : {result.total_attachments}")
    print(f"  Reactions : {result.total_reactions}")

    if result.errors:
        print(f"\n  Erreurs ({len(result.errors)}) :")
        for err in result.errors[:10]:
            print(f"    - {err}")
        if len(result.errors) > 10:
            print(f"    ... et {len(result.errors) - 10} autres")

    rec = result.reconciliation
    if rec:
        print()
        print("  RECONCILIATION :")
        print(f"    chat.db → {rec.get('chat_db_messages', '?')} messages")
        print(f"    jarvis.db → {rec.get('jarvis_db_messages', '?')} messages")
        print(f"    Orphelins corriges : {rec.get('orphan_fixed', 0)}")
        print(f"    Doublons supprimes : {rec.get('duplicates_removed', 0)}")
        print(f"    OK : {rec.get('ok', '?')}")

    return 0


def cmd_sync(importer: IMessageImporter, args: argparse.Namespace) -> int:
    """Sync incrementale — uniquement les nouveaux messages."""
    if not importer.is_available():
        print("ERREUR : chat.db inaccessible.")
        return 1

    cursor = importer.get_status()
    last_rowid = cursor.get("last_apple_rowid", 0)
    print(f"Sync incrementale depuis ROWID {last_rowid}...")

    t0 = time.time()
    result = importer.sync_incremental()
    elapsed = time.time() - t0

    if result.total_messages == 0 and result.total_skipped == 0:
        print("Aucun nouveau message.")
    else:
        print(f"Sync terminee en {elapsed:.1f}s :")
        print(f"  Nouveaux messages : {result.total_messages}")
        print(f"  Skipes : {result.total_skipped}")
        print(f"  Echoues : {result.total_failed}")
        if result.errors:
            for err in result.errors[:5]:
                print(f"    - {err}")

    return 0


def cmd_check(importer: IMessageImporter, args: argparse.Namespace) -> int:
    """Audit sans import — reconciliation seule."""
    if not importer.is_available():
        print("ERREUR : chat.db inaccessible.")
        return 1

    print("Lancement de l'audit (reconciliation)...")
    report = importer.reconcile()

    print()
    print("=" * 60)
    print("AUDIT iMessage")
    print("=" * 60)
    print(f"  Messages chat.db : {report.chat_db_messages}")
    print(f"  Messages jarvis.db : {report.jarvis_db_messages}")
    print(f"  Chats chat.db : {report.chat_db_chats}")
    print(f"  Chats jarvis.db : {report.jarvis_db_chats}")
    print(f"  Handles chat.db : {report.chat_db_handles}")
    print(f"  Handles jarvis.db : {report.jarvis_db_handles}")
    print(f"  Orphelins : {report.orphan_messages}")
    print(f"  Corriges : {report.orphan_fixed}")
    print(f"  Doublons trouves : {report.duplicates_found}")
    print(f"  Doublons supprimes : {report.duplicates_removed}")
    print(f"  OK : {report.ok}")

    if report.chat_db_messages > 0:
        pct = report.jarvis_db_messages / report.chat_db_messages * 100
        print(f"  Taux de couverture : {pct:.1f}%")

    return 0 if report.ok else 1


def cmd_status(importer: IMessageImporter, args: argparse.Namespace) -> int:
    """Affiche l'etat du curseur de synchronisation."""
    status = importer.get_status()
    print("=" * 60)
    print("STATUT IMPORT iMessage")
    print("=" * 60)
    for key in [
        "status", "last_apple_rowid", "last_date", "total_imported",
        "total_failed", "last_sync_at", "completed_at", "started_at",
        "error_message", "jarvis_db_messages", "jarvis_db_chats", "jarvis_db_handles",
    ]:
        val = status.get(key, "—")
        print(f"  {key}: {val}")
    return 0


def cmd_reset(importer: IMessageImporter, args: argparse.Namespace) -> int:
    """Reinitialise le curseur pour un reimport complet."""
    confirm = input(
        "ATTENTION : reinitialiser le curseur forcera un reimport complet. "
        "Continuer ? (oui/NON) : "
    )
    if confirm.strip().lower() != "oui":
        print("Abandonne.")
        return 0

    importer.reset_cursor()
    print("Curseur reinitialise. Lancez `python scripts/imessage_import.py` pour re-importer.")
    return 0


def cmd_reconcile(importer: IMessageImporter, args: argparse.Namespace) -> int:
    """Reconciliation seule."""
    return cmd_check(importer, args)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Import iMessage — chat.db → jarvis.db",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exemples :
  python scripts/imessage_import.py              # import initial complet
  python scripts/imessage_import.py --sync       # sync incrementale
  python scripts/imessage_import.py --force      # import force (ignore curseur)
  python scripts/imessage_import.py --reset      # reset le curseur
  python scripts/imessage_import.py --check      # audit sans import
  python scripts/imessage_import.py --status     # etat du curseur
        """,
    )
    parser.add_argument("--sync", action="store_true", help="Sync incrementale (nouveaux messages)")
    parser.add_argument("--force", action="store_true", help="Forcer l'import initial meme si un precedent existe")
    parser.add_argument("--reset", action="store_true", help="Reinitialiser le curseur de sync")
    parser.add_argument("--check", action="store_true", help="Audit de reconciliation sans import")
    parser.add_argument("--status", action="store_true", help="Afficher l'etat du curseur")
    parser.add_argument("--reconcile", action="store_true", help="Reconciliation seule")
    parser.add_argument("--batch", type=int, default=5000, help="Taille de batch (defaut: 5000)")
    parser.add_argument("-v", "--verbose", action="store_true", help="Logs detailles (DEBUG)")

    args = parser.parse_args()

    _setup_logging(verbose=args.verbose)

    # Initialiser la DB avant toute operation
    init_db()

    importer = IMessageImporter(batch_size=args.batch)

    if not importer.is_available():
        print("AVERTISSEMENT : chat.db inaccessible pour le moment.")
        print("  → L'import sera possible quand le fichier sera accessible.")
        if args.status:
            return cmd_status(importer, args)
        # On peut quand meme faire status/reset sans chat.db
        if args.reset:
            return cmd_reset(importer, args)

    if args.reset:
        return cmd_reset(importer, args)
    elif args.check:
        return cmd_check(importer, args)
    elif args.status:
        return cmd_status(importer, args)
    elif args.sync:
        return cmd_sync(importer, args)
    elif args.reconcile:
        return cmd_reconcile(importer, args)
    else:
        return cmd_import(importer, args)


if __name__ == "__main__":
    sys.exit(main())
