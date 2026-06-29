"""Bridge iMessage (macOS uniquement).

Principe :
  - Lecture en READONLY de ~/Library/Messages/chat.db (table `message`)
  - Polling toutes les N secondes pour détecter les nouveaux messages reçus
  - Envoi des réponses via `osascript` qui pilote Messages.app

Permissions macOS requises :
  - Full Disk Access pour Terminal/iTerm (sinon SQLite refuse de lire chat.db)
  - Automation pour Messages.app (demandée au 1er envoi)

Sécurité : on ne traite QUE les messages venant de `IMESSAGE_TARGET` (ton propre
numéro ou email iMessage). Les messages des autres contacts sont ignorés.
"""

import asyncio
import logging
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import config

from ._applescript import escape_applescript_string, run_applescript

logger = logging.getLogger(__name__)

MESSAGE_CHUNK_SIZE = 2000  # limite raisonnable iMessage avant split
OSASCRIPT_TIMEOUT = 30.0   # secondes (aligné Mail / Calendar)


class IMessageBridge:
    """Polling + envoi iMessage.

    Usage :
        bridge = IMessageBridge("+33612345678")
        if bridge.is_available():
            asyncio.create_task(bridge.start_polling(interval=3.0))
        # …
        bridge.stop()
    """

    def __init__(self, target_address: str):
        self.target = (target_address or "").strip()
        self.db_path = Path.home() / "Library" / "Messages" / "chat.db"
        self.last_check_rowid: int = 0
        self.running: bool = False
        self.processed_rowids: set[int] = set()
        self._processed_rowids_max = 5000
        # Mémoire des dernières réponses envoyées par JARVIS (anti-écho).
        # Si l'utilisateur renvoie EXACTEMENT le texte d'une réponse récente
        # (cas où Messages.app re-déclenche une notif, ou pour tester), on skip.
        self._recent_outgoing: list[str] = []
        self._recent_outgoing_max = 10
        logger.info(
            f"[iMessage] Init bridge — target={self.target or '(non configuré)'} | "
            f"db={self.db_path}"
        )

    def _remember_outgoing(self, text: str) -> None:
        """Garde une trace des N dernières réponses envoyées (anti-écho)."""
        normalized = (text or "").strip()
        if not normalized:
            return
        self._recent_outgoing.append(normalized)
        if len(self._recent_outgoing) > self._recent_outgoing_max:
            self._recent_outgoing = self._recent_outgoing[-self._recent_outgoing_max:]

    def _is_echo(self, text: str) -> bool:
        """True si le texte reçu correspond à une réponse récemment envoyée."""
        normalized = (text or "").strip()
        if not normalized:
            return False
        return normalized in self._recent_outgoing

    # ── État ─────────────────────────────────────────────────

    def is_available(self) -> bool:
        """Vérifie l'accès à chat.db. Retourne False si pas configuré ou pas accessible."""
        if not self.target:
            logger.info("[iMessage] Bridge desactive — IMESSAGE_TARGET non configure dans .env")
            return False
        if not self.db_path.exists():
            logger.error(
                "[iMessage] chat.db INTROUVABLE : %s — "
                "Verifiez que Messages.app a ete ouvert au moins une fois sur ce Mac.",
                self.db_path,
            )
            return False
        try:
            conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True, timeout=2.0)
            count = conn.execute("SELECT COUNT(*) FROM message").fetchone()[0]
            conn.close()
            logger.info("[iMessage] chat.db accessible (%d messages)", count)
            return True
        except sqlite3.OperationalError as e:
            err = str(e).lower()
            if "unable to open" in err or "authorization" in err or "permission" in err:
                logger.critical(
                    "[iMessage] ACCES REFUSE a chat.db (%s) — "
                    "Reglages Systeme > Confidentialite et securite > Acces complet au disque "
                    "> ajoutez Terminal / Cursor / l'app qui lance JARVIS.",
                    e,
                )
            else:
                logger.error("[iMessage] OperationalError inattendue : %s", e)
            return False
        except sqlite3.DatabaseError as e:
            logger.error("[iMessage] DatabaseError sur chat.db : %s", e)
            return False
        except Exception as e:
            logger.error("[iMessage] Erreur inattendue acces chat.db : %s (%s)", e, type(e).__name__)
            return False

    # ── Lecture chat.db ──────────────────────────────────────

    def _max_rowid(self) -> int:
        """Récupère le ROWID max actuel de la table message (pour init)."""
        try:
            conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True, timeout=2.0)
            row = conn.execute("SELECT COALESCE(MAX(ROWID), 0) FROM message").fetchone()
            conn.close()
            return int(row[0]) if row else 0
        except sqlite3.Error as e:
            logger.error(f"[iMessage] _max_rowid : {e}")
            return 0

    def _get_new_messages(self) -> list[dict]:
        """Lit les messages reçus depuis `self.target` non encore traités.

        On filtre côté SQL pour ne JAMAIS prendre en compte :
          - `is_from_me = 0` → messages reçus uniquement (CRITIQUE :
            sans ce filtre, JARVIS retraiterait ses propres réponses → boucle)
          - `text IS NOT NULL` → skip réactions, fichiers attachés sans texte
          - `h.id = self.target` → skip tous les autres contacts
        """
        try:
            conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True, timeout=2.0)
            cur = conn.execute(
                """
                SELECT m.ROWID, m.text, m.date, m.is_from_me, h.id AS handle_id
                FROM message m
                LEFT JOIN handle h ON m.handle_id = h.ROWID
                WHERE m.ROWID > ?
                  AND m.is_from_me = 0
                  AND m.text IS NOT NULL
                  AND m.text != ''
                  AND h.id = ?
                ORDER BY m.ROWID ASC
                """,
                (self.last_check_rowid, self.target),
            )
            rows = cur.fetchall()
            conn.close()
        except sqlite3.Error as e:
            logger.error(f"[iMessage] _get_new_messages : {e}")
            return []

        messages = []
        for rowid, text, date_int, is_from_me, handle in rows:
            messages.append({
                "rowid": rowid,
                "text": text or "",
                "date": date_int,
                "handle": handle,
            })
            if rowid > self.last_check_rowid:
                self.last_check_rowid = rowid

        return messages

    # ── Envoi via osascript ──────────────────────────────────

    @staticmethod
    def _escape_for_applescript(text: str) -> str:
        """Échappe les caractères spéciaux pour une string AppleScript."""
        return escape_applescript_string(text)

    def _send_chunk(self, chunk: str) -> bool:
        """Envoie un chunk via Messages.app + osascript. Retourne True si OK."""
        escaped = self._escape_for_applescript(chunk)
        script = (
            'tell application "Messages"\n'
            '    set targetService to 1st account whose service type = iMessage\n'
            f'    set targetBuddy to participant "{self.target}" of targetService\n'
            f'    send "{escaped}" to targetBuddy\n'
            'end tell'
        )
        for attempt in range(2):
            result = run_applescript(script, timeout=OSASCRIPT_TIMEOUT)
            if result.ok:
                return True
            if result.reason == "timeout":
                logger.warning("[iMessage] osascript timeout (tentative %s)", attempt + 1)
                if attempt == 0:
                    continue
                return False
            if result.reason == "not_found":
                logger.error("[iMessage] osascript introuvable — pas un macOS ?")
                return False
            logger.error("[iMessage] Envoi KO (%s) : %s", result.returncode, result.stderr)
            return False
        return False

    def _send_message(self, text: str) -> int:
        """Envoie le texte (split en chunks si long). Retourne le nombre de chunks envoyés."""
        if not text:
            return 0
        chunks = [
            text[i:i + MESSAGE_CHUNK_SIZE]
            for i in range(0, len(text), MESSAGE_CHUNK_SIZE)
        ]
        sent = 0
        for chunk in chunks:
            if self._send_chunk(chunk):
                sent += 1
            else:
                break
        if sent:
            logger.info(f"[iMessage] → {self.target} ({sent}/{len(chunks)} chunks, {len(text)} chars)")
            # Mémorise la réponse complète pour l'anti-écho côté input
            self._remember_outgoing(text)
        return sent

    # ── Traitement ───────────────────────────────────────────

    def _apply_prefix_filter(self, text: str) -> str | None:
        """Si IMESSAGE_PREFIX est défini, vérifie que le message commence par lui.

        Retourne le texte sans le préfixe (et trim) si OK, None sinon.
        Si pas de préfixe configuré, retourne le texte tel quel.
        """
        prefix = (config.IMESSAGE_PREFIX or "").strip()
        if not prefix:
            return text
        # Match case-insensitive sur le 1er mot, suivi optionnel de ":" "," " "
        pattern = re.compile(rf"^\s*{re.escape(prefix)}\b[\s:,;\-]*", re.IGNORECASE)
        m = pattern.match(text)
        if not m:
            return None
        return text[m.end():].strip()

    async def _process_message(self, text: str) -> str:
        """Filtre + délègue à l'orchestrateur. Retourne la réponse à envoyer (ou '')."""
        cleaned = self._apply_prefix_filter(text)
        if cleaned is None:
            logger.info(f"[iMessage] Message ignoré (pas de préfixe) : {text[:60]!r}")
            return ""
        if not cleaned:
            return ""

        # Imports tardifs pour éviter les cycles d'import
        from agents.orchestrator import orchestrator
        from database import create_conversation, end_conversation, save_message

        conv_id = None
        try:
            conv_id = create_conversation(agent="orchestrator")
            save_message(conv_id, "user", cleaned)

            result = await orchestrator.handle(cleaned, conversation_id=conv_id)
            response = result.get("response", "") or ""

            # save_message déjà fait par _call_claude — ici on le refait pas pour éviter doublon

            return response
        except Exception as e:
            logger.exception("[iMessage] Erreur traitement message")
            return f"Erreur JARVIS : {type(e).__name__}: {e}"
        finally:
            if conv_id:
                try:
                    end_conversation(conv_id)
                except Exception:
                    pass

    # ── Polling loop ─────────────────────────────────────────

    async def start_polling(self, interval: float = 3.0) -> None:
        """Lance la boucle de polling. À mettre dans `asyncio.create_task()`."""
        if not self.is_available():
            logger.warning("[iMessage] Polling annulé (bridge indisponible)")
            return

        loop = asyncio.get_event_loop()

        # Init : on saute tous les messages déjà présents au démarrage
        self.last_check_rowid = await loop.run_in_executor(None, self._max_rowid)
        logger.info(
            f"[iMessage] Polling démarré (interval={interval}s, "
            f"start_rowid={self.last_check_rowid}, target={self.target}, "
            f"prefix={(config.IMESSAGE_PREFIX or '∅')!r})"
        )

        self.running = True
        while self.running:
            try:
                messages = await loop.run_in_executor(None, self._get_new_messages)
                # `_get_new_messages` met déjà à jour `self.last_check_rowid`
                # AVANT qu'on lance le traitement → garantit qu'un message ne
                # peut pas être retraité même si le LLM met du temps à répondre.
                for msg in messages:
                    text = (msg.get("text") or "").strip()
                    rowid = int(msg["rowid"])
                    # Garde-fou anti-boucle : ne traite jamais deux fois le même ROWID.
                    if rowid in self.processed_rowids:
                        self.last_check_rowid = max(self.last_check_rowid, rowid)
                        logger.debug("[iMessage] rowid déjà traité — skip (%s)", rowid)
                        continue
                    self.processed_rowids.add(rowid)
                    if len(self.processed_rowids) > self._processed_rowids_max:
                        # Conserver une fenêtre glissante des derniers rowids.
                        self.processed_rowids = set(sorted(self.processed_rowids)[-self._processed_rowids_max:])
                    self.last_check_rowid = max(self.last_check_rowid, rowid)
                    logger.info(
                        f"[iMessage] ← {msg.get('handle')} (rowid={msg['rowid']}) : "
                        f"{text[:80]!r}"
                    )

                    # Anti-écho : si le texte reçu est exactement une de nos
                    # dernières réponses (réfléchissement Messages, doublon Apple…)
                    if self._is_echo(text):
                        logger.info(f"[iMessage] Écho détecté — skip (rowid={msg['rowid']})")
                        continue

                    response = await self._process_message(text)
                    if response:
                        await loop.run_in_executor(None, self._send_message, response)
                        # Laisse à Messages.app le temps d'écrire le message
                        # sortant dans chat.db (sinon le prochain poll pourrait
                        # le voir et — bien que filtré par is_from_me=0 — on
                        # évite les conditions de course sur ROWID).
                        await asyncio.sleep(1.0)
            except asyncio.CancelledError:
                logger.info("[iMessage] Polling annulé (CancelledError)")
                break
            except Exception as e:
                logger.exception(f"[iMessage] Erreur dans la boucle : {e}")

            try:
                logger.debug("[iMessage] Poll cycle — last_rowid=%s", self.last_check_rowid)
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                break

        logger.info("[iMessage] Polling arrêté")

    def stop(self) -> None:
        self.running = False


def send_imessage_to_address(address: str, text: str) -> tuple[bool, str]:
    """Envoie un message iMessage à une adresse (téléphone ou email Apple ID)."""
    addr = (address or "").strip()
    if not addr:
        return False, "Adresse vide"
    if not (text or "").strip():
        return False, "Texte vide"
    bridge = IMessageBridge(addr)
    try:
        n = bridge._send_message(text)
        if n > 0:
            return True, "Envoyé"
        return False, "Échec envoi (osascript)"
    except Exception as e:
        logger.exception("[iMessage] send_imessage_to_address")
        return False, str(e)


# ── Singleton (None si pas configuré) ───────────────────────

imessage_bridge: IMessageBridge | None = (
    IMessageBridge(config.IMESSAGE_TARGET) if config.IMESSAGE_TARGET else None
)
