"""Rafraîchissement borné des sources Apple avant une recherche explicite.

Le retrieval principal reste local et synchrone. Ce module ne contacte Mail ou
Calendar que lorsqu'une requête les nomme, avec singleflight et cache durable.
"""

from __future__ import annotations

import asyncio
import logging
import re
import threading
import time
from datetime import datetime, time as datetime_time, timedelta

from database.core import current_profile_id
from database.email import cache_email_preview, get_recent_emails_from_db
from database.knowledge import (
    upsert_calendar_events,
    update_knowledge_source_state,
)
from database.time_buckets import configured_timezone, sqlite_utc_timestamp

from .models import RetrievalRequest

logger = logging.getLogger(__name__)

_MAIL_TERMS = re.compile(r"\b(mail|mails|e-?mail|emails|courriel|bo[iî]te)\b", re.I)
_CALENDAR_TERMS = re.compile(
    r"\b(agenda|calendrier|planning|rendez-vous|rdv|év[ée]nement|"
    r"aujourd'hui|demain|hier|semaine|horaire)\b",
    re.I,
)
_IMESSAGE_TERMS = re.compile(
    r"\b(imessage|iMessage|sms|texto|textos|messages?|conversation)\b",
    re.I,
)
_COUNT_RE = re.compile(r"\b([1-9]|[1-9][0-9])\b")
_MAIL_TTL_SECONDS = 30.0
_CALENDAR_TTL_SECONDS = 300.0
_IMESSAGE_TTL_SECONDS = 60.0
_lock_guard = threading.Lock()
_locks: dict[tuple[str, str], asyncio.Lock] = {}
_last_refresh: dict[tuple[str, str], float] = {}


async def refresh_live_sources(request: RetrievalRequest) -> dict[str, str]:
    """Rafraîchit uniquement les sources live explicitement demandées."""

    # Les suivis elliptiques (par ex. « et celui de Grégoire ? ») ont besoin
    # des tours récents pour sélectionner la même source live que le tour initial.
    query = request.effective_query
    report: dict[str, str] = {}
    if _MAIL_TERMS.search(query) or "email" in request.source_types:
        report["email"] = await _refresh_mail(query)
    if _CALENDAR_TERMS.search(query) or "calendar" in request.source_types:
        report["calendar"] = await _refresh_calendar(query)
    if _IMESSAGE_TERMS.search(query) or "imessage" in request.source_types:
        report["imessage"] = await _refresh_imessage(query)
    return report


async def _refresh_mail(query: str) -> str:
    cache_key = _profile_source_key("mail")
    source_lock = _source_lock(cache_key)
    if _cache_is_fresh(cache_key, _MAIL_TTL_SECONDS):
        return "cached"
    async with source_lock:
        if _cache_is_fresh(cache_key, _MAIL_TTL_SECONDS):
            return "cached"
        try:
            from integrations import mail_client

            if mail_client is None:
                raise RuntimeError("mail_client_missing")
            requested = _requested_count(query, default=5)
            result = await mail_client.get_recent_result(max(5, requested))
            if result.status != "ok":
                raise RuntimeError(result.error or "mail_unavailable")
            for message in result.messages:
                message_id = str(message.get("id") or "").strip()
                if not message_id:
                    continue
                cache_email_preview(
                    gmail_id=message_id,
                    sender=str(message.get("from") or ""),
                    subject=str(message.get("subject") or "(sans sujet)"),
                    preview=str(message.get("snippet") or ""),
                    received_at=str(message.get("date") or ""),
                    is_read=bool(message.get("is_read")),
                )
            now = sqlite_utc_timestamp()
            update_knowledge_source_state(
                "email_live",
                "email",
                status="ok",
                item_count=len(result.messages),
                last_indexed_at=now,
                error_code=None,
            )
            _last_refresh[cache_key] = time.monotonic()
            return "ok"
        except Exception as exc:
            cached = get_recent_emails_from_db(limit=1)
            status = "degraded" if cached else "unavailable"
            update_knowledge_source_state(
                "email_live",
                "email",
                status=status,
                item_count=len(cached),
                error_code=type(exc).__name__,
            )
            logger.warning("[retrieval] Mail live %s : %s", status, exc)
            return status


async def _refresh_calendar(query: str) -> str:
    cache_key = _profile_source_key("calendar")
    source_lock = _source_lock(cache_key)
    if _cache_is_fresh(cache_key, _CALENDAR_TTL_SECONDS):
        return "cached"
    async with source_lock:
        if _cache_is_fresh(cache_key, _CALENDAR_TTL_SECONDS):
            return "cached"
        try:
            from integrations import calendar_client

            if calendar_client is None or not calendar_client.is_available():
                raise RuntimeError("calendar_unavailable")
            start, end = _calendar_window(query)
            get_result = getattr(calendar_client, "get_events_result", None)
            if callable(get_result):
                result = await get_result(start.isoformat(), end.isoformat())
                if result.status != "ok":
                    raise RuntimeError(result.error or "calendar_unavailable")
                events = list(result.events)
            else:
                events = await calendar_client.get_events(
                    start.isoformat(), end.isoformat()
                )
                if events is None:
                    raise RuntimeError("calendar_no_response")
            upserted = upsert_calendar_events(
                events,
                window_start=start.isoformat(),
                window_end=end.isoformat(),
            )
            now = sqlite_utc_timestamp()
            update_knowledge_source_state(
                "calendar_live",
                "calendar",
                status="ok",
                cursor=end.isoformat(),
                item_count=upserted,
                last_indexed_at=now,
                error_code=None,
            )
            _last_refresh[cache_key] = time.monotonic()
            return "ok"
        except Exception as exc:
            # La projection locale reste utilisable ; le coordinator indiquera
            # sa fraîcheur au modèle.
            update_knowledge_source_state(
                "calendar_live",
                "calendar",
                status="degraded",
                error_code=type(exc).__name__,
            )
            logger.warning("[retrieval] Calendar live degraded : %s", exc)
            return "degraded"


async def _refresh_imessage(query: str) -> str:
    """Rattrape le miroir ``imessage_messages`` avant une question messages."""
    del query  # réserve pour filtres futurs (contact nommé, etc.)
    cache_key = _profile_source_key("imessage")
    source_lock = _source_lock(cache_key)
    if _cache_is_fresh(cache_key, _IMESSAGE_TTL_SECONDS):
        return "cached"
    async with source_lock:
        if _cache_is_fresh(cache_key, _IMESSAGE_TTL_SECONDS):
            return "cached"
        try:
            from integrations.imessage_import import IMessageImporter

            importer = IMessageImporter()
            if not importer.is_available():
                raise RuntimeError("imessage_unavailable")
            result = await asyncio.to_thread(importer.sync_incremental)
            if result.errors == ["sync_already_running"]:
                return "cached"
            if result.errors:
                raise RuntimeError(result.errors[0])
            now = sqlite_utc_timestamp()
            update_knowledge_source_state(
                "imessage_live",
                "imessage",
                status="ok",
                item_count=int(result.total_messages or 0),
                last_indexed_at=now,
                error_code=None,
            )
            _last_refresh[cache_key] = time.monotonic()
            return "ok"
        except Exception as exc:
            update_knowledge_source_state(
                "imessage_live",
                "imessage",
                status="degraded",
                error_code=type(exc).__name__,
            )
            logger.warning("[retrieval] iMessage live degraded : %s", exc)
            return "degraded"


def _calendar_window(query: str) -> tuple[datetime, datetime]:
    tz = configured_timezone()
    now = datetime.now(tz)
    folded = query.casefold()
    target = now.date()
    if "hier" in folded:
        target -= timedelta(days=1)
    elif "demain" in folded:
        target += timedelta(days=1)
    if "semaine" in folded:
        start_date = target - timedelta(days=target.weekday())
        end_date = start_date + timedelta(days=7)
    else:
        start_date = target
        end_date = target + timedelta(days=1)
    return (
        datetime.combine(start_date, datetime_time.min, tzinfo=tz),
        datetime.combine(end_date, datetime_time.min, tzinfo=tz),
    )


def _requested_count(query: str, *, default: int) -> int:
    match = _COUNT_RE.search(query)
    return max(1, min(20, int(match.group(1)))) if match else default


def _profile_source_key(source: str) -> tuple[str, str]:
    return current_profile_id(), source


def _source_lock(cache_key: tuple[str, str]) -> asyncio.Lock:
    with _lock_guard:
        return _locks.setdefault(cache_key, asyncio.Lock())


def _cache_is_fresh(cache_key: tuple[str, str], ttl: float) -> bool:
    refreshed = _last_refresh.get(cache_key)
    return refreshed is not None and time.monotonic() - refreshed < ttl


def reset_live_source_cache_for_tests() -> None:
    """Réinitialise uniquement les TTL, jamais les données persistées."""

    with _lock_guard:
        _last_refresh.clear()
        _locks.clear()
