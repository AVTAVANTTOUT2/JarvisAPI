#!/usr/bin/env python3
"""Migration idempotente des tables DevAgent dans jarvis.db."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from database import get_db, init_db  # noqa: E402
from database.devagent import migrate_devagent_tables  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("migrate_devagent")


def main() -> int:
    init_db()
    with get_db() as conn:
        migrate_devagent_tables(conn)
    logger.info("Migration DevAgent terminee.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
