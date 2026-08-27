"""Cycle de vie borné des sessions et preuves du navigateur agentique."""

from __future__ import annotations

import atexit
import asyncio
from collections.abc import Mapping
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import logging
import re
import threading
from typing import Any, Protocol

from core.outbound_security import validate_open_world_https_url
from integrations.browser_driver import (
    BrowserElement,
    LiveBrowserElement,
    PlaywrightDriver,
)
from integrations.browser_security import sanitized_browser_url
from integrations.playwright_runtime import PlaywrightUnavailable
from jarvis.security.redaction import redact_persisted_text

logger = logging.getLogger("jarvis.browser")
MAX_SESSIONS = 2
MAX_RECEIPTS = 64
MIN_NAV_TIMEOUT_MS = 1_000
MAX_NAV_TIMEOUT_MS = 120_000
MIN_ACTION_TIMEOUT_MS = 500
MAX_ACTION_TIMEOUT_MS = 60_000
MIN_SESSION_TTL_SECONDS = 1
MAX_SESSION_TTL_SECONDS = 3_600
EXPIRY_POLL_SECONDS = 0.25


class BrowserError(RuntimeError):
    """Erreur bornée du navigateur agentique."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class BrowserDriver(Protocol):
    url: str

    async def open(self, url: str) -> None: ...
    async def observe(self) -> tuple[str, str, str, list[BrowserElement]]: ...
    async def inspect(self, element: BrowserElement) -> LiveBrowserElement: ...
    async def submit_search(self, element: LiveBrowserElement, text: str) -> None: ...
    async def close(self) -> None: ...


@dataclass
class BrowserSession:
    run_id: str
    driver: BrowserDriver
    elements: dict[str, BrowserElement] = field(default_factory=dict)
    created_at: float = field(default_factory=lambda: 0.0)
    last_used_at: float = field(default_factory=lambda: 0.0)
    generation: int = 0
    leases: int = 0
    closing: bool = False
    expiry_cleanup_pending: bool = False
    operation_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


@dataclass(frozen=True, slots=True)
class _BrowserReceipt:
    snapshot_id: str
    metadata: Mapping[str, Any]


_SESSIONS: dict[str, BrowserSession] = {}
_SESSIONS_LOCK = threading.Lock()
_RECEIPTS: dict[str, _BrowserReceipt] = {}
_RECEIPTS_LOCK = threading.Lock()
_LOOP: asyncio.AbstractEventLoop | None = None
_LOOP_THREAD: threading.Thread | None = None
_LOOP_LOCK = threading.Lock()
_SESSION_CREATE_LOCK: asyncio.Lock | None = None
_SESSION_CREATE_LOOP: asyncio.AbstractEventLoop | None = None
_EXPIRY_TASK: asyncio.Task[None] | None = None
_SHUTTING_DOWN = False
_DRIVER_FACTORY: Any = None
_TEST_TARGET_VALIDATOR: Any = None


def browser_now() -> float:
    import time

    return time.monotonic()


def set_driver_factory(factory: Any | None, *, target_validator: Any | None = None) -> None:
    """Injection réservée aux tests ; aucune variable runtime ne l'active."""

    global _DRIVER_FACTORY, _TEST_TARGET_VALIDATOR
    _DRIVER_FACTORY = factory
    _TEST_TARGET_VALIDATOR = target_validator


def _settings() -> tuple[bool, bool, int, int, int]:
    import config

    nav_ms = int(getattr(config, "BROWSER_NAV_TIMEOUT_MS", 20_000))
    act_ms = int(getattr(config, "BROWSER_ACTION_TIMEOUT_MS", 8_000))
    ttl_seconds = int(getattr(config, "BROWSER_SESSION_TTL_SECONDS", 300))
    return (
        bool(getattr(config, "BROWSER_ENABLED", True)),
        bool(getattr(config, "BROWSER_HEADLESS", True)),
        max(MIN_NAV_TIMEOUT_MS, min(MAX_NAV_TIMEOUT_MS, nav_ms)),
        max(MIN_ACTION_TIMEOUT_MS, min(MAX_ACTION_TIMEOUT_MS, act_ms)),
        max(MIN_SESSION_TTL_SECONDS, min(MAX_SESSION_TTL_SECONDS, ttl_seconds)),
    )


def validate_browser_target(url: str, *, resolver: Any | None = None) -> str:
    """Valide une destination HTTPS/443 publique sans mode loopback runtime."""

    return validate_open_world_https_url(str(url or "").strip(), resolver=resolver)


def validate_target(url: str) -> str:
    if _TEST_TARGET_VALIDATOR is not None:
        return str(_TEST_TARGET_VALIDATOR(url))
    return validate_browser_target(url)


def _ensure_loop() -> asyncio.AbstractEventLoop:
    global _LOOP, _LOOP_THREAD
    with _LOOP_LOCK:
        if _SHUTTING_DOWN:
            raise BrowserError("browser_shutting_down", "arrêt du navigateur en cours")
        if _LOOP is not None and _LOOP.is_running():
            return _LOOP
        loop = asyncio.new_event_loop()

        def _run_loop() -> None:
            asyncio.set_event_loop(loop)
            loop.run_forever()

        thread = threading.Thread(target=_run_loop, name="jarvis-browser", daemon=True)
        thread.start()
        _LOOP = loop
        _LOOP_THREAD = thread
        return loop


async def _wait_with_timeout(coro: Any, timeout_s: float) -> Any:
    try:
        return await asyncio.wait_for(coro, timeout=timeout_s)
    except TimeoutError as exc:
        raise BrowserError("browser_timeout", "Délai du navigateur dépassé") from exc


def run_browser_coroutine(coro: Any, timeout_s: float = 45.0) -> Any:
    try:
        loop = _ensure_loop()
    except Exception:
        close = getattr(coro, "close", None)
        if callable(close):
            close()
        raise
    wrapped = _wait_with_timeout(coro, timeout_s)
    try:
        future = asyncio.run_coroutine_threadsafe(wrapped, loop)
    except Exception:
        wrapped.close()
        close = getattr(coro, "close", None)
        if callable(close):
            close()
        raise
    try:
        return future.result(timeout_s + 2.0)
    except FutureTimeoutError as exc:
        future.cancel()
        try:
            future.result(1.0)
        except Exception:
            pass
        raise BrowserError("browser_timeout", "Délai du navigateur dépassé") from exc


async def _make_driver(*, headless: bool, nav_ms: int, act_ms: int) -> BrowserDriver:
    driver: Any = _DRIVER_FACTORY() if _DRIVER_FACTORY is not None else PlaywrightDriver()
    try:
        start = getattr(driver, "start", None)
        if callable(start):
            result = start(headless=headless, nav_ms=nav_ms, act_ms=act_ms)
            if asyncio.iscoroutine(result):
                await result
        return driver
    except BaseException:
        await _close_driver(driver)
        raise


async def _close_driver(driver: BrowserDriver) -> bool:
    cleanup = asyncio.create_task(driver.close())
    try:
        await asyncio.wait_for(asyncio.shield(cleanup), timeout=20.0)
        return True
    except asyncio.CancelledError:
        try:
            await asyncio.wait_for(asyncio.shield(cleanup), timeout=20.0)
        except Exception:
            logger.warning("fermeture navigateur incomplète", exc_info=True)
        raise
    except Exception:
        cleanup.cancel()
        await asyncio.gather(cleanup, return_exceptions=True)
        logger.warning("fermeture navigateur incomplète", exc_info=True)
        return False


async def discard_session(
    run_id: str, expected: BrowserSession | None = None
) -> bool:
    with _SESSIONS_LOCK:
        session = _SESSIONS.get(run_id)
        if session is None or (expected is not None and session is not expected):
            return True
        session.closing = True
        session.expiry_cleanup_pending = False
    async with session.operation_lock:
        with _SESSIONS_LOCK:
            if _SESSIONS.get(run_id) is not session:
                return True
        closed = await _close_driver(session.driver)
    if closed:
        with _SESSIONS_LOCK:
            if _SESSIONS.get(run_id) is session:
                _SESSIONS.pop(run_id, None)
        return True
    raise BrowserError("browser_cleanup_failed", "fermeture navigateur incomplète")


def _session_creation_lock() -> asyncio.Lock:
    global _SESSION_CREATE_LOCK, _SESSION_CREATE_LOOP
    loop = asyncio.get_running_loop()
    if _SESSION_CREATE_LOCK is None or _SESSION_CREATE_LOOP is not loop:
        _SESSION_CREATE_LOCK = asyncio.Lock()
        _SESSION_CREATE_LOOP = loop
    return _SESSION_CREATE_LOCK


async def _expire_idle_sessions(ttl_seconds: int) -> None:
    """Ferme les sessions réellement inactives sans attendre un nouvel appel."""

    now = browser_now()
    expired: list[BrowserSession] = []
    with _SESSIONS_LOCK:
        for candidate in tuple(_SESSIONS.values()):
            expired_by_ttl = now - candidate.last_used_at > ttl_seconds
            retrying_expiry = (
                candidate.closing and candidate.expiry_cleanup_pending
            )
            if (
                candidate.leases == 0
                and not candidate.operation_lock.locked()
                and (retrying_expiry or (expired_by_ttl and not candidate.closing))
            ):
                candidate.closing = True
                candidate.expiry_cleanup_pending = True
                expired.append(candidate)
    for candidate in expired:
        async with candidate.operation_lock:
            with _SESSIONS_LOCK:
                if (
                    _SESSIONS.get(candidate.run_id) is not candidate
                    or candidate.leases > 0
                    or not candidate.expiry_cleanup_pending
                ):
                    continue
            closed = await _close_driver(candidate.driver)
        if closed:
            with _SESSIONS_LOCK:
                if _SESSIONS.get(candidate.run_id) is candidate:
                    _SESSIONS.pop(candidate.run_id, None)


async def _expiry_loop() -> None:
    try:
        while True:
            _enabled, _headless, _nav_ms, _act_ms, ttl_seconds = _settings()
            interval = min(1.0, max(EXPIRY_POLL_SECONDS, ttl_seconds / 4))
            await asyncio.sleep(interval)
            if _SHUTTING_DOWN:
                return
            async with _session_creation_lock():
                await _expire_idle_sessions(ttl_seconds)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("balayage TTL du navigateur interrompu")


def _ensure_expiry_task() -> None:
    global _EXPIRY_TASK
    if _SHUTTING_DOWN:
        return
    if _EXPIRY_TASK is None or _EXPIRY_TASK.done():
        _EXPIRY_TASK = asyncio.create_task(
            _expiry_loop(), name="jarvis-browser-expiry"
        )


def release_session(session: BrowserSession) -> None:
    """Libère un lease obtenu par ``get_session`` sans ressusciter la session."""

    with _SESSIONS_LOCK:
        session.leases = max(0, session.leases - 1)


async def get_session(run_id: str) -> BrowserSession:
    async with _session_creation_lock():
        return await _get_session_locked(run_id)


async def _get_session_locked(run_id: str) -> BrowserSession:
    enabled, headless, nav_ms, act_ms, ttl_seconds = _settings()
    if _SHUTTING_DOWN:
        raise BrowserError("browser_shutting_down", "arrêt du navigateur en cours")
    if not enabled:
        raise BrowserError("browser_disabled", "navigateur agentique désactivé")
    expired: list[BrowserSession] = []
    now = browser_now()
    with _SESSIONS_LOCK:
        existing = _SESSIONS.get(run_id)
        if existing is not None and existing.closing:
            raise BrowserError(
                "browser_cleanup_pending", "nettoyage navigateur en attente"
            )
        if existing is not None and (
            now - existing.last_used_at <= ttl_seconds or existing.leases > 0
        ):
            existing.leases += 1
            _ensure_expiry_task()
            return existing
        if existing is not None:
            existing.closing = True
            expired.append(existing)
        for key, candidate in tuple(_SESSIONS.items()):
            if (
                now - candidate.last_used_at > ttl_seconds
                and candidate.leases == 0
                and not candidate.closing
                and not candidate.operation_lock.locked()
            ):
                candidate.closing = True
                expired.append(candidate)
    for candidate in expired:
        async with candidate.operation_lock:
            closed = await _close_driver(candidate.driver)
        if not closed:
            raise BrowserError(
                "browser_cleanup_failed", "fermeture navigateur incomplète"
            )
        with _SESSIONS_LOCK:
            if _SESSIONS.get(candidate.run_id) is candidate:
                _SESSIONS.pop(candidate.run_id, None)
    with _SESSIONS_LOCK:
        if len(_SESSIONS) >= MAX_SESSIONS:
            raise BrowserError("browser_capacity", "capacité navigateur atteinte")
    try:
        driver = await _make_driver(headless=headless, nav_ms=nav_ms, act_ms=act_ms)
    except PlaywrightUnavailable as exc:
        raise BrowserError("playwright_unavailable", str(exc)) from exc
    session = BrowserSession(
        run_id=run_id,
        driver=driver,
        created_at=now,
        last_used_at=now,
        leases=1,
    )
    with _SESSIONS_LOCK:
        concurrent = _SESSIONS.get(run_id)
        if concurrent is None:
            _SESSIONS[run_id] = session
            _ensure_expiry_task()
            return session
    if not await _close_driver(driver):
        raise BrowserError("browser_cleanup_failed", "fermeture navigateur incomplète")
    with _SESSIONS_LOCK:
        concurrent.leases += 1
    _ensure_expiry_task()
    return concurrent


def record_browser_receipt(
    run_id: str,
    *,
    snapshot_id: str,
    operation: str,
    url: str,
    title: str,
    text: str,
    policy_result: str,
    block_reason: str | None = None,
) -> None:
    metadata: dict[str, Any] = {
        "schema_version": 1,
        "issuer": "jarvis_browser",
        "run_id": run_id,
        "snapshot_id": snapshot_id,
        "operation": operation,
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "url": sanitized_browser_url(url),
        "title": redact_persisted_text(title.replace("\x00", ""))[:200],
        "content_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "policy_result": policy_result,
        "approval_verified": False,
    }
    if block_reason:
        metadata["block_reason"] = block_reason[:80]
    with _RECEIPTS_LOCK:
        _RECEIPTS[run_id] = _BrowserReceipt(snapshot_id, metadata)
        while len(_RECEIPTS) > MAX_RECEIPTS:
            _RECEIPTS.pop(next(iter(_RECEIPTS)))


def mark_browser_receipt_approved(
    run_id: str, arguments_sha256: str, *, snapshot_id: str
) -> None:
    """Lie le dernier snapshot à l'approbation exacte consommée par le parent."""

    if not re.fullmatch(r"[0-9a-f]{64}", str(arguments_sha256 or "")):
        raise ValueError("digest d'approbation navigateur invalide")
    with _RECEIPTS_LOCK:
        receipt = _RECEIPTS.get(run_id)
        if receipt is None:
            raise RuntimeError("snapshot navigateur absent")
        if receipt.snapshot_id != snapshot_id:
            raise RuntimeError("snapshot navigateur remplacé avant approbation")
        metadata = dict(receipt.metadata)
        if metadata.get("policy_result") != "allowed":
            raise RuntimeError("snapshot navigateur bloqué")
        metadata["approval_verified"] = True
        metadata["approval_arguments_sha256"] = arguments_sha256
        _RECEIPTS[run_id] = _BrowserReceipt(receipt.snapshot_id, metadata)


def clear_browser_receipt(run_id: str) -> None:
    with _RECEIPTS_LOCK:
        _RECEIPTS.pop(run_id, None)


def get_browser_snapshot_artifact(run_id: str) -> Any | None:
    """Construit l'artefact borné depuis le reçu émis côté parent JARVIS."""

    from jarvis.agentic.models import Artifact

    with _RECEIPTS_LOCK:
        receipt = _RECEIPTS.get(run_id)
    if receipt is None:
        return None
    encoded = json.dumps(
        dict(receipt.metadata),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return Artifact(
        artifact_id=f"{run_id}-browser-{receipt.snapshot_id}",
        run_id=run_id,
        type="browser_snapshot",
        reference=f"jarvis://browser/{run_id}/{receipt.snapshot_id}",
        sha256=hashlib.sha256(encoded).hexdigest(),
        size_bytes=len(encoded),
        metadata=receipt.metadata,
    )


def close_session(run_id: str, *, clear_receipt: bool = False) -> bool:
    """Ferme un run ; le reçu reste disponible jusqu'à sa collecte explicite."""

    if not run_id:
        return True
    closed = False
    try:
        closed = bool(run_browser_coroutine(discard_session(run_id), timeout_s=25.0))
    except Exception:
        logger.warning("fermeture navigateur échouée", exc_info=True)
    if clear_receipt and closed:
        clear_browser_receipt(run_id)
    return closed


async def _shutdown_sessions() -> bool:
    global _EXPIRY_TASK
    async with _session_creation_lock():
        # Wait for any in-flight creation before detaching the sweeper; that
        # creation cannot install a replacement while SHUTTING_DOWN is set.
        expiry, _EXPIRY_TASK = _EXPIRY_TASK, None
        if expiry is not None and expiry is not asyncio.current_task():
            expiry.cancel()
            await asyncio.gather(expiry, return_exceptions=True)
        # A partially-started driver may have completed just before this lock.
        # Re-read the registry and retry one transient close failure.
        for _attempt in range(2):
            with _SESSIONS_LOCK:
                sessions = tuple(_SESSIONS.values())
            if not sessions:
                break
            for session in sessions:
                try:
                    await discard_session(session.run_id, session)
                except Exception:
                    logger.warning(
                        "fermeture navigateur à l'arrêt incomplète", exc_info=True
                    )
                clear_browser_receipt(session.run_id)
        with _SESSIONS_LOCK:
            return not _SESSIONS


def shutdown() -> None:
    """Ferme les contextes puis arrête et rejoint la boucle globale."""

    global _LOOP, _LOOP_THREAD, _SESSION_CREATE_LOCK, _SESSION_CREATE_LOOP
    global _SHUTTING_DOWN
    _SHUTTING_DOWN = True
    with _LOOP_LOCK:
        loop, thread = _LOOP, _LOOP_THREAD
    if loop is not None and loop.is_running():
        if thread is threading.current_thread():
            # Aucun appel de production n'utilise ce chemin synchrone depuis la
            # boucle privée. Refuser de se joindre soi-même plutôt que masquer
            # une fermeture partielle.
            _SHUTTING_DOWN = False
            raise RuntimeError("browser_shutdown_from_loop_thread")
        cleanup = _shutdown_sessions()
        future = None
        try:
            future = asyncio.run_coroutine_threadsafe(cleanup, loop)
            clean = bool(future.result(timeout=50.0))
        except Exception:
            clean = False
            if future is None:
                cleanup.close()
            else:
                future.cancel()
            logger.warning("arrêt du navigateur incomplet", exc_info=True)
        if not clean:
            logger.error("arrêt du navigateur: sessions encore suivies")
    with _LOOP_LOCK:
        _LOOP = None
        _LOOP_THREAD = None
        _SESSION_CREATE_LOCK = None
        _SESSION_CREATE_LOOP = None
    if loop is not None and loop.is_running():
        loop.call_soon_threadsafe(loop.stop)
    if thread is not None and thread is not threading.current_thread():
        thread.join(timeout=5.0)
        if thread.is_alive():
            logger.error("arrêt du navigateur: thread encore actif")
    if loop is not None and not loop.is_running() and not loop.is_closed():
        loop.close()
    _SHUTTING_DOWN = False


atexit.register(shutdown)

__all__ = [
    "BrowserError",
    "BrowserSession",
    "browser_now",
    "clear_browser_receipt",
    "close_session",
    "discard_session",
    "get_browser_snapshot_artifact",
    "get_session",
    "mark_browser_receipt_approved",
    "release_session",
    "record_browser_receipt",
    "run_browser_coroutine",
    "set_driver_factory",
    "shutdown",
    "validate_browser_target",
    "validate_target",
]
