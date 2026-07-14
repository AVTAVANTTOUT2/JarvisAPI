"""Surveillance proactive des emails via Apple Mail.

DeepSeek (modèle rapide) analyse chaque nouveau mail (~$0.001) et décide seul s'il mérite d'être
signalé. Seuls deux types d'emails sont notifiés :
  - PAIEMENT : facture, prélèvement, virement, commande, etc.
  - DEMANDE : une vraie personne qui attend une réponse/action.

Tout le reste (newsletters, promos, notifs auto) est silencieusement ignoré.

Au premier cycle après démarrage : les non-lus **déjà** présents dans
`email_summaries` sont ignorés ; les autres sont **analysés** (rattrapage
après une longue coupure). Les cycles suivants traitent les nouveaux IDs.
"""

import asyncio
import json
import logging
import re
from typing import Any

from pathlib import Path

import config
import llm
from database import (
    create_task,
    get_all_processed_email_ids,
    save_email_full,
    upsert_email_summary,
)
from jarvis.notification_service import notification_service

logger = logging.getLogger(__name__)

JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*\n(.*?)\n```", re.DOTALL)
PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "email_analyzer.txt"

MAX_BODY_CHARS = 1500
MAX_UNREAD_PER_CYCLE = 20


def _load_prompt_template() -> str:
    if PROMPT_PATH.exists():
        return PROMPT_PATH.read_text(encoding="utf-8")
    logger.warning(f"[email_watcher] Prompt absent : {PROMPT_PATH}")
    return ""


def _truncate_body(body: str | None) -> str:
    if not body:
        return ""
    body = body.replace("\r", "").strip()
    if len(body) > MAX_BODY_CHARS:
        return body[:MAX_BODY_CHARS] + "\n[…tronqué…]"
    return body


def _parse_json(raw: str) -> dict | None:
    """Parse tolérant : accepte JSON brut, blocs ```json, ou JSON noyé dans du texte."""
    if not raw:
        return None
    raw = raw.strip()

    match = JSON_BLOCK_RE.search(raw)
    payload = match.group(1) if match else raw

    if not payload.startswith("{"):
        start = payload.find("{")
        end = payload.rfind("}")
        if start != -1 and end != -1 and end > start:
            payload = payload[start:end + 1]

    try:
        return json.loads(payload)
    except json.JSONDecodeError as e:
        logger.warning(f"[email_watcher] JSON invalide ({e})")
        return None


def _priority_for(reason: str, urgent) -> str:
    """Priorité de notification : urgent (48h) > paiement (high) > demande (medium)."""
    if urgent is True:
        return "urgent"
    return "high" if reason == "payment" else "medium"


class EmailWatcher:
    """Worker async — polling Mail.app, analyse DeepSeek, actions automatiques.

    Lifecycle :
        watcher = EmailWatcher()
        asyncio.create_task(watcher.start())
        watcher.stop()
    """

    def __init__(self):
        self.running: bool = False
        self.last_processed_ids: set[str] = set()
        self.check_interval: float = float(config.EMAIL_CHECK_INTERVAL)
        self._prompt_template: str = _load_prompt_template()
        self._initialized: bool = False
        self._cycle_lock = asyncio.Lock()
        self._last_cycle_stats: dict[str, Any] = {}

    # ── Lifecycle ──────────────────────────────────────────────

    async def start(self) -> None:
        """Boucle de surveillance. À lancer dans `asyncio.create_task()`."""
        try:
            self.last_processed_ids = get_all_processed_email_ids()
            logger.info(
                f"[email_watcher] Cache hydraté : "
                f"{len(self.last_processed_ids)} emails déjà en DB"
            )
        except Exception as e:
            logger.error(f"[email_watcher] Hydratation cache : {e}")
            self.last_processed_ids = set()

        self.running = True
        self._initialized = False
        logger.info(
            f"[email_watcher] Surveillance démarrée "
            f"(interval={self.check_interval:.0f}s)"
        )

        while self.running:
            try:
                await self._check_new_emails()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception(f"[email_watcher] Erreur cycle : {e}")

            try:
                await asyncio.sleep(self.check_interval)
            except asyncio.CancelledError:
                break

        logger.info("[email_watcher] Arrêté")

    def stop(self) -> None:
        self.running = False

    async def run_catchup_cycle(self) -> dict[str, Any]:
        """Réhydrate le cache depuis la DB, reset le cache Mail, rejoue un cycle « premier démarrage ».

        Appelable via ``POST /api/email-watcher/catchup`` pendant que le watcher tourne.
        """
        from integrations import mail_client as mc

        if mc is not None and hasattr(mc, "reset_availability_cache"):
            mc.reset_availability_cache()
        try:
            self.last_processed_ids = get_all_processed_email_ids()
            hydrated = len(self.last_processed_ids)
        except Exception as e:
            logger.error(f"[email_watcher] run_catchup_cycle hydratation : {e}")
            self.last_processed_ids = set()
            hydrated = 0
        self._initialized = False
        await self._check_new_emails()
        out = {"ok": True, "hydrated_from_db": hydrated, **self._last_cycle_stats}
        return out

    # ── Cycle de check ─────────────────────────────────────────

    async def _check_new_emails(self) -> None:
        from integrations import mail_client

        async with self._cycle_lock:
            stats: dict[str, Any] = {
                "mail_client_missing": False,
                "mail_available": False,
                "unread_fetched": 0,
                "mode": "noop",
                "first_cycle_already": 0,
                "first_cycle_to_analyze": 0,
                "incremental_new": 0,
            }
            self._last_cycle_stats = stats

            # Sync avec la DB (ex. script catchup lancé à côté du serveur).
            try:
                self.last_processed_ids |= get_all_processed_email_ids()
            except Exception as e:
                logger.warning("[email_watcher] sync last_processed_ids <- DB : %s", e)

            if mail_client is None:
                stats["mail_client_missing"] = True
                return
            # is_available() appelle subprocess.run (bloquant) — exécuter dans un thread
            # pour ne pas bloquer l'event loop asyncio.
            available = await asyncio.to_thread(mail_client.is_available)
            stats["mail_available"] = available
            if not available:
                return

            try:
                unread = await mail_client.get_unread(MAX_UNREAD_PER_CYCLE)
            except Exception as e:
                logger.error(f"[email_watcher] get_unread : {e}")
                return

            stats["unread_fetched"] = len(unread)

            if not unread:
                if not self._initialized:
                    self._initialized = True
                    logger.info("[email_watcher] Premier cycle : 0 non-lus")
                    stats["mode"] = "first_cycle_empty"
                return

            # Premier cycle : rattrapage — analyser les non-lus absents de la DB.
            if not self._initialized:
                self._initialized = True
                stats["mode"] = "first_cycle_catchup"
                processed_in_db = get_all_processed_email_ids()
                fresh: list[dict] = []
                already = 0
                for e in unread:
                    eid = e.get("id")
                    if not eid:
                        continue
                    if eid in processed_in_db:
                        self.last_processed_ids.add(eid)
                        already += 1
                    else:
                        fresh.append(e)
                stats["first_cycle_already"] = already
                stats["first_cycle_to_analyze"] = len(fresh)
                logger.info(
                    "[email_watcher] Premier cycle : %d déjà en base, %d à analyser",
                    already,
                    len(fresh),
                )
                for email_summary in fresh:
                    email_id = email_summary["id"]
                    try:
                        await self._analyze_email(email_summary)
                    except Exception as e:
                        logger.exception(f"[email_watcher] _analyze_email {email_id} : {e}")
                    finally:
                        self.last_processed_ids.add(email_id)
                return

            stats["mode"] = "incremental"
            new = [e for e in unread if e.get("id") and e["id"] not in self.last_processed_ids]
            stats["incremental_new"] = len(new)
            if not new:
                return

            logger.info(f"[email_watcher] {len(new)} nouveau(x) email(s) à analyser")

            for email_summary in new:
                email_id = email_summary["id"]
                try:
                    await self._analyze_email(email_summary)
                except Exception as e:
                    logger.exception(f"[email_watcher] _analyze_email {email_id} : {e}")
                finally:
                    self.last_processed_ids.add(email_id)

    # ── Analyse d'un email ─────────────────────────────────────

    async def _analyze_email(self, email_summary: dict) -> None:
        """Récupère le body → DeepSeek → JSON → agit si notify=true.
        
        Stocke également le contenu intégral et le résumé en DB
        via ``save_email_full`` pour une lecture vocale instantanée (sans AppleScript).
        """
        from integrations import mail_client

        email_id = email_summary["id"]
        full = await mail_client.get_message(email_id)
        if not full:
            logger.warning(f"[email_watcher] get_message {email_id} → None")
            return

        prompt = self._build_prompt(full)
        if not prompt:
            return

        try:
            result = await llm.chat(
                messages=[{"role": "user", "content": prompt}],
                model=config.DEEPSEEK_FAST_MODEL,
                system="Tu retournes UNIQUEMENT du JSON valide, rien d'autre.",
                max_tokens=200,
                temperature=0.0,
                use_cache=False,
            )
        except Exception as e:
            logger.error(f"[email_watcher] DeepSeek call : {e}")
            return

        analysis = _parse_json(result.get("content", ""))
        if not analysis:
            logger.warning(f"[email_watcher] JSON non parseable pour {email_id}")
            return

        sender = (full.get("from") or "?").strip()
        sender_short = sender.split("<")[0].strip() or sender
        subject = full.get("subject") or "(sans sujet)"
        body_full = full.get("body", "")
        received_at = full.get("date", "")

        notify = analysis.get("notify", False)
        reason = analysis.get("reason", "ignore")
        summary = analysis.get("summary", "")

        # ── Toujours sauvegarder le contenu intégral + résumé en DB ──────
        try:
            category = "finance" if reason == "payment" else ("pro" if reason == "request" else "notification")
            save_email_full(
                gmail_id=email_id,
                sender=sender_short,
                subject=subject,
                body=body_full,         # contenu intégral, pas tronqué
                received_at=received_at,
                summary=summary or subject,
                category=category,
                priority="high" if reason == "payment" else ("medium" if reason == "request" else "low"),
            )
        except Exception as e:
            logger.error(f"[email_watcher] save_email_full : {e}")

        if not notify or reason == "ignore":
            logger.info(f"[email_watcher] Ignoré : {sender_short} — {subject}")
            # Déjà sauvegardé via save_email_full plus haut — rien à faire
            return

        # DeepSeek dit de notifier → on agit
        logger.info(
            f"[email_watcher] NOTIF ({reason}) : {sender_short} — {summary}"
        )

        amount = analysis.get("amount")
        from_name = analysis.get("from_name")
        action_needed = analysis.get("action_needed")
        deadline = analysis.get("deadline")
        priority = _priority_for(reason, analysis.get("urgent"))

        # Mail urgent → drapeau dans Mail.app (best-effort, jamais bloquant)
        if priority == "urgent":
            try:
                await mail_client.flag_message(email_id)
            except Exception as e:
                logger.debug(f"[email_watcher] flag Mail.app : {e}")

        try:
            from integrations.notifications_macos import mac_notifier
            if config.DESKTOP_NOTIFICATIONS and mac_notifier.is_available():
                if priority == "high" or reason == "payment":
                    await mac_notifier.notify_urgent(
                        title="Mail urgent",
                        message=f"De {from_name or sender_short} : {summary}"[:200],
                    )
                else:
                    await mac_notifier.notify(
                        title="JARVIS — Courrier",
                        message=f"De {from_name or sender_short} : {summary}"[:200],
                        sound="Glass",
                    )
        except Exception as e:
            logger.exception("[email_watcher] notification bureau : %s", e)

        # 1. Notification
        try:
            notification_service.create(
                source="email",
                title=summary,
                content=action_needed,
                priority=priority,
                email_id=email_id,
            )
        except Exception as e:
            logger.error(f"[email_watcher] create_notification : {e}")

        # 2. Tâche si paiement avec montant
        if reason == "payment" and amount:
            try:
                tid = create_task(
                    title=f"Paiement : {summary}",
                    description=f"Montant : {amount}",
                    priority="high",
                    due_date=deadline,
                    category="finance",
                )
                logger.info(f"[email_watcher] → Tâche finance #{tid}")
            except Exception as e:
                logger.error(f"[email_watcher] create_task (payment) : {e}")

        # 3. Tâche si demande avec action
        if reason == "request" and action_needed:
            try:
                tid = create_task(
                    title=action_needed,
                    priority="medium",
                    due_date=deadline,
                    category="email",
                )
                logger.info(f"[email_watcher] → Tâche email #{tid}")
            except Exception as e:
                logger.error(f"[email_watcher] create_task (request) : {e}")

        # 4. Alerte iMessage — supprimée pendant les heures calmes et le mode
        #    « silence total sauf feu » (seul l'urgent passe le DND).
        #    La notification reste en base et dans l'UI dans tous les cas.
        from database import is_dnd_active

        if config.is_quiet_hours():
            logger.info("[email_watcher] heures calmes — iMessage non envoyé : %s", subject[:60])
        elif is_dnd_active() and priority != "urgent":
            logger.info("[email_watcher] DND actif — iMessage non envoyé : %s", subject[:60])
        else:
            display_name = from_name or sender_short
            imsg = f"Mail de {display_name} : {summary}"
            if amount:
                imsg += f" — {amount}"
            if deadline:
                imsg += f" — avant le {deadline}"
            await self._send_imessage_alert(imsg)

        # Note : le contenu intégral + résumé est déjà en DB via save_email_full
        # (appelé avant la branche notify/ignore). Pas besoin d'upsert_email_summary.

    def _build_prompt(self, full_email: dict) -> str:
        if not self._prompt_template:
            return ""
        return (
            self._prompt_template
            .replace("{{from}}", str(full_email.get("from", "")))
            .replace("{{subject}}", str(full_email.get("subject", "")))
            .replace("{{body}}", _truncate_body(full_email.get("body", "")))
            .replace("{{user_name}}", config.USER_NAME)
        )

    # ── iMessage ───────────────────────────────────────────────

    async def _send_imessage_alert(self, msg: str) -> None:
        try:
            from integrations import imessage_bridge
        except Exception:
            return

        if imessage_bridge is None or not imessage_bridge.is_available():
            return

        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, imessage_bridge._send_message, msg)
            logger.info(f"[email_watcher] → iMessage envoyé")
        except Exception as e:
            logger.error(f"[email_watcher] iMessage : {e}")


email_watcher = EmailWatcher()
