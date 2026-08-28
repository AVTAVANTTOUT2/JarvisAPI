"""Cycle de vie, concurrence et nettoyage du navigateur agentique."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import threading
import time

import pytest

import integrations.browser as browser
import integrations.browser_runtime as runtime


class _RuntimeDriver:
    def __init__(
        self,
        *,
        start_delay: float = 0.0,
        block_start: bool = False,
        open_delay: float = 0.0,
    ) -> None:
        self.url = ""
        self.start_delay = start_delay
        self.block_start = block_start
        self.open_delay = open_delay
        self.opened: list[str] = []
        self.closed = False
        self.closed_event = threading.Event()
        self.close_calls = 0
        self.fail_close_once = False
        self.active_opens = 0
        self.max_active_opens = 0

    async def start(self, **_kwargs: object) -> None:
        if self.start_delay:
            await asyncio.sleep(self.start_delay)
        if self.block_start:
            await asyncio.Event().wait()

    async def open(self, url: str) -> None:
        self.active_opens += 1
        self.max_active_opens = max(self.max_active_opens, self.active_opens)
        try:
            if self.open_delay:
                await asyncio.sleep(self.open_delay)
            self.url = url
            self.opened.append(url)
        finally:
            self.active_opens -= 1

    async def observe(self):
        return self.url, "Public title", "Public body", []

    async def inspect(self, _element):
        raise AssertionError("aucun élément attendu")

    async def submit_search(self, _element, _text: str) -> None:
        raise AssertionError("aucun élément attendu")

    async def close(self) -> None:
        self.close_calls += 1
        if self.fail_close_once:
            self.fail_close_once = False
            raise RuntimeError("close failed once")
        self.closed = True
        self.closed_event.set()


@pytest.fixture(autouse=True)
def isolated_browser_runtime(monkeypatch: pytest.MonkeyPatch):
    runtime.shutdown()
    runtime.set_driver_factory(None, target_validator=None)
    monkeypatch.setattr("config.BROWSER_ENABLED", True)
    monkeypatch.setattr("config.BROWSER_SESSION_TTL_SECONDS", 300)
    yield
    runtime.shutdown()
    runtime.set_driver_factory(None, target_validator=None)


def _open(run_id: str) -> dict[str, object]:
    return browser.apply(run_id, {"op": "open", "url": "https://public.example/"})


def test_concurrent_session_creation_is_bounded_and_isolated() -> None:
    drivers: list[_RuntimeDriver] = []

    def factory() -> _RuntimeDriver:
        driver = _RuntimeDriver(start_delay=0.02)
        drivers.append(driver)
        return driver

    runtime.set_driver_factory(factory, target_validator=str)
    with ThreadPoolExecutor(max_workers=3) as executor:
        results = list(executor.map(_open, ("race-a", "race-b", "race-c")))

    assert sum(result.get("ok") is True for result in results) == runtime.MAX_SESSIONS
    assert sum(result.get("error") == "browser_capacity" for result in results) == 1
    assert len(drivers) == runtime.MAX_SESSIONS
    assert sorted(driver.opened[0] for driver in drivers) == [
        "https://public.example/",
        "https://public.example/",
    ]


def test_same_run_operations_are_serialized() -> None:
    driver = _RuntimeDriver(open_delay=0.04)
    runtime.set_driver_factory(lambda: driver, target_validator=str)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(_open, ("same-run", "same-run")))

    assert all(result.get("ok") is True for result in results)
    assert driver.max_active_opens == 1
    assert len(driver.opened) == 2


def test_expired_idle_session_is_closed_before_replacement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = [10.0]
    drivers: list[_RuntimeDriver] = []

    def factory() -> _RuntimeDriver:
        driver = _RuntimeDriver()
        drivers.append(driver)
        return driver

    monkeypatch.setattr("config.BROWSER_SESSION_TTL_SECONDS", 5)
    monkeypatch.setattr(runtime, "browser_now", lambda: clock[0])
    monkeypatch.setattr(browser, "browser_now", lambda: clock[0])
    runtime.set_driver_factory(factory, target_validator=str)

    assert _open("ttl-run")["ok"] is True
    clock[0] = 20.0
    assert _open("ttl-run")["ok"] is True

    assert len(drivers) == 2
    assert drivers[0].closed is True
    assert drivers[1].closed is False


def test_idle_session_is_closed_proactively_at_ttl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    driver = _RuntimeDriver()
    monkeypatch.setattr("config.BROWSER_SESSION_TTL_SECONDS", 1)
    runtime.set_driver_factory(lambda: driver, target_validator=str)

    assert _open("proactive-ttl")["ok"] is True

    assert driver.closed_event.wait(2.5)
    deadline = time.monotonic() + 1.0
    while "proactive-ttl" in runtime._SESSIONS and time.monotonic() < deadline:
        time.sleep(0.005)
    assert "proactive-ttl" not in runtime._SESSIONS


def test_proactive_ttl_retries_a_transient_cleanup_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    driver = _RuntimeDriver()
    driver.fail_close_once = True
    monkeypatch.setattr("config.BROWSER_SESSION_TTL_SECONDS", 1)
    runtime.set_driver_factory(lambda: driver, target_validator=str)

    assert _open("proactive-ttl-retry")["ok"] is True

    assert driver.closed_event.wait(3.0)
    deadline = time.monotonic() + 1.0
    while "proactive-ttl-retry" in runtime._SESSIONS and time.monotonic() < deadline:
        time.sleep(0.005)
    assert driver.close_calls == 2
    assert "proactive-ttl-retry" not in runtime._SESSIONS


def test_timeout_while_starting_closes_partial_driver() -> None:
    driver = _RuntimeDriver(block_start=True)
    runtime.set_driver_factory(lambda: driver, target_validator=str)

    with pytest.raises(runtime.BrowserError, match="Délai") as caught:
        runtime.run_browser_coroutine(runtime.get_session("start-timeout"), timeout_s=0.05)

    assert caught.value.code == "browser_timeout"
    assert driver.closed is True
    assert "start-timeout" not in runtime._SESSIONS


def test_timed_out_coroutine_is_cancelled_and_awaited() -> None:
    cancelled = threading.Event()

    async def never_finishes() -> None:
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    with pytest.raises(runtime.BrowserError) as caught:
        runtime.run_browser_coroutine(never_finishes(), timeout_s=0.05)

    assert caught.value.code == "browser_timeout"
    assert cancelled.wait(1.0)


def test_cleanup_failure_remains_tracked_until_retry_succeeds() -> None:
    driver = _RuntimeDriver()
    runtime.set_driver_factory(lambda: driver, target_validator=str)
    assert _open("cleanup-run")["ok"] is True

    driver.fail_close_once = True
    assert runtime.close_session("cleanup-run") is False
    retry_while_tracked = browser.apply("cleanup-run", {"op": "see"})
    assert retry_while_tracked["error"] == "browser_cleanup_pending"
    assert "cleanup-run" in runtime._SESSIONS

    assert runtime.close_session("cleanup-run") is True
    assert driver.closed is True
    assert "cleanup-run" not in runtime._SESSIONS


def test_shutdown_closes_sessions_joins_thread_and_closes_loop() -> None:
    driver = _RuntimeDriver()
    runtime.set_driver_factory(lambda: driver, target_validator=str)
    assert _open("shutdown-run")["ok"] is True
    loop = runtime._LOOP
    thread = runtime._LOOP_THREAD
    assert loop is not None
    assert thread is not None

    runtime.shutdown()

    assert driver.closed is True
    assert runtime._SESSIONS == {}
    assert not thread.is_alive()
    assert loop.is_closed()


def test_shutdown_waits_for_concurrent_driver_creation() -> None:
    started = threading.Event()
    release_start = threading.Event()

    class _StartingDriver(_RuntimeDriver):
        async def start(self, **_kwargs: object) -> None:
            started.set()
            await asyncio.to_thread(release_start.wait)

    driver = _StartingDriver()
    runtime.set_driver_factory(lambda: driver, target_validator=str)

    with ThreadPoolExecutor(max_workers=2) as executor:
        opening = executor.submit(_open, "shutdown-race")
        assert started.wait(1.0)
        stopping = executor.submit(runtime.shutdown)
        deadline = time.monotonic() + 1.0
        while not runtime._SHUTTING_DOWN and time.monotonic() < deadline:
            time.sleep(0.005)
        assert runtime._SHUTTING_DOWN is True
        release_start.set()
        opening.result(timeout=2.0)
        stopping.result(timeout=5.0)

    assert driver.closed is True
    assert runtime._SESSIONS == {}


def test_shutdown_gate_refuses_new_loop_work_and_closes_coroutine() -> None:
    runtime._SHUTTING_DOWN = True
    pending = asyncio.sleep(0)
    try:
        with pytest.raises(runtime.BrowserError) as caught:
            runtime.run_browser_coroutine(pending)
        assert caught.value.code == "browser_shutting_down"
        assert pending.cr_frame is None
    finally:
        runtime._SHUTTING_DOWN = False


def test_failed_loop_submission_closes_unsubmitted_coroutine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime._ensure_loop()
    pending = asyncio.sleep(0)

    def reject_submission(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("loop rejected submission")

    with monkeypatch.context() as scoped:
        scoped.setattr(
            runtime.asyncio, "run_coroutine_threadsafe", reject_submission
        )
        with pytest.raises(RuntimeError, match="loop rejected"):
            runtime.run_browser_coroutine(pending)
    assert pending.cr_frame is None


def test_failed_shutdown_submission_closes_cleanup_coroutine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loop = runtime._ensure_loop()
    submitted: list[object] = []

    def reject_submission(coro: object, _loop: object) -> None:
        submitted.append(coro)
        raise RuntimeError("loop rejected shutdown")

    with monkeypatch.context() as scoped:
        scoped.setattr(
            runtime.asyncio, "run_coroutine_threadsafe", reject_submission
        )
        runtime.shutdown()

    assert len(submitted) == 1
    assert getattr(submitted[0], "cr_frame", object()) is None
    assert loop.is_closed()
