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
import sqlite3
import struct
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

import config
from pipeline import process_message_internal, process_voice_fast
from audio.audio_format import playback_file_extension, prepare_stt_bytes
from database import (
    create_conversation,
    create_notification,
    get_all_devices,
    get_all_processed_email_ids,
    get_current_screen_context,
    mark_device_offline,
    save_message,
    set_active_device,
)
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
        self.tts_queue: asyncio.Queue[str] = asyncio.Queue()
        self.last_tts_time: float = 0.0
        self.tts_cooldown = int(getattr(config, "DAEMON_TTS_COOLDOWN", 30))

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

        # Initialiser le dernier ROWID iMessage pour ne pas retraiter le backlog
        try:
            from integrations.imessage_reader import imessage_reader

            if imessage_reader and imessage_reader.is_available():
                with sqlite3.connect(
                    f"file:{Path.home() / 'Library/Messages/chat.db'}?mode=ro",
                    uri=True,
                ) as db:
                    row = db.execute("SELECT MAX(ROWID) FROM message").fetchone()
                    max_rowid = int(row[0] or 0)
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

    def stop(self) -> None:
        self.running = False
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
            await self.tts_queue.put(text)

    async def _on_idle(self, idle_minutes: int) -> None:
        """Utilisateur inactif depuis longtemps."""
        await self.tts_queue.put(
            f"Monsieur, vous semblez inactif depuis {int(idle_minutes)} minutes. Tout va bien ?"
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
            with sqlite3.connect(
                f"file:{Path.home() / 'Library/Messages/chat.db'}?mode=ro",
                uri=True,
            ) as db:
                db.row_factory = sqlite3.Row
                rows = db.execute(
                    """SELECT m.ROWID, m.text, m.is_from_me, m.date, h.id as handle
                       FROM message m
                       LEFT JOIN handle h ON m.handle_id = h.ROWID
                       WHERE m.ROWID > ? AND m.text IS NOT NULL AND m.text != ''
                       ORDER BY m.ROWID ASC
                       LIMIT 10""",
                    (last_rowid,),
                ).fetchall()
        except Exception as e:
            logger.warning("[daemon] iMessage scan : %s", e)
            return

        if rows:
            advance_consumer_cursor(
                self.imessage_cursor_name,
                max(int(row["ROWID"]) for row in rows),
            )

        for row in rows:
            rowid = int(row["ROWID"])

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
                create_notification(
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
                create_notification(
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
        """Consomme la file d'attente de messages à prononcer."""
        while self.running:
            try:
                item = await asyncio.wait_for(self.tts_queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue

            # La file accepte un str, un tuple (texte, émotion) ou
            # (texte, émotion, priorité). priorité "urgent" = seul son
            # autorisé pendant le mode « silence total sauf feu ».
            emotion, priority = "neutral", "normal"
            if isinstance(item, tuple):
                if len(item) >= 3:
                    text, emotion, priority = item[0], item[1], item[2]
                elif len(item) == 2:
                    text, emotion = item
                else:
                    text = item[0] if item else ""
            else:
                text = item

            if not text or not str(text).strip():
                continue

            # Silence total sauf feu : seul l'urgent passe.
            try:
                from database import is_dnd_active

                if priority != "urgent" and is_dnd_active():
                    logger.info("[daemon] DND actif — TTS ignoré : %s", str(text)[:50])
                    continue
            except Exception:
                pass

            # Heures calmes : JARVIS ne parle pas la nuit en mode veille.
            # Les notifications restent en base ; seule la voix est coupée.
            if self.mode == "veille" and config.is_quiet_hours():
                logger.info("[daemon] heures calmes — TTS ignoré : %s", str(text)[:50])
                continue

            if self.mode == "veille":
                now = time.time()
                elapsed = now - self.last_tts_time
                if elapsed < self.tts_cooldown:
                    wait_s = self.tts_cooldown - elapsed
                    logger.info(
                        "[daemon] TTS cooldown — report %.0fs : %s",
                        wait_s,
                        text[:50],
                    )
                    await asyncio.sleep(wait_s)
                self.last_tts_time = time.time()

            try:
                from audio.tts import tts as _tts

                if _tts:
                    logger.info("[daemon] TTS (%s) : %s", emotion, str(text)[:80])
                    audio_bytes = await _tts.synthesize(text, emotion=emotion)
                    if audio_bytes:
                        await self._play_audio_local(audio_bytes)
            except Exception as e:
                logger.warning("[daemon] TTS erreur : %s", e)

    async def _play_audio_local(self, audio_bytes: bytes) -> None:
        """Joue l'audio localement (MP3, WAV, M4A) via afplay."""
        tmp_path: str | None = None
        ext = playback_file_extension(audio_bytes)
        try:
            with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
                tmp.write(audio_bytes)
                tmp_path = tmp.name

            proc = await asyncio.create_subprocess_exec(
                "afplay", tmp_path,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            try:
                await asyncio.wait_for(proc.wait(), timeout=30.0)
            except asyncio.TimeoutError:
                logger.warning("[daemon] afplay timeout (30s) — kill")
                proc.kill()
                await proc.wait()
        except Exception as e:
            logger.warning("[daemon] playback erreur : %s", e)
        finally:
            if tmp_path:
                try:
                    Path(tmp_path).unlink(missing_ok=True)
                except Exception:
                    pass

    # ── Wake Word ─────────────────────────────────────────────────────────────

    async def _wake_word_loop(self) -> None:
        """Écoute en permanence le mot 'Jarvis' via Porcupine.

        Tourne dans un thread (pyaudio est bloquant), avec une queue async pour
        signaler la détection à l'event loop principal.
        """
        access_key = str(getattr(config, "PORCUPINE_ACCESS_KEY", "") or "")
        if not access_key:
            logger.warning("[daemon] PORCUPINE_ACCESS_KEY non configuré — wake word désactivé")
            return

        try:
            import pvporcupine  # type: ignore[import-not-found]
            import pyaudio  # type: ignore[import-not-found]
        except ImportError:
            logger.warning("[daemon] pvporcupine / pyaudio non installés — wake word désactivé")
            return

        loop = asyncio.get_running_loop()
        wake_queue: asyncio.Queue[bool] = asyncio.Queue()

        def _blocking_loop() -> None:
            try:
                self.porcupine = pvporcupine.create(access_key=access_key, keywords=["jarvis"])
                pa = pyaudio.PyAudio()
                stream = pa.open(
                    rate=self.porcupine.sample_rate,
                    channels=1,
                    format=pyaudio.paInt16,
                    input=True,
                    frames_per_buffer=self.porcupine.frame_length,
                )
                logger.info("[daemon] wake word 'Jarvis' en écoute")
                while self.running:
                    try:
                        pcm = stream.read(self.porcupine.frame_length, exception_on_overflow=False)
                        pcm_unpacked = struct.unpack_from("h" * self.porcupine.frame_length, pcm)
                        if self.porcupine.process(pcm_unpacked) >= 0:
                            asyncio.run_coroutine_threadsafe(wake_queue.put(True), loop)
                    except Exception as e:
                        logger.debug("[daemon] wake read : %s", e)
                stream.stop_stream()
                stream.close()
                pa.terminate()
                self.porcupine.delete()
            except Exception as e:
                logger.exception("[daemon] wake word boucle bloquante : %s", e)

        # Démarre le thread bloquant en arrière-plan
        await loop.run_in_executor(None, lambda: None)  # warm-up
        thread_future = loop.run_in_executor(None, _blocking_loop)

        while self.running:
            try:
                detected = await asyncio.wait_for(wake_queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            if detected and self.mode == "veille":
                logger.info("[daemon] wake word détecté !")
                await self._start_conversation()

        try:
            thread_future.cancel()
        except Exception:
            pass

    async def _start_conversation(self) -> None:
        """Bascule en mode conversation après wake word."""
        self.mode = "conversation"
        self.conversation_active = True
        self.conversation_id = create_conversation(agent="daemon_voice")
        await self.tts_queue.put("Oui Monsieur, je vous écoute.")

        try:
            while self.conversation_active and self.running:
                audio_bytes = await self._listen_with_vad(timeout=15)
                if not audio_bytes:
                    await self.tts_queue.put("Bien Monsieur. Je reste en veille.")
                    break

                from audio.stt import stt as _stt

                if not _stt:
                    break
                stt_payload = prepare_stt_bytes(audio_bytes, sample_rate=16000)
                try:
                    text = await _stt.transcribe(stt_payload)
                except Exception as e:
                    logger.warning("[daemon] STT échoué : %s", e)
                    text = ""

                if not text or len(text.strip()) < 3:
                    continue

                logger.info("[daemon] entendu : %s", text)
                lower = text.lower()
                if any(p in lower for p in config.END_PHRASES):
                    await self.tts_queue.put(
                        "Bien Monsieur, je reste en veille si vous avez besoin."
                    )
                    break

                try:
                    result = await process_voice_fast(text, self.conversation_id)
                except Exception as e:
                    logger.exception("[daemon] _process_voice_fast : %s", e)
                    await self.tts_queue.put("Désolé Monsieur, une erreur est survenue.")
                    break

                response_text = (result or {}).get("text") or ""
                if response_text.strip():
                    await self.tts_queue.put(response_text.strip())

        except Exception as e:
            logger.exception("[daemon] erreur conversation : %s", e)
            await self.tts_queue.put("Désolé Monsieur, une erreur est survenue.")

        self.mode = "veille"
        self.conversation_active = False
        self.conversation_id = None

    async def _listen_with_vad(self, timeout: int = 15) -> bytes | None:
        """Capture micro avec VAD volume basique. Retourne bytes ou None.

        L'enregistrement tourne dans un thread pyaudio (bloquant).
        Retourne du PCM 16 kHz mono ; encapsulé en WAV avant STT.
        """
        try:
            import pyaudio  # type: ignore[import-not-found]
        except ImportError:
            logger.warning("[daemon] pyaudio non installé — listen impossible")
            return None

        loop = asyncio.get_running_loop()

        def _blocking() -> bytes | None:
            try:
                rate = 16000
                chunk = 1024
                silence_limit_s = 1.5
                pa = pyaudio.PyAudio()
                stream = pa.open(
                    rate=rate, channels=1, format=pyaudio.paInt16,
                    input=True, frames_per_buffer=chunk,
                )
                frames: list[bytes] = []
                silent_chunks = 0
                has_speech = False
                max_chunks = int(timeout * rate / chunk)
                silence_chunks_limit = int(silence_limit_s * rate / chunk)

                for _ in range(max_chunks):
                    if not self.running or not self.conversation_active:
                        break
                    data = stream.read(chunk, exception_on_overflow=False)
                    frames.append(data)
                    samples = struct.unpack(f"{chunk}h", data)
                    volume = max(abs(s) for s in samples)
                    if volume > 500:
                        has_speech = True
                        silent_chunks = 0
                    else:
                        silent_chunks += 1
                    if has_speech and silent_chunks >= silence_chunks_limit:
                        break

                stream.stop_stream()
                stream.close()
                pa.terminate()
                return b"".join(frames) if has_speech else None
            except Exception as e:
                logger.warning("[daemon] listen erreur : %s", e)
                return None

        return await loop.run_in_executor(None, _blocking)

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
