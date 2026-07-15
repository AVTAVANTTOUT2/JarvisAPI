"""Daemon JARVIS — sentinelle permanente.

Le daemon tourne **en parallèle** du serveur web (lancé via `asyncio.create_task`
dans le lifespan FastAPI). Il orchestre :

- screen watcher (capture + diff + LLM vision local) ;
- surveillance iMessage (handle.id de tous les contacts → triage local Ollama) ;
- surveillance mails (Apple Mail) ;
- rappels calendrier (15 min avant) ;
- wake word "Jarvis" (Porcupine) → mode conversation mains libres ;
- file d'attente TTS jouée sur les haut-parleurs locaux ;
- surveillance des heartbeats des machines distantes.

PRINCIPE GÉNÉRAL :
  Ollama local pré-digère et trie. Claude API ne reçoit que des résumés texte,
  jamais d'images. 95 % du travail coûte 0 token API.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from typing import Any

import httpx

import config
from audio.voice_queue import VoicePriority, priority_from_string, voice_queue
from database import (
    create_conversation,
    get_all_devices,
    get_all_processed_email_ids,
    get_current_screen_context,
    mark_device_offline,
    set_active_device,
)
from jarvis.notification_service import notification_service
from integrations.apple_data import apple_data
from pipeline import process_message_internal
from scripts.screen_watcher import screen_watcher

logger = logging.getLogger(__name__)


class JarvisDaemon:
    """Sentinelle permanente — tourne 24/7 en parallèle du serveur web."""

    MODES = ("veille", "conversation", "ecoute_passive")

    def __init__(self) -> None:
        self.mode: str = "veille"
        self.conversation_active = False
        self.conversation_id: int | None = None

        # File d'attente des messages à prononcer
        self.tts_queue: asyncio.Queue[Any] = asyncio.Queue()
        self.last_tts_time: float = 0.0
        self.tts_cooldown = int(getattr(config, "DAEMON_TTS_COOLDOWN", 30))
        self._delayed_voice_tasks: set[asyncio.Task[Any]] = set()

        # iMessage tracking
        self.imessage_cursor_name = "daemon.notifications"
        self.known_msg_ids: set[int] = set()

        # Mail tracking
        self.known_mail_ids: set[str] = set()

        # Screen watcher
        self.screen_watcher = screen_watcher
        self.screen_watcher.on_notable = self._on_screen_notable
        self.screen_watcher.on_idle = self._on_idle

        # Wake word
        self.wake_word_enabled = bool(getattr(config, "WAKE_WORD_ENABLED", False))
        self.porcupine = None

        self.running = False

    # ── Cycle de vie ──────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Lance toutes les boucles du daemon en parallèle."""
        self.running = True
        logger.info("[daemon] démarrage en mode %s", self.mode)

        # Le lecteur vocal existe même si la capture micro est désactivée.
        # audio_daemon reste l'unique implémentation de lecture, sans ouvrir
        # PyAudio tant que AUDIO_DAEMON_ENABLED=false.
        await self._ensure_voice_output()

        # Initialiser le dernier ROWID iMessage pour ne pas retraiter le backlog
        try:
            if apple_data.is_available():
                max_rowid = apple_data.get_max_rowid()
                from integrations.imessage_cursor import initialize_consumer_cursor

                start_rowid = initialize_consumer_cursor(
                    self.imessage_cursor_name, max_rowid
                )
                logger.info("[daemon] iMessage starting rowid: %s", start_rowid)
        except Exception as e:
            logger.warning("[daemon] init iMessage rowid échoué : %s", e)

        # Hydrater le cache mail depuis la DB (évite re-notif au redémarrage)
        try:
            for gid in get_all_processed_email_ids():
                if gid:
                    self.known_mail_ids.add(str(gid))
            if self.known_mail_ids:
                logger.info("[daemon] %d mail(s) connus hydratés depuis email_summaries", len(self.known_mail_ids))
        except Exception as e:
            logger.warning("[daemon] hydratation known_mail_ids : %s", e)

        tasks = [
            asyncio.create_task(self._tts_loop(), name="daemon_tts"),
            asyncio.create_task(self._notification_loop(), name="daemon_notif"),
            asyncio.create_task(self.screen_watcher.start(), name="daemon_screen"),
            asyncio.create_task(self._calendar_reminder_loop(), name="daemon_calendar"),
            asyncio.create_task(self._device_health_loop(), name="daemon_health"),
        ]
        if self.wake_word_enabled:
            tasks.append(asyncio.create_task(self._wake_word_loop(), name="daemon_wake"))

        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            logger.info("[daemon] arrêt demandé")
            for t in tasks:
                t.cancel()
            raise
        finally:
            for task in self._delayed_voice_tasks:
                task.cancel()
            self._delayed_voice_tasks.clear()
            await voice_queue.stop()

    async def _ensure_voice_output(self) -> None:
        """Démarre le lecteur central sans ouvrir le microphone."""
        from scripts.audio_daemon import audio_daemon

        await voice_queue.start(
            audio_daemon._play_tts_native,
            audio_daemon._stop_current_tts,
        )

    def stop(self) -> None:
        self.running = False
        for task in self._delayed_voice_tasks:
            task.cancel()
        self._delayed_voice_tasks.clear()
        try:
            self.screen_watcher.stop()
        except Exception:
            pass

    # ── Callbacks Screen Watcher ──────────────────────────────────────────────

    async def _on_screen_notable(self, notable: str, context: dict) -> None:
        """Quelque chose de pertinent vu à l'écran. Claude formule, peut-être TTS."""
        logger.info("[daemon] screen notable : %s", notable[:120])

        temp_conv = create_conversation(agent="daemon_screen")
        prompt = (
            f"[NOTIFICATION ÉCRAN] L'utilisateur est sur "
            f"{context.get('app', '?')} ({context.get('activity', '?')}). "
            f"Observation : {notable}. "
            "Si c'est pertinent, propose une aide courte (1-2 phrases max, style JARVIS). "
            "Si ce n'est pas pertinent, réponds juste NULL."
        )
        try:
            result = await process_message_internal(prompt, temp_conv, voice_mode=True)
        except Exception as e:
            logger.warning("[daemon] _process_message_internal screen : %s", e)
            return

        text = (result or {}).get("text") or ""
        if text and "NULL" not in text.upper():
            await self.tts_queue.put((text, "neutral", "background"))

    async def _on_idle(self, idle_minutes: int) -> None:
        """Utilisateur inactif depuis longtemps."""
        await self.tts_queue.put(
            (
                f"Monsieur, vous semblez inactif depuis {int(idle_minutes)} minutes. Tout va bien ?",
                "concerned",
                "background",
            )
        )

    # ── Boucle Notifications ──────────────────────────────────────────────────

    async def _notification_loop(self) -> None:
        """Surveille iMessage + Mail."""
        while self.running:
            try:
                await self._check_imessage()
            except Exception as e:
                logger.warning("[daemon] erreur iMessage check : %s", e)
            try:
                await self._check_mail()
            except Exception as e:
                logger.warning("[daemon] erreur mail check : %s", e)
            await asyncio.sleep(5)

    async def _check_imessage(self) -> None:
        """Lit les nouveaux messages pour notification vocale uniquement.

        IMPORTANT: si le bridge iMessage est actif, le daemon n'interroge pas
        chat.db pour éviter tout double traitement. Le bridge reste l'unique
        composant autorisé à répondre aux messages.
        """
        try:
            from integrations.imessage import imessage_bridge
            if imessage_bridge and getattr(imessage_bridge, "running", False):
                logger.debug("[daemon] iMessage check ignoré (bridge actif)")
                return
        except Exception:
            pass

        from integrations.imessage_reader import imessage_reader

        if not imessage_reader or not imessage_reader.is_available():
            return

        try:
            from integrations.contacts import contacts_reader
        except Exception:
            contacts_reader = None  # type: ignore[assignment]

        rows: list[Any] = []
        try:
            from integrations.imessage_cursor import (
                advance_consumer_cursor,
                get_consumer_cursor,
            )

            last_rowid = get_consumer_cursor(self.imessage_cursor_name)
            rows = apple_data.get_new_messages(last_rowid, limit=10)
        except Exception as e:
            logger.warning("[daemon] iMessage scan : %s", e)
            return

        if rows:
            advance_consumer_cursor(
                self.imessage_cursor_name,
                max(int(row["rowid"]) for row in rows),
            )

        for row in rows:
            rowid = int(row["rowid"])

            if rowid in self.known_msg_ids:
                continue
            self.known_msg_ids.add(rowid)

            if row["is_from_me"]:
                continue

            handle = row["handle"] or ""
            text = (row["text"] or "").strip()
            if not text:
                continue

            sender = handle
            try:
                if contacts_reader and getattr(contacts_reader, "_cache", None):
                    sender = contacts_reader.resolve_handle(handle) or handle
            except Exception:
                pass

            should_notify = await self._local_triage(
                f"Message iMessage de {sender} : \"{text[:200]}\""
            )

            if should_notify:
                try:
                    # Ne jamais utiliser le pipeline principal ici pour éviter
                    # toute action secondaire involontaire.
                    await self.tts_queue.put(
                        f"Monsieur, {sender} vous a envoyé : {text[:100]}"
                    )
                except Exception as e:
                    logger.warning("[daemon] formulation iMessage : %s", e)

            try:
                notification_service.create(
                    source="imessage",
                    title=f"Message de {sender}",
                    content=text[:300],
                    priority="medium" if should_notify else "low",
                )
            except Exception as e:
                logger.debug("[daemon] create_notification imessage : %s", e)

    async def _check_mail(self) -> None:
        """Notifications vocales mail — délégué à email_watcher si actif."""
        try:
            from scripts.email_watcher import email_watcher as _ew
            if _ew and (getattr(_ew, "_running", False) or getattr(_ew, "running", False)):
                return
        except Exception:
            pass

        from database import get_recent_email_summaries

        try:
            summaries = get_recent_email_summaries(limit=10, action_needed_only=True)
        except Exception as e:
            logger.warning("[daemon] email_summaries : %s", e)
            return

        for row in summaries:
            mail_id = str(row.get("gmail_id") or row.get("id") or "")
            if not mail_id or mail_id in self.known_mail_ids:
                continue
            self.known_mail_ids.add(mail_id)

            sender = row.get("sender") or "?"
            subject = row.get("subject") or "?"
            should_notify = await self._local_triage(
                f"Email de {sender}, objet : {subject}"
            )

            if should_notify:
                summary = (row.get("summary") or subject or "")[:120]
                await self.tts_queue.put(f"Monsieur, mail de {sender} : {summary}")

            try:
                notification_service.create(
                    source="email",
                    title=f"Mail de {sender}",
                    content=subject,
                    priority=str(row.get("priority") or "medium"),
                )
            except Exception as e:
                logger.debug("[daemon] create_notification mail : %s", e)

    # ── Triage LOCAL via Ollama ───────────────────────────────────────────────

    async def _local_triage(self, event_description: str) -> bool:
        """Décide localement si l'événement mérite d'interrompre l'utilisateur.

        Ollama qwen2.5:7b — réponse ~500 ms, coût Claude API = 0 token.
        En cas d'échec on renvoie False (silence > faux positifs).
        """
        triage_model = str(getattr(config, "TRIAGE_MODEL", "qwen2.5:7b"))
        ollama_url = str(getattr(config, "OLLAMA_URL", "http://localhost:11434"))

        prompt = (
            "L'utilisateur travaille. Cet événement vient d'arriver :\n"
            f"{event_description}\n\n"
            "Dois-je l'interrompre vocalement ? Réponds UNIQUEMENT par OUI ou NON.\n"
            "- Message personnel d'un ami/famille → OUI\n"
            "- Mail urgent ou professionnel important → OUI\n"
            "- Spam, newsletter, pub, notification système → NON\n"
            "- Message de groupe sans mention directe → NON"
        )

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    f"{ollama_url}/api/generate",
                    json={
                        "model": triage_model,
                        "prompt": prompt,
                        "stream": False,
                        "options": {"temperature": 0.0, "num_predict": 5},
                    },
                )
                result = (response.json().get("response") or "").strip().upper()
                return "OUI" in result
        except Exception as e:
            logger.warning("[daemon] triage local échoué : %s", e)
            return False

    # ── Rappels Calendar ──────────────────────────────────────────────────────

    async def _calendar_reminder_loop(self) -> None:
        """Vérifie le calendrier toutes les 5 min et prévient 15 min avant."""
        reminded_events: set[str] = set()

        while self.running:
            try:
                from integrations.calendar_api import calendar_client

                if calendar_client and calendar_client.is_available():
                    events = await calendar_client.get_today_events() or []
                    now = datetime.now()

                    for event in events:
                        try:
                            start_str = str(event.get("start", "") or "")
                            if not start_str:
                                continue
                            if len(start_str) <= 5:
                                start_time = datetime.strptime(start_str, "%H:%M").replace(
                                    year=now.year, month=now.month, day=now.day
                                )
                            else:
                                # ISO ou "YYYY-MM-DD HH:MM"
                                try:
                                    start_time = datetime.fromisoformat(start_str.replace("Z", ""))
                                except ValueError:
                                    start_time = datetime.strptime(start_str, "%Y-%m-%d %H:%M")

                            delta = (start_time - now).total_seconds() / 60
                            event_id = f"{event.get('summary', '')}_{start_str}"

                            if 0 < delta <= 15 and event_id not in reminded_events:
                                reminded_events.add(event_id)
                                mins = int(delta)
                                summary = event.get("summary", "événement")
                                await self.tts_queue.put(
                                    f"Monsieur, rappel : {summary} dans {mins} minutes."
                                )
                        except Exception:
                            continue
            except Exception as e:
                logger.warning("[daemon] calendar check : %s", e)
            await asyncio.sleep(300)

    # ── Boucle TTS ────────────────────────────────────────────────────────────

    async def _tts_loop(self) -> None:
        """Relaie la file interne vers la file vocale centrale (audio_daemon joue)."""
        while self.running:
            try:
                item = await asyncio.wait_for(self.tts_queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue

            emotion, priority = "neutral", VoicePriority.BACKGROUND
            if isinstance(item, tuple):
                if len(item) >= 3:
                    text, emotion, priority_raw = item[0], item[1], item[2]
                    if str(priority_raw).lower() in ("urgent", "critical"):
                        priority = VoicePriority.CRITICAL
                    else:
                        priority = priority_from_string(str(priority_raw))
                elif len(item) == 2:
                    text, emotion = item
                else:
                    text = item[0] if item else ""
            else:
                text = item

            if not text or not str(text).strip():
                continue

            try:
                from database import is_dnd_active

                if priority != VoicePriority.CRITICAL and is_dnd_active():
                    logger.info("[daemon] DND actif — TTS ignoré : %s", str(text)[:50])
                    continue
            except Exception:
                pass

            if self.mode == "veille" and config.is_quiet_hours():
                logger.info("[daemon] heures calmes — TTS ignoré : %s", str(text)[:50])
                continue

            if self.mode == "veille" and priority != VoicePriority.CRITICAL:
                now = time.time()
                elapsed = now - self.last_tts_time
                if elapsed < self.tts_cooldown:
                    wait_s = self.tts_cooldown - elapsed
                    logger.info(
                        "[daemon] TTS cooldown — report %.0fs : %s",
                        wait_s,
                        str(text)[:50],
                    )
                    self.last_tts_time = now + wait_s
                    task = asyncio.create_task(
                        self._enqueue_voice_after(
                            wait_s, str(text), str(emotion), priority,
                        ),
                        name="daemon_voice_delayed",
                    )
                    self._delayed_voice_tasks.add(task)
                    task.add_done_callback(self._delayed_voice_tasks.discard)
                    continue
                self.last_tts_time = now

            logger.info("[daemon] File vocale (%s) : %s", priority.name, str(text)[:80])
            await voice_queue.enqueue(str(text), emotion=str(emotion), priority=priority)

    async def _enqueue_voice_after(
        self,
        delay: float,
        text: str,
        emotion: str,
        priority: VoicePriority,
    ) -> None:
        await asyncio.sleep(max(0.0, delay))
        if self.running:
            await voice_queue.enqueue(text, emotion=emotion, priority=priority)

    # ── Wake Word ─────────────────────────────────────────────────────────────

    async def _wake_word_loop(self) -> None:
        """Wake word désactivé ici — le micro est détenu par audio_daemon uniquement."""
        if not self.wake_word_enabled:
            return
        logger.warning(
            "[daemon] Wake word Porcupine désactivé dans jarvis_daemon "
            "(utilisez audio_daemon ou WAKE_WORD_ENABLED=false)"
        )
        while self.running:
            await asyncio.sleep(3600)

    async def _start_conversation(self) -> None:
        """Conversation vocale — déléguée à audio_daemon (micro unique)."""
        logger.warning(
            "[daemon] _start_conversation désactivé — utilisez audio_daemon "
            "(WAKE_WORD_ENABLED=false = mode continu sans 2e capture micro)"
        )

    async def _listen_with_vad(self, timeout: int = 15) -> bytes | None:
        """Capture micro désactivée — audio_daemon détient le micro exclusivement."""
        logger.warning(
            "[daemon] _listen_with_vad ignoré (timeout=%ss) — micro détenu par audio_daemon",
            timeout,
        )
        return None

    # ── Health check devices ──────────────────────────────────────────────────

    async def _device_health_loop(self) -> None:
        """Marque les machines offline si pas de heartbeat depuis 2 min."""
        while self.running:
            try:
                devices = get_all_devices()
                now = datetime.now()
                for device in devices:
                    last_hb = device.get("last_heartbeat")
                    if not last_hb:
                        continue
                    try:
                        last = datetime.fromisoformat(str(last_hb).replace("Z", ""))
                    except Exception:
                        continue
                    if (now - last).total_seconds() > 120:
                        if device.get("is_online"):
                            mark_device_offline(device["device_id"])
                            logger.info(
                                "[daemon] device %s marqué offline",
                                device.get("device_name") or device["device_id"],
                            )
                            if device.get("is_active"):
                                online_devices = [
                                    d for d in devices
                                    if d["device_id"] != device["device_id"]
                                    and d.get("is_online")
                                ]
                                if online_devices:
                                    set_active_device(online_devices[0]["device_id"])
                                    logger.info(
                                        "[daemon] basculé sur %s",
                                        online_devices[0].get("device_name"),
                                    )
            except Exception as e:
                logger.warning("[daemon] device health : %s", e)
            await asyncio.sleep(30)


daemon = JarvisDaemon()
