"""Réglages Uber Eats modifiables depuis l'interface, bornés par le fichier .env.

Piloter la commande de repas depuis le navigateur suppose de pouvoir activer
l'intégration, quitter la simulation et ajuster les plafonds sans éditer un
fichier ni redémarrer. Cela déplace un pouvoir de dépense vers une surface
réseau : le modèle retenu sépare donc deux niveaux.

- **Le fichier `.env` fixe les bornes dures.** Elles ne sont modifiables que
  sur la machine, avec un redémarrage. `UBER_EATS_MAX_ORDER_PRICE` n'est plus
  le plafond courant mais le plafond *maximal atteignable*.
- **La base porte les réglages courants.** L'interface peut les baisser
  librement, jamais les faire dépasser la borne correspondante.

Conséquence concrète : une session compromise peut au pire dépenser ce que le
propriétaire de la machine a déjà autorisé dans son `.env`. Elle ne peut pas
relever elle-même le plafond pour dépenser davantage.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import config
import database
from database import get_setting, set_setting

logger = logging.getLogger("jarvis.food")

SETTINGS_PREFIX = "uber_eats."

#: Réglages booléens : chacun ne peut être activé que si le `.env` l'autorise.
#: Un interrupteur fermé dans le fichier ne peut pas être ouvert par le réseau.
BOOLEAN_KEYS: tuple[str, ...] = (
    "enabled",
    "dry_run",
    "menu_scrape_enabled",
    "suggestions_enabled",
    "headless",
)

#: Réglages numériques : (clé, attribut de borne dans config, type, minimum).
NUMERIC_LIMITS: tuple[tuple[str, str, type, float], ...] = (
    ("max_order_price", "UBER_EATS_MAX_ORDER_PRICE", float, 0.0),
    ("max_daily_spend", "UBER_EATS_MAX_DAILY_SPEND", float, 0.0),
    ("max_daily_orders", "UBER_EATS_MAX_DAILY_ORDERS", int, 0),
    ("max_items", "UBER_EATS_MAX_ITEMS", int, 1),
    ("max_item_quantity", "UBER_EATS_MAX_ITEM_QUANTITY", int, 1),
)

WRITABLE_KEYS: frozenset[str] = frozenset(
    BOOLEAN_KEYS + tuple(key for key, *_ in NUMERIC_LIMITS)
)


class FoodSettingsError(ValueError):
    """Un réglage proposé est inconnu, mal typé ou hors des bornes du `.env`."""


@dataclass(frozen=True, slots=True)
class FoodSettings:
    """Réglages effectifs, déjà bornés."""

    enabled: bool
    dry_run: bool
    menu_scrape_enabled: bool
    suggestions_enabled: bool
    headless: bool
    max_order_price: float
    max_daily_spend: float
    max_daily_orders: int
    max_items: int
    max_item_quantity: int

    def as_dict(self) -> dict[str, Any]:
        """Représentation transmissible à l'interface."""
        return asdict(self)


def _env_bool(attribute: str, default: bool) -> bool:
    return bool(getattr(config, attribute, default))


def _stored(key: str) -> str | None:
    """Lit un réglage persisté, ou ``None`` s'il n'a jamais été modifié."""
    db_path = Path(database.DB_PATH)
    if str(db_path) != ":memory:" and not db_path.exists():
        # Lire les valeurs par défaut avant ``init_db()`` doit rester une
        # opération pure. Ouvrir SQLite ici créerait un fichier vide que le
        # reste de l'application pourrait prendre à tort pour une base
        # initialisée.
        return None
    try:
        raw = get_setting(f"{SETTINGS_PREFIX}{key}", "")
    except sqlite3.OperationalError as exc:
        # Les validateurs purs sont aussi utilisés avant l'initialisation de la
        # base (CLI, imports à froid, tests). Seule l'absence de la table est un
        # état de démarrage valide ; toute autre erreur SQLite reste visible.
        if "no such table: app_settings" not in str(exc):
            raise
        return None
    return raw or None


def _resolve_bool(key: str, env_attribute: str, *, env_default: bool) -> bool:
    """Combine la borne du `.env` et le choix de l'interface.

    Pour ``dry_run`` la logique s'inverse : le fichier impose la simulation, il
    ne peut pas l'interdire. Si le `.env` dit « simulation », l'interface ne
    peut pas en sortir.
    """
    env_value = _env_bool(env_attribute, env_default)
    stored = _stored(key)
    if stored is None:
        return env_value
    chosen = stored == "true"
    if key == "dry_run":
        # `.env` en simulation → verrouillé en simulation.
        return True if env_value else chosen
    # Interrupteur fermé dans le fichier → impossible à ouvrir depuis le réseau.
    return chosen and env_value


def _resolve_number(key: str, env_attribute: str, caster: type, minimum: float) -> Any:
    """Retourne la valeur courante, jamais supérieure à la borne du `.env`."""
    ceiling = caster(getattr(config, env_attribute, minimum))
    stored = _stored(key)
    if stored is None:
        return ceiling
    try:
        value = caster(stored)
    except (TypeError, ValueError):
        logger.warning(
            "[food] réglage %s illisible en base (%r) — borne du .env appliquée",
            key,
            stored,
        )
        return ceiling
    return max(caster(minimum), min(value, ceiling))


def get_settings() -> FoodSettings:
    """Assemble les réglages effectifs, bornés par le `.env`."""
    return FoodSettings(
        enabled=_resolve_bool("enabled", "UBER_EATS_ENABLED", env_default=False),
        dry_run=_resolve_bool("dry_run", "UBER_EATS_DRY_RUN", env_default=True),
        menu_scrape_enabled=_resolve_bool(
            "menu_scrape_enabled", "FOOD_MENU_SCRAPE_ENABLED", env_default=False
        ),
        suggestions_enabled=_resolve_bool(
            "suggestions_enabled", "FOOD_SUGGESTIONS_ENABLED", env_default=False
        ),
        headless=_resolve_bool("headless", "UBER_EATS_HEADLESS", env_default=True),
        max_order_price=_resolve_number(
            "max_order_price", "UBER_EATS_MAX_ORDER_PRICE", float, 0.0
        ),
        max_daily_spend=_resolve_number(
            "max_daily_spend", "UBER_EATS_MAX_DAILY_SPEND", float, 0.0
        ),
        max_daily_orders=_resolve_number(
            "max_daily_orders", "UBER_EATS_MAX_DAILY_ORDERS", int, 0
        ),
        max_items=_resolve_number("max_items", "UBER_EATS_MAX_ITEMS", int, 1),
        max_item_quantity=_resolve_number(
            "max_item_quantity", "UBER_EATS_MAX_ITEM_QUANTITY", int, 1
        ),
    )


def get_ceilings() -> dict[str, Any]:
    """Bornes dures issues du `.env`, affichées pour expliquer les refus."""
    ceilings: dict[str, Any] = {
        "enabled": _env_bool("UBER_EATS_ENABLED", False),
        "dry_run_forced": _env_bool("UBER_EATS_DRY_RUN", True),
        "menu_scrape_enabled": _env_bool("FOOD_MENU_SCRAPE_ENABLED", False),
        "suggestions_enabled": _env_bool("FOOD_SUGGESTIONS_ENABLED", False),
        "headless": _env_bool("UBER_EATS_HEADLESS", True),
    }
    for key, attribute, caster, minimum in NUMERIC_LIMITS:
        ceilings[key] = caster(getattr(config, attribute, minimum))
    return ceilings


def _validate_bool(key: str, value: object) -> str:
    """Convertit une valeur booléenne, en refusant les entrées ambiguës."""
    if isinstance(value, bool):
        return "true" if value else "false"
    text = str(value).strip().lower()
    if text in ("true", "1", "yes", "on"):
        return "true"
    if text in ("false", "0", "no", "off"):
        return "false"
    raise FoodSettingsError(f"Réglage {key!r} : booléen attendu, reçu {value!r}")


def _validate_number(key: str, value: object) -> str:
    """Convertit et borne une valeur numérique.

    Raises:
        FoodSettingsError: valeur non numérique, négative, ou au-dessus de la
            borne dure correspondante.
    """
    spec = next((item for item in NUMERIC_LIMITS if item[0] == key), None)
    if spec is None:  # pragma: no cover - protégé par WRITABLE_KEYS
        raise FoodSettingsError(f"Réglage numérique inconnu : {key!r}")
    _, attribute, caster, minimum = spec
    if isinstance(value, bool):
        raise FoodSettingsError(f"Réglage {key!r} : nombre attendu, reçu un booléen")
    try:
        number = caster(value)
    except (TypeError, ValueError) as exc:
        raise FoodSettingsError(
            f"Réglage {key!r} : nombre attendu, reçu {value!r}"
        ) from exc
    if number < caster(minimum):
        raise FoodSettingsError(
            f"Réglage {key!r} : {number} en dessous du minimum {caster(minimum)}"
        )
    ceiling = caster(getattr(config, attribute, minimum))
    if number > ceiling:
        raise FoodSettingsError(
            f"Réglage {key!r} : {number} au-dessus de la borne {ceiling} fixée par "
            f"{attribute} dans .env. Cette borne ne se change pas depuis l'interface."
        )
    return str(number)


def update_settings(patch: Mapping[str, object]) -> FoodSettings:
    """Applique une modification partielle et retourne les réglages effectifs.

    Args:
        patch: Réglages à modifier, limités à ``WRITABLE_KEYS``.

    Raises:
        FoodSettingsError: clé inconnue, valeur mal typée ou hors bornes.
    """
    if not isinstance(patch, Mapping) or not patch:
        raise FoodSettingsError("Aucun réglage fourni.")
    unknown = sorted(set(map(str, patch)) - WRITABLE_KEYS)
    if unknown:
        raise FoodSettingsError(
            f"Réglages inconnus : {unknown} (autorisés : {sorted(WRITABLE_KEYS)})"
        )

    normalised: dict[str, str] = {}
    for key, value in patch.items():
        name = str(key)
        normalised[name] = (
            _validate_bool(name, value)
            if name in BOOLEAN_KEYS
            else _validate_number(name, value)
        )

    for name, value in normalised.items():
        set_setting(f"{SETTINGS_PREFIX}{name}", value)
    logger.info("[food] réglages mis à jour depuis l'interface : %s", sorted(normalised))
    return get_settings()


def reset_settings() -> FoodSettings:
    """Efface les réglages persistés et revient aux valeurs du `.env`."""
    for key in sorted(WRITABLE_KEYS):
        set_setting(f"{SETTINGS_PREFIX}{key}", "")
    logger.info("[food] réglages réinitialisés sur le .env")
    return get_settings()
