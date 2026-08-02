"""Tests : commande Uber Eats pilotée au navigateur.

Trois propriétés sont vérifiées avant tout le reste, parce qu'elles portent
l'argent de l'utilisateur : aucun clic de paiement sans plan serveur confirmé,
un plan ne sert qu'une fois, et les plafonds sont revérifiés au moment de payer
et pas seulement au moment d'annoncer le total.

Playwright n'est pas installé dans l'environnement de test. Un faux module
``playwright.async_api`` est injecté dans ``sys.modules`` pour que le parcours
navigateur soit exercé de bout en bout, avec ses locators, ses erreurs et ses
captures d'échec, sans jamais ouvrir de navigateur ni joindre Uber.
"""

from __future__ import annotations

import json
import sys
import types
from collections.abc import Callable, Iterator
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# ── Faux runtime Playwright ─────────────────────────────────────────────────


class FakePlaywrightError(Exception):
    """Équivalent de ``playwright.async_api.Error``."""


class FakePlaywrightTimeout(FakePlaywrightError):
    """Équivalent de ``playwright.async_api.TimeoutError``."""


class FakeLocator:
    """Locator minimal : visible ou non selon la clé demandée à la page."""

    def __init__(self, page: "FakePage", key: str) -> None:
        self._page = page
        self._key = key
        self._index = 0

    def nth(self, index: int) -> "FakeLocator":
        self._index = index
        return self

    async def wait_for(self, *, state: str = "visible", timeout: int = 0) -> None:
        self._page.waits.append(self._key)
        if self._key not in self._page.visible:
            raise FakePlaywrightTimeout(f"locator {self._key!r} absent")

    async def click(self) -> None:
        if self._key not in self._page.visible:
            raise FakePlaywrightTimeout(f"clic impossible sur {self._key!r}")
        self._page.clicks.append(self._key)
        hook = self._page.on_click.get(self._key)
        if hook is not None:
            hook(self._page)

    async def fill(self, value: str) -> None:
        self._page.filled[self._key] = value

    async def press(self, key: str) -> None:
        self._page.pressed.append((self._key, key))

    async def inner_text(self) -> str:
        if self._key in self._page.raising_text:
            raise FakePlaywrightError(f"texte illisible pour {self._key!r}")
        return self._page.texts.get(self._key, "")


class FakeContext:
    """Contexte navigateur : session, timeouts et fermeture observables."""

    def __init__(self, page: "FakePage", storage_state: dict[str, Any]) -> None:
        self._page = page
        self._storage_state = storage_state
        self.closed = False
        self.default_timeout: int | None = None
        self.default_navigation_timeout: int | None = None

    def set_default_timeout(self, timeout: int) -> None:
        self.default_timeout = timeout

    def set_default_navigation_timeout(self, timeout: int) -> None:
        self.default_navigation_timeout = timeout

    async def new_page(self) -> "FakePage":
        return self._page

    async def storage_state(self) -> dict[str, Any]:
        return self._storage_state

    async def close(self) -> None:
        self.closed = True


class FakePage:
    """Page dont la visibilité des éléments est pilotée par le test."""

    def __init__(
        self,
        visible: set[str] | None = None,
        texts: dict[str, str] | None = None,
        on_click: dict[str, Callable[["FakePage"], None]] | None = None,
    ) -> None:
        self.visible = set(visible or set())
        self.texts = dict(texts or {})
        self.on_click = dict(on_click or {})
        self.raising_text: set[str] = set()
        self.clicks: list[str] = []
        self.waits: list[str] = []
        self.pressed: list[tuple[str, str]] = []
        self.filled: dict[str, str] = {}
        self.navigations: list[str] = []
        self.screenshots: list[str] = []
        self.goto_error: Exception | None = None
        self.url = "https://www.ubereats.com/"
        self.context = FakeContext(self, {"cookies": [{"name": "sid", "value": "fresh"}]})

    async def goto(self, url: str, *, wait_until: str = "load", timeout: int = 0) -> None:
        if self.goto_error is not None:
            raise self.goto_error
        self.navigations.append(url)
        self.url = url

    async def screenshot(self, *, path: str, full_page: bool = False) -> None:
        Path(path).write_bytes(b"\x89PNG\r\n\x1a\n")
        self.screenshots.append(path)

    def get_by_test_id(self, value: str) -> FakeLocator:
        return FakeLocator(self, value)

    def get_by_role(self, role: str, name: str = "", exact: bool = False) -> FakeLocator:
        return FakeLocator(self, f"role:{role}:{name}")

    def get_by_placeholder(self, value: str, exact: bool = False) -> FakeLocator:
        return FakeLocator(self, f"placeholder:{value}")

    def get_by_label(self, value: str, exact: bool = False) -> FakeLocator:
        return FakeLocator(self, f"label:{value}")

    def get_by_alt_text(self, value: str, exact: bool = False) -> FakeLocator:
        return FakeLocator(self, f"alt:{value}")

    def get_by_title(self, value: str, exact: bool = False) -> FakeLocator:
        return FakeLocator(self, f"title:{value}")

    def get_by_text(self, value: str, exact: bool = False) -> FakeLocator:
        return FakeLocator(self, f"text:{value}")

    def locator(self, value: str) -> FakeLocator:
        return FakeLocator(self, f"css:{value}")


class FakeBrowser:
    def __init__(self, page: FakePage) -> None:
        self._page = page
        self.closed = False
        self.context: FakeContext | None = None

    async def new_context(self, **kwargs: Any) -> FakeContext:
        self.context = self._page.context
        self.context.launch_kwargs = kwargs  # type: ignore[attr-defined]
        return self.context

    async def close(self) -> None:
        self.closed = True


class FakeBrowserType:
    def __init__(self, page: FakePage) -> None:
        self._page = page
        self.browser: FakeBrowser | None = None

    async def launch(self, **kwargs: Any) -> FakeBrowser:
        self.browser = FakeBrowser(self._page)
        self.browser.launch_kwargs = kwargs  # type: ignore[attr-defined]
        return self.browser


class FakePlaywrightRuntime:
    def __init__(self, page: FakePage) -> None:
        self.chromium = FakeBrowserType(page)


class FakePlaywrightContextManager:
    def __init__(self, page: FakePage) -> None:
        self._page = page

    async def __aenter__(self) -> FakePlaywrightRuntime:
        return FakePlaywrightRuntime(self._page)

    async def __aexit__(self, *_exc: object) -> bool:
        return False


@pytest.fixture()
def fake_playwright(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, FakePage | None]]:
    """Installe un faux ``playwright.async_api`` importable par le code réel."""
    holder: dict[str, FakePage | None] = {"page": None}

    module = types.ModuleType("playwright.async_api")
    module.Error = FakePlaywrightError  # type: ignore[attr-defined]
    module.TimeoutError = FakePlaywrightTimeout  # type: ignore[attr-defined]

    def _async_playwright() -> FakePlaywrightContextManager:
        page = holder["page"]
        assert page is not None, "aucune FakePage installée pour ce test"
        return FakePlaywrightContextManager(page)

    module.async_playwright = _async_playwright  # type: ignore[attr-defined]

    package = types.ModuleType("playwright")
    package.async_api = module  # type: ignore[attr-defined]
    package.__path__ = []  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "playwright", package)
    monkeypatch.setitem(sys.modules, "playwright.async_api", module)
    yield holder


# ── Fichier de sélecteurs de test ───────────────────────────────────────────


TEST_LOCATORS: dict[str, list[dict[str, Any]]] = {
    "login_marker": [{"strategy": "test_id", "value": "login-form"}],
    "session_marker": [{"strategy": "test_id", "value": "account-menu"}],
    "search_input": [{"strategy": "test_id", "value": "search-input"}],
    "store_card": [{"strategy": "test_id", "value": "store-card"}],
    "menu_item": [{"strategy": "test_id", "value": "menu-item"}],
    "add_to_cart_button": [{"strategy": "test_id", "value": "add-to-cart"}],
    "quantity_increase_button": [{"strategy": "test_id", "value": "quantity-plus"}],
    "cart_button": [{"strategy": "test_id", "value": "cart-button"}],
    "cart_total": [{"strategy": "test_id", "value": "cart-total"}],
    "checkout_button": [{"strategy": "test_id", "value": "checkout"}],
    "place_order_button": [{"strategy": "test_id", "value": "place-order"}],
    "order_confirmation_marker": [{"strategy": "test_id", "value": "order-confirmed"}],
}

HAPPY_PATH_VISIBLE = {
    "account-menu",
    "search-input",
    "store-card",
    "menu-item",
    "quantity-plus",
    "add-to-cart",
    "cart-button",
    "cart-total",
    "checkout",
}


def write_selector_file(path: Path, *, verified: bool = True, **overrides: Any) -> Path:
    """Écrit un fichier de sélecteurs valide, éventuellement modifié."""
    document: dict[str, Any] = {
        "version": 1,
        "verified": verified,
        "captured_at": "2026-07-31",
        "locators": {role: list(v) for role, v in TEST_LOCATORS.items()},
    }
    document.update(overrides)
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    from integrations.uber_eats_selectors import clear_selector_cache

    clear_selector_cache()
    return path


def make_checkout_page(total_text: str = "Total 24,90 €") -> FakePage:
    """Page nominale : le panier existe et le paiement aboutit après clics."""

    def _open_checkout(page: FakePage) -> None:
        page.visible.add("place-order")

    def _confirm(page: FakePage) -> None:
        page.visible.add("order-confirmed")

    return FakePage(
        visible=set(HAPPY_PATH_VISIBLE),
        texts={"cart-total": total_text},
        on_click={"checkout": _open_checkout, "place-order": _confirm},
    )


@pytest.fixture()
def without_playwright(monkeypatch: pytest.MonkeyPatch) -> None:
    """Simule explicitement l'absence de Playwright.

    Se fier au fait que la bibliothèque n'est pas installée rendrait ces tests
    dépendants de la machine : ils passeraient en CI et échoueraient sur le Mac
    de déploiement, où Playwright est justement présent.
    """
    from integrations import playwright_runtime

    def _absent() -> None:
        raise playwright_runtime.PlaywrightUnavailable(
            "Playwright absent : installer 'pip install playwright' (simulé par le test)"
        )

    monkeypatch.setattr(playwright_runtime, "import_playwright", _absent)
    monkeypatch.setattr(playwright_runtime, "is_playwright_installed", lambda: False)
    # `integrations.uber_eats` a importé ces fonctions par valeur au chargement.
    import integrations.uber_eats as uber_eats_module

    monkeypatch.setattr(uber_eats_module, "import_playwright", _absent)
    monkeypatch.setattr(uber_eats_module, "is_playwright_installed", lambda: False)


def test_playwright_error_types_are_safe_when_the_optional_runtime_is_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from integrations import playwright_runtime

    def _absent() -> None:
        raise playwright_runtime.PlaywrightUnavailable("absent")

    monkeypatch.setattr(playwright_runtime, "import_playwright", _absent)

    assert playwright_runtime.playwright_errors() == (
        playwright_runtime.PlaywrightUnavailable,
    )


@pytest.fixture()
def uber_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Base isolée + configuration Uber Eats pointant sur des fichiers temporaires."""
    db_path = tmp_path / "jarvis.db"
    monkeypatch.setattr("config.DB_PATH", str(db_path))
    monkeypatch.setattr("database.DB_PATH", db_path)
    from database import init_db

    init_db()

    storage = tmp_path / "storage_state.json"
    storage.write_text(json.dumps({"cookies": [], "origins": []}), encoding="utf-8")
    selectors = write_selector_file(tmp_path / "selectors.json")
    screenshots = tmp_path / "shots"

    import config

    monkeypatch.setattr(config, "UBER_EATS_ENABLED", True)
    monkeypatch.setattr(config, "UBER_EATS_DRY_RUN", True)
    monkeypatch.setattr(config, "UBER_EATS_STORAGE_STATE", str(storage))
    monkeypatch.setattr(config, "UBER_EATS_SELECTORS_FILE", str(selectors))
    monkeypatch.setattr(config, "UBER_EATS_SCREENSHOT_DIR", str(screenshots))
    monkeypatch.setattr(config, "UBER_EATS_MAX_ORDER_PRICE", 40.0)
    monkeypatch.setattr(config, "UBER_EATS_MAX_DAILY_SPEND", 80.0)
    monkeypatch.setattr(config, "UBER_EATS_MAX_DAILY_ORDERS", 2)
    monkeypatch.setattr(config, "UBER_EATS_ACTION_TIMEOUT_MS", 1_000)
    monkeypatch.setattr(config, "UBER_EATS_NAV_TIMEOUT_MS", 1_000)
    monkeypatch.setattr(config, "UBER_EATS_PLAN_TTL_SECONDS", 600)

    from integrations.uber_eats import reset_order_plans_for_tests

    reset_order_plans_for_tests()
    yield types.SimpleNamespace(
        db_path=db_path,
        storage=storage,
        selectors=selectors,
        screenshots=screenshots,
        config=config,
    )
    reset_order_plans_for_tests()


# ── Sélecteurs ──────────────────────────────────────────────────────────────


def test_parse_selector_document_accepts_complete_map(tmp_path: Path) -> None:
    from integrations.uber_eats_selectors import load_selector_map

    path = write_selector_file(tmp_path / "sel.json")

    selector_map = load_selector_map(path)

    assert selector_map.verified is True
    assert selector_map.has("cart_total")
    assert selector_map.candidates("cart_total")[0].value == "cart-total"


def test_load_selector_map_rejects_missing_required_role(tmp_path: Path) -> None:
    from integrations.uber_eats_selectors import SelectorConfigError, load_selector_map

    locators = {k: v for k, v in TEST_LOCATORS.items() if k != "cart_total"}
    path = write_selector_file(tmp_path / "sel.json", locators=locators)

    with pytest.raises(SelectorConfigError, match="cart_total"):
        load_selector_map(path)


def test_load_selector_map_rejects_unknown_role(tmp_path: Path) -> None:
    from integrations.uber_eats_selectors import SelectorConfigError, load_selector_map

    locators = dict(TEST_LOCATORS)
    locators["tip_slider"] = [{"strategy": "test_id", "value": "tip"}]
    path = write_selector_file(tmp_path / "sel.json", locators=locators)

    with pytest.raises(SelectorConfigError, match="tip_slider"):
        load_selector_map(path)


def test_load_selector_map_rejects_unknown_strategy(tmp_path: Path) -> None:
    from integrations.uber_eats_selectors import SelectorConfigError, load_selector_map

    locators = dict(TEST_LOCATORS)
    locators["cart_total"] = [{"strategy": "xpath", "value": "//div"}]
    path = write_selector_file(tmp_path / "sel.json", locators=locators)

    with pytest.raises(SelectorConfigError, match="xpath"):
        load_selector_map(path)


def test_load_selector_map_rejects_role_strategy_without_aria_role(tmp_path: Path) -> None:
    from integrations.uber_eats_selectors import SelectorConfigError, load_selector_map

    locators = dict(TEST_LOCATORS)
    locators["checkout_button"] = [{"strategy": "role", "value": "Commander"}]
    path = write_selector_file(tmp_path / "sel.json", locators=locators)

    with pytest.raises(SelectorConfigError, match="exige un champ 'role'"):
        load_selector_map(path)


def test_load_selector_map_rejects_placeholder_on_wrong_role(tmp_path: Path) -> None:
    from integrations.uber_eats_selectors import SelectorConfigError, load_selector_map

    locators = dict(TEST_LOCATORS)
    locators["cart_total"] = [{"strategy": "text", "value": "Total {item}"}]
    path = write_selector_file(tmp_path / "sel.json", locators=locators)

    with pytest.raises(SelectorConfigError, match="gabarit"):
        load_selector_map(path)


def test_load_selector_map_rejects_unclosed_placeholder(tmp_path: Path) -> None:
    from integrations.uber_eats_selectors import SelectorConfigError, load_selector_map

    locators = dict(TEST_LOCATORS)
    locators["menu_item"] = [{"strategy": "text", "value": "Menu {item"}]
    path = write_selector_file(tmp_path / "sel.json", locators=locators)

    with pytest.raises(SelectorConfigError, match="accolade"):
        load_selector_map(path)


def test_load_selector_map_rejects_empty_candidate_list(tmp_path: Path) -> None:
    from integrations.uber_eats_selectors import SelectorConfigError, load_selector_map

    locators = dict(TEST_LOCATORS)
    locators["cart_button"] = []
    path = write_selector_file(tmp_path / "sel.json", locators=locators)

    with pytest.raises(SelectorConfigError, match="au moins une stratégie"):
        load_selector_map(path)


def test_load_selector_map_rejects_unsupported_version(tmp_path: Path) -> None:
    from integrations.uber_eats_selectors import SelectorConfigError, load_selector_map

    path = write_selector_file(tmp_path / "sel.json", version=2)

    with pytest.raises(SelectorConfigError, match="version de schéma"):
        load_selector_map(path)


def test_load_selector_map_reports_invalid_json(tmp_path: Path) -> None:
    from integrations.uber_eats_selectors import SelectorConfigError, load_selector_map

    path = tmp_path / "sel.json"
    path.write_text("{not json", encoding="utf-8")

    with pytest.raises(SelectorConfigError, match="JSON invalide"):
        load_selector_map(path)


def test_load_selector_map_reports_missing_file(tmp_path: Path) -> None:
    from integrations.uber_eats_selectors import SelectorConfigError, load_selector_map

    with pytest.raises(SelectorConfigError, match="illisible|introuvable"):
        load_selector_map(tmp_path / "absent.json")


def test_load_selector_map_reloads_after_recapture(tmp_path: Path) -> None:
    """Le cache est invalidé par la signature du fichier, pas par un redémarrage."""
    from integrations.uber_eats_selectors import load_selector_map

    path = write_selector_file(tmp_path / "sel.json", verified=False)
    assert load_selector_map(path).verified is False

    document = json.loads(path.read_text(encoding="utf-8"))
    document["verified"] = True
    document["captured_at"] = "2026-08-01T10:00:00"
    path.write_text(json.dumps(document), encoding="utf-8")

    assert load_selector_map(path).verified is True


def test_shipped_selector_file_is_valid_but_unverified() -> None:
    """Les sélecteurs livrés sont des hypothèses : ils ne doivent pas payer."""
    from integrations.uber_eats_selectors import clear_selector_cache, load_selector_map

    clear_selector_cache()
    selector_map = load_selector_map(PROJECT_ROOT / "integrations" / "uber_eats_selectors.json")

    assert selector_map.verified is False


def test_locator_strategy_render_substitutes_placeholder() -> None:
    from integrations.uber_eats_selectors import LocatorStrategy

    strategy = LocatorStrategy(kind="text", value="Menu {item}")

    assert strategy.render({"item": "Tacos"}).value == "Menu Tacos"
    assert strategy.render(None).value == "Menu {item}"


@pytest.mark.asyncio
async def test_resolve_locator_falls_back_to_second_strategy(
    tmp_path: Path, fake_playwright: dict[str, FakePage | None]
) -> None:
    from integrations.uber_eats_selectors import load_selector_map, resolve_locator

    locators = dict(TEST_LOCATORS)
    locators["cart_total"] = [
        {"strategy": "test_id", "value": "old-total"},
        {"strategy": "test_id", "value": "cart-total"},
    ]
    path = write_selector_file(tmp_path / "sel.json", locators=locators)
    page = FakePage(visible={"cart-total"}, texts={"cart-total": "31,00 €"})

    locator = await resolve_locator(page, load_selector_map(path), "cart_total", timeout_ms=10)

    assert await locator.inner_text() == "31,00 €"
    assert page.waits == ["old-total", "cart-total"]


@pytest.mark.asyncio
async def test_resolve_locator_lists_every_attempt_when_all_fail(
    tmp_path: Path, fake_playwright: dict[str, FakePage | None]
) -> None:
    from integrations.uber_eats_selectors import (
        SelectorResolutionError,
        load_selector_map,
        resolve_locator,
    )

    path = write_selector_file(tmp_path / "sel.json")
    page = FakePage(visible=set())

    with pytest.raises(SelectorResolutionError, match="cart_total"):
        await resolve_locator(page, load_selector_map(path), "cart_total", timeout_ms=10)


@pytest.mark.asyncio
async def test_role_is_visible_returns_false_for_undeclared_role(
    tmp_path: Path, fake_playwright: dict[str, FakePage | None]
) -> None:
    from integrations.uber_eats_selectors import load_selector_map, role_is_visible

    locators = {k: v for k, v in TEST_LOCATORS.items() if k != "cookie_accept_button"}
    path = write_selector_file(tmp_path / "sel.json", locators=locators)
    page = FakePage(visible=set())

    visible = await role_is_visible(
        page, load_selector_map(path), "cookie_accept_button", timeout_ms=10
    )

    assert visible is False


# ── Analyse des prix ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("text", "expected_value", "expected_currency"),
    [
        ("24,90 €", 24.90, "EUR"),
        ("Total : 8 €", 8.0, "EUR"),
        ("$34.56", 34.56, "USD"),
        ("£12.05", 12.05, "GBP"),
        ("Total\u00a0:\u00a0234,50 €", 234.50, "EUR"),
        ("Sous-total 12,00 € Total 15,50 €", 15.50, "EUR"),
        ("18.00 EUR", 18.0, "EUR"),
    ],
)
def test_parse_price_reads_common_formats(
    text: str, expected_value: float, expected_currency: str
) -> None:
    from integrations.uber_eats import parse_price

    value, currency = parse_price(text)

    assert (value, currency) == (expected_value, expected_currency)


@pytest.mark.parametrize("text", ["", "Total indisponible", None])
def test_parse_price_rejects_text_without_amount(text: str | None) -> None:
    from integrations.uber_eats import UberEatsAutomationError, parse_price

    with pytest.raises(UberEatsAutomationError, match="illisible"):
        parse_price(text)  # type: ignore[arg-type]


@pytest.mark.parametrize("text", ["9 999 999 €", "1 234,50 €"])
def test_parse_price_rejects_implausible_total(text: str) -> None:
    """Un total à quatre chiffres trahit un sélecteur obsolète, pas un festin."""
    from integrations.uber_eats import UberEatsAutomationError, parse_price

    with pytest.raises(UberEatsAutomationError, match="invraisemblable"):
        parse_price(text)


# ── Validation des entrées du modèle ────────────────────────────────────────


def test_normalise_restaurant_collapses_whitespace() -> None:
    from integrations.uber_eats import normalise_restaurant

    assert normalise_restaurant("  Chez\tPierre \n") == "Chez Pierre"


def test_normalise_restaurant_rejects_empty_name() -> None:
    from integrations.uber_eats import UberEatsInvalidRequest, normalise_restaurant

    with pytest.raises(UberEatsInvalidRequest, match="manquant"):
        normalise_restaurant("   ")


def test_normalise_restaurant_rejects_overlong_name() -> None:
    from integrations.uber_eats import UberEatsInvalidRequest, normalise_restaurant

    with pytest.raises(UberEatsInvalidRequest, match="trop long"):
        normalise_restaurant("a" * 500)


def test_parse_cart_items_normalises_quantities_and_notes() -> None:
    from integrations.uber_eats import parse_cart_items

    items = parse_cart_items(
        [{"name": " Tacos\x00 ", "quantity": "2", "notes": "sans oignon"}]
    )

    assert items[0].name == "Tacos"
    assert items[0].quantity == 2
    assert items[0].notes == "sans oignon"
    assert items[0].label() == "2x Tacos"


def test_parse_cart_items_rejects_string_payload() -> None:
    from integrations.uber_eats import UberEatsInvalidRequest, parse_cart_items

    with pytest.raises(UberEatsInvalidRequest, match="Liste d'articles"):
        parse_cart_items("un tacos")


def test_parse_cart_items_rejects_empty_list() -> None:
    from integrations.uber_eats import UberEatsInvalidRequest, parse_cart_items

    with pytest.raises(UberEatsInvalidRequest, match="Panier vide"):
        parse_cart_items([])


def test_parse_cart_items_rejects_missing_name() -> None:
    from integrations.uber_eats import UberEatsInvalidRequest, parse_cart_items

    with pytest.raises(UberEatsInvalidRequest, match="nom manquant"):
        parse_cart_items([{"quantity": 1}])


def test_parse_cart_items_rejects_quantity_above_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    import config
    from integrations.uber_eats import UberEatsInvalidRequest, parse_cart_items

    monkeypatch.setattr(config, "UBER_EATS_MAX_ITEM_QUANTITY", 3)

    with pytest.raises(UberEatsInvalidRequest, match="hors bornes"):
        parse_cart_items([{"name": "Tacos", "quantity": 4}])


def test_parse_cart_items_rejects_non_numeric_quantity() -> None:
    from integrations.uber_eats import UberEatsInvalidRequest, parse_cart_items

    with pytest.raises(UberEatsInvalidRequest, match="non entière"):
        parse_cart_items([{"name": "Tacos", "quantity": "beaucoup"}])


def test_parse_cart_items_rejects_too_many_distinct_items(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import config
    from integrations.uber_eats import UberEatsInvalidRequest, parse_cart_items

    monkeypatch.setattr(config, "UBER_EATS_MAX_ITEMS", 2)

    with pytest.raises(UberEatsInvalidRequest, match="Panier trop grand"):
        parse_cart_items([{"name": f"Plat {i}"} for i in range(3)])


# ── Registre des plans ──────────────────────────────────────────────────────


def _make_plan(plan_id: str = "plan-1", *, ttl: float = 600.0):
    import time

    from integrations.uber_eats import CartItem, OrderPlan

    now = time.monotonic()
    return OrderPlan(
        plan_id=plan_id,
        restaurant="Chez Pierre",
        items=(CartItem(name="Tacos", quantity=1),),
        total_price=12.5,
        currency="EUR",
        dry_run=False,
        created_at=now,
        expires_at=now + ttl,
    )


def test_consume_order_plan_succeeds_once() -> None:
    from integrations.uber_eats import (
        UberEatsPlanError,
        _register_plan,
        consume_order_plan,
        reset_order_plans_for_tests,
    )

    reset_order_plans_for_tests()
    _register_plan(_make_plan())

    assert consume_order_plan("plan-1").total_price == 12.5
    with pytest.raises(UberEatsPlanError):
        consume_order_plan("plan-1")


def test_consume_order_plan_rejects_expired_plan() -> None:
    from integrations.uber_eats import (
        UberEatsPlanError,
        _register_plan,
        consume_order_plan,
        reset_order_plans_for_tests,
    )

    reset_order_plans_for_tests()
    _register_plan(_make_plan("expired", ttl=-1.0))

    with pytest.raises(UberEatsPlanError):
        consume_order_plan("expired")


def test_get_order_plan_does_not_consume() -> None:
    from integrations.uber_eats import (
        _register_plan,
        consume_order_plan,
        get_order_plan,
        reset_order_plans_for_tests,
    )

    reset_order_plans_for_tests()
    _register_plan(_make_plan())

    view = get_order_plan("plan-1")

    assert view["items_label"] == "1x Tacos"
    assert consume_order_plan("plan-1").plan_id == "plan-1"


def test_get_order_plan_view_hides_internal_timestamps() -> None:
    from integrations.uber_eats import _register_plan, get_order_plan, reset_order_plans_for_tests

    reset_order_plans_for_tests()
    _register_plan(_make_plan())

    view = get_order_plan("plan-1")

    assert "created_at" not in view
    assert "expires_at" not in view
    assert view["expires_in_seconds"] > 0


def test_revoke_order_plan_reports_whether_plan_existed() -> None:
    from integrations.uber_eats import (
        _register_plan,
        reset_order_plans_for_tests,
        revoke_order_plan,
    )

    reset_order_plans_for_tests()
    _register_plan(_make_plan())

    assert revoke_order_plan("plan-1") is True
    assert revoke_order_plan("plan-1") is False


def test_plan_registry_evicts_oldest_when_saturated() -> None:
    from integrations.uber_eats import (
        MAX_PENDING_PLANS,
        UberEatsPlanError,
        _register_plan,
        get_order_plan,
        reset_order_plans_for_tests,
    )

    reset_order_plans_for_tests()
    for index in range(MAX_PENDING_PLANS + 1):
        _register_plan(_make_plan(f"plan-{index}"))

    with pytest.raises(UberEatsPlanError):
        get_order_plan("plan-0")
    assert get_order_plan(f"plan-{MAX_PENDING_PLANS}")["plan_id"]


# ── Persistance ─────────────────────────────────────────────────────────────


def test_record_food_order_persists_row(uber_env) -> None:
    from database import get_food_order, record_food_order

    order_id = record_food_order(
        restaurant="Chez Pierre",
        items=[{"name": "Tacos", "quantity": 2, "notes": ""}],
        status="planned",
        dry_run=True,
        total_price=24.9,
    )

    row = get_food_order(order_id)
    assert row is not None
    assert row["restaurant"] == "Chez Pierre"
    assert row["status"] == "planned"
    assert row["dry_run"] == 1
    assert json.loads(row["items_json"])[0]["quantity"] == 2


def test_record_food_order_rejects_unknown_status(uber_env) -> None:
    from database.food_orders import FoodOrderError, record_food_order

    with pytest.raises(FoodOrderError, match="Statut"):
        record_food_order(
            restaurant="Chez Pierre", items=[], status="shipped", dry_run=True
        )


def test_record_food_order_rejects_negative_total(uber_env) -> None:
    from database.food_orders import FoodOrderError, record_food_order

    with pytest.raises(FoodOrderError, match="négatif"):
        record_food_order(
            restaurant="Chez Pierre",
            items=[],
            status="planned",
            dry_run=True,
            total_price=-1.0,
        )


def test_record_food_order_rejects_blank_restaurant(uber_env) -> None:
    from database.food_orders import FoodOrderError, record_food_order

    with pytest.raises(FoodOrderError, match="restaurant"):
        record_food_order(restaurant="   ", items=[], status="planned", dry_run=True)


def test_record_food_order_ignores_duplicate_placed_plan(uber_env) -> None:
    """Rejouer une confirmation ne peut pas produire deux commandes payées."""
    from database import record_food_order

    first = record_food_order(
        restaurant="Chez Pierre",
        items=[{"name": "Tacos"}],
        status="placed",
        dry_run=False,
        total_price=20.0,
        plan_id="plan-x",
    )
    second = record_food_order(
        restaurant="Chez Pierre",
        items=[{"name": "Tacos"}],
        status="placed",
        dry_run=False,
        total_price=20.0,
        plan_id="plan-x",
    )

    assert first > 0
    assert second == 0


def test_daily_stats_count_only_real_placed_orders(uber_env) -> None:
    from database import get_daily_food_order_stats, record_food_order

    record_food_order(
        restaurant="A", items=[], status="placed", dry_run=False, total_price=20.0
    )
    record_food_order(
        restaurant="B", items=[], status="simulated", dry_run=True, total_price=99.0
    )
    record_food_order(
        restaurant="C", items=[], status="planned", dry_run=False, total_price=99.0
    )
    record_food_order(
        restaurant="D", items=[], status="blocked", dry_run=False, total_price=99.0
    )

    stats = get_daily_food_order_stats()

    assert stats["orders"] == 1
    assert stats["spend"] == 20.0


def test_daily_stats_ignore_other_days(uber_env) -> None:
    from database import get_daily_food_order_stats, record_food_order

    record_food_order(
        restaurant="A", items=[], status="placed", dry_run=False, total_price=20.0
    )

    yesterday = get_daily_food_order_stats(date.today() - timedelta(days=1))

    assert yesterday["orders"] == 0
    assert yesterday["spend"] == 0.0


def test_get_food_orders_returns_most_recent_first(uber_env) -> None:
    from database import get_food_orders, record_food_order

    for name in ("A", "B", "C"):
        record_food_order(restaurant=name, items=[], status="planned", dry_run=True)

    history = get_food_orders(limit=2)

    assert [row["restaurant"] for row in history] == ["C", "B"]


# ── Disponibilité et plafonds ───────────────────────────────────────────────


def test_availability_lists_every_blocking_reason(uber_env, monkeypatch) -> None:
    from integrations.uber_eats import uber_eats

    monkeypatch.setattr(uber_env.config, "UBER_EATS_ENABLED", False)
    monkeypatch.setattr(uber_env.config, "UBER_EATS_STORAGE_STATE", "/nonexistent/state.json")

    state = uber_eats.availability()

    assert state["can_browse"] is False
    assert state["can_place_real_order"] is False
    assert any("désactivée" in reason for reason in state["reasons"])
    assert any("session absente" in reason for reason in state["reasons"])


def test_availability_flags_unverified_selectors(
    uber_env, fake_playwright: dict[str, FakePage | None]
) -> None:
    from integrations.uber_eats import uber_eats

    write_selector_file(uber_env.selectors, verified=False)

    state = uber_eats.availability()

    assert state["can_browse"] is True
    assert state["selectors_verified"] is False
    assert state["can_place_real_order"] is False


def test_require_selectors_refuses_payment_when_unverified(
    uber_env, fake_playwright: dict[str, FakePage | None], monkeypatch
) -> None:
    from integrations.uber_eats import UberEatsUnavailable, uber_eats

    monkeypatch.setattr(uber_env.config, "UBER_EATS_DRY_RUN", False)
    write_selector_file(uber_env.selectors, verified=False)

    with pytest.raises(UberEatsUnavailable, match="vérifiés"):
        uber_eats._require_selectors(for_payment=True)


def test_spending_limits_reject_total_above_order_cap(uber_env) -> None:
    from integrations.uber_eats import UberEatsLimitExceeded, uber_eats

    with pytest.raises(UberEatsLimitExceeded, match="plafond par commande"):
        uber_eats._check_spending_limits(41.0)


def test_spending_limits_reject_daily_spend_overflow(uber_env) -> None:
    from database import record_food_order
    from integrations.uber_eats import UberEatsLimitExceeded, uber_eats

    record_food_order(
        restaurant="A", items=[], status="placed", dry_run=False, total_price=60.0
    )

    with pytest.raises(UberEatsLimitExceeded, match="plafond journalier"):
        uber_eats._check_spending_limits(25.0)


def test_spending_limits_reject_too_many_orders(uber_env) -> None:
    from database import record_food_order
    from integrations.uber_eats import UberEatsLimitExceeded, uber_eats

    for _ in range(2):
        record_food_order(
            restaurant="A", items=[], status="placed", dry_run=False, total_price=5.0
        )

    with pytest.raises(UberEatsLimitExceeded, match="Plafond atteint"):
        uber_eats._check_spending_limits(None)


def test_assert_total_unchanged_rejects_drift() -> None:
    from integrations.uber_eats import UberEatsClient, UberEatsLimitExceeded

    plan = _make_plan()

    UberEatsClient._assert_total_unchanged(plan, 12.5, "EUR")
    with pytest.raises(UberEatsLimitExceeded, match="Total du panier modifié"):
        UberEatsClient._assert_total_unchanged(plan, 19.9, "EUR")
    with pytest.raises(UberEatsLimitExceeded, match="Devise"):
        UberEatsClient._assert_total_unchanged(plan, 12.5, "USD")


# ── Parcours navigateur complet ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_prepare_order_builds_cart_without_paying(
    uber_env, fake_playwright: dict[str, FakePage | None]
) -> None:
    from database import get_food_orders
    from integrations.uber_eats import uber_eats

    page = make_checkout_page()
    fake_playwright["page"] = page

    plan, outcome = await uber_eats.prepare_order(
        "Chez Pierre", [{"name": "Tacos", "quantity": 2}]
    )

    assert plan.total_price == 24.90
    assert outcome.status == "planned"
    assert "place-order" not in page.clicks
    assert "checkout" not in page.clicks
    assert page.filled["search-input"] == "Chez Pierre"
    assert page.clicks.count("quantity-plus") == 1
    assert get_food_orders(limit=1)[0]["status"] == "planned"


@pytest.mark.asyncio
async def test_prepare_order_refreshes_persisted_session(
    uber_env, fake_playwright: dict[str, FakePage | None]
) -> None:
    from integrations.uber_eats import uber_eats

    fake_playwright["page"] = make_checkout_page()

    await uber_eats.prepare_order("Chez Pierre", [{"name": "Tacos"}])

    saved = json.loads(uber_env.storage.read_text(encoding="utf-8"))
    assert saved["cookies"][0]["value"] == "fresh"
    assert uber_env.storage.stat().st_mode & 0o077 == 0


@pytest.mark.asyncio
async def test_prepare_order_detects_expired_session(
    uber_env, fake_playwright: dict[str, FakePage | None]
) -> None:
    from database import get_food_orders
    from integrations.uber_eats import UberEatsSessionExpired, uber_eats

    page = FakePage(visible={"login-form"})
    fake_playwright["page"] = page

    with pytest.raises(UberEatsSessionExpired, match="expirée"):
        await uber_eats.prepare_order("Chez Pierre", [{"name": "Tacos"}])

    assert get_food_orders(limit=1)[0]["status"] == "failed"
    assert page.screenshots, "une capture d'échec doit être conservée"


@pytest.mark.asyncio
async def test_prepare_order_detects_missing_session_marker(
    uber_env, fake_playwright: dict[str, FakePage | None]
) -> None:
    from integrations.uber_eats import UberEatsSessionExpired, uber_eats

    fake_playwright["page"] = FakePage(visible={"search-input"})

    with pytest.raises(UberEatsSessionExpired, match="non authentifiée"):
        await uber_eats.prepare_order("Chez Pierre", [{"name": "Tacos"}])


@pytest.mark.asyncio
async def test_prepare_order_reports_stale_selector(
    uber_env, fake_playwright: dict[str, FakePage | None]
) -> None:
    from integrations.uber_eats import UberEatsAutomationError, uber_eats

    page = make_checkout_page()
    page.visible.discard("cart-total")
    fake_playwright["page"] = page

    with pytest.raises(UberEatsAutomationError, match="cart_total"):
        await uber_eats.prepare_order("Chez Pierre", [{"name": "Tacos"}])


@pytest.mark.asyncio
async def test_prepare_order_blocks_cart_above_order_cap(
    uber_env, fake_playwright: dict[str, FakePage | None]
) -> None:
    from database import get_food_orders
    from integrations.uber_eats import UberEatsLimitExceeded, uber_eats

    fake_playwright["page"] = make_checkout_page("Total 89,00 €")

    with pytest.raises(UberEatsLimitExceeded, match="plafond par commande"):
        await uber_eats.prepare_order("Chez Pierre", [{"name": "Menu géant"}])

    latest = get_food_orders(limit=1)[0]
    assert latest["status"] == "blocked"
    assert latest["total_price"] == 89.0


@pytest.mark.asyncio
async def test_prepare_order_requires_enabled_integration(
    uber_env, fake_playwright: dict[str, FakePage | None], monkeypatch
) -> None:
    from integrations.uber_eats import UberEatsUnavailable, uber_eats

    monkeypatch.setattr(uber_env.config, "UBER_EATS_ENABLED", False)
    fake_playwright["page"] = make_checkout_page()

    with pytest.raises(UberEatsUnavailable, match="désactivée"):
        await uber_eats.prepare_order("Chez Pierre", [{"name": "Tacos"}])


@pytest.mark.asyncio
async def test_confirm_order_in_dry_run_never_touches_the_browser(
    uber_env, fake_playwright: dict[str, FakePage | None]
) -> None:
    from integrations.uber_eats import uber_eats

    page = make_checkout_page()
    fake_playwright["page"] = page
    plan, _ = await uber_eats.prepare_order("Chez Pierre", [{"name": "Tacos"}])
    page.clicks.clear()

    outcome = await uber_eats.confirm_order(plan.plan_id)

    assert outcome.status == "simulated"
    assert outcome.dry_run is True
    assert page.clicks == []


@pytest.mark.asyncio
async def test_confirm_order_places_real_order_when_everything_is_green(
    uber_env, fake_playwright: dict[str, FakePage | None], monkeypatch
) -> None:
    from database import get_daily_food_order_stats
    from integrations.uber_eats import uber_eats

    monkeypatch.setattr(uber_env.config, "UBER_EATS_DRY_RUN", False)
    page = make_checkout_page()
    fake_playwright["page"] = page

    plan, _ = await uber_eats.prepare_order("Chez Pierre", [{"name": "Tacos"}])
    outcome = await uber_eats.confirm_order(plan.plan_id)

    assert outcome.status == "placed"
    assert outcome.dry_run is False
    assert page.clicks[-2:] == ["checkout", "place-order"]
    assert get_daily_food_order_stats()["spend"] == 24.90


@pytest.mark.asyncio
async def test_confirm_order_cannot_be_replayed(
    uber_env, fake_playwright: dict[str, FakePage | None], monkeypatch
) -> None:
    from integrations.uber_eats import UberEatsPlanError, uber_eats

    monkeypatch.setattr(uber_env.config, "UBER_EATS_DRY_RUN", False)
    fake_playwright["page"] = make_checkout_page()

    plan, _ = await uber_eats.prepare_order("Chez Pierre", [{"name": "Tacos"}])
    await uber_eats.confirm_order(plan.plan_id)

    with pytest.raises(UberEatsPlanError):
        await uber_eats.confirm_order(plan.plan_id)


@pytest.mark.asyncio
async def test_confirm_order_refuses_when_total_changed(
    uber_env, fake_playwright: dict[str, FakePage | None], monkeypatch
) -> None:
    from database import get_food_orders
    from integrations.uber_eats import uber_eats

    monkeypatch.setattr(uber_env.config, "UBER_EATS_DRY_RUN", False)
    page = make_checkout_page()
    fake_playwright["page"] = page

    plan, _ = await uber_eats.prepare_order("Chez Pierre", [{"name": "Tacos"}])
    page.texts["cart-total"] = "Total 31,40 €"

    outcome = await uber_eats.confirm_order(plan.plan_id)

    assert outcome.ok is False
    assert outcome.status == "blocked"
    assert "place-order" not in page.clicks
    assert get_food_orders(limit=1)[0]["status"] == "blocked"


@pytest.mark.asyncio
async def test_confirm_order_refuses_payment_with_unverified_selectors(
    uber_env, fake_playwright: dict[str, FakePage | None], monkeypatch
) -> None:
    from integrations.uber_eats import uber_eats

    monkeypatch.setattr(uber_env.config, "UBER_EATS_DRY_RUN", False)
    page = make_checkout_page()
    fake_playwright["page"] = page

    plan, _ = await uber_eats.prepare_order("Chez Pierre", [{"name": "Tacos"}])
    write_selector_file(uber_env.selectors, verified=False)

    outcome = await uber_eats.confirm_order(plan.plan_id)

    assert outcome.ok is False
    assert outcome.status == "blocked"
    assert "place-order" not in page.clicks


@pytest.mark.asyncio
async def test_confirm_order_fails_when_no_confirmation_marker_appears(
    uber_env, fake_playwright: dict[str, FakePage | None], monkeypatch
) -> None:
    from integrations.uber_eats import uber_eats

    monkeypatch.setattr(uber_env.config, "UBER_EATS_DRY_RUN", False)
    page = make_checkout_page()
    page.on_click.pop("place-order")
    fake_playwright["page"] = page

    plan, _ = await uber_eats.prepare_order("Chez Pierre", [{"name": "Tacos"}])
    outcome = await uber_eats.confirm_order(plan.plan_id)

    assert outcome.ok is False
    assert outcome.status == "failed"
    assert "aucune confirmation" in (outcome.error or "")


@pytest.mark.asyncio
async def test_screenshots_are_rotated_and_private(
    uber_env, fake_playwright: dict[str, FakePage | None], monkeypatch
) -> None:
    from integrations.uber_eats import UberEatsSessionExpired, uber_eats

    monkeypatch.setattr(uber_env.config, "UBER_EATS_SCREENSHOT_KEEP", 2)
    fake_playwright["page"] = FakePage(visible={"login-form"})

    for _ in range(3):
        with pytest.raises(UberEatsSessionExpired):
            await uber_eats.prepare_order("Chez Pierre", [{"name": "Tacos"}])

    shots = sorted(uber_env.screenshots.glob("*.png"))
    assert len(shots) == 2
    assert uber_env.screenshots.stat().st_mode & 0o077 == 0


@pytest.mark.asyncio
async def test_prepare_order_without_playwright_stops_before_the_browser(
    uber_env, without_playwright
) -> None:
    """Sans Playwright installé, le refus est immédiat et sans trace de commande."""
    from database import get_food_orders
    from integrations.uber_eats import UberEatsUnavailable, uber_eats

    with pytest.raises(UberEatsUnavailable, match="Playwright non installé"):
        await uber_eats.prepare_order("Chez Pierre", [{"name": "Tacos"}])

    assert get_food_orders(limit=1) == []


# ── Couche action ───────────────────────────────────────────────────────────


@pytest.fixture()
def action_env(uber_env, monkeypatch: pytest.MonkeyPatch):
    """``uber_env`` sans le journal d'action différé.

    ``execute_action`` planifie son journal dans une tâche de fond. Cette tâche
    peut survivre au test, donc au ``monkeypatch`` de ``DB_PATH``, et écrire
    dans la vraie base. On la neutralise ; son contenu n'est pas l'objet de ces
    tests.
    """
    import actions as actions_module

    monkeypatch.setattr(actions_module, "_schedule_action_log", lambda **_kwargs: None)
    return uber_env


@pytest.mark.asyncio
async def test_food_action_first_pass_only_prepares(
    action_env, fake_playwright: dict[str, FakePage | None]
) -> None:
    from actions import execute_action

    page = make_checkout_page()
    fake_playwright["page"] = page

    result = await execute_action(
        {"type": "food_order", "restaurant": "Chez Pierre", "items": [{"name": "Tacos"}]}
    )

    assert result["ok"] is True
    assert result["needs_confirmation"] is True
    assert result["plan_id"]
    assert "place-order" not in page.clicks
    assert "24,90" in result["message"]


@pytest.mark.asyncio
async def test_food_action_ignores_preconfirmation_without_server_plan(
    action_env, fake_playwright: dict[str, FakePage | None], monkeypatch
) -> None:
    """Un ``confirmed: true`` inventé par le modèle ne paie jamais."""
    from actions import execute_action

    monkeypatch.setattr(action_env.config, "UBER_EATS_DRY_RUN", False)
    page = make_checkout_page()
    fake_playwright["page"] = page

    result = await execute_action(
        {
            "type": "food_order",
            "restaurant": "Chez Pierre",
            "items": [{"name": "Tacos"}],
            "confirmed": True,
        }
    )

    assert result["needs_confirmation"] is True
    assert "place-order" not in page.clicks


@pytest.mark.asyncio
async def test_food_action_second_pass_places_order(
    action_env, fake_playwright: dict[str, FakePage | None], monkeypatch
) -> None:
    from actions import execute_action

    monkeypatch.setattr(action_env.config, "UBER_EATS_DRY_RUN", False)
    page = make_checkout_page()
    fake_playwright["page"] = page

    first = await execute_action(
        {"type": "food_order", "restaurant": "Chez Pierre", "items": [{"name": "Tacos"}]}
    )
    second = await execute_action(
        {"type": "food_order", "plan_id": first["plan_id"], "confirmed": True}
    )

    assert second["ok"] is True
    assert second["status"] == "placed"
    assert page.clicks[-1] == "place-order"


@pytest.mark.asyncio
async def test_food_action_replays_confirmation_view_without_paying(
    action_env, fake_playwright: dict[str, FakePage | None], monkeypatch
) -> None:
    from actions import execute_action

    monkeypatch.setattr(action_env.config, "UBER_EATS_DRY_RUN", False)
    page = make_checkout_page()
    fake_playwright["page"] = page

    first = await execute_action(
        {"type": "food_order", "restaurant": "Chez Pierre", "items": [{"name": "Tacos"}]}
    )
    again = await execute_action({"type": "food_order", "plan_id": first["plan_id"]})

    assert again["needs_confirmation"] is True
    assert "place-order" not in page.clicks


@pytest.mark.asyncio
async def test_food_action_rejects_unknown_plan(action_env) -> None:
    from actions import execute_action

    result = await execute_action(
        {"type": "food_order", "plan_id": "inconnu", "confirmed": True}
    )

    assert result["ok"] is False
    assert "Panier refusé" in result["message"]


@pytest.mark.asyncio
async def test_food_action_reports_invalid_request(action_env) -> None:
    from actions import execute_action

    result = await execute_action({"type": "food_order", "restaurant": "", "items": []})

    assert result["ok"] is False
    assert "Demande incomplète" in result["message"]


def test_food_order_is_registered_for_llm_followup() -> None:
    from api.chat_actions import ACTIONS_WITH_FOLLOWUP

    assert "food_order" in ACTIONS_WITH_FOLLOWUP


def test_llm_boundary_strips_paths_and_plan_ids() -> None:
    from jarvis.security.llm_data_boundary import format_action_result_for_external_llm

    formatted = format_action_result_for_external_llm(
        {"type": "food_order"},
        {
            "ok": True,
            "status": "placed",
            "restaurant": "Chez Pierre",
            "items_label": "1x Tacos",
            "total_price": 24.9,
            "currency": "EUR",
            "dry_run": False,
            "plan_id": "secret-plan-token",
            "screenshot_path": "/Users/zeldris/JARVIS/data/uber_eats_screenshots/x.png",
            "message": "Commande passée chez Chez Pierre pour 24,90 €.",
        },
    )

    assert "secret-plan-token" not in formatted
    assert "uber_eats_screenshots" not in formatted
    assert "placed" in formatted
    assert "24.9" in formatted


def test_abandoned_proposal_revokes_the_food_plan() -> None:
    from api.action_confirmations import _revoke_action_plan
    from integrations.uber_eats import (
        _register_plan,
        get_order_plan,
        reset_order_plans_for_tests,
        UberEatsPlanError,
    )

    reset_order_plans_for_tests()
    _register_plan(_make_plan())

    revoked = _revoke_action_plan({"type": "food_order", "plan_id": "plan-1"})

    assert revoked is True
    with pytest.raises(UberEatsPlanError):
        get_order_plan("plan-1")


# ── Routage et enregistrement de l'agent ────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "message",
    [
        "j'ai faim, commande une pizza",
        "commande à manger chez Chez Pierre",
        "ouvre uber eats",
        "commande des sushis pour ce soir",
        "fais-toi livrer un burger",
    ],
)
async def test_orchestrator_routes_food_requests(message: str) -> None:
    from agents.orchestrator import classify_category

    assert await classify_category(message) == "FOOD"


@pytest.mark.parametrize(
    "message",
    [
        "lance la commande git status",
        "commande npm run build dans le projet",
        "commande de la boucle de rendu à revoir",
        "j'ai passé une sale journée, je me sens vide",
    ],
)
def test_food_keywords_stay_narrow(message: str) -> None:
    """Le mot « commande » seul ne doit jamais déclencher une dépense."""
    from agents.orchestrator import FOOD_PATTERNS, _match_any

    assert _match_any(message, FOOD_PATTERNS) is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("debug mon API Flask", "DEVOPS"),
        ("git merge conflict sur ma branche", "DEVOPS"),
        ("je suis stresse par mon code", "COACH"),
        ("planning de la semaine", "PRODUCTIVITY"),
    ],
)
async def test_food_routing_does_not_disturb_existing_categories(
    message: str, expected: str
) -> None:
    from agents.orchestrator import classify_category

    assert await classify_category(message) == expected


def test_food_category_maps_to_food_agent() -> None:
    from agents.orchestrator import CATEGORIES, CATEGORY_TO_AGENT

    assert "FOOD" in CATEGORIES
    assert CATEGORY_TO_AGENT["FOOD"] == "food"


def test_food_agent_speaks_with_the_jarvis_persona() -> None:
    from agents.food import food_agent

    assert food_agent.name == "food"
    assert food_agent.inject_persona is True
    assert (PROJECT_ROOT / "prompts" / "food.txt").is_file()


def test_food_agent_context_survives_a_broken_integration(
    uber_env, without_playwright
) -> None:
    """Une intégration indisponible dégrade le contexte, elle ne casse rien."""
    from agents.food import food_agent

    context = food_agent._enrich_context({})

    assert "indisponible" in context["food_status_context"]
    assert "0/2 commande(s)" in context["food_history_context"]


def test_food_agent_context_lists_recent_orders(uber_env) -> None:
    from agents.food import food_agent
    from database import record_food_order

    record_food_order(
        restaurant="Chez Pierre",
        items=[{"name": "Tacos"}],
        status="placed",
        dry_run=False,
        total_price=24.9,
    )

    history = food_agent._enrich_context({})["food_history_context"]

    assert "Chez Pierre" in history
    assert "1/2 commande(s)" in history


def test_lifespan_registers_the_food_agent() -> None:
    source = (PROJECT_ROOT / "api" / "lifespan.py").read_text(encoding="utf-8")

    assert "register_agent(food_agent)" in source
