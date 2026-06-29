"""Alertes relationnelles périodiques (scheduler)."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


def _handle_from_display_name(name: str) -> str | None:
    from integrations.contacts import contacts_reader

    try:
        contacts_reader.build_cache()
    except Exception as e:
        logger.warning("[contact_alerts] cache contacts : %s", e)
    target = (name or "").strip().lower()
    if not target:
        return None
    for h, disp in contacts_reader._cache.items():
        if (disp or "").strip().lower() != target:
            continue
        hs = str(h).strip()
        if hs.startswith("+") or "@" in hs:
            return hs
        if re.match(r"^\+?\d", hs):
            return hs
    return None


def _notification_recently_sent(title: str, hours: float = 36.0) -> bool:
    from database import get_db

    cutoff = (datetime.now() - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")
    try:
        with get_db() as conn:
            row = conn.execute(
                "SELECT 1 FROM notifications WHERE title = ? AND created_at >= ? LIMIT 1",
                (title, cutoff),
            ).fetchone()
            return row is not None
    except Exception:
        return False


def _resolve_handle(person: dict) -> str | None:
    from database import get_relationship_profile

    pid = person.get("id")
    prof = get_relationship_profile(pid) if pid else None
    if prof and prof.get("handle"):
        return str(prof["handle"]).strip()
    n = (person.get("name") or "").strip()
    if "@" in n:
        return n
    if re.match(r"^\+?\d[\d\s\-\(\)\.]+$", n):
        return re.sub(r"\s+", "", n)
    return _handle_from_display_name(n)


async def check_relationship_alerts() -> None:
    from database import create_notification, get_all_people
    from integrations.imessage_reader import _apple_ts_to_datetime_from_value
    from integrations.imessage_reader import imessage_reader

    if not imessage_reader or not imessage_reader.is_available():
        logger.debug("[contact_alerts] chat.db indisponible — skip")
        return

    now = datetime.now()
    people = get_all_people()

    for person in people:
        try:
            handle = _resolve_handle(person)
            if not handle:
                continue

            msgs = imessage_reader.get_conversation_for_period(handle, days=60, limit=400)
            if not msgs:
                continue

            norm = []
            for m in msgs:
                dt = _apple_ts_to_datetime_from_value(m.get("date"))
                if dt is None:
                    continue
                norm.append({**m, "date": dt})

            if not norm:
                continue

            last_msg_date = max(m["date"] for m in norm)
            days_since = (now - last_msg_date).days
            n_msg = len(norm)
            avg_gap_days = 60 / max(n_msg, 1)

            pname = person.get("name") or handle

            if days_since > avg_gap_days * 2 and days_since > 3:
                title = f"Silence avec {pname}"
                if not _notification_recently_sent(title):
                    create_notification(
                        source="relationship",
                        title=title,
                        content=(
                            f"Pas de contact depuis {days_since} jours. "
                            f"D'habitude en moyenne tous les {round(avg_gap_days, 1)} jours."
                        ),
                        priority="medium",
                    )

            sorted_msgs = sorted(norm, key=lambda x: x["date"])
            last = sorted_msgs[-1]
            if not last.get("is_from_me"):
                hours = (now - last["date"]).total_seconds() / 3600
                if hours > 24:
                    title_u = f"Message non répondu — {pname}"
                    if not _notification_recently_sent(title_u, hours=18.0):
                        snippet = ((last.get("text") or "") or "")[:80]
                        create_notification(
                            source="relationship",
                            title=title_u,
                            content=f"Il y a ~{round(hours)} h : « {snippet} »",
                            priority="high",
                        )
        except Exception as e:
            logger.warning("[contact_alerts] person %s : %s", person.get("name"), e)
