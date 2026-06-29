"""Synchronise la table `people` avec Contacts.app : numéros / emails → vrais noms."""

from __future__ import annotations

import asyncio
import logging
import re

from database import get_all_people, get_db

logger = logging.getLogger(__name__)

_PHONE_LIKE = re.compile(r"^[\+\d\s\-\.()]+$")


def _looks_like_handle_not_name(name: str) -> bool:
    n = (name or "").strip()
    if not n:
        return False
    if "@" in n:
        return True
    return bool(_PHONE_LIKE.match(n))


def _merge_into_existing(conn, keep_id: int, drop_id: int) -> None:
    conn.execute(
        "UPDATE people_events SET person_id = ? WHERE person_id = ?",
        (keep_id, drop_id),
    )
    conn.execute(
        "UPDATE relationship_events SET person_id = ? WHERE person_id = ?",
        (keep_id, drop_id),
    )
    keep_prof = conn.execute(
        "SELECT id FROM relationship_profiles WHERE person_id = ?",
        (keep_id,),
    ).fetchone()
    dup_profiles = conn.execute(
        "SELECT id FROM relationship_profiles WHERE person_id = ?",
        (drop_id,),
    ).fetchall()
    if keep_prof:
        for row in dup_profiles:
            conn.execute("DELETE FROM relationship_profiles WHERE id = ?", (row["id"],))
    else:
        conn.execute(
            "UPDATE relationship_profiles SET person_id = ? WHERE person_id = ?",
            (keep_id, drop_id),
        )
    conn.execute("DELETE FROM people WHERE id = ?", (drop_id,))


def _similar(a: str, b: str) -> bool:
    """Retourne True si les deux noms sont similaires (distance de Levenshtein <= 2)."""
    x, y = (a or "").lower().strip(), (b or "").lower().strip()
    if not x or not y:
        return False
    if x == y:
        return True
    if abs(len(x) - len(y)) > 2:
        return False
    if len(x) > len(y):
        x, y = y, x
    distances = list(range(len(x) + 1))
    for i2, c2 in enumerate(y):
        new_distances = [i2 + 1]
        for i1, c1 in enumerate(x):
            if c1 == c2:
                new_distances.append(distances[i1])
            else:
                new_distances.append(1 + min(distances[i1], distances[i1 + 1], new_distances[-1]))
        distances = new_distances
    return distances[-1] <= 2


def sync_people_names_sync() -> dict:
    """Parcourt `people` et remplace les handles par les noms Contacts.app."""
    from integrations.contacts import contacts_reader

    if not contacts_reader.is_available():
        logger.warning("[sync] Contacts.app non disponible (hors macOS ou osascript)")
        return {"ok": False, "updated": 0, "reason": "unavailable"}

    contacts_reader.build_cache()
    if not contacts_reader._cache:
        logger.warning("[sync] Cache contacts vide — vérifier Contacts.app / Automation")
        return {"ok": True, "updated": 0, "reason": "empty_cache"}

    people = get_all_people()
    updated = 0

    all_contact_names = {str(v).strip() for v in contacts_reader._cache.values() if str(v).strip()}

    with get_db() as conn:
        # Passe 1 : handles (numéro/email) -> nom Contacts
        for person in people:
            pid = int(person["id"])
            name = (person.get("name") or "").strip()
            if not _looks_like_handle_not_name(name):
                continue
            resolved = contacts_reader.resolve_handle(name)
            if not resolved or resolved.strip().lower() == name.strip().lower():
                continue
            resolved = resolved.strip()
            existing = conn.execute(
                "SELECT id FROM people WHERE LOWER(TRIM(name)) = LOWER(TRIM(?)) AND id != ?",
                (resolved, pid),
            ).fetchone()
            if existing:
                keep_id = int(existing["id"])
                if keep_id != pid:
                    _merge_into_existing(conn, keep_id, pid)
                    logger.info("[sync] Fusionné %s → %s (conservation id %s)", name, resolved, keep_id)
                    updated += 1
            else:
                conn.execute(
                    "UPDATE people SET name = ? WHERE id = ?",
                    (resolved, pid),
                )
                logger.info("[sync] Renommé %s → %s", name, resolved)
                updated += 1

        # Passe 2 : correction fuzzy des noms potentiellement mal orthographiés
        refreshed = conn.execute("SELECT id, name FROM people ORDER BY id").fetchall()
        for row in refreshed:
            pid = int(row["id"])
            name = str(row["name"] or "").strip()
            if not name:
                continue
            if _looks_like_handle_not_name(name):
                continue
            if name in all_contact_names:
                continue

            for real_name in all_contact_names:
                if not _similar(name, real_name):
                    continue
                if name.lower() == real_name.lower():
                    continue

                existing = conn.execute(
                    "SELECT id FROM people WHERE LOWER(name) = LOWER(?) AND id != ?",
                    (real_name, pid),
                ).fetchone()
                if existing:
                    keep_id = int(existing["id"])
                    _merge_into_existing(conn, keep_id, pid)
                    logger.info("[sync] Fuzzy merge : %s -> %s", name, real_name)
                else:
                    conn.execute("UPDATE people SET name = ? WHERE id = ?", (real_name, pid))
                    logger.info("[sync] Fuzzy rename : %s -> %s", name, real_name)
                updated += 1
                break

    logger.info(
        "[sync] Terminé — %d mise(s) à jour sur %d personne(s) en base",
        updated,
        len(people),
    )
    return {"ok": True, "updated": updated, "scanned": len(people)}


async def sync_people_names() -> dict:
    """Version async pour FastAPI (AppleScript et SQLite hors du event loop)."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, sync_people_names_sync)
