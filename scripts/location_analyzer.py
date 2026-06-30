"""Analyse quotidienne des habitudes géographiques (DeepSeek) — patterns + faits."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

import config
import llm
from database import add_fact, create_notification
from database.location_helpers import (
    get_active_location_patterns,
    get_all_places,
    get_today_visits,
    visits_summary_last_days,
)

logger = logging.getLogger(__name__)

JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*\n(.*?)\n```", re.DOTALL)
PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "location_analyzer.txt"


def _load_prompt_template() -> str:
    if PROMPT_PATH.exists():
        return PROMPT_PATH.read_text(encoding="utf-8")
    logger.warning("[location_analyzer] Prompt absent : %s", PROMPT_PATH)
    return ""


def _parse_json(raw: str) -> dict | None:
    if not raw:
        return None
    raw = raw.strip()
    match = JSON_BLOCK_RE.search(raw)
    payload = match.group(1) if match else raw
    if not payload.startswith("{"):
        start = payload.find("{")
        end = payload.rfind("}")
        if start != -1 and end != -1 and end > start:
            payload = payload[start : end + 1]
    try:
        out = json.loads(payload)
        return out if isinstance(out, dict) else None
    except json.JSONDecodeError as e:
        logger.warning("[location_analyzer] JSON invalide : %s", e)
        return None


def _fmt_visits_short(visits: list[dict], limit: int = 80) -> str:
    lines = []
    for v in visits[:limit]:
        pn = v.get("place_name") or "?"
        arr = v.get("arrived_at") or ""
        dep = v.get("departed_at") or ""
        dur = v.get("duration_min")
        lines.append(f"- {pn} | arrivée {arr} | départ {dep} | durée_min {dur}")
    if len(visits) > limit:
        lines.append(f"... ({len(visits) - limit} autres)")
    return "\n".join(lines) if lines else "(aucune)"


class LocationAnalyzer:
    async def run_daily_analysis(self) -> None:
        if not getattr(config, "LOCATION_TRACKING", True):
            logger.info("[location_analyzer] Désactivé (LOCATION_TRACKING=false)")
            return

        tpl = _load_prompt_template()
        if not tpl:
            return

        places = get_all_places()
        visits_30 = visits_summary_last_days(30)
        today = get_today_visits()

        places_txt = "\n".join(
            f"- {p.get('name')} ({p.get('category')}) — visites détectées: {p.get('visit_count', 0)}"
            for p in places
        ) or "(aucun lieu nommé)"

        prompt = (
            tpl.replace("{{user_name}}", config.USER_NAME)
            .replace("{{places_list}}", places_txt)
            .replace("{{visits_summary}}", _fmt_visits_short(visits_30, 120))
            .replace("{{today_visits}}", _fmt_visits_short(today, 40))
        )

        try:
            result = await llm.chat(
                [{"role": "user", "content": prompt}],
                model=config.DEEPSEEK_FAST_MODEL,
                system="Tu réponds uniquement en JSON valide, sans texte autour.",
                max_tokens=2048,
                temperature=0.2,
                use_cache=False,
            )
        except Exception as e:
            logger.exception("[location_analyzer] llm.chat : %s", e)
            return

        raw = result.get("content") or ""
        data = _parse_json(raw)
        if not data:
            logger.warning("[location_analyzer] Pas de JSON exploitable")
            return

        from database.location_helpers import add_location_pattern

        for r in data.get("routines_detected") or []:
            if not isinstance(r, dict):
                continue
            desc = (r.get("pattern") or "").strip()
            if not desc:
                continue
            day = (r.get("day") or "").strip()
            full = f"{day}: {desc}" if day else desc
            try:
                add_location_pattern("routine", full[:500], None)
            except Exception as e:
                logger.warning("[location_analyzer] add_location_pattern routine : %s", e)

        for fact_text in data.get("suggestions") or []:
            if isinstance(fact_text, str) and fact_text.strip():
                try:
                    add_fact("location", fact_text.strip()[:500], source="location_analyzer", confidence="medium")
                except Exception as e:
                    logger.warning("[location_analyzer] add_fact : %s", e)

        for an in data.get("anomalies") or []:
            if not isinstance(an, dict):
                continue
            desc = (an.get("description") or "").strip()
            if not desc:
                continue
            atype = (an.get("type") or "other").strip()
            if atype not in (
                "routine",
                "absence",
                "new_place",
                "frequency_change",
                "timing_change",
                "unusual_visit",
                "long_stay",
                "short_stay",
            ):
                atype = "unusual_visit"
            try:
                add_location_pattern(atype, desc[:500], None)
            except Exception as e:
                logger.warning("[location_analyzer] pattern anomalie : %s", e)

            if atype in ("absence", "unusual_visit", "timing_change") and config.DESKTOP_NOTIFICATIONS:
                try:
                    create_notification(
                        source="location",
                        title="JARVIS — Localisation",
                        content=desc[:500],
                        priority="medium",
                    )
                except Exception as e:
                    logger.warning("[location_analyzer] create_notification : %s", e)
                try:
                    from integrations.notifications_macos import mac_notifier

                    await mac_notifier.notify(
                        title="JARVIS — Habitudes géo",
                        message=desc[:180],
                        sound=config.NOTIFICATION_SOUND or "Glass",
                    )
                except Exception as e:
                    logger.debug("[location_analyzer] mac notify : %s", e)

        logger.info(
            "[location_analyzer] Terminé (patterns actifs en DB : %s)",
            len(get_active_location_patterns()),
        )


location_analyzer = LocationAnalyzer()


async def run_location_analysis() -> None:
    await location_analyzer.run_daily_analysis()
