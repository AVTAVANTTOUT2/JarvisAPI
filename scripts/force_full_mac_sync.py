"""Sync profonde macOS -> JARVIS (contacts + iMessage) avec correction des dates.

Usage:
    python scripts/force_full_mac_sync.py
"""

from __future__ import annotations

import logging
from pathlib import Path
import sys
from typing import Any

# Permet l'exécution directe: python scripts/force_full_mac_sync.py
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from database import force_upsert_people_from_mac_sync, init_db
from integrations.contacts import contacts_reader
from integrations.imessage_reader import imessage_reader

logger = logging.getLogger("force_full_mac_sync")


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


def _build_contact_name_map() -> dict[str, str]:
    """Construit un index handle/email/numéro -> nom depuis contacts macOS."""
    contacts = contacts_reader.get_all_contacts()
    logger.info("[sync] Contacts extraits: %d", len(contacts))
    idx: dict[str, str] = {}
    for c in contacts:
        name = str(c.get("name") or "").strip()
        if not name:
            continue
        for e in c.get("emails", []):
            key = str(e or "").strip().lower()
            if key:
                idx[key] = name
        for p in c.get("phones", []):
            raw = str(p or "").strip()
            if not raw:
                continue
            idx[raw] = name
            norm = contacts_reader._normalize_phone(raw)  # noqa: SLF001 - utilitaire interne assumé ici
            if norm:
                idx[norm] = name
                if norm.startswith("0") and len(norm) == 10:
                    idx["+33" + norm[1:]] = name
                    idx["0033" + norm[1:]] = name
    logger.info("[sync] Index handles contacts: %d clés", len(idx))
    return idx


def _resolve_name(handle: str, contact_idx: dict[str, str]) -> str:
    """Résout un handle iMessage en nom humain."""
    h = (handle or "").strip()
    if not h:
        return "Contact inconnu"
    if h in contact_idx:
        return contact_idx[h]
    low = h.lower()
    if low in contact_idx:
        return contact_idx[low]
    norm = contacts_reader._normalize_phone(h)  # noqa: SLF001
    if norm and norm in contact_idx:
        return contact_idx[norm]
    if norm.startswith("0") and len(norm) == 10:
        p1 = "+33" + norm[1:]
        p2 = "0033" + norm[1:]
        if p1 in contact_idx:
            return contact_idx[p1]
        if p2 in contact_idx:
            return contact_idx[p2]
    return h


def _aggregate_imessage_records(raw_rows: list[dict[str, Any]], contact_idx: dict[str, str]) -> list[dict[str, Any]]:
    """Agrège les stats iMessage par handle (sécurité anti-doublons)."""
    by_handle: dict[str, dict[str, Any]] = {}
    for row in raw_rows:
        handle = str(row.get("handle") or "").strip()
        if not handle:
            continue
        cur = by_handle.get(handle)
        if cur is None:
            by_handle[handle] = {
                "handle": handle,
                "name": _resolve_name(handle, contact_idx),
                "msg_count": int(row.get("msg_count") or 0),
                "first_message_at": row.get("first_message_at"),
                "last_message_at": row.get("last_message_at"),
                "last_rowid": int(row.get("last_rowid") or 0),
            }
            continue
        cur["msg_count"] = max(int(cur["msg_count"]), int(row.get("msg_count") or 0))
        if row.get("first_message_at") and (
            not cur.get("first_message_at") or str(row["first_message_at"]) < str(cur["first_message_at"])
        ):
            cur["first_message_at"] = row["first_message_at"]
        if row.get("last_message_at") and (
            not cur.get("last_message_at") or str(row["last_message_at"]) > str(cur["last_message_at"])
        ):
            cur["last_message_at"] = row["last_message_at"]
        cur["last_rowid"] = max(int(cur.get("last_rowid") or 0), int(row.get("last_rowid") or 0))
    return list(by_handle.values())


def run_force_full_mac_sync() -> dict[str, Any]:
    """Exécute la synchronisation complète et retourne un rapport."""
    logger.info("[sync] Initialisation DB…")
    init_db()

    logger.info("[sync] Extraction contacts macOS (AddressBook sqlite + fallback AppleScript)…")
    contact_idx = _build_contact_name_map()

    logger.info("[sync] Extraction complète iMessage chat.db (toutes conversations)…")
    conv_stats = imessage_reader.get_all_conversation_stats_full()
    logger.info("[sync] Conversations distinctes trouvées: %d", len(conv_stats))

    records = _aggregate_imessage_records(conv_stats, contact_idx)
    logger.info("[sync] Records agrégés prêts pour UPSERT: %d", len(records))

    result = force_upsert_people_from_mac_sync(records)
    logger.info("[sync] Résultat UPSERT: %s", result)

    return {
        "contacts_indexed": len(contact_idx),
        "conversation_rows": len(conv_stats),
        "records_upserted": len(records),
        "db_result": result,
    }


if __name__ == "__main__":
    _setup_logging()
    report = run_force_full_mac_sync()
    print("\n=== FORCE FULL MAC SYNC REPORT ===")
    for k, v in report.items():
        print(f"{k}: {v}")
