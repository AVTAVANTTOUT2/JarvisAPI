"""Persistance localisation — lieux nommés, historique GPS, visites, trajets, patterns."""

from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timedelta
from typing import Any

import config

from .core import get_db

logger = logging.getLogger(__name__)


def haversine(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Distance en mètres entre deux points GPS."""
    r = 6371000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def resolve_place(lat: float, lng: float) -> dict | None:
    """Lieu nommé le plus proche dont le point est dans le rayon."""
    places = get_all_places()
    best: dict | None = None
    best_d = float("inf")
    for place in places:
        try:
            plat = float(place["latitude"])
            plng = float(place["longitude"])
        except (TypeError, ValueError):
            continue
        dist = haversine(lat, lng, plat, plng)
        r = float(place.get("radius_meters") or config.LOCATION_PLACE_RADIUS or 100)
        if dist <= r and dist < best_d:
            best_d = dist
            best = place
    return best


def get_location_point_dedup(device_id: str, client_point_id: str) -> dict | None:
    """Retourne l'entrée d'idempotence GPS si elle existe."""
    with get_db() as conn:
        row = conn.execute(
            """SELECT device_id, client_point_id, location_history_id, created_at
               FROM location_point_dedup
               WHERE device_id = ? AND client_point_id = ?""",
            (device_id, client_point_id),
        ).fetchone()
        return dict(row) if row else None


def save_location_point_dedup(
    device_id: str,
    client_point_id: str,
    location_history_id: int | None,
) -> None:
    """Enregistre un point client comme déjà traité (INSERT OR IGNORE)."""
    with get_db() as conn:
        conn.execute(
            """INSERT OR IGNORE INTO location_point_dedup
               (device_id, client_point_id, location_history_id)
               VALUES (?, ?, ?)""",
            (device_id, client_point_id, location_history_id),
        )


def count_location_history() -> int:
    """Nombre total de lignes dans location_history (tests / diagnostics)."""
    with get_db() as conn:
        row = conn.execute("SELECT COUNT(*) AS c FROM location_history").fetchone()
        return int(row["c"] if row else 0)


def get_mobile_location_diagnostics(device_id: str) -> dict[str, Any]:
    """Statistiques GPS reçues pour un appareil mobile (24 h glissantes)."""
    since = (datetime.now() - timedelta(hours=24)).isoformat(timespec="seconds")
    with get_db() as conn:
        count_row = conn.execute(
            """SELECT COUNT(*) AS c FROM location_point_dedup
               WHERE device_id = ? AND datetime(created_at) >= datetime(?)""",
            (device_id, since),
        ).fetchone()
        last_row = conn.execute(
            """SELECT created_at FROM location_point_dedup
               WHERE device_id = ?
               ORDER BY datetime(created_at) DESC LIMIT 1""",
            (device_id,),
        ).fetchone()
    return {
        "device_id": device_id,
        "points_received_24h": int(count_row["c"] if count_row else 0),
        "last_point_received_at": str(last_row["created_at"]) if last_row else None,
    }


def add_location(
    lat: float,
    lng: float,
    altitude: float | None = None,
    accuracy: float | None = None,
    speed: float | None = None,
    heading: float | None = None,
    source: str = "app",
    created_at: str | None = None,
) -> int:
    """Insère un point GPS ; résout le lieu le plus proche."""
    resolved = resolve_place(lat, lng)
    place_id = int(resolved["id"]) if resolved else None
    ts = created_at or datetime.now().isoformat(timespec="seconds")
    with get_db() as conn:
        cur = conn.execute(
            """INSERT INTO location_history
               (latitude, longitude, altitude, accuracy, speed, heading, source, place_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, COALESCE(?, CURRENT_TIMESTAMP))""",
            (lat, lng, altitude, accuracy, speed, heading, source, place_id, ts),
        )
        return cur.lastrowid


def get_location_history(hours: int = 24) -> list[dict]:
    since = (datetime.now() - timedelta(hours=max(1, hours))).isoformat(timespec="seconds")
    with get_db() as conn:
        rows = conn.execute(
            """SELECT lh.*, p.name AS place_name FROM location_history lh
               LEFT JOIN places p ON p.id = lh.place_id
               WHERE lh.created_at >= ?
               ORDER BY lh.created_at ASC""",
            (since,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_last_known_location() -> dict | None:
    """Dernier point GPS connu, quel que soit son âge (affichage frontend)."""
    with get_db() as conn:
        row = conn.execute(
            """SELECT lh.*, p.name AS place_name FROM location_history lh
               LEFT JOIN places p ON p.id = lh.place_id
               ORDER BY lh.created_at DESC, lh.id DESC LIMIT 1""",
        ).fetchone()
        return dict(row) if row else None


def get_current_location(max_age_minutes: int = 10) -> dict | None:
    """Dernier point datant de moins de ``max_age_minutes`` (name_place / actions)."""
    cutoff = (datetime.now() - timedelta(minutes=max(1, max_age_minutes))).isoformat(
        timespec="seconds"
    )
    with get_db() as conn:
        row = conn.execute(
            """SELECT lh.*, p.name AS place_name FROM location_history lh
               LEFT JOIN places p ON p.id = lh.place_id
               WHERE lh.created_at >= ?
               ORDER BY lh.created_at DESC, lh.id DESC LIMIT 1""",
            (cutoff,),
        ).fetchone()
        return dict(row) if row else None


def create_place(
    name: str,
    category: str,
    lat: float,
    lng: float,
    radius: float | None = None,
    address: str | None = None,
    notes: str | None = None,
) -> int:
    r = radius if radius is not None else float(config.LOCATION_PLACE_RADIUS or 100)
    with get_db() as conn:
        cur = conn.execute(
            """INSERT INTO places (name, category, latitude, longitude, radius_meters, address, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (name.strip(), category, lat, lng, r, address, notes),
        )
        return cur.lastrowid


def get_all_places() -> list[dict]:
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM places ORDER BY name COLLATE NOCASE").fetchall()
        return [dict(r) for r in rows]


def get_place(place_id: int) -> dict | None:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM places WHERE id = ?", (place_id,)).fetchone()
        return dict(row) if row else None


def get_place_by_name(name: str) -> dict | None:
    q = f"%{name.strip()}%"
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM places WHERE LOWER(name) LIKE LOWER(?) LIMIT 1",
            (q,),
        ).fetchone()
        return dict(row) if row else None


def update_place(place_id: int, **kwargs: Any) -> None:
    if not kwargs:
        return
    keys = [k for k in kwargs if k in (
        "name", "category", "latitude", "longitude", "radius_meters", "address", "notes",
        "visit_count", "avg_duration_min", "last_visit",
    )]
    if not keys:
        return
    sets = ", ".join(f"{k} = ?" for k in keys)
    vals = [kwargs[k] for k in keys] + [place_id]
    with get_db() as conn:
        conn.execute(f"UPDATE places SET {sets} WHERE id = ?", vals)


def delete_place(place_id: int) -> bool:
    with get_db() as conn:
        conn.execute("UPDATE location_history SET place_id = NULL WHERE place_id = ?", (place_id,))
        conn.execute("DELETE FROM visits WHERE place_id = ?", (place_id,))
        conn.execute(
            "DELETE FROM trips WHERE from_place_id = ? OR to_place_id = ?",
            (place_id, place_id),
        )
        conn.execute("UPDATE location_patterns SET place_id = NULL WHERE place_id = ?", (place_id,))
        cur = conn.execute("DELETE FROM places WHERE id = ?", (place_id,))
        return cur.rowcount > 0


def start_visit(place_id: int, arrived_at: datetime | None = None) -> int:
    ts = arrived_at or datetime.now()
    dow = ts.weekday()
    ts_s = ts.isoformat(timespec="seconds")
    with get_db() as conn:
        cur = conn.execute(
            """INSERT INTO visits (place_id, arrived_at, day_of_week)
               VALUES (?, ?, ?)""",
            (place_id, ts_s, dow),
        )
        return cur.lastrowid


def end_visit(visit_id: int, ended_at: datetime | None = None) -> dict | None:
    now = ended_at or datetime.now()
    with get_db() as conn:
        row = conn.execute("SELECT * FROM visits WHERE id = ?", (visit_id,)).fetchone()
        if not row:
            return None
        arrived = row["arrived_at"]
        try:
            t0 = datetime.fromisoformat(str(arrived).replace("Z", "+00:00"))
        except ValueError:
            t0 = now
        dur_min = max(0.0, (now - t0).total_seconds() / 60.0)
        conn.execute(
            "UPDATE visits SET departed_at = ?, duration_min = ? WHERE id = ?",
            (now.isoformat(timespec="seconds"), dur_min, visit_id),
        )
        place_id = row["place_id"]
        prow = conn.execute(
            "SELECT visit_count, avg_duration_min FROM places WHERE id = ?",
            (place_id,),
        ).fetchone()
        if prow:
            n = int(prow["visit_count"] or 0) + 1
            old_avg = float(prow["avg_duration_min"] or 0)
            new_avg = dur_min if n == 1 else (old_avg * (n - 1) + dur_min) / n
            conn.execute(
                """UPDATE places SET visit_count = ?, avg_duration_min = ?, last_visit = ?
                   WHERE id = ?""",
                (n, new_avg, now.isoformat(timespec="seconds"), place_id),
            )
        out = conn.execute(
            """SELECT v.*, p.name AS place_name FROM visits v
               JOIN places p ON p.id = v.place_id WHERE v.id = ?""",
            (visit_id,),
        ).fetchone()
        return dict(out) if out else None


def get_current_visit() -> dict | None:
    with get_db() as conn:
        row = conn.execute(
            """SELECT v.*, p.name AS place_name FROM visits v
               JOIN places p ON p.id = v.place_id
               WHERE v.departed_at IS NULL
               ORDER BY v.arrived_at DESC LIMIT 1""",
        ).fetchone()
        return dict(row) if row else None


def get_visits_for_place(place_id: int, days: int = 30) -> list[dict]:
    since = (datetime.now() - timedelta(days=max(1, days))).strftime("%Y-%m-%d %H:%M:%S")
    with get_db() as conn:
        rows = conn.execute(
            """SELECT v.*, p.name AS place_name FROM visits v
               JOIN places p ON p.id = v.place_id
               WHERE v.place_id = ? AND datetime(v.arrived_at) >= datetime(?)
               ORDER BY v.arrived_at DESC""",
            (place_id, since),
        ).fetchall()
        return [dict(r) for r in rows]


def get_today_visits() -> list[dict]:
    day = datetime.now().strftime("%Y-%m-%d")
    with get_db() as conn:
        rows = conn.execute(
            """SELECT v.*, p.name AS place_name FROM visits v
               JOIN places p ON p.id = v.place_id
               WHERE date(v.arrived_at) = ?
               ORDER BY v.arrived_at ASC""",
            (day,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_visits_by_day(day_of_week: int) -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            """SELECT v.*, p.name AS place_name FROM visits v
               JOIN places p ON p.id = v.place_id
               WHERE v.day_of_week = ?
               ORDER BY v.arrived_at DESC LIMIT 500""",
            (day_of_week,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_recent_visits(days: int = 7) -> list[dict]:
    since = (datetime.now() - timedelta(days=max(1, days))).isoformat(timespec="seconds")
    with get_db() as conn:
        rows = conn.execute(
            """SELECT v.*, p.name AS place_name FROM visits v
               JOIN places p ON p.id = v.place_id
               WHERE datetime(v.arrived_at) >= datetime(?)
               ORDER BY v.arrived_at DESC""",
            (since,),
        ).fetchall()
        return [dict(r) for r in rows]


def _transport_mode_from_speed_kmh(speed_kmh: float) -> str:
    if speed_kmh < 6:
        return "pied"
    if speed_kmh < 20:
        return "vélo"
    if speed_kmh < 50:
        return "transport"
    return "voiture"


def create_trip(
    from_place_id: int | None,
    to_place_id: int | None,
    started_at: datetime,
    ended_at: datetime,
    distance_km: float | None = None,
    route_points: list | None = None,
) -> int:
    duration_min = max(0.0, (ended_at - started_at).total_seconds() / 60.0)
    speed_kmh = 0.0
    if distance_km is not None and duration_min > 0:
        speed_kmh = float(distance_km) / (duration_min / 60.0)
    mode = _transport_mode_from_speed_kmh(speed_kmh)
    rp = json.dumps(route_points) if route_points else None
    with get_db() as conn:
        cur = conn.execute(
            """INSERT INTO trips (from_place_id, to_place_id, started_at, ended_at, duration_min,
                   distance_km, transport_mode, route_points)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                from_place_id,
                to_place_id,
                started_at.isoformat(timespec="seconds"),
                ended_at.isoformat(timespec="seconds"),
                duration_min,
                distance_km,
                mode,
                rp,
            ),
        )
        return cur.lastrowid


def get_recent_trips(days: int = 7) -> list[dict]:
    since = (datetime.now() - timedelta(days=max(1, days))).isoformat(timespec="seconds")
    with get_db() as conn:
        rows = conn.execute(
            """SELECT * FROM trips WHERE datetime(started_at) >= datetime(?)
               ORDER BY started_at DESC LIMIT 200""",
            (since,),
        ).fetchall()
        return [dict(r) for r in rows]


def add_location_pattern(pattern_type: str, description: str, place_id: int | None = None) -> int:
    with get_db() as conn:
        cur = conn.execute(
            """INSERT INTO location_patterns (pattern_type, description, place_id)
               VALUES (?, ?, ?)""",
            (pattern_type, description, place_id),
        )
        return cur.lastrowid


def get_active_location_patterns() -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            """SELECT * FROM location_patterns WHERE status = 'active'
               ORDER BY last_seen DESC"""
        ).fetchall()
        return [dict(r) for r in rows]


def increment_location_pattern(pattern_id: int) -> None:
    with get_db() as conn:
        conn.execute(
            """UPDATE location_patterns SET occurrences = occurrences + 1,
                   last_seen = CURRENT_TIMESTAMP WHERE id = ?""",
            (pattern_id,),
        )


def visits_summary_last_days(days: int = 30) -> list[dict]:
    """Résumé compact pour prompts (Haiku)."""
    return get_recent_visits(days=days)


def get_trips_for_today() -> list[dict]:
    day = datetime.now().strftime("%Y-%m-%d")
    with get_db() as conn:
        rows = conn.execute(
            """SELECT * FROM trips
               WHERE date(started_at) = ? OR date(ended_at) = ?
               ORDER BY started_at DESC""",
            (day, day),
        ).fetchall()
        return [dict(r) for r in rows]


def get_place_visit_stats(place_id: int, days: int = 90) -> dict:
    """Agrégats simples : fréquence par jour de semaine, moyennes d'heure arrivée/départ."""
    visits = get_visits_for_place(place_id, days=max(1, days))
    if not visits:
        return {
            "place_id": place_id,
            "visit_count": 0,
            "by_weekday": {},
            "avg_arrival_hour": None,
            "avg_departure_hour": None,
        }
    from collections import Counter

    wd = Counter()
    arr_h: list[float] = []
    dep_h: list[float] = []
    for v in visits:
        try:
            a = v.get("arrived_at")
            if a:
                at = datetime.fromisoformat(str(a).replace("Z", "+00:00"))
                wd[at.weekday()] += 1
                arr_h.append(at.hour + at.minute / 60.0)
        except (TypeError, ValueError):
            pass
        try:
            d = v.get("departed_at")
            if d:
                dt = datetime.fromisoformat(str(d).replace("Z", "+00:00"))
                dep_h.append(dt.hour + dt.minute / 60.0)
        except (TypeError, ValueError):
            pass
    n = len(visits)
    wday_names = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
    by_weekday = {wday_names[k]: wd[k] for k in sorted(wd.keys())}
    return {
        "place_id": place_id,
        "visit_count": n,
        "by_weekday": by_weekday,
        "avg_arrival_hour": round(sum(arr_h) / len(arr_h), 2) if arr_h else None,
        "avg_departure_hour": round(sum(dep_h) / len(dep_h), 2) if dep_h else None,
    }
