"""Conversion des journées locales en bornes UTC pour les requêtes SQLite."""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import config

logger = logging.getLogger(__name__)

SQLITE_UTC_FORMAT = "%Y-%m-%d %H:%M:%S"


def configured_timezone() -> ZoneInfo:
    """Retourne le fuseau IANA configuré, avec repli sûr sur UTC."""
    name = str(getattr(config, "TIMEZONE", "UTC") or "UTC").strip()
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        logger.warning("Fuseau TIMEZONE inconnu (%s) — repli UTC", name)
        return ZoneInfo("UTC")


def local_datetime(now: datetime | None = None) -> datetime:
    """Normalise une date de référence dans le fuseau JARVIS."""
    zone = configured_timezone()
    if now is None:
        return datetime.now(zone)
    if now.tzinfo is None:
        return now.replace(tzinfo=zone)
    return now.astimezone(zone)


def utc_bounds_for_local_dates(
    start_date: date,
    end_date_exclusive: date,
) -> tuple[str, str]:
    """Convertit ``[date locale, date locale)`` en timestamps UTC SQLite.

    Les bornes passent par ``ZoneInfo`` : une journée peut donc durer 23, 24
    ou 25 heures lors des changements d'heure.
    """
    if end_date_exclusive <= start_date:
        raise ValueError("La borne de fin doit être postérieure à la borne de début")

    zone = configured_timezone()
    local_start = datetime.combine(start_date, time.min, tzinfo=zone)
    local_end = datetime.combine(end_date_exclusive, time.min, tzinfo=zone)
    return (
        local_start.astimezone(timezone.utc).strftime(SQLITE_UTC_FORMAT),
        local_end.astimezone(timezone.utc).strftime(SQLITE_UTC_FORMAT),
    )
