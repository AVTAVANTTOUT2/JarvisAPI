"""Logique métier des commandes en un clic et du suivi de livraison.

Le clic sur une suggestion tient lieu de confirmation humaine — mais un clic
ne vaut consentement que si l'utilisateur voyait le montant au moment où il l'a
fait. Le parcours implémenté ici respecte cette contrainte sans imposer une
seconde validation :

1. La suggestion affichée porte un montant maximum figé à sa génération.
2. Le client renvoie ce montant avec le clic. S'il ne correspond pas à celui
   enregistré, l'écran était périmé et rien n'est engagé.
3. Le panier réel est construit, son total lu à l'écran, puis comparé au
   montant autorisé. En dessous, la commande part. Au-dessus, JARVIS s'arrête
   et rend un plan à confirmer explicitement.

Autrement dit : un clic dépense au plus ce qui était écrit sur le bouton.
"""

from __future__ import annotations

import logging
from typing import Any

from database import (
    claim_suggestion,
    get_active_suggestion_by_slot,
    get_orders_awaiting_delivery,
    release_suggestion,
    update_food_order_delivery,
)
from integrations.uber_eats import (
    UberEatsError,
    UberEatsLimitExceeded,
    UberEatsUnavailable,
    revoke_order_plan,
    uber_eats,
)
from jarvis.event_bus import JarvisEvent, event_bus

logger = logging.getLogger("jarvis.food")

#: Écart toléré entre le montant affiché au clic et celui figé en base.
PRICE_MATCH_TOLERANCE_EUR = 0.01
FOOD_EVENT_TYPE = "food.order_updated"


class QuickOrderError(RuntimeError):
    """Le clic ne peut pas être honoré ; ``status_code`` porte la réponse HTTP."""

    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


def _emit(payload: dict[str, Any]) -> None:
    """Pousse une mise à jour vers les clients connectés, sans jamais lever."""
    try:
        event_bus.emit_nowait(
            JarvisEvent(type=FOOD_EVENT_TYPE, agent="food", data=payload, source="api.food")
        )
    except (RuntimeError, ValueError) as exc:
        logger.debug("[food] événement non diffusé : %s", exc)


def _public_outcome(outcome: Any, suggestion: dict[str, Any]) -> dict[str, Any]:
    """Réduit une issue de commande à ce que l'interface doit connaître."""
    data = outcome.as_dict()
    return {
        "ok": data["ok"],
        "status": data["status"],
        "restaurant": data["restaurant"],
        "items_label": data["items_label"],
        "total_price": data["total_price"],
        "currency": data["currency"],
        "dry_run": data["dry_run"],
        "error": data["error"],
        "slot": suggestion["slot"],
        "suggestion_id": suggestion["id"],
    }


def _authorised_amount(suggestion: dict[str, Any], accepted_price: object) -> float:
    """Vérifie que le client a bien cliqué sur le montant enregistré.

    Raises:
        QuickOrderError: montant absent, illisible ou différent de celui figé.
    """
    stored = suggestion.get("max_price")
    if stored is None:
        raise QuickOrderError(
            f"La suggestion {suggestion['slot']} n'a pas de montant maximum : "
            "commande en un clic refusée.",
            status_code=409,
        )
    try:
        accepted = float(accepted_price)
    except (TypeError, ValueError) as exc:
        raise QuickOrderError(
            "Le montant accepté est absent ou illisible ; le clic n'engage rien.",
            status_code=400,
        ) from exc
    if abs(accepted - float(stored)) > PRICE_MATCH_TOLERANCE_EUR:
        raise QuickOrderError(
            f"Montant affiché ({accepted:.2f}) différent du montant enregistré "
            f"({float(stored):.2f}) : rafraîchir les suggestions avant de commander.",
            status_code=409,
        )
    return float(stored)


async def quick_order(slot: int, accepted_price: object) -> dict[str, Any]:
    """Commande la suggestion d'un emplacement, dans la limite acceptée.

    Args:
        slot: Emplacement affiché (1, 2 ou 3).
        accepted_price: Montant maximum lu sur le bouton par l'utilisateur.

    Returns:
        L'issue de la commande, ou une demande de confirmation si le panier
        réel dépasse le montant autorisé.

    Raises:
        QuickOrderError: suggestion absente, périmée, déjà prise, ou montant
            incohérent avec l'affichage.
    """
    suggestion = get_active_suggestion_by_slot(slot)
    if not suggestion:
        raise QuickOrderError(
            f"Aucune suggestion active à l'emplacement {slot} : elle a expiré "
            "ou a déjà été commandée.",
            status_code=404,
        )
    authorised = _authorised_amount(suggestion, accepted_price)
    if not suggestion.get("items"):
        raise QuickOrderError(
            f"La suggestion {slot} ne contient aucun article.", status_code=409
        )

    if not claim_suggestion(int(suggestion["id"])):
        raise QuickOrderError(
            f"La suggestion {slot} vient d'être prise par une autre demande.",
            status_code=409,
        )

    try:
        plan, _ = await uber_eats.prepare_order(
            suggestion["restaurant"], suggestion["items"]
        )
    except UberEatsError as exc:
        release_suggestion(int(suggestion["id"]))
        status = 503 if isinstance(exc, UberEatsUnavailable) else 409
        raise QuickOrderError(str(exc), status_code=status) from exc

    if plan.total_price > authorised + PRICE_MATCH_TOLERANCE_EUR:
        # Le panier coûte plus cher que ce que l'utilisateur a validé du regard.
        # On ne consomme pas le plan : il devra confirmer le nouveau montant.
        logger.info(
            "[food] clic non honoré : panier %.2f %s au-dessus du montant autorisé %.2f",
            plan.total_price,
            plan.currency,
            authorised,
        )
        release_suggestion(int(suggestion["id"]))
        payload = {
            "ok": False,
            "status": "confirmation_required",
            "slot": suggestion["slot"],
            "suggestion_id": suggestion["id"],
            "restaurant": plan.restaurant,
            "items_label": ", ".join(item.label() for item in plan.items),
            "total_price": plan.total_price,
            "authorised_price": authorised,
            "currency": plan.currency,
            "plan_id": plan.plan_id,
            "dry_run": plan.dry_run,
        }
        _emit({k: v for k, v in payload.items() if k != "plan_id"})
        return payload

    try:
        outcome = await uber_eats.confirm_order(
            plan.plan_id, suggestion_id=int(suggestion["id"])
        )
    except UberEatsError as exc:
        revoke_order_plan(plan.plan_id)
        release_suggestion(int(suggestion["id"]))
        status = 409 if isinstance(exc, UberEatsLimitExceeded) else 502
        raise QuickOrderError(str(exc), status_code=status) from exc

    if not outcome.ok:
        release_suggestion(int(suggestion["id"]))

    result = _public_outcome(outcome, suggestion)
    _emit(result)
    return result


async def refresh_delivery_progress() -> dict[str, Any]:
    """Relit le statut des commandes encore en cours et diffuse les changements.

    Returns:
        Le nombre de commandes inspectées et celles dont le statut a bougé.
    """
    orders = get_orders_awaiting_delivery()
    if not orders:
        return {"checked": 0, "updated": 0, "orders": []}

    from integrations.uber_eats_discovery import uber_eats_discovery

    updated: list[dict[str, Any]] = []
    for order in orders:
        tracking_url = order.get("tracking_url")
        if not tracking_url:
            continue
        try:
            progress = await uber_eats_discovery.read_delivery_progress(tracking_url)
        except UberEatsError as exc:
            logger.warning(
                "[food] suivi indisponible pour la commande %s : %s", order.get("id"), exc
            )
            continue
        if progress.status is None:
            continue
        if progress.status == order.get("delivery_status") and (
            progress.eta_minutes == order.get("eta_minutes")
        ):
            continue
        row = update_food_order_delivery(
            int(order["id"]),
            delivery_status=progress.status,
            eta_minutes=progress.eta_minutes,
        )
        if not row:
            continue
        payload = {
            "ok": True,
            "status": "delivery_update",
            "order_id": row["id"],
            "restaurant": row["restaurant"],
            "delivery_status": row["delivery_status"],
            "eta_minutes": row["eta_minutes"],
        }
        updated.append(payload)
        _emit(payload)

    return {"checked": len(orders), "updated": len(updated), "orders": updated}


def integration_status() -> dict[str, Any]:
    """État consolidé affiché en tête de la page Nourriture."""
    from integrations.uber_eats_discovery import uber_eats_discovery
    from integrations.uber_eats_settings import get_settings

    state = uber_eats_discovery.availability()
    settings = get_settings()
    return {
        "enabled": state["enabled"],
        "dry_run": state["dry_run"],
        "can_browse": state["can_browse"],
        "can_place_real_order": state["can_place_real_order"],
        "can_scrape": state["can_scrape"],
        "suggestions_enabled": settings.suggestions_enabled,
        "selectors_verified": state["selectors_verified"],
        "reasons": state["reasons"],
        "max_order_price": settings.max_order_price,
        "max_daily_spend": settings.max_daily_spend,
        "max_daily_orders": settings.max_daily_orders,
    }
