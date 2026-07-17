"""Moteur de briefings nouvelle génération — priorisation + version vocale."""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Literal

import config
import llm
from agents.display_text import finalize_assistant_display_text

logger = logging.getLogger(__name__)

Priority = Literal["critique", "aujourd_hui", "surveiller", "information"]


@dataclass
class BriefingItem:
    id: str
    title: str
    detail: str
    priority: Priority
    source: str
    freshness: str
    status: str = "ok"
    actions: list[dict[str, str]] = field(default_factory=list)
    dedupe_key: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class StructuredBriefing:
    kind: str
    generated_at: str
    items: list[BriefingItem]
    full_text: str
    voice_text: str
    unavailable: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "generated_at": self.generated_at,
            "items": [i.to_dict() for i in self.items],
            "full_text": self.full_text,
            "voice_text": self.voice_text,
            "unavailable": self.unavailable,
        }


def _dedupe(items: list[BriefingItem]) -> list[BriefingItem]:
    seen: set[str] = set()
    out: list[BriefingItem] = []
    for item in items:
        key = item.dedupe_key or f"{item.source}:{item.title.lower().strip()}"
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _prio_rank(p: Priority) -> int:
    return {"critique": 0, "aujourd_hui": 1, "surveiller": 2, "information": 3}.get(p, 9)


async def collect_briefing_sources() -> tuple[list[BriefingItem], list[dict[str, str]], dict[str, Any]]:
    """Collecte déterministe des sources — jamais un dump brut."""
    items: list[BriefingItem] = []
    unavailable: list[dict[str, str]] = []
    raw: dict[str, Any] = {}

    # Agenda
    try:
        from integrations import calendar_client

        events = []
        if calendar_client and calendar_client.is_available():
            events = await calendar_client.get_today_events() or []
        raw["calendar"] = events
        for i, ev in enumerate(events[:8]):
            items.append(
                BriefingItem(
                    id=f"cal-{i}",
                    title=str(ev.get("summary") or "(sans titre)"),
                    detail=str(ev.get("start") or ""),
                    priority="aujourd_hui",
                    source="calendar",
                    freshness="live",
                    actions=[{"type": "open_event", "label": "Ouvrir"}],
                    dedupe_key=f"cal:{ev.get('summary')}:{ev.get('start')}",
                )
            )
    except Exception as exc:
        unavailable.append({"source": "calendar", "reason": str(exc)})

    # Tâches
    try:
        from database import get_tasks

        tasks = get_tasks(status="todo")
        raw["tasks"] = tasks
        for t in (tasks or [])[:12]:
            if not isinstance(t, dict):
                continue
            if (t.get("status") or "") == "done":
                continue
            prio = "critique" if (t.get("priority") or "") == "high" else "aujourd_hui"
            items.append(
                BriefingItem(
                    id=f"task-{t.get('id')}",
                    title=str(t.get("title") or ""),
                    detail=str(t.get("due_date") or t.get("priority") or ""),
                    priority=prio,  # type: ignore[arg-type]
                    source="tasks",
                    freshness="db",
                    actions=[{"type": "complete_task", "label": "Terminer", "id": str(t.get("id"))}],
                    dedupe_key=f"task:{t.get('id')}",
                )
            )
    except Exception as exc:
        unavailable.append({"source": "tasks", "reason": str(exc)})

    # Emails analysés
    try:
        from database import get_recent_email_summaries

        emails = get_recent_email_summaries(15)
        raw["emails"] = emails
        for e in emails or []:
            if (e.get("priority") or "").lower() in ("high", "urgent") or e.get("action_needed"):
                items.append(
                    BriefingItem(
                        id=f"email-{e.get('id')}",
                        title=str(e.get("subject") or "(sans objet)"),
                        detail=f"De {e.get('sender')}: {e.get('summary') or ''}"[:240],
                        priority="critique" if (e.get("priority") or "") == "urgent" else "aujourd_hui",
                        source="email",
                        freshness="email_summaries",
                        actions=[{"type": "open_email", "label": "Ouvrir"}],
                        dedupe_key=f"email:{e.get('gmail_id') or e.get('id')}",
                    )
                )
    except Exception as exc:
        unavailable.append({"source": "email", "reason": str(exc)})

    # Notifications
    try:
        from database import get_unread_notifications

        notifs = get_unread_notifications(15)
        raw["notifications"] = notifs
        for n in notifs or []:
            pr = (n.get("priority") or "medium").lower()
            items.append(
                BriefingItem(
                    id=f"notif-{n.get('id')}",
                    title=str(n.get("title") or ""),
                    detail=str(n.get("content") or "")[:200],
                    priority="critique" if pr in ("urgent", "high") else "surveiller",
                    source="notifications",
                    freshness="db",
                    dedupe_key=f"notif:{n.get('title')}",
                )
            )
    except Exception as exc:
        unavailable.append({"source": "notifications", "reason": str(exc)})

    # Météo
    try:
        from integrations import weather

        w = await weather.get_weather(config.WEATHER_CITY) if hasattr(weather, "get_weather") else None
        if w is None and hasattr(weather, "current"):
            w = await weather.current()
        raw["weather"] = w
        if w:
            items.append(
                BriefingItem(
                    id="weather",
                    title=f"Météo {w.get('city', config.WEATHER_CITY)}",
                    detail=f"{w.get('temp')}°C, {w.get('description')}",
                    priority="information",
                    source="weather",
                    freshness="live",
                    dedupe_key="weather:today",
                )
            )
    except Exception as exc:
        unavailable.append({"source": "weather", "reason": str(exc)})

    # Jobs Cursor
    try:
        from database.cursor_jobs import list_cursor_jobs

        jobs = list_cursor_jobs(limit=10)
        raw["cursor_jobs"] = jobs
        for j in jobs:
            if j.get("status") in ("pr_opened", "completed", "failed", "needs_input"):
                items.append(
                    BriefingItem(
                        id=f"cursor-{j.get('job_id')}",
                        title=f"Cursor: {j.get('title')}",
                        detail=f"Statut {j.get('status')}"
                        + (f" — {j.get('pr_url')}" if j.get("pr_url") else ""),
                        priority="aujourd_hui" if j.get("status") == "pr_opened" else "surveiller",
                        source="cursor",
                        freshness="db",
                        actions=(
                            [{"type": "open_pr", "label": "Ouvrir PR", "url": j["pr_url"]}]
                            if j.get("pr_url")
                            else [{"type": "open_job", "label": "Voir job", "id": j.get("job_id", "")}]
                        ),
                        dedupe_key=f"cursor:{j.get('job_id')}",
                    )
                )
    except Exception as exc:
        unavailable.append({"source": "cursor", "reason": str(exc)})

    # Engagements pris (commitments) — rappel des promesses non tenues
    try:
        from database import get_commitments

        commitments = get_commitments(status="open", limit=5)
        raw["commitments"] = commitments
        for c in commitments:
            items.append(
                BriefingItem(
                    id=f"commit-{c.get('id')}",
                    title=str(c.get("content") or "")[:120],
                    detail=f"Engagement pris{' — ' + str(c.get('due_hint')) if c.get('due_hint') else ''}",
                    priority="surveiller",
                    source="commitments",
                    freshness="db",
                    dedupe_key=f"commit:{c.get('id')}",
                )
            )
    except Exception as exc:
        unavailable.append({"source": "commitments", "reason": str(exc)})

    items = _dedupe(items)
    items.sort(key=lambda i: (_prio_rank(i.priority), i.title))
    return items, unavailable, raw


_WORK_SOURCES = frozenset({"tasks", "email", "cursor", "calendar", "commitments"})

_ITEMS_SNAPSHOT_KEY = "briefing_items_snapshot"


def _save_items_snapshot(items: list[BriefingItem]) -> None:
    """Mémorise les clés du briefing du matin pour le delta « depuis ce matin »."""
    try:
        import json

        from database import set_setting

        payload = {
            "date": datetime.now().strftime("%Y-%m-%d"),
            "keys": [i.dedupe_key or f"{i.source}:{i.title.lower()}" for i in items],
        }
        set_setting(_ITEMS_SNAPSHOT_KEY, json.dumps(payload, ensure_ascii=False))
    except Exception as exc:
        logger.debug("[briefing] snapshot skip: %s", exc)


def _load_items_snapshot() -> set[str]:
    try:
        import json

        from database import get_setting

        raw = get_setting(_ITEMS_SNAPSHOT_KEY)
        if not raw:
            return set()
        payload = json.loads(raw)
        if payload.get("date") != datetime.now().strftime("%Y-%m-%d"):
            return set()
        return set(payload.get("keys") or [])
    except Exception:
        return set()


def _evening_prompt(structure: str) -> str:
    return (
        f"Résumé du soir. Structure collectée :\n{structure}\n\n"
        "Rédige le bilan du soir (écran), 8-15 lignes max. Distingue clairement : "
        "terminé / reporté / bloqué / nouveau / décisions prises / engagements / "
        "travaux Cursor / à préparer pour demain. N'invente rien : si une "
        "catégorie est vide, ne la mentionne pas."
    )


async def generate_structured_briefing(
    kind: str = "morning",
    *,
    voice_only: bool = False,
    filter_priority: Priority | None = None,
    work_only: bool = False,
) -> StructuredBriefing:
    """Briefing structuré : morning / evening / delta (depuis ce matin).

    ``voice_only=True`` : saute la rédaction écran (DeepSeek Main) et produit
    directement la version vocale depuis la structure — 1 seul appel LLM,
    latence réduite pour les demandes vocales « version courte ».
    """
    items, unavailable, raw = await collect_briefing_sources()
    if filter_priority:
        items = [i for i in items if i.priority == filter_priority]
    if work_only:
        items = [i for i in items if i.source in _WORK_SOURCES]

    if kind == "delta":
        seen_keys = _load_items_snapshot()
        if seen_keys:
            items = [
                i for i in items
                if (i.dedupe_key or f"{i.source}:{i.title.lower()}") not in seen_keys
            ]

    # Limiter le briefing principal
    top = items[:12]
    bullet_lines = []
    for p in ("critique", "aujourd_hui", "surveiller", "information"):
        group = [i for i in top if i.priority == p]
        if not group:
            continue
        bullet_lines.append(f"## {p.replace('_', ' ').title()}")
        for i in group:
            bullet_lines.append(f"- [{i.source}] {i.title} — {i.detail} (fraîcheur: {i.freshness})")

    if bullet_lines:
        structure = "\n".join(bullet_lines)
    elif kind == "delta":
        structure = "(rien de nouveau depuis ce matin)"
    else:
        structure = "(aucune donnée disponible)"

    system = (
        "Tu es JARVIS. Produis un briefing utile à la décision, en français, "
        "sans emoji, sans point d'exclamation sauf urgence réelle. "
        "Classe mentalement : critique / à faire aujourd'hui / à surveiller / information. "
        "Ne répète pas le même élément. Données d'abord."
    )
    if kind == "evening":
        user_msg = _evening_prompt(structure)
    elif kind == "delta":
        user_msg = (
            f"Changements depuis ce matin :\n{structure}\n\n"
            "Rédige un point de situation court (5-8 lignes) : uniquement ce qui "
            "est NOUVEAU ou a changé. Si rien n'a changé, dis-le en une phrase."
        )
    else:
        user_msg = (
            f"Briefing {kind}. Structure collectée :\n{structure}\n\n"
            "Rédige le briefing complet (écran), 8-15 lignes max. Réponds à la "
            "question : qu'est-ce qui mérite réellement l'attention aujourd'hui ?"
        )

    full_text = structure
    if not voice_only:
        try:
            result = await llm.chat(
                messages=[{"role": "user", "content": user_msg}],
                model=config.DEEPSEEK_MAIN_MODEL,
                system=system,
                max_tokens=1200,
                temperature=0.4,
            )
            full_text = finalize_assistant_display_text(result.get("content") or structure)
        except Exception as exc:
            logger.error("[briefing] génération Main échouée : %s", exc)
            unavailable.append({"source": "deepseek_main", "reason": str(exc)})

    voice_text = ""
    try:
        voice_source = structure if voice_only else full_text
        v = await llm.chat(
            messages=[
                {
                    "role": "user",
                    "content": (
                        "Transforme ce briefing en version vocale de 30 à 60 secondes max "
                        "(5-8 phrases). Données d'abord, ton JARVIS.\n\n"
                        f"{voice_source}"
                    ),
                }
            ],
            model=config.DEEPSEEK_FAST_MODEL,
            system="Tu es JARVIS à l'oral. Concision absolue. Pas de markdown.",
            max_tokens=280,
            temperature=0.3,
        )
        voice_text = finalize_assistant_display_text(v.get("content") or "")
    except Exception as exc:
        logger.error("[briefing] version vocale échouée : %s", exc)
        voice_text = full_text[:400]

    # Persistance best-effort
    try:
        from database import save_daily_briefing

        today = datetime.now().strftime("%Y-%m-%d")
        if kind == "morning":
            save_daily_briefing(today, morning=full_text)
            _save_items_snapshot(items)
        elif kind == "evening":
            save_daily_briefing(today, evening=full_text)
    except Exception as exc:
        logger.debug("[briefing] save skip: %s", exc)

    return StructuredBriefing(
        kind=kind,
        generated_at=datetime.now().isoformat(timespec="seconds"),
        items=top,
        full_text=full_text,
        voice_text=voice_text,
        unavailable=unavailable,
    )
