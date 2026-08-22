"""Navigateur agentique générique — yeux (voir) et mains (cliquer / taper).

Playwright reste facultatif : JARVIS démarre sans. Uber Eats garde son
parcours dédié ; ici une demande ouverte peut ouvrir un site HTTPS public,
lire la page, puis agir. Un paiement ou une réservation finale est toujours
refusé — y compris si le modèle envoie ``confirm: true``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, field
import logging
import re
import threading
from typing import Any, Protocol
from urllib.parse import urlsplit

from core.outbound_security import OutboundURLRejected, validate_open_world_https_url
from integrations.playwright_runtime import (
    PlaywrightUnavailable,
    import_playwright,
)

logger = logging.getLogger("jarvis.browser")

BROWSER_TOOL_NAME = "jarvis_browser"
MAX_SESSIONS = 2
MAX_ELEMENTS = 60
MAX_TEXT_CHARS = 4_000
MAX_NAME_CHARS = 80
MAX_TYPE_CHARS = 500
ALLOWED_KEYS = frozenset({"Enter", "Tab", "Escape"})
_INTERACTIVE = (
    "a, button, input, select, textarea, "
    "[role='button'], [role='link'], [role='textbox'], [role='combobox']"
)
_COMMIT_RE = re.compile(
    r"(?i)\b("
    r"pay(?:ment|er)?(?:\s+now)?|acheter|checkout|billing|"
    r"place\s+order|complete\s+(?:booking|purchase|order)|"
    r"book\s+now|r[ée]server|confirm(?:er)?\s+"
    r"(?:booking|reservation|purchase|and\s+pay)"
    r")\b"
)
_DESTRUCTIVE_RE = re.compile(
    r"(?i)\b("
    r"delete\s+account|supprimer\s+(?:le\s+)?compte|"
    r"wipe|factory\s+reset|remove\s+permanently|"
    r"supprime(?:r)?\s+d[ée]finitivement"
    r")\b"
)
_CHECKOUT_PATH_RE = re.compile(
    r"(?i)/(checkout|payment|pay|billing|complete-booking)(/|$|\?)"
)
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "[::1]"})

BROWSER_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "op": {
            "type": "string",
            "enum": ["open", "see", "click", "type", "press", "close"],
        },
        "url": {"type": ["string", "null"], "maxLength": 2000},
        "ref": {"type": ["string", "null"], "maxLength": 16},
        "text": {"type": ["string", "null"], "maxLength": 500},
        "key": {"type": ["string", "null"], "enum": ["Enter", "Tab", "Escape", None]},
    },
    "required": ["op"],
    "additionalProperties": False,
}


class BrowserError(RuntimeError):
    """Erreur bornée du navigateur agentique."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class BrowserElement:
    ref: str
    role: str
    name: str
    index: int


class BrowserDriver(Protocol):
    url: str

    async def open(self, url: str) -> None: ...
    async def observe(self) -> tuple[str, str, str, list[BrowserElement]]: ...
    async def click(self, element: BrowserElement) -> None: ...
    async def fill(self, element: BrowserElement, text: str) -> None: ...
    async def press(self, element: BrowserElement, key: str) -> None: ...
    async def close(self) -> None: ...


@dataclass
class _Session:
    run_id: str
    driver: BrowserDriver
    elements: dict[str, BrowserElement] = field(default_factory=dict)
    created_at: float = field(default_factory=lambda: 0.0)


_SESSIONS: dict[str, _Session] = {}
_SESSIONS_LOCK = threading.Lock()
_LOOP: asyncio.AbstractEventLoop | None = None
_LOOP_THREAD: threading.Thread | None = None
_LOOP_LOCK = threading.Lock()
_DRIVER_FACTORY: Any = None


def _now() -> float:
    import time

    return time.monotonic()


def set_driver_factory(factory: Any | None) -> None:
    """Hook de test : injecte un driver sans Playwright ni réseau."""

    global _DRIVER_FACTORY
    _DRIVER_FACTORY = factory


def _settings() -> tuple[bool, bool, int, int, bool]:
    import config

    enabled = bool(getattr(config, "BROWSER_ENABLED", True))
    headless = bool(getattr(config, "BROWSER_HEADLESS", True))
    nav_ms = int(getattr(config, "BROWSER_NAV_TIMEOUT_MS", 20_000))
    act_ms = int(getattr(config, "BROWSER_ACTION_TIMEOUT_MS", 8_000))
    loopback = bool(getattr(config, "BROWSER_ALLOW_LOOPBACK", False))
    return enabled, headless, nav_ms, act_ms, loopback


def validate_browser_target(
    url: str,
    *,
    allow_loopback: bool = False,
    resolver: Any | None = None,
) -> str:
    """HTTPS public, ou HTTP(S) loopback si le test l'autorise."""

    candidate = str(url or "").strip()
    parsed = urlsplit(candidate)
    host = (parsed.hostname or "").lower().rstrip(".")
    if allow_loopback and host in _LOOPBACK_HOSTS and parsed.scheme in {"http", "https"}:
        if parsed.username is not None or parsed.password is not None:
            raise OutboundURLRejected("https_required", "identifiants interdits dans l'URL")
        return candidate
    return validate_open_world_https_url(
        candidate, resolver=resolver, allow_loopback=allow_loopback
    )


def is_final_commit(name: str, url: str) -> bool:
    """True si l'acte conclurait un paiement, une réservation ou une destruction."""

    label = " ".join(str(name or "").split())
    if _COMMIT_RE.search(label) or _DESTRUCTIVE_RE.search(label):
        return True
    path = urlsplit(url).path or ""
    if _CHECKOUT_PATH_RE.search(path) and re.search(
        r"(?i)\b(submit|confirm|envoyer|valider|pay|acheter)\b", label
    ):
        return True
    return False


def _ensure_loop() -> asyncio.AbstractEventLoop:
    global _LOOP, _LOOP_THREAD
    with _LOOP_LOCK:
        if _LOOP is not None and _LOOP.is_running():
            return _LOOP
        loop = asyncio.new_event_loop()

        def _run() -> None:
            asyncio.set_event_loop(loop)
            loop.run_forever()

        thread = threading.Thread(target=_run, name="jarvis-browser", daemon=True)
        thread.start()
        _LOOP = loop
        _LOOP_THREAD = thread
        return loop


def _run(coro: Any, timeout_s: float = 45.0) -> Any:
    future = asyncio.run_coroutine_threadsafe(coro, _ensure_loop())
    return future.result(timeout_s)


class PlaywrightDriver:
    """Backend Playwright : un contexte Chromium par session de run."""

    def __init__(self) -> None:
        self.url = ""
        self._playwright: Any = None
        self._browser: Any = None
        self._context: Any = None
        self._page: Any = None

    async def start(self, *, headless: bool, nav_ms: int, act_ms: int) -> None:
        api = import_playwright()
        self._playwright = await api.async_playwright().start()
        self._browser = await self._playwright.chromium.launch(headless=headless)
        self._context = await self._browser.new_context()
        self._page = await self._context.new_page()
        self._page.set_default_timeout(act_ms)
        self._page.set_default_navigation_timeout(nav_ms)

    async def open(self, url: str) -> None:
        await self._page.goto(url, wait_until="domcontentloaded")
        self.url = str(self._page.url or url)

    async def observe(self) -> tuple[str, str, str, list[BrowserElement]]:
        page = self._page
        self.url = str(page.url or self.url)
        title = str(await page.title() or "")
        try:
            text = str(await page.inner_text("body") or "")
        except Exception:  # noqa: BLE001 — page vide ou non HTML
            text = ""
        locator = page.locator(_INTERACTIVE)
        count = min(int(await locator.count()), MAX_ELEMENTS)
        elements: list[BrowserElement] = []
        for index in range(count):
            item = locator.nth(index)
            role = (
                str(await item.get_attribute("role") or "").strip()
                or str(await item.evaluate("el => el.tagName") or "").strip().lower()
            )
            name = (
                str(await item.get_attribute("aria-label") or "").strip()
                or str(await item.get_attribute("placeholder") or "").strip()
                or " ".join(str(await item.inner_text() or "").split())
            )
            elements.append(
                BrowserElement(
                    ref=f"e{index + 1}",
                    role=role[:40],
                    name=name[:MAX_NAME_CHARS],
                    index=index,
                )
            )
        return self.url, title[:200], text[:MAX_TEXT_CHARS], elements

    async def click(self, element: BrowserElement) -> None:
        await self._page.locator(_INTERACTIVE).nth(element.index).click()

    async def fill(self, element: BrowserElement, text: str) -> None:
        await self._page.locator(_INTERACTIVE).nth(element.index).fill(text)

    async def press(self, element: BrowserElement, key: str) -> None:
        await self._page.locator(_INTERACTIVE).nth(element.index).press(key)

    async def close(self) -> None:
        for handle in (self._page, self._context, self._browser):
            if handle is None:
                continue
            closer = getattr(handle, "close", None)
            if closer is not None:
                await closer()
        if self._playwright is not None:
            await self._playwright.stop()
        self._page = self._context = self._browser = self._playwright = None
        self.url = ""


async def _make_driver(*, headless: bool, nav_ms: int, act_ms: int) -> BrowserDriver:
    if _DRIVER_FACTORY is not None:
        driver = _DRIVER_FACTORY()
        start = getattr(driver, "start", None)
        if callable(start):
            result = start()
            if asyncio.iscoroutine(result):
                await result
        return driver
    driver = PlaywrightDriver()
    await driver.start(headless=headless, nav_ms=nav_ms, act_ms=act_ms)
    return driver


def _snapshot(session: _Session, url: str, title: str, text: str) -> dict[str, Any]:
    return {
        "url": url,
        "title": title,
        "text": text,
        "elements": [
            {"ref": item.ref, "role": item.role, "name": item.name}
            for item in session.elements.values()
        ],
    }


async def _observe(session: _Session) -> dict[str, Any]:
    url, title, text, elements = await session.driver.observe()
    session.elements = {item.ref: item for item in elements}
    session.driver.url = url
    return _snapshot(session, url, title, text)


async def _session(run_id: str) -> _Session:
    enabled, headless, nav_ms, act_ms, _loopback = _settings()
    if not enabled:
        raise BrowserError("browser_disabled", "navigateur agentique désactivé")
    evicted: list[_Session] = []
    with _SESSIONS_LOCK:
        existing = _SESSIONS.get(run_id)
        if existing is not None:
            return existing
        while len(_SESSIONS) >= MAX_SESSIONS:
            oldest_id = min(_SESSIONS, key=lambda key: _SESSIONS[key].created_at)
            evicted.append(_SESSIONS.pop(oldest_id))
    for stale in evicted:
        await stale.driver.close()
    try:
        driver = await _make_driver(headless=headless, nav_ms=nav_ms, act_ms=act_ms)
    except PlaywrightUnavailable as exc:
        raise BrowserError("playwright_unavailable", str(exc)) from exc
    session = _Session(run_id=run_id, driver=driver, created_at=_now())
    with _SESSIONS_LOCK:
        _SESSIONS[run_id] = session
    return session


async def _close(run_id: str) -> dict[str, Any]:
    with _SESSIONS_LOCK:
        session = _SESSIONS.pop(run_id, None)
    if session is not None:
        await session.driver.close()
    return {"closed": True, "run_id": run_id}


def close_session(run_id: str) -> None:
    """Ferme la session d'un run, y compris à la fin du broker MCP."""

    if not run_id:
        return
    try:
        _run(_close(run_id), timeout_s=10.0)
    except Exception:  # noqa: BLE001 — la fermeture ne doit pas masquer l'arrêt du run
        logger.debug("fermeture navigateur ignorée pour %s", run_id, exc_info=True)


def _require_ref(session: _Session, ref: str | None) -> BrowserElement:
    key = str(ref or "").strip()
    element = session.elements.get(key)
    if element is None:
        raise BrowserError("unknown_ref", f"référence inconnue : {key or '∅'}")
    return element


async def _apply(run_id: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    op = str(arguments.get("op") or "").strip()
    if op == "close":
        return await _close(run_id)
    enabled, _headless, _nav, _act, allow_loopback = _settings()
    if not enabled:
        raise BrowserError("browser_disabled", "navigateur agentique désactivé")
    session = await _session(run_id)
    if op == "open":
        target = validate_browser_target(
            str(arguments.get("url") or ""),
            allow_loopback=allow_loopback,
        )
        await session.driver.open(target)
        snapshot = await _observe(session)
        return {"ok": True, "op": op, "started": True, **snapshot}
    if op == "see":
        if not session.driver.url and not session.elements:
            raise BrowserError("no_page", "aucune page ouverte")
        snapshot = await _observe(session)
        return {"ok": True, "op": op, "started": True, **snapshot}
    element = _require_ref(session, arguments.get("ref") if isinstance(arguments.get("ref"), str) else None)
    current_url = session.driver.url
    if is_final_commit(element.name, current_url):
        snapshot = await _observe(session)
        return {
            "ok": False,
            "op": op,
            "started": True,
            "blocked": "payment_or_booking",
            "needs_confirmation": True,
            "message": (
                "Paiement, réservation finale ou action destructive : "
                "je m'arrête. Confirmez vous-même sur la page."
            ),
            **snapshot,
        }
    if op == "click":
        await session.driver.click(element)
    elif op == "type":
        text = str(arguments.get("text") or "")
        if not text or len(text) > MAX_TYPE_CHARS:
            raise BrowserError("text_invalid", "texte à saisir invalide")
        await session.driver.fill(element, text)
    elif op == "press":
        key = str(arguments.get("key") or "")
        if key not in ALLOWED_KEYS:
            raise BrowserError("key_invalid", "touche non autorisée")
        await session.driver.press(element, key)
    else:
        raise BrowserError("op_invalid", f"opération inconnue : {op}")
    snapshot = await _observe(session)
    return {"ok": True, "op": op, "started": True, **snapshot}


def apply(run_id: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Point d'entrée synchrone pour le pont MCP."""

    clean_run = str(run_id or "").strip()
    if not clean_run or len(clean_run) > 160:
        return {"ok": False, "error": "run_id_invalid", "started": False}
    try:
        return _run(_apply(clean_run, arguments))
    except OutboundURLRejected as exc:
        return {
            "ok": False,
            "error": exc.code,
            "message": str(exc),
            "started": True,
        }
    except BrowserError as exc:
        return {
            "ok": False,
            "error": exc.code,
            "message": str(exc),
            "started": exc.code != "browser_disabled",
        }
    except Exception as exc:  # noqa: BLE001 — frontière outil : jamais d'exception brute
        logger.warning("navigateur agentique : %s", type(exc).__name__)
        return {
            "ok": False,
            "error": "browser_failed",
            "message": f"navigateur indisponible ({type(exc).__name__})",
            "started": True,
        }


__all__ = [
    "BROWSER_INPUT_SCHEMA",
    "BROWSER_TOOL_NAME",
    "BrowserElement",
    "BrowserError",
    "apply",
    "close_session",
    "is_final_commit",
    "set_driver_factory",
    "validate_browser_target",
]
