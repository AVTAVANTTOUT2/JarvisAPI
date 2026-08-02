"""Relevé en lecture seule des restaurants, des menus et du suivi de livraison.

Ce module est volontairement séparé de ``integrations.uber_eats`` : il n'y a
ici aucun clic sur un bouton d'ajout, de paiement ou de validation. Il ouvre
des pages, lit du texte, ferme le navigateur. Cette séparation est une
propriété de sécurité vérifiable — un test statique interdit à ce fichier de
contenir la moindre action d'achat — et non une simple préférence de rangement.

Le relevé alimente ``food_menu_cache`` pour que les suggestions ne portent que
sur des plats réellement proposés : sans menu connu, une recommandation
inventerait des noms et le panier échouerait au moment de l'ajout.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

import config
from integrations.playwright_runtime import playwright_errors
from integrations.uber_eats import (
    IMPLAUSIBLE_TOTAL_EUR,
    UberEatsAutomationError,
    UberEatsUnavailable,
    normalise_restaurant,
    parse_price,
    uber_eats,
)
from integrations.uber_eats_settings import get_settings
from integrations.uber_eats_selectors import (
    SelectorMap,
    SelectorResolutionError,
    read_text,
    resolve_all,
)

if TYPE_CHECKING:  # pragma: no cover - typage seulement
    from playwright.async_api import Locator, Page

logger = logging.getLogger("jarvis.uber_eats")

MAX_STORES_PER_FEED = 40
MAX_SECTIONS_PER_MENU = 40
MAX_ENTRIES_PER_SECTION = 60
MAX_ITEMS_PER_MENU = 300
FEED_PATH = "/feed"

#: Correspondances entre le texte affiché par Uber et le vocabulaire interne.
#: L'ordre compte : « en préparation » doit être testé avant « prépar… ».
_STATUS_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("delivered", ("livré", "delivered", "remis")),
    ("cancelled", ("annul", "cancel")),
    ("on_the_way", ("en route", "on the way", "arrive", "approach", "proche")),
    ("picked_up", ("récupér", "picked up", "en chemin", "a récupéré")),
    ("preparing", ("prépar", "preparing", "en cours de prépa", "cuisine")),
    ("placed", ("confirmé", "placed", "reçue", "received")),
)

# Les gardes `(?<!\d)` et `(?!\d)` sont indispensables : sans elles, « 9999 min »
# livrerait 999 et afficherait une estimation inventée.
_ETA_MINUTES_RE = re.compile(r"(?<!\d)(\d{1,3})(?!\d)\s*(?:min|minute)", re.IGNORECASE)
_ETA_RANGE_RE = re.compile(r"(?<!\d)(\d{1,3})(?!\d)\s*[–\-—]\s*(?<!\d)(\d{1,3})(?!\d)")
MAX_PLAUSIBLE_ETA_MINUTES = 24 * 60


@dataclass(frozen=True, slots=True)
class MenuEntry:
    """Un article relevé sur la page d'un restaurant."""

    restaurant: str
    item_name: str
    category: str
    price: float | None
    currency: str

    def as_dict(self) -> dict[str, Any]:
        """Représentation attendue par la couche de persistance."""
        return {
            "restaurant": self.restaurant,
            "item_name": self.item_name,
            "category": self.category,
            "price": self.price,
            "currency": self.currency,
        }


@dataclass(frozen=True, slots=True)
class DeliveryProgress:
    """Avancement lu sur la page de suivi d'une commande."""

    status: str | None
    eta_minutes: int | None
    raw_status: str
    raw_eta: str


def parse_delivery_status(raw: str) -> str | None:
    """Traduit le libellé affiché en statut interne, ou ``None`` si inconnu.

    Retourner ``None`` plutôt que de deviner est délibéré : un statut faux
    afficherait « livré » sur une commande en route.
    """
    text = " ".join(str(raw or "").split()).casefold()
    if not text:
        return None
    for status, markers in _STATUS_PATTERNS:
        if any(marker in text for marker in markers):
            return status
    return None


def parse_eta_minutes(raw: str) -> int | None:
    """Extrait une durée en minutes d'un texte d'estimation.

    Gère « 25 min », « 20–30 min » (borne haute retenue, plus honnête qu'une
    moyenne optimiste) et « 1 h 05 ». Retourne ``None`` si rien d'exploitable.
    """
    text = " ".join(str(raw or "").split())
    if not text:
        return None

    hours_match = re.search(
        r"(?<!\d)(\d{1,2})(?!\d)\s*h(?:\s*(\d{1,2})(?!\d))?", text, re.IGNORECASE
    )
    if hours_match:
        hours = int(hours_match.group(1))
        minutes = int(hours_match.group(2) or 0)
        total = hours * 60 + minutes
        return total if 0 < total <= MAX_PLAUSIBLE_ETA_MINUTES else None

    range_match = _ETA_RANGE_RE.search(text)
    if range_match:
        upper = int(range_match.group(2))
        return upper if 0 < upper <= MAX_PLAUSIBLE_ETA_MINUTES else None

    single = _ETA_MINUTES_RE.search(text)
    if single:
        value = int(single.group(1))
        return value if 0 < value <= MAX_PLAUSIBLE_ETA_MINUTES else None
    return None


def _safe_price(raw: str) -> tuple[float | None, str]:
    """Lit un prix d'article sans jamais interrompre le relevé.

    Un article dont le prix est illisible reste utile : on connaît son
    existence. Il sera simplement écarté des suggestions chiffrées.
    """
    try:
        value, currency = parse_price(raw)
    except UberEatsAutomationError:
        return None, "EUR"
    if value <= 0 or value > IMPLAUSIBLE_TOTAL_EUR:
        return None, currency
    return value, currency


class UberEatsDiscovery:
    """Relève les données publiques d'Uber Eats avec la session enregistrée."""

    def __init__(self) -> None:
        self._lock: asyncio.Lock | None = None
        self._lock_loop: asyncio.AbstractEventLoop | None = None

    @property
    def enabled(self) -> bool:
        """Relevé de menus autorisé par le `.env` et confirmé dans l'interface."""
        return get_settings().menu_scrape_enabled

    def availability(self) -> dict[str, Any]:
        """État du relevé, dérivé de celui du client de commande."""
        state = dict(uber_eats.availability())
        reasons = list(state.get("reasons", []))
        if not self.enabled:
            reasons.append(
                "relevé de menus désactivé — l'activer dans les réglages de la page "
                "Nourriture, ou dans .env si FOOD_MENU_SCRAPE_ENABLED vaut false"
            )
        state["scrape_enabled"] = self.enabled
        state["can_scrape"] = bool(state.get("can_browse")) and self.enabled
        state["reasons"] = reasons
        return state

    def _lock_for_loop(self) -> asyncio.Lock:
        """Verrou lié à la boucle courante : un seul relevé à la fois."""
        loop = asyncio.get_running_loop()
        if self._lock is None or self._lock_loop is not loop:
            self._lock = asyncio.Lock()
            self._lock_loop = loop
        return self._lock

    def _require_enabled(self) -> None:
        """Vérifie que le relevé est autorisé avant d'ouvrir un navigateur.

        Raises:
            UberEatsUnavailable: relevé désactivé ou intégration inutilisable.
        """
        state = self.availability()
        if not state["can_scrape"]:
            raise UberEatsUnavailable("; ".join(state["reasons"]) or "relevé indisponible")

    # ── Relevés ─────────────────────────────────────────────────────────────

    async def list_feed_restaurants(self, limit: int = MAX_STORES_PER_FEED) -> list[str]:
        """Retourne les restaurants visibles sur le fil d'accueil.

        Raises:
            UberEatsUnavailable: relevé désactivé ou dépendance absente.
            UberEatsAutomationError: navigation impossible.
        """
        self._require_enabled()
        bounded = max(1, min(int(limit), MAX_STORES_PER_FEED))
        base = str(getattr(config, "UBER_EATS_BASE_URL", "https://www.ubereats.com"))

        async with self._lock_for_loop():
            async with uber_eats.authenticated_page() as (page, selector_map):
                await uber_eats.goto(page, f"{base.rstrip('/')}{FEED_PATH}")
                cards = await resolve_all(
                    page, selector_map, "feed_store_card", limit=bounded
                )
                names = [
                    name
                    for card in cards
                    if (name := await self._card_title(card, selector_map))
                ]

        unique = list(dict.fromkeys(names))
        logger.info("[food] fil d'accueil : %d restaurant(s) relevé(s)", len(unique))
        return unique

    async def _card_title(self, card: "Locator", selector_map: SelectorMap) -> str:
        """Lit le nom porté par une carte du fil, ou une chaîne vide."""
        title = await read_text(
            card, selector_map, "feed_store_title", timeout_ms=self._short_timeout()
        )
        if title:
            return " ".join(title.splitlines())[:120].strip()
        try:
            fallback = await card.inner_text(timeout=self._short_timeout())
        except playwright_errors():
            return ""
        first_line = next((line.strip() for line in fallback.splitlines() if line.strip()), "")
        return first_line[:120]

    def _short_timeout(self) -> int:
        """Délai court : un relevé ne doit pas bloquer sur un élément absent."""
        return min(int(getattr(config, "UBER_EATS_ACTION_TIMEOUT_MS", 10_000)), 3_000)

    async def scrape_menu(self, restaurant: str) -> list[MenuEntry]:
        """Relève le menu d'un restaurant, article par article.

        Raises:
            UberEatsUnavailable: relevé désactivé ou dépendance absente.
            UberEatsAutomationError: restaurant introuvable ou page illisible.
        """
        clean_restaurant = normalise_restaurant(restaurant)
        self._require_enabled()

        async with self._lock_for_loop():
            async with uber_eats.authenticated_page() as (page, selector_map):
                try:
                    await uber_eats.open_restaurant_page(
                        page, selector_map, clean_restaurant
                    )
                except SelectorResolutionError as exc:
                    raise UberEatsAutomationError(
                        f"Restaurant '{clean_restaurant}' introuvable : {exc}"
                    ) from exc
                entries = await self._collect_menu(page, selector_map, clean_restaurant)

        logger.info(
            "[food] menu de %s relevé : %d article(s)", clean_restaurant, len(entries)
        )
        return entries

    async def _collect_menu(
        self, page: "Page", selector_map: SelectorMap, restaurant: str
    ) -> list[MenuEntry]:
        """Parcourt les sections de la page et en extrait les articles."""
        sections = await resolve_all(
            page, selector_map, "menu_section", limit=MAX_SECTIONS_PER_MENU
        )
        if not sections:
            # Certaines pages listent les articles sans conteneur de section.
            return await self._collect_entries(
                page, selector_map, restaurant, category="", limit=MAX_ITEMS_PER_MENU
            )

        entries: list[MenuEntry] = []
        for section in sections:
            if len(entries) >= MAX_ITEMS_PER_MENU:
                break
            category = await read_text(
                section, selector_map, "menu_section_title", timeout_ms=self._short_timeout()
            )
            entries.extend(
                await self._collect_entries(
                    section,
                    selector_map,
                    restaurant,
                    category=category,
                    limit=min(
                        MAX_ENTRIES_PER_SECTION, MAX_ITEMS_PER_MENU - len(entries)
                    ),
                )
            )
        return entries

    async def _collect_entries(
        self,
        scope: "Page | Locator",
        selector_map: SelectorMap,
        restaurant: str,
        *,
        category: str,
        limit: int,
    ) -> list[MenuEntry]:
        """Extrait les articles d'un conteneur donné."""
        rows = await resolve_all(scope, selector_map, "menu_entry", limit=limit)
        entries: list[MenuEntry] = []
        for row in rows:
            name = await read_text(
                row, selector_map, "menu_entry_title", timeout_ms=self._short_timeout()
            )
            if not name:
                continue
            price_text = await read_text(
                row, selector_map, "menu_entry_price", timeout_ms=self._short_timeout()
            )
            price, currency = _safe_price(price_text)
            entries.append(
                MenuEntry(
                    restaurant=restaurant,
                    item_name=" ".join(name.split())[:200],
                    category=" ".join(category.split())[:120],
                    price=price,
                    currency=currency,
                )
            )
        return entries

    async def read_delivery_progress(self, tracking_url: str) -> DeliveryProgress:
        """Lit le statut et l'estimation d'arrivée sur une page de suivi.

        Raises:
            UberEatsUnavailable: relevé désactivé ou dépendance absente.
            UberEatsAutomationError: URL hors du domaine attendu, ou page
                inaccessible.
        """
        url = self._validate_tracking_url(tracking_url)
        self._require_enabled()

        async with self._lock_for_loop():
            async with uber_eats.raw_page() as (page, selector_map):
                await uber_eats.goto(page, url)
                await uber_eats.dismiss_overlays(page, selector_map)
                raw_status = await read_text(
                    page, selector_map, "order_status_text", timeout_ms=self._short_timeout()
                )
                raw_eta = await read_text(
                    page, selector_map, "order_eta_text", timeout_ms=self._short_timeout()
                )

        return DeliveryProgress(
            status=parse_delivery_status(raw_status),
            eta_minutes=parse_eta_minutes(raw_eta or raw_status),
            raw_status=raw_status[:200],
            raw_eta=raw_eta[:200],
        )

    @staticmethod
    def _validate_tracking_url(raw: object) -> str:
        """Refuse toute URL hors du domaine configuré.

        Le lien de suivi provient d'une page web : sans ce contrôle, une valeur
        détournée ferait naviguer un navigateur porteur de la session vers un
        domaine arbitraire.

        Raises:
            UberEatsAutomationError: URL vide, malformée ou hors domaine.
        """
        candidate = str(raw or "").strip()
        if not candidate:
            raise UberEatsAutomationError("Lien de suivi vide : suivi impossible.")
        base = str(getattr(config, "UBER_EATS_BASE_URL", "https://www.ubereats.com"))
        expected_host = (urlparse(base).hostname or "").casefold()
        parsed = urlparse(candidate)
        host = (parsed.hostname or "").casefold()
        if parsed.scheme != "https" or not host:
            raise UberEatsAutomationError(
                f"Lien de suivi refusé (schéma {parsed.scheme!r}) : HTTPS attendu."
            )
        if host != expected_host and not host.endswith(f".{expected_host}"):
            raise UberEatsAutomationError(
                f"Lien de suivi refusé : hôte {host!r} hors du domaine {expected_host!r}."
            )
        return candidate

    async def refresh_menus(self, restaurants: Sequence[str]) -> dict[str, Any]:
        """Relève puis persiste les menus d'une liste de restaurants.

        Un échec sur un restaurant n'interrompt pas les suivants : le relevé
        est un travail de fond dont le résultat partiel reste utile.

        Returns:
            Le décompte des relevés réussis et la liste détaillée des échecs.
        """
        from database import replace_menu_items

        refreshed: dict[str, int] = {}
        failures: list[dict[str, str]] = []
        limit = max(1, int(getattr(config, "FOOD_MENU_SCRAPE_MAX_RESTAURANTS", 8)))

        for restaurant in list(dict.fromkeys(restaurants))[:limit]:
            try:
                entries = await self.scrape_menu(restaurant)
            except (UberEatsUnavailable, UberEatsAutomationError, *playwright_errors()) as exc:
                logger.warning("[food] menu de %s non relevé : %s", restaurant, exc)
                failures.append({"restaurant": restaurant, "error": str(exc)[:300]})
                continue
            if not entries:
                failures.append(
                    {"restaurant": restaurant, "error": "aucun article lisible sur la page"}
                )
                continue
            currency = entries[0].currency
            refreshed[restaurant] = replace_menu_items(
                restaurant, [entry.as_dict() for entry in entries], currency=currency
            )

        return {
            "refreshed": refreshed,
            "failures": failures,
            "restaurants_seen": len(refreshed) + len(failures),
        }


uber_eats_discovery = UberEatsDiscovery()
