"""Demande un rattrapage durable après une longue coupure.

Usage:
    cd /chemin/absolu/vers/JarvisAPI && source venv/bin/activate
    python scripts/catchup_after_downtime.py

Le script ne lit jamais directement les sources Apple. Il enfile les demandes
auprès du service ``com.jarvis.ingestion``, seul propriétaire des connecteurs.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("catchup_after_downtime")


async def main() -> None:
    from database import init_db
    from jarvis.ingestion.service import request_ingestion_freshness

    init_db()
    states = await asyncio.to_thread(
        request_ingestion_freshness,
        ("mail", "imessage", "calendar"),
        budget_ms=5_000,
    )
    summary = {
        source: (state.status if state is not None else "pending")
        for source, state in states.items()
    }
    logger.info("[catchup] Demandes durables enregistrées : %s", summary)
    logger.info(
        "[catchup] Le service d'ingestion poursuivra le backfill en arrière-plan."
    )


if __name__ == "__main__":
    asyncio.run(main())
