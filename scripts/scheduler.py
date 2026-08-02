"""Planificateur APScheduler — briefing, rituels, maintenance, ticks.

Chaque job est décoré par ``@tracked`` : les exécutions sont persistées dans
``scheduler_job_runs`` pour la page ``/scheduler``.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any, Awaitable, Callable

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

import config
from scripts.scheduler_tracking import (
    JOB_SPECS,
    _current_trigger,
    err,
    ok,
    parse_hh_mm,
    silent,
    skipped,
    tracked,
)

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()

# Une notification « tâche en retard » par tâche et par jour civil (évite le spam horaire).
_OVERDUE_NOTIFIED_DAY: dict[int, str] = {}

JobFn = Callable[[], Awaitable[Any]]


@tracked("location_analysis")
async def _run_location_analysis_job():
    if not config.LOCATION_ANALYSIS_ENABLED or not config.LOCATION_TRACKING:
        return skipped("LOCATION_ANALYSIS_ENABLED=false ou LOCATION_TRACKING=false")
    from scripts.location_analyzer import run_location_analysis

    await run_location_analysis()
    return ok("Analyse géographique terminée")


@tracked("morning_briefing")
async def scheduled_morning_briefing():
    """Génère le briefing du matin et notifie le bureau."""
    if not config.MORNING_BRIEFING_ENABLED:
        return skipped("MORNING_BRIEFING_ENABLED=false")
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
    return ok("Briefing matin généré")


@tracked("check_overdue")
async def check_overdue_tasks():
    """Notifications pour les tâches non terminées dont l’échéance est dépassée."""
    if not config.OVERDUE_TASKS_ENABLED:
        return skipped("OVERDUE_TASKS_ENABLED=false")
    from integrations.notifications_macos import mac_notifier
    from database import get_tasks

    if not config.DESKTOP_NOTIFICATIONS:
        return skipped("DESKTOP_NOTIFICATIONS=false")
    tasks = get_tasks()
    now = datetime.now()
    today_s = now.strftime("%Y-%m-%d")
    notified = 0
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
            notified += 1
            logger.info("[scheduler] Notif retard : %s", task.get("title"))
    if notified == 0:
        return silent("aucune tâche en retard à notifier")
    return ok(f"{notified} notification(s) retard")


@tracked("evening_summary")
async def scheduled_evening_summary():
    """Génère le résumé du soir."""
    if not config.EVENING_SUMMARY_ENABLED:
        return skipped("EVENING_SUMMARY_ENABLED=false")
    from agents.productivity import productivity_agent

    await productivity_agent.evening_summary()
    logger.info("[scheduler] Résumé du soir généré")
    return ok("Résumé du soir généré")


@tracked("weekly_summary")
async def scheduled_weekly_summary():
    """Résumé hebdomadaire (dimanche soir)."""
    if not config.WEEKLY_SUMMARY_ENABLED:
        return skipped("WEEKLY_SUMMARY_ENABLED=false")
    from agents.memory import memory_agent

    await memory_agent.weekly_summary()
    logger.info("[scheduler] Résumé hebdomadaire généré")
    return ok("Résumé hebdomadaire généré")


@tracked("relationship_analysis_daily")
async def _relationship_analysis_daily_job() -> Any:
    """Analyse relationnelle iMessage quotidienne (3h du matin)."""
    if not config.RELATIONSHIP_ANALYSIS_ENABLED:
        return skipped("RELATIONSHIP_ANALYSIS_ENABLED=false")
    from scripts.relationship_analyzer import analyzer

    await analyzer.run_daily_update()
    logger.info("[scheduler] Analyse relationnelle quotidienne terminée")
    return ok("Analyse relationnelle quotidienne terminée")


@tracked("relationship_alerts")
async def _relationship_alerts_job() -> Any:
    if not config.RELATIONSHIP_ALERTS_ENABLED:
        return skipped("RELATIONSHIP_ALERTS_ENABLED=false")
    from scripts.contact_alerts import check_relationship_alerts

    await check_relationship_alerts()
    return ok("Alertes relationnelles vérifiées")


def _parse_hh_mm(s: str) -> tuple[int, int]:
    return parse_hh_mm(s)


@tracked("db_backup")
async def _db_backup_job():
    """Sauvegarde SQLite quotidienne (04:15)."""
    if not config.BACKUP_ENABLED:
        return skipped("BACKUP_ENABLED=false")
    from scripts.db_maintenance import run_backup

    report = await asyncio.to_thread(run_backup)
    if not report.get("ok"):
        return err(str(report.get("error") or "backup échoué"))
    return ok(report)


@tracked("db_maintenance")
async def _db_maintenance_job():
    """Purge de rétention + optimisation (dimanche 04:45)."""
    if not config.DB_MAINTENANCE_ENABLED:
        return skipped("DB_MAINTENANCE_ENABLED=false")
    from scripts.db_maintenance import run_maintenance

    report = await asyncio.to_thread(run_maintenance)
    return ok(report if isinstance(report, dict) else "Maintenance terminée")


@tracked("llm_budget")
async def _llm_budget_job():
    """Vérification du budget LLM mensuel (21:30)."""
    if not config.LLM_BUDGET_CHECK_ENABLED:
        return skipped("LLM_BUDGET_CHECK_ENABLED=false")
    from scripts.db_maintenance import check_llm_budget

    report = await asyncio.to_thread(check_llm_budget)
    return ok(report if report is not None else "Budget LLM vérifié")


@tracked("daily_roast")
async def _roast_job():
    """Roast quotidien des tâches non faites."""
    if not config.RITUALS_ENABLED:
        return skipped("RITUALS_ENABLED=false")
    from scripts.rituals import daily_roast

    result = await daily_roast()
    return ok(result if result is not None else "Roast exécuté")


@tracked("evening_debrief")
async def _debrief_job():
    """Debrief du soir (résumé + ratés) + score productivité figé."""
    if not config.RITUALS_ENABLED:
        return skipped("RITUALS_ENABLED=false")
    from scripts.rituals import evening_debrief

    result = await evening_debrief()
    return ok(result if result is not None else "Débrief exécuté")


@tracked("daily_quote")
async def _quote_job():
    """Citation ironique du jour (widget TV)."""
    if not config.RITUALS_ENABLED:
        return skipped("RITUALS_ENABLED=false")
    from scripts.rituals import daily_quote

    result = await daily_quote()
    return ok(result if result is not None else "Citation générée")


@tracked("birthday_check")
async def _birthday_job():
    """Anniversaires des contacts du jour."""
    if not config.RITUALS_ENABLED:
        return skipped("RITUALS_ENABLED=false")
    from scripts.rituals import check_birthdays

    result = await asyncio.to_thread(check_birthdays)
    return ok(result if result is not None else "Anniversaires vérifiés")


@tracked("coffee_break")
async def _coffee_break_job():
    """Alerte pause café si activité écran continue trop longue."""
    if not config.BREAK_ALERTS_ENABLED:
        return skipped("BREAK_ALERTS_ENABLED=false")
    from scripts.rituals import check_coffee_break

    result = await asyncio.to_thread(check_coffee_break)
    if result:
        return ok(result if isinstance(result, str) else "Pause proposée")
    return silent("aucune pause nécessaire")


@tracked("weekly_debrief")
async def _weekly_debrief_job():
    """Debrief hebdo vocal (dimanche soir)."""
    if not config.RITUALS_ENABLED:
        return skipped("RITUALS_ENABLED=false")
    from scripts.rituals import weekly_debrief

    result = await weekly_debrief()
    return ok(result if result is not None else "Débrief hebdo exécuté")


@tracked("mood_signal")
async def _mood_signal_job():
    """Signal comportemental quotidien (aucun diagnostic)."""
    if not config.MOOD_SIGNALS_ENABLED:
        return skipped("MOOD_SIGNALS_ENABLED=false")
    from scripts.rituals import compute_mood_signal

    result = await asyncio.to_thread(compute_mood_signal)
    return ok(result if result is not None else "Signal d'humeur calculé")


@tracked("jarvis_journal")
async def _jarvis_journal_job():
    """Entrée quotidienne du journal de JARVIS (point de vue majordome)."""
    if not config.JARVIS_JOURNAL_ENABLED:
        return skipped("JARVIS_JOURNAL_ENABLED=false")
    from scripts.jarvis_journal import generate_journal_entry

    result = await generate_journal_entry()
    return ok(result if result is not None else "Journal JARVIS généré")


@tracked("doomscroll_check")
async def _doomscroll_check_job():
    """Notifie une fois par jour si le temps sur les apps à risque dépasse le seuil."""
    if not config.DOOMSCROLL_ALERTS_ENABLED:
        return skipped("DOOMSCROLL_ALERTS_ENABLED=false")
    from scripts.doomscroll_detector import check_and_notify_today

    result = await asyncio.to_thread(check_and_notify_today)
    if result:
        return ok(result if isinstance(result, str) else "Seuil doomscroll atteint")
    return silent("seuil non atteint")


@tracked("missed_opportunities")
async def _missed_opportunities_job():
    """Notifie une fois par semaine s'il existe des lieux favoris délaissés."""
    if not config.MISSED_OPPORTUNITIES_ENABLED:
        return skipped("MISSED_OPPORTUNITIES_ENABLED=false")
    from scripts.favorite_places import check_and_notify_weekly

    result = await asyncio.to_thread(check_and_notify_weekly)
    if result:
        return ok(result if isinstance(result, str) else "Lieux délaissés notifiés")
    return silent("aucun lieu délaissé")


@tracked("food_menu_refresh")
async def _food_menu_refresh_job():
    """Relève les menus des restaurants suivis, avant les pics de commande."""
    from integrations.uber_eats_settings import get_settings

    if not get_settings().menu_scrape_enabled:
        return skipped("relevé de menus désactivé")
    from scripts.food_menu_refresh import refresh_tracked_menus

    report = await refresh_tracked_menus()
    if not report.get("ok"):
        return silent(str(report.get("reason") or "aucun menu relevé"))
    counts = report.get("refreshed") or {}
    return ok(f"{len(counts)} menu(s) relevé(s) : {', '.join(sorted(counts))}")


@tracked("food_suggestions")
async def _food_suggestions_job():
    """Recalcule les préférences puis régénère les suggestions du jour."""
    from integrations.uber_eats_settings import get_settings

    if not get_settings().suggestions_enabled:
        return skipped("suggestions désactivées")
    from scripts.food_intelligence import generate_suggestions, learn_preferences

    await asyncio.to_thread(learn_preferences)
    report = await generate_suggestions()
    if not report.get("ok"):
        return silent(str(report.get("reason") or "aucune suggestion générée"))
    return ok(f"{report['created']} suggestion(s) prête(s)")


@tracked("food_delivery_tracking")
async def _food_delivery_tracking_job():
    """Relit l'avancement des livraisons en cours et pousse les changements."""
    from integrations.uber_eats_settings import get_settings

    if not get_settings().menu_scrape_enabled:
        return skipped("relevé de menus désactivé")
    from api.food_support import refresh_delivery_progress

    report = await refresh_delivery_progress()
    if not report["checked"]:
        return silent("aucune commande en cours")
    return ok(f"{report['updated']} mise(s) à jour sur {report['checked']} commande(s)")


@tracked("self_improvement")
async def _self_improvement_job():
    """Auto-amélioration : collecte de preuves → proposition → PR Cursor (pr_only)."""
    if not getattr(config, "SELF_IMPROVEMENT_ENABLED", False):
        return skipped("SELF_IMPROVEMENT_ENABLED=false")
    from scripts.self_improvement import propose_improvements

    auto = (
        getattr(config, "SELF_MODIFICATION_MODE", "pr_only") in ("pr_only", "auto_merge_low_risk")
        and getattr(config, "CURSOR_DELEGATION_ENABLED", True)
    )
    result = await propose_improvements(auto_delegate=auto)
    n = len(result.get("proposals") or [])
    if n:
        logger.info(
            "[scheduler] self_improvement : %d proposition(s), %d job(s)",
            n,
            len(result.get("jobs") or []),
        )
        return ok(result)
    return silent("aucune proposition")


@tracked("presence_tick")
async def _presence_tick_job():
    """Contrôle de départ : ferme la session après le timeout de silence."""
    if not config.PRESENCE_ENABLED:
        return skipped("PRESENCE_ENABLED=false")
    from scripts.presence import presence_detector

    result = await asyncio.to_thread(presence_detector.tick)
    if result:
        return ok(result if isinstance(result, str) else "Présence mise à jour")
    return silent("aucun changement de présence")


@tracked("streaming_binge")
async def _binge_job():
    """Commentaire sec si marathon streaming détecté."""
    if not config.BINGE_ALERTS_ENABLED:
        return skipped("BINGE_ALERTS_ENABLED=false")
    from scripts.rituals import check_streaming_binge

    result = await asyncio.to_thread(check_streaming_binge)
    if result:
        return ok(result if isinstance(result, str) else "Binge détecté")
    return silent("pas de binge")


@tracked("late_return")
async def _late_return_job():
    """« Rentrez, Monsieur » si dehors après LATE_RETURN_HOUR."""
    if not config.LATE_RETURN_ENABLED:
        return skipped("LATE_RETURN_ENABLED=false")
    from scripts.rituals import check_late_return

    result = await asyncio.to_thread(check_late_return)
    if result:
        return ok(result if isinstance(result, str) else "Retour tardif signalé")
    return silent("pas de retour tardif")


@tracked("meeting_tick")
async def _meeting_tick_job():
    """Clôt une réunion captée après le silence requis, puis la résume."""
    if not config.MEETING_CAPTURE_ENABLED:
        return skipped("MEETING_CAPTURE_ENABLED=false")
    from scripts.meeting import meeting_tracker, summarize_meeting

    meeting = meeting_tracker.tick()
    if meeting:
        await summarize_meeting(meeting)
        return ok("Réunion clôturée et résumée")
    return silent("aucune réunion à clôturer")


@tracked("commitments_extract")
async def _commitments_extract_job():
    """Extraction des engagements pris dans les messages du jour (22:40)."""
    if not config.RITUALS_ENABLED:
        return skipped("RITUALS_ENABLED=false")
    from scripts.commitments import extract_today_commitments

    result = await extract_today_commitments()
    return ok(result if result is not None else "Engagements extraits")


@tracked("duplicate_scan")
async def _duplicate_scan_job():
    """Scan hebdomadaire de code dupliqué (rapport seul, jamais de réécriture auto)."""
    if not config.DUPLICATE_SCAN_ENABLED:
        return skipped("DUPLICATE_SCAN_ENABLED=false")
    from scripts.duplicate_scanner import scan_and_report

    result = await asyncio.to_thread(scan_and_report)
    return ok(result if result is not None else "Scan doublons terminé")


@tracked("security_audit")
async def _security_audit_job():
    """Audit sécurité hebdomadaire (secrets, patterns dangereux — rapport)."""
    if not config.SECURITY_AUDIT_ENABLED:
        return skipped("SECURITY_AUDIT_ENABLED=false")
    from scripts.security_audit import scan_and_report

    result = await asyncio.to_thread(scan_and_report)
    return ok(result if result is not None else "Audit sécurité terminé")


@tracked("test_gen")
async def _test_gen_job():
    """Génération de tests manquants (opt-in, no-op si non configuré)."""
    if not config.AUTO_TEST_GEN_ENABLED:
        return skipped("AUTO_TEST_GEN_ENABLED=false")
    from scripts.test_coverage_scan import run_test_generation

    result = await run_test_generation()
    return ok(result if result is not None else "Génération de tests terminée")


@tracked("commitments_overdue")
async def _commitments_overdue_job():
    """Rappel sec des promesses ouvertes depuis plus de 3 jours (10:00)."""
    if not config.RITUALS_ENABLED:
        return skipped("RITUALS_ENABLED=false")
    from scripts.commitments import check_overdue_commitments_job

    result = await asyncio.to_thread(check_overdue_commitments_job)
    return ok(result if result is not None else "Engagements en retard vérifiés")


@tracked("fitness_reminders")
async def _fitness_reminders_job():
    """Relance vocalement séance et repas selon le programme SQLite."""
    if not config.FITNESS_REMINDERS_ENABLED:
        return skipped("FITNESS_REMINDERS_ENABLED=false")
    from scripts.fitness_reminders import run_fitness_reminders

    result = await asyncio.to_thread(run_fitness_reminders)
    if result:
        return ok(result if isinstance(result, str) else "Rappels fitness envoyés")
    return silent("aucun rappel fitness")


JOB_RUNNERS: dict[str, JobFn] = {
    "morning_briefing": scheduled_morning_briefing,
    "check_overdue": check_overdue_tasks,
    "fitness_reminders": _fitness_reminders_job,
    "location_analysis": _run_location_analysis_job,
    "relationship_alerts": _relationship_alerts_job,
    "evening_summary": scheduled_evening_summary,
    "weekly_summary": scheduled_weekly_summary,
    "relationship_analysis_daily": _relationship_analysis_daily_job,
    "db_backup": _db_backup_job,
    "db_maintenance": _db_maintenance_job,
    "llm_budget": _llm_budget_job,
    "daily_roast": _roast_job,
    "evening_debrief": _debrief_job,
    "daily_quote": _quote_job,
    "birthday_check": _birthday_job,
    "coffee_break": _coffee_break_job,
    "weekly_debrief": _weekly_debrief_job,
    "mood_signal": _mood_signal_job,
    "presence_tick": _presence_tick_job,
    "streaming_binge": _binge_job,
    "late_return": _late_return_job,
    "meeting_tick": _meeting_tick_job,
    "commitments_extract": _commitments_extract_job,
    "commitments_overdue": _commitments_overdue_job,
    "duplicate_scan": _duplicate_scan_job,
    "security_audit": _security_audit_job,
    "test_gen": _test_gen_job,
    "jarvis_journal": _jarvis_journal_job,
    "doomscroll_check": _doomscroll_check_job,
    "missed_opportunities": _missed_opportunities_job,
    "self_improvement": _self_improvement_job,
}


async def run_job_now(job_id: str) -> dict[str, Any]:
    """Exécute un job hors cron (manuel). Refusé pour les ticks fréquents."""
    spec = JOB_SPECS.get(job_id)
    if spec is None:
        raise KeyError(f"Job inconnu : {job_id}")
    if not spec.manual_run or spec.cadence == "frequent":
        raise PermissionError(
            f"Le job {job_id} est un tick fréquent — exécution manuelle refusée"
        )
    fn = JOB_RUNNERS.get(job_id)
    if fn is None:
        raise KeyError(f"Runner absent pour {job_id}")
    token = _current_trigger.set("manual")
    try:
        result = await fn()
    finally:
        _current_trigger.reset(token)
    if isinstance(result, dict):
        return {"job_id": job_id, **result}
    return {"job_id": job_id, "status": "ok", "output": str(result or "")}


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
        _fitness_reminders_job,
        CronTrigger(minute="*/30"),
        id="fitness_reminders",
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
    wh, wm = _parse_hh_mm(config.WEEKLY_DEBRIEF_TIME)
    scheduler.add_job(
        _weekly_debrief_job, CronTrigger(day_of_week="sun", hour=wh, minute=wm),
        id="weekly_debrief", replace_existing=True,
    )
    mh, mm = _parse_hh_mm(config.MOOD_SIGNAL_TIME)
    scheduler.add_job(
        _mood_signal_job, CronTrigger(hour=mh, minute=mm),
        id="mood_signal", replace_existing=True,
    )
    scheduler.add_job(
        _presence_tick_job, CronTrigger(minute="*/10"),
        id="presence_tick", replace_existing=True,
    )
    scheduler.add_job(
        _binge_job, CronTrigger(minute="*/30"),
        id="streaming_binge", replace_existing=True,
    )
    scheduler.add_job(
        _late_return_job, CronTrigger(hour="0-3,22-23", minute="*/30"),
        id="late_return", replace_existing=True,
    )
    scheduler.add_job(
        _meeting_tick_job, CronTrigger(minute="*/5"),
        id="meeting_tick", replace_existing=True,
    )
    # Relevé avant les deux services : un menu de la veille proposerait des
    # plats retirés de la carte.
    scheduler.add_job(
        _food_menu_refresh_job, CronTrigger(hour="11,18", minute=10),
        id="food_menu_refresh", replace_existing=True,
    )
    scheduler.add_job(
        _food_suggestions_job, CronTrigger(hour="11,18", minute=40),
        id="food_suggestions", replace_existing=True,
    )
    scheduler.add_job(
        _food_delivery_tracking_job, CronTrigger(minute="*/5"),
        id="food_delivery_tracking", replace_existing=True,
    )
    scheduler.add_job(
        _commitments_extract_job, CronTrigger(hour=22, minute=40),
        id="commitments_extract", replace_existing=True,
    )
    scheduler.add_job(
        _commitments_overdue_job, CronTrigger(hour=10, minute=0),
        id="commitments_overdue", replace_existing=True,
    )
    scheduler.add_job(
        _duplicate_scan_job, CronTrigger(day_of_week="wed", hour=5, minute=0),
        id="duplicate_scan", replace_existing=True,
    )
    scheduler.add_job(
        _security_audit_job, CronTrigger(day_of_week="wed", hour=5, minute=15),
        id="security_audit", replace_existing=True,
    )
    scheduler.add_job(
        _test_gen_job, CronTrigger(day_of_week="sat", hour=5, minute=30),
        id="test_gen", replace_existing=True,
    )
    jh, jm = _parse_hh_mm(config.JARVIS_JOURNAL_TIME)
    scheduler.add_job(
        _jarvis_journal_job, CronTrigger(hour=jh, minute=jm),
        id="jarvis_journal", replace_existing=True,
    )
    scheduler.add_job(
        _doomscroll_check_job, CronTrigger(hour=22, minute=0),
        id="doomscroll_check", replace_existing=True,
    )
    scheduler.add_job(
        _missed_opportunities_job, CronTrigger(day_of_week="sun", hour=19, minute=0),
        id="missed_opportunities", replace_existing=True,
    )
    # Auto-amélioration : propositions basées sur preuves (PR only, jamais de
    # merge auto). SELF_IMPROVEMENT_SCHEDULE=weekly → dim 06:00 ; daily → 06:00.
    if getattr(config, "SELF_IMPROVEMENT_ENABLED", False):
        _si_schedule = str(getattr(config, "SELF_IMPROVEMENT_SCHEDULE", "weekly")).lower()
        _si_trigger = (
            CronTrigger(hour=6, minute=0)
            if _si_schedule == "daily"
            else CronTrigger(day_of_week="sun", hour=6, minute=0)
        )
        scheduler.add_job(
            _self_improvement_job, _si_trigger,
            id="self_improvement", replace_existing=True,
        )

    logger.info(
        "[scheduler] %d jobs enregistrés (briefing %02d:%02d, résumé soir %02d:%02d, "
        "hebdo dim 20:00, overdue chaque heure, analyse géo 23:00, "
        "alertes relationnelles /6h, analyse relationnelle 3:00, "
        "backup 4:15, maintenance dim 4:45, budget LLM 21:30, "
        "roast %s, debrief %s, citation %s, anniversaires %s, pause café /20min 9-22h, "
        "debrief hebdo dim %s, signal mood %s, présence /10min, "
        "scan doublons mer 5:00, audit sécurité mer 5:15, génération tests sam 5:30, "
        "journal JARVIS %s, fitness /30min, doomscroll 22:00, lieux délaissés dim 19:00)",
        len(scheduler.get_jobs()), h, m, eh, em,
        config.ROAST_TIME, config.DEBRIEF_TIME, config.QUOTE_TIME, config.BIRTHDAY_CHECK_TIME,
        config.WEEKLY_DEBRIEF_TIME, config.MOOD_SIGNAL_TIME, config.JARVIS_JOURNAL_TIME,
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
