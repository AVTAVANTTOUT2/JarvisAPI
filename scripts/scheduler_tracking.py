"""Catalogue et suivi des jobs APScheduler (page /scheduler)."""

from __future__ import annotations

import contextvars
import json
import logging
from dataclasses import dataclass
from datetime import datetime, time
from functools import wraps
from typing import Any, Awaitable, Callable, Literal

import config
from database.scheduler_runs import finish_run, start_run
from database.time_buckets import local_datetime

logger = logging.getLogger(__name__)

Cadence = Literal["daily", "frequent", "weekly"]
Trigger = Literal["cron", "manual"]

_current_trigger: contextvars.ContextVar[Trigger] = contextvars.ContextVar(
    "scheduler_trigger", default="cron"
)


@dataclass(frozen=True)
class JobSpec:
    """Métadonnée UI + politique d'exécution manuelle."""

    job_id: str
    title: str
    description: str
    cadence: Cadence
    schedule_label: str
    group: str  # daily | frequent | weekly
    enabled_flag: str | None = None  # nom d'attribut config, None = toujours on
    manual_run: bool = True


def _flag(name: str) -> bool:
    return bool(getattr(config, name, False))


def _hhmm(attr: str, default: str) -> str:
    value = str(getattr(config, attr, default) or default).strip()
    return value or default


JOB_SPECS: dict[str, JobSpec] = {
    "relationship_analysis_daily": JobSpec(
        "relationship_analysis_daily",
        "Analyse relationnelle iMessage",
        "Met à jour les tendances et les échanges récents.",
        "daily",
        "03:00",
        "daily",
        None,
    ),
    "db_backup": JobSpec(
        "db_backup",
        "Sauvegarde SQLite",
        "Crée une sauvegarde chiffrée et applique la rotation configurée.",
        "daily",
        "04:15",
        "daily",
        "BACKUP_ENABLED",
    ),
    "daily_quote": JobSpec(
        "daily_quote",
        "Citation du jour",
        "Prépare la citation ironique affichée sur le tableau de bord TV.",
        "daily",
        _hhmm("QUOTE_TIME", "07:00"),
        "daily",
        "RITUALS_ENABLED",
    ),
    "morning_briefing": JobSpec(
        "morning_briefing",
        "Briefing du matin",
        "Rassemble agenda, tâches, météo et informations importantes.",
        "daily",
        _hhmm("MORNING_BRIEFING_TIME", "07:30"),
        "daily",
        None,
    ),
    "birthday_check": JobSpec(
        "birthday_check",
        "Anniversaires",
        "Vérifie les anniversaires présents dans les fiches contacts.",
        "daily",
        _hhmm("BIRTHDAY_CHECK_TIME", "08:00"),
        "daily",
        "RITUALS_ENABLED",
    ),
    "commitments_overdue": JobSpec(
        "commitments_overdue",
        "Engagements en attente",
        "Rappelle les promesses ouvertes depuis plus de trois jours.",
        "daily",
        "10:00",
        "daily",
        "RITUALS_ENABLED",
    ),
    "daily_roast": JobSpec(
        "daily_roast",
        "Revue des tâches",
        "Signale, sur le ton de JARVIS, les tâches prévues mais non terminées.",
        "daily",
        _hhmm("ROAST_TIME", "18:30"),
        "daily",
        "RITUALS_ENABLED",
    ),
    "llm_budget": JobSpec(
        "llm_budget",
        "Budget LLM",
        "Contrôle la consommation mensuelle et les seuils d'alerte.",
        "daily",
        "21:30",
        "daily",
        None,
    ),
    "evening_debrief": JobSpec(
        "evening_debrief",
        "Débrief du soir",
        "Résume la journée, les réussites et les éléments manqués.",
        "daily",
        _hhmm("DEBRIEF_TIME", "21:45"),
        "daily",
        "RITUALS_ENABLED",
    ),
    "evening_summary": JobSpec(
        "evening_summary",
        "Résumé du soir",
        "Génère le résumé du soir.",
        "daily",
        _hhmm("EVENING_SUMMARY_TIME", "22:00"),
        "daily",
        None,
    ),
    "doomscroll_check": JobSpec(
        "doomscroll_check",
        "Temps d'écran / doomscroll",
        "Vérifie un éventuel doomscrolling.",
        "daily",
        "22:00",
        "daily",
        None,
    ),
    "commitments_extract": JobSpec(
        "commitments_extract",
        "Extraction des engagements",
        "Repère les promesses prises dans les messages de la journée.",
        "daily",
        "22:40",
        "daily",
        "RITUALS_ENABLED",
    ),
    "location_analysis": JobSpec(
        "location_analysis",
        "Analyse des déplacements",
        "Met à jour lieux, visites, trajets et habitudes géographiques.",
        "daily",
        "23:00",
        "daily",
        "LOCATION_TRACKING",
    ),
    "mood_signal": JobSpec(
        "mood_signal",
        "Signal d'humeur",
        "Calcule un signal comportemental discret, sans diagnostic médical.",
        "daily",
        _hhmm("MOOD_SIGNAL_TIME", "23:15"),
        "daily",
        None,
    ),
    "jarvis_journal": JobSpec(
        "jarvis_journal",
        "Journal de JARVIS",
        "Produit une courte entrée récapitulative (si activé).",
        "daily",
        _hhmm("JARVIS_JOURNAL_TIME", "23:50"),
        "daily",
        "JARVIS_JOURNAL_ENABLED",
    ),
    "meeting_tick": JobSpec(
        "meeting_tick",
        "Fin de réunion captée",
        "Détecte la fin d'une réunion captée et lance son résumé.",
        "frequent",
        "Toutes les 5 minutes",
        "frequent",
        "MEETING_CAPTURE_ENABLED",
        manual_run=False,
    ),
    "presence_tick": JobSpec(
        "presence_tick",
        "Présence bureau",
        "Vérifie la présence et clôt une session après une longue période de silence.",
        "frequent",
        "Toutes les 10 minutes",
        "frequent",
        "PRESENCE_ENABLED",
        manual_run=False,
    ),
    "coffee_break": JobSpec(
        "coffee_break",
        "Pause écran",
        "Propose une pause après une activité écran continue trop longue.",
        "frequent",
        "Toutes les 20 minutes, 09:00–22:40",
        "frequent",
        None,
        manual_run=False,
    ),
    "streaming_binge": JobSpec(
        "streaming_binge",
        "Sessions de streaming",
        "Vérifie les longues sessions de streaming.",
        "frequent",
        "Toutes les 30 minutes",
        "frequent",
        None,
        manual_run=False,
    ),
    "fitness_reminders": JobSpec(
        "fitness_reminders",
        "Rappels fitness",
        "Rappels liés au module fitness.",
        "frequent",
        "Toutes les 30 minutes",
        "frequent",
        "FITNESS_REMINDERS_ENABLED",
        manual_run=False,
    ),
    "late_return": JobSpec(
        "late_return",
        "Retour tardif",
        "Peut signaler un retour tardif selon la localisation.",
        "frequent",
        "Toutes les 30 minutes, 22:00–03:30",
        "frequent",
        "LATE_RETURN_ENABLED",
        manual_run=False,
    ),
    "check_overdue": JobSpec(
        "check_overdue",
        "Tâches en retard",
        "Recherche les tâches dont l'échéance est dépassée.",
        "frequent",
        "Toutes les heures",
        "frequent",
        "DESKTOP_NOTIFICATIONS",
        manual_run=False,
    ),
    "relationship_alerts": JobSpec(
        "relationship_alerts",
        "Alertes relationnelles",
        "Recherche les alertes relationnelles utiles.",
        "frequent",
        "Toutes les 6 heures",
        "frequent",
        None,
        manual_run=False,
    ),
    "duplicate_scan": JobSpec(
        "duplicate_scan",
        "Code dupliqué",
        "Rapport sur le code dupliqué, sans réécriture automatique.",
        "weekly",
        "Mercredi 05:00",
        "weekly",
        "DUPLICATE_SCAN_ENABLED",
    ),
    "security_audit": JobSpec(
        "security_audit",
        "Audit de sécurité",
        "Audit de sécurité du dépôt.",
        "weekly",
        "Mercredi 05:15",
        "weekly",
        "SECURITY_AUDIT_ENABLED",
    ),
    "test_gen": JobSpec(
        "test_gen",
        "Tests manquants",
        "Propose via une PR Cursor les tests manquants.",
        "weekly",
        "Samedi 05:30",
        "weekly",
        "AUTO_TEST_GEN_ENABLED",
    ),
    "db_maintenance": JobSpec(
        "db_maintenance",
        "Maintenance SQLite",
        "Purge de rétention et optimisation de SQLite.",
        "weekly",
        "Dimanche 04:45",
        "weekly",
        None,
    ),
    "self_improvement": JobSpec(
        "self_improvement",
        "Auto-amélioration",
        "Recherche d'améliorations et proposition de PR si activée.",
        "weekly",
        "Dimanche 06:00",
        "weekly",
        "SELF_IMPROVEMENT_ENABLED",
    ),
    "missed_opportunities": JobSpec(
        "missed_opportunities",
        "Lieux délaissés",
        "Recherche de lieux favoris délaissés.",
        "weekly",
        "Dimanche 19:00",
        "weekly",
        None,
    ),
    "weekly_summary": JobSpec(
        "weekly_summary",
        "Résumé mémoire de la semaine",
        "Résumé mémoire de la semaine.",
        "weekly",
        "Dimanche 20:00",
        "weekly",
        None,
    ),
    "weekly_debrief": JobSpec(
        "weekly_debrief",
        "Débrief hebdomadaire vocal",
        "Débrief hebdomadaire vocal.",
        "weekly",
        f"Dimanche {_hhmm('WEEKLY_DEBRIEF_TIME', '21:00')}",
        "weekly",
        "RITUALS_ENABLED",
    ),
}


def job_enabled(spec: JobSpec) -> bool:
    if spec.enabled_flag is None:
        return True
    return _flag(spec.enabled_flag)


def skipped(reason: str) -> dict[str, str]:
    return {"status": "skipped", "output": reason}


def silent(message: str = "aucune action") -> dict[str, str]:
    return {"status": "silent", "output": message}


def ok(output: str = "") -> dict[str, str]:
    return {"status": "ok", "output": output}


def err(message: str) -> dict[str, str]:
    return {"status": "error", "output": message}


def _normalize_result(result: Any) -> tuple[str, str]:
    if result is None:
        return "ok", ""
    if isinstance(result, dict):
        status = str(result.get("status") or "ok")
        raw = result.get("output")
        if raw is None and "report" in result:
            raw = result["report"]
        if isinstance(raw, str):
            output = raw
        elif raw is None:
            output = ""
        else:
            output = json.dumps(raw, ensure_ascii=False, default=str)
        return status, output
    return "ok", str(result)


def tracked(job_id: str) -> Callable[[Callable[..., Awaitable[Any]]], Callable[..., Awaitable[Any]]]:
    """Décorateur : journalise chaque exécution dans ``scheduler_job_runs``."""

    def decorator(fn: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
        @wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            trigger = _current_trigger.get()
            run_id = start_run(job_id, trigger=trigger)
            try:
                result = await fn(*args, **kwargs)
                status, output = _normalize_result(result)
                finish_run(run_id, status=status, output=output)
                return result
            except Exception as exc:
                logger.exception("[scheduler] %s : %s", job_id, exc)
                finish_run(run_id, status="error", output="", error=str(exc))
                return err(str(exc))

        wrapper._scheduler_job_id = job_id  # type: ignore[attr-defined]
        return wrapper

    return decorator


def parse_hh_mm(value: str, default: tuple[int, int] = (7, 30)) -> tuple[int, int]:
    parts = (value or "").strip().split(":")
    try:
        hour = max(0, min(23, int(parts[0])))
        minute = max(0, min(59, int(parts[1]) if len(parts) > 1 else 0))
        return hour, minute
    except Exception:
        return default


def expected_today(spec: JobSpec, now: datetime | None = None) -> bool:
    """True si le créneau prévu pour aujourd'hui est déjà passé (jobs daily)."""
    local = local_datetime(now)
    if spec.cadence == "frequent":
        return True
    if spec.cadence == "weekly":
        # Heuristique : label commence par le jour FR ; sinon on ne force pas « manqué ».
        weekday = local.weekday()  # 0=lun … 6=dim
        label = spec.schedule_label.lower()
        day_map = {
            "lundi": 0,
            "mardi": 1,
            "mercredi": 2,
            "jeudi": 3,
            "vendredi": 4,
            "samedi": 5,
            "dimanche": 6,
        }
        matched = next((v for k, v in day_map.items() if label.startswith(k)), None)
        if matched is None:
            return False
        if weekday != matched:
            return False
        # Heure dans le label si présente (HH:MM en fin)
        for token in label.replace(",", " ").split():
            if ":" in token and token[0].isdigit():
                h, m = parse_hh_mm(token, (0, 0))
                return local.time() >= time(h, m)
        return True

    # daily — schedule_label souvent "HH:MM" ou valeur config
    label = spec.schedule_label.strip()
    if len(label) >= 4 and label[0].isdigit() and ":" in label:
        h, m = parse_hh_mm(label)
        return local.time() >= time(h, m)
    return True


def derive_today_status(
    *,
    spec: JobSpec,
    enabled: bool,
    today_count: int,
    today_ok: int,
    today_error: int,
    last_status: str | None,
) -> str:
    """Statut synthétique du jour pour l'UI."""
    if not enabled:
        return "disabled"
    if today_error > 0 and today_ok == 0 and today_count > 0:
        return "error"
    if today_error > 0:
        return "error"
    if today_ok > 0 or (last_status == "ok" and today_count > 0):
        return "ok"
    if today_count > 0 and last_status == "skipped":
        return "skipped"
    if today_count > 0 and last_status == "silent":
        return "silent"
    if today_count > 0:
        return last_status or "ok"
    if expected_today(spec):
        return "missed"
    return "pending"
