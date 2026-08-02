"""Génération d'une timeline relationnelle via DeepSeek (à la demande, coût tokens)."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime

import config
import llm
from database import get_person, get_relationship_profile
from jarvis.security.llm_data_boundary import (
    UNTRUSTED_DATA_SYSTEM_RULE,
    redact_for_external_llm,
    wrap_untrusted_data,
)

_TIMELINE_CHUNK_CONCURRENCY = 3
_TIMELINE_SYSTEM_TEMPLATE = (
    "Extrais les événements marquants de cette conversation entre "
    "l'utilisateur et {display_name}.\n"
    "Retourne UNIQUEMENT un JSON array valide et COMPLET (fermé par ]).\n"
    '[{{"date": "YYYY-MM-DD", "type": "first_contact|conflict|reconciliation|'
    'milestone|deep_conversation|distance|reunion|support", '
    '"title": "titre court", "summary": "résumé en 1 phrase"}}]\n'
    "Maximum 3 événements par bloc. Titres ≤ 6 mots, summaries ≤ 15 mots. "
    "Si rien de notable : []."
)
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


_EVENT_OBJECT = re.compile(r"\{[^{}]*\}")


def _is_timeline_event(item: object) -> bool:
    return (
        isinstance(item, dict)
        and bool(item.get("date"))
        and bool(item.get("title") or item.get("summary"))
    )


def _salvage_event_objects(text: str) -> list[dict]:
    """Récupère les objets JSON complets dans une réponse tronquée (max_tokens)."""
    events: list[dict] = []
    for match in _EVENT_OBJECT.finditer(text or ""):
        try:
            obj = json.loads(match.group(0))
        except json.JSONDecodeError:
            continue
        if _is_timeline_event(obj):
            events.append(obj)
    return events


def _parse_events_json(content: str) -> list[dict]:
    text = (content or "").strip()
    if not text:
        return []
    try:
        data = json.loads(text)
        if isinstance(data, list):
            return [x for x in data if _is_timeline_event(x)]
    except json.JSONDecodeError:
        pass
    m = _JSON_BLOCK.search(text)
    if m:
        try:
            data = json.loads(m.group(1).strip())
            if isinstance(data, list):
                return [x for x in data if _is_timeline_event(x)]
        except json.JSONDecodeError:
            pass
    m2 = _JSON_ARRAY.search(text)
    if m2:
        try:
            data = json.loads(m2.group(0))
            if isinstance(data, list):
                return [x for x in data if _is_timeline_event(x)]
        except json.JSONDecodeError:
            pass
    # DeepSeek coupe souvent le JSON mid-objet quand max_tokens est trop bas.
    return _salvage_event_objects(text)

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

    # Cap pour rester sous ~30s côté UI tout en couvrant span + fin récente.
    chunks = list(_chunks(msgs, 50))
    if len(chunks) > 8:
        chunks = chunks[:3] + chunks[len(chunks) // 2 : len(chunks) // 2 + 2] + chunks[-3:]

    safe_display_name = redact_for_external_llm(display_name, max_chars=200)
    system = (
        UNTRUSTED_DATA_SYSTEM_RULE
        + "\n"
        + _TIMELINE_SYSTEM_TEMPLATE.format(display_name=safe_display_name)
    )
    sem = asyncio.Semaphore(_TIMELINE_CHUNK_CONCURRENCY)

    async def _extract_chunk(chunk: list[dict]) -> list[dict]:
        formatted = "\n".join(
            [
                f"[{m['date'].strftime('%d/%m/%Y %H:%M')}] "
                f"{'MOI' if m['is_from_me'] else display_name}: "
                f"{((m.get('text') or '') or '')[:200]}"
                for m in chunk
            ]
        )
        safe_messages = wrap_untrusted_data(
            "IMESSAGE_TIMELINE",
            formatted,
            max_chars=12_000,
        )
        async with sem:
            try:
                result = await llm.chat(
                    messages=[{"role": "user", "content": safe_messages}],
                    model=config.DEEPSEEK_FAST_MODEL,
                    system=system,
                    max_tokens=1200,
                    temperature=0.0,
                    use_cache=False,
                )
            except Exception as e:
                logger.warning("[timeline] chunk DeepSeek : %s", e)
                return []
        events = _parse_events_json(result.get("content") or "")
        if not events and (result.get("content") or "").strip() not in ("", "[]"):
            logger.warning(
                "[timeline] parse vide (stop=%s, preview=%r)",
                result.get("stop_reason"),
                (result.get("content") or "")[:180],
            )
        return events

    chunk_results = await asyncio.gather(*[_extract_chunk(c) for c in chunks])
    all_events: list[dict] = [ev for part in chunk_results for ev in part]

    seen: set[tuple[str, str]] = set()
    deduped: list[dict] = []
    for ev in sorted(all_events, key=lambda e: str(e.get("date") or "")):
        k = (str(ev.get("date") or ""), str(ev.get("title") or ""))
        if k in seen:
            continue
        seen.add(k)
        deduped.append(ev)

    return deduped
