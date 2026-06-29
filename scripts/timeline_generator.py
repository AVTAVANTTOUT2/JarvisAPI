"""Génération d'une timeline relationnelle via Haiku (à la demande, coût tokens)."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime

import config
import llm
from database import get_person, get_relationship_profile

logger = logging.getLogger(__name__)

_JSON_BLOCK = re.compile(r"```(?:json)?\s*\n([\s\S]*?)\n```", re.IGNORECASE)
_JSON_ARRAY = re.compile(r"\[[\s\S]*\]")

_NAME_EMAIL_PHONE = re.compile(r"^\+?\d[\d\s\-\(\)\.]+$")


def _normalize_name(name: str) -> str:
    return (name or "").strip()


def resolve_handle_for_person(person_name: str) -> tuple[dict | None, str | None]:
    """Retourne (person dict, handle iMessage) ou (None, None)."""
    from integrations.contacts import contacts_reader

    key = _normalize_name(person_name)
    person = get_person(key)
    if not person:
        return None, None

    pid = person.get("id")
    profile = get_relationship_profile(pid) if pid else None
    if profile and profile.get("handle"):
        h = str(profile["handle"]).strip()
        if h:
            return person, h

    n = (person.get("name") or "").strip()
    if "@" in n:
        return person, n
    if _NAME_EMAIL_PHONE.match(n):
        return person, re.sub(r"\s+", "", n)

    try:
        contacts_reader.build_cache()
    except Exception as e:
        logger.warning("[timeline] contacts cache : %s", e)

    low = n.lower()
    for handle, disp in contacts_reader._cache.items():
        if (disp or "").strip().lower() == low:
            hs = str(handle).strip()
            if hs.startswith("+") or "@" in hs:
                return person, hs

    return person, None


def _parse_message_dt(val) -> datetime | None:
    if isinstance(val, datetime):
        return val.replace(tzinfo=None) if val.tzinfo else val
    if isinstance(val, str):
        try:
            return datetime.fromisoformat(val.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            return None
    return None


def _parse_events_json(content: str) -> list[dict]:
    text = (content or "").strip()
    if not text:
        return []
    try:
        data = json.loads(text)
        if isinstance(data, list):
            return [x for x in data if isinstance(x, dict)]
    except json.JSONDecodeError:
        pass
    m = _JSON_BLOCK.search(text)
    if m:
        try:
            data = json.loads(m.group(1).strip())
            if isinstance(data, list):
                return [x for x in data if isinstance(x, dict)]
        except json.JSONDecodeError:
            pass
    m2 = _JSON_ARRAY.search(text)
    if m2:
        try:
            data = json.loads(m2.group(0))
            if isinstance(data, list):
                return [x for x in data if isinstance(x, dict)]
        except json.JSONDecodeError:
            pass
    return []


def _chunks(lst: list, n: int):
    for i in range(0, len(lst), n):
        yield lst[i : i + n]


async def generate_timeline(person_name: str, handle_override: str | None = None) -> list[dict]:
    from integrations.imessage_reader import imessage_reader

    person, handle = resolve_handle_for_person(person_name)
    if handle_override:
        handle = handle_override.strip()
    display_name = (person.get("name") if person else None) or person_name

    if not imessage_reader or not imessage_reader.is_available() or not handle:
        return []

    raw = imessage_reader.get_conversation_for_period(handle, days=730, limit=500)
    msgs = []
    for m in raw:
        dt = _parse_message_dt(m.get("date"))
        if dt is None:
            continue
        msgs.append({**m, "date": dt})

    if not msgs:
        return []

    msgs.sort(key=lambda x: x["date"])
    all_events: list[dict] = []

    for chunk in _chunks(msgs, 50):
        formatted = "\n".join(
            [
                f"[{m['date'].strftime('%d/%m/%Y %H:%M')}] "
                f"{'MOI' if m['is_from_me'] else display_name}: "
                f"{((m.get('text') or '') or '')[:200]}"
                for m in chunk
            ]
        )
        try:
            result = await llm.chat(
                messages=[{"role": "user", "content": formatted}],
                model=config.DEEPSEEK_FAST_MODEL,
                system=(
                    f"Extrais les événements marquants de cette conversation entre "
                    f"l'utilisateur et {display_name}.\n"
                    "Retourne UNIQUEMENT un JSON array :\n"
                    '[{"date": "YYYY-MM-DD", "type": "first_contact|conflict|reconciliation|'
                    'milestone|deep_conversation|distance|reunion|support", '
                    '"title": "titre court", "summary": "résumé en 1 phrase"}]\n'
                    "Événements significatifs uniquement. Maximum 5 événements par bloc."
                ),
                max_tokens=500,
                temperature=0.0,
                use_cache=False,
            )
            events = _parse_events_json(result.get("content") or "")
            all_events.extend(events)
        except Exception as e:
            logger.warning("[timeline] chunk Haiku : %s", e)

    seen: set[tuple[str, str]] = set()
    deduped: list[dict] = []
    for ev in sorted(all_events, key=lambda e: str(e.get("date") or "")):
        k = (str(ev.get("date") or ""), str(ev.get("title") or ""))
        if k in seen:
            continue
        seen.add(k)
        deduped.append(ev)

    return deduped
