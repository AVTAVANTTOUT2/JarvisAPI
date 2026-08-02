"""Tests : pilotage d'Uber Eats depuis le tableau de bord.

La propriété défendue ici est la borne de pouvoir : l'interface web peut tout
régler, **sauf** s'accorder plus de dépense que ce que le fichier `.env` de la
machine autorise déjà. Une session compromise ne doit pas pouvoir relever un
plafond avant de commander.

Le reste couvre le panier libre en deux passes et le diagnostic, qui évitent
d'avoir à ouvrir un terminal sur le Mac pour réparer l'intégration.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from tests.test_uber_eats import (  # noqa: E402  (dépend de sys.path)
    FakePage,
    fake_playwright,  # noqa: F401  (fixture réexportée)
    make_checkout_page,
    uber_env,  # noqa: F401  (fixture réexportée)
    write_selector_file,
)


@pytest.fixture()
def control_env(uber_env, monkeypatch: pytest.MonkeyPatch):  # noqa: F811
    """``uber_env`` avec le suivi de capture remis à zéro."""
    from api import food_control

    food_control.reset_capture_for_tests()
    monkeypatch.setattr(food_control, "CAPTURE_TIMEOUT_SECONDS", 5)
    return uber_env


# ── Réglages : l'interface resserre, jamais elle n'élargit ──────────────────


def test_reading_defaults_before_init_does_not_create_an_empty_database(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import database
    from integrations.uber_eats_settings import get_settings

    missing_db = tmp_path / "not-initialised.db"
    monkeypatch.setattr(database, "DB_PATH", missing_db)

    assert get_settings().dry_run is True
    assert not missing_db.exists()


def test_settings_default_to_the_env_values(control_env) -> None:
    from integrations.uber_eats_settings import get_settings

    settings = get_settings()

    assert settings.enabled is True
    assert settings.dry_run is True
    assert settings.max_order_price == 40.0
    assert settings.max_daily_orders == 2


def test_settings_can_be_tightened_from_the_interface(control_env) -> None:
    from integrations.uber_eats_settings import get_settings, update_settings

    update_settings({"max_order_price": 15, "max_daily_orders": 1})

    settings = get_settings()
    assert settings.max_order_price == 15.0
    assert settings.max_daily_orders == 1


def test_settings_refuse_to_exceed_the_env_ceiling(control_env) -> None:
    """Le cœur du modèle : le réseau ne s'accorde pas plus de budget."""
    from integrations.uber_eats_settings import FoodSettingsError, get_settings, update_settings

    with pytest.raises(FoodSettingsError, match="au-dessus de la borne"):
        update_settings({"max_order_price": 5_000})

    assert get_settings().max_order_price == 40.0


def test_a_stored_value_above_the_ceiling_is_clamped(control_env) -> None:
    """Si la borne du .env baisse après coup, la valeur stockée suit."""
    import config
    from database import set_setting
    from integrations.uber_eats_settings import get_settings

    set_setting("uber_eats.max_order_price", "39.0")
    monkey_ceiling = 20.0
    config.UBER_EATS_MAX_ORDER_PRICE = monkey_ceiling
    try:
        assert get_settings().max_order_price == monkey_ceiling
    finally:
        config.UBER_EATS_MAX_ORDER_PRICE = 40.0


def test_dry_run_cannot_be_disabled_when_the_env_forces_it(control_env) -> None:
    """`.env` en simulation → l'interface ne peut pas en sortir."""
    import config
    from integrations.uber_eats_settings import get_settings, update_settings

    config.UBER_EATS_DRY_RUN = True
    update_settings({"dry_run": False})

    assert get_settings().dry_run is True


def test_dry_run_can_be_disabled_when_the_env_allows_it(control_env) -> None:
    import config
    from integrations.uber_eats_settings import get_settings, update_settings

    config.UBER_EATS_DRY_RUN = False
    try:
        update_settings({"dry_run": False})
        assert get_settings().dry_run is False
        update_settings({"dry_run": True})
        assert get_settings().dry_run is True
    finally:
        config.UBER_EATS_DRY_RUN = True


def test_a_switch_closed_in_env_cannot_be_opened_from_the_interface(control_env) -> None:
    import config
    from integrations.uber_eats_settings import get_settings, update_settings

    config.UBER_EATS_ENABLED = False
    try:
        update_settings({"enabled": True})
        assert get_settings().enabled is False
    finally:
        config.UBER_EATS_ENABLED = True


def test_unknown_and_malformed_settings_are_refused(control_env) -> None:
    from integrations.uber_eats_settings import FoodSettingsError, update_settings

    with pytest.raises(FoodSettingsError, match="inconnus"):
        update_settings({"max_order_price_please": 10})
    with pytest.raises(FoodSettingsError, match="booléen attendu"):
        update_settings({"enabled": "peut-être"})
    with pytest.raises(FoodSettingsError, match="nombre attendu"):
        update_settings({"max_order_price": "gratuit"})
    with pytest.raises(FoodSettingsError, match="Aucun réglage"):
        update_settings({})


def test_reset_returns_to_the_env_values(control_env) -> None:
    from integrations.uber_eats_settings import get_settings, reset_settings, update_settings

    update_settings({"max_order_price": 12})
    assert get_settings().max_order_price == 12.0

    reset_settings()
    assert get_settings().max_order_price == 40.0


@pytest.mark.asyncio
async def test_lowering_a_limit_blocks_a_cart_already_prepared(
    control_env, fake_playwright  # noqa: F811
) -> None:
    """Un plafond abaissé s'applique au panier en attente de confirmation."""
    import config
    from integrations.uber_eats import uber_eats
    from integrations.uber_eats_settings import update_settings

    fake_playwright["page"] = make_checkout_page("Total 30,00 €")
    config.UBER_EATS_DRY_RUN = False
    try:
        plan, _ = await uber_eats.prepare_order("Chez Pierre", [{"name": "Tacos"}])
        update_settings({"max_order_price": 10})
        outcome = await uber_eats.confirm_order(plan.plan_id)
    finally:
        config.UBER_EATS_DRY_RUN = True

    assert outcome.ok is False
    assert outcome.status == "blocked"
    assert "plafond" in (outcome.error or "").lower()


@pytest.mark.asyncio
async def test_a_cart_prepared_in_simulation_stays_simulated(
    control_env, fake_playwright  # noqa: F811
) -> None:
    """Quitter la simulation ne transforme pas rétroactivement un panier en dépense.

    Le mode est figé dans le plan au moment où l'utilisateur voit le total :
    un changement de réglage entre l'annonce et l'accord ne peut pas convertir
    un essai en paiement.
    """
    import config
    from integrations.uber_eats import uber_eats

    fake_playwright["page"] = make_checkout_page("Total 24,90 €")
    plan, _ = await uber_eats.prepare_order("Chez Pierre", [{"name": "Tacos"}])
    assert plan.dry_run is True

    config.UBER_EATS_DRY_RUN = False
    try:
        outcome = await uber_eats.confirm_order(plan.plan_id)
    finally:
        config.UBER_EATS_DRY_RUN = True

    assert outcome.status == "simulated"


# ── Panier libre en deux passes ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_manual_cart_reads_the_total_without_spending(
    control_env, fake_playwright  # noqa: F811
) -> None:
    from api.food_control import prepare_manual_order
    from database import get_food_orders

    fake_playwright["page"] = make_checkout_page("Total 24,90 €")

    plan = await prepare_manual_order("Chez Pierre", [{"name": "Tacos", "quantity": 1}])

    assert plan["total_price"] == 24.90
    assert plan["needs_confirmation"] is True
    assert plan["plan_id"]
    assert not [order for order in get_food_orders(limit=5) if order["status"] == "placed"]


@pytest.mark.asyncio
async def test_manual_cart_confirmation_consumes_the_plan_once(
    control_env, fake_playwright  # noqa: F811
) -> None:
    from api.food_control import FoodControlError, confirm_manual_order, prepare_manual_order

    fake_playwright["page"] = make_checkout_page("Total 24,90 €")
    plan = await prepare_manual_order("Chez Pierre", [{"name": "Tacos"}])

    outcome = await confirm_manual_order(plan["plan_id"])
    assert outcome["ok"] is True
    # Le second appel ne peut pas commander une seconde fois.
    with pytest.raises(FoodControlError) as excinfo:
        await confirm_manual_order(plan["plan_id"])
    assert excinfo.value.status_code == 409


@pytest.mark.asyncio
async def test_manual_cart_response_never_leaks_the_consumed_plan_id(
    control_env, fake_playwright  # noqa: F811
) -> None:
    from api.food_control import confirm_manual_order, prepare_manual_order

    fake_playwright["page"] = make_checkout_page("Total 24,90 €")
    plan = await prepare_manual_order("Chez Pierre", [{"name": "Tacos"}])

    outcome = await confirm_manual_order(plan["plan_id"])

    assert "plan_id" not in outcome


@pytest.mark.asyncio
async def test_cancelling_a_cart_prevents_its_confirmation(
    control_env, fake_playwright  # noqa: F811
) -> None:
    from api.food_control import (
        FoodControlError,
        cancel_manual_order,
        confirm_manual_order,
        prepare_manual_order,
    )

    fake_playwright["page"] = make_checkout_page("Total 24,90 €")
    plan = await prepare_manual_order("Chez Pierre", [{"name": "Tacos"}])

    assert cancel_manual_order(plan["plan_id"]) == {"ok": True, "revoked": True}
    with pytest.raises(FoodControlError):
        await confirm_manual_order(plan["plan_id"])
    # Révoquer deux fois reste sans effet et sans erreur.
    assert cancel_manual_order(plan["plan_id"]) == {"ok": True, "revoked": False}


@pytest.mark.asyncio
async def test_peeking_a_cart_does_not_consume_it(
    control_env, fake_playwright  # noqa: F811
) -> None:
    from api.food_control import confirm_manual_order, peek_manual_order, prepare_manual_order

    fake_playwright["page"] = make_checkout_page("Total 24,90 €")
    plan = await prepare_manual_order("Chez Pierre", [{"name": "Tacos"}])

    seen = peek_manual_order(plan["plan_id"])
    assert seen["total_price"] == 24.90
    assert (await confirm_manual_order(plan["plan_id"]))["ok"] is True


@pytest.mark.asyncio
async def test_unknown_cart_is_a_not_found(control_env) -> None:
    from api.food_control import FoodControlError, peek_manual_order

    with pytest.raises(FoodControlError) as excinfo:
        peek_manual_order("jamais-vu")

    assert excinfo.value.status_code == 404


@pytest.mark.asyncio
async def test_manual_cart_reports_an_unavailable_integration_as_503(
    control_env, fake_playwright  # noqa: F811
) -> None:
    import config
    from api.food_control import FoodControlError, prepare_manual_order

    config.UBER_EATS_ENABLED = False
    try:
        with pytest.raises(FoodControlError) as excinfo:
            await prepare_manual_order("Chez Pierre", [{"name": "Tacos"}])
    finally:
        config.UBER_EATS_ENABLED = True

    assert excinfo.value.status_code == 503


@pytest.mark.asyncio
async def test_manual_cart_rejects_a_malformed_request(control_env) -> None:
    from api.food_control import FoodControlError, prepare_manual_order

    with pytest.raises(FoodControlError) as excinfo:
        await prepare_manual_order("", [])

    assert excinfo.value.status_code == 400


# ── Diagnostic ──────────────────────────────────────────────────────────────


def test_selectors_report_describes_coverage(control_env) -> None:
    from api.food_control import selectors_report

    report = selectors_report()

    assert report["ok"] is True
    assert report["verified"] is True
    assert report["missing_required"] == []
    assert report["roles"]["cart_total"] >= 1


def test_selectors_report_explains_an_invalid_file(control_env) -> None:
    import config
    from api.food_control import selectors_report
    from integrations.uber_eats_selectors import clear_selector_cache

    broken = Path(config.UBER_EATS_SELECTORS_FILE)
    broken.write_text("{ pas du json", encoding="utf-8")
    clear_selector_cache()

    report = selectors_report()

    assert report["ok"] is False
    assert "JSON invalide" in report["error"]
    assert report["missing_required"]


def test_reloading_selectors_picks_up_an_edited_file(control_env) -> None:
    from api.food_control import reload_selectors

    write_selector_file(Path(control_env.selectors), verified=False)

    assert reload_selectors()["verified"] is False


def test_session_report_never_exposes_the_session_content(control_env) -> None:
    from api.food_control import session_report

    report = session_report()

    assert report["exists"] is True
    assert report["readable"] is True
    assert "cookies" not in report
    assert report["age_hours"] is not None


@pytest.mark.asyncio
async def test_probe_reports_a_valid_session(control_env, fake_playwright) -> None:  # noqa: F811
    from api.food_control import probe_session

    fake_playwright["page"] = make_checkout_page()

    assert (await probe_session())["ok"] is True


@pytest.mark.asyncio
async def test_probe_reports_an_expired_session(control_env, fake_playwright) -> None:  # noqa: F811
    from api.food_control import probe_session

    # Écran de connexion visible : la session n'est plus authentifiée.
    fake_playwright["page"] = FakePage(visible={"login-form"})

    result = await probe_session()

    assert result["ok"] is False
    assert "expirée" in result["message"]


# ── Capture pilotée ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_capture_refuses_an_unknown_mode(control_env) -> None:
    from api.food_control import FoodControlError, start_capture

    with pytest.raises(FoodControlError, match="Mode de capture inconnu"):
        await start_capture("codegen-plus")


@pytest.mark.asyncio
async def test_only_one_capture_runs_at_a_time(
    control_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    from api import food_control

    class _NeverEndingProcess:
        pid = 4242
        returncode = None

        async def communicate(self) -> tuple[bytes, bytes]:
            import asyncio

            await asyncio.sleep(30)
            return b"", b""

        def kill(self) -> None:
            self.returncode = -9

    async def _spawn(*_args: Any, **_kwargs: Any) -> _NeverEndingProcess:
        return _NeverEndingProcess()

    monkeypatch.setattr("asyncio.create_subprocess_exec", _spawn)

    first = await food_control.start_capture()
    assert first["running"] is True

    with pytest.raises(food_control.FoodControlError) as excinfo:
        await food_control.start_capture()
    assert excinfo.value.status_code == 409

    await food_control.stop_capture()
    food_control.reset_capture_for_tests()


@pytest.mark.asyncio
async def test_capture_failure_to_spawn_is_reported(
    control_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    from api import food_control

    async def _boom(*_args: Any, **_kwargs: Any) -> None:
        raise OSError("binaire introuvable")

    monkeypatch.setattr("asyncio.create_subprocess_exec", _boom)

    with pytest.raises(food_control.FoodControlError) as excinfo:
        await food_control.start_capture()

    assert excinfo.value.status_code == 500


# ── Frontière de sécurité ───────────────────────────────────────────────────


def test_control_routes_stay_behind_the_session_gate() -> None:
    from api.middleware import _bypasses_session_gate

    for method, path in (
        ("GET", "/api/food/settings"),
        ("PATCH", "/api/food/settings"),
        ("POST", "/api/food/settings/reset"),
        ("POST", "/api/food/cart/prepare"),
        ("POST", "/api/food/cart/abc/confirm"),
        ("DELETE", "/api/food/cart/abc"),
        ("POST", "/api/food/session/capture"),
        ("POST", "/api/food/session/probe"),
        ("POST", "/api/food/selectors/reload"),
    ):
        assert _bypasses_session_gate(method, path) is False


def test_settings_expose_their_ceilings_for_the_interface(control_env) -> None:
    from integrations.uber_eats_settings import get_ceilings

    ceilings = get_ceilings()

    assert ceilings["max_order_price"] == 40.0
    assert ceilings["dry_run_forced"] is True
    assert set(ceilings) >= {"enabled", "max_daily_spend", "max_items"}


def test_verified_flag_is_not_writable_from_the_control_layer() -> None:
    """Marquer les sélecteurs vérifiés reste un geste hors interface.

    C'est le dernier garde-fou avant le clic de paiement : l'ouvrir à une
    requête web reviendrait à pouvoir tout débloquer depuis le réseau.
    """
    source = (PROJECT_ROOT / "api" / "food_control.py").read_text(encoding="utf-8")
    router = (PROJECT_ROOT / "api" / "router_food.py").read_text(encoding="utf-8")

    for forbidden in ('"verified": true', "verified=True", "set_verified"):
        assert forbidden not in source
        assert forbidden not in router
