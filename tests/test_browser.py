"""Navigateur agentique générique : voir, agir, jamais payer."""

from __future__ import annotations

import pytest

from core.outbound_security import OutboundURLRejected
from integrations.browser import (
    BrowserElement,
    apply,
    close_session,
    is_final_commit,
    set_driver_factory,
    validate_browser_target,
)
from jarvis.agentic.models import AgenticRequestCategory
from jarvis.agentic.profiles import CAPABILITY_PROFILES, select_capability_profile
from integrations.opencode.register import create_runtime


class FakeDriver:
    def __init__(self) -> None:
        self.url = ""
        self.title = "Hotels"
        self.text = "Hotel Casa 120 EUR"
        self.opened: list[str] = []
        self.clicks: list[str] = []
        self.filled: list[tuple[str, str]] = []
        self.closed = False
        self.elements = [
            BrowserElement("e1", "textbox", "Destination", 0),
            BrowserElement("e2", "button", "Search", 1),
            BrowserElement("e3", "button", "Book now", 2),
        ]

    async def open(self, url: str) -> None:
        self.url = url
        self.opened.append(url)

    async def observe(self) -> tuple[str, str, str, list[BrowserElement]]:
        return self.url, self.title, self.text, list(self.elements)

    async def click(self, element: BrowserElement) -> None:
        self.clicks.append(element.ref)

    async def fill(self, element: BrowserElement, text: str) -> None:
        self.filled.append((element.ref, text))

    async def press(self, element: BrowserElement, key: str) -> None:
        self.filled.append((element.ref, key))

    async def close(self) -> None:
        self.closed = True


@pytest.fixture
def fake_browser(monkeypatch: pytest.MonkeyPatch) -> FakeDriver:
    driver = FakeDriver()
    set_driver_factory(lambda: driver)
    monkeypatch.setattr(
        "integrations.browser.validate_browser_target",
        lambda url, **_kwargs: url,
    )
    yield driver
    set_driver_factory(None)
    close_session("run-browser")
    close_session("run-1")


@pytest.mark.parametrize(
    ("name", "url", "blocked"),
    [
        ("Search", "https://hotels.example/search", False),
        ("Book now", "https://hotels.example/search", True),
        ("Pay now", "https://hotels.example/room", True),
        ("Confirm booking", "https://hotels.example/room", True),
        ("Submit", "https://hotels.example/checkout", True),
        ("Delete account", "https://hotels.example/settings", True),
        ("Hotel Casa", "https://hotels.example/checkout", False),
    ],
)
def test_final_commit_blocks_payment_and_booking(
    name: str, url: str, blocked: bool
) -> None:
    assert is_final_commit(name, url) is blocked


def test_browser_target_rejects_private_and_credentials() -> None:
    with pytest.raises(OutboundURLRejected):
        validate_browser_target(
            "https://user:pass@hotels.example/",
            resolver=lambda *_a, **_k: [
                (0, 0, 0, "", ("8.8.8.8", 443)),
            ],
        )
    with pytest.raises(OutboundURLRejected):
        validate_browser_target("http://hotels.example/")


def test_loopback_http_is_opt_in() -> None:
    assert (
        validate_browser_target("http://127.0.0.1:9/fixture", allow_loopback=True)
        == "http://127.0.0.1:9/fixture"
    )
    with pytest.raises(OutboundURLRejected):
        validate_browser_target("http://127.0.0.1:9/fixture", allow_loopback=False)


def test_open_see_type_click_then_block_booking(fake_browser: FakeDriver) -> None:
    opened = apply("run-browser", {"op": "open", "url": "https://hotels.example/search"})
    assert opened["ok"] is True
    assert opened["started"] is True
    assert opened["url"] == "https://hotels.example/search"
    assert {item["ref"] for item in opened["elements"]} == {"e1", "e2", "e3"}
    assert fake_browser.opened == ["https://hotels.example/search"]

    typed = apply("run-browser", {"op": "type", "ref": "e1", "text": "Barcelona"})
    assert typed["ok"] is True
    assert fake_browser.filled == [("e1", "Barcelona")]

    clicked = apply("run-browser", {"op": "click", "ref": "e2"})
    assert clicked["ok"] is True
    assert fake_browser.clicks == ["e2"]

    blocked = apply("run-browser", {"op": "click", "ref": "e3"})
    assert blocked["ok"] is False
    assert blocked["blocked"] == "payment_or_booking"
    assert blocked["needs_confirmation"] is True
    assert fake_browser.clicks == ["e2"]


def test_disabled_browser_does_not_start(monkeypatch: pytest.MonkeyPatch) -> None:
    import config

    monkeypatch.setattr(config, "BROWSER_ENABLED", False)
    result = apply("run-1", {"op": "see"})
    assert result["ok"] is False
    assert result["error"] == "browser_disabled"
    assert result["started"] is False


def test_opencode_declares_browser_scopes_of_the_hotel_profile() -> None:
    runtime = create_runtime()
    declared = {item.scope for item in runtime.capabilities} | {
        item.name for item in runtime.capabilities
    }
    missing = set(CAPABILITY_PROFILES["browser"].default_permissions) - declared
    assert missing == set()


@pytest.mark.parametrize(
    "request_text",
    [
        "réserve-moi une table au restaurant",
        "book me a restaurant in Paris",
        "trouve-moi un billet de concert",
    ],
)
def test_daily_life_requests_select_browser_profile(request_text: str) -> None:
    profile = select_capability_profile(
        request_text, AgenticRequestCategory.AGENTIC_EXTERNAL_EFFECT
    )
    assert profile.profile_id == "browser"
    assert "browser:control" in profile.default_permissions
    assert "financial:act" not in profile.permissions
