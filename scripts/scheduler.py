"""Planificateur APScheduler — briefing matin automatique, tâches en retard."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

import config

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()

# Une notification « tâche en retard » par tâche et par jour civil (évite le spam horaire).
_OVERDUE_NOTIFIED_DAY: dict[int, str] = {}


async def _run_location_analysis_job():
    try:
        from scripts.location_analyzer import run_location_analysis

        await run_location_analysis()
    except Exception as e:
        logger.exception("[scheduler] location_analysis : %s", e)


async def scheduled_morning_briefing():
    """Génère le briefing du matin et notifie le bureau."""
    try:
        from agents.productivity import productivity_agent
        from integrations.notifications_macos import mac_notifier

        await productivity_agent.morning_briefing()
        logger.info("[scheduler] Briefing matin généré")
        if config.DESKTOP_NOTIFICATIONS:
            await mac_notifier.notify(
                title="JARVIS — Briefing du matin",
                message="Ton briefing est prêt. Ouvre JARVIS pour le consulter.",
                sound=config.NOTIFICATION_SOUND or "Glass",
            )
    except Exception as e:
        logger.exception("[scheduler] Erreur briefing matin : %s", e)


async def check_overdue_tasks():
    """Notifications pour les tâches non terminées dont l’échéance est dépassée."""
    try:
        from integrations.notifications_macos import mac_notifier
        from database import get_tasks

        if not config.DESKTOP_NOTIFICATIONS:
            return
        tasks = get_tasks()
        now = datetime.now()
        today_s = now.strftime("%Y-%m-%d")
        for task in tasks:
            dd = task.get("due_date")
            if not dd or task.get("status") == "done":
                continue
            tid = task.get("id")
            if tid is not None and _OVERDUE_NOTIFIED_DAY.get(int(tid)) == today_s:
                continue
            try:
                due_s = str(dd).replace("Z", "+00:00")
                if "T" in due_s:
                    due = datetime.fromisoformat(due_s.split("+")[0])
                else:
                    due = datetime.fromisoformat(due_s[:10])
            except Exception:
                logger.warning("[scheduler] due_date illisible : %s", dd)
                continue
            if due <= now:
                await mac_notifier.notify_urgent(
                    title="JARVIS — Tâche en retard",
                    message=f"{task.get('title', '?')} — échéance dépassée",
                )
                if tid is not None:
                    _OVERDUE_NOTIFIED_DAY[int(tid)] = today_s
                logger.info("[scheduler] Notif retard : %s", task.get("title"))
    except Exception as e:
        logger.exception("[scheduler] check_overdue_tasks : %s", e)


async def scheduled_evening_summary():
    """Génère le résumé du soir."""
    try:
        from agents.productivity import productivity_agent

        await productivity_agent.evening_summary()
        logger.info("[scheduler] Résumé du soir généré")
    except Exception as e:
        logger.exception("[scheduler] Erreur résumé soir : %s", e)


async def scheduled_weekly_summary():
    """Résumé hebdomadaire (dimanche soir)."""
    try:
        from agents.memory import memory_agent

        await memory_agent.weekly_summary()
        logger.info("[scheduler] Résumé hebdomadaire généré")
    except Exception as e:
        logger.exception("[scheduler] Erreur résumé hebdo : %s", e)


async def _relationship_analysis_daily_job() -> None:
    """Analyse relationnelle iMessage quotidienne (3h du matin)."""
    try:
        from scripts.relationship_analyzer import analyzer

        await analyzer.run_daily_update()
        logger.info("[scheduler] Analyse relationnelle quotidienne terminée")
    except Exception as e:
        logger.exception("[scheduler] relationship_analysis_daily : %s", e)


async def _relationship_alerts_job() -> None:
    try:
        from scripts.contact_alerts import check_relationship_alerts

        await check_relationship_alerts()
    except Exception as e:
        logger.exception("[scheduler] relationship_alerts : %s", e)


def _parse_hh_mm(s: str) -> tuple[int, int]:
    parts = (s or "07:30").strip().split(":")
    try:
        h = max(0, min(23, int(parts[0])))
        m = max(0, min(59, int(parts[1]) if len(parts) > 1 else 0))
        return h, m
    except Exception:
        return 7, 30


async def _db_backup_job():
    """Sauvegarde SQLite quotidienne (04:15)."""
    if not config.BACKUP_ENABLED:
        return
    try:
        from scripts.db_maintenance import run_backup

        report = await asyncio.to_thread(run_backup)
        if not report.get("ok"):
            logger.error("[scheduler] backup : %s", report.get("error"))
    except Exception as e:
        logger.exception("[scheduler] db_backup : %s", e)


async def _db_maintenance_job():
    """Purge de rétention + optimisation (dimanche 04:45)."""
    try:
        from scripts.db_maintenance import run_maintenance

        await asyncio.to_thread(run_maintenance)
    except Exception as e:
        logger.exception("[scheduler] db_maintenance : %s", e)


async def _llm_budget_job():
    """Vérification du budget LLM mensuel (21:30)."""
    try:
        from scripts.db_maintenance import check_llm_budget

        await asyncio.to_thread(check_llm_budget)
    except Exception as e:
        logger.exception("[scheduler] llm_budget : %s", e)


async def _roast_job():
    """Roast quotidien des tâches non faites."""
    if not config.RITUALS_ENABLED:
        return
    try:
        from scripts.rituals import daily_roast

        await daily_roast()
    except Exception as e:
        logger.exception("[scheduler] roast : %s", e)


async def _debrief_job():
    """Debrief du soir (résumé + ratés) + score productivité figé."""
    if not config.RITUALS_ENABLED:
        return
    try:
        from scripts.rituals import evening_debrief

        await evening_debrief()
    except Exception as e:
        logger.exception("[scheduler] debrief : %s", e)


async def _quote_job():
    """Citation ironique du jour (widget TV)."""
    if not config.RITUALS_ENABLED:
        return
    try:
        from scripts.rituals import daily_quote

        await daily_quote()
    except Exception as e:
        logger.exception("[scheduler] quote : %s", e)


async def _birthday_job():
    """Anniversaires des contacts du jour."""
    if not config.RITUALS_ENABLED:
        return
    try:
        from scripts.rituals import check_birthdays

        await asyncio.to_thread(check_birthdays)
    except Exception as e:
        logger.exception("[scheduler] birthdays : %s", e)


async def _coffee_break_job():
    """Alerte pause café si activité écran continue trop longue."""
    try:
        from scripts.rituals import check_coffee_break

        await asyncio.to_thread(check_coffee_break)
    except Exception as e:
        logger.exception("[scheduler] coffee_break : %s", e)


def setup_scheduler() -> None:
    """Enregistre les jobs (idempotent avec replace_existing)."""
    h, m = _parse_hh_mm(config.MORNING_BRIEFING_TIME)
    scheduler.add_job(
        scheduled_morning_briefing,
        CronTrigger(hour=h, minute=m),
        id="morning_briefing",
        replace_existing=True,
    )
    scheduler.add_job(
        check_overdue_tasks,
        CronTrigger(minute=0),
        id="check_overdue",
        replace_existing=True,
    )
    scheduler.add_job(
        _run_location_analysis_job,
        CronTrigger(hour=23, minute=0),
        id="location_analysis",
        replace_existing=True,
    )
    scheduler.add_job(
        _relationship_alerts_job,
        CronTrigger(hour="*/6", minute=0),
        id="relationship_alerts",
        replace_existing=True,
    )

    eh, em = _parse_hh_mm(config.EVENING_SUMMARY_TIME)
    scheduler.add_job(
        scheduled_evening_summary,
        CronTrigger(hour=eh, minute=em),
        id="evening_summary",
        replace_existing=True,
    )
    scheduler.add_job(
        scheduled_weekly_summary,
        CronTrigger(day_of_week="sun", hour=20, minute=0),
        id="weekly_summary",
        replace_existing=True,
    )
    scheduler.add_job(
        _relationship_analysis_daily_job,
        CronTrigger(hour=3, minute=0),
        id="relationship_analysis_daily",
        replace_existing=True,
    )
    scheduler.add_job(
        _db_backup_job,
        CronTrigger(hour=4, minute=15),
        id="db_backup",
        replace_existing=True,
    )
    scheduler.add_job(
        _db_maintenance_job,
        CronTrigger(day_of_week="sun", hour=4, minute=45),
        id="db_maintenance",
        replace_existing=True,
    )
    scheduler.add_job(
        _llm_budget_job,
        CronTrigger(hour=21, minute=30),
        id="llm_budget",
        replace_existing=True,
    )

    rh, rm = _parse_hh_mm(config.ROAST_TIME)
    scheduler.add_job(
        _roast_job, CronTrigger(hour=rh, minute=rm),
        id="daily_roast", replace_existing=True,
    )
    dh, dm = _parse_hh_mm(config.DEBRIEF_TIME)
    scheduler.add_job(
        _debrief_job, CronTrigger(hour=dh, minute=dm),
        id="evening_debrief", replace_existing=True,
    )
    qh, qm = _parse_hh_mm(config.QUOTE_TIME)
    scheduler.add_job(
        _quote_job, CronTrigger(hour=qh, minute=qm),
        id="daily_quote", replace_existing=True,
    )
    bh, bm = _parse_hh_mm(config.BIRTHDAY_CHECK_TIME)
    scheduler.add_job(
        _birthday_job, CronTrigger(hour=bh, minute=bm),
        id="birthday_check", replace_existing=True,
    )
    scheduler.add_job(
        _coffee_break_job, CronTrigger(hour="9-22", minute="*/20"),
        id="coffee_break", replace_existing=True,
    )

    logger.info(
        "[scheduler] 15 jobs enregistrés (briefing %02d:%02d, résumé soir %02d:%02d, "
        "hebdo dim 20:00, overdue chaque heure, analyse géo 23:00, "
        "alertes relationnelles /6h, analyse relationnelle 3:00, "
        "backup 4:15, maintenance dim 4:45, budget LLM 21:30, "
        "roast %s, debrief %s, citation %s, anniversaires %s, pause café /20min 9-22h)",
        h, m, eh, em,
        config.ROAST_TIME, config.DEBRIEF_TIME, config.QUOTE_TIME, config.BIRTHDAY_CHECK_TIME,
    )


def start_scheduler() -> None:
    setup_scheduler()
    if not scheduler.running:
        scheduler.start()
        logger.info("[scheduler] Démarré")


def shutdown_scheduler() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("[scheduler] Arrêté")
