"""Client Apple Mail via AppleScript (macOS uniquement).

Remplace l'ancienne intégration Gmail OAuth2. Aucune configuration nécessaire :
il suffit que Mail.app soit configuré avec un compte email (Gmail, iCloud, etc.)
et que Terminal ait la permission Automation pour Mail.app.

Toutes les opérations passent par `osascript` (subprocess bloquant) wrappé dans
`asyncio.run_in_executor()` pour rester non-bloquant côté serveur.

Permissions macOS requises :
  - Automation pour Mail.app (demandée par macOS au 1er appel osascript)
"""

import asyncio
import logging
import re
import time
from dataclasses import dataclass
from typing import Literal

from ._applescript import escape_applescript_string, run_applescript

logger = logging.getLogger(__name__)

OSASCRIPT_TIMEOUT = 30.0
OSASCRIPT_TIMEOUT_LONG = 60.0
# `is_available` active Mail puis interroge les comptes (premier lancement lent).
MAIL_IS_AVAILABLE_TIMEOUT = 90.0
_FAILURE_COOLDOWN = 120.0  # secondes avant de retenter après un échec

# Disjoncteur sur timeouts consécutifs.
#
# Un `osascript` qui expire vraiment (Mail figé) ne doit pas être martelé :
# chaque essai immobilise un thread. Mais une fenêtre de 10 min coupait le
# watcher H24 pour de simples ralentissements Mail (sync, gros inbox).
# Seuil un peu plus haut + cooldown court : on protège le CPU sans arrêt
# de service prolongé. À la reprise, `reset_availability_cache` rouvre aussi.
_TIMEOUT_BREAKER_THRESHOLD = 3
_TIMEOUT_BREAKER_COOLDOWN = 90.0  # 1,5 min — assez pour Mail, pas une coupure H24
PREVIEW_MAX_CHARS = 1000
BODY_MAX_CHARS = 3000
HYDRATION_BODY_MAX_CHARS = 1_000_000
MSG_SEPARATOR = "---MSG---"
MAIL_DATE_HANDLERS = """on jarvisPad2(value)
    set rendered to value as string
    if (count of rendered) is 1 then set rendered to "0" & rendered
    return rendered
end jarvisPad2

on jarvisIsoDate(value)
    set yearText to ((year of value) as integer) as string
    set monthNumber to (month of value) as integer
    return yearText & "-" & my jarvisPad2(monthNumber) & "-" & my jarvisPad2(day of value) & "T" & my jarvisPad2(hours of value) & ":" & my jarvisPad2(minutes of value) & ":" & my jarvisPad2(seconds of value)
end jarvisIsoDate"""


@dataclass(frozen=True, slots=True)
class MailQueryResult:
    """Résultat typé : une panne ne peut plus ressembler à une boîte vide."""

    status: Literal["ok", "unavailable"]
    messages: tuple[dict, ...] = ()
    error: str | None = None
    next_offset: int | None = None
    has_more: bool = False
    complete: bool = True


class AppleMailClient:
    """Lecture/envoi d'emails via Mail.app + AppleScript."""

    def __init__(self):
        self._available: bool | None = None
        self._last_failed_check: float = 0.0
        self._consecutive_timeouts: int = 0
        self._breaker_open_until: float = 0.0
        logger.info("[Mail] Init AppleMailClient (AppleScript)")

    # ── Helpers ────────────────────────────────────────────────

    @staticmethod
    def _escape(text: str) -> str:
        """Échappe les caractères spéciaux pour injection dans une string AppleScript."""
        return escape_applescript_string(text)

    def _run_applescript(
        self,
        script: str,
        timeout: float | None = None,
        *,
        attempts: int = 2,
    ) -> str | None:
        """Exécute un AppleScript via osascript. Retourne stdout ou None.

        Une seule reprise sur timeout, et un disjoncteur au-delà : Mail.app qui
        n'a pas répondu en 30 s ne répondra pas davantage à la tentative
        suivante, et chaque essai immobilise un thread pendant tout le délai.
        """
        now = time.time()
        if now < self._breaker_open_until:
            return None

        eff_timeout = timeout or OSASCRIPT_TIMEOUT
        max_attempts = max(1, min(int(attempts or 1), 2))
        for attempt in range(max_attempts):
            result = run_applescript(script, timeout=eff_timeout)
            if result.ok:
                if self._consecutive_timeouts:
                    logger.info(
                        "[Mail] osascript répond de nouveau après %d timeout(s)",
                        self._consecutive_timeouts,
                    )
                self._consecutive_timeouts = 0
                return result.stdout

            if result.reason == "timeout":
                self._consecutive_timeouts += 1
                if self._consecutive_timeouts >= _TIMEOUT_BREAKER_THRESHOLD:
                    self._breaker_open_until = time.time() + _TIMEOUT_BREAKER_COOLDOWN
                    self._available = None  # forcera un vrai test à la reprise
                    logger.error(
                        "[Mail] %d timeouts consécutifs — Mail.app ne répond pas. "
                        "Appels suspendus %.0f min. Vérifiez que Mail.app est "
                        "lancé et que l'autorisation Automation est accordée.",
                        self._consecutive_timeouts,
                        _TIMEOUT_BREAKER_COOLDOWN / 60,
                    )
                    return None
                logger.warning(
                    "[Mail] osascript timeout (tentative %s/%s, timeout=%.0fs)",
                    attempt + 1,
                    max_attempts,
                    eff_timeout,
                )
                if attempt + 1 < max_attempts:
                    continue
                return None
            if result.reason == "not_found":
                logger.error("[Mail] osascript introuvable — pas un macOS ?")
                return None

            logger.error(
                "[Mail] osascript erreur (rc=%s) stderr=%s",
                result.returncode,
                result.stderr,
            )
            if result.is_permission_denied():
                logger.critical(
                    "[Mail] PERMISSION REFUSEE : Reglages Systeme > Confidentialite et securite "
                    "> Automatisation > autorise Terminal/Cursor a controler Mail."
                )
            elif result.is_app_not_running():
                logger.error(
                    "[Mail] Mail.app ne semble pas tourner — tentative de lancement..."
                )
            return None
        return None

    # ── État ───────────────────────────────────────────────────

    def reset_availability_cache(self) -> None:
        """Oblige le prochain `is_available()` à re-tester Mail.app.

        Utile après un timeout, un prompt Automation résolu, ou avant un rattrapage manuel.
        """
        self._available = None
        self._last_failed_check = 0.0
        self._consecutive_timeouts = 0
        self._breaker_open_until = 0.0

    def is_available(self) -> bool:
        """Vérifie que Mail.app est accessible via AppleScript.

        Cache le résultat positif indéfiniment et l'échec pendant _FAILURE_COOLDOWN
        secondes pour éviter des retries osascript coûteux en boucle.
        """
        if self._available is True:
            return True
        if (
            self._available is False
            and time.monotonic() - self._last_failed_check < _FAILURE_COOLDOWN
        ):
            return False

        logger.info("[Mail] Verification disponibilite Mail.app...")

        # Active Mail avant la requête (réduit -600 / premier lancement très lent).
        probe_script = (
            'tell application "Mail" to activate\n'
            "delay 0.4\n"
            'tell application "Mail" to return name of every account'
        )
        result = run_applescript(probe_script, timeout=MAIL_IS_AVAILABLE_TIMEOUT)

        if result.ok:
            self._available = True
            logger.info("[Mail] Mail.app accessible")
            return True

        self._available = False
        self._last_failed_check = time.monotonic()

        if result.reason == "timeout":
            logger.error(
                "[Mail] TIMEOUT (%.0fs) — Mail.app met trop longtemps a repondre. "
                "Verifiez qu'un prompt Automation n'est pas en attente.",
                MAIL_IS_AVAILABLE_TIMEOUT,
            )
            return False
        if result.reason == "not_found":
            logger.error("[Mail] osascript introuvable — pas un macOS ?")
            return False

        if result.is_permission_denied():
            logger.critical(
                "[Mail] PERMISSION REFUSEE : Reglages Systeme > Confidentialite et securite "
                "> Automatisation > autorise Terminal/Cursor a controler Mail."
            )
        elif result.is_app_not_running():
            logger.error(
                "[Mail] Mail.app ne tourne pas (erreur -600). "
                "Lancez Mail.app manuellement ou ajoutez-le aux elements d'ouverture."
            )
        else:
            logger.error(
                "[Mail] Echec AppleScript (rc=%s) stdout=%s stderr=%s",
                result.returncode,
                result.stdout[:200],
                result.stderr[:500],
            )

        logger.warning(
            "[Mail] Mail.app INACTIF — retry dans %ds", int(_FAILURE_COOLDOWN)
        )
        return False

    # ── Lecture ─────────────────────────────────────────────────

    async def get_unread(self, max_results: int = 20) -> list[dict]:
        """Liste les emails non lus de la boîte de réception.

        N'utilise PAS `whose read status is false` (cause un full-scan lent
        sur les grosses boîtes). Boucle manuellement sur les messages les
        plus récents et skip ceux qui sont lus. Le contenu (snippet) est
        limité à PREVIEW_MAX_CHARS pour ne pas exploser les tokens.
        """
        scan_limit = max_results * 5
        script = f'''{MAIL_DATE_HANDLERS}
tell application "Mail"
    set output to ""
    set collected to 0
    set maxCount to {max_results}
    set allMsgs to messages of inbox
    set scanLimit to {scan_limit}
    if (count of allMsgs) < scanLimit then set scanLimit to (count of allMsgs)
    repeat with i from 1 to scanLimit
        if collected >= maxCount then exit repeat
        set m to item i of allMsgs
        if read status of m is false then
            set output to output & "{MSG_SEPARATOR}" & linefeed
            set output to output & "ID:" & (id of m as string) & linefeed
            set output to output & "FROM:" & (sender of m) & linefeed
            set output to output & "SUBJECT:" & (subject of m) & linefeed
            set output to output & "DATE:" & my jarvisIsoDate(date received of m) & linefeed
            set rawContent to ""
            try
                set rawContent to content of m
            end try
            if length of rawContent > {PREVIEW_MAX_CHARS} then
                set rawContent to text 1 thru {PREVIEW_MAX_CHARS} of rawContent
            end if
            set output to output & "PREVIEW:" & rawContent & linefeed
            set collected to collected + 1
        end if
    end repeat
    return output
end tell'''

        loop = asyncio.get_event_loop()
        try:
            raw = await loop.run_in_executor(
                None,
                lambda: self._run_applescript(
                    script,
                    timeout=OSASCRIPT_TIMEOUT,
                    attempts=2,
                ),
            )
        except Exception as e:
            logger.error(f"[Mail] get_unread : {e}")
            return []

        if not raw:
            return []

        return self._parse_message_list(raw)

    async def get_recent_result(
        self,
        max_results: int = 20,
        *,
        include_preview: bool = True,
        offset: int = 0,
    ) -> MailQueryResult:
        """Interroge les derniers messages sans confondre vide et indisponible.

        Cette vue sert de source canonique aux demandes « derniers mails » et
        au cache local. Elle reste bornée. ``include_preview=False`` ne lit que
        les métadonnées (watcher H24) — charger le corps de 20 mails via
        AppleScript était la cause principale des timeouts 8 s.
        """
        bounded = max(1, min(int(max_results or 20), 100))
        page_offset = max(0, int(offset or 0))
        if include_preview:
            preview_block = f"""
        set rawContent to ""
        try
            set rawContent to content of m
        end try
        if length of rawContent > {PREVIEW_MAX_CHARS} then
            set rawContent to text 1 thru {PREVIEW_MAX_CHARS} of rawContent
        end if
        set output to output & "PREVIEW:" & rawContent & linefeed"""
            # Aperçus = lent ; budget long + 2 essais.
            fetch_timeout = OSASCRIPT_TIMEOUT_LONG
            fetch_attempts = 2
        else:
            preview_block = '\n        set output to output & "PREVIEW:" & linefeed'
            # Métadonnées seules : Mail répond en général en quelques secondes.
            fetch_timeout = OSASCRIPT_TIMEOUT
            fetch_attempts = 2
        script = f'''{MAIL_DATE_HANDLERS}
tell application "Mail"
    set output to ""
    set allMsgs to messages of inbox
    set totalCount to count of allMsgs
    set firstIndex to {page_offset + 1}
    set lastIndex to firstIndex + {bounded - 1}
    if lastIndex > totalCount then set lastIndex to totalCount
    if firstIndex <= lastIndex then
        repeat with i from firstIndex to lastIndex
            set m to item i of allMsgs
            set msgAccount to ""
            set msgMailbox to ""
            try
                set msgMailbox to name of mailbox of m
                set msgAccount to name of account of mailbox of m
            end try
            set output to output & "{MSG_SEPARATOR}" & linefeed
            set output to output & "ID:" & (id of m as string) & linefeed
            set output to output & "ACCOUNT:" & msgAccount & linefeed
            set output to output & "MAILBOX:" & msgMailbox & linefeed
            set output to output & "FROM:" & (sender of m) & linefeed
            set output to output & "SUBJECT:" & (subject of m) & linefeed
            set output to output & "DATE:" & my jarvisIsoDate(date received of m) & linefeed
            set output to output & "READ:" & (read status of m as string) & linefeed{preview_block}
        end repeat
    end if
    return "TOTAL:" & (totalCount as string) & linefeed & output
end tell'''

        loop = asyncio.get_event_loop()
        try:
            raw = await loop.run_in_executor(
                None,
                lambda t=fetch_timeout, a=fetch_attempts: self._run_applescript(
                    script,
                    timeout=t,
                    attempts=a,
                ),
            )
        except Exception as exc:
            logger.error("[Mail] get_recent : %s", exc)
            return MailQueryResult(
                status="unavailable",
                error=type(exc).__name__,
            )
        if raw is None:
            return MailQueryResult(status="unavailable", error="mail_no_response")
        messages = tuple(self._parse_message_list(raw))
        total_match = re.search(r"(?:^|\n)TOTAL:(\d+)", raw)
        total_count = (
            int(total_match.group(1)) if total_match else page_offset + len(messages)
        )
        next_offset = page_offset + len(messages)
        has_more = next_offset < total_count
        return MailQueryResult(
            status="ok",
            messages=messages,
            next_offset=next_offset if has_more else None,
            has_more=has_more,
            complete=not has_more,
        )

    async def get_recent_page_result(
        self,
        max_results: int = 20,
        *,
        offset: int = 0,
        include_preview: bool = True,
    ) -> MailQueryResult:
        """Page bornée des messages, avec curseur explicite et état complet."""

        return await self.get_recent_result(
            max_results,
            include_preview=include_preview,
            offset=offset,
        )

    async def get_recent(self, max_results: int = 20) -> list[dict]:
        """API historique : retourne les messages, avec statut disponible à part."""

        result = await self.get_recent_result(max_results)
        return list(result.messages)

    async def get_message(
        self,
        msg_id: str,
        *,
        max_body_chars: int = BODY_MAX_CHARS,
    ) -> dict | None:
        """Récupère un message par ID avec une borne explicite sur le corps."""
        body_limit = max(1, min(int(max_body_chars), HYDRATION_BODY_MAX_CHARS))
        if not str(msg_id).isdigit():
            logger.warning("[Mail] get_message refuse un identifiant non numérique")
            return None
        safe_id = int(msg_id)
        script = f"""{MAIL_DATE_HANDLERS}
tell application "Mail"
    set m to first message of inbox whose id is {safe_id}
    set msgAccount to ""
    set msgMailbox to ""
    try
        set msgMailbox to name of mailbox of m
        set msgAccount to name of account of mailbox of m
    end try
    set msgFrom to sender of m
    set msgTo to ""
    try
        set msgTo to address of to recipient 1 of m
    end try
    set msgSubject to subject of m
    set msgDate to my jarvisIsoDate(date received of m)
    set msgRead to read status of m as string
    set msgBody to ""
    set msgBodyLength to 0
    set msgBodyTruncated to false
    try
        set msgBody to content of m as string
        set msgBodyLength to count characters of msgBody
        if msgBodyLength > {body_limit} then
            set msgBody to text 1 thru {body_limit} of msgBody
            set msgBodyTruncated to true
        end if
    end try
    return "ACCOUNT:" & msgAccount & linefeed & "MAILBOX:" & msgMailbox & linefeed & "FROM:" & msgFrom & linefeed & "TO:" & msgTo & linefeed & "SUBJECT:" & msgSubject & linefeed & "DATE:" & msgDate & linefeed & "READ:" & msgRead & linefeed & "BODY_CHARS:" & msgBodyLength & linefeed & "BODY_TRUNCATED:" & (msgBodyTruncated as string) & linefeed & "BODY:" & msgBody
end tell"""

        loop = asyncio.get_event_loop()
        try:
            raw = await loop.run_in_executor(None, self._run_applescript, script)
        except Exception as e:
            logger.error(f"[Mail] get_message({msg_id}) : {e}")
            return None

        if not raw:
            return None

        return self._parse_single_message(raw, msg_id, max_body_chars=body_limit)

    async def get_unread_ids(self, max_results: int = 100) -> list[str]:
        """Retourne uniquement les IDs des mails non lus (rapide, sans contenu).

        Utilise `unread count` d'abord pour court-circuiter si = 0.
        Boucle directement sur les messages (sans `whose` qui force un scan
        complet et timeout sur les grosses boîtes).
        """
        script = f"""tell application "Mail"
    set output to ""
    set maxCount to {max_results}
    set allMsgs to messages of inbox
    set collected to 0
    repeat with m in allMsgs
        if collected >= maxCount then exit repeat
        if read status of m is false then
            set output to output & (id of m as string) & linefeed
            set collected to collected + 1
        end if
    end repeat
    return output
end tell"""
        loop = asyncio.get_event_loop()
        try:
            raw = await loop.run_in_executor(
                None, self._run_applescript, script, OSASCRIPT_TIMEOUT_LONG
            )
        except Exception as e:
            logger.error(f"[Mail] get_unread_ids : {e}")
            return []

        if not raw:
            return []

        return [line.strip() for line in raw.splitlines() if line.strip()]

    async def get_unread_count(self) -> int:
        """Nombre de mails non lus (appel rapide)."""
        script = """tell application "Mail"
    return (count of (messages of inbox whose read status is false))
end tell"""
        loop = asyncio.get_event_loop()
        try:
            raw = await loop.run_in_executor(None, self._run_applescript, script)
        except Exception as e:
            logger.error(f"[Mail] get_unread_count : {e}")
            return 0

        try:
            return int(raw) if raw else 0
        except ValueError:
            return 0

    # ── Actions ─────────────────────────────────────────────────

    async def mark_read(self, msg_id: str) -> bool:
        """Marque un message comme lu."""
        safe_id = int(msg_id) if str(msg_id).isdigit() else msg_id
        script = f"""tell application "Mail"
    set read status of (first message of inbox whose id is {safe_id}) to true
end tell"""
        loop = asyncio.get_event_loop()
        try:
            result = await loop.run_in_executor(None, self._run_applescript, script)
            return result is not None
        except Exception as e:
            logger.error(f"[Mail] mark_read({msg_id}) : {e}")
            return False

    async def flag_message(self, msg_id: str) -> bool:
        """Pose un drapeau (flag rouge) sur un message — mails urgents."""
        safe_id = int(msg_id) if str(msg_id).isdigit() else msg_id
        script = f"""tell application "Mail"
    set flagged status of (first message of inbox whose id is {safe_id}) to true
end tell"""
        loop = asyncio.get_event_loop()
        try:
            result = await loop.run_in_executor(None, self._run_applescript, script)
            return result is not None
        except Exception as e:
            logger.error(f"[Mail] flag_message({msg_id}) : {e}")
            return False

    async def send(self, to: str, subject: str, body: str) -> dict | None:
        """Envoie un email via Mail.app. Retourne {status} ou None."""
        escaped_subject = self._escape(subject)
        escaped_body = self._escape(body)
        escaped_to = self._escape(to)

        script = f'''tell application "Mail"
    set newMsg to make new outgoing message with properties {{subject:"{escaped_subject}", content:"{escaped_body}", visible:false}}
    tell newMsg
        make new to recipient at end of to recipients with properties {{address:"{escaped_to}"}}
    end tell
    send newMsg
end tell'''

        loop = asyncio.get_event_loop()
        try:
            result = await loop.run_in_executor(None, self._run_applescript, script)
            if result is not None:
                logger.info(f"[Mail] Email envoyé à {to}")
                return {"status": "sent"}
            return None
        except Exception as e:
            logger.error(f"[Mail] send({to}) : {e}")
            return None

    # ── Parsing ─────────────────────────────────────────────────

    @staticmethod
    def _extract_field(block: str, field: str) -> str:
        """Extrait la valeur d'un champ 'FIELD:valeur' dans un bloc texte."""
        pattern = re.compile(rf"^{re.escape(field)}:(.*)", re.MULTILINE)
        m = pattern.search(block)
        return m.group(1).strip() if m else ""

    def _parse_message_list(self, raw: str) -> list[dict]:
        """Parse le stdout de get_unread en liste de dicts."""
        blocks = raw.split(MSG_SEPARATOR)
        messages = []
        for block in blocks:
            block = block.strip()
            if not block:
                continue
            msg_id = self._extract_field(block, "ID")
            if not msg_id:
                continue
            preview = self._extract_field(block, "PREVIEW")
            messages.append(
                {
                    "id": msg_id,
                    "account_id": self._extract_field(block, "ACCOUNT"),
                    "mailbox_id": self._extract_field(block, "MAILBOX"),
                    "from": self._extract_field(block, "FROM"),
                    "subject": self._extract_field(block, "SUBJECT"),
                    "date": self._extract_field(block, "DATE"),
                    "is_read": self._extract_field(block, "READ").lower()
                    in {"true", "yes", "1"},
                    "snippet": preview[:PREVIEW_MAX_CHARS],
                }
            )
        return messages

    def _parse_single_message(
        self,
        raw: str,
        msg_id: str,
        *,
        max_body_chars: int = BODY_MAX_CHARS,
    ) -> dict:
        """Parse le stdout de get_message en dict."""
        body_match = re.search(r"^BODY:(.*)", raw, re.MULTILINE | re.DOTALL)
        body = body_match.group(1).strip() if body_match else ""
        declared_count = self._extract_field(raw, "BODY_CHARS")
        try:
            body_char_count = max(len(body), int(declared_count))
        except (TypeError, ValueError):
            body_char_count = len(body)
        body_limit = max(1, min(int(max_body_chars), HYDRATION_BODY_MAX_CHARS))
        declared_truncated = self._extract_field(raw, "BODY_TRUNCATED").lower() in {
            "true",
            "yes",
            "1",
        }
        body_truncated = declared_truncated or body_char_count > body_limit
        if len(body) > body_limit:
            body = body[:body_limit] + "\n[…tronqué…]"
        elif body_truncated and not body.endswith("[…tronqué…]"):
            body += "\n[…tronqué…]"

        return {
            "id": msg_id,
            "account_id": self._extract_field(raw, "ACCOUNT"),
            "mailbox_id": self._extract_field(raw, "MAILBOX"),
            "from": self._extract_field(raw, "FROM"),
            "to": self._extract_field(raw, "TO"),
            "subject": self._extract_field(raw, "SUBJECT"),
            "date": self._extract_field(raw, "DATE"),
            "is_read": self._extract_field(raw, "READ").lower() in {"true", "yes", "1"},
            "body": body,
            "body_char_count": body_char_count,
            "body_truncated": body_truncated,
        }


mail_client = AppleMailClient()
