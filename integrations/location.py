"""Gestionnaire de localisation — points GPS, visites, trajets entre lieux nommés."""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any

import config
from database.location_helpers import (
    add_location,
    create_trip,
    end_visit,
    get_all_places,
    get_current_location,
    get_current_visit,
    get_last_known_location,
    get_location_history,
    get_place,
    get_today_visits,
    get_trips_for_today,
    haversine,
    resolve_place,
    start_visit,
)

logger = logging.getLogger(__name__)


class LocationManager:
    """État courant (mémoire processus) + transitions arrivée / départ / trajet."""

    def __init__(self) -> None:
        self.current_place_id: int | None = None
        self.current_visit_id: int | None = None
        self.last_location: dict[str, Any] | None = None
        self._last_inside_ts: dict[int, datetime] = {}

    def _sync_from_db(self) -> None:
        v = get_current_visit()
        if v:
            self.current_visit_id = int(v["id"])
            self.current_place_id = int(v["place_id"])
        else:
            self.current_visit_id = None
            self.current_place_id = None

    async def process_location(
        self,
        latitude: float,
        longitude: float,
        altitude: float | None = None,
        accuracy: float | None = None,
        speed: float | None = None,
        heading: float | None = None,
        source: str = "app",
        point_time: datetime | None = None,
        created_at: str | None = None,
    ) -> dict[str, Any]:
        """Point GPS entrant : historique, résolution lieu, visites et trajets."""
        ts = point_time
        if ts is None and created_at:
            try:
                ts = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            except ValueError:
                ts = None
        if ts is None:
            ts = datetime.now()

        created_iso = ts.isoformat(timespec="seconds")
        # Toujours persister le point brut (companion Android / Shortcuts).
        # LOCATION_TRACKING=false désactive seulement les transitions lieux/visites.
        location_history_id = add_location(
            latitude,
            longitude,
            altitude=altitude,
            accuracy=accuracy,
            speed=speed,
            heading=heading,
            source=source or "app",
            created_at=created_iso,
        )

        if not getattr(config, "LOCATION_TRACKING", True):
            self.last_location = {
                "latitude": latitude,
                "longitude": longitude,
                "at": created_iso,
                "place_id": None,
            }
            return {
                "place": None,
                "place_id": None,
                "arrived": False,
                "departed": False,
                "visit_id": None,
                "skipped": True,
                "location_history_id": location_history_id,
            }

        self._sync_from_db()

        resolved = resolve_place(latitude, longitude)
        new_pid = int(resolved["id"]) if resolved else None
        old_pid = self.current_place_id

        arrived = False
        departed = False
        visit_out = self.current_visit_id

        if new_pid == old_pid:
            if new_pid is not None:
                self._last_inside_ts[new_pid] = ts
            self.last_location = {
                "latitude": latitude,
                "longitude": longitude,
                "at": created_iso,
                "place_id": new_pid,
            }
            pname = resolved["name"] if resolved else None
            return {
                "place": pname,
                "place_id": new_pid,
                "arrived": False,
                "departed": False,
                "visit_id": visit_out,
                "location_history_id": location_history_id,
            }

        # Transition : lieu changé (incluant entrée/sortie d'un périmètre nommé)
        if old_pid is not None and new_pid is None:
            if self.current_visit_id:
                pl = get_place(old_pid)
                pname = pl["name"] if pl else str(old_pid)
                end_visit(self.current_visit_id, ended_at=ts)
                logger.info("[location] Départ de %s", pname)
                departed = True
            self.current_visit_id = None
            self.current_place_id = None

        elif old_pid is None and new_pid is not None:
            vid = start_visit(new_pid, arrived_at=ts)
            self.current_visit_id = vid
            self.current_place_id = new_pid
            self._last_inside_ts[new_pid] = ts
            pl = get_place(new_pid)
            pname = pl["name"] if pl else str(new_pid)
            logger.info("[location] Arrivée à %s", pname)
            arrived = True

        elif old_pid is not None and new_pid is not None and old_pid != new_pid:
            pa = get_place(old_pid)
            pb = get_place(new_pid)
            if self.current_visit_id:
                end_visit(self.current_visit_id, ended_at=ts)
                departed = True
                if pa:
                    logger.info("[location] Départ de %s", pa.get("name"))

            t_start = self._last_inside_ts.get(old_pid) or ts
            dist_km = None
            if pa and pb:
                try:
                    dist_km = haversine(
                        float(pa["latitude"]),
                        float(pa["longitude"]),
                        float(pb["latitude"]),
                        float(pb["longitude"]),
                    ) / 1000.0
                except (TypeError, ValueError):
                    pass
            create_trip(old_pid, new_pid, t_start, ts, distance_km=dist_km)

            vid = start_visit(new_pid, arrived_at=ts)
            self.current_visit_id = vid
            self.current_place_id = new_pid
            self._last_inside_ts[new_pid] = ts
            arrived = True
            if pb:
                logger.info("[location] Arrivée à %s", pb.get("name"))

        self.last_location = {
            "latitude": latitude,
            "longitude": longitude,
            "at": created_iso,
            "place_id": new_pid,
        }

        pl_final = get_place(new_pid) if new_pid else None
        return {
            "place": pl_final["name"] if pl_final else None,
            "place_id": new_pid,
            "arrived": arrived,
            "departed": departed,
            "visit_id": self.current_visit_id,
            "location_history_id": location_history_id,
        }

    async def get_status(self) -> dict[str, Any]:
        """Dernière position connue (tout âge), visite en cours et durée.

        ``current_location`` reste peuplé même si le point a plus de 10 minutes
        ou si ``LOCATION_TRACKING`` est false — le frontend calcule la fraîcheur
        via ``created_at``. ``get_current_location()`` (fenêtre courte) reste
        réservé à ``name_place`` / actions.
        """
        loc = get_last_known_location()
        visit = get_current_visit()
        since_min = None
        if visit and visit.get("arrived_at"):
            try:
                at = datetime.fromisoformat(str(visit["arrived_at"]).replace("Z", "+00:00"))
                since_min = round((datetime.now() - at).total_seconds() / 60.0, 1)
            except ValueError:
                since_min = None
        recent_points = get_location_history(hours=24)
        return {
            "tracking_enabled": bool(getattr(config, "LOCATION_TRACKING", True)),
            "default_radius_m": int(getattr(config, "LOCATION_PLACE_RADIUS", 100)),
            "current_location": loc,
            "current_visit": visit,
            "minutes_at_place": since_min,
            "points_24h": len(recent_points),
            "state": {
                "current_place_id": self.current_place_id,
                "current_visit_id": self.current_visit_id,
            },
        }

    async def get_daily_summary(self) -> dict[str, Any]:
        """Résumé jour civil : visites, durées par lieu, distance trajets, nombre de déplacements."""
        visits_today = get_today_visits()
        trips_today = get_trips_for_today()
        duration_by_place: dict[str, float] = {}
        for v in visits_today:
            name = str(v.get("place_name") or "?")
            dm = v.get("duration_min")
            if dm is not None:
                try:
                    duration_by_place[name] = duration_by_place.get(name, 0.0) + float(dm)
                except (TypeError, ValueError):
                    pass
        dist_sum = 0.0
        for t in trips_today:
            d = t.get("distance_km")
            if d is not None:
                try:
                    dist_sum += float(d)
                except (TypeError, ValueError):
                    pass
        return {
            "date": date.today().isoformat(),
            "visits": visits_today,
            "duration_by_place_min": duration_by_place,
            "trips": trips_today,
            "trip_count": len(trips_today),
            "total_distance_km": round(dist_sum, 3),
            "places_named_count": len(get_all_places()),
        }


location_manager = LocationManager()
