"""Rattrapage manuel après longue coupure : mails non présents en DB + analyse iMessage.

Usage:
    cd /Users/zeldris/JarvisAPI && source venv/bin/activate
    python scripts/catchup_after_downtime.py

Nécessite Mail.app accessible et les mêmes permissions que le serveur JARVIS.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("catchup_after_downtime")


async def main() -> None:
    from database import get_all_processed_email_ids, init_db
    from scripts.email_watcher import EmailWatcher
    from scripts.relationship_analyzer import analyzer

    init_db()
    n_ids = len(get_all_processed_email_ids())
    logger.info("[catchup] email_summaries déjà en base : %d gmail_id", n_ids)

    try:
        from integrations import mail_client as _mc

        if _mc is not None and hasattr(_mc, "reset_availability_cache"):
            _mc.reset_availability_cache()
            logger.info("[catchup] Cache disponibilité Mail réinitialisé (retry immédiat)")
    except Exception as e:
        logger.warning("[catchup] reset Mail cache : %s", e)

    w = EmailWatcher()
    logger.info("[catchup] Cycle mail (rattrapage non-lus hors DB)…")
    mail_stats = await w.run_catchup_cycle()
    logger.info("[catchup] Cycle mail terminé : %s", mail_stats)

    logger.info("[catchup] Analyse relationnelle incrémentale iMessage…")
    stats = await analyzer.run_daily_update()
    logger.info("[catchup] run_daily_update : %s", stats)

    try:
        from scripts.force_full_mac_sync import run_force_full_mac_sync

        report = run_force_full_mac_sync()
        logger.info("[catchup] force_full_mac_sync : %s", report)
    except Exception as e:
        logger.warning("[catchup] force_full_mac_sync : %s", e)

    logger.info("[catchup] Terminé.")


if __name__ == "__main__":
    asyncio.run(main())
