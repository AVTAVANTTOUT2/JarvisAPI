"""Handlers de santé : sonde de vie publique et diagnostic authentifié.

Deux routes, deux publics, deux niveaux d'information :

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

Les deux réponses portent ``Cache-Control: no-store`` : un état de santé
rejoué depuis un cache navigateur ou un proxy est pire qu'aucun état.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import Query, Response

from jarvis import health
from database import get_metric_history

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


__all__ = ["api_health_detail", "api_health_live", "api_metrics_history"]
