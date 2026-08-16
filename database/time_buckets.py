"""Conversion des journées locales en bornes UTC pour les requêtes SQLite."""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import config

logger = logging.getLogger(__name__)

SQLITE_UTC_FORMAT = "%Y-%m-%d %H:%M:%S"
_FRENCH_MONTHS = {
    "janvier": 1,
    "février": 2,
    "fevrier": 2,
    "mars": 3,
    "avril": 4,
    "mai": 5,
    "juin": 6,
    "juillet": 7,
    "août": 8,
    "aout": 8,
    "septembre": 9,
    "octobre": 10,
    "novembre": 11,
    "décembre": 12,
    "decembre": 12,
}
_FRENCH_MAIL_DATE_RE = re.compile(
    r"^(?:(?:lundi|mardi|mercredi|jeudi|vendredi|samedi|dimanche)\s+)?"
    r"(\d{1,2})\s+([a-zéû]+)\s+(\d{4})\s+(?:à\s+)?"
    r"(\d{1,2}):(\d{2}):(\d{2})$",
    re.IGNORECASE,
)


def _parse_datetime_text(value: str) -> datetime:
    raw = value.strip()
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        match = _FRENCH_MAIL_DATE_RE.fullmatch(raw.casefold())
        if match is None:
            raise
    day, month_name, year, hour, minute, second = match.groups()
    month = _FRENCH_MONTHS.get(month_name)
    if month is None:
        raise ValueError("unsupported_localized_month")
    return datetime(
        int(year),
        month,
        int(day),
        int(hour),
        int(minute),
        int(second),
    )


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


def utc_datetime(value: datetime | str | None = None) -> datetime:
    """Normalise un instant en UTC timezone-aware.

    Les valeurs naïves sont interprétées dans ``config.TIMEZONE``. Ce choix
    conserve le contrat des saisies historiques (heure civile locale) tout en
    garantissant que toute nouvelle persistance peut utiliser un format UTC
    unique.
    """
    if value is None:
        parsed = datetime.now(timezone.utc)
    elif isinstance(value, str):
        parsed = _parse_datetime_text(value)
    else:
        parsed = value
    if parsed.tzinfo is None:
        parsed = local_datetime(parsed)
    return parsed.astimezone(timezone.utc)


def sqlite_utc_timestamp(value: datetime | str | None = None) -> str:
    """Sérialise un instant au format UTC naïf canonique de SQLite."""
    return utc_datetime(value).strftime(SQLITE_UTC_FORMAT)


def sqlite_utc_datetime(value: str | datetime) -> datetime:
    """Interprète une valeur persistée SQLite comme un instant UTC.

    À la différence de :func:`utc_datetime`, une valeur naïve est déjà
    canonicalisée en UTC et ne doit surtout pas être réinterprétée comme une
    saisie civile locale.
    """
    parsed = datetime.fromisoformat(value) if isinstance(value, str) else value
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


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


def utc_bounds_for_local_day(value: date | str) -> tuple[str, str]:
    """Retourne les bornes UTC exclusives d'une journée civile locale."""
    local_day = date.fromisoformat(value) if isinstance(value, str) else value
    return utc_bounds_for_local_dates(
        local_day,
        local_day + timedelta(days=1),
    )


def sqlite_utc_to_local(value: str | datetime) -> datetime:
    """Convertit un timestamp SQLite UTC en datetime timezone-aware locale."""
    return sqlite_utc_datetime(value).astimezone(configured_timezone())
