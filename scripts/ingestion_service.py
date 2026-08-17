#!/usr/bin/env python3
"""Entrypoint supervisable du worker d'ingestion durable."""

from __future__ import annotations

import asyncio
import argparse
import logging
import signal
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config  # noqa: E402
from database import bind_connector, init_db  # noqa: E402
from jarvis.ingestion.service import (  # noqa: E402
    ingestion_singleton_lock,
    run_ingestion_worker,
)


logger = logging.getLogger(__name__)


async def _run() -> None:
    from audio.continuous_recorder import register_recording_ingestion_handler

    register_recording_ingestion_handler()
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            pass
    await run_ingestion_worker(stop_event)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Worker d'ingestion locale JARVIS")
    subparsers = parser.add_subparsers(dest="command")
    bind = subparsers.add_parser(
        "bind-local", help="autoriser explicitement les sources locales"
    )
    bind.add_argument(
        "--source",
        action="extend",
        nargs="+",
        choices=("mail", "imessage", "calendar"),
        required=True,
        help="une ou plusieurs sources à lier au profil courant (option répétable)",
    )
    return parser.parse_args(argv)


def _bind_local(sources: list[str]) -> int:
    init_db()
    intervals = {
        "mail": int(getattr(config, "INGESTION_MAIL_INTERVAL_S", 120)),
        "imessage": int(getattr(config, "INGESTION_IMESSAGE_INTERVAL_S", 30)),
        "calendar": int(getattr(config, "INGESTION_CALENDAR_INTERVAL_S", 300)),
    }
    for source in dict.fromkeys(sources):
        bind_connector(
            source,
            connector_kind=source,
            account_ref="local",
            consent_source="explicit_cli",
            sync_interval_seconds=intervals[source],
        )
        logger.info("[ingestion] source locale liée explicitement: %s", source)
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    args = _parse_args(argv)
    if args.command == "bind-local":
        return _bind_local(args.source)
    try:
        with ingestion_singleton_lock():
            asyncio.run(_run())
    except RuntimeError as exc:
        if str(exc) == "ingestion_service_already_running":
            logger.error("[ingestion] un autre worker détient déjà le verrou")
            return 2
        raise
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
