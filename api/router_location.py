"""Routes d'ingestion GPS, de lieux et de déplacements."""

from __future__ import annotations

import hmac
import logging
import math
import re
import threading
import time
from collections import deque
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request

import auth
import config

router = APIRouter()
logger = logging.getLogger(__name__)

_CLIENT_POINT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
_location_rate_limit_buckets: dict[str, deque[float]] = {}
_location_rate_limit_lock = threading.Lock()


def _client_host(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _enforce_location_rate_limit(request: Request) -> None:
    """Limite l'ingestion par adresse source, avant toute vérification de jeton."""
    limit = max(1, int(getattr(config, "LOCATION_RATE_LIMIT_REQUESTS", 120)))
    window = max(
        1.0,
        float(getattr(config, "LOCATION_RATE_LIMIT_WINDOW_SECONDS", 60)),
    )
    client = _client_host(request)
    now = time.monotonic()
    cutoff = now - window

    with _location_rate_limit_lock:
        bucket = _location_rate_limit_buckets.setdefault(client, deque())
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()
        if len(bucket) >= limit:
            retry_after = max(1, math.ceil(window - (now - bucket[0])))
            logger.warning(
                "Ingestion GPS limitée client=%s limite=%d fenêtre=%ss",
                client,
                limit,
                int(window),
            )
            raise HTTPException(
                429,
                "Trop de requêtes de localisation",
                headers={"Retry-After": str(retry_after)},
            )
        bucket.append(now)

        # Évite de conserver indéfiniment les adresses de clients disparus.
        if len(_location_rate_limit_buckets) > 1024:
            stale = [
                key
                for key, values in _location_rate_limit_buckets.items()
                if not values or values[-1] <= cutoff
            ]
            for key in stale:
                _location_rate_limit_buckets.pop(key, None)


def _parse_optional_point_time(body: dict[str, Any]) -> datetime | None:
    """ISO8601, unix (s ou ms), depuis les clés timestamp / created_at / point_time / captured_at."""
    for key in ("captured_at", "timestamp", "created_at", "point_time"):
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


def _require_location_token(request: Request) -> str:
    """Vérifie un Bearer mobile ou le jeton partagé des Shortcuts iOS."""
    client = _client_host(request)
    authorization = request.headers.get("authorization", "")
    scheme, _, token = authorization.partition(" ")
    bearer = token.strip()
    if scheme.lower() == "bearer" and bearer and auth.verify_mobile_token(bearer):
        return "mobile_bearer"

    expected = config.LOCATION_API_TOKEN.strip()
    if not expected:
        logger.warning(
            "Ingestion GPS refusée client=%s: LOCATION_API_TOKEN non configuré",
            client,
        )
        raise HTTPException(503, "Authentification de localisation non configurée")

    provided = request.headers.get("x-location-token", "")
    valid = bool(provided) and hmac.compare_digest(
        provided.encode("utf-8"),
        expected.encode("utf-8"),
    )
    if not valid:
        logger.warning("Ingestion GPS non autorisée client=%s", client)
        raise HTTPException(401, "Jeton de localisation invalide ou manquant")
    return "shared_token"


def _require_mobile_bearer_device(request: Request) -> dict[str, Any]:
    """Bearer mobile obligatoire (batch offline-first Android)."""
    client = _client_host(request)
    authorization = request.headers.get("authorization", "")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        logger.warning("Batch GPS sans Bearer mobile client=%s", client)
        raise HTTPException(401, "Bearer mobile requis pour /api/location/batch")
    device = auth.verify_mobile_token(token.strip())
    if not device:
        logger.warning("Batch GPS avec Bearer invalide client=%s", client)
        raise HTTPException(401, "Jeton mobile invalide ou révoqué")
    return device


def _validate_coordinates(lat: float, lng: float) -> str | None:
    if (
        not math.isfinite(lat)
        or not math.isfinite(lng)
        or not (-90.0 <= lat <= 90.0)
        or not (-180.0 <= lng <= 180.0)
    ):
        return "invalid_coordinates"
    return None


def _validate_point_time(pt: datetime | None) -> str | None:
    if pt is None:
        return None
    now = datetime.now(pt.tzinfo) if pt.tzinfo else datetime.now()
    # Futur incohérent (> 5 min)
    if (pt - now).total_seconds() > 300:
        return "invalid_timestamp"
    # Trop ancien (> 30 jours) — aligné sur rétention file Android (spec Vague 2B)
    if (now - pt).total_seconds() > 30 * 24 * 3600:
        return "invalid_timestamp"
    return None


@router.post("/api/location")
async def api_location_receive(body: dict[str, Any], request: Request):
    """Réception d'un point GPS (app native, raccourci iOS, etc.)."""
    _enforce_location_rate_limit(request)
    auth_method = _require_location_token(request)
    try:
        lat = float(body["latitude"])
        lng = float(body["longitude"])
    except (KeyError, TypeError, ValueError) as e:
        raise HTTPException(400, f"latitude/longitude invalides : {e}") from e
    coord_err = _validate_coordinates(lat, lng)
    if coord_err:
        raise HTTPException(400, coord_err)
    from integrations.location import location_manager

    pt = _parse_optional_point_time(body)
    result = await location_manager.process_location(
        lat,
        lng,
        altitude=body.get("altitude"),
        accuracy=body.get("accuracy"),
        speed=body.get("speed"),
        heading=body.get("heading") if body.get("heading") is not None else body.get("bearing"),
        source=str(body.get("source") or "app"),
        point_time=pt,
        created_at=body.get("created_at") if isinstance(body.get("created_at"), str) else None,
    )
    logger.info(
        "Point GPS accepté client=%s auth=%s source=%r",
        _client_host(request),
        auth_method,
        str(body.get("source") or "app")[:80],
    )
    return result


@router.post("/api/location/batch")
async def api_location_batch(body: dict[str, Any], request: Request):
    """Rattrapage hors ligne Android — Bearer + client_point_id idempotent."""
    _enforce_location_rate_limit(request)
    device = _require_mobile_bearer_device(request)
    device_id = str(device["device_id"])
    points = body.get("points")
    if not isinstance(points, list):
        raise HTTPException(400, 'Body attendu : {"points": [...]}')

    max_points = int(getattr(config, "LOCATION_BATCH_MAX_POINTS", 50) or 50)
    if len(points) > max_points:
        raise HTTPException(
            400,
            f"Lot trop grand : max {max_points} points (reçu {len(points)})",
        )
    if len(points) == 0:
        return {"accepted": [], "duplicates": [], "rejected": []}

    from database.location_helpers import (
        get_location_point_dedup,
        save_location_point_dedup,
    )
    from integrations.location import location_manager

    accepted: list[str] = []
    duplicates: list[str] = []
    rejected: list[dict[str, str]] = []

    indexed: list[tuple[int, dict[str, Any]]] = []
    for i, p in enumerate(points):
        if isinstance(p, dict):
            indexed.append((i, p))
        else:
            rejected.append(
                {
                    "client_point_id": f"index-{i}",
                    "reason": "invalid_payload",
                }
            )

    def sort_key(item: tuple[int, dict[str, Any]]) -> tuple[float, int]:
        idx, p = item
        t = _parse_optional_point_time(p)
        if t is not None:
            return (t.timestamp(), idx)
        return (float(idx), idx)

    for _, p in sorted(indexed, key=sort_key):
        raw_id = p.get("client_point_id") or p.get("clientPointId")
        client_point_id = str(raw_id).strip() if raw_id is not None else ""
        if not client_point_id or not _CLIENT_POINT_ID_RE.match(client_point_id):
            rejected.append(
                {
                    "client_point_id": client_point_id or "missing",
                    "reason": "missing_client_point_id",
                }
            )
            continue

        existing = get_location_point_dedup(device_id, client_point_id)
        if existing:
            duplicates.append(client_point_id)
            continue

        try:
            lat = float(p["latitude"])
            lng = float(p["longitude"])
        except (KeyError, TypeError, ValueError):
            rejected.append(
                {"client_point_id": client_point_id, "reason": "invalid_coordinates"}
            )
            continue

        coord_err = _validate_coordinates(lat, lng)
        if coord_err:
            rejected.append({"client_point_id": client_point_id, "reason": coord_err})
            continue

        pt = _parse_optional_point_time(p)
        time_err = _validate_point_time(pt)
        if time_err:
            rejected.append({"client_point_id": client_point_id, "reason": time_err})
            continue

        heading = p.get("heading")
        if heading is None:
            heading = p.get("bearing")

        try:
            result = await location_manager.process_location(
                lat,
                lng,
                altitude=p.get("altitude"),
                accuracy=p.get("accuracy"),
                speed=p.get("speed"),
                heading=heading,
                source=str(p.get("source") or "android_background"),
                point_time=pt,
                created_at=p.get("created_at") if isinstance(p.get("created_at"), str) else None,
            )
        except Exception:
            logger.exception("Échec process_location pour %s", client_point_id)
            rejected.append(
                {"client_point_id": client_point_id, "reason": "processing_error"}
            )
            continue

        history_id = result.get("location_history_id")
        if history_id is None:
            logger.error(
                "Batch point %s accepté sans location_history_id — rejet",
                client_point_id,
            )
            rejected.append(
                {"client_point_id": client_point_id, "reason": "processing_error"}
            )
            continue

        save_location_point_dedup(
            device_id,
            client_point_id,
            int(history_id),
        )
        accepted.append(client_point_id)

    response = {
        "accepted": accepted,
        "duplicates": duplicates,
        "rejected": rejected,
    }
    logger.info(
        "Batch GPS traité client=%s device=%s reçus=%d acceptés=%d doublons=%d rejetés=%d",
        _client_host(request),
        device_id,
        len(points),
        len(accepted),
        len(duplicates),
        len(rejected),
    )
    return response


@router.get("/api/mobile/location/diagnostics")
async def api_mobile_location_diagnostics(request: Request):
    """Diagnostics GPS côté serveur pour l'appareil mobile authentifié."""
    device = _require_mobile_bearer_device(request)
    from database.location_helpers import get_mobile_location_diagnostics

    return get_mobile_location_diagnostics(str(device["device_id"]))


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
    from database.location_helpers import PLACE_MUTABLE_FIELDS, update_place

    payload = dict(body)
    if "radius" in payload and "radius_meters" not in payload:
        payload["radius_meters"] = payload.pop("radius")
    if not any(key in PLACE_MUTABLE_FIELDS for key in payload):
        raise HTTPException(400, "Aucun champ modifiable fourni")
    if not update_place(place_id, **payload):
        raise HTTPException(404, "Lieu introuvable")
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
