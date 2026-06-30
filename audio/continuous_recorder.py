"""Enregistrement continu — accumulation audio → transcription Scribe → Haiku + Sonnet → actions."""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from pathlib import Path

import config
import llm
from database import add_fact, create_task, save_episode, save_recording, upsert_person

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent
PROMPT_EXTRACTOR = BASE_DIR / "prompts" / "continuous_extractor.txt"
PROMPT_SYNTH = BASE_DIR / "prompts" / "continuous_synthesizer.txt"

JSON_BLOCK_RE = re.compile(r"```json\s*\n(.*?)\n```", re.DOTALL | re.IGNORECASE)

CHUNK_CHARS = 12000  # ~3000 tokens FR
WARN_BYTES = 100 * 1024 * 1024


def _parse_json_response(raw: str) -> dict | None:
    if not raw:
        return None
    raw = raw.strip()
    m = JSON_BLOCK_RE.search(raw)
    payload = m.group(1).strip() if m else raw
    if not payload.startswith("{"):
        s, e = payload.find("{"), payload.rfind("}")
        if s != -1 and e > s:
            payload = payload[s : e + 1]
    try:
        out = json.loads(payload)
        return out if isinstance(out, dict) else None
    except json.JSONDecodeError:
        logger.warning("[recording] JSON invalide dans la réponse LLM")
        return None


def _dedupe_str_list(items: list, cap: int = 80) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for x in items:
        if not isinstance(x, str):
            continue
        t = x.strip()
        if not t:
            continue
        k = t.lower()
        if k not in seen:
            seen.add(k)
            out.append(t)
        if len(out) >= cap:
            break
    return out


def _merge_extractor_parts(parts: list[dict]) -> dict:
    keys = (
        "key_points",
        "action_items",
        "dates_mentioned",
        "people_mentioned",
        "facts_learned",
        "decisions_made",
        "questions_unanswered",
    )
    acc: dict = {k: [] for k in keys}
    tones: list[str] = []
    for p in parts:
        if not isinstance(p, dict):
            continue
        for k in keys:
            v = p.get(k)
            if isinstance(v, list):
                acc[k].extend(v)
        mt = p.get("mood_tone")
        if isinstance(mt, str) and mt.strip():
            tones.append(mt.strip())

    acc["key_points"] = _dedupe_str_list(
        [x if isinstance(x, str) else str(x) for x in acc["key_points"]],
        60,
    )
    seen_t: set[str] = set()
    ai = []
    for it in acc["action_items"]:
        if isinstance(it, dict) and it.get("title"):
            tt = str(it["title"]).strip().lower()
            if tt and tt not in seen_t:
                seen_t.add(tt)
                ai.append(it)
    acc["action_items"] = ai[:40]

    mood = tones[-1] if tones else "productif"
    return {
        **acc,
        "mood_tone": mood,
    }


def _stt_timeout_for_bytes(n: int) -> float:
    return min(600.0, max(45.0, 35.0 + n / (400 * 1024)))


ProgressFn = Callable[[str, dict[str, object]], Awaitable[None]] | None


class ContinuousRecording:
    """Session d'écoute continue : chunks audio → traitement à l'arrêt."""

    def __init__(self, conversation_id: int) -> None:
        self.conversation_id = conversation_id
        self.audio_chunks: list[bytes] = []
        self.started_at = datetime.now()
        self.ended_at: datetime | None = None
        self.total_bytes = 0
        self.is_active = False
        self.label = ""
        self.transcription = ""
        self.synthesis: dict | None = None
        self._last_log_wall = time.monotonic()

    def add_chunk(self, audio_bytes: bytes) -> None:
        if not self.is_active:
            return
        self.audio_chunks.append(audio_bytes)
        self.total_bytes += len(audio_bytes)
        now = time.monotonic()
        if now - self._last_log_wall >= 60.0:
            self._last_log_wall = now
            logger.info(
                "[recording] En cours — %.0f KB accumulés",
                self.total_bytes / 1024.0,
            )

    async def stop_and_process(self, progress: ProgressFn = None) -> dict:
        """Transcrit, synthétise, applique les actions, persiste."""
        self.is_active = False
        self.ended_at = datetime.now()
        duration_sec = int((self.ended_at - self.started_at).total_seconds())
        duration_min = duration_sec / 60.0

        if duration_min > config.RECORDING_MAX_DURATION_MIN:
            return {
                "ok": False,
                "error": (
                    f"Durée maximale dépassée ({config.RECORDING_MAX_DURATION_MIN} min). "
                    "Découpe l'enregistrement et relance."
                ),
                "duration_seconds": duration_sec,
                "label": self.label,
            }

        if self.total_bytes < 3000:
            return {
                "ok": False,
                "error": "Audio trop court pour être transcrit.",
                "duration_seconds": duration_sec,
                "label": self.label,
            }

        if self.total_bytes > WARN_BYTES:
            logger.warning(
                "[recording] Fichier audio très volumineux (%.1f MB) — transcription potentiellement longue.",
                self.total_bytes / (1024 * 1024),
            )

        try:
            from audio.stt import stt
        except ImportError:
            stt = None  # type: ignore[misc, assignment]

        if stt is None or not getattr(stt, "available", False):
            return {"ok": False, "error": "STT indisponible.", "duration_seconds": duration_sec}

        self.transcription = await self._transcribe_all(stt, progress)
        if not self.transcription.strip():
            return {
                "ok": False,
                "error": "Transcription vide.",
                "duration_seconds": duration_sec,
                "label": self.label,
            }

        if progress:
            await progress("recording_analyzing", {"message": "Analyse en cours…"})

        self.synthesis = await self._synthesize(self.transcription, duration_sec)
        action_results = await self._apply_synthesis(self.synthesis)

        title = (self.synthesis or {}).get("title") or self.label or "Enregistrement"
        summary_text = (self.synthesis or {}).get("summary") or ""

        rec_id = save_recording(
            conversation_id=self.conversation_id,
            label=self.label,
            duration_seconds=duration_sec,
            transcription=self.transcription,
            summary=summary_text,
            synthesis=self.synthesis or {},
            actions=action_results,
            audio_size_kb=max(1, int(self.total_bytes / 1024)),
            title=title,
        )

        out = {
            "ok": True,
            "recording_id": rec_id,
            "title": title,
            "summary": summary_text,
            "duration_seconds": duration_sec,
            "tasks_created": action_results.get("tasks_created", 0),
            "events_created": action_results.get("events_created", 0),
            "facts_stored": action_results.get("facts_stored", 0),
            "people_updated": action_results.get("people_updated", 0),
            "synthesis": self.synthesis,
            "actions": action_results,
            "label": self.label,
        }
        if not config.RECORDING_SUMMARY_ONLY:
            out["transcription"] = self.transcription
        return out

    async def _transcribe_all(self, stt, progress: ProgressFn) -> str:
        """Transcrit chaque chunk média séquentiellement (chaque morceau = WebM valide)."""
        parts_text: list[str] = []
        chunks = [c for c in self.audio_chunks if len(c) >= 800]
        if not chunks:
            chunks = self.audio_chunks

        mb_limit = max(1, config.RECORDING_CHUNK_SIZE_MB) * 1024 * 1024
        segment_units: list[bytes] = []
        for c in chunks:
            if len(c) <= mb_limit:
                segment_units.append(c)
            else:
                # WebM ne supporte pas la découpe arbitraire — segment entier ou skip
                logger.warning(
                    "[recording] Chunk WebM %d Mo > limite %d Mo — transcription entière",
                    len(c) // (1024 * 1024),
                    config.RECORDING_CHUNK_SIZE_MB,
                )
                segment_units.append(c)

        n = len(segment_units)
        for i, seg in enumerate(segment_units, start=1):
            if progress:
                await progress(
                    "recording_transcribing",
                    {"progress": f"segment {i}/{n}"},
                )
            logger.info("[recording] Transcription segment %d/%d (%d bytes)", i, n, len(seg))
            to = _stt_timeout_for_bytes(len(seg))
            try:
                txt = await stt.transcribe(seg, language=config.LANGUAGE, timeout=to)
            except Exception as e:
                logger.exception("[recording] Erreur STT segment %d : %s", i, e)
                txt = ""
            if txt and txt.strip():
                parts_text.append(txt.strip())

        return "\n\n".join(parts_text)

    async def _synthesize(self, transcription: str, duration_sec: int) -> dict:
        dur_human = f"{duration_sec // 3600}h {(duration_sec % 3600) // 60}min" if duration_sec >= 3600 else f"{duration_sec // 60} min {duration_sec % 60}s"
        segments = []
        step = CHUNK_CHARS
        for i in range(0, len(transcription), step):
            segments.append(transcription[i : i + step])
        if not segments:
            segments = [transcription]

        extractor_tpl = PROMPT_EXTRACTOR.read_text(encoding="utf-8")
        partials: list[dict] = []
        total = len(segments)

        for idx, chunk in enumerate(segments, start=1):
            prompt = (
                extractor_tpl.replace("{{label}}", self.label or "Sans titre")
                .replace("{{duration}}", dur_human)
                .replace("{{segment_num}}", str(idx))
                .replace("{{total_segments}}", str(total))
                .replace("{{chunk}}", chunk)
            )
            try:
                r = await llm.chat(
                    messages=[{"role": "user", "content": prompt}],
                    model=config.DEEPSEEK_FAST_MODEL,
                    system="Tu réponds uniquement par un objet JSON valide, sans markdown.",
                    max_tokens=4096,
                    temperature=0.2,
                    use_cache=False,
                )
                p = _parse_json_response(r.get("content", ""))
                if p:
                    partials.append(p)
            except Exception as e:
                logger.exception("[recording] Extraction Haiku segment %d : %s", idx, e)

        aggregated = _merge_extractor_parts(partials)
        agg_txt = json.dumps(aggregated, ensure_ascii=False, indent=2)

        synth_tpl = PROMPT_SYNTH.read_text(encoding="utf-8")
        synth_prompt = (
            synth_tpl.replace("{{duration}}", dur_human)
            .replace("{{label}}", self.label or "Sans titre")
            .replace("{{aggregated_data}}", agg_txt[:120_000])
        )

        try:
            r2 = await llm.chat(
                messages=[{"role": "user", "content": synth_prompt}],
                model=config.DEEPSEEK_MAIN_MODEL,
                system="Tu réponds uniquement par un objet JSON valide.",
                max_tokens=8192,
                temperature=0.4,
                use_cache=False,
            )
            syn = _parse_json_response(r2.get("content", ""))
            if syn:
                return syn
        except Exception as e:
            logger.exception("[recording] Synthèse Sonnet : %s", e)

        return {
            "title": self.label or "Enregistrement",
            "summary": transcription[:4000],
            "tasks": [],
            "calendar_events": [],
            "facts": [],
            "people": [],
            "patterns_observed": [],
        }

    async def _apply_synthesis(self, synthesis: dict) -> dict:
        results = {
            "tasks_created": 0,
            "events_created": 0,
            "facts_stored": 0,
            "people_updated": 0,
        }

        for task in synthesis.get("tasks") or []:
            if not isinstance(task, dict) or not task.get("title"):
                continue
            try:
                cat = task.get("category") or "perso"
                if isinstance(cat, str):
                    c = cat.lower().strip()
                    if c == "personal":
                        c = "perso"
                    if c not in ("school", "work", "perso"):
                        c = "perso"
                else:
                    c = "perso"
                create_task(
                    title=str(task["title"])[:500],
                    priority=task.get("priority") or "medium",
                    due_date=task.get("due_date"),
                    category=c,
                )
                results["tasks_created"] += 1
            except Exception as e:
                logger.exception("[recording] Tâche : %s", e)

        try:
            from integrations.calendar_api import calendar_client
        except ImportError:
            calendar_client = None  # type: ignore[misc, assignment]

        for event in synthesis.get("calendar_events") or []:
            if not isinstance(event, dict) or not event.get("summary") or not event.get("date"):
                continue
            try:
                if not calendar_client or not calendar_client.is_available():
                    logger.warning("[recording] Calendar indisponible — événement ignoré")
                    continue
                d = str(event["date"]).strip()[:10]
                tm = event.get("time")
                if tm is None or str(tm).lower() in ("null", ""):
                    tm = "09:00"
                tm = str(tm).strip()[:5]
                start_s = f"{d} {tm}"
                start_dt = datetime.strptime(start_s, "%Y-%m-%d %H:%M")
                dur_m = int(event.get("duration_min") or 60)
                end_dt = start_dt + timedelta(minutes=max(15, dur_m))
                end_str = end_dt.strftime("%Y-%m-%d %H:%M")
                r = await calendar_client.create_event(
                    summary=str(event["summary"])[:200],
                    start_date=start_s,
                    end_date=end_str,
                    location="",
                    notes="Créé depuis un enregistrement JARVIS",
                )
                if r.get("ok"):
                    results["events_created"] += 1
            except Exception as e:
                logger.exception("[recording] Calendrier : %s", e)

        for fact in synthesis.get("facts") or []:
            if not isinstance(fact, dict) or not fact.get("content"):
                continue
            try:
                add_fact(
                    category=str(fact.get("category") or "work"),
                    content=str(fact["content"])[:2000],
                    source="recording",
                    confidence="medium",
                )
                results["facts_stored"] += 1
            except Exception as e:
                logger.exception("[recording] Fait : %s", e)

        for person in synthesis.get("people") or []:
            if not isinstance(person, dict) or not person.get("name"):
                continue
            try:
                notes = str(person.get("notes") or "")[:4000]
                upsert_person(
                    str(person["name"]).strip()[:200],
                    relationship=str(person.get("role") or "")[:500] or None,
                    personality_notes=notes or None,
                )
                results["people_updated"] += 1
            except Exception as e:
                logger.exception("[recording] Personne : %s", e)

        try:
            summ = synthesis.get("summary") or ""
            title = synthesis.get("title") or self.label or "Enregistrement"
            tags = ["recording", (self.label or "")[:80]]
            save_episode(
                agent="recording",
                content=summ[:8000],
                summary=title[:500],
                importance=7,
                tags=tags,
            )
        except Exception as e:
            logger.exception("[recording] Épisode : %s", e)

        if config.DESKTOP_NOTIFICATIONS:
            try:
                from integrations.notifications_macos import mac_notifier

                await mac_notifier.notify(
                    title="JARVIS — Enregistrement traité",
                    message=f"{synthesis.get('title', 'Enregistrement')} — {results['tasks_created']} tâches, {results['events_created']} événements",
                    sound=config.NOTIFICATION_SOUND or "Glass",
                )
            except Exception as e:
                logger.exception("[recording] Notification : %s", e)

        return results
