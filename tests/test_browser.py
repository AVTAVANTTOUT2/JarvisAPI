"""Navigateur agentique générique : lire le Web, jamais muter ni payer."""

from __future__ import annotations

from dataclasses import replace
from urllib.parse import urlencode

import pytest

from core.outbound_security import OutboundURLRejected
from integrations.browser import (
    BrowserElement,
    apply,
    clear_browser_receipt,
    close_session,
    get_browser_snapshot_artifact,
    is_final_commit,
    set_driver_factory,
    validate_browser_target,
)
from integrations.browser_runtime import (
    mark_browser_receipt_approved,
    record_browser_receipt,
)
from integrations.browser_driver import LiveBrowserElement
from integrations.browser_security import BrowserSecurityError
from integrations.opencode.register import create_runtime
from jarvis.agentic.models import AgenticRequestCategory
from jarvis.agentic.profiles import CAPABILITY_PROFILES, select_capability_profile


def _element(
    name: str,
    index: int,
    *,
    tag: str,
    role: str,
    handle: object | None = None,
    **values: object,
) -> BrowserElement:
    return BrowserElement(
        "",
        role,
        name,
        index,
        tag=tag,
        handle=handle or object(),
        fingerprint=str(values.pop("fingerprint", f"fp-{index}-{name}")),
        **values,
    )


class FakeDriver:
    def __init__(self) -> None:
        self.url = ""
        self.title = "Hotels"
        self.text = "Hotel Casa 120 EUR"
        self.opened: list[str] = []
        self.searches: list[str] = []
        self.closed = False
        self.values: dict[object, str] = {}
        self.elements = [
            _element(
                "Destination",
                0,
                tag="input",
                role="searchbox",
                input_type="search",
                form_action="https://hotels.example/search",
                form_method="get",
                field_name="q",
            ),
            _element(
                "Results",
                1,
                tag="a",
                role="link",
                href="https://hotels.example/results",
            ),
            _element(
                "Book now",
                2,
                tag="a",
                role="link",
                href="https://hotels.example/checkout",
            ),
        ]

    async def start(self, **_kwargs: object) -> None:
        return None

    async def open(self, url: str) -> None:
        self.url = url
        self.opened.append(url)

    async def observe(self) -> tuple[str, str, str, list[BrowserElement]]:
        return self.url, self.title, self.text, list(self.elements)

    async def inspect(self, element: BrowserElement) -> LiveBrowserElement:
        current = next(
            (item for item in self.elements if item.handle is element.handle), None
        )
        if current is None or current.fingerprint != element.fingerprint:
            raise BrowserSecurityError("stale_ref", "Référence DOM périmée")
        return LiveBrowserElement(
            replace(
                current,
                ref=element.ref,
                generation=element.generation,
                snapshot_id=element.snapshot_id,
                page_url=element.page_url,
            ),
            self.values.get(element.handle, ""),
        )

    async def submit_search(self, live: LiveBrowserElement, text: str) -> None:
        target = (
            f"{live.element.form_action}?"
            f"{urlencode({live.element.field_name: text})}"
        )
        self.searches.append(target)
        await self.open(target)

    async def close(self) -> None:
        self.closed = True


@pytest.fixture
def fake_browser(monkeypatch: pytest.MonkeyPatch) -> FakeDriver:
    driver = FakeDriver()
    monkeypatch.setattr("config.BROWSER_ENABLED", True)
    set_driver_factory(lambda: driver, target_validator=lambda url: str(url))
    yield driver
    for run_id in (
        "run-browser",
        "run-disabled",
        "run-invalid",
        "run-sensitive",
        "run-stale",
    ):
        close_session(run_id, clear_receipt=True)
    set_driver_factory(None, target_validator=None)


def _named(snapshot: dict[str, object], name: str) -> dict[str, str]:
    elements = snapshot["elements"]
    assert isinstance(elements, list)
    return next(item for item in elements if item["name"] == name)


@pytest.mark.parametrize(
    ("name", "url", "blocked"),
    [
        ("Search", "https://hotels.example/search", False),
        ("Book now", "https://hotels.example/search", True),
        ("Pay now", "https://hotels.example/room", True),
        ("Confirm booking", "https://hotels.example/room", True),
        ("Submit", "https://hotels.example/checkout", True),
        ("Delete account", "https://hotels.example/settings", True),
        ("Hotel Casa", "https://hotels.example/checkout", True),
    ],
)
def test_final_commit_blocks_payment_and_booking(
    name: str, url: str, blocked: bool
) -> None:
    assert is_final_commit(name, url) is blocked


def test_browser_target_rejects_private_credentials_and_loopback() -> None:
    with pytest.raises(OutboundURLRejected):
        validate_browser_target(
            "https://user:pass@hotels.example/",
            resolver=lambda *_a, **_k: [(0, 0, 0, "", ("8.8.8.8", 443))],
        )
    with pytest.raises(OutboundURLRejected):
        validate_browser_target("http://hotels.example/")
    with pytest.raises(OutboundURLRejected):
        validate_browser_target("http://127.0.0.1:9/fixture")


def test_open_root_search_and_arbitrary_actions_are_unavailable(
    fake_browser: FakeDriver,
) -> None:
    opened = apply(
        "run-browser", {"op": "open", "url": "https://hotels.example/"}
    )
    assert opened["ok"] is True
    assert opened["started"] is True
    assert opened["url"] == "https://hotels.example/"
    assert fake_browser.opened == ["https://hotels.example/"]

    search = _named(opened, "Destination")
    typed = apply(
        "run-browser",
        {
            "op": "type",
            "ref": search["ref"],
            "snapshot_id": opened["snapshot_id"],
            "element_name": search["name"],
            "page_origin": opened["url"],
            "text": "Barcelona",
        },
    )
    assert typed["ok"] is False
    assert typed["error"] == "op_invalid"

    clicked = apply(
        "run-browser",
        {
            "op": "click",
            "ref": _named(opened, "Results")["ref"],
            "snapshot_id": opened["snapshot_id"],
            "element_name": "Results",
            "page_origin": opened["url"],
        },
    )
    assert clicked["ok"] is False
    assert clicked["error"] == "op_invalid"

    submitted = apply(
        "run-browser",
        {
            "op": "search",
            "ref": search["ref"],
            "snapshot_id": opened["snapshot_id"],
            "element_name": search["name"],
            "page_origin": opened["url"],
            "target_origin": search["target_origin"],
            "target_path": search["target_path"],
            "target_sha256": search["target_sha256"],
            "text": "Barcelona",
        },
    )
    assert submitted["ok"] is True
    assert fake_browser.searches == [
        "https://hotels.example/search?q=Barcelona"
    ]


@pytest.mark.parametrize("same_handle", [False, True])
def test_ref_is_rejected_when_dom_node_is_replaced_or_reclassified(
    fake_browser: FakeDriver, same_handle: bool
) -> None:
    opened = apply(
        "run-stale", {"op": "open", "url": "https://hotels.example/"}
    )
    search = _named(opened, "Destination")
    previous = fake_browser.elements[0]
    fake_browser.elements[0] = _element(
        "Pay now",
        0,
        tag="input",
        role="searchbox",
        handle=previous.handle if same_handle else object(),
        input_type="search",
        form_action="https://hotels.example/checkout",
        form_method="get",
        field_name="q",
        fingerprint="changed",
    )

    result = apply(
        "run-stale",
        {
            "op": "search",
            "ref": search["ref"],
            "snapshot_id": opened["snapshot_id"],
            "element_name": search["name"],
            "page_origin": opened["url"],
            "target_origin": search["target_origin"],
            "target_path": search["target_path"],
            "target_sha256": search["target_sha256"],
            "text": "Paris",
        },
    )

    assert result["error"] == "stale_ref"
    assert fake_browser.searches == []


@pytest.mark.parametrize(
    "element",
    [
        _element(
            "Password",
            0,
            tag="input",
            role="textbox",
            input_type="password",
        ),
        _element(
            "Verification code",
            0,
            tag="input",
            role="textbox",
            input_type="text",
            autocomplete="one-time-code",
        ),
        _element(
            "Card",
            0,
            tag="input",
            role="textbox",
            input_type="text",
            autocomplete="cc-number",
        ),
        _element(
            "Attachment",
            0,
            tag="input",
            role="textbox",
            input_type="file",
        ),
    ],
)
def test_sensitive_and_file_fields_are_structurally_blocked(
    fake_browser: FakeDriver, element: BrowserElement
) -> None:
    fake_browser.elements = [element]
    opened = apply(
        "run-sensitive", {"op": "open", "url": "https://hotels.example/"}
    )
    target = opened["elements"][0]
    result = apply(
        "run-sensitive",
        {
            "op": "search",
            "ref": target["ref"],
            "snapshot_id": opened["snapshot_id"],
            "element_name": target["name"],
            "page_origin": opened["url"],
            "text": "secret",
        },
    )

    assert result["ok"] is False
    assert result["blocked"] == "sensitive_field"
    assert fake_browser.searches == []


def test_search_is_one_atomic_classified_get(fake_browser: FakeDriver) -> None:
    opened = apply(
        "run-browser", {"op": "open", "url": "https://hotels.example/"}
    )
    search = _named(opened, "Destination")
    submitted = apply(
        "run-browser",
        {
            "op": "search",
            "ref": search["ref"],
            "snapshot_id": opened["snapshot_id"],
            "element_name": search["name"],
            "page_origin": opened["url"],
            "target_origin": search["target_origin"],
            "target_path": search["target_path"],
            "target_sha256": search["target_sha256"],
            "text": "Paris",
        },
    )

    assert submitted["ok"] is True
    assert fake_browser.searches == ["https://hotels.example/search?q=Paris"]


def test_search_refuses_checkout_even_when_label_is_neutral(
    fake_browser: FakeDriver,
) -> None:
    fake_browser.elements = [
        _element(
            "Continue",
            0,
            tag="input",
            role="searchbox",
            input_type="search",
            form_action="https://hotels.example/checkout",
            form_method="get",
            field_name="q",
        )
    ]
    opened = apply(
        "run-browser", {"op": "open", "url": "https://hotels.example/"}
    )
    target = opened["elements"][0]
    result = apply(
        "run-browser",
        {
            "op": "search",
            "ref": target["ref"],
            "snapshot_id": opened["snapshot_id"],
            "element_name": target["name"],
            "page_origin": opened["url"],
            "target_origin": target["target_origin"],
            "target_path": target["target_path"],
            "target_sha256": target["target_sha256"],
            "text": "Paris",
        },
    )

    assert result["blocked"] == "external_effect_blocked"
    assert fake_browser.searches == []


def test_browser_receipt_keeps_only_redacted_title_and_no_page_text_path_or_query(
    fake_browser: FakeDriver,
) -> None:
    fake_browser.title = "Hotels for alice@example.com"
    result = apply(
        "run-browser",
        {"op": "open", "url": "https://hotels.example/"},
    )
    artifact = get_browser_snapshot_artifact("run-browser")

    assert result["ok"] is True
    assert artifact is not None
    assert artifact.metadata["url"] == "https://hotels.example/"
    assert "text" not in artifact.metadata
    assert artifact.metadata["title"] == "Hotels for [EMAIL_1]"
    assert len(artifact.metadata["content_sha256"]) == 64
    assert artifact.reference.startswith("jarvis://browser/run-browser/")
    clear_browser_receipt("run-browser")


def test_approval_cannot_bind_to_a_snapshot_replaced_by_a_concurrent_action() -> None:
    record_browser_receipt(
        "receipt-race",
        snapshot_id="a" * 32,
        operation="see",
        url="https://public.example/",
        title="First",
        text="first",
        policy_result="allowed",
    )
    record_browser_receipt(
        "receipt-race",
        snapshot_id="b" * 32,
        operation="see",
        url="https://public.example/",
        title="Second",
        text="second",
        policy_result="allowed",
    )
    try:
        with pytest.raises(RuntimeError, match="remplacé"):
            mark_browser_receipt_approved(
                "receipt-race", "c" * 64, snapshot_id="a" * 32
            )
        artifact = get_browser_snapshot_artifact("receipt-race")
        assert artifact is not None
        assert artifact.metadata["snapshot_id"] == "b" * 32
        assert artifact.metadata["approval_verified"] is False
    finally:
        clear_browser_receipt("receipt-race")


def test_open_refuses_opaque_path_and_query_before_starting_driver(
    fake_browser: FakeDriver,
) -> None:
    result = apply(
        "run-invalid",
        {"op": "open", "url": "https://hotels.example/a9f3c2?token=secret"},
    )

    assert result["ok"] is False
    assert result["error"] == "entrypoint_blocked"
    assert result["started"] is True
    assert fake_browser.opened == []


def test_invalid_operation_never_starts_a_driver(fake_browser: FakeDriver) -> None:
    result = apply("run-invalid", {"op": "execute-javascript"})
    assert result["error"] == "op_invalid"
    assert fake_browser.opened == []


def test_disabled_browser_does_not_start(
    fake_browser: FakeDriver, monkeypatch: pytest.MonkeyPatch
) -> None:
    import config

    monkeypatch.setattr(config, "BROWSER_ENABLED", False)
    result = apply("run-disabled", {"op": "see"})
    assert result["ok"] is False
    assert result["error"] == "browser_disabled"
    assert result["started"] is False


def test_opencode_declares_browser_scopes_of_the_hotel_profile() -> None:
    runtime = create_runtime()
    declared = {item.scope for item in runtime.capabilities} | {
        item.name for item in runtime.capabilities
    }
    profile = CAPABILITY_PROFILES["browser"]
    assert set(profile.default_permissions) - declared == set()
    assert profile.approval_permissions == ("browser:control",)
    assert "browser:download" not in profile.permissions


@pytest.mark.parametrize(
    "request_text",
    [
        "réserve-moi une table au restaurant",
        "book me a restaurant in Paris",
        "trouve-moi un billet de concert",
        "book tickets for Saturday",
    ],
)
def test_daily_life_requests_select_browser_profile(request_text: str) -> None:
    profile = select_capability_profile(
        request_text, AgenticRequestCategory.AGENTIC_EXTERNAL_EFFECT
    )
    assert profile.profile_id == "browser"
    assert "browser:control" in profile.default_permissions
    assert "financial:act" not in profile.permissions


def test_support_ticket_request_does_not_select_browser() -> None:
    profile = select_capability_profile(
        "envoie un ticket au support",
        AgenticRequestCategory.AGENTIC_EXTERNAL_EFFECT,
    )
    assert profile.profile_id != "browser"
