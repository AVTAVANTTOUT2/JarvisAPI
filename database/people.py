"""Profils personnels, événements, contexte de vie et index iMessage."""

from __future__ import annotations

import json
import re
import sqlite3
from typing import Any

from jarvis.event_bus import event_bus
from jarvis.events import MemoryUpdated, PersonUpserted

from .core import get_db


def get_life_profile() -> dict:
    with get_db() as conn:
        rows = conn.execute("SELECT category, content FROM life_profile ORDER BY category").fetchall()
        profile = {}
        for r in rows:
            cat = r["category"]
            if cat not in profile:
                profile[cat] = []
            profile[cat].append(r["content"])
        return profile


def get_person(name: str) -> dict | None:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM people WHERE LOWER(name) = LOWER(?)", (name,)).fetchone()
        if row:
            result = dict(row)
            # people_events (legacy)
            pevents = conn.execute(
                "SELECT *, content as content, '' as summary FROM people_events WHERE person_id = ? ORDER BY created_at DESC LIMIT 10",
                (row["id"],)
            ).fetchall()
            # relationship_events (mémoire profonde, source principale)
            revents = conn.execute(
                "SELECT *, '' as content, summary FROM relationship_events WHERE person_id = ? ORDER BY event_date DESC, created_at DESC LIMIT 15",
                (row["id"],)
            ).fetchall()
            # Merge : relationship_events en premier (plus riches), puis people_events
            all_events: list[dict] = [dict(e) for e in revents]
            for pe in pevents:
                all_events.append(dict(pe))
            result["events"] = all_events[:15]
            return result
        return None


def get_all_people() -> list:
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM people ORDER BY last_mentioned DESC").fetchall()
        return [dict(r) for r in rows]


def get_people_sorted_by_recent() -> list:
    """Contacts triés par dernière interaction (last_mentioned, puis événements, puis created_at).

    Inclut message_count = imessage_count (messages iMessage analysés) ou fallback sur events.
    """
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT p.*,
                COALESCE(
                    NULLIF(p.imessage_count, 0),
                    (SELECT COUNT(*) FROM people_events WHERE person_id = p.id) +
                    (SELECT COUNT(*) FROM relationship_events WHERE person_id = p.id)
                ) as message_count
            FROM people p
            ORDER BY datetime(
                COALESCE(
                    NULLIF(TRIM(p.last_mentioned), ''),
                    (SELECT MAX(created_at) FROM people_events e WHERE e.person_id = p.id),
                    (SELECT MAX(created_at) FROM relationship_events r WHERE r.person_id = p.id),
                    p.created_at
                )
            ) DESC
            """
        ).fetchall()
        return [dict(r) for r in rows]


def set_person_ai_description(person_id: int, text: str) -> None:
    with get_db() as conn:
        conn.execute(
            "UPDATE people SET ai_description = ? WHERE id = ?",
            (text, person_id),
        )


def clear_person_ai_description(person_id: int) -> None:
    with get_db() as conn:
        conn.execute("UPDATE people SET ai_description = NULL WHERE id = ?", (person_id,))


def upsert_person(name: str, **kwargs: Any) -> int:
    with get_db() as conn:
        existing = conn.execute("SELECT id FROM people WHERE LOWER(name) = LOWER(?)", (name,)).fetchone()
        if existing:
            person_id = int(existing["id"])
            if kwargs:
                sets = ", ".join(f"{k} = ?" for k in kwargs)
                vals = list(kwargs.values()) + [person_id]
                conn.execute(
                    f"UPDATE people SET {sets}, last_mentioned = CURRENT_TIMESTAMP WHERE id = ?",
                    vals,
                )
            else:
                conn.execute(
                    "UPDATE people SET last_mentioned = CURRENT_TIMESTAMP WHERE id = ?",
                    (person_id,),
                )
            created = False
        else:
            cols = ", ".join(["name"] + list(kwargs.keys()))
            placeholders = ", ".join(["?"] * (1 + len(kwargs)))
            vals = [name] + list(kwargs.values())
            cur = conn.execute(f"INSERT INTO people ({cols}) VALUES ({placeholders})", vals)
            person_id = int(cur.lastrowid)
            created = True
    event_bus.emit_nowait(
        PersonUpserted(person_id, name, {**kwargs, "created": created})
    )
    return person_id


def update_person_imessage_count(person_id: int, count: int) -> None:
    """Met à jour le compteur de messages iMessage analysés pour un contact."""
    with get_db() as conn:
        conn.execute(
            "UPDATE people SET imessage_count = ? WHERE id = ?",
            (count, person_id),
        )


def rename_person_if_phone_number(person_id: int, new_name: str) -> bool:
    """Renomme un contact si son nom actuel est un numéro de téléphone.

    Returns True si renommé, False sinon.
    """
    import re
    with get_db() as conn:
        row = conn.execute("SELECT name FROM people WHERE id = ?", (person_id,)).fetchone()
        if not row:
            return False
        current_name = row["name"] or ""
        # Vérifie si le nom actuel est un numéro de téléphone
        if re.match(r'^[\+\d\s\-\.]+$', current_name.strip()):
            # Vérifie qu'un contact avec ce nom n'existe pas déjà
            existing = conn.execute(
                "SELECT id FROM people WHERE LOWER(name) = LOWER(?) AND id != ?",
                (new_name, person_id)
            ).fetchone()
            if not existing:
                conn.execute(
                    "UPDATE people SET name = ? WHERE id = ?",
                    (new_name, person_id),
                )
                return True
    return False


def get_person_timeline_cache(name: str) -> dict | None:
    """Retourne le cache timeline d'un contact (timeline_cache JSON + timeline_updated_at).

    Retourne None si le contact n'existe pas ou si le cache est vide.
    Retourne un dict {"events": [...], "updated_at": "ISO datetime string"}.
    """
    with get_db() as conn:
        row = conn.execute(
            "SELECT timeline_cache, timeline_updated_at FROM people WHERE LOWER(name) = LOWER(?)",
            (name,),
        ).fetchone()
    if not row or not row["timeline_cache"]:
        return None
    try:
        import json as _json
        events = _json.loads(row["timeline_cache"])
        return {"events": events, "updated_at": row["timeline_updated_at"]}
    except Exception:
        return None


def update_person_timeline_cache(name: str, events: list) -> None:
    """Sérialise `events` en JSON et l'enregistre dans people.timeline_cache.

    Met à jour timeline_updated_at au timestamp courant (UTC).
    """
    import json as _json
    payload = _json.dumps(events, ensure_ascii=False)
    with get_db() as conn:
        conn.execute(
            """UPDATE people
               SET timeline_cache = ?,
                   timeline_updated_at = CURRENT_TIMESTAMP
               WHERE LOWER(name) = LOWER(?)""",
            (payload, name),
        )


def patch_person(old_name: str, fields: dict[str, Any]) -> dict | None:
    """Met à jour une ligne `people` identifiée par le nom (insensible à la casse).

    Champs autorisés : name, relationship, personality_notes, dynamics,
    patterns, birthday. Lève ``ValueError`` si le nouveau nom est déjà
    utilisé par un autre contact.
    """
    allowed = ("name", "relationship", "personality_notes", "dynamics", "patterns", "birthday")
    key = (old_name or "").strip()
    if not key:
        return None
    updates: dict[str, Any] = {}
    for k in allowed:
        if k not in fields:
            continue
        val = fields[k]
        if val is None:
            continue
        if k == "name":
            n = str(val).strip()
            if not n:
                continue
            updates[k] = n
        else:
            updates[k] = val if isinstance(val, str) else str(val)

    if not updates:
        return get_person(key)

    with get_db() as conn:
        row = conn.execute(
            "SELECT id, name FROM people WHERE LOWER(name) = LOWER(?)",
            (key,),
        ).fetchone()
        if not row:
            return None
        pid = row["id"]
        current_name = row["name"]

        if "name" in updates:
            new_n = str(updates["name"]).strip()
            conflict = conn.execute(
                "SELECT id FROM people WHERE LOWER(name) = LOWER(?) AND id != ?",
                (new_n, pid),
            ).fetchone()
            if conflict:
                raise ValueError(f"Une personne nommée « {new_n} » existe déjà.")

        sets = ", ".join(f"{k} = ?" for k in updates)
        vals = list(updates.values()) + [pid]
        conn.execute(f"UPDATE people SET {sets} WHERE id = ?", vals)

    final_lookup = updates.get("name", current_name)
    return get_person(str(final_lookup))


def add_life_profile_entry(category: str, content: str) -> int:
    """Ajoute une entrée au life profile. Retourne l'id."""
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO life_profile (category, content) VALUES (?, ?)",
            (category, content),
        )
        return cur.lastrowid


def update_life_profile_entry(entry_id: int, content: str) -> bool:
    with get_db() as conn:
        cur = conn.execute(
            "UPDATE life_profile SET content = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (content, entry_id),
        )
        return cur.rowcount > 0


def delete_life_profile_entry(entry_id: int) -> bool:
    with get_db() as conn:
        cur = conn.execute("DELETE FROM life_profile WHERE id = ?", (entry_id,))
        return cur.rowcount > 0


def get_life_profile_entries() -> list:
    """Comme `get_life_profile()` mais retourne les ids (utile pour l'édition UI)."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, category, content, updated_at FROM life_profile ORDER BY category, id"
        ).fetchall()
        return [dict(r) for r in rows]


def add_people_event(person_id_or_name: int | str, event_type: str, content: str,
                      lesson_learned: str = None) -> int | None:
    """Ajoute un event à une personne (résolue par id OU par nom).
    Crée la personne si elle n'existe pas (cas string)."""
    with get_db() as conn:
        if isinstance(person_id_or_name, int):
            person_id = person_id_or_name
        else:
            row = conn.execute(
                "SELECT id FROM people WHERE LOWER(name) = LOWER(?)",
                (person_id_or_name,),
            ).fetchone()
            if row:
                person_id = row["id"]
            else:
                cur = conn.execute(
                    "INSERT INTO people (name, last_mentioned) VALUES (?, CURRENT_TIMESTAMP)",
                    (person_id_or_name,),
                )
                person_id = cur.lastrowid

        cur = conn.execute(
            """INSERT INTO people_events (person_id, event_type, content, lesson_learned)
               VALUES (?, ?, ?, ?)""",
            (person_id, event_type, content, lesson_learned),
        )
        return cur.lastrowid


def add_life_context(context_type: str, description: str,
                     period_start: str = None, period_end: str = None,
                     impact_on_mood: str = None,
                     impact_on_productivity: str = None) -> int:
    with get_db() as conn:
        cur = conn.execute(
            """INSERT INTO life_context
               (context_type, description, period_start, period_end,
                impact_on_mood, impact_on_productivity)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (context_type, description, period_start, period_end,
             impact_on_mood, impact_on_productivity),
        )
        context_id = int(cur.lastrowid)
    event_bus.emit_nowait(MemoryUpdated(context_id, context_type, description))
    return context_id


def get_active_life_context() -> list:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM life_context WHERE active = 1 ORDER BY created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def close_life_context(context_id: int) -> bool:
    with get_db() as conn:
        cur = conn.execute(
            "UPDATE life_context SET active = 0, period_end = DATE('now') WHERE id = ?",
            (context_id,),
        )
        return cur.rowcount > 0


def get_all_life_context(limit: int = 100) -> list:
    """Historique complet (actifs + clos), le plus récent en premier."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM life_context ORDER BY COALESCE(period_start, created_at) DESC, id DESC LIMIT ?",
            (max(1, min(limit, 500)),),
        ).fetchall()
        return [dict(r) for r in rows]


def get_analysis_cursor(handle: str) -> int:
    with get_db() as conn:
        row = conn.execute(
            "SELECT last_analyzed_rowid FROM imessage_analysis_cache WHERE handle = ?",
            (handle,),
        ).fetchone()
        return row["last_analyzed_rowid"] if row else 0


def update_analysis_cursor(handle: str, last_rowid: int, messages_count: int) -> None:
    with get_db() as conn:
        conn.execute(
            """INSERT INTO imessage_analysis_cache (handle, last_analyzed_rowid, last_analyzed_at, total_messages_analyzed)
               VALUES (?, ?, CURRENT_TIMESTAMP, ?)
               ON CONFLICT(handle)
               DO UPDATE SET last_analyzed_rowid = excluded.last_analyzed_rowid,
                            last_analyzed_at = excluded.last_analyzed_at,
                            total_messages_analyzed = total_messages_analyzed + excluded.total_messages_analyzed""",
            (handle, last_rowid, messages_count),
        )


def get_total_messages_analyzed(handle: str) -> int:
    """Retourne le nombre total de messages analysés pour un handle iMessage."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT total_messages_analyzed FROM imessage_analysis_cache WHERE handle = ?",
            (handle,),
        ).fetchone()
        return row["total_messages_analyzed"] if row else 0


def sync_imessage_counts_to_people() -> int:
    """Synchronise les compteurs imessage_count dans people depuis la cache d'analyse.

    Returns le nombre de mises à jour effectuées.
    """
    with get_db() as conn:
        # Mise à jour via JOIN entre people (via relationship_profiles.handle) et imessage_analysis_cache
        result = conn.execute(
            """
            UPDATE people SET imessage_count = (
                SELECT iac.total_messages_analyzed
                FROM relationship_profiles rp
                JOIN imessage_analysis_cache iac ON LOWER(rp.handle) = LOWER(iac.handle)
                WHERE rp.person_id = people.id
            )
            WHERE id IN (
                SELECT rp.person_id FROM relationship_profiles rp
                JOIN imessage_analysis_cache iac ON LOWER(rp.handle) = LOWER(iac.handle)
            )
            """
        )
        return result.rowcount


def _normalize_handle_for_match(handle: str) -> str:
    h = (handle or "").strip().lower()
    if not h:
        return ""
    if "@" in h:
        return h
    digits = re.sub(r"[^\d]", "", h)
    if digits.startswith("33") and len(digits) >= 11:
        digits = "0" + digits[2:]
    elif digits.startswith("0033") and len(digits) >= 13:
        digits = "0" + digits[4:]
    if len(digits) > 10:
        digits = digits[-10:]
    return digits or h


def _merge_people_ids(conn: sqlite3.Connection, keep_id: int, drop_id: int) -> None:
    """Fusionne drop_id vers keep_id dans les tables relationnelles."""
    if keep_id == drop_id:
        return
    conn.execute("UPDATE people_events SET person_id = ? WHERE person_id = ?", (keep_id, drop_id))
    conn.execute("UPDATE relationship_events SET person_id = ? WHERE person_id = ?", (keep_id, drop_id))
    conn.execute("UPDATE relationship_profiles SET person_id = ? WHERE person_id = ?", (keep_id, drop_id))
    conn.execute("DELETE FROM people WHERE id = ?", (drop_id,))


def force_upsert_people_from_mac_sync(records: list[dict[str, Any]]) -> dict[str, int]:
    """UPSERT massif depuis sync macOS (contacts + iMessage), avec correction dates.

    Chaque record peut contenir:
      - handle
      - name
      - msg_count
      - first_message_at / last_message_at (ISO)
      - last_rowid
    """
    stats = {
        "input_records": len(records),
        "created": 0,
        "updated": 0,
        "dates_corrected": 0,
        "profiles_upserted": 0,
        "cache_upserted": 0,
        "merged_duplicates": 0,
    }
    if not records:
        return stats

    with get_db() as conn:
        # Index existants
        people_rows = conn.execute("SELECT id, name, last_mentioned, COALESCE(imessage_count, 0) AS imessage_count FROM people").fetchall()
        by_name: dict[str, dict] = {str(r["name"]).strip().lower(): dict(r) for r in people_rows if r["name"]}

        profile_rows = conn.execute("SELECT id, person_id, handle FROM relationship_profiles WHERE handle IS NOT NULL").fetchall()
        by_handle_norm: dict[str, int] = {}
        for r in profile_rows:
            hn = _normalize_handle_for_match(str(r["handle"] or ""))
            if hn:
                by_handle_norm[hn] = int(r["person_id"])

        for rec in records:
            raw_handle = str(rec.get("handle") or "").strip()
            handle_norm = _normalize_handle_for_match(raw_handle)
            name = str(rec.get("name") or "").strip() or raw_handle or "Contact inconnu"
            name_key = name.lower()
            msg_count = int(rec.get("msg_count") or 0)
            last_rowid = int(rec.get("last_rowid") or 0)
            last_message_at = str(rec.get("last_message_at") or "").strip() or None

            person_id = None
            if handle_norm and handle_norm in by_handle_norm:
                person_id = by_handle_norm[handle_norm]
            elif name_key in by_name:
                person_id = int(by_name[name_key]["id"])

            if person_id is None:
                cur = conn.execute(
                    "INSERT INTO people (name, relationship, last_mentioned, imessage_count) VALUES (?, ?, ?, ?)",
                    (name, "connaissance", last_message_at, msg_count),
                )
                person_id = int(cur.lastrowid)
                stats["created"] += 1
                by_name[name_key] = {
                    "id": person_id,
                    "name": name,
                    "last_mentioned": last_message_at,
                    "imessage_count": msg_count,
                }
            else:
                row = conn.execute(
                    "SELECT name, last_mentioned, COALESCE(imessage_count,0) AS imessage_count FROM people WHERE id = ?",
                    (person_id,),
                ).fetchone()
                old_last = (row["last_mentioned"] or "") if row else ""
                old_count = int((row["imessage_count"] or 0) if row else 0)
                new_last = old_last
                date_changed = False
                if last_message_at and (not old_last or last_message_at > old_last):
                    new_last = last_message_at
                    date_changed = True
                    stats["dates_corrected"] += 1
                new_count = max(old_count, msg_count)
                conn.execute(
                    "UPDATE people SET last_mentioned = ?, imessage_count = ? WHERE id = ?",
                    (new_last, new_count, person_id),
                )
                stats["updated"] += 1
                if date_changed:
                    by_name.setdefault(name_key, {"id": person_id, "name": name})["last_mentioned"] = new_last
                by_name.setdefault(name_key, {"id": person_id, "name": name})["imessage_count"] = new_count

            # UPsert profile par person_id
            rp = conn.execute("SELECT id FROM relationship_profiles WHERE person_id = ?", (person_id,)).fetchone()
            if rp:
                conn.execute(
                    "UPDATE relationship_profiles SET handle = ?, last_analyzed = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP WHERE person_id = ?",
                    (raw_handle or None, person_id),
                )
            else:
                conn.execute(
                    "INSERT INTO relationship_profiles (person_id, handle, last_analyzed) VALUES (?, ?, CURRENT_TIMESTAMP)",
                    (person_id, raw_handle or None),
                )
            stats["profiles_upserted"] += 1

            # Détection/fusion des doublons: même handle -> même person_id
            if handle_norm:
                previous_person_id = by_handle_norm.get(handle_norm)
                if previous_person_id and previous_person_id != person_id:
                    _merge_people_ids(conn, keep_id=previous_person_id, drop_id=person_id)
                    person_id = previous_person_id
                    stats["merged_duplicates"] += 1
                by_handle_norm[handle_norm] = person_id

            # imessage_analysis_cache: écraser avec l'état réel (pas + incrémental)
            if raw_handle:
                conn.execute(
                    """
                    INSERT INTO imessage_analysis_cache (handle, last_analyzed_rowid, last_analyzed_at, total_messages_analyzed)
                    VALUES (?, ?, CURRENT_TIMESTAMP, ?)
                    ON CONFLICT(handle)
                    DO UPDATE SET
                        last_analyzed_rowid = CASE
                            WHEN excluded.last_analyzed_rowid > imessage_analysis_cache.last_analyzed_rowid
                                THEN excluded.last_analyzed_rowid
                            ELSE imessage_analysis_cache.last_analyzed_rowid
                        END,
                        last_analyzed_at = excluded.last_analyzed_at,
                        total_messages_analyzed = CASE
                            WHEN excluded.total_messages_analyzed > imessage_analysis_cache.total_messages_analyzed
                                THEN excluded.total_messages_analyzed
                            ELSE imessage_analysis_cache.total_messages_analyzed
                        END
                    """,
                    (raw_handle, last_rowid, msg_count),
                )
                stats["cache_upserted"] += 1

    return stats
