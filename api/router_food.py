"""Routes de la page Nourriture : suggestions, commande en un clic, suivi.

Toutes ces routes passent par le verrou de session global et exigent le jeton
synchronisé sur les mutations : commander un repas est une dépense, elle ne
peut pas dépendre d'une simple requête portant un cookie.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from api.errors import api_error
from api.food_control import (
    FoodControlError,
    cancel_manual_order,
    capture_status,
    confirm_manual_order,
    peek_manual_order,
    prepare_manual_order,
    probe_session,
    reload_selectors,
    selectors_report,
    session_report,
    start_capture,
    stop_capture,
)
from api.food_support import (
    QuickOrderError,
    integration_status,
    quick_order,
    refresh_delivery_progress,
)
from database import (
    get_active_suggestions,
    get_daily_food_order_stats,
    get_food_orders,
    get_food_preferences,
    get_menu_items,
    get_menu_restaurants,
    get_orders_awaiting_delivery,
    rate_food_order,
)
from database.food_orders import FoodOrderError
from integrations.uber_eats_settings import (
    FoodSettingsError,
    get_ceilings,
    get_settings,
    reset_settings,
    update_settings,
)

router = APIRouter()
logger = logging.getLogger("jarvis")

MAX_HISTORY = 100


@router.get("/api/food/status")
async def api_food_status():
    """État de l'intégration, plafonds et consommation du jour."""
    return {"integration": integration_status(), "today": get_daily_food_order_stats()}


@router.get("/api/food/suggestions")
async def api_food_suggestions():
    """Suggestions encore cliquables, par emplacement croissant."""
    suggestions = [
        {
            "id": item["id"],
            "slot": item["slot"],
            "restaurant": item["restaurant"],
            "items": item["items"],
            "estimated_price": item["estimated_price"],
            "max_price": item["max_price"],
            "currency": item["currency"],
            "reasoning": item["reasoning"],
            "score": item["score"],
            "factors": item["factors"],
            "expires_at": item["expires_at"],
        }
        for item in get_active_suggestions()
    ]
    return {"suggestions": suggestions}


@router.post("/api/food/suggestions/generate")
async def api_food_suggestions_generate():
    """Recalcule les préférences puis régénère le lot de suggestions."""
    from scripts.food_intelligence import generate_suggestions, learn_preferences

    learned = learn_preferences()
    generated = await generate_suggestions()
    return {"learned": learned["preferences"], "generation": generated}


@router.post("/api/food/suggestions/{slot}/order")
async def api_food_quick_order(slot: int, payload: dict):
    """Commande la suggestion d'un emplacement, dans la limite affichée.

    Body JSON : `{accepted_price}` — le montant lu sur le bouton. Il doit
    correspondre au montant figé à la génération, sinon rien n'est engagé.
    """
    if slot < 1:
        raise HTTPException(400, "`slot` doit être supérieur ou égal à 1")
    try:
        return await quick_order(slot, payload.get("accepted_price"))
    except QuickOrderError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc


@router.get("/api/food/orders")
async def api_food_orders(limit: int = 30):
    """Historique des commandes, de la plus récente à la plus ancienne."""
    bounded = max(1, min(int(limit), MAX_HISTORY))
    return {"orders": get_food_orders(limit=bounded)}


@router.post("/api/food/orders/{order_id}/rating")
async def api_food_rate_order(order_id: int, payload: dict):
    """Note un repas déjà commandé. Body JSON : `{rating}` de 1 à 5."""
    try:
        order = rate_food_order(order_id, payload.get("rating"))
    except FoodOrderError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not order:
        raise HTTPException(404, f"Commande {order_id} introuvable")
    return {"order": order}


@router.get("/api/food/delivery")
async def api_food_delivery():
    """Commandes encore en route, avec leur avancement connu."""
    return {"orders": get_orders_awaiting_delivery()}


@router.post("/api/food/delivery/refresh")
async def api_food_delivery_refresh():
    """Relit le suivi des commandes en cours et diffuse les changements."""
    return await refresh_delivery_progress()


@router.get("/api/food/menus")
async def api_food_menus():
    """Restaurants dont le menu est en cache, avec sa fraîcheur."""
    return {"restaurants": get_menu_restaurants()}


@router.post("/api/food/menus/refresh")
async def api_food_menus_refresh(payload: dict | None = None):
    """Relève les menus des restaurants les plus commandés.

    Body JSON optionnel : `{restaurants: [...]}` pour cibler un relevé précis.
    """
    from scripts.food_menu_refresh import refresh_tracked_menus

    requested = (payload or {}).get("restaurants")
    names = [str(name) for name in requested] if isinstance(requested, list) else None
    try:
        return await refresh_tracked_menus(names)
    except (OSError, RuntimeError, ValueError) as exc:
        logger.error("[food] relevé de menus impossible : %s", exc)
        raise api_error(
            503,
            "food_menu_refresh_failed",
            "Actualisation des menus indisponible",
        ) from exc


@router.get("/api/food/menus/{restaurant}")
async def api_food_menu_items(restaurant: str):
    """Articles connus d'un restaurant, pour composer un panier à la main."""
    items = get_menu_items(restaurant)
    if not items:
        raise HTTPException(404, f"Aucun menu en cache pour « {restaurant} »")
    return {"restaurant": restaurant, "items": items}


@router.get("/api/food/preferences")
async def api_food_preferences():
    """Préférences dérivées de l'historique, avec leur confiance."""
    return {"preferences": get_food_preferences()}


# ── Réglages pilotables ─────────────────────────────────────────────────────


@router.get("/api/food/settings")
async def api_food_settings():
    """Réglages courants et bornes dures issues du fichier `.env`."""
    return {"settings": get_settings().as_dict(), "ceilings": get_ceilings()}


@router.patch("/api/food/settings")
async def api_food_settings_update(payload: dict):
    """Modifie les réglages. Une valeur au-dessus de la borne `.env` est refusée."""
    try:
        settings = update_settings(payload)
    except FoodSettingsError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"settings": settings.as_dict(), "ceilings": get_ceilings()}


@router.post("/api/food/settings/reset")
async def api_food_settings_reset():
    """Efface les réglages persistés et revient aux valeurs du `.env`."""
    return {"settings": reset_settings().as_dict(), "ceilings": get_ceilings()}


# ── Panier libre en deux passes ─────────────────────────────────────────────


@router.post("/api/food/cart/prepare")
async def api_food_cart_prepare(payload: dict):
    """Construit un panier et retourne le total réel. N'engage aucune dépense.

    Body JSON : `{restaurant, items: [{name, quantity?, notes?}]}`.
    """
    try:
        return await prepare_manual_order(payload.get("restaurant"), payload.get("items"))
    except FoodControlError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc


@router.get("/api/food/cart/{plan_id}")
async def api_food_cart_peek(plan_id: str):
    """Relit un panier en attente sans le consommer."""
    try:
        return peek_manual_order(plan_id)
    except FoodControlError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc


@router.post("/api/food/cart/{plan_id}/confirm")
async def api_food_cart_confirm(plan_id: str):
    """Consomme le panier et passe la commande."""
    try:
        return await confirm_manual_order(plan_id)
    except FoodControlError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc


@router.delete("/api/food/cart/{plan_id}")
async def api_food_cart_cancel(plan_id: str):
    """Abandonne un panier en attente."""
    try:
        return cancel_manual_order(plan_id)
    except FoodControlError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc


# ── Diagnostic et réparation ────────────────────────────────────────────────


@router.get("/api/food/selectors")
async def api_food_selectors():
    """État du fichier de sélecteurs : validité, couverture, vérification."""
    return selectors_report()


@router.post("/api/food/selectors/reload")
async def api_food_selectors_reload():
    """Relit le fichier de sélecteurs après une modification manuelle."""
    return reload_selectors()


@router.get("/api/food/session")
async def api_food_session():
    """Présence et fraîcheur de la session enregistrée."""
    return session_report()


@router.post("/api/food/session/probe")
async def api_food_session_probe():
    """Vérifie en conditions réelles qu'Uber reconnaît encore la session."""
    return await probe_session()


@router.post("/api/food/session/capture")
async def api_food_session_capture(payload: dict | None = None):
    """Ouvre une fenêtre de capture sur la machine hôte.

    Body JSON optionnel : `{mode}` — `session` (défaut) ou `codegen`.
    """
    try:
        return await start_capture(str((payload or {}).get("mode") or "session"))
    except FoodControlError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc


@router.delete("/api/food/session/capture")
async def api_food_session_capture_stop():
    """Interrompt la capture en cours."""
    return await stop_capture()


@router.get("/api/food/session/capture")
async def api_food_session_capture_status():
    """État de la dernière capture déclenchée depuis l'interface."""
    return capture_status()
