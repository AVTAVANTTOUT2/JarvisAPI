"""Lecture du répertoire Contacts.app (macOS) via AppleScript.

Mappe téléphones / emails → nom affiché pour résoudre les handles iMessage
stockés comme numéros dans la table `people`.

Permissions : Automation pour Contacts.app au premier appel `osascript`.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from pathlib import Path
from typing import Any

from ._applescript import is_macos_with_osascript, run_applescript

logger = logging.getLogger(__name__)

OSASCRIPT_TIMEOUT = 90.0


class ContactsReader:
    """Cache handle (numéro brut, normalisé, email) → nom affiché Contacts.app."""

    def __init__(self) -> None:
        self._cache: dict[str, str] = {}
        self._available: bool | None = None

    def is_available(self) -> bool:
        if self._available is True:
            return True
        if not is_macos_with_osascript():
            self._available = False
            return False
        self._available = True
        return True

    @staticmethod
    def _run_applescript(script: str) -> str | None:
        result = run_applescript(script, timeout=OSASCRIPT_TIMEOUT)
        if not result.ok:
            if result.reason == "nonzero_exit":
                logger.error("[Contacts] AppleScript (%s) : %s", result.returncode, result.stderr)
            else:
                logger.error("[Contacts] osascript : %s", result.stderr)
            return None
        return result.stdout

    def _normalize_phone(self, phone: str) -> str:
        """Normalise un numéro français vers 10 chiffres (forme 0XXXXXXXXX)."""
        digits = re.sub(r"[^\d]", "", phone or "")
        if digits.startswith("33") and len(digits) >= 11:
            digits = "0" + digits[2:]
        elif digits.startswith("0033") and len(digits) >= 13:
            digits = "0" + digits[4:]
        if len(digits) > 10:
            digits = digits[-10:]
        return digits

    def _parse_export_lines(self, raw: str) -> list[tuple[str, str]]:
        pairs: list[tuple[str, str]] = []
        for line in raw.splitlines():
            line = line.strip()
            if not line or "\t" not in line:
                continue
            handle, name = line.split("\t", 1)
            handle = handle.strip()
            name = name.strip().replace("\t", " ")
            if handle and name:
                pairs.append((handle, name))
        return pairs

    @staticmethod
    def _addressbook_db_paths() -> list[Path]:
        base = Path.home() / "Library" / "Application Support" / "AddressBook" / "Sources"
        if not base.exists():
            return []
        return list(base.glob("*/AddressBook-v22.abcddb"))

    def _extract_from_addressbook_sqlite(self) -> list[dict[str, Any]]:
        """Extraction directe AddressBook CoreData (read-only).

        Cette voie est plus robuste que l'AppleScript quand Contacts.app est fermée
        ou quand Automation est refusée.
        """
        contacts_by_name: dict[str, dict[str, Any]] = {}
        db_paths = self._addressbook_db_paths()
        if not db_paths:
            return []

        # Variantes de schéma selon versions macOS.
        query_variants = [
            """
            SELECT
              TRIM(COALESCE(p.ZFIRSTNAME, '') || ' ' || COALESCE(p.ZLASTNAME, '')) AS display_name,
              ph.ZFULLNUMBER AS phone,
              em.ZADDRESS AS email
            FROM ZABCDRECORD p
            LEFT JOIN ZABCDPHONENUMBER ph ON ph.ZOWNER = p.Z_PK
            LEFT JOIN ZABCDEMAILADDRESS em ON em.ZOWNER = p.Z_PK
            WHERE p.Z_PK IS NOT NULL
            """,
            """
            SELECT
              COALESCE(p.ZFULLNAME, TRIM(COALESCE(p.ZFIRSTNAME, '') || ' ' || COALESCE(p.ZLASTNAME, ''))) AS display_name,
              ph.ZFULLNUMBER AS phone,
              em.ZADDRESS AS email
            FROM ZABCDRECORD p
            LEFT JOIN ZABCDPHONENUMBER ph ON ph.ZOWNER = p.Z_PK
            LEFT JOIN ZABCDEMAILADDRESS em ON em.ZOWNER = p.Z_PK
            WHERE p.Z_PK IS NOT NULL
            """,
        ]

        for db_path in db_paths:
            try:
                conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
                conn.row_factory = sqlite3.Row
            except Exception as e:
                logger.warning("[Contacts] AddressBook open failed %s: %s", db_path, e)
                continue

            rows = []
            for q in query_variants:
                try:
                    rows = conn.execute(q).fetchall()
                    if rows:
                        break
                except sqlite3.Error:
                    continue

            conn.close()
            if not rows:
                continue

            for r in rows:
                display_name = (r["display_name"] or "").strip()
                phone = (r["phone"] or "").strip()
                email = (r["email"] or "").strip().lower()

                # Ne jamais ignorer un contact: fallback sur handle brut.
                if not display_name:
                    display_name = phone or email or "Contact sans nom"

                row = contacts_by_name.setdefault(display_name, {"name": display_name, "phones": [], "emails": []})
                if phone:
                    row["phones"].append(phone)
                if email:
                    row["emails"].append(email)

        # Dédup légère
        out: list[dict[str, Any]] = []
        for v in contacts_by_name.values():
            v["phones"] = sorted(set(v["phones"]))
            v["emails"] = sorted(set(v["emails"]))
            out.append(v)
        if out:
            logger.info("[Contacts] AddressBook sqlite: %d contacts extraits", len(out))
        return out

    def get_all_contacts(self) -> list[dict[str, Any]]:
        """Exporte Contacts.app : liste {name, phones[], emails[]} agrégée par personne."""
        # 1) Extraction SQLite AddressBook (robuste)
        sqlite_contacts = self._extract_from_addressbook_sqlite()

        # 2) Fallback / complément AppleScript
        if not self.is_available() and sqlite_contacts:
            return sqlite_contacts
        if not self.is_available():
            return []
        script = r'''
tell application "Contacts"
    set AppleScript's text item delimiters to linefeed
    set linesOut to {}
    repeat with p in people
        try
            set displayName to name of p as text
            if displayName is not "" then
                repeat with ph in (get phones of p)
                    try
                        set pv to value of ph as text
                        set end of linesOut to (pv & tab & displayName)
                    end try
                end repeat
                repeat with em in (get emails of p)
                    try
                        set ev to value of em as text
                        set end of linesOut to (ev & tab & displayName)
                    end try
                end repeat
            end if
        end try
    end repeat
    return linesOut as text
end tell
'''
        raw = self._run_applescript(script)
        if not raw:
            return sqlite_contacts

        by_name: dict[str, dict[str, Any]] = {
            c["name"]: {"name": c["name"], "phones": list(c.get("phones", [])), "emails": list(c.get("emails", []))}
            for c in sqlite_contacts
        }
        for handle, display_name in self._parse_export_lines(raw):
            row = by_name.setdefault(display_name, {"name": display_name, "phones": [], "emails": []})
            if "@" in handle:
                row["emails"].append(handle)
            else:
                row["phones"].append(handle)
        out = []
        for v in by_name.values():
            v["phones"] = sorted(set(v["phones"]))
            v["emails"] = sorted(set(v["emails"]))
            out.append(v)
        return out

    def build_cache(self) -> None:
        """Construit le cache handle → nom depuis Contacts.app."""
        self._cache.clear()
        contacts = self.get_all_contacts()
        if not contacts:
            logger.warning("[Contacts] Aucun contact extrait (sqlite + AppleScript)")
            return

        for c in contacts:
            name = str(c.get("name") or "").strip()
            if not name:
                continue
            for email in c.get("emails", []):
                e = str(email).strip().lower()
                if e:
                    self._cache[e] = name
            for phone in c.get("phones", []):
                p = str(phone).strip()
                if not p:
                    continue
                n = self._normalize_phone(p)
                self._cache[p] = name
                if n:
                    self._cache[n] = name
                if n.startswith("0") and len(n) == 10:
                    self._cache["+33" + n[1:]] = name
                    self._cache["0033" + n[1:]] = name

        logger.info("[Contacts] Cache construit : %d clés → noms", len(self._cache))

    def resolve_handle(self, handle: str) -> str:
        """Résout un handle (numéro / email) en nom affiché."""
        if not handle or not str(handle).strip():
            return handle
        h = str(handle).strip()
        if not self._cache:
            self.build_cache()
        if not self._cache:
            return h

        if h in self._cache:
            return self._cache[h]
        low = h.lower()
        if low in self._cache:
            return self._cache[low]

        normalized = self._normalize_phone(h)
        if normalized and normalized in self._cache:
            return self._cache[normalized]

        if normalized.startswith("0") and len(normalized) == 10:
            alt = "+33" + normalized[1:]
            if alt in self._cache:
                return self._cache[alt]

        if len(normalized) >= 8:
            suffix = normalized[-8:]
            for cached_handle, disp_name in self._cache.items():
                if cached_handle.startswith("+") or cached_handle.isdigit() or re.match(
                    r"^[\d\s\-\+]+$", str(cached_handle)
                ):
                    ch_norm = self._normalize_phone(str(cached_handle))
                    if ch_norm.endswith(suffix) or str(cached_handle).replace(" ", "").endswith(suffix):
                        return disp_name

        return h


contacts_reader = ContactsReader()
