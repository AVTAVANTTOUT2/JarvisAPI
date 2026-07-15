"""Routes d'ingestion GPS, de lieux et de déplacements."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request

import config
import auth

router = APIRouter()




def _parse_optional_point_time(body: dict[str, Any]) -> datetime | None:
    """ISO8601, unix (s ou ms), depuis les clés timestamp / created_at / point_time."""
    for key in ("timestamp", "created_at", "point_time"):
        v = body.get(key)
        if v is None:
            continue
        if isinstance(v, (int, float)):
            x = float(v)
            if x > 1e12:
                x /= 1000.0
            try:
                return datetime.fromtimestamp(x)
            except (OSError, OverflowError, ValueError):
                continue
        if isinstance(v, str):
            try:
                return datetime.fromisoformat(v.replace("Z", "+00:00"))
            except ValueError:
                continue
    return None


# ── Localisation (GPS, lieux nommés, visites) ───────────────


def _require_location_token(request: Request) -> None:
    """Vérifie le jeton partagé pour les intégrations non-navigateur (Shortcuts iOS).

    Si `LOCATION_API_TOKEN` n'est pas configuré, l'endpoint reste ouvert
    (rétro-compatibilité) — un avertissement est loggué une fois au démarrage.
    """
    authorization = request.headers.get("authorization", "")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() == "bearer" and auth.verify_mobile_token(token.strip()):
        return
    if not config.LOCATION_API_TOKEN:
        return
    provided = request.headers.get("x-location-token") or request.query_params.get("token")
    if provided != config.LOCATION_API_TOKEN:
        raise HTTPException(401, "Jeton de localisation invalide ou manquant")


@router.post("/api/location")
async def api_location_receive(body: dict[str, Any], request: Request):
    """Réception d'un point GPS (app native, raccourci iOS, etc.)."""
    _require_location_token(request)
    try:
        lat = float(body["latitude"])
        lng = float(body["longitude"])
    except (KeyError, TypeError, ValueError) as e:
        raise HTTPException(400, f"latitude/longitude invalides : {e}") from e
    from integrations.location import location_manager

    pt = _parse_optional_point_time(body)
    return await location_manager.process_location(
        lat,
        lng,
        altitude=body.get("altitude"),
        accuracy=body.get("accuracy"),
        speed=body.get("speed"),
        heading=body.get("heading"),
        source=str(body.get("source") or "app"),
        point_time=pt,
        created_at=body.get("created_at") if isinstance(body.get("created_at"), str) else None,
    )


@router.post("/api/location/batch")
async def api_location_batch(body: dict[str, Any], request: Request):
    """Points groupés (ex. rattrapage hors ligne). Chaque point peut avoir timestamp."""
    _require_location_token(request)
    points = body.get("points")
    if not isinstance(points, list):
        raise HTTPException(400, "Body attendu : {\"points\": [...]}")
    from integrations.location import location_manager

    results: list[dict[str, Any]] = []
    indexed: list[tuple[int, dict[str, Any]]] = []
    for i, p in enumerate(points):
        if isinstance(p, dict):
            indexed.append((i, p))

    def sort_key(item: tuple[int, dict[str, Any]]) -> tuple[float, int]:
        idx, p = item
        t = _parse_optional_point_time(p)
        if t is not None:
            return (t.timestamp(), idx)
        return (float(idx), idx)

    for _, p in sorted(indexed, key=sort_key):
        try:
            lat = float(p["latitude"])
            lng = float(p["longitude"])
        except (KeyError, TypeError, ValueError):
            continue
        pt = _parse_optional_point_time(p)
        r = await location_manager.process_location(
            lat,
            lng,
            altitude=p.get("altitude"),
            accuracy=p.get("accuracy"),
            speed=p.get("speed"),
            heading=p.get("heading"),
            source=str(p.get("source") or "app"),
            point_time=pt,
            created_at=p.get("created_at") if isinstance(p.get("created_at"), str) else None,
        )
        results.append(r)
    return {"processed": len(results), "results": results}


@router.get("/api/places")
async def api_places_list():
    from database.location_helpers import get_all_places

    return {"places": get_all_places()}


@router.post("/api/places")
async def api_places_create(body: dict[str, Any]):
    from database.location_helpers import create_place

    try:
        pid = create_place(
            name=str(body["name"]),
            category=str(body.get("category") or "other"),
            lat=float(body["latitude"]),
            lng=float(body["longitude"]),
            radius=float(body["radius"]) if body.get("radius") is not None else None,
            address=body.get("address"),
            notes=body.get("notes"),
        )
    except (KeyError, TypeError, ValueError) as e:
        raise HTTPException(400, str(e)) from e
    return {"id": pid, **body}


@router.put("/api/places/{place_id}")
async def api_places_update(place_id: int, body: dict[str, Any]):
    from database.location_helpers import update_place

    payload = dict(body)
    if "radius" in payload and "radius_meters" not in payload:
        payload["radius_meters"] = payload.pop("radius")
    update_place(place_id, **payload)
    return {"ok": True}


@router.delete("/api/places/{place_id}")
async def api_places_delete(place_id: int):
    from database.location_helpers import delete_place

    ok = delete_place(place_id)
    if not ok:
        raise HTTPException(404, "Lieu introuvable")
    return {"ok": True}


@router.get("/api/places/{place_id}/stats")
async def api_place_stats(place_id: int):
    from database.location_helpers import get_place, get_place_visit_stats

    if not get_place(place_id):
        raise HTTPException(404, "Lieu introuvable")
    return get_place_visit_stats(place_id)


@router.get("/api/location/status")
async def api_location_status():
    from integrations.location import location_manager

    return await location_manager.get_status()


@router.get("/api/location/history")
async def api_location_history(hours: int = 24):
    from database.location_helpers import get_location_history

    return {"points": get_location_history(hours=max(1, min(hours, 168)))}


@router.get("/api/visits")
async def api_visits_list(days: int = 7):
    from database.location_helpers import get_recent_visits

    return {"visits": get_recent_visits(max(1, min(days, 90)))}


@router.get("/api/visits/today")
async def api_visits_today():
    from database.location_helpers import get_today_visits

    return {"visits": get_today_visits()}


@router.get("/api/trips")
async def api_trips_list(days: int = 7):
    from database.location_helpers import get_recent_trips

    return {"trips": get_recent_trips(max(1, min(days, 30)))}


@router.get("/api/location/patterns")
async def api_location_patterns():
    from database.location_helpers import get_active_location_patterns

    return {"patterns": get_active_location_patterns()}


@router.post("/api/location/name-current")
async def api_location_name_current(body: dict[str, Any]):
    from database.location_helpers import create_place, get_current_location

    cur = get_current_location()
    if not cur:
        return {"ok": False, "message": "Pas de position GPS récente"}
    try:
        pid = create_place(
            name=str(body["name"]),
            category=str(body.get("category") or "other"),
            lat=float(cur["latitude"]),
            lng=float(cur["longitude"]),
        )
    except (KeyError, TypeError, ValueError) as e:
        raise HTTPException(400, str(e)) from e
    return {"ok": True, "place_id": pid}
