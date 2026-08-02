"""Tests : suggestions de repas, commande en un clic et suivi de livraison.

La propriété centrale vérifiée ici est celle qui engage de l'argent : **un clic
ne peut jamais dépenser plus que le montant affiché sur le bouton**. Le reste
(scoring, relevé de menus, notation) protège la qualité des suggestions, mais
c'est cette borne qui rend le parcours en un clic défendable.

Playwright n'est pas installé : les faux objets de ``tests/test_uber_eats.py``
sont réutilisés pour exercer le parcours réel sans ouvrir de navigateur.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from tests.test_uber_eats import (  # noqa: E402  (dépend de sys.path)
    FakePage,
    fake_playwright,  # noqa: F401  (fixture réexportée)
    make_checkout_page,
    uber_env,  # noqa: F401  (fixture réexportée)
)


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture()
def food_env(uber_env, monkeypatch: pytest.MonkeyPatch):  # noqa: F811
    """``uber_env`` avec les suggestions et le relevé de menus activés.

    Les événements sont capturés au lieu d'être diffusés : une émission réelle
    planifie une tâche qui survit à la fermeture de la boucle du test. Le test
    ``test_emit_pushes_to_the_event_bus`` couvre le branchement lui-même.
    """
    import config
    from api import food_support

    monkeypatch.setattr(config, "FOOD_SUGGESTIONS_ENABLED", True)
    monkeypatch.setattr(config, "FOOD_MENU_SCRAPE_ENABLED", True)
    monkeypatch.setattr(config, "FOOD_QUICK_ORDER_PRICE_TOLERANCE", 0.15)
    monkeypatch.setattr(config, "FOOD_SUGGESTION_MIN_ORDERS", 1)

    captured: list[Any] = []
    monkeypatch.setattr(
        food_support, "event_bus", SimpleNamespace(emit_nowait=captured.append)
    )
    uber_env.events = captured
    return uber_env


def seed_suggestion(
    *,
    slot: int = 1,
    restaurant: str = "Chez Pierre",
    estimated: float = 20.0,
    max_price: float = 23.0,
) -> dict[str, Any]:
    """Insère une suggestion active et retourne sa représentation."""
    from database import get_active_suggestion_by_slot, replace_suggestions

    replace_suggestions(
        [
            {
                "restaurant": restaurant,
                "items": [{"name": "Tacos", "quantity": 1}],
                "estimated_price": estimated,
                "max_price": max_price,
                "currency": "EUR",
                "reasoning": "Votre habitude du jeudi.",
                "score": 42.0,
                "factors": {"frequency": 20.0},
            }
        ]
    )
    suggestion = get_active_suggestion_by_slot(slot)
    assert suggestion is not None
    return suggestion


# ── Lecture des pages de suivi (fonctions pures) ────────────────────────────


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Votre commande est en préparation", "preparing"),
        ("Order placed", "placed"),
        ("Le livreur est en route", "on_the_way"),
        ("Commande livrée", "delivered"),
        ("Commande annulée", "cancelled"),
        ("Le coursier a récupéré votre commande", "picked_up"),
    ],
)
def test_parse_delivery_status_maps_known_wordings(raw: str, expected: str) -> None:
    from integrations.uber_eats_discovery import parse_delivery_status

    assert parse_delivery_status(raw) == expected


def test_parse_delivery_status_returns_none_rather_than_guessing() -> None:
    """Un libellé inconnu ne doit pas être traduit au hasard."""
    from integrations.uber_eats_discovery import parse_delivery_status

    assert parse_delivery_status("Merci de votre fidélité") is None
    assert parse_delivery_status("") is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Arrivée dans 25 min", 25),
        ("20–30 min", 30),
        ("1 h 05", 65),
        ("2h", 120),
        ("bientôt", None),
        ("", None),
    ],
)
def test_parse_eta_minutes_reads_common_wordings(raw: str, expected: int | None) -> None:
    from integrations.uber_eats_discovery import parse_eta_minutes

    assert parse_eta_minutes(raw) == expected


def test_parse_eta_minutes_rejects_implausible_durations() -> None:
    from integrations.uber_eats_discovery import parse_eta_minutes

    assert parse_eta_minutes("9999 min") is None


# ── Frontière de domaine sur les URL de suivi ───────────────────────────────


def test_tracking_url_must_stay_on_the_configured_domain() -> None:
    """Ouvrir un lien externe avec la session exposerait ses cookies."""
    from integrations.uber_eats import UberEatsAutomationError
    from integrations.uber_eats_discovery import UberEatsDiscovery

    validate = UberEatsDiscovery._validate_tracking_url
    assert validate("https://www.ubereats.com/orders/abc").endswith("/orders/abc")

    for hostile in (
        "https://evil.example.com/orders/abc",
        "http://www.ubereats.com/orders/abc",
        "https://www.ubereats.com.evil.example/orders",
        "",
    ):
        with pytest.raises(UberEatsAutomationError):
            validate(hostile)


def test_confirm_order_only_keeps_a_same_domain_tracking_url() -> None:
    from integrations.uber_eats import UberEatsClient

    keep = UberEatsClient._safe_tracking_url
    assert keep("https://www.ubereats.com/orders/1") == "https://www.ubereats.com/orders/1"
    assert keep("https://attacker.test/orders/1") is None
    assert keep("") is None


# ── Scoring déterministe ────────────────────────────────────────────────────


def build_history() -> list[dict[str, Any]]:
    """Historique synthétique : un habitué, un occasionnel."""
    base = datetime(2026, 7, 30, 20, 0, 0)
    return [
        {
            "restaurant": "Chez Pierre",
            "total_price": 22.0,
            "rating": 5,
            "created_at": (base - timedelta(days=day)).strftime("%Y-%m-%d %H:%M:%S"),
        }
        for day in (0, 7, 14)
    ] + [
        {
            "restaurant": "Sushi Bar",
            "total_price": 31.0,
            "rating": 2,
            "created_at": (base - timedelta(days=3)).strftime("%Y-%m-%d %H:%M:%S"),
        }
    ]


def test_summarise_orders_counts_restaurants_and_weekday_pairs() -> None:
    from scripts.food_intelligence import summarise_orders

    summary = summarise_orders(build_history())

    assert summary["order_count"] == 4
    assert summary["restaurants"]["Chez Pierre"] == 3
    assert summary["avg_rating"]["Chez Pierre"] == 5.0
    assert summary["avg_spend"] == pytest.approx(24.25)
    # 30/07/2026 est un jeudi (indice 3) : les trois commandes tombent le même jour.
    assert summary["weekday_pairs"]["3|Chez Pierre"] == 3


def test_derive_preferences_exposes_confidence_with_sample_size() -> None:
    from scripts.food_intelligence import derive_preferences, summarise_orders

    preferences = derive_preferences(summarise_orders(build_history()))

    assert preferences["favorite_restaurant"]["value"] == "Chez Pierre"
    assert 0.0 < preferences["favorite_restaurant"]["confidence"] <= 0.95
    assert preferences["favorite_restaurant"]["sample_size"] == 3


def test_score_favours_the_habitual_restaurant_on_its_usual_day() -> None:
    from scripts.food_intelligence import score_restaurant, summarise_orders

    summary = summarise_orders(build_history())
    now = datetime(2026, 8, 6, 20, 0, 0)  # jeudi suivant

    habitual, factors = score_restaurant(
        "Chez Pierre", summary=summary, weekday=3, now=now, budget=25.0, estimated_price=20.0
    )
    occasional, _ = score_restaurant(
        "Sushi Bar", summary=summary, weekday=3, now=now, budget=25.0, estimated_price=20.0
    )

    assert habitual > occasional
    assert factors["weekday_match"] > 0
    assert factors["rating"] > 0


def test_score_penalises_a_restaurant_ordered_yesterday() -> None:
    """Proposer trois fois le même plat dans la semaine n'est pas conseiller."""
    from scripts.food_intelligence import score_restaurant, summarise_orders

    summary = summarise_orders(build_history())
    day_after = datetime(2026, 7, 31, 20, 0, 0)
    far_later = datetime(2026, 9, 30, 20, 0, 0)

    fresh, _ = score_restaurant(
        "Chez Pierre", summary=summary, weekday=3, now=day_after, budget=None, estimated_price=None
    )
    stale, _ = score_restaurant(
        "Chez Pierre", summary=summary, weekday=3, now=far_later, budget=None, estimated_price=None
    )

    assert stale > fresh


def test_build_suggestions_skips_restaurants_without_priced_items() -> None:
    """Sans prix, le montant autorisé au clic serait un chèque en blanc."""
    from scripts.food_intelligence import build_suggestions, summarise_orders

    suggestions = build_suggestions(
        summary=summarise_orders(build_history()),
        menus={
            "Chez Pierre": [{"item_name": "Tacos", "price": 9.5, "currency": "EUR"}],
            "Sans Prix": [{"item_name": "Mystère", "price": None, "currency": "EUR"}],
        },
        now=datetime(2026, 8, 6, 20, 0, 0),
        budget=25.0,
        slots=3,
    )

    assert [item["restaurant"] for item in suggestions] == ["Chez Pierre"]
    assert suggestions[0]["max_price"] >= suggestions[0]["estimated_price"]


def test_build_suggestions_never_authorises_more_than_the_order_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts.food_intelligence import build_suggestions, summarise_orders

    monkeypatch.setattr("config.UBER_EATS_MAX_ORDER_PRICE", 15.0)
    suggestions = build_suggestions(
        summary=summarise_orders(build_history()),
        menus={"Chez Pierre": [{"item_name": "Menu complet", "price": 14.0, "currency": "EUR"}]},
        now=datetime(2026, 8, 6, 20, 0, 0),
        budget=None,
        slots=3,
    )

    assert suggestions[0]["max_price"] <= 15.0


# ── Persistance ─────────────────────────────────────────────────────────────


def test_replace_menu_items_is_a_full_replacement(food_env) -> None:
    from database import get_menu_items, replace_menu_items

    replace_menu_items(
        "Chez Pierre",
        [
            {"item_name": "Tacos", "price": 9.5, "category": "Plats"},
            {"item_name": "Frites", "price": 3.0, "category": "Sides"},
        ],
    )
    replace_menu_items("Chez Pierre", [{"item_name": "Tacos", "price": 10.5}])

    items = get_menu_items("Chez Pierre")
    assert [item["item_name"] for item in items] == ["Tacos"]
    assert items[0]["price"] == 10.5


def test_replace_menu_items_refuses_an_empty_restaurant_name(food_env) -> None:
    from database.food_intelligence import FoodIntelligenceError, replace_menu_items

    with pytest.raises(FoodIntelligenceError):
        replace_menu_items("   ", [{"item_name": "Tacos", "price": 9.0}])


def test_generating_a_new_batch_expires_the_previous_one(food_env) -> None:
    from database import get_active_suggestions

    seed_suggestion(restaurant="Chez Pierre")
    seed_suggestion(restaurant="Sushi Bar")

    active = get_active_suggestions()
    assert [item["restaurant"] for item in active] == ["Sushi Bar"]


def test_claim_suggestion_succeeds_exactly_once(food_env) -> None:
    """Deux clics rapides ne doivent pas produire deux commandes."""
    from database import claim_suggestion

    suggestion = seed_suggestion()

    assert claim_suggestion(suggestion["id"]) is True
    assert claim_suggestion(suggestion["id"]) is False


def test_expired_suggestions_are_not_returned(food_env) -> None:
    from database import get_active_suggestions, replace_suggestions

    replace_suggestions(
        [
            {
                "restaurant": "Chez Pierre",
                "items": [{"name": "Tacos", "quantity": 1}],
                "estimated_price": 12.0,
                "max_price": 14.0,
            }
        ],
        ttl_hours=1,
    )
    with __import__("database").get_db() as conn:
        conn.execute("UPDATE food_suggestions SET expires_at = '2000-01-01 00:00:00'")

    assert get_active_suggestions() == []


def test_rating_is_bounded_and_persisted(food_env) -> None:
    from database import rate_food_order, record_food_order
    from database.food_orders import FoodOrderError

    order_id = record_food_order(
        restaurant="Chez Pierre",
        items=[{"name": "Tacos", "quantity": 1}],
        status="placed",
        dry_run=False,
        total_price=20.0,
    )

    assert rate_food_order(order_id, 4)["rating"] == 4
    with pytest.raises(FoodOrderError):
        rate_food_order(order_id, 9)
    assert rate_food_order(999_999, 4) is None


def test_delivery_updates_stamp_the_arrival_only_once(food_env) -> None:
    from database import record_food_order, update_food_order_delivery

    order_id = record_food_order(
        restaurant="Chez Pierre",
        items=[{"name": "Tacos", "quantity": 1}],
        status="placed",
        dry_run=False,
        total_price=20.0,
    )

    update_food_order_delivery(order_id, delivery_status="preparing", eta_minutes=30)
    delivered = update_food_order_delivery(order_id, delivery_status="delivered")
    first_stamp = delivered["delivered_at"]
    again = update_food_order_delivery(order_id, delivery_status="delivered")

    assert first_stamp is not None
    assert again["delivered_at"] == first_stamp
    # L'estimation précédente est conservée quand la mise à jour ne la fournit pas.
    assert again["eta_minutes"] == 30


def test_orders_awaiting_delivery_ignores_simulations(food_env) -> None:
    from database import get_orders_awaiting_delivery, record_food_order

    record_food_order(
        restaurant="Simulé",
        items=[{"name": "Tacos", "quantity": 1}],
        status="simulated",
        dry_run=True,
        total_price=20.0,
    )
    record_food_order(
        restaurant="Réel",
        items=[{"name": "Tacos", "quantity": 1}],
        status="placed",
        dry_run=False,
        total_price=20.0,
        delivery_status="preparing",
    )

    awaiting = get_orders_awaiting_delivery()
    assert [order["restaurant"] for order in awaiting] == ["Réel"]


# ── Commande en un clic ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_quick_order_places_the_order_within_the_displayed_amount(
    food_env, fake_playwright, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    import config
    from api.food_support import quick_order
    from database import get_food_orders

    monkeypatch.setattr(config, "UBER_EATS_DRY_RUN", False)
    fake_playwright["page"] = make_checkout_page("Total 20,00 €")
    suggestion = seed_suggestion(estimated=20.0, max_price=23.0)

    result = await quick_order(1, suggestion["max_price"])

    assert result["ok"] is True
    assert result["status"] == "placed"
    assert result["total_price"] == 20.0
    placed = [order for order in get_food_orders(limit=10) if order["status"] == "placed"]
    assert placed and placed[0]["suggestion_id"] == suggestion["id"]


@pytest.mark.asyncio
async def test_quick_order_refuses_to_pay_above_the_displayed_amount(
    food_env, fake_playwright, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    """Le panier réel dépasse l'estimation : rien n'est payé sans nouvel accord."""
    import config
    from api.food_support import quick_order
    from database import get_food_orders

    monkeypatch.setattr(config, "UBER_EATS_DRY_RUN", False)
    fake_playwright["page"] = make_checkout_page("Total 38,00 €")
    suggestion = seed_suggestion(estimated=20.0, max_price=23.0)

    result = await quick_order(1, suggestion["max_price"])

    assert result["status"] == "confirmation_required"
    assert result["total_price"] == 38.0
    assert result["authorised_price"] == 23.0
    assert not [order for order in get_food_orders(limit=10) if order["status"] == "placed"]


@pytest.mark.asyncio
async def test_quick_order_rejects_a_stale_displayed_amount(food_env) -> None:
    """Un écran périmé annonce un autre montant : le clic n'engage rien."""
    from api.food_support import QuickOrderError, quick_order

    seed_suggestion(estimated=20.0, max_price=23.0)

    with pytest.raises(QuickOrderError) as excinfo:
        await quick_order(1, 9.99)

    assert excinfo.value.status_code == 409


@pytest.mark.asyncio
async def test_quick_order_requires_an_accepted_amount(food_env) -> None:
    from api.food_support import QuickOrderError, quick_order

    seed_suggestion()

    with pytest.raises(QuickOrderError) as excinfo:
        await quick_order(1, None)

    assert excinfo.value.status_code == 400


@pytest.mark.asyncio
async def test_quick_order_on_an_unknown_slot_is_a_not_found(food_env) -> None:
    from api.food_support import QuickOrderError, quick_order

    with pytest.raises(QuickOrderError) as excinfo:
        await quick_order(2, 20.0)

    assert excinfo.value.status_code == 404


@pytest.mark.asyncio
async def test_quick_order_releases_the_suggestion_when_the_cart_fails(
    food_env, fake_playwright  # noqa: F811
) -> None:
    """Un échec de panier ne doit pas consommer la suggestion pour rien."""
    from api.food_support import QuickOrderError, quick_order
    from database import get_active_suggestion_by_slot

    # Page sans carte de restaurant : la recherche échouera.
    fake_playwright["page"] = FakePage(visible={"account-menu", "search-input"})
    suggestion = seed_suggestion()

    with pytest.raises(QuickOrderError):
        await quick_order(1, suggestion["max_price"])

    assert get_active_suggestion_by_slot(1) is not None


@pytest.mark.asyncio
async def test_quick_order_in_dry_run_never_reports_a_real_purchase(
    food_env, fake_playwright  # noqa: F811
) -> None:
    from api.food_support import quick_order

    fake_playwright["page"] = make_checkout_page("Total 20,00 €")
    suggestion = seed_suggestion(estimated=20.0, max_price=23.0)

    result = await quick_order(1, suggestion["max_price"])

    assert result["status"] == "simulated"
    assert result["dry_run"] is True


# ── Suivi de livraison ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_refresh_delivery_progress_updates_and_reports(
    food_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    from api.food_support import refresh_delivery_progress
    from database import get_food_order, record_food_order
    from integrations.uber_eats_discovery import DeliveryProgress, uber_eats_discovery

    order_id = record_food_order(
        restaurant="Chez Pierre",
        items=[{"name": "Tacos", "quantity": 1}],
        status="placed",
        dry_run=False,
        total_price=20.0,
        tracking_url="https://www.ubereats.com/orders/xyz",
        delivery_status="placed",
    )

    async def _fake_progress(_url: str) -> DeliveryProgress:
        return DeliveryProgress(
            status="on_the_way", eta_minutes=12, raw_status="En route", raw_eta="12 min"
        )

    monkeypatch.setattr(uber_eats_discovery, "read_delivery_progress", _fake_progress)

    report = await refresh_delivery_progress()

    assert report["updated"] == 1
    row = get_food_order(order_id)
    assert row["delivery_status"] == "on_the_way"
    assert row["eta_minutes"] == 12


@pytest.mark.asyncio
async def test_refresh_delivery_progress_ignores_unreadable_statuses(
    food_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Un libellé non reconnu laisse la commande dans son état connu."""
    from api.food_support import refresh_delivery_progress
    from database import get_food_order, record_food_order
    from integrations.uber_eats_discovery import DeliveryProgress, uber_eats_discovery

    order_id = record_food_order(
        restaurant="Chez Pierre",
        items=[{"name": "Tacos", "quantity": 1}],
        status="placed",
        dry_run=False,
        total_price=20.0,
        tracking_url="https://www.ubereats.com/orders/xyz",
        delivery_status="preparing",
    )

    async def _unknown(_url: str) -> DeliveryProgress:
        return DeliveryProgress(status=None, eta_minutes=None, raw_status="???", raw_eta="")

    monkeypatch.setattr(uber_eats_discovery, "read_delivery_progress", _unknown)

    report = await refresh_delivery_progress()

    assert report["updated"] == 0
    assert get_food_order(order_id)["delivery_status"] == "preparing"


# ── Relevé de menus ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_refresh_menus_persists_results_and_keeps_going_after_a_failure(
    food_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    from integrations.uber_eats import UberEatsAutomationError
    from integrations.uber_eats_discovery import MenuEntry, uber_eats_discovery

    async def _scrape(restaurant: str) -> list[MenuEntry]:
        if restaurant == "Fermé":
            raise UberEatsAutomationError("restaurant introuvable")
        return [
            MenuEntry(
                restaurant=restaurant,
                item_name="Tacos",
                category="Plats",
                price=9.5,
                currency="EUR",
            )
        ]

    monkeypatch.setattr(uber_eats_discovery, "scrape_menu", _scrape)

    report = await uber_eats_discovery.refresh_menus(["Chez Pierre", "Fermé", "Sushi Bar"])

    assert report["refreshed"] == {"Chez Pierre": 1, "Sushi Bar": 1}
    assert [failure["restaurant"] for failure in report["failures"]] == ["Fermé"]


@pytest.mark.asyncio
async def test_scraping_is_refused_when_disabled(food_env, monkeypatch: pytest.MonkeyPatch) -> None:
    import config
    from integrations.uber_eats import UberEatsUnavailable
    from integrations.uber_eats_discovery import uber_eats_discovery

    monkeypatch.setattr(config, "FOOD_MENU_SCRAPE_ENABLED", False)

    with pytest.raises(UberEatsUnavailable):
        await uber_eats_discovery.list_feed_restaurants()


# ── Génération complète ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_generate_suggestions_needs_a_menu_in_cache(food_env) -> None:
    from scripts.food_intelligence import generate_suggestions

    report = await generate_suggestions()

    assert report == {"ok": False, "reason": "no_menu_cached", "created": 0}


@pytest.mark.asyncio
async def test_generate_suggestions_writes_a_batch_with_a_fallback_sentence(
    food_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sans modèle joignable, la phrase de conseil reste déterministe."""
    from database import get_active_suggestions, record_food_order, replace_menu_items
    from scripts import food_intelligence

    replace_menu_items(
        "Chez Pierre",
        [
            {"item_name": "Tacos", "price": 9.5, "category": "Plats"},
            {"item_name": "Frites", "price": 3.0, "category": "Sides"},
        ],
    )
    record_food_order(
        restaurant="Chez Pierre",
        items=[{"name": "Tacos", "quantity": 1}],
        status="placed",
        dry_run=False,
        total_price=12.5,
    )

    async def _unreachable(**_kwargs: Any) -> dict[str, str]:
        raise RuntimeError("réseau indisponible")

    monkeypatch.setattr("llm.chat", _unreachable)

    report = await food_intelligence.generate_suggestions()
    suggestions = get_active_suggestions()

    assert report["ok"] is True
    assert len(suggestions) == 1
    assert suggestions[0]["restaurant"] == "Chez Pierre"
    assert suggestions[0]["reasoning"]
    assert suggestions[0]["max_price"] >= suggestions[0]["estimated_price"]


@pytest.mark.asyncio
async def test_generate_suggestions_uses_model_sentences_when_available(
    food_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    from database import get_active_suggestions, record_food_order, replace_menu_items
    from scripts import food_intelligence

    replace_menu_items("Chez Pierre", [{"item_name": "Tacos", "price": 9.5}])
    record_food_order(
        restaurant="Chez Pierre",
        items=[{"name": "Tacos", "quantity": 1}],
        status="placed",
        dry_run=False,
        total_price=9.5,
    )

    async def _chat(**_kwargs: Any) -> dict[str, str]:
        return {"content": '```json\n["Votre jeudi habituel, Monsieur."]\n```'}

    monkeypatch.setattr("llm.chat", _chat)

    await food_intelligence.generate_suggestions()

    assert get_active_suggestions()[0]["reasoning"] == "Votre jeudi habituel, Monsieur."


def test_reasoning_payload_parser_tolerates_noise() -> None:
    from scripts.food_intelligence import _parse_reasoning_payload

    assert _parse_reasoning_payload('["a", "b"]') == ["a", "b"]
    assert _parse_reasoning_payload('Voici :\n["a"]\nVoilà.') == ["a"]
    assert _parse_reasoning_payload("pas de json") == []


# ── Contrainte structurelle ─────────────────────────────────────────────────


def test_discovery_module_cannot_place_an_order() -> None:
    """Le relevé est en lecture seule — et cette propriété est vérifiée.

    Un ajout involontaire de clic d'achat dans ce module contournerait tout le
    dispositif de confirmation ; le test échoue avant la revue humaine.
    """
    source = (PROJECT_ROOT / "integrations" / "uber_eats_discovery.py").read_text(
        encoding="utf-8"
    )

    for forbidden in (
        "place_order_button",
        "checkout_button",
        "add_to_cart_button",
        "confirm_order",
        "prepare_order",
        "_place_order",
    ):
        assert forbidden not in source, (
            f"integrations/uber_eats_discovery.py référence {forbidden!r} : "
            "le relevé doit rester incapable de commander"
        )


def test_food_routes_are_all_under_the_session_gate() -> None:
    """Aucune route food ne doit figurer parmi les exceptions d'authentification."""
    from api.middleware import _bypasses_session_gate

    for method, path in (
        ("GET", "/api/food/status"),
        ("GET", "/api/food/suggestions"),
        ("POST", "/api/food/suggestions/1/order"),
        ("POST", "/api/food/orders/1/rating"),
        ("POST", "/api/food/menus/refresh"),
    ):
        assert _bypasses_session_gate(method, path) is False


def test_emit_pushes_to_the_event_bus(monkeypatch: pytest.MonkeyPatch) -> None:
    """La page reçoit ses mises à jour par le flux d'événements existant."""
    from api import food_support

    sent: list[Any] = []
    monkeypatch.setattr(food_support, "event_bus", SimpleNamespace(emit_nowait=sent.append))

    food_support._emit({"status": "delivery_update", "order_id": 7})

    assert len(sent) == 1
    assert sent[0].type == "food.order_updated"
    assert sent[0].data["order_id"] == 7


def test_emit_never_raises_when_the_bus_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    """Un échec de diffusion ne doit pas faire échouer une commande réussie."""
    from api import food_support

    def _boom(_event: Any) -> None:
        raise RuntimeError("bus indisponible")

    monkeypatch.setattr(food_support, "event_bus", SimpleNamespace(emit_nowait=_boom))
    food_support._emit({"status": "delivery_update"})


def test_food_event_type_is_declared_without_shifting_domain_events() -> None:
    """Ajouter un type ne doit pas décaler la fenêtre des événements de domaine."""
    from jarvis.event_bus import DOMAIN_EVENT_TYPES, VALID_EVENT_TYPES
    from jarvis.events import DOMAIN_EVENT_CLASSES

    assert "food.order_updated" in VALID_EVENT_TYPES
    assert "food.order_updated" not in DOMAIN_EVENT_TYPES
    assert tuple(event.EVENT_TYPE for event in DOMAIN_EVENT_CLASSES) == DOMAIN_EVENT_TYPES


def test_food_agent_context_lists_active_suggestions(food_env) -> None:
    from agents.food import food_agent

    seed_suggestion(restaurant="Chez Pierre", estimated=20.0)

    context = food_agent._enrich_context({})

    assert "Chez Pierre" in context["food_suggestions_context"]
    assert "[1]" in context["food_suggestions_context"]


def test_food_agent_context_mentions_a_delivery_in_progress(food_env) -> None:
    from agents.food import food_agent
    from database import record_food_order

    record_food_order(
        restaurant="Chez Pierre",
        items=[{"name": "Tacos", "quantity": 1}],
        status="placed",
        dry_run=False,
        total_price=20.0,
        delivery_status="on_the_way",
    )

    context = food_agent._enrich_context({})

    assert "Livraison en cours" in context["food_suggestions_context"]


def test_suggestion_items_survive_a_corrupted_payload(food_env) -> None:
    """Une ligne illisible ne doit pas faire tomber toute la page."""
    from database import get_active_suggestions, get_db

    seed_suggestion()
    with get_db() as conn:
        conn.execute("UPDATE food_suggestions SET items_json = 'pas du json'")

    suggestions = get_active_suggestions()
    assert suggestions[0]["items"] == []
