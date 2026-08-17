"""Handlers de santé : sonde de vie publique et diagnostic authentifié.

Les sondes publiques restent minimales ; les diagnostics restent authentifiés :

``GET /api/health/live``
    Sonde de vie. Publique — elle traverse le verrou de session, sinon un
    superviseur ne pourrait pas distinguer « application verrouillée » de
    « application morte ». Elle ne dit donc **rien** d'autre que « ce
    processus répond » : ni version, ni hôte, ni composant, ni compteur.

    Le chemin porte ``/live`` plutôt que le ``/api/health`` habituel parce que
    ``tv/server.py`` — une autre application, sur un autre port — expose déjà
    sa propre route ``/api/health``. Deux serveurs du même dépôt répondant au
    même chemin avec deux significations différentes est exactement le genre
    d'ambiguïté qu'une sonde de vie ne doit pas créer.

``GET /api/health/detail``
    Diagnostic complet. Passe par le verrou de session standard, comme
    n'importe quelle route ``/api/*``. L'agrégation vit dans
    ``jarvis/health.py`` ; ce module n'ajoute que le transport HTTP.

``GET /health/ready``
    Readiness publique réduite à ``ready`` ou ``not_ready``. Elle exige un
    heartbeat ingestion frais et les connecteurs locaux liés/synchronisés.

``GET /api/data-health``
    Couverture, fraîcheur et files d'ingestion sans payload personnel. Cette
    vue suit le verrou de session standard.

Les deux réponses portent ``Cache-Control: no-store`` : un état de santé
rejoué depuis un cache navigateur ou un proxy est pire qu'aucun état.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import Query, Response

from jarvis import health
import config
from database import get_ingestion_health_summary, get_metric_history

logger = logging.getLogger(__name__)

_NO_STORE = {"Cache-Control": "no-store"}

#: Codes HTTP du diagnostic. Une panne partielle reste une réponse valide :
#: c'est justement quand tout ne va pas bien qu'il faut pouvoir lire la page.
_STATUS_CODES = {
    health.HEALTHY: 200,
    health.DEGRADED: 200,
    health.UNKNOWN: 200,
    health.UNAVAILABLE: 503,
}


async def api_health_live(response: Response) -> dict[str, Any]:
    """Sonde de vie minimale — aucun accès disque, aucune donnée."""
    response.headers.update(_NO_STORE)
    return {"status": "ok"}


def _utc_datetime(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        value = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _ingestion_readiness() -> tuple[bool, dict[str, Any]]:
    """Évalue le service et les trois connecteurs sans exposer de contenu."""

    try:
        report = get_ingestion_health_summary()
    except Exception as exc:
        logger.warning("[health] ingestion state unavailable: %s", type(exc).__name__)
        return False, {"status": "unavailable", "reason": "state_unavailable"}

    states = {str(item["source"]): item for item in report.get("states", [])}
    service = states.get("__service__")
    heartbeat = _utc_datetime(service.get("heartbeat_at") if service else None)
    heartbeat_age = (
        max(0.0, (datetime.now(timezone.utc) - heartbeat).total_seconds())
        if heartbeat
        else None
    )
    service_ok = bool(
        config.INGESTION_SERVICE_ENABLED
        and service
        and service.get("status") not in {"error", "disabled"}
        and heartbeat_age is not None
        and heartbeat_age <= config.INGESTION_HEARTBEAT_MAX_AGE_S
    )

    freshness_limits = {
        "imessage": config.INGESTION_IMESSAGE_INTERVAL_S * 2,
        "mail": config.INGESTION_MAIL_INTERVAL_S * 2,
        "calendar": config.INGESTION_CALENDAR_INTERVAL_S * 2,
    }
    bindings = report.get("bindings", {})
    unbound = set(bindings.get("unbound", []))
    permission_missing = set(bindings.get("permission_missing", []))
    connectors: dict[str, dict[str, Any]] = {}
    connectors_ok = True
    now = datetime.now(timezone.utc)
    for source, max_age in freshness_limits.items():
        state = states.get(source)
        success_at = _utc_datetime(state.get("last_success_at") if state else None)
        age = max(0.0, (now - success_at).total_seconds()) if success_at else None
        bound = source not in unbound
        permission_granted = source not in permission_missing
        healthy = bool(
            bound
            and permission_granted
            and state
            and state.get("status") not in {"error", "disabled"}
            and age is not None
            and age <= max_age
        )
        connectors_ok = connectors_ok and healthy
        connectors[source] = {
            "status": (
                "unbound"
                if not bound
                else "permission_required"
                if not permission_granted
                else "fresh"
                if healthy
                else "unavailable"
                if state and state.get("status") == "error"
                else "stale"
            ),
            "permission_state": (
                "unbound"
                if not bound
                else "granted"
                if permission_granted
                else "unknown"
            ),
            "completeness": state.get("completeness", "unknown")
            if state
            else "unknown",
            "coverage_start_utc": state.get("coverage_start_utc") if state else None,
            "coverage_end_utc": state.get("coverage_end_utc") if state else None,
            "last_success_at": state.get("last_success_at") if state else None,
            "lag_seconds": round(age, 3) if age is not None else None,
            "error_code": state.get("error_code") if state else None,
        }

    ready = service_ok and connectors_ok
    detail = {
        "status": "ready" if ready else "degraded",
        "service": {
            "status": "fresh" if service_ok else "stale",
            "heartbeat_at": service.get("heartbeat_at") if service else None,
            "heartbeat_age_seconds": (
                round(heartbeat_age, 3) if heartbeat_age is not None else None
            ),
        },
        "connectors": connectors,
        "jobs": report.get("jobs", []),
    }
    return ready, detail


async def api_health_ready(response: Response) -> dict[str, str]:
    """Readiness publique minimale : aucun détail de source ou de profil."""

    ready, _detail = _ingestion_readiness()
    response.headers.update(_NO_STORE)
    response.status_code = 200 if ready else 503
    return {"status": "ready" if ready else "not_ready"}


async def api_data_health(response: Response) -> dict[str, Any]:
    """Diagnostic authentifié des couvertures et files d'ingestion."""

    ready, detail = _ingestion_readiness()
    response.headers.update(_NO_STORE)
    response.status_code = 200 if ready else 503
    return detail


async def api_health_detail(
    response: Response,
    refresh: bool = Query(
        False,
        description="Ignore le relevé partagé et resonde immédiatement.",
    ),
) -> dict[str, Any]:
    """Agrégat des composants vérifiables, avec états explicites."""
    report = await health.get_health(force=refresh)
    response.headers.update(_NO_STORE)
    response.status_code = _STATUS_CODES.get(report["status"], 200)
    return report


async def api_metrics_history(
    hours: int = Query(24, ge=1, le=24 * 365),
) -> dict[str, Any]:
    """Buckets persistants et tendances du diagnostic opérationnel."""
    return get_metric_history(hours)


__all__ = [
    "api_data_health",
    "api_health_detail",
    "api_health_live",
    "api_health_ready",
    "api_metrics_history",
]
