"""Handlers calendrier, relations, recherche et export."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from fastapi import HTTPException

import config
from api.errors import api_error, internal_error
from api.people_support import _decode_person_path
from api.relationship_models import AnalyzeContactRequest, CalendarEventCreateRequest
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
    unified_search,
)
from database.time_buckets import local_datetime, sqlite_utc_timestamp
from integrations import calendar_client

logger = logging.getLogger("jarvis")


# ── Calendar ──────────────────────────────────────────────────


async def api_calendar_get(start: str = "", end: str = ""):
    """Récupère les événements Calendar.app entre deux dates ISO."""
    if not calendar_client:
        raise api_error(503, "calendar_unavailable", "Calendar.app indisponible")
    if not start or not end:
        raise api_error(
            400,
            "calendar_range_required",
            "Paramètres start et end requis (ISO 8601)",
        )
    try:
        result = await calendar_client.get_events_result(start, end)
    except Exception as exc:
        logger.exception("[calendar/list] échec")
        raise api_error(
            502, "calendar_read_failed", "Lecture du calendrier impossible"
        ) from exc
    if result.status != "ok":
        error = result.error or "calendar_read_failed"
        if error == "calendar_range_invalid":
            raise api_error(400, error, "Plage de calendrier invalide")
        if error == "calendar_unavailable":
            raise api_error(503, error, "Calendar.app indisponible")
        raise api_error(502, "calendar_read_failed", "Lecture du calendrier impossible")
    events = list(result.events)
    return {"events": events, "count": len(events)}


async def api_calendar_create(body: CalendarEventCreateRequest):
    """Crée un événement dans Calendar.app."""
    if not calendar_client or not calendar_client.is_available():
        raise api_error(503, "calendar_unavailable", "Calendar.app indisponible")
    try:
        result = await calendar_client.create_event(
            summary=body.title,
            start_date=body.start,
            end_date=body.end,
            calendar_name=body.calendar,
            location=body.location,
            notes=body.notes,
        )
    except Exception as exc:
        logger.exception("[calendar/create] échec")
        raise api_error(
            502, "calendar_create_failed", "Création de l'événement impossible"
        ) from exc
    if not result.get("ok"):
        logger.error("[calendar/create] échec : %s", result.get("message", "inconnu"))
        raise api_error(
            502, "calendar_create_failed", "Création de l'événement impossible"
        )
    return result


async def api_calendar_test():
    """Crée un événement de test pour vérifier le pipeline Calendar."""
    if not calendar_client or not calendar_client.is_available():
        raise api_error(503, "calendar_unavailable", "Calendar.app indisponible")

    start = local_datetime() + timedelta(hours=1)
    end = start + timedelta(minutes=30)
    try:
        result = await calendar_client.create_event(
            summary="TEST JARVIS — à supprimer",
            start_date=start.strftime("%Y-%m-%d %H:%M"),
            end_date=end.strftime("%Y-%m-%d %H:%M"),
        )
    except Exception as exc:
        logger.exception("[calendar/test] échec")
        raise api_error(
            502, "calendar_test_failed", "Test du calendrier impossible"
        ) from exc
    if not result.get("ok"):
        logger.error("[calendar/test] refus : %s", result.get("message", "inconnu"))
        raise api_error(502, "calendar_test_failed", "Test du calendrier impossible")
    return result


# ── Mémoire profonde : analyse relationnelle ────────────────


async def api_analyze_contact(payload: AnalyzeContactRequest):
    """Lance l'analyse Haiku d'un contact iMessage. Body : {"name": "Bertille"}."""
    try:
        from scripts.relationship_analyzer import analyzer

        result = await analyzer.analyze_single_contact(payload.name)
        if result is None:
            raise api_error(404, "contact_messages_not_found", "Aucun message trouvé")
        return {"status": "ok", "profile": result}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Erreur analyze-contact")
        raise internal_error(
            "contact_analysis_failed", "Analyse du contact impossible"
        ) from e


async def api_relationship_detail(name: str):
    """Profil relationnel complet d'un contact : people + relationship_profile + timeline."""
    decoded = _decode_person_path(name)
    person = get_person(decoded) or get_person(name.strip())
    if not person:
        raise api_error(404, "person_not_found", "Contact non trouvé")

    profile = get_relationship_profile(person["id"]) if person.get("id") else None
    timeline = (
        get_relationship_timeline(person["id"], limit=30) if person.get("id") else []
    )

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
        raise api_error(
            400, "invalid_calendar_date", "Format de date invalide, attendu YYYY-MM-DD"
        ) from None

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
            contacts.append(
                {
                    "handle": handle,
                    "name": disp,
                    "msg_count": c.get("msg_count"),
                    "last_date": c.get("last_date"),
                }
            )
        return {"contacts": contacts}
    except Exception as e:
        logger.warning("[api/contacts] %s", e)
        raise api_error(503, "contacts_unavailable", "Contacts indisponibles") from e


async def api_contacts_sync():
    """Re-synchronise les entrées `people` dont le nom est encore un numéro / email."""
    try:
        from scripts.sync_contacts import sync_people_names

        result = await sync_people_names()
        return result
    except Exception as e:
        logger.error("[api/contacts/sync] %s", e)
        raise internal_error(
            "contacts_sync_failed", "Synchronisation des contacts impossible"
        ) from e


async def api_search(q: str = "", limit: int = 50):
    """Recherche classée dans toutes les données locales, sans appel LLM."""
    results = unified_search(q, limit=limit)
    categories: dict[str, int] = {}
    for result in results:
        category = str(result["category"])
        categories[category] = categories.get(category, 0) + 1
    return {
        "query": q,
        "total": len(results),
        "categories": categories,
        "results": results,
    }


async def api_export_dump(format: str = "json"):
    """Dump JSON agrégé pour sauvegarde locale (pas de secrets tiers)."""
    if format.lower() != "json":
        raise api_error(
            400, "unsupported_export_format", "Seul format=json est supporté"
        )

    try:
        from database.location_helpers import get_all_places

        payload = {
            "exported_at": sqlite_utc_timestamp(),
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
        raise internal_error("export_failed", "Export des données impossible") from e
