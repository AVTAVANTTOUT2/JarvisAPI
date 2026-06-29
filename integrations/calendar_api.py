"""Apple Calendar (Calendar.app) via AppleScript — même esprit que Mail.app.

Aucune OAuth : les calendriers iCloud / Google / autres déjà présents dans l’app native.
Permissions : Automation pour Calendar.app au premier appel osascript.
"""

from __future__ import annotations

import asyncio
import logging
import re
import subprocess
import time
from datetime import datetime, timedelta

from dateutil import parser as date_parser

import config

from ._applescript import escape_applescript_string, run_applescript

logger = logging.getLogger(__name__)

OSASCRIPT_TIMEOUT = 10.0
OSASCRIPT_AVAILABILITY_TIMEOUT = 6.0
OSASCRIPT_LAUNCH_TIMEOUT = 5.0

# Utiliser l'ID de bundle évite les soucis de localisation ("Calendar" vs "Calendrier").
CALENDAR_APP_ID = "com.apple.iCal"

_MONTH_NAMES_AS = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)


class AppleCalendarClient:
    """Lecture et création d’événements via Calendar.app."""

    _FAILURE_COOLDOWN = 120.0  # secondes avant de retenter après un échec

    def __init__(self) -> None:
        self._available: bool | None = None
        self._last_error: str | None = None
        self._last_error_details: str | None = None
        self._last_failed_check: float = 0.0
        logger.info("[Calendar] Init AppleCalendarClient (AppleScript)")

    @staticmethod
    def _escape_as(s: str) -> str:
        return escape_applescript_string(s)

    def _run_applescript(self, script: str, timeout: float = OSASCRIPT_TIMEOUT) -> str | None:
        """Exécute osascript ; une retry si timeout (API historique : stdout ou None)."""
        for attempt in range(2):
            diag = self._run_applescript_detailed(script, timeout=timeout)

            if diag["ok"]:
                return diag["stdout"]

            if diag.get("reason") == "timeout":
                logger.warning("[Calendar] osascript timeout (tentative %s)", attempt + 1)
                if attempt == 0:
                    continue
                return None

            logger.error(
                "[Calendar] osascript erreur (%s) : %s",
                diag.get("returncode"),
                (diag.get("stderr") or "").strip(),
            )
            return None

        return None

    def _run_applescript_detailed(self, script: str, timeout: float) -> dict:
        """Exécute osascript et retourne un diagnostic complet (stdout/stderr/returncode).

        Wrapper compatible avec l'API historique du module (dict avec
        keys `ok`, `reason`, `returncode`, `stdout`, `stderr`).
        """
        result = run_applescript(script, timeout=timeout)
        return {
            "ok": result.ok,
            "reason": result.reason,
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }

    async def _run_applescript_async(self, script: str, timeout: float = OSASCRIPT_TIMEOUT) -> str | None:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, lambda: self._run_applescript(script, timeout=timeout))

    def _launch_calendar(self) -> dict:
        """Force le lancement en arrière-plan de Calendar.app avant les requêtes."""
        diag = self._run_applescript_detailed(
            f'tell application id "{CALENDAR_APP_ID}" to launch',
            timeout=OSASCRIPT_LAUNCH_TIMEOUT,
        )
        if diag.get("ok"):
            return diag

        # Fallback pragmatique : certains contextes retournent -600 sur "launch".
        stderr = (diag.get("stderr") or "").strip()
        if "-600" in stderr or "L’application n’est pas ouverte" in stderr or "Application isn’t running" in stderr:
            try:
                open_res = subprocess.run(
                    ["open", "-gj", "-b", CALENDAR_APP_ID],
                    capture_output=True,
                    text=True,
                    timeout=2.5,
                )
                if open_res.returncode != 0:
                    return {
                        **diag,
                        "reason": "open_failed",
                        "open_returncode": open_res.returncode,
                        "open_stderr": (open_res.stderr or "").strip(),
                        "open_stdout": (open_res.stdout or "").strip(),
                    }
            except Exception as e:
                return {
                    **diag,
                    "reason": "open_exception",
                    "open_exception": f"{type(e).__name__}: {e}",
                }

            # Retenter un launch AppleScript très court après open.
            return self._run_applescript_detailed(
                f'tell application id "{CALENDAR_APP_ID}" to launch',
                timeout=3.0,
            )

        return diag

    def get_status(self) -> dict:
        """Statut structuré (pour /api/integrations)."""
        available = self.is_available()
        return {
            "available": available,
            "error": None if available else (self._last_error or "Calendar.app indisponible"),
            "details": None if available else self._last_error_details,
        }

    def is_available(self) -> bool:
        if self._available is True:
            return True
        # Court-circuit si le dernier échec est récent (évite les retries osascript en boucle)
        if self._available is False and time.monotonic() - self._last_failed_check < self._FAILURE_COOLDOWN:
            return False
        self._last_error = None
        self._last_error_details = None

        # 1) Réveil explicite pour éviter une app totalement éteinte
        launch_diag = self._launch_calendar()
        if not launch_diag.get("ok"):
            stderr = (launch_diag.get("stderr") or "").strip()
            self._last_error_details = stderr or None

            if "Not authorized to send Apple events" in stderr:
                self._last_error = "Permission refusée (Automation)"
                logger.critical(
                    '🚨 PERMISSION REFUSÉE : Va dans Réglages Système > Confidentialité et sécurité > Automatisation, '
                    'et autorise le Terminal/Cursor à contrôler Calendrier.'
                )
            elif launch_diag.get("reason") == "timeout":
                self._last_error = "Timeout au lancement de Calendar.app"
                logger.warning("[Calendar] Timeout au lancement (osascript)")
            else:
                self._last_error = "Échec lancement Calendar.app"
                logger.error(
                    "[Calendar] Échec lancement (rc=%s) stderr=%s stdout=%s",
                    launch_diag.get("returncode"),
                    stderr,
                    (launch_diag.get("stdout") or "").strip(),
                )

            self._available = False
            self._last_failed_check = time.monotonic()
            return False

        # 2) Requête minimale : liste des calendriers (diagnostic complet)
        diag = self._run_applescript_detailed(
            f'tell application id "{CALENDAR_APP_ID}" to return name of every calendar',
            timeout=OSASCRIPT_AVAILABILITY_TIMEOUT,
        )
        if diag.get("ok"):
            self._available = True
            logger.info("[Calendar] Calendar.app accessible")
            return True

        stderr = (diag.get("stderr") or "").strip()
        self._last_error_details = stderr or None

        if "Not authorized to send Apple events" in stderr:
            self._last_error = "Permission refusée (Automation)"
            logger.critical(
                '🚨 PERMISSION REFUSÉE : Va dans Réglages Système > Confidentialité et sécurité > Automatisation, '
                'et autorise le Terminal/Cursor à contrôler Calendrier.'
            )
        elif diag.get("reason") == "timeout":
            self._last_error = "Timeout (osascript) — prompt Automation possible"
            logger.warning(
                "[Calendar] osascript timeout pendant is_available() (Calendar lent à démarrer ou prompt Automation en attente)"
            )
        elif diag.get("reason") in ("not_found", "not_macos"):
            self._last_error = "osascript introuvable"
            logger.error("[Calendar] osascript introuvable (pas macOS ?)")
        else:
            self._last_error = "AppleScript erreur"
            logger.error(
                "[Calendar] osascript erreur (rc=%s) stderr=%s stdout=%s",
                diag.get("returncode"),
                stderr,
                (diag.get("stdout") or "").strip(),
            )

        logger.warning("[Calendar] Calendar.app inaccessible pour le moment")
        self._available = False
        self._last_failed_check = time.monotonic()
        return False

    def _events_script(self, days: int) -> str:
        """Collecte les événements sur `days` jours à partir du début du jour local."""
        return f'''
set today to current date
set todayStart to today - (time of today)
set todayEnd to todayStart + ({days} * days)

tell application id "{CALENDAR_APP_ID}"
    set output to ""
    repeat with cal in calendars
        try
            set evts to (every event of cal whose start date >= todayStart and start date < todayEnd)
            repeat with e in evts
                set output to output & "---EVENT---" & linefeed
                set output to output & "SUMMARY:" & (summary of e) & linefeed
                set output to output & "START:" & (start date of e) & linefeed
                set output to output & "END:" & (end date of e) & linefeed
                try
                    set output to output & "LOCATION:" & (location of e) & linefeed
                on error
                    set output to output & "LOCATION:" & linefeed
                end try
                try
                    set output to output & "NOTES:" & (description of e) & linefeed
                on error
                    set output to output & "NOTES:" & linefeed
                end try
                set output to output & "CALENDAR:" & (name of cal) & linefeed
            end repeat
        end try
    end repeat
    return output
end tell
'''

    def _parse_events_output(self, raw: str) -> list[dict]:
        if not raw or not raw.strip():
            return []
        chunks = [c.strip() for c in raw.split("---EVENT---") if c.strip()]
        events: list[dict] = []
        for chunk in chunks:
            row: dict[str, str] = {}
            for line in chunk.splitlines():
                if ":" in line:
                    k, _, v = line.partition(":")
                    row[k.strip().upper()] = v.strip()
            summary = row.get("SUMMARY", "(sans titre)")
            start_s = row.get("START", "")
            end_s = row.get("END", "")
            events.append(
                {
                    "summary": summary,
                    "start": self._fmt_time(start_s),
                    "end": self._fmt_time(end_s),
                    "location": row.get("LOCATION", ""),
                    "notes": row.get("NOTES", ""),
                    "calendar": row.get("CALENDAR", ""),
                    "start_sort": start_s,
                }
            )

        def sort_key(ev: dict) -> str:
            return ev.get("start_sort") or ""

        events.sort(key=sort_key)
        for ev in events:
            ev.pop("start_sort", None)
        return events

    @staticmethod
    def _fmt_time(s: str) -> str:
        if not s:
            return ""
        try:
            dt = date_parser.parse(s, dayfirst=True, yearfirst=False)
            return dt.strftime("%H:%M")
        except Exception:
            return s[:16]

    def _events_range_script(self, start_dt: datetime, end_dt: datetime) -> str:
        """Collecte les événements entre deux dates absolues (ISO)."""
        start_block = self._build_set_date_block("rangeStart", start_dt)
        end_block = self._build_set_date_block("rangeEnd", end_dt)
        return f'''
{start_block}
{end_block}

tell application id "{CALENDAR_APP_ID}"
    set output to ""
    repeat with cal in calendars
        try
            set evts to (every event of cal whose start date >= rangeStart and start date < rangeEnd)
            repeat with e in evts
                set output to output & "---EVENT---" & linefeed
                set output to output & "SUMMARY:" & (summary of e) & linefeed
                set output to output & "START:" & (start date of e) & linefeed
                set output to output & "END:" & (end date of e) & linefeed
                try
                    set output to output & "LOCATION:" & (location of e) & linefeed
                on error
                    set output to output & "LOCATION:" & linefeed
                end try
                try
                    set output to output & "NOTES:" & (description of e) & linefeed
                on error
                    set output to output & "NOTES:" & linefeed
                end try
                set output to output & "CALENDAR:" & (name of cal) & linefeed
            end repeat
        end try
    end repeat
    return output
end tell
'''

    def _parse_events_output_full(self, raw: str) -> list[dict]:
        """Parse en conservant les datetimes ISO complètes (pas juste HH:MM)."""
        if not raw or not raw.strip():
            return []
        chunks = [c.strip() for c in raw.split("---EVENT---") if c.strip()]
        events: list[dict] = []
        for chunk in chunks:
            row: dict[str, str] = {}
            for line in chunk.splitlines():
                if ":" in line:
                    k, _, v = line.partition(":")
                    row[k.strip().upper()] = v.strip()
            summary = row.get("SUMMARY", "(sans titre)")
            start_s = row.get("START", "")
            end_s = row.get("END", "")
            events.append(
                {
                    "id": f"{summary[:20]}_{start_s}".replace(" ", "_"),
                    "title": summary,
                    "start": self._to_iso(start_s),
                    "end": self._to_iso(end_s),
                    "location": row.get("LOCATION", ""),
                    "notes": row.get("NOTES", ""),
                    "calendar": row.get("CALENDAR", ""),
                }
            )
        events.sort(key=lambda e: e.get("start") or "")
        return events

    @staticmethod
    def _to_iso(s: str) -> str:
        """Convertit une date AppleScript (locale) en ISO 8601."""
        if not s:
            return ""
        try:
            dt = date_parser.parse(s, dayfirst=True, yearfirst=False)
            return dt.isoformat()
        except Exception:
            return s

    async def get_events(self, start_date: str, end_date: str) -> list[dict]:
        """Récupère les événements entre deux dates ISO."""
        if not self.is_available():
            return []
        start_dt = self._parse_user_datetime(start_date)
        end_dt = self._parse_user_datetime(end_date)
        if not start_dt or not end_dt:
            return []
        raw = await self._run_applescript_async(
            self._events_range_script(start_dt, end_dt), timeout=15.0
        )
        if raw is None:
            return []
        return self._parse_events_output_full(raw)

    async def get_today_events(self) -> list[dict]:
        if not self.is_available():
            return []
        raw = await self._run_applescript_async(self._events_script(1))
        if raw is None:
            return []
        return self._parse_events_output(raw)

    async def get_week_events(self) -> list[dict]:
        if not self.is_available():
            return []
        raw = await self._run_applescript_async(self._events_script(7))
        if raw is None:
            return []
        return self._parse_events_output(raw)

    async def get_calendars(self) -> list[str]:
        if not self.is_available():
            return []
        raw = await self._run_applescript_async(
            f'tell application id "{CALENDAR_APP_ID}" to return name of every calendar'
        )
        if not raw:
            return []
        parts = re.split(r",\s*", raw)
        return [p.strip() for p in parts if p.strip()]

    @staticmethod
    def _parse_user_datetime(s: str) -> datetime | None:
        raw = (s or "").strip()
        if not raw:
            return None

        now = datetime.now()
        lower = raw.lower()

        # ISO / formats explicites
        explicit_formats = (
            "%Y-%m-%d %H:%M",
            "%Y-%m-%dT%H:%M",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%dT%H:%M:%S",
            "%d/%m/%Y %H:%M",
            "%d/%m/%Y %H:%M:%S",
        )
        for fmt in explicit_formats:
            try:
                return datetime.strptime(raw, fmt)
            except ValueError:
                continue

        # Heure seule: 14:00 / 14h / 14h30
        hm = re.fullmatch(r"(\d{1,2})\s*(?:h|:)\s*(\d{0,2})", lower)
        if hm:
            h = int(hm.group(1))
            m = int(hm.group(2)) if hm.group(2) else 0
            if 0 <= h <= 23 and 0 <= m <= 59:
                return now.replace(hour=h, minute=m, second=0, microsecond=0)

        # Date seule ISO: 2026-05-08 -> 09:00
        try:
            d = datetime.strptime(raw, "%Y-%m-%d")
            return d.replace(hour=9, minute=0, second=0, microsecond=0)
        except ValueError:
            pass

        def _extract_time(text: str) -> tuple[int, int] | None:
            mt = re.search(r"(\d{1,2})\s*(?:h|:)\s*(\d{0,2})", text)
            if not mt:
                return None
            hh = int(mt.group(1))
            mm = int(mt.group(2)) if mt.group(2) else 0
            if 0 <= hh <= 23 and 0 <= mm <= 59:
                return hh, mm
            return None

        if "demain" in lower:
            base = now + timedelta(days=1)
            t = _extract_time(lower)
            if t:
                return base.replace(hour=t[0], minute=t[1], second=0, microsecond=0)
            return base.replace(hour=9, minute=0, second=0, microsecond=0)

        if "aujourd" in lower:
            t = _extract_time(lower)
            if t:
                return now.replace(hour=t[0], minute=t[1], second=0, microsecond=0)
            return now.replace(second=0, microsecond=0)

        days_map = {
            "lundi": 0,
            "mardi": 1,
            "mercredi": 2,
            "jeudi": 3,
            "vendredi": 4,
            "samedi": 5,
            "dimanche": 6,
            "monday": 0,
            "tuesday": 1,
            "wednesday": 2,
            "thursday": 3,
            "friday": 4,
            "saturday": 5,
            "sunday": 6,
        }
        for day_name, target_wd in days_map.items():
            if day_name in lower:
                days_ahead = (target_wd - now.weekday()) % 7
                if days_ahead == 0:
                    days_ahead = 7
                base = now + timedelta(days=days_ahead)
                t = _extract_time(lower)
                if t:
                    return base.replace(hour=t[0], minute=t[1], second=0, microsecond=0)
                return base.replace(hour=9, minute=0, second=0, microsecond=0)

        try:
            return date_parser.parse(raw, dayfirst=True)
        except Exception:
            return None

    def _build_set_date_block(self, var_name: str, dt: datetime) -> str:
        sm = _MONTH_NAMES_AS[dt.month - 1]
        return f"""set {var_name} to current date
set year of {var_name} to {dt.year}
set month of {var_name} to {sm}
set day of {var_name} to {dt.day}
set hours of {var_name} to {dt.hour}
set minutes of {var_name} to {dt.minute}
set seconds of {var_name} to 0"""

    async def create_event(
        self,
        summary: str,
        start_date: str,
        end_date: str = "",
        calendar_name: str | None = None,
        location: str = "",
        notes: str = "",
    ) -> dict:
        if not self.is_available():
            return {"ok": False, "message": "Calendar.app indisponible"}

        summary = (summary or "").strip() or "Événement"
        start_dt = self._parse_user_datetime(start_date)
        if not start_dt:
            return {
                "ok": False,
                "message": f"Date de début invalide : {start_date!r}. Formats acceptés: YYYY-MM-DD HH:MM, demain 14h, vendredi 10:00, 14:00.",
            }
        end_dt = self._parse_user_datetime(end_date) if (end_date or "").strip() else None
        if end_dt is None:
            end_dt = start_dt + timedelta(hours=1)
        if end_dt <= start_dt:
            end_dt = start_dt + timedelta(hours=1)

        summ = self._escape_as(summary)
        loc = self._escape_as(location)
        note = self._escape_as(notes)
        cal_spec = (calendar_name or "").strip()
        if not cal_spec:
            calendars = await self.get_calendars()
            if not calendars:
                return {"ok": False, "message": "Aucun calendrier disponible"}
            cal_spec = next((c for c in calendars if "icloud" in c.lower()), calendars[0])
        cal_esc = self._escape_as(cal_spec)

        start_block = self._build_set_date_block("startDT", start_dt)
        end_block = self._build_set_date_block("endDT", end_dt)
        cal_pick = f'set targetCal to first calendar whose name is "{cal_esc}"'

        script = f"""
tell application id "{CALENDAR_APP_ID}"
{start_block}
{end_block}
{cal_pick}
set newEvent to make new event at end of events of targetCal with properties {{start date:startDT, end date:endDT, summary:"{summ}", location:"{loc}", description:"{note}"}}
end tell
"""
        out = await self._run_applescript_async(script.strip(), timeout=OSASCRIPT_TIMEOUT)
        if out is None:
            return {"ok": False, "message": "Échec AppleScript (Calendar.app)."}
        logger.info("[Calendar] Événement créé : %s — %s -> %s", summary[:80], start_dt.isoformat(), end_dt.isoformat())
        return {
            "ok": True,
            "summary": summary,
            "start": start_dt.strftime("%Y-%m-%dT%H:%M:%S"),
            "end": end_dt.strftime("%Y-%m-%dT%H:%M:%S"),
            "calendar": cal_spec,
            "message": "Événement créé dans Calendar.",
        }


calendar_client = AppleCalendarClient()
