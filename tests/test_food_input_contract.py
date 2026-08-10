"""Contrats Pydantic stricts de la surface HTTP Food."""

from __future__ import annotations

from pathlib import Path


from tests.conftest import authenticate
from tests.test_uber_eats import uber_env  # noqa: F401


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _client():
    import main
    from fastapi.testclient import TestClient

    return TestClient(main.app)


def test_quick_order_and_rating_refuse_coercions_and_unknown_fields(uber_env) -> None:  # noqa: F811
    with _client() as client:
        authenticate(client)
        responses = (
            client.post(
                "/api/food/suggestions/1/order",
                json={"accepted_price": "12.50"},
            ),
            client.post(
                "/api/food/suggestions/1/order",
                json={"accepted_price": 12.5, "force": True},
            ),
            client.post("/api/food/orders/1/rating", json={"rating": "5"}),
            client.post("/api/food/orders/1/rating", json={"rating": 6}),
        )

    assert [response.status_code for response in responses] == [422, 422, 422, 422]


def test_menu_refresh_requires_a_bounded_string_list(uber_env) -> None:  # noqa: F811
    with _client() as client:
        authenticate(client)
        responses = (
            client.post(
                "/api/food/menus/refresh",
                json={"restaurants": "Chez Pierre"},
            ),
            client.post("/api/food/menus/refresh", json={"restaurants": [42]}),
            client.post(
                "/api/food/menus/refresh",
                json={"restaurants": ["Chez Pierre"], "all": True},
            ),
        )

    assert [response.status_code for response in responses] == [422, 422, 422]


def test_settings_patch_is_strict_non_empty_and_non_null(uber_env) -> None:  # noqa: F811
    with _client() as client:
        authenticate(client)
        valid = client.patch(
            "/api/food/settings",
            json={"max_order_price": 10, "max_daily_orders": 1},
        )
        responses = (
            client.patch("/api/food/settings", json={}),
            client.patch("/api/food/settings", json={"enabled": "false"}),
            client.patch("/api/food/settings", json={"max_items": 1.5}),
            client.patch("/api/food/settings", json={"max_items": None}),
            client.patch("/api/food/settings", json={"verified": True}),
        )

    assert valid.status_code == 200
    assert valid.json()["settings"]["max_order_price"] == 10.0
    assert [response.status_code for response in responses] == [422] * 5


def test_manual_cart_rejects_malformed_nested_items_before_browser_access(uber_env) -> None:  # noqa: F811
    with _client() as client:
        authenticate(client)
        responses = (
            client.post(
                "/api/food/cart/prepare",
                json={"restaurant": 42, "items": [{"name": "Tacos"}]},
            ),
            client.post(
                "/api/food/cart/prepare",
                json={"restaurant": "Chez Pierre", "items": []},
            ),
            client.post(
                "/api/food/cart/prepare",
                json={
                    "restaurant": "Chez Pierre",
                    "items": [{"name": "Tacos", "quantity": "2"}],
                },
            ),
            client.post(
                "/api/food/cart/prepare",
                json={
                    "restaurant": "Chez Pierre",
                    "items": [{"name": "Tacos", "price": 1}],
                },
            ),
        )

    assert [response.status_code for response in responses] == [422, 422, 422, 422]


def test_capture_mode_is_an_allowlisted_literal(uber_env) -> None:  # noqa: F811
    with _client() as client:
        authenticate(client)
        invalid = client.post(
            "/api/food/session/capture",
            json={"mode": "codegen-plus"},
        )
        unknown = client.post(
            "/api/food/session/capture",
            json={"mode": "session", "headless": True},
        )

    assert invalid.status_code == 422
    assert unknown.status_code == 422


def test_food_router_has_no_raw_mapping_body_contracts() -> None:
    source = (PROJECT_ROOT / "api/router_food.py").read_text(encoding="utf-8")

    assert "payload: dict" not in source
    assert "body: dict" not in source
