"""Commande Uber Eats pilotée au navigateur, avec confirmation obligatoire.

Uber ne publie pas d'API consommateur : l'API officielle est réservée aux
restaurateurs. La seule voie restante est de piloter un navigateur avec la
session déjà authentifiée de l'utilisateur, capturée à la main une fois par
``scripts/uber_eats_capture_session.py``. C'est une automatisation de
navigateur, donc contraire aux conditions d'utilisation d'Uber, et sujette à
la détection de robots — c'est un risque assumé, pas un détail d'implémentation.

Le module est construit autour d'un principe : **rien ne dépense d'argent sans
un plan figé côté serveur et une confirmation humaine explicite**. La première
passe remplit le panier et lit le total, puis enregistre un plan opaque à
durée de vie courte. La seconde passe consomme ce plan une seule fois, vérifie
que le total réel n'a pas bougé, revérifie les plafonds, et seulement alors
clique sur le bouton de paiement.

Trois interrupteurs indépendants doivent être ouverts pour une commande
réelle : ``UBER_EATS_ENABLED``, ``UBER_EATS_DRY_RUN=false``, et un fichier de
sélecteurs marqué ``verified``.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import secrets
from database import dbapi as sqlite3
import threading
import time
from collections.abc import AsyncIterator, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

import config
from core.file_security import ensure_private_directory, ensure_private_file, write_private_bytes
from database import get_daily_food_order_stats, record_food_order
from integrations.playwright_runtime import (
    PlaywrightUnavailable,
    import_playwright,
    is_playwright_installed,
    playwright_errors,
)
from integrations.uber_eats_settings import get_settings
from integrations.uber_eats_selectors import (
    SelectorConfigError,
    SelectorMap,
    SelectorResolutionError,
    load_selector_map,
    resolve_locator,
    role_is_visible,
)

if TYPE_CHECKING:  # pragma: no cover - typage seulement
    from playwright.async_api import BrowserContext, Page

logger = logging.getLogger("jarvis.uber_eats")

MAX_PENDING_PLANS = 20
MAX_ITEM_NAME_CHARS = 120
MAX_ITEM_NOTES_CHARS = 200
MAX_RESTAURANT_CHARS = 120
#: Au-delà, un total lu à l'écran est presque sûrement une erreur d'analyse.
IMPLAUSIBLE_TOTAL_EUR = 1_000.0
#: Écart toléré entre le total figé dans le plan et le total relu au paiement.
TOTAL_DRIFT_TOLERANCE_EUR = 0.01
STATUS_PLANNED = "planned"
STATUS_SIMULATED = "simulated"
STATUS_PLACED = "placed"
STATUS_BLOCKED = "blocked"
STATUS_FAILED = "failed"

_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]")
_DECIMAL_PRICE_RE = re.compile(r"\d{1,3}(?:[ .,]\d{3})*[.,]\d{1,2}(?!\d)")
_INTEGER_PRICE_RE = re.compile(r"\d{1,3}(?:[ .,]\d{3})*(?!\d)")
_CURRENCY_SYMBOLS: tuple[tuple[str, str], ...] = (("€", "EUR"), ("$", "USD"), ("£", "GBP"))

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


class UberEatsError(RuntimeError):
    """Erreur de base de l'intégration Uber Eats."""


class UberEatsUnavailable(UberEatsError):
    """Intégration désactivée, dépendance absente ou session manquante."""


class UberEatsSessionExpired(UberEatsError):
    """La session enregistrée n'est plus authentifiée."""


class UberEatsInvalidRequest(UberEatsError):
    """La demande de panier est malformée (articles, restaurant, quantités)."""


class UberEatsLimitExceeded(UberEatsError):
    """Un plafond de sécurité financière refuse la commande."""


class UberEatsPlanError(UberEatsError):
    """Plan de commande inconnu, expiré ou déjà consommé."""


class UberEatsAutomationError(UberEatsError):
    """Le parcours navigateur a échoué (page, sélecteur, lecture de total)."""


@dataclass(frozen=True, slots=True)
class CartItem:
    """Un article du panier, tel que validé avant tout accès réseau."""

    name: str
    quantity: int = 1
    notes: str = ""

    def as_dict(self) -> dict[str, Any]:
        """Représentation sérialisable, utilisée pour la persistance et l'UI."""
        return {"name": self.name, "quantity": self.quantity, "notes": self.notes}

    def label(self) -> str:
        """Libellé lisible du type ``2x Burger végétarien``."""
        return f"{self.quantity}x {self.name}"


@dataclass(frozen=True, slots=True)
class OrderPlan:
    """Panier figé, en attente de confirmation humaine."""

    plan_id: str
    restaurant: str
    items: tuple[CartItem, ...]
    total_price: float
    currency: str
    dry_run: bool
    created_at: float
    expires_at: float

    def public_view(self) -> dict[str, Any]:
        """Vue transmissible au client : jamais de cookie ni de chemin local."""
        return {
            "plan_id": self.plan_id,
            "restaurant": self.restaurant,
            "items": [item.as_dict() for item in self.items],
            "items_label": ", ".join(item.label() for item in self.items),
            "total_price": self.total_price,
            "currency": self.currency,
            "dry_run": self.dry_run,
            "expires_in_seconds": max(0, int(self.expires_at - time.monotonic())),
        }


@dataclass(frozen=True, slots=True)
class OrderOutcome:
    """Résultat d'une étape de commande, prêt à être journalisé."""

    ok: bool
    status: str
    restaurant: str
    items: tuple[CartItem, ...]
    total_price: float | None = None
    currency: str = "EUR"
    dry_run: bool = True
    plan_id: str | None = None
    error: str | None = None
    screenshot_path: str | None = None
    timestamp: str = ""

    def as_dict(self) -> dict[str, Any]:
        """Dictionnaire destiné à la couche action et à l'interface."""
        return {
            "ok": self.ok,
            "status": self.status,
            "restaurant": self.restaurant,
            "items": [item.as_dict() for item in self.items],
            "items_label": ", ".join(item.label() for item in self.items),
            "total_price": self.total_price,
            "currency": self.currency,
            "dry_run": self.dry_run,
            "plan_id": self.plan_id,
            "error": self.error,
            "timestamp": self.timestamp or datetime.now().isoformat(timespec="seconds"),
        }


# ── Validation des entrées produites par un LLM ─────────────────────────────


def normalise_restaurant(raw: object) -> str:
    """Valide le nom de restaurant issu du modèle.

    Raises:
        UberEatsInvalidRequest: nom vide ou trop long après nettoyage.
    """
    text = _CONTROL_CHARS_RE.sub(" ", str(raw or "")).strip()
    text = re.sub(r"\s+", " ", text)
    if not text:
        raise UberEatsInvalidRequest("Nom de restaurant manquant.")
    if len(text) > MAX_RESTAURANT_CHARS:
        raise UberEatsInvalidRequest(
            f"Nom de restaurant trop long ({len(text)} caractères, "
            f"maximum {MAX_RESTAURANT_CHARS})."
        )
    return text


def parse_cart_items(raw_items: object) -> tuple[CartItem, ...]:
    """Convertit une liste d'articles non fiable en panier validé.

    Raises:
        UberEatsInvalidRequest: structure invalide, panier vide, quantité hors
            bornes ou nombre d'articles distincts au-dessus du plafond.
    """
    if not isinstance(raw_items, Iterable) or isinstance(raw_items, (str, bytes, Mapping)):
        raise UberEatsInvalidRequest(
            "Liste d'articles attendue, par exemple [{'name': 'Tacos', 'quantity': 1}]."
        )

    settings = get_settings()
    max_items = max(1, settings.max_items)
    max_quantity = max(1, settings.max_item_quantity)

    items: list[CartItem] = []
    for position, raw in enumerate(raw_items, start=1):
        if not isinstance(raw, Mapping):
            raise UberEatsInvalidRequest(
                f"Article {position} : objet attendu avec au moins une clé 'name'."
            )
        name = _CONTROL_CHARS_RE.sub(" ", str(raw.get("name", ""))).strip()
        name = re.sub(r"\s+", " ", name)
        if not name:
            raise UberEatsInvalidRequest(f"Article {position} : nom manquant.")
        if len(name) > MAX_ITEM_NAME_CHARS:
            raise UberEatsInvalidRequest(
                f"Article {position} ({name[:30]}…) : nom trop long "
                f"({len(name)} caractères, maximum {MAX_ITEM_NAME_CHARS})."
            )

        raw_quantity = raw.get("quantity", 1)
        if isinstance(raw_quantity, bool) or not isinstance(raw_quantity, (int, float, str)):
            raise UberEatsInvalidRequest(f"Article '{name}' : quantité invalide.")
        try:
            quantity = int(str(raw_quantity).strip() or "1")
        except ValueError as exc:
            raise UberEatsInvalidRequest(
                f"Article '{name}' : quantité non entière ({raw_quantity!r})."
            ) from exc
        if quantity < 1 or quantity > max_quantity:
            raise UberEatsInvalidRequest(
                f"Article '{name}' : quantité {quantity} hors bornes (1 à {max_quantity})."
            )

        notes = _CONTROL_CHARS_RE.sub(" ", str(raw.get("notes", "") or "")).strip()
        items.append(CartItem(name=name, quantity=quantity, notes=notes[:MAX_ITEM_NOTES_CHARS]))

    if not items:
        raise UberEatsInvalidRequest("Panier vide : aucun article à commander.")
    if len(items) > max_items:
        raise UberEatsInvalidRequest(
            f"Panier trop grand : {len(items)} articles distincts, maximum {max_items}."
        )
    return tuple(items)


def parse_price(raw: str) -> tuple[float, str]:
    """Extrait un montant et sa devise d'un texte de page.

    Gère les formats européens (``24,90 €``) et anglo-saxons (``$1,234.56``),
    espaces insécables inclus. Lorsque plusieurs montants apparaissent dans le
    même élément (sous-total puis total), le dernier est retenu.

    Raises:
        UberEatsAutomationError: aucun montant exploitable dans le texte.
    """
    text = str(raw or "").replace("\u00a0", " ").replace("\u202f", " ").strip()
    currency = "EUR"
    for symbol, code in _CURRENCY_SYMBOLS:
        if symbol in text:
            currency = code
            break
    else:
        upper = text.upper()
        for code in ("EUR", "USD", "GBP"):
            if code in upper:
                currency = code
                break

    matches = _DECIMAL_PRICE_RE.findall(text) or _INTEGER_PRICE_RE.findall(text)
    if not matches:
        raise UberEatsAutomationError(
            f"Total illisible : aucun montant trouvé dans {text[:120]!r}."
        )

    number = matches[-1].replace(" ", "")
    has_comma, has_dot = "," in number, "." in number
    if has_comma and has_dot:
        decimal_sep = "," if number.rfind(",") > number.rfind(".") else "."
    elif has_comma:
        decimal_sep = "," if len(number.rsplit(",", 1)[1]) <= 2 else ""
    elif has_dot:
        decimal_sep = "." if len(number.rsplit(".", 1)[1]) <= 2 else ""
    else:
        decimal_sep = ""

    if decimal_sep:
        thousands_sep = "." if decimal_sep == "," else ","
        number = number.replace(thousands_sep, "").replace(decimal_sep, ".")
    else:
        number = number.replace(",", "").replace(".", "")

    try:
        value = float(number)
    except ValueError as exc:
        raise UberEatsAutomationError(
            f"Total illisible : {matches[-1]!r} non convertible depuis {text[:120]!r}."
        ) from exc
    if value < 0 or value > IMPLAUSIBLE_TOTAL_EUR:
        raise UberEatsAutomationError(
            f"Total invraisemblable ({value}) lu depuis {text[:120]!r} — "
            "sélecteur 'cart_total' probablement obsolète."
        )
    return round(value, 2), currency


# ── Registre des plans en attente ───────────────────────────────────────────

_pending_plans: dict[str, OrderPlan] = {}
_plans_lock = threading.Lock()


def _purge_expired_plans_locked(now: float) -> None:
    """Retire les plans expirés. À appeler avec ``_plans_lock`` détenu."""
    for plan_id in [pid for pid, plan in _pending_plans.items() if plan.expires_at <= now]:
        _pending_plans.pop(plan_id, None)


def _register_plan(plan: OrderPlan) -> None:
    """Enregistre un plan en bornant la taille du registre."""
    with _plans_lock:
        _purge_expired_plans_locked(time.monotonic())
        while len(_pending_plans) >= MAX_PENDING_PLANS:
            oldest = min(_pending_plans.values(), key=lambda item: item.created_at)
            _pending_plans.pop(oldest.plan_id, None)
            logger.warning(
                "[uber_eats] registre saturé — plan %s évincé sans confirmation",
                oldest.plan_id,
            )
        _pending_plans[plan.plan_id] = plan


def get_order_plan(plan_id: str) -> dict[str, Any]:
    """Retourne la vue publique d'un plan sans le consommer.

    Raises:
        UberEatsPlanError: plan inconnu, expiré ou déjà utilisé.
    """
    now = time.monotonic()
    with _plans_lock:
        plan = _pending_plans.get(str(plan_id or ""))
        if plan and plan.expires_at <= now:
            _pending_plans.pop(plan.plan_id, None)
            plan = None
    if not plan:
        raise UberEatsPlanError("Panier inconnu, expiré ou déjà confirmé.")
    return plan.public_view()


def consume_order_plan(plan_id: str) -> OrderPlan:
    """Retire et retourne un plan de façon atomique — un seul appel réussit.

    Raises:
        UberEatsPlanError: plan inconnu, expiré ou déjà consommé.
    """
    now = time.monotonic()
    with _plans_lock:
        plan = _pending_plans.pop(str(plan_id or ""), None)
    if not plan or plan.expires_at <= now:
        raise UberEatsPlanError("Panier inconnu, expiré ou déjà confirmé.")
    return plan


def revoke_order_plan(plan_id: str) -> bool:
    """Annule un plan en attente. Retourne ``True`` s'il existait encore."""
    with _plans_lock:
        return _pending_plans.pop(str(plan_id or ""), None) is not None


def reset_order_plans_for_tests() -> None:
    """Vide le registre des plans. Réservé aux tests."""
    with _plans_lock:
        _pending_plans.clear()


# ── Client ──────────────────────────────────────────────────────────────────


class UberEatsClient:
    """Pilote le parcours de commande dans un navigateur headless.

    Le constructeur ne fait aucune entrée-sortie et ne lève jamais : l'objet
    doit pouvoir exister sur une machine sans Playwright, sans session et sans
    sélecteurs, afin que le démarrage de JARVIS ne dépende pas de cette
    intégration facultative.
    """

    def __init__(self) -> None:
        self._order_lock: asyncio.Lock | None = None
        self._lock_loop: asyncio.AbstractEventLoop | None = None

    # ── Configuration lue à chaud (permet le pilotage par .env et par test) ──

    @property
    def enabled(self) -> bool:
        """Intégration activée : `.env` autorisant, interface confirmant."""
        return get_settings().enabled

    @property
    def dry_run(self) -> bool:
        """Mode simulation : le bouton de paiement n'est jamais cliqué.

        Reste vrai tant que le `.env` impose la simulation, quel que soit le
        réglage choisi dans l'interface.
        """
        return get_settings().dry_run

    @property
    def storage_state_path(self) -> Path:
        """Chemin du fichier de session Playwright."""
        return Path(getattr(config, "UBER_EATS_STORAGE_STATE", "data/uber_eats_storage_state.json"))

    @property
    def selectors_path(self) -> Path:
        """Chemin du fichier de sélecteurs."""
        return Path(
            getattr(config, "UBER_EATS_SELECTORS_FILE", "integrations/uber_eats_selectors.json")
        )

    @property
    def screenshot_dir(self) -> Path:
        """Dossier privé des captures d'échec."""
        return Path(getattr(config, "UBER_EATS_SCREENSHOT_DIR", "data/uber_eats_screenshots"))

    def _action_timeout(self) -> int:
        return max(1_000, int(getattr(config, "UBER_EATS_ACTION_TIMEOUT_MS", 10_000)))

    def _nav_timeout(self) -> int:
        return max(1_000, int(getattr(config, "UBER_EATS_NAV_TIMEOUT_MS", 30_000)))

    def _plan_ttl(self) -> int:
        return max(30, int(getattr(config, "UBER_EATS_PLAN_TTL_SECONDS", 600)))

    # ── Disponibilité ───────────────────────────────────────────────────────

    def _load_selectors(self) -> SelectorMap | None:
        """Charge les sélecteurs, en journalisant l'erreur plutôt qu'en la levant."""
        try:
            return load_selector_map(self.selectors_path)
        except SelectorConfigError as exc:
            logger.error("[uber_eats] sélecteurs inutilisables : %s", exc)
            return None

    def _session_state_readable(self) -> bool:
        """Vérifie que la session existe, est privée et contient du JSON valide."""
        path = self.storage_state_path
        if path.is_symlink() or not path.is_file():
            return False
        try:
            ensure_private_file(path)
            json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, RuntimeError) as exc:
            logger.error("[uber_eats] session illisible (%s) : %s", path, exc)
            return False
        return True

    def availability(self) -> dict[str, Any]:
        """État détaillé de l'intégration et raisons d'indisponibilité."""
        selector_map = self._load_selectors()
        playwright_ready = is_playwright_installed()
        session_ready = self._session_state_readable()
        selectors_verified = bool(selector_map and selector_map.verified)

        reasons: list[str] = []
        if not self.enabled:
            reasons.append(
                "intégration désactivée — l'activer dans les réglages de la page "
                "Nourriture, ou dans .env si UBER_EATS_ENABLED vaut false"
            )
        if not playwright_ready:
            reasons.append("Playwright non installé (pip install playwright)")
        if not session_ready:
            reasons.append(
                f"session absente ou illisible ({self.storage_state_path}) — "
                "lancer scripts/uber_eats_capture_session.py"
            )
        if selector_map is None:
            reasons.append(f"fichier de sélecteurs invalide ({self.selectors_path})")
        elif not selector_map.verified:
            reasons.append(
                "sélecteurs non vérifiés : capturer le DOM réel puis passer "
                f"\"verified\": true dans {self.selectors_path}"
            )

        can_browse = self.enabled and playwright_ready and session_ready and selector_map is not None
        return {
            "enabled": self.enabled,
            "playwright": playwright_ready,
            "session": session_ready,
            "selectors_loaded": selector_map is not None,
            "selectors_verified": selectors_verified,
            "dry_run": self.dry_run,
            "can_browse": can_browse,
            "can_place_real_order": can_browse and selectors_verified and not self.dry_run,
            "reasons": reasons,
        }

    def is_available(self) -> bool:
        """Vrai si un panier peut être construit (même en simulation)."""
        return bool(self.availability()["can_browse"])

    def _require_selectors(self, *, for_payment: bool) -> SelectorMap:
        """Retourne les sélecteurs utilisables ou explique précisément le blocage.

        Raises:
            UberEatsUnavailable: intégration inutilisable en l'état.
        """
        state = self.availability()
        if not state["can_browse"]:
            raise UberEatsUnavailable("; ".join(state["reasons"]))
        if for_payment and not state["selectors_verified"]:
            raise UberEatsUnavailable(
                "Paiement refusé : les sélecteurs ne sont pas marqués vérifiés dans "
                f"{self.selectors_path}. Tant que \"verified\" vaut false, JARVIS "
                "ne clique sur aucun bouton de paiement."
            )
        selector_map = self._load_selectors()
        if selector_map is None:  # pragma: no cover - déjà couvert par can_browse
            raise UberEatsUnavailable(f"Sélecteurs illisibles : {self.selectors_path}")
        return selector_map

    # ── Plafonds financiers ─────────────────────────────────────────────────

    def _check_spending_limits(self, total: float | None) -> None:
        """Vérifie les plafonds par commande, par jour et en nombre.

        Les plafonds sont relus à chaque appel : un réglage abaissé depuis
        l'interface s'applique immédiatement, y compris à un panier déjà
        construit qui attend sa confirmation.

        Raises:
            UberEatsLimitExceeded: un plafond serait dépassé.
        """
        settings = get_settings()
        max_order = settings.max_order_price
        max_daily_spend = settings.max_daily_spend
        max_daily_orders = settings.max_daily_orders

        stats = get_daily_food_order_stats()
        if stats["orders"] >= max_daily_orders:
            raise UberEatsLimitExceeded(
                f"Plafond atteint : {stats['orders']} commande(s) déjà passée(s) "
                f"aujourd'hui, maximum {max_daily_orders}."
            )
        if total is None:
            return
        if total > max_order:
            raise UberEatsLimitExceeded(
                f"Total {total:.2f} € au-dessus du plafond par commande "
                f"({max_order:.2f} €)."
            )
        projected = round(stats["spend"] + total, 2)
        if projected > max_daily_spend:
            raise UberEatsLimitExceeded(
                f"Total {total:.2f} € porterait la dépense du jour à {projected:.2f} €, "
                f"au-dessus du plafond journalier ({max_daily_spend:.2f} €)."
            )

    # ── Navigateur ──────────────────────────────────────────────────────────

    def _lock(self) -> asyncio.Lock:
        """Verrou lié à la boucle courante : un seul panier manipulé à la fois."""
        loop = asyncio.get_running_loop()
        if self._order_lock is None or self._lock_loop is not loop:
            self._order_lock = asyncio.Lock()
            self._lock_loop = loop
        return self._order_lock

    @contextlib.asynccontextmanager
    async def _browser_page(self) -> AsyncIterator["Page"]:
        """Ouvre un navigateur avec la session persistée et garantit sa fermeture."""
        api = import_playwright()
        errors = playwright_errors()
        async with api.async_playwright() as playwright:
            browser = await playwright.chromium.launch(
                headless=get_settings().headless,
                args=["--disable-blink-features=AutomationControlled"],
            )
            try:
                context = await browser.new_context(
                    storage_state=str(self.storage_state_path),
                    locale=str(getattr(config, "UBER_EATS_LOCALE", "fr-FR")),
                    timezone_id=str(getattr(config, "TIMEZONE", "Europe/Paris")),
                    user_agent=_USER_AGENT,
                    viewport={"width": 1440, "height": 900},
                )
                context.set_default_timeout(self._action_timeout())
                context.set_default_navigation_timeout(self._nav_timeout())
                try:
                    page = await context.new_page()
                    yield page
                finally:
                    with contextlib.suppress(*errors):
                        await context.close()
            finally:
                with contextlib.suppress(*errors):
                    await browser.close()

    @contextlib.asynccontextmanager
    async def authenticated_page(self) -> AsyncIterator[tuple["Page", SelectorMap]]:
        """Ouvre une page prête à lire : session vérifiée, bannières fermées.

        Primitive publique partagée avec le relevé en lecture seule
        (``integrations.uber_eats_discovery``), pour qu'aucun autre module
        n'ait à réimplémenter le préambule de navigation ni à toucher aux
        détails internes de ce client. Elle ne construit aucun panier et ne
        clique sur aucun bouton d'achat.

        Raises:
            UberEatsUnavailable: intégration inutilisable ou Playwright absent.
            UberEatsSessionExpired: session à recapturer.
            UberEatsAutomationError: page d'accueil inaccessible.
        """
        selector_map = self._require_selectors(for_payment=False)
        try:
            async with self._browser_page() as page:
                await self._goto(page, str(config.UBER_EATS_BASE_URL))
                await self._dismiss_overlays(page, selector_map)
                await self._assert_session_alive(page, selector_map)
                yield page, selector_map
        except PlaywrightUnavailable as exc:
            raise UberEatsUnavailable(str(exc)) from exc

    @contextlib.asynccontextmanager
    async def raw_page(self) -> AsyncIterator[tuple["Page", SelectorMap]]:
        """Ouvre une page sans naviguer, pour visiter une URL précise.

        Utilisée par le suivi de livraison, qui ouvre directement un lien de
        commande déjà validé plutôt que de repasser par l'accueil.

        Raises:
            UberEatsUnavailable: intégration inutilisable ou Playwright absent.
        """
        selector_map = self._require_selectors(for_payment=False)
        try:
            async with self._browser_page() as page:
                yield page, selector_map
        except PlaywrightUnavailable as exc:
            raise UberEatsUnavailable(str(exc)) from exc

    async def open_restaurant_page(
        self, page: "Page", selector_map: SelectorMap, restaurant: str
    ) -> None:
        """Recherche un restaurant et ouvre sa fiche. Aucun ajout au panier.

        Raises:
            SelectorResolutionError: restaurant introuvable à l'écran.
        """
        await self._open_restaurant(page, selector_map, restaurant)

    async def goto(self, page: "Page", url: str) -> None:
        """Navigation partagée, tolérante aux connexions longues d'Uber.

        Raises:
            UberEatsAutomationError: navigation impossible.
        """
        await self._goto(page, url)

    async def dismiss_overlays(self, page: "Page", selector_map: SelectorMap) -> None:
        """Ferme la bannière de cookies si elle est présente."""
        await self._dismiss_overlays(page, selector_map)

    async def _persist_session(self, context: "BrowserContext") -> None:
        """Réécrit la session rafraîchie en 0600, sans passer par un fichier lisible."""
        try:
            state = await context.storage_state()
            payload = json.dumps(state, ensure_ascii=False).encode("utf-8")
            write_private_bytes(self.storage_state_path, payload)
        except (OSError, RuntimeError, ValueError, *playwright_errors()) as exc:
            logger.warning("[uber_eats] session non rafraîchie : %s", exc)

    async def _goto(self, page: "Page", url: str) -> None:
        """Navigation tolérante : on attend le DOM, jamais l'inactivité réseau.

        Uber Eats maintient des connexions longues ; ``networkidle`` expire
        presque systématiquement et transformerait chaque commande en échec.
        """
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=self._nav_timeout())
        except playwright_errors() as exc:
            raise UberEatsAutomationError(f"Navigation impossible vers {url} : {exc}") from exc

    async def _assert_session_alive(self, page: "Page", selector_map: SelectorMap) -> None:
        """Détecte une session expirée avant toute manipulation de panier.

        Raises:
            UberEatsSessionExpired: écran de connexion présent ou marqueur de
                session authentifiée absent.
        """
        short_timeout = min(self._action_timeout(), 4_000)
        if await role_is_visible(page, selector_map, "login_marker", timeout_ms=short_timeout):
            raise UberEatsSessionExpired(
                "Session Uber Eats expirée : écran de connexion détecté. "
                "Relancer scripts/uber_eats_capture_session.py."
            )
        if not await role_is_visible(page, selector_map, "session_marker", timeout_ms=short_timeout):
            raise UberEatsSessionExpired(
                "Session Uber Eats non authentifiée : aucun marqueur de compte "
                f"trouvé sur {page.url}. Relancer scripts/uber_eats_capture_session.py."
            )

    async def _dismiss_overlays(self, page: "Page", selector_map: SelectorMap) -> None:
        """Ferme la bannière de cookies si elle est déclarée et présente."""
        if not selector_map.has("cookie_accept_button"):
            return
        try:
            button = await resolve_locator(
                page, selector_map, "cookie_accept_button", timeout_ms=2_000
            )
            await button.click()
        except (SelectorResolutionError, *playwright_errors()) as exc:
            logger.debug("[uber_eats] pas de bannière cookies à fermer : %s", exc)

    async def _open_restaurant(
        self, page: "Page", selector_map: SelectorMap, restaurant: str
    ) -> None:
        """Recherche un restaurant et ouvre sa page."""
        search = await resolve_locator(
            page, selector_map, "search_input", timeout_ms=self._action_timeout()
        )
        await search.fill(restaurant)
        await search.press("Enter")
        card = await resolve_locator(
            page,
            selector_map,
            "store_card",
            timeout_ms=self._nav_timeout(),
            substitutions={"restaurant": restaurant},
        )
        await card.click()

    async def _add_item(
        self, page: "Page", selector_map: SelectorMap, item: CartItem
    ) -> None:
        """Ouvre un article du menu, ajuste la quantité et l'ajoute au panier."""
        entry = await resolve_locator(
            page,
            selector_map,
            "menu_item",
            timeout_ms=self._action_timeout(),
            substitutions={"item": item.name},
        )
        await entry.click()

        if item.quantity > 1:
            if not selector_map.has("quantity_increase_button"):
                raise UberEatsAutomationError(
                    f"Quantité {item.quantity} demandée pour '{item.name}' mais aucun "
                    "sélecteur 'quantity_increase_button' n'est déclaré."
                )
            increase = await resolve_locator(
                page, selector_map, "quantity_increase_button", timeout_ms=self._action_timeout()
            )
            for _ in range(item.quantity - 1):
                await increase.click()

        add_button = await resolve_locator(
            page, selector_map, "add_to_cart_button", timeout_ms=self._action_timeout()
        )
        await add_button.click()

    async def _read_cart_total(
        self, page: "Page", selector_map: SelectorMap
    ) -> tuple[float, str]:
        """Ouvre le panier et retourne le total lu à l'écran."""
        try:
            cart = await resolve_locator(
                page, selector_map, "cart_button", timeout_ms=self._action_timeout()
            )
            await cart.click()
        except SelectorResolutionError as exc:
            logger.debug("[uber_eats] panier déjà ouvert ou bouton absent : %s", exc)

        total_locator = await resolve_locator(
            page, selector_map, "cart_total", timeout_ms=self._action_timeout()
        )
        try:
            text = await total_locator.inner_text()
        except playwright_errors() as exc:
            raise UberEatsAutomationError(f"Total du panier illisible : {exc}") from exc
        return parse_price(text)

    async def _place_order(self, page: "Page", selector_map: SelectorMap) -> None:
        """Franchit l'écran de paiement puis confirme la commande."""
        checkout = await resolve_locator(
            page, selector_map, "checkout_button", timeout_ms=self._action_timeout()
        )
        await checkout.click()
        place = await resolve_locator(
            page, selector_map, "place_order_button", timeout_ms=self._nav_timeout()
        )
        await place.click()
        confirmed = await role_is_visible(
            page, selector_map, "order_confirmation_marker", timeout_ms=self._nav_timeout()
        )
        if not confirmed:
            raise UberEatsAutomationError(
                "Bouton de commande cliqué mais aucune confirmation détectée. "
                "Vérifier manuellement l'état de la commande dans l'application Uber Eats."
            )

    # ── Captures d'échec ────────────────────────────────────────────────────

    async def _capture_failure(self, page: "Page", label: str) -> str | None:
        """Enregistre une capture privée du dernier écran, sans jamais lever."""
        try:
            directory = ensure_private_directory(self.screenshot_dir)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            path = directory / f"{label}_{stamp}.png"
            await page.screenshot(path=str(path), full_page=False)
            ensure_private_file(path)
            self._rotate_screenshots(directory)
            return str(path)
        except (OSError, RuntimeError, *playwright_errors()) as exc:
            logger.warning("[uber_eats] capture d'échec impossible : %s", exc)
            return None

    def _rotate_screenshots(self, directory: Path) -> None:
        """Conserve uniquement les N captures les plus récentes."""
        keep = max(0, int(getattr(config, "UBER_EATS_SCREENSHOT_KEEP", 20)))
        try:
            shots = sorted(
                directory.glob("*.png"), key=lambda item: item.stat().st_mtime, reverse=True
            )
            for stale in shots[keep:]:
                stale.unlink(missing_ok=True)
        except OSError as exc:
            logger.debug("[uber_eats] rotation des captures ignorée : %s", exc)

    # ── Journalisation ──────────────────────────────────────────────────────

    def _record(
        self,
        outcome: OrderOutcome,
        *,
        suggestion_id: int | None = None,
        tracking_url: str | None = None,
        delivery_status: str | None = None,
    ) -> OrderOutcome:
        """Persiste l'issue sans laisser une erreur de base casser la commande."""
        try:
            record_food_order(
                restaurant=outcome.restaurant,
                items=[item.as_dict() for item in outcome.items],
                status=outcome.status,
                dry_run=outcome.dry_run,
                total_price=outcome.total_price,
                currency=outcome.currency,
                plan_id=outcome.plan_id,
                error=outcome.error,
                screenshot_path=outcome.screenshot_path,
                suggestion_id=suggestion_id,
                tracking_url=tracking_url,
                delivery_status=delivery_status,
            )
        except (sqlite3.Error, ValueError, OSError) as exc:
            logger.error(
                "[uber_eats] journalisation impossible (%s chez %s) : %s",
                outcome.status,
                outcome.restaurant,
                exc,
                exc_info=True,
            )
        return outcome

    def _failure(
        self,
        *,
        restaurant: str,
        items: Sequence[CartItem],
        status: str,
        error: str,
        total_price: float | None = None,
        currency: str = "EUR",
        plan_id: str | None = None,
        screenshot_path: str | None = None,
    ) -> OrderOutcome:
        """Construit et journalise une issue négative."""
        return self._record(
            OrderOutcome(
                ok=False,
                status=status,
                restaurant=restaurant,
                items=tuple(items),
                total_price=total_price,
                currency=currency,
                dry_run=self.dry_run,
                plan_id=plan_id,
                error=error,
                screenshot_path=screenshot_path,
                timestamp=datetime.now().isoformat(timespec="seconds"),
            )
        )

    # ── API publique ────────────────────────────────────────────────────────

    async def prepare_order(
        self, restaurant: str, raw_items: object
    ) -> tuple[OrderPlan, OrderOutcome]:
        """Remplit le panier, lit le total et fige un plan à confirmer.

        Aucun paiement n'est déclenché ici. Le plan retourné est à usage unique
        et expire au bout de ``UBER_EATS_PLAN_TTL_SECONDS``.

        Raises:
            UberEatsInvalidRequest: demande malformée.
            UberEatsUnavailable: intégration inutilisable.
            UberEatsSessionExpired: session à recapturer.
            UberEatsLimitExceeded: plafond financier atteint.
            UberEatsAutomationError: parcours navigateur en échec.
        """
        clean_restaurant = normalise_restaurant(restaurant)
        items = parse_cart_items(raw_items)
        selector_map = self._require_selectors(for_payment=False)
        self._check_spending_limits(None)

        total: float = 0.0
        currency: str = "EUR"
        async with self._lock():
            screenshot: str | None = None
            try:
                async with self._browser_page() as page:
                    try:
                        await self._goto(page, str(config.UBER_EATS_BASE_URL))
                        await self._dismiss_overlays(page, selector_map)
                        await self._assert_session_alive(page, selector_map)
                        await self._open_restaurant(page, selector_map, clean_restaurant)
                        for item in items:
                            await self._add_item(page, selector_map, item)
                        total, currency = await self._read_cart_total(page, selector_map)
                        await self._persist_session(page.context)
                    except UberEatsError:
                        screenshot = await self._capture_failure(page, "prepare")
                        raise
                    except playwright_errors() as exc:
                        screenshot = await self._capture_failure(page, "prepare")
                        raise UberEatsAutomationError(
                            f"Échec du parcours panier chez {clean_restaurant} : {exc}"
                        ) from exc
                    except SelectorResolutionError as exc:
                        screenshot = await self._capture_failure(page, "prepare")
                        raise UberEatsAutomationError(str(exc)) from exc
            except PlaywrightUnavailable as exc:
                self._failure(
                    restaurant=clean_restaurant,
                    items=items,
                    status=STATUS_FAILED,
                    error=str(exc),
                )
                raise UberEatsUnavailable(str(exc)) from exc
            except UberEatsError as exc:
                status = (
                    STATUS_BLOCKED if isinstance(exc, UberEatsLimitExceeded) else STATUS_FAILED
                )
                self._failure(
                    restaurant=clean_restaurant,
                    items=items,
                    status=status,
                    error=str(exc),
                    screenshot_path=screenshot,
                )
                raise

            try:
                self._check_spending_limits(total)
            except UberEatsLimitExceeded as exc:
                self._failure(
                    restaurant=clean_restaurant,
                    items=items,
                    status=STATUS_BLOCKED,
                    error=str(exc),
                    total_price=total,
                    currency=currency,
                )
                raise

            now = time.monotonic()
            plan = OrderPlan(
                plan_id=secrets.token_urlsafe(24),
                restaurant=clean_restaurant,
                items=items,
                total_price=total,
                currency=currency,
                dry_run=self.dry_run,
                created_at=now,
                expires_at=now + self._plan_ttl(),
            )
            _register_plan(plan)
            outcome = self._record(
                OrderOutcome(
                    ok=True,
                    status=STATUS_PLANNED,
                    restaurant=clean_restaurant,
                    items=items,
                    total_price=total,
                    currency=currency,
                    dry_run=plan.dry_run,
                    plan_id=plan.plan_id,
                    timestamp=datetime.now().isoformat(timespec="seconds"),
                )
            )
            logger.info(
                "[uber_eats] panier prêt chez %s — %.2f %s, plan %s",
                clean_restaurant,
                total,
                currency,
                plan.plan_id,
            )
            return plan, outcome

    async def confirm_order(
        self, plan_id: str, *, suggestion_id: int | None = None
    ) -> OrderOutcome:
        """Consomme un plan confirmé et passe la commande pour de bon.

        Le plan est retiré du registre **avant** toute action navigateur : un
        échec de paiement ne peut donc pas être rejoué automatiquement, et une
        confirmation dupliquée ne peut pas commander deux fois.

        Args:
            plan_id: Identifiant opaque du plan à consommer.
            suggestion_id: Suggestion à l'origine du clic, journalisée pour
                mesurer plus tard quelles recommandations ont été suivies.

        Raises:
            UberEatsPlanError: plan inconnu, expiré ou déjà consommé.
        """
        plan = consume_order_plan(plan_id)

        if plan.dry_run or self.dry_run:
            logger.info(
                "[uber_eats] mode simulation — commande non envoyée chez %s (%.2f %s)",
                plan.restaurant,
                plan.total_price,
                plan.currency,
            )
            return self._record(
                OrderOutcome(
                    ok=True,
                    status=STATUS_SIMULATED,
                    restaurant=plan.restaurant,
                    items=plan.items,
                    total_price=plan.total_price,
                    currency=plan.currency,
                    dry_run=True,
                    plan_id=plan.plan_id,
                    timestamp=datetime.now().isoformat(timespec="seconds"),
                ),
                suggestion_id=suggestion_id,
            )

        try:
            selector_map = self._require_selectors(for_payment=True)
            self._check_spending_limits(plan.total_price)
        except UberEatsLimitExceeded as exc:
            return self._failure(
                restaurant=plan.restaurant,
                items=plan.items,
                status=STATUS_BLOCKED,
                error=str(exc),
                total_price=plan.total_price,
                currency=plan.currency,
                plan_id=plan.plan_id,
            )
        except UberEatsUnavailable as exc:
            return self._failure(
                restaurant=plan.restaurant,
                items=plan.items,
                status=STATUS_BLOCKED,
                error=str(exc),
                total_price=plan.total_price,
                currency=plan.currency,
                plan_id=plan.plan_id,
            )

        live_total: float = plan.total_price
        live_currency: str = plan.currency
        tracking_url: str | None = None
        async with self._lock():
            screenshot: str | None = None
            try:
                async with self._browser_page() as page:
                    try:
                        await self._goto(page, str(config.UBER_EATS_BASE_URL))
                        await self._dismiss_overlays(page, selector_map)
                        await self._assert_session_alive(page, selector_map)
                        live_total, live_currency = await self._read_cart_total(page, selector_map)
                        self._assert_total_unchanged(plan, live_total, live_currency)
                        self._check_spending_limits(live_total)
                        await self._place_order(page, selector_map)
                        tracking_url = self._safe_tracking_url(page.url)
                        await self._persist_session(page.context)
                    except UberEatsError:
                        screenshot = await self._capture_failure(page, "confirm")
                        raise
                    except playwright_errors() as exc:
                        screenshot = await self._capture_failure(page, "confirm")
                        raise UberEatsAutomationError(
                            f"Échec du paiement chez {plan.restaurant} : {exc}"
                        ) from exc
                    except SelectorResolutionError as exc:
                        screenshot = await self._capture_failure(page, "confirm")
                        raise UberEatsAutomationError(str(exc)) from exc
            except PlaywrightUnavailable as exc:
                return self._failure(
                    restaurant=plan.restaurant,
                    items=plan.items,
                    status=STATUS_FAILED,
                    error=str(exc),
                    total_price=plan.total_price,
                    currency=plan.currency,
                    plan_id=plan.plan_id,
                )
            except UberEatsError as exc:
                status = (
                    STATUS_BLOCKED
                    if isinstance(exc, (UberEatsLimitExceeded, UberEatsUnavailable))
                    else STATUS_FAILED
                )
                return self._failure(
                    restaurant=plan.restaurant,
                    items=plan.items,
                    status=status,
                    error=str(exc),
                    total_price=plan.total_price,
                    currency=plan.currency,
                    plan_id=plan.plan_id,
                    screenshot_path=screenshot,
                )

        logger.info(
            "[uber_eats] commande passée chez %s — %.2f %s (plan %s)",
            plan.restaurant,
            live_total,
            live_currency,
            plan.plan_id,
        )
        return self._record(
            OrderOutcome(
                ok=True,
                status=STATUS_PLACED,
                restaurant=plan.restaurant,
                items=plan.items,
                total_price=live_total,
                currency=live_currency,
                dry_run=False,
                plan_id=plan.plan_id,
                timestamp=datetime.now().isoformat(timespec="seconds"),
            ),
            suggestion_id=suggestion_id,
            tracking_url=tracking_url,
            delivery_status=STATUS_PLACED,
        )

    @staticmethod
    def _safe_tracking_url(raw: object) -> str | None:
        """Retient l'URL d'arrivée si elle appartient bien au domaine attendu.

        Le lien sert plus tard à rouvrir une page avec la session : accepter
        une redirection sortante reviendrait à exposer ces cookies.
        """
        candidate = str(raw or "").strip()
        if not candidate:
            return None
        base = str(getattr(config, "UBER_EATS_BASE_URL", "https://www.ubereats.com"))
        expected = (urlparse(base).hostname or "").casefold()
        parsed = urlparse(candidate)
        host = (parsed.hostname or "").casefold()
        if parsed.scheme != "https" or not host or not expected:
            return None
        if host != expected and not host.endswith(f".{expected}"):
            return None
        return candidate[:500]

    @staticmethod
    def _assert_total_unchanged(plan: OrderPlan, live_total: float, live_currency: str) -> None:
        """Refuse de payer si le panier a changé entre l'annonce et l'accord.

        Raises:
            UberEatsLimitExceeded: total ou devise différents du plan confirmé.
        """
        if live_currency != plan.currency:
            raise UberEatsLimitExceeded(
                f"Devise du panier modifiée depuis la confirmation "
                f"({plan.currency} → {live_currency}) — commande annulée."
            )
        if abs(live_total - plan.total_price) > TOTAL_DRIFT_TOLERANCE_EUR:
            raise UberEatsLimitExceeded(
                f"Total du panier modifié depuis la confirmation "
                f"({plan.total_price:.2f} → {live_total:.2f} {live_currency}) — "
                "commande annulée, il faut reconstruire le panier."
            )


uber_eats = UberEatsClient()
