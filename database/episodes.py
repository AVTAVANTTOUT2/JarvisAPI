"""Mémoire épisodique, enregistrements et résumés hebdomadaires."""

from __future__ import annotations

import json
import logging

from jarvis.event_bus import event_bus
from jarvis.events import EpisodeSaved

from .core import current_profile_id, get_db, use_profile

logger = logging.getLogger(__name__)


def save_episode(
    agent: str,
    content: str,
    summary: str = None,
    importance: int = 5,
    tags: list = None,
    *,
    recording_id: int | None = None,
) -> int:
    created = True
    with get_db() as conn:
        if recording_id is None:
            cur = conn.execute(
                """INSERT INTO episodes (agent, content, summary, importance, tags)
                   VALUES (?, ?, ?, ?, ?)""",
                (agent, content, summary, importance, json.dumps(tags or [])),
            )
            episode_id = int(cur.lastrowid)
        else:
            row = conn.execute(
                """
                INSERT INTO episodes(
                    recording_id, agent, content, summary, importance, tags
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT DO NOTHING
                RETURNING id
                """,
                (
                    int(recording_id),
                    agent,
                    content,
                    summary,
                    importance,
                    json.dumps(tags or []),
                ),
            ).fetchone()
            created = row is not None
            if row is None:
                row = conn.execute(
                    "SELECT id FROM episodes WHERE recording_id = ?",
                    (int(recording_id),),
                ).fetchone()
            if row is None:
                raise RuntimeError("recording_episode_idempotency_failed")
            episode_id = int(row["id"])
    from . import _dispatch_semantic_indexing as dispatch_semantic_indexing

    if created:
        dispatch_semantic_indexing("episode", episode_id, summary or content)
        event_bus.emit_nowait(
            EpisodeSaved(int(episode_id), summary or content[:160], importance)
        )
    return episode_id


def _dispatch_semantic_indexing(source_type: str, source_id: int, text: str) -> None:
    """Indexe un texte pour la recherche sémantique — arrière-plan, best-effort, jamais bloquant.

    Ne fait rien silencieusement si `sentence-transformers` n'est pas
    installé (dépendance lourde optionnelle) — jamais de crash appelant.
    """
    import threading

    profile_id = current_profile_id()

    def _index():
        with use_profile(profile_id):
            try:
                from scripts.semantic_search import (
                    SemanticSearchUnavailable,
                    index_text,
                )

                index_text(source_type, source_id, text)
            except SemanticSearchUnavailable:
                pass
            except Exception:
                logger.debug(
                    "[semantic_search] indexation échouée (best-effort)", exc_info=True
                )

    threading.Thread(target=_index, daemon=True).start()


def save_recording(
    conversation_id: int | None,
    label: str,
    duration_seconds: int,
    transcription: str,
    summary: str,
    synthesis: dict,
    actions: dict,
    audio_size_kb: int,
    title: str | None = None,
    *,
    recording_session_id: str | None = None,
) -> int:
    """Persiste un enregistrement continu (transcription + synthèse + actions)."""
    synthesis_json = (
        json.dumps(synthesis, ensure_ascii=False)
        if isinstance(synthesis, dict)
        else (synthesis or "")
    )
    actions_json = (
        json.dumps(actions, ensure_ascii=False)
        if isinstance(actions, dict)
        else (actions or "")
    )
    created = True
    with get_db() as conn:
        params = (
            recording_session_id,
            conversation_id,
            label,
            title,
            duration_seconds,
            transcription,
            summary,
            synthesis_json,
            actions_json,
            audio_size_kb,
        )
        row = conn.execute(
            """
            INSERT INTO recordings(
                recording_session_id, conversation_id, label, title,
                duration_seconds, transcription, summary, synthesis,
                actions_taken, audio_size_kb
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT DO NOTHING
            RETURNING id
            """,
            params,
        ).fetchone()
        created = row is not None
        if row is None and recording_session_id is not None:
            row = conn.execute(
                "SELECT id FROM recordings WHERE recording_session_id = ?",
                (recording_session_id,),
            ).fetchone()
        if row is None:
            raise RuntimeError("recording_idempotency_failed")
        rec_id = int(row["id"])
    from . import _dispatch_semantic_indexing as dispatch_semantic_indexing

    if created:
        dispatch_semantic_indexing("recording", rec_id, summary or transcription[:2000])
    return rec_id


def get_recordings(limit: int = 20) -> list:
    """Liste légère (pas de transcription complète dans les lignes — colonne summary uniquement)."""
    with get_db() as conn:
        rows = conn.execute(
            """SELECT id, label, title, duration_seconds, summary, actions_taken, created_at, audio_size_kb
               FROM recordings ORDER BY created_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
    out: list[dict] = []
    for r in rows:
        d = dict(r)
        acts: dict = {}
        raw = d.get("actions_taken")
        if raw and isinstance(raw, str):
            try:
                acts = json.loads(raw)
            except json.JSONDecodeError:
                pass
        d["tasks_created"] = int(acts.get("tasks_created", 0))
        d["events_created"] = int(acts.get("events_created", 0))
        d["facts_stored"] = int(acts.get("facts_stored", 0))
        d["people_updated"] = int(acts.get("people_updated", 0))
        d.pop("actions_taken", None)
        out.append(d)
    return out


def get_recording(recording_id: int) -> dict | None:
    """Détail complet, y compris transcription et JSONs parsés."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM recordings WHERE id = ?", (recording_id,)
        ).fetchone()
    if not row:
        return None
    d = dict(row)
    for k in ("synthesis", "actions_taken"):
        v = d.get(k)
        if v and isinstance(v, str):
            try:
                d[k] = json.loads(v)
            except json.JSONDecodeError:
                d[k] = None
    return d


def get_recent_episodes(agent: str = None, limit: int = 10) -> list:
    with get_db() as conn:
        if agent:
            rows = conn.execute(
                "SELECT * FROM episodes WHERE agent = ? ORDER BY created_at DESC LIMIT ?",
                (agent, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM episodes ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]


def get_weekly_episodes(days: int = 7) -> list:
    """Épisodes des N derniers jours."""
    with get_db() as conn:
        rows = conn.execute(
            f"""SELECT * FROM episodes
                WHERE created_at >= datetime('now', '-{int(days)} days')
                ORDER BY created_at DESC"""
        ).fetchall()
        return [dict(r) for r in rows]


def save_weekly_summary(
    week_start: str,
    summary: str,
    patterns_spotted: list = None,
    recommendations: list = None,
) -> int:
    with get_db() as conn:
        cur = conn.execute(
            """INSERT INTO weekly_summaries (week_start, summary, patterns_spotted, recommendations)
               VALUES (?, ?, ?, ?)""",
            (
                week_start,
                summary,
                json.dumps(patterns_spotted or []),
                json.dumps(recommendations or []),
            ),
        )
        return cur.lastrowid
