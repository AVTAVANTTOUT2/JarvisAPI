"""Handlers calendrier, relations, recherche et export."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from fastapi import Body, HTTPException

import config
from api.people_support import _decode_person_path
from database import (
    get_active_patterns,
    get_all_people,
    get_life_profile,
    get_life_profile_entries,
    get_person,
    get_recent_episodes,
    get_recent_moods,
    get_relationship_profile,
    get_relationship_timeline,
    get_school_documents,
    get_tasks,
)
from integrations import calendar_client

logger = logging.getLogger("jarvis")



# ── Calendar ──────────────────────────────────────────────────


async def api_calendar_get(start: str = "", end: str = ""):
    """Récupère les événements Calendar.app entre deux dates ISO."""
    if not calendar_client or not calendar_client.is_available():
        raise HTTPException(503, "Calendar.app indisponible")
    if not start or not end:
        raise HTTPException(400, "Paramètres start et end requis (ISO 8601)")
    events = await calendar_client.get_events(start, end)
    return {"events": events, "count": len(events)}


async def api_calendar_create(body: dict = Body(default_factory=dict)):
    """Crée un événement dans Calendar.app."""
    if not calendar_client or not calendar_client.is_available():
        raise HTTPException(503, "Calendar.app indisponible")
    title = (body.get("title") or body.get("summary") or "").strip()
    start = (body.get("start") or "").strip()
    end = (body.get("end") or "").strip()
    if not title or not start:
        raise HTTPException(400, "title/summary et start sont requis")
    result = await calendar_client.create_event(
        summary=title,
        start_date=start,
        end_date=end,
        calendar_name=body.get("calendar"),
        location=body.get("location", ""),
        notes=body.get("notes", ""),
    )
    if not result.get("ok"):
        raise HTTPException(500, result.get("message", "Erreur création événement"))
    return result


async def api_calendar_test():
    """Crée un événement de test pour vérifier le pipeline Calendar."""
    if not calendar_client:
        return {"ok": False, "error": "calendar_client non initialisé"}
    if not calendar_client.is_available():
        return {"ok": False, "error": "Calendar non disponible"}

    start = datetime.now() + timedelta(hours=1)
    end = start + timedelta(minutes=30)
    return await calendar_client.create_event(
        summary="TEST JARVIS — à supprimer",
        start_date=start.strftime("%Y-%m-%d %H:%M"),
        end_date=end.strftime("%Y-%m-%d %H:%M"),
    )


# ── Mémoire profonde : analyse relationnelle ────────────────


async def api_analyze_contact(payload: dict):
    """Lance l'analyse Haiku d'un contact iMessage. Body : {"name": "Bertille"}."""
    name = (payload.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "`name` requis")

    try:
        from scripts.relationship_analyzer import analyzer
        result = await analyzer.analyze_single_contact(name)
        if result is None:
            raise HTTPException(404, f"Aucun message trouvé pour '{name}'")
        return {"status": "ok", "profile": result}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Erreur analyze-contact")
        raise HTTPException(500, f"Erreur analyse : {e}")


async def api_relationship_detail(name: str):
    """Profil relationnel complet d'un contact : people + relationship_profile + timeline."""
    decoded = _decode_person_path(name)
    person = get_person(decoded) or get_person(name.strip())
    if not person:
        raise HTTPException(404, f"Personne inconnue : {decoded}")

    profile = get_relationship_profile(person["id"]) if person.get("id") else None
    timeline = get_relationship_timeline(person["id"], limit=30) if person.get("id") else []

    return {
        "person": person,
        "relationship_profile": profile,
        "timeline": timeline,
    }


async def api_relationship_graph():
    """Graphe vivant des relations : utilisateur + contacts + liens multi-personnes détectés."""
    from scripts.relationship_graph import build_relationship_graph

    return build_relationship_graph()


async def api_time_machine(date: str):
    """Reconstruction chronologique d'une journée (messages, tâches, lieux, humeur, écran, journal)."""
    from scripts.time_machine import build_day_timeline

    try:
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(400, "Format de date invalide, attendu YYYY-MM-DD")

    return build_day_timeline(date)


# ── Recherche, export, contacts macOS (iMessage DB) ──────────


async def api_mac_contacts():
    """Handles iMessage (chat.db) + résolution noms via Contacts.app si disponible."""
    try:
        from integrations.contacts import contacts_reader
        from integrations.imessage_reader import IMessageReader

        if contacts_reader.is_available():
            contacts_reader.build_cache()

        r = IMessageReader()
        raw = r.get_all_contacts()
        contacts = []
        for c in raw:
            handle = c.get("handle")
            if contacts_reader.is_available():
                disp = contacts_reader.resolve_handle(handle or "")
            else:
                disp = handle
            contacts.append({
                "handle": handle,
                "name": disp,
                "msg_count": c.get("msg_count"),
                "last_date": c.get("last_date"),
            })
        return {"contacts": contacts}
    except Exception as e:
        logger.warning("[api/contacts] %s", e)
        return {"contacts": [], "error": str(e)}


async def api_contacts_sync():
    """Re-synchronise les entrées `people` dont le nom est encore un numéro / email."""
    try:
        from scripts.sync_contacts import sync_people_names

        result = await sync_people_names()
        return result
    except Exception as e:
        logger.error("[api/contacts/sync] %s", e)
        raise HTTPException(500, str(e)) from e


async def api_search(q: str = ""):
    """Recherche légère multi-sources (pas de LLM)."""
    needle = (q or "").strip().lower()
    if len(needle) < 2:
        return {"query": q, "results": []}

    results: list[dict] = []

    try:
        for p in get_all_people():
            name = (p.get("name") or "").strip()
            if needle in name.lower():
                results.append({
                    "type": "person",
                    "id": p.get("id"),
                    "title": name,
                    "subtitle": p.get("relationship") or "",
                    "meta": "people",
                })
    except Exception as e:
        logger.warning("search people : %s", e)

    try:
        for ep in get_recent_episodes(limit=80):
            blob = f"{ep.get('summary', '')} {ep.get('content', '')}".lower()
            if needle in blob:
                results.append({
                    "type": "episode",
                    "id": ep.get("id"),
                    "title": (ep.get("summary") or ep.get("content") or "")[:120],
                    "subtitle": str(ep.get("created_at") or ""),
                    "meta": "episode",
                })
    except Exception as e:
        logger.warning("search episodes : %s", e)

    try:
        docs = get_school_documents(limit=50)
        for d in docs:
            t = (d.get("title") or "").lower()
            if needle in t or needle in (d.get("content") or "").lower()[:2000]:
                results.append({
                    "type": "document",
                    "id": d.get("id"),
                    "title": d.get("title") or "(sans titre)",
                    "subtitle": d.get("doc_type") or "",
                    "meta": "school_document",
                })
    except Exception as e:
        logger.warning("search docs : %s", e)

    return {"query": q, "results": results[:80]}


async def api_export_dump(format: str = "json"):
    """Dump JSON agrégé pour sauvegarde locale (pas de secrets tiers)."""
    if format.lower() != "json":
        raise HTTPException(400, "Seul format=json est supporté")

    try:
        from database.location_helpers import get_all_places

        payload = {
            "exported_at": datetime.now().isoformat(timespec="seconds"),
            "user": config.USER_NAME,
            "life_profile": get_life_profile(),
            "life_profile_entries": get_life_profile_entries(),
            "people": get_all_people(),
            "tasks": get_tasks(),
            "patterns": get_active_patterns(),
            "journal_moods": get_recent_moods(90),
            "recent_episodes": get_recent_episodes(limit=100),
            "school_documents_meta": get_school_documents(limit=200),
            "places": get_all_places(),
        }
        return payload
    except Exception as e:
        logger.exception("api/export : %s", e)
        raise HTTPException(500, str(e)) from e
