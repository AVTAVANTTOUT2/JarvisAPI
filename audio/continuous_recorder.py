"""Enregistrement continu — audio → transcription locale → analyse et actions."""

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
from audio.recording_spool import (
    RECORDING_PROCESSING_MAX_ATTEMPTS,
    RecordingSpool,
    purge_expired_recordings,
    reconcile_recording_sessions,
)
from database import (
    claim_recording_desktop_notification,
    get_recording_session,
    save_episode,
    save_recording,
    update_recording_session,
)
from jarvis.ingestion.models import IngestionJob, IngestionRunResult
from jarvis.security.llm_data_boundary import (
    UNTRUSTED_DATA_SYSTEM_RULE,
    redact_for_external_llm,
    wrap_untrusted_data,
)

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


class RecordingProcessingError(RuntimeError):
    """Erreur classée du worker, sans contenu audio dans le message."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class ContinuousRecording:
    """Session d'écoute continue : chunks audio → traitement à l'arrêt."""

    def __init__(self, conversation_id: int | None) -> None:
        self.conversation_id = conversation_id
        # Compatibilité des tests/anciens appels sans spool. Une capture durable
        # ne remplit jamais cette liste : l'audio reste borné à un segment.
        self.audio_chunks: list[bytes] = []
        self.started_at = datetime.now()
        self.ended_at: datetime | None = None
        self.total_bytes = 0
        self.is_active = False
        self.label = ""
        self.transcription = ""
        self.synthesis: dict | None = None
        self.spool: RecordingSpool | None = None
        self._last_log_wall = time.monotonic()

    def start(self, label: str = "Enregistrement") -> str:
        """Persiste la session avant d'autoriser le premier chunk audio."""

        if self.spool is not None:
            raise RuntimeError("recording_already_started")
        self.label = str(label or "Enregistrement").strip()[:200]
        self.spool = RecordingSpool.create(
            conversation_id=self.conversation_id,
            label=self.label,
        )
        self.is_active = True
        return self.spool.session_id

    def add_chunk(self, audio_bytes: bytes) -> None:
        if not self.is_active:
            return
        if self.spool is None:
            self.spool = RecordingSpool.create(
                conversation_id=self.conversation_id,
                label=self.label or "Enregistrement",
            )
        self.spool.append(audio_bytes)
        self.total_bytes += len(audio_bytes)
        now = time.monotonic()
        if now - self._last_log_wall >= 60.0:
            self._last_log_wall = now
            logger.info(
                "[recording] En cours — %.0f KB accumulés",
                self.total_bytes / 1024.0,
            )

    def queue_for_processing(
        self,
        *,
        duration_seconds: int | None = None,
        expected_chunks: int | None = None,
    ) -> dict:
        """Scelle le spool puis enfile le traitement idempotent."""

        self.is_active = False
        self.ended_at = datetime.now()
        measured_duration = int((self.ended_at - self.started_at).total_seconds())
        duration_sec = (
            max(0, int(duration_seconds))
            if duration_seconds is not None
            else measured_duration
        )
        if self.spool is None:
            return {
                "ok": False,
                "error": "Aucun audio reçu.",
                "duration_seconds": duration_sec,
                "label": self.label,
            }
        if expected_chunks is not None and int(expected_chunks) != self.spool.chunk_count:
            return {
                "ok": False,
                "error": "recording_chunks_incomplete",
                "expected_chunks": int(expected_chunks),
                "received_chunks": self.spool.chunk_count,
                "duration_seconds": duration_sec,
                "label": self.label,
            }
        if self.spool.duration_ms > 0:
            duration_sec = (self.spool.duration_ms + 999) // 1000
        if duration_sec > config.RECORDING_MAX_DURATION_MIN * 60:
            self.spool.mark_failed("recording_duration_exceeded", terminal=True)
            return {
                "ok": False,
                "error": f"Durée maximale dépassée ({config.RECORDING_MAX_DURATION_MIN} min).",
                "duration_seconds": duration_sec,
                "label": self.label,
            }
        if self.total_bytes < 3000:
            self.spool.mark_failed("recording_too_short", terminal=True)
            return {
                "ok": False,
                "error": "Audio trop court pour être transcrit.",
                "duration_seconds": duration_sec,
                "label": self.label,
            }
        session_id = self.spool.enqueue(
            label=self.label,
            duration_seconds=duration_sec,
        )
        return {
            "ok": True,
            "queued": True,
            "session_id": session_id,
            "duration_seconds": duration_sec,
            "label": self.label,
        }

    @classmethod
    def from_spool(
        cls,
        session_id: str,
        *,
        duration_seconds: int | None = None,
    ) -> "ContinuousRecording":
        session = get_recording_session(session_id)
        if session is None:
            raise LookupError("recording_session_not_found")
        spool = RecordingSpool.open(session_id)
        recording = cls(session.conversation_id)
        recording.label = session.label
        recording.spool = spool
        recording.total_bytes = spool.size_bytes
        if session.created_at:
            recording.started_at = datetime.fromisoformat(
                session.created_at.replace("Z", "+00:00")
            ).replace(tzinfo=None)
        if duration_seconds is not None:
            recording.ended_at = recording.started_at + timedelta(
                seconds=max(0, int(duration_seconds))
            )
        return recording

    async def stop_and_process(self, progress: ProgressFn = None) -> dict:
        """Traite un spool déjà durable (appelé par le service ingestion)."""
        self.is_active = False
        if self.ended_at is None:
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
            from audio import stt
        except ImportError:
            stt = None  # type: ignore[misc, assignment]

        if stt is None or not getattr(stt, "available", False):
            raise RecordingProcessingError("recording_stt_unavailable")

        self._raise_if_cancelled()
        self.transcription = await self._transcribe_all(stt, progress)
        if not self.transcription.strip():
            raise RecordingProcessingError("recording_transcription_empty")

        if progress:
            await progress("recording_analyzing", {"message": "Analyse en cours…"})

        self._raise_if_cancelled()
        self.synthesis = await self._synthesize(self.transcription, duration_sec)
        proposal_results = self._proposal_summary(self.synthesis)

        title = (self.synthesis or {}).get("title") or self.label or "Enregistrement"
        summary_text = (self.synthesis or {}).get("summary") or ""

        self._raise_if_cancelled()
        rec_id = save_recording(
            conversation_id=self.conversation_id,
            label=self.label,
            duration_seconds=duration_sec,
            transcription=self.transcription,
            summary=summary_text,
            synthesis=self.synthesis or {},
            actions=proposal_results,
            audio_size_kb=max(1, int(self.total_bytes / 1024)),
            title=title,
            recording_session_id=self.spool.session_id if self.spool else None,
        )

        # Les propositions et dérivés ne sont publiés qu'après la persistance
        # canonique de l'enregistrement.
        await self._apply_synthesis(
            self.synthesis,
            recording_id=rec_id,
            session_id=self.spool.session_id if self.spool else None,
        )
        turns_captured = await self._maybe_capture_turns(stt, rec_id)
        if self.spool is not None:
            self.spool.mark_succeeded(
                transcript=self.transcription,
                summary=summary_text,
            )

        out = {
            "ok": True,
            "turns_captured": turns_captured,
            "recording_id": rec_id,
            "title": title,
            "summary": summary_text,
            "duration_seconds": duration_sec,
            "tasks_created": 0,
            "events_created": 0,
            "tasks_proposed": proposal_results.get("tasks_proposed", 0),
            "events_proposed": proposal_results.get("events_proposed", 0),
            "synthesis": self.synthesis,
            "actions": proposal_results,
            "label": self.label,
        }
        if not config.RECORDING_SUMMARY_ONLY:
            out["transcription"] = self.transcription
        return out

    async def _transcribe_all(self, stt, progress: ProgressFn) -> str:
        """Transcrit chaque chunk média séquentiellement (chaque morceau = WebM valide)."""
        parts_text: list[str] = []
        mb_limit = max(1, config.RECORDING_CHUNK_SIZE_MB) * 1024 * 1024
        if self.spool is not None:
            paths = [
                path for path in self.spool.chunk_paths() if path.stat().st_size >= 800
            ]
            if not paths:
                paths = self.spool.chunk_paths()
            n = len(paths)
            segments = ((path.read_bytes(), index) for index, path in enumerate(paths, 1))
        else:
            chunks = [chunk for chunk in self.audio_chunks if len(chunk) >= 800]
            if not chunks:
                chunks = self.audio_chunks
            n = len(chunks)
            segments = ((chunk, index) for index, chunk in enumerate(chunks, 1))

        failed_segments: list[int] = []
        for seg, i in segments:
            self._raise_if_cancelled()
            if progress:
                await progress(
                    "recording_transcribing",
                    {"progress": f"segment {i}/{n}"},
                )
            logger.info(
                "[recording] Transcription segment %d/%d (%d bytes)", i, n, len(seg)
            )
            if len(seg) > mb_limit:
                raise RecordingProcessingError("recording_chunk_too_large")
            to = _stt_timeout_for_bytes(len(seg))
            try:
                txt = await stt.transcribe(seg, language=config.LANGUAGE, timeout=to)
            except TimeoutError as exc:
                raise RecordingProcessingError("recording_stt_timeout") from exc
            except Exception as e:
                logger.exception("[recording] Erreur STT segment %d : %s", i, e)
                txt = ""
            if txt and txt.strip():
                parts_text.append(txt.strip())
            else:
                failed_segments.append(i)

        if failed_segments:
            raise RecordingProcessingError("recording_stt_partial")

        return "\n\n".join(parts_text)

    async def _maybe_capture_turns(self, stt, recording_id: int) -> int:
        """Capture les tours de parole diarisés en UN SEUL appel STT.

        Les labels « A »/« B » ne sont cohérents qu'au sein d'un même appel —
        d'où un appel unique sur l'audio entier. Plusieurs chunks issus d'un
        même ``MediaRecorder`` sont des fragments d'un seul flux WebM : leur
        concaténation est le fichier complet valide. Si l'audio concaténé
        n'était pas exploitable (chunks indépendants, flux corrompu), le moteur
        local échoue et on dégrade proprement (0 tour, transcription classique
        déjà persistée par ailleurs).
        """
        if not config.DIARIZATION_ENABLED:
            return 0
        if self.spool is not None:
            paths = [
                path for path in self.spool.chunk_paths() if path.stat().st_size >= 800
            ] or self.spool.chunk_paths()
            # Les segments indépendants n'ont pas une chronologie de labels
            # garantie entre appels. Ne jamais les concaténer en RAM.
            if len(paths) != 1:
                logger.info(
                    "[recording] Diarisation ignorée — enregistrement segmenté (%d segments)",
                    len(paths),
                )
                return 0
            audio = paths[0].read_bytes()
        else:
            chunks = [c for c in self.audio_chunks if len(c) >= 800] or self.audio_chunks
            if not chunks:
                return 0
            audio = chunks[0] if len(chunks) == 1 else b"".join(chunks)
        if len(audio) > WARN_BYTES:
            logger.info(
                "[recording] Diarisation ignorée — audio trop volumineux (%.0f MB)",
                len(audio) / (1024 * 1024),
            )
            return 0
        try:
            turns = await stt.transcribe_with_diarization(
                audio,
                language=config.LANGUAGE,
                timeout=_stt_timeout_for_bytes(len(audio)),
            )
        except Exception as e:
            logger.warning("[recording] Diarisation échouée : %s", e)
            return 0
        if not turns:
            return 0
        from database import save_conversation_turns

        return save_conversation_turns(recording_id, turns)

    def _raise_if_cancelled(self) -> None:
        if self.spool is None:
            return
        session = get_recording_session(self.spool.session_id)
        if session is not None and session.error == "recording_cancelled":
            raise RecordingProcessingError("recording_cancelled")

    async def _synthesize(self, transcription: str, duration_sec: int) -> dict:
        dur_human = (
            f"{duration_sec // 3600}h {(duration_sec % 3600) // 60}min"
            if duration_sec >= 3600
            else f"{duration_sec // 60} min {duration_sec % 60}s"
        )
        segments = []
        step = CHUNK_CHARS
        for i in range(0, len(transcription), step):
            segments.append(transcription[i : i + step])
        if not segments:
            segments = [transcription]

        extractor_tpl = PROMPT_EXTRACTOR.read_text(encoding="utf-8")
        partials: list[dict] = []
        total = len(segments)
        safe_label = redact_for_external_llm(self.label or "Sans titre", max_chars=200)

        for idx, chunk in enumerate(segments, start=1):
            prompt = (
                extractor_tpl.replace("{{label}}", safe_label)
                .replace("{{duration}}", dur_human)
                .replace("{{segment_num}}", str(idx))
                .replace("{{total_segments}}", str(total))
                .replace(
                    "{{chunk}}",
                    wrap_untrusted_data(
                        "TRANSCRIPTION",
                        chunk,
                        max_chars=CHUNK_CHARS,
                    ),
                )
            )
            try:
                r = await llm.chat(
                    messages=[{"role": "user", "content": prompt}],
                    model=config.DEEPSEEK_FAST_MODEL,
                    system=(
                        UNTRUSTED_DATA_SYSTEM_RULE
                        + "\nTu réponds uniquement par un objet JSON valide, sans markdown."
                    ),
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
            .replace("{{label}}", safe_label)
            .replace(
                "{{aggregated_data}}",
                wrap_untrusted_data(
                    "TRANSCRIPTION_EXTRACTION",
                    agg_txt,
                    max_chars=120_000,
                ),
            )
        )

        try:
            r2 = await llm.chat(
                messages=[{"role": "user", "content": synth_prompt}],
                model=config.DEEPSEEK_MAIN_MODEL,
                system=(
                    UNTRUSTED_DATA_SYSTEM_RULE
                    + "\nTu réponds uniquement par un objet JSON valide."
                ),
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

    @staticmethod
    def _proposal_summary(synthesis: dict) -> dict:
        tasks = [
            task
            for task in (synthesis.get("tasks") or [])
            if isinstance(task, dict) and task.get("title")
        ]
        events = [
            event
            for event in (synthesis.get("calendar_events") or [])
            if isinstance(event, dict) and event.get("summary")
        ]
        return {
            "requires_approval": True,
            "tasks_proposed": len(tasks),
            "events_proposed": len(events),
            "task_proposals": tasks[:20],
            "event_proposals": events[:20],
        }

    async def _apply_synthesis(
        self,
        synthesis: dict,
        *,
        recording_id: int,
        session_id: str | None,
    ) -> dict:
        """Publie uniquement des propositions ; aucun effet externe implicite."""

        proposals = self._proposal_summary(synthesis)
        title = str(synthesis.get("title") or self.label or "Enregistrement")[:500]
        summary = str(synthesis.get("summary") or "")[:8000]
        try:
            save_episode(
                agent="recording",
                content=summary,
                summary=title,
                importance=7,
                tags=["recording", (self.label or "")[:80]],
                recording_id=recording_id,
            )
        except Exception as exc:
            logger.exception("[recording] Épisode dérivé : %s", exc)

        if proposals["tasks_proposed"] or proposals["events_proposed"]:
            from jarvis.notification_service import notification_service

            notification_service.create(
                source="recording",
                title=f"Propositions depuis « {title} »",
                content=(
                    f"{proposals['tasks_proposed']} tâche(s) et "
                    f"{proposals['events_proposed']} événement(s) à valider."
                ),
                priority="medium",
                idempotency_key=f"recording:{session_id or recording_id}:proposals",
            )

        if config.DESKTOP_NOTIFICATIONS and session_id is not None:
            try:
                from integrations.notifications_macos import mac_notifier

                if claim_recording_desktop_notification(session_id):
                    await mac_notifier.notify(
                        title="JARVIS — Enregistrement traité",
                        message=(
                            f"{title} — {proposals['tasks_proposed']} tâche(s), "
                            f"{proposals['events_proposed']} événement(s) proposé(s)"
                        ),
                        sound=config.NOTIFICATION_SOUND or "Glass",
                    )
            except Exception as exc:
                logger.exception("[recording] Notification : %s", exc)
        return proposals


async def process_recording_ingestion_job(
    job: IngestionJob,
    _binding,
    _state,
) -> IngestionRunResult:
    """Handler durable appelé uniquement sous le profil capturé par le worker."""

    session_id = str(job.payload.get("session_id") or "").strip()
    if not session_id:
        raise ValueError("recording_session_id_required")
    session = get_recording_session(session_id)
    if session is None:
        raise LookupError("recording_session_not_found")
    if session.error == "recording_cancelled":
        return IngestionRunResult(
            status="ok",
            item_count=0,
            completeness="complete",
        )
    if session.state in {"completed", "ready"}:
        if session.state == "ready":
            update_recording_session(session_id, state="completed", error=None)
        return IngestionRunResult(
            status="ok",
            item_count=1,
            completeness="complete",
        )
    duration_seconds = int(job.payload.get("duration_seconds") or 0)
    recording = ContinuousRecording.from_spool(
        session_id,
        duration_seconds=duration_seconds,
    )
    update_recording_session(session_id, state="processing", error=None)
    try:
        result = await recording.stop_and_process()
        if not result.get("ok"):
            raw_error = str(result.get("error") or "recording_processing_failed")
            code = (
                raw_error
                if raw_error.startswith("recording_")
                else "recording_processing_failed"
            )
            raise RecordingProcessingError(code)
    except Exception as exc:
        refreshed = get_recording_session(session_id)
        if refreshed is not None and refreshed.error == "recording_cancelled":
            return IngestionRunResult(
                status="ok",
                item_count=0,
                completeness="complete",
            )
        assert recording.spool is not None
        error_code = (
            exc.code
            if isinstance(exc, RecordingProcessingError)
            else f"recording_{type(exc).__name__.casefold()}"
        )
        recording.spool.mark_failed(
            error_code,
            terminal=(
                job.attempts >= min(job.max_attempts, RECORDING_PROCESSING_MAX_ATTEMPTS)
            ),
        )
        raise
    return IngestionRunResult(
        status="ok",
        item_count=1,
        completeness="complete",
    )


def register_recording_ingestion_handler() -> None:
    from jarvis.ingestion.service import (
        register_ingestion_handler,
        register_ingestion_maintenance_hook,
    )

    register_ingestion_handler(
        "recording",
        "recording_process",
        process_recording_ingestion_job,
        replace=True,
    )
    register_ingestion_maintenance_hook(
        "recording-reconcile",
        reconcile_recording_sessions,
        replace=True,
    )
    register_ingestion_maintenance_hook(
        "recording-retention",
        purge_expired_recordings,
        replace=True,
    )
