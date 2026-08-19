"""Surveillance locale de ``chat.db`` — enqueue seulement, jamais de lecture SQLite.

Le worker d'ingestion est le seul propriétaire de l'import. Ce module signale
un fichier Messages.app modifié (kqueue Darwin, sinon no-op : le poll 30 s du
scheduler reste le filet). Les tests injectent un backend et appellent
``notify()`` / ``tick()`` sans kqueue réel.
"""

from __future__ import annotations

import asyncio
import logging
import os
import select
import threading
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Protocol

from integrations.apple_data import DEFAULT_CHAT_DB_PATH


logger = logging.getLogger(__name__)

EnqueueFn = Callable[[], None]


class WatchBackend(Protocol):
    def start(
        self, paths: Sequence[Path], on_event: Callable[[], None]
    ) -> None: ...

    def stop(self) -> None: ...


class NullWatchBackend:
    """CI Linux / tests : aucun descripteur, le debounce se pilote à la main."""

    def start(
        self, paths: Sequence[Path], on_event: Callable[[], None]
    ) -> None:
        del paths, on_event

    def stop(self) -> None:
        return None


class KqueueWatchBackend:
    """Darwin : NOTE_WRITE/EXTEND sur ``chat.db`` et ses sidecars WAL."""

    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(
        self, paths: Sequence[Path], on_event: Callable[[], None]
    ) -> None:
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            args=(tuple(Path(p) for p in paths), on_event),
            name="imessage-kqueue",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
        self._thread = None

    def _run(
        self, paths: tuple[Path, ...], on_event: Callable[[], None]
    ) -> None:
        while not self._stop.is_set():
            fds: list[int] = []
            kq = None
            try:
                kq = select.kqueue()
                flags = select.KQ_EV_ADD | select.KQ_EV_CLEAR | select.KQ_EV_ENABLE
                fflags = (
                    select.KQ_NOTE_WRITE
                    | select.KQ_NOTE_EXTEND
                    | select.KQ_NOTE_ATTRIB
                    | select.KQ_NOTE_DELETE
                    | select.KQ_NOTE_RENAME
                )
                open_flags = getattr(os, "O_EVTONLY", os.O_RDONLY)
                for path in paths:
                    if not path.exists():
                        continue
                    try:
                        fd = os.open(path, open_flags)
                    except OSError:
                        continue
                    fds.append(fd)
                    kq.control(
                        [
                            select.kevent(
                                fd,
                                filter=select.KQ_FILTER_VNODE,
                                flags=flags,
                                fflags=fflags,
                            )
                        ],
                        0,
                    )
                if not fds:
                    self._stop.wait(1.0)
                    continue
                while not self._stop.is_set():
                    events = kq.control(None, 8, 0.5)
                    if events:
                        on_event()
            except Exception:
                logger.exception("[imessage_watch] kqueue")
                self._stop.wait(1.0)
            finally:
                if kq is not None:
                    try:
                        kq.close()
                    except OSError:
                        pass
                for fd in fds:
                    try:
                        os.close(fd)
                    except OSError:
                        pass


def default_watch_backend() -> WatchBackend:
    if hasattr(select, "kqueue"):
        return KqueueWatchBackend()
    return NullWatchBackend()


def chat_db_watch_paths(chat_db: Path | None = None) -> tuple[Path, ...]:
    root = Path(chat_db) if chat_db is not None else DEFAULT_CHAT_DB_PATH
    return (root, Path(str(root) + "-wal"), Path(str(root) + "-shm"))


def enqueue_imessage_watch_job() -> None:
    """Enfile un sync iMessage pour chaque profil lié, sans ouvrir chat.db."""

    from database import list_user_profiles, use_profile
    from database.ingestion import (
        ConnectorBindingRequired,
        enqueue_ingestion_job,
        refresh_local_connector_device_hash,
    )

    try:
        profiles = list_user_profiles()
    except Exception:
        logger.exception("[imessage_watch] profils indisponibles")
        return
    if not profiles:
        profiles = [{"id": "default"}]
    for profile in profiles:
        profile_id = str(profile.get("id") or "default")
        try:
            with use_profile(profile_id):
                refresh_local_connector_device_hash("imessage")
                enqueue_ingestion_job(
                    "imessage",
                    job_kind="sync",
                    dedupe_key="sync:watch",
                )
        except ConnectorBindingRequired:
            continue
        except Exception:
            logger.exception(
                "[imessage_watch] enqueue failed profile=%s", profile_id
            )


class IMessageFileWatcher:
    """Debounce les events FS puis appelle ``enqueue`` une fois le calme revenu."""

    def __init__(
        self,
        *,
        debounce_s: float = 0.3,
        enqueue: EnqueueFn | None = None,
        backend: WatchBackend | None = None,
        paths: Sequence[Path] | None = None,
    ) -> None:
        self._debounce_s = max(0.05, float(debounce_s))
        self._enqueue = enqueue or enqueue_imessage_watch_job
        self._backend = backend if backend is not None else default_watch_backend()
        self._paths = tuple(paths) if paths is not None else chat_db_watch_paths()
        self._lock = threading.Lock()
        self._pending_at: float | None = None

    def notify(self, *, now: float | None = None) -> None:
        """Signale une écriture. Relance le debounce (dernier event gagne)."""

        stamp = time.monotonic() if now is None else float(now)
        with self._lock:
            self._pending_at = stamp

    def tick(self, *, now: float | None = None) -> bool:
        """Enfile le job si le debounce est écoulé. Retourne True si enfilé."""

        stamp = time.monotonic() if now is None else float(now)
        with self._lock:
            pending = self._pending_at
            if pending is None:
                return False
            if stamp - pending < self._debounce_s:
                return False
            self._pending_at = None
        try:
            self._enqueue()
        except Exception:
            logger.exception("[imessage_watch] enqueue")
            with self._lock:
                if self._pending_at is None:
                    self._pending_at = stamp
            return False
        return True

    async def run_until(self, stop: asyncio.Event) -> None:
        self._backend.start(self._paths, lambda: self.notify())
        try:
            while not stop.is_set():
                self.tick()
                try:
                    await asyncio.wait_for(stop.wait(), timeout=0.05)
                except TimeoutError:
                    pass
        finally:
            self._backend.stop()


__all__ = [
    "IMessageFileWatcher",
    "KqueueWatchBackend",
    "NullWatchBackend",
    "WatchBackend",
    "chat_db_watch_paths",
    "default_watch_backend",
    "enqueue_imessage_watch_job",
]
