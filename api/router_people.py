"""Routes des contacts et de leur mémoire relationnelle."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import JSONResponse

import config
import llm
from agents.display_text import strip_leading_emotion
from api.people_chat import api_people_ask
from api.people_support import (
    _decode_person_path,
    _generate_person_ai_description,
    _resolve_handle_with_contacts,
)
from database import (
    clear_person_ai_description,
    create_task,
    get_people_sorted_by_recent,
    get_person,
    get_relationship_profile,
    patch_person,
    set_person_ai_description,
    upsert_person,
)

router = APIRouter()
logger = logging.getLogger("jarvis")


@router.get("/api/people")
async def api_people_list():
    return {"people": get_people_sorted_by_recent()}


@router.get("/api/people/{name}")
async def api_people_detail(name: str):
    decoded = _decode_person_path(name)
    person = get_person(decoded) or get_person(name.strip())
    if not person:
        raise HTTPException(404, f"Personne inconnue : {decoded}")
    return person


@router.patch("/api/people/{name}")
async def api_people_patch(name: str, payload: dict[str, Any] = Body(default_factory=dict)):
    """Met à jour une fiche contact (nom, relation, notes…) — `WHERE LOWER(name) = LOWER(?)`."""
    decoded = _decode_person_path(name)
    try:
        updated = patch_person(decoded, payload)
        if not updated:
            updated = patch_person(name.strip(), payload)
        if not updated:
            raise HTTPException(404, f"Personne inconnue : {decoded}")
        return updated
    except ValueError as e:
        raise HTTPException(409, str(e)) from e


@router.get("/api/people/{name}/analytics")
async def api_person_analytics(name: str):
    """Métriques iMessage calculées en Python (pas de LLM) — `scripts/contact_analytics.py`."""
    from scripts.contact_analytics import contact_analytics

    decoded = _decode_person_path(name)
    person = get_person(decoded) or get_person(name.strip())
    if not person:
        return JSONResponse(status_code=404, content={"error": "Contact non trouvé"})

    handle = _resolve_handle_with_contacts(person.get("name") or decoded)
    if not handle:
        return {
            "error": "Aucun handle iMessage (profil, numéro ou Contacts)",
            "proximity_score": {"score": 0},
        }

    try:
        data = contact_analytics.compute_all(
            handle, person.get("name") or decoded, days=730
        )
        return data
    except Exception as e:
        logger.exception("[api/people/analytics]")
        return JSONResponse(status_code=500, content={"error": str(e)})


@router.get("/api/people/{name}/timeline")
async def api_person_timeline_haiku(name: str):
    """Retourne la timeline du contact depuis le cache DB.

    Si aucun cache n'existe encore, genere via Haiku, stocke le resultat,
    puis le retourne. Utiliser POST /timeline/regenerate pour forcer un refresh.
    """
    from scripts.timeline_generator import generate_timeline
    from database import get_person_timeline_cache, update_person_timeline_cache

    decoded = _decode_person_path(name)
    key = decoded or name.strip()

    cached = await asyncio.to_thread(get_person_timeline_cache, key)
    if cached is not None:
        return {"events": cached["events"], "updated_at": cached["updated_at"], "from_cache": True}

    person = get_person(key)
    handle = _resolve_handle_with_contacts(person.get("name") if person else key)
    try:
        events = await generate_timeline(key, handle_override=handle)
        await asyncio.to_thread(update_person_timeline_cache, key, events)
        cached2 = await asyncio.to_thread(get_person_timeline_cache, key)
        return {
            "events": events,
            "updated_at": cached2["updated_at"] if cached2 else None,
            "from_cache": False,
        }
    except Exception as e:
        logger.exception("[api/people/timeline]")
        raise HTTPException(500, str(e)) from e


@router.post("/api/people/{name}/timeline/regenerate")
async def api_person_timeline_regenerate(name: str):
    """Force la regeneration de la timeline via Haiku et ecrase le cache DB."""
    from scripts.timeline_generator import generate_timeline
    from database import update_person_timeline_cache, get_person_timeline_cache

    decoded = _decode_person_path(name)
    key = decoded or name.strip()
    person = get_person(key)
    handle = _resolve_handle_with_contacts(person.get("name") if person else key)
    try:
        events = await generate_timeline(key, handle_override=handle)
        await asyncio.to_thread(update_person_timeline_cache, key, events)
        cached = await asyncio.to_thread(get_person_timeline_cache, key)
        return {
            "events": events,
            "updated_at": cached["updated_at"] if cached else None,
            "from_cache": False,
        }
    except Exception as e:
        logger.exception("[api/people/timeline/regenerate]")
        raise HTTPException(500, str(e)) from e


@router.post("/api/people/{name}/send")
async def api_person_send_imessage(name: str, body: dict[str, Any] = Body(default_factory=dict)):
    from integrations.imessage import send_imessage_to_address

    text = (body.get("text") or "").strip()
    if not text:
        return {"ok": False, "message": "Texte vide"}

    decoded = _decode_person_path(name)
    person = get_person(decoded) or get_person(name.strip())
    if not person:
        return {"ok": False, "message": f"Contact inconnu : {decoded}"}

    handle = _resolve_handle_with_contacts(person.get("name") or decoded)
    if not handle:
        return {"ok": False, "message": f"Pas de numéro ou email iMessage pour {person.get('name')}"}

    try:
        loop = asyncio.get_event_loop()
        ok, msg = await loop.run_in_executor(
            None, lambda: send_imessage_to_address(handle, text)
        )
        if ok:
            return {"ok": True, "message": f"Message envoyé à {person.get('name')}"}
        return {"ok": False, "message": msg}
    except Exception as e:
        logger.exception("[api/people/send]")
        return {"ok": False, "message": str(e)}


@router.post("/api/people/{name}/suggest-message")
async def api_person_suggest_message(name: str):
    from scripts.contact_analytics import contact_analytics

    decoded = _decode_person_path(name)
    person = get_person(decoded) or get_person(name.strip())
    if not person:
        raise HTTPException(404, f"Contact inconnu : {decoded}")

    display = person.get("name") or decoded
    handle = _resolve_handle_with_contacts(display) or display

    analytics: dict = {}
    try:
        analytics = contact_analytics.compute_all(handle, display, days=365)
    except Exception as e:
        logger.warning("[suggest-message] analytics : %s", e)

    pxd = analytics.get("proximity_score") or {}
    if isinstance(pxd, dict):
        details = pxd.get("details") or {}
        days_last = details.get("days_since_last", "?")
    else:
        days_last = "?"
    last_ex = analytics.get("last_exchanges") or []

    system = f"""Tu es JARVIS. Génère un message iMessage court et naturel que l'utilisateur pourrait envoyer à {display}.
Relation : {person.get("relationship") or "?"}
Dernier échange (jours depuis) : {days_last}
Derniers messages (aperçu) : {last_ex!s}
Le message doit être naturel, pas formel, comme l'utilisateur parle vraiment. 1-2 phrases max. Retourne UNIQUEMENT le message, rien d'autre."""

    try:
        result = await llm.chat(
            messages=[
                {
                    "role": "user",
                    "content": f"Suggère un message court et naturel à envoyer à {display}.",
                }
            ],
            model=config.DEEPSEEK_FAST_MODEL,
            system=system,
            max_tokens=100,
            temperature=0.8,
            use_cache=False,
        )
        out = strip_leading_emotion((result.get("content") or "").strip())
        return {"suggestion": out, "model": result.get("model"), "cost": result.get("cost", 0.0)}
    except Exception as e:
        logger.exception("[api/people/suggest-message]")
        raise HTTPException(500, str(e)) from e


@router.post("/api/people/{name}/remind")
async def api_person_remind(name: str, body: dict[str, Any] = Body(default_factory=dict)):
    when = (body.get("when") or "").strip() or "bientôt"
    decoded = _decode_person_path(name)
    person = get_person(decoded) or get_person(name.strip())
    if not person:
        raise HTTPException(404, f"Contact inconnu : {decoded}")
    label = person.get("name") or decoded
    try:
        task_id = create_task(
            title=f"Recontacter {label}",
            description=when,
            priority="medium",
            category="relation",
        )
        return {"ok": True, "task_id": task_id}
    except Exception as e:
        logger.exception("[api/people/remind]")
        raise HTTPException(500, str(e)) from e



@router.get("/api/people/{name}/description")
async def api_person_description(name: str):
    """Description IA courte (cache people.ai_description ou génération Haiku)."""
    decoded = _decode_person_path(name)
    person = get_person(decoded) or get_person(name.strip())
    if not person:
        raise HTTPException(404, f"Personne inconnue : {decoded}")
    cached = person.get("ai_description")
    if cached and str(cached).strip():
        return {"description": str(cached).strip()}
    pid = person["id"]
    profile = get_relationship_profile(pid)
    try:
        text, meta = await _generate_person_ai_description(person, profile)
        if text:
            set_person_ai_description(pid, text)
        return {
            "description": text,
            "model": meta.get("model"),
            "cost": meta.get("cost", 0.0),
        }
    except Exception as e:
        logger.exception("[api/people/description]")
        raise HTTPException(500, str(e)) from e


@router.post("/api/people/{name}/description/refresh")
async def api_person_description_refresh(name: str):
    decoded = _decode_person_path(name)
    person = get_person(decoded) or get_person(name.strip())
    if not person:
        raise HTTPException(404, f"Personne inconnue : {decoded}")
    clear_person_ai_description(person["id"])
    lookup_name = person["name"]
    person = get_person(lookup_name)
    profile = get_relationship_profile(person["id"])
    try:
        text, meta = await _generate_person_ai_description(person or {}, profile)
        if text:
            set_person_ai_description(person["id"], text)
        return {
            "description": text,
            "model": meta.get("model"),
            "cost": meta.get("cost", 0.0),
        }
    except Exception as e:
        logger.exception("[api/people/description/refresh]")
        raise HTTPException(500, str(e)) from e


@router.post("/api/people")
async def api_people_upsert(payload: dict):
    name = (payload.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "`name` requis")

    fields = {}
    for k in ("relationship", "personality_notes", "dynamics", "patterns"):
        v = payload.get(k)
        if v is not None:
            fields[k] = v

    person_id = upsert_person(name, **fields)
    return get_person(name) or {"id": person_id, "name": name}



router.add_api_route(
    "/api/people/{name}/ask",
    api_people_ask,
    methods=["POST"],
)


