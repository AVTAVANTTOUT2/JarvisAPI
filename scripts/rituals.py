"""Rituels quotidiens JARVIS — roast, debrief, citation, score, anniversaires, pause.

Ton : majordome britannique sec, zéro emoji, zéro complaisance. Chaque rituel
est stocké dans ``daily_rituals`` (une ligne par date), notifié dans l'UI et,
si ``RITUALS_TTS`` est actif, prononcé par le daemon (hors heures calmes —
le daemon coupe déjà la voix dans la plage calme).

Appelés par le scheduler ; déclenchables à la main via les endpoints
``POST /api/rituals/{roast|debrief|quote}/run``.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

import config
import llm
from database import (
    create_notification,
    create_task,
    get_daily_ritual,
    get_db,
    get_todays_birthdays,
    set_daily_ritual,
)

logger = logging.getLogger(__name__)

_PERSONA_RULES = (
    "Tu es JARVIS, majordome IA britannique. Ton sec, pince-sans-rire, "
    "vouvoiement 'Monsieur'. INTERDIT : emoji, exclamation, flatterie, "
    "conseils de coach. Réponds en français."
)

# Citations de secours si l'API LLM est indisponible — le rituel ne saute jamais.
_FALLBACK_QUOTES = [
    "La procrastination est un art, Monsieur. Vous en êtes le conservateur de musée.",
    "Rome ne s'est pas faite en un jour. À ce rythme, votre liste de tâches non plus.",
    "L'espoir n'est pas une stratégie, Monsieur. J'ai vérifié.",
    "Votre potentiel est immense. Il serait temps de le déranger.",
    "Demain est un autre jour. C'est précisément le problème.",
]


def _speak(text: str, emotion: str = "neutral") -> None:
    """Pousse le texte dans la file TTS du daemon (best-effort, jamais bloquant).

    Muet en mode « silence total sauf feu » — les rituels ne sont jamais vitaux.
    """
    if not config.RITUALS_TTS or not text:
        return
    try:
        from database import is_dnd_active

        if is_dnd_active():
            logger.info("[rituals] DND actif — voix coupée : %s", text[:50])
            return
    except Exception:
        pass
    try:
        from scripts.jarvis_daemon import daemon

        daemon.tts_queue.put_nowait((text, emotion))
    except Exception as e:
        logger.debug("[rituals] TTS indisponible : %s", e)


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


# ═══════════════════════════════════════════════════════════
# 1. Roast quotidien — tâches non faites, ton sec
# ═══════════════════════════════════════════════════════════

def _pending_tasks_snapshot() -> tuple[list[dict], list[dict]]:
    """(tâches en retard, tâches en attente) — listes de dicts légers."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    with get_db() as conn:
        rows = conn.execute(
            """SELECT title, priority, due_date, category FROM tasks
               WHERE status != 'done' ORDER BY due_date IS NULL, due_date ASC LIMIT 30"""
        ).fetchall()
    overdue, pending = [], []
    for r in rows:
        d = dict(r)
        if d.get("due_date") and str(d["due_date"])[:16] < now:
            overdue.append(d)
        else:
            pending.append(d)
    return overdue, pending


async def daily_roast() -> dict:
    """Critique sèche des tâches non faites. Une par jour, pas de pitié."""
    overdue, pending = _pending_tasks_snapshot()

    if not overdue and not pending:
        text = "Aucune tâche en souffrance aujourd'hui, Monsieur. C'est inhabituel au point d'être suspect."
    else:
        lines = [f"- {t['title']} (échéance dépassée : {t['due_date']})" for t in overdue]
        lines += [f"- {t['title']}" for t in pending[:10]]
        try:
            result = await llm.chat(
                messages=[{"role": "user", "content": (
                    f"Tâches en retard : {len(overdue)}. Tâches en attente : {len(pending)}.\n"
                    + "\n".join(lines)
                )}],
                model=config.DEEPSEEK_FAST_MODEL,
                system=(
                    _PERSONA_RULES + " Rédige un roast de 2 à 3 phrases sur ces tâches "
                    "non faites. Cite au moins une tâche précise. Sec, ironique, "
                    "factuel. Pas de liste, pas de conseil, pas de morale finale."
                ),
                max_tokens=150,
                temperature=0.8,
            )
            text = result["content"].strip()
        except Exception as e:
            logger.warning("[rituals] roast LLM indisponible : %s", e)
            text = (
                f"{len(overdue)} tâche(s) en retard et {len(pending)} en attente, Monsieur. "
                "Je m'abstiendrai de commenter. Le silence est parfois plus éloquent."
            )

    set_daily_ritual(_today(), "roast", text)
    create_notification(source="system", title="Roast du jour", content=text, priority="low")
    _speak(text, emotion="amused")
    logger.info("[rituals] roast : %s", text[:80])
    return {"roast": text, "overdue": len(overdue), "pending": len(pending)}


# ═══════════════════════════════════════════════════════════
# 2. Debrief du soir — résumé journée + ratés, voix concerned
# ═══════════════════════════════════════════════════════════

def _day_snapshot() -> dict:
    """Chiffres bruts de la journée pour le debrief (SQL pur, zéro LLM)."""
    today = _today()
    with get_db() as conn:
        msg = conn.execute(
            "SELECT COUNT(*) FROM messages WHERE DATE(created_at) = ?", (today,)
        ).fetchone()[0]
        done = [dict(r) for r in conn.execute(
            "SELECT title FROM tasks WHERE status = 'done' AND DATE(completed_at) = ?", (today,))]
        apps = [dict(r) for r in conn.execute(
            """SELECT app, SUM(duration_seconds) AS s FROM app_usage
               WHERE date = ? GROUP BY app ORDER BY s DESC LIMIT 3""", (today,))]
    overdue, pending = _pending_tasks_snapshot()
    return {
        "messages": msg,
        "tasks_done": done,
        "overdue": overdue,
        "pending": pending,
        "top_apps": apps,
    }


async def evening_debrief() -> dict:
    """Bilan de journée : accompli, raté, à surveiller. Émotion concerned."""
    snap = _day_snapshot()
    apps_txt = ", ".join(
        f"{a['app']} ({int(a['s'] // 60)} min)" for a in snap["top_apps"]
    ) or "(pas de suivi écran)"
    facts = (
        f"Messages échangés avec JARVIS : {snap['messages']}\n"
        f"Tâches terminées aujourd'hui : {[t['title'] for t in snap['tasks_done']] or 'aucune'}\n"
        f"Tâches en retard : {[t['title'] for t in snap['overdue']] or 'aucune'}\n"
        f"Tâches en attente : {len(snap['pending'])}\n"
        f"Applications principales : {apps_txt}"
    )
    try:
        result = await llm.chat(
            messages=[{"role": "user", "content": facts}],
            model=config.DEEPSEEK_MAIN_MODEL,
            system=(
                _PERSONA_RULES + " Rédige le debrief du soir en 4 phrases max : "
                "ce qui a été accompli, ce qui a été raté ou repoussé, et un point "
                "de vigilance pour demain. Ton concerné mais mesuré, pas dramatique."
            ),
            max_tokens=250,
            temperature=0.5,
        )
        text = result["content"].strip()
    except Exception as e:
        logger.warning("[rituals] debrief LLM indisponible : %s", e)
        text = (
            f"Journée close, Monsieur : {len(snap['tasks_done'])} tâche(s) terminée(s), "
            f"{len(snap['overdue'])} en retard, {len(snap['pending'])} en attente. "
            "Les chiffres parlent d'eux-mêmes."
        )

    set_daily_ritual(_today(), "debrief", text)
    create_notification(source="system", title="Debrief du soir", content=text, priority="low")
    _speak(text, emotion="concerned")
    logger.info("[rituals] debrief généré")

    # Le score du jour est figé au debrief (le widget TV le lit en base).
    compute_productivity_score(persist=True)
    try:
        from scripts.day_scoring import score_day

        score_day(persist=True)
    except Exception as e:
        logger.debug("[rituals] score_day indisponible : %s", e)
    return {"debrief": text, **{k: len(v) if isinstance(v, list) else v for k, v in snap.items()}}


# ═══════════════════════════════════════════════════════════
# 3. Score productivité hebdo — déterministe, ton stoïque
# ═══════════════════════════════════════════════════════════

def compute_productivity_score(persist: bool = False) -> dict:
    """Score 0-100 sur les 7 derniers jours. Formule fixe, zéro LLM.

    50 + 8 x tâches terminées (7 j) − 12 x tâches en retard, borné [0, 100].
    """
    week_start = (datetime.now().date() - timedelta(days=6)).isoformat()
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    with get_db() as conn:
        done = conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE status = 'done' AND DATE(completed_at) >= ?",
            (week_start,),
        ).fetchone()[0]
        overdue = conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE status != 'done' AND due_date IS NOT NULL "
            "AND substr(due_date, 1, 16) < ?",
            (now,),
        ).fetchone()[0]

    score = max(0, min(100, 50 + 8 * done - 12 * overdue))
    if score >= 80:
        label = "Acceptable, Monsieur."
    elif score >= 60:
        label = "Convenable."
    elif score >= 40:
        label = "Passable."
    else:
        label = "Préoccupant."

    detail = {"done_7d": done, "overdue": overdue, "label": label}
    if persist:
        import json as _json

        set_daily_ritual(_today(), "productivity_score", score)
        set_daily_ritual(_today(), "score_detail", _json.dumps(detail, ensure_ascii=False))
    return {"score": score, **detail}


# ═══════════════════════════════════════════════════════════
# 4. Anniversaires des contacts
# ═══════════════════════════════════════════════════════════

def check_birthdays() -> list[dict]:
    """Notifie les anniversaires du jour (une notification par contact et par an)."""
    found = get_todays_birthdays()
    year = datetime.now().year
    notified = []
    for p in found:
        title = f"Anniversaire de {p['name']} — {year}"
        with get_db() as conn:
            dup = conn.execute(
                "SELECT 1 FROM notifications WHERE title = ? LIMIT 1", (title,)
            ).fetchone()
        if dup:
            continue
        age_txt = ""
        bday = str(p.get("birthday") or "")
        if len(bday) == 10 and bday[:4].isdigit():
            age_txt = f" ({year - int(bday[:4])} ans)"
        content = (
            f"{p['name']}{age_txt} fête son anniversaire aujourd'hui, Monsieur. "
            "Un message serait de bon ton."
        )
        create_notification(source="relationship", title=title, content=content, priority="medium")
        try:
            create_task(
                title=f"Souhaiter l'anniversaire de {p['name']}",
                priority="medium",
                category="relation",
                due_date=datetime.now().strftime("%Y-%m-%d 20:00"),
            )
        except Exception as e:
            logger.debug("[rituals] tâche anniversaire : %s", e)
        _speak(content, emotion="warm")
        notified.append(p)
    if notified:
        logger.info("[rituals] anniversaires notifiés : %s", [p["name"] for p in notified])
    return notified


# ═══════════════════════════════════════════════════════════
# 6. Alerte pause café — activité écran continue
# ═══════════════════════════════════════════════════════════

def _continuous_screen_minutes(rows: list[str], gap_minutes: int) -> float:
    """Durée de la session d'activité continue se terminant au dernier point.

    ``rows`` : timestamps ISO croissants. Un trou > gap_minutes remet à zéro.
    """
    if not rows:
        return 0.0
    fmt = "%Y-%m-%d %H:%M:%S"
    start = prev = datetime.strptime(rows[0][:19], fmt)
    for ts in rows[1:]:
        cur = datetime.strptime(ts[:19], fmt)
        if (cur - prev).total_seconds() > gap_minutes * 60:
            start = cur
        prev = cur
    return (prev - start).total_seconds() / 60


def check_coffee_break() -> dict | None:
    """Alerte si l'écran est actif sans pause depuis BREAK_ALERT_MINUTES.

    Cooldown : pas deux alertes en moins de BREAK_COOLDOWN_MINUTES.
    Retourne le rapport si une alerte part, sinon None.
    """
    if config.BREAK_ALERT_MINUTES <= 0:
        return None
    lookback = datetime.now() - timedelta(minutes=config.BREAK_ALERT_MINUTES * 3)
    with get_db() as conn:
        rows = [r[0] for r in conn.execute(
            "SELECT created_at FROM screen_activity WHERE created_at >= ? ORDER BY created_at ASC",
            (lookback.strftime("%Y-%m-%d %H:%M:%S"),),
        )]
        last_alert = conn.execute(
            """SELECT created_at FROM notifications
               WHERE title = 'Pause café' ORDER BY created_at DESC LIMIT 1"""
        ).fetchone()

    minutes = _continuous_screen_minutes(rows, config.BREAK_GAP_MINUTES)
    if minutes < config.BREAK_ALERT_MINUTES:
        return None
    if last_alert:
        fmt = "%Y-%m-%d %H:%M:%S"
        elapsed = datetime.utcnow() - datetime.strptime(str(last_alert[0])[:19], fmt)
        if elapsed.total_seconds() < config.BREAK_COOLDOWN_MINUTES * 60:
            return None

    text = (
        f"{int(minutes)} minutes d'écran sans interruption, Monsieur. "
        "Même vos processeurs préférés ont un système de refroidissement. Un café s'impose."
    )
    create_notification(source="system", title="Pause café", content=text, priority="medium")
    _speak(text, emotion="serious")
    logger.info("[rituals] pause café — %d min continues", int(minutes))
    return {"continuous_minutes": round(minutes, 1)}


# ═══════════════════════════════════════════════════════════
# Détection binge streaming — commentaire sec, zéro jugement moral
# ═══════════════════════════════════════════════════════════

def check_streaming_binge() -> dict | None:
    """Commentaire sec si le streaming tourne sans pause depuis BINGE_ALERT_MINUTES.

    Détection via screen_activity (app ou activité contenant un nom de
    plateforme). Cooldown : un commentaire par tranche de 4 h.
    """
    if config.BINGE_ALERT_MINUTES <= 0:
        return None
    lookback = datetime.now() - timedelta(minutes=config.BINGE_ALERT_MINUTES * 3)
    like_clauses = " OR ".join(
        ["LOWER(COALESCE(app, '')) LIKE ? OR LOWER(COALESCE(activity, '')) LIKE ?"]
        * len(config.STREAMING_APPS)
    )
    params: list = []
    for name in config.STREAMING_APPS:
        params += [f"%{name}%", f"%{name}%"]
    with get_db() as conn:
        rows = [r[0] for r in conn.execute(
            f"""SELECT created_at FROM screen_activity
                WHERE created_at >= ? AND ({like_clauses})
                ORDER BY created_at ASC""",  # noqa: S608 — clauses générées, valeurs bindées
            [lookback.strftime("%Y-%m-%d %H:%M:%S"), *params],
        )]
        last_alert = conn.execute(
            """SELECT created_at FROM notifications
               WHERE title = 'Marathon streaming' ORDER BY created_at DESC LIMIT 1"""
        ).fetchone()

    minutes = _continuous_screen_minutes(rows, config.BINGE_GAP_MINUTES)
    if minutes < config.BINGE_ALERT_MINUTES:
        return None
    if last_alert:
        fmt = "%Y-%m-%d %H:%M:%S"
        elapsed = datetime.utcnow() - datetime.strptime(str(last_alert[0])[:19], fmt)
        if elapsed.total_seconds() < 4 * 3600:
            return None

    hours = minutes / 60
    text = (
        f"{hours:.1f} heures de streaming sans interruption, Monsieur. "
        "Je ne juge pas. Je comptabilise."
    )
    create_notification(source="system", title="Marathon streaming", content=text, priority="low")
    _speak(text, emotion="amused")
    logger.info("[rituals] binge streaming — %.0f min continues", minutes)
    return {"continuous_minutes": round(minutes, 1)}


# ═══════════════════════════════════════════════════════════
# Alerte trajet retour tard — GPS + heure
# ═══════════════════════════════════════════════════════════

_HOME_KEYWORDS = ("maison", "home", "domicile", "chez moi", "appart")


def check_late_return(now: datetime | None = None) -> dict | None:
    """« Rentrez, Monsieur » si position fraîche hors de chez soi après LATE_RETURN_HOUR.

    Une seule alerte par nuit (dédup sur la date de la nuit). L'alerte part en
    notification + iMessage (best-effort — c'est le seul canal qui le suit
    dehors). Rien ne part en mode silence total.
    """
    if not config.LATE_RETURN_ENABLED:
        return None
    now = now or datetime.now()
    if not (now.hour >= config.LATE_RETURN_HOUR or now.hour < 4):
        return None

    from database import is_dnd_active
    if is_dnd_active():
        return None

    from database.location_helpers import get_current_location
    loc = get_current_location()   # dernier point < 10 min, sinon None
    if not loc:
        return None
    place = (loc.get("place_name") or "").lower()
    if place and any(k in place for k in _HOME_KEYWORDS):
        return None
    if loc.get("place_id"):
        with get_db() as conn:
            cat = conn.execute(
                "SELECT category FROM places WHERE id = ?", (loc["place_id"],)
            ).fetchone()
        if cat and cat[0] == "home":
            return None

    # date de la nuit : après minuit, l'alerte appartient à la veille
    night = (now - timedelta(hours=4)).strftime("%Y-%m-%d")
    title = f"Retour tardif — {night}"
    with get_db() as conn:
        dup = conn.execute(
            "SELECT 1 FROM notifications WHERE title = ? LIMIT 1", (title,)
        ).fetchone()
    if dup:
        return None

    where = f" ({loc['place_name']})" if loc.get("place_name") else ""
    text = (
        f"Il est {now.strftime('%H:%M')} et vous n'êtes pas chez vous{where}, Monsieur. "
        "Rentrez. Demain existe, et il commence tôt."
    )
    create_notification(source="location", title=title, content=text, priority="medium")
    try:
        from integrations import imessage_bridge

        if imessage_bridge is not None and imessage_bridge.is_available():
            imessage_bridge._send_message(text)
    except Exception as e:
        logger.debug("[rituals] retour tardif iMessage : %s", e)
    logger.info("[rituals] alerte retour tardif (%s)", place or "lieu inconnu")
    return {"hour": now.strftime("%H:%M"), "place": loc.get("place_name")}


# ═══════════════════════════════════════════════════════════
# Debrief hebdo vocal — dimanche soir, bilan complet de semaine
# ═══════════════════════════════════════════════════════════

def _week_snapshot() -> dict:
    """Chiffres bruts des 7 derniers jours (SQL pur)."""
    week_start = (datetime.now().date() - timedelta(days=6)).isoformat()
    with get_db() as conn:
        done = [r[0] for r in conn.execute(
            "SELECT title FROM tasks WHERE status = 'done' AND DATE(completed_at) >= ?",
            (week_start,))]
        msg = conn.execute(
            "SELECT COUNT(*) FROM messages WHERE DATE(created_at) >= ?", (week_start,)
        ).fetchone()[0]
        voice = conn.execute(
            """SELECT COUNT(*) FROM messages m JOIN conversations c ON c.id = m.conversation_id
               WHERE c.agent = 'voice' AND DATE(m.created_at) >= ?""", (week_start,)
        ).fetchone()[0]
        apps = [dict(r) for r in conn.execute(
            """SELECT app, SUM(duration_seconds) AS s FROM app_usage
               WHERE date >= ? GROUP BY app ORDER BY s DESC LIMIT 5""", (week_start,))]
        moods = [dict(r) for r in conn.execute(
            """SELECT mood_score, energy_level FROM mood_log
               WHERE DATE(created_at) >= ?""", (week_start,))]
    overdue, pending = _pending_tasks_snapshot()
    return {
        "tasks_done": done,
        "overdue": overdue,
        "pending": pending,
        "messages": msg,
        "voice": voice,
        "top_apps": apps,
        "moods": moods,
    }


async def weekly_debrief() -> dict:
    """Bilan complet de la semaine, prononcé le dimanche soir.

    Ton mesuré : accompli / raté / tendance / cap pour la semaine suivante.
    Émotion vocale : concerned si le score est mauvais, neutral sinon.
    """
    snap = _week_snapshot()
    score = compute_productivity_score()
    apps_txt = ", ".join(
        f"{a['app']} ({int(a['s'] // 3600)}h{int(a['s'] % 3600 // 60):02d})"
        for a in snap["top_apps"][:3]
    ) or "(pas de suivi écran)"
    mood_txt = "aucun relevé"
    if snap["moods"]:
        avg = sum(m["mood_score"] or 0 for m in snap["moods"]) / len(snap["moods"])
        mood_txt = f"moyenne {avg:.1f}/10 sur {len(snap['moods'])} relevé(s)"
    facts = (
        f"Score productivité : {score['score']}/100 ({score['label']})\n"
        f"Tâches terminées ({len(snap['tasks_done'])}) : {snap['tasks_done'][:10]}\n"
        f"Tâches en retard : {[t['title'] for t in snap['overdue']] or 'aucune'}\n"
        f"Tâches en attente : {len(snap['pending'])}\n"
        f"Messages échangés : {snap['messages']} (dont {snap['voice']} en vocal)\n"
        f"Applications principales : {apps_txt}\n"
        f"Humeur : {mood_txt}"
    )
    try:
        result = await llm.chat(
            messages=[{"role": "user", "content": facts}],
            model=config.DEEPSEEK_MAIN_MODEL,
            system=(
                _PERSONA_RULES + " C'est le debrief du dimanche soir. Rédige le "
                "bilan complet de la semaine en 6 phrases max, destiné à être LU À "
                "VOIX HAUTE : phrases courtes, pas de liste, pas de chiffre inutile. "
                "Structure : ce qui a été accompli, ce qui a été raté, la tendance "
                "de la semaine, et UN cap clair pour la semaine prochaine."
            ),
            max_tokens=350,
            temperature=0.5,
        )
        text = result["content"].strip()
    except Exception as e:
        logger.warning("[rituals] debrief hebdo LLM indisponible : %s", e)
        text = (
            f"Semaine close, Monsieur : {len(snap['tasks_done'])} tâche(s) terminée(s), "
            f"{len(snap['overdue'])} en retard, score {score['score']} sur 100. "
            f"{score['label']}"
        )

    set_daily_ritual(_today(), "weekly_debrief", text)
    create_notification(source="system", title="Debrief de la semaine",
                        content=text, priority="low")
    _speak(text, emotion="concerned" if score["score"] < 40 else "neutral")
    logger.info("[rituals] debrief hebdo généré (score %d)", score["score"])
    return {"weekly_debrief": text, "score": score["score"]}


# ═══════════════════════════════════════════════════════════
# Mood tracking discret — signal comportemental, aucun diagnostic
# ═══════════════════════════════════════════════════════════

def compute_mood_signal(date: str | None = None) -> dict:
    """Signal quotidien déterministe depuis les patterns écran + messages.

    Aucun LLM, aucun diagnostic — des chiffres et des drapeaux factuels :
    - activite_nocturne : points d'activité écran entre 23h et 5h
    - hyperactivite / silence_inhabituel : volume de messages vs moyenne 14 j
    - marathon_ecran : plus de 10 h d'écran dans la journée
    Une notification discrète (priorité low) part seulement si un drapeau
    est levé, formulée comme une observation, jamais comme un diagnostic.
    """
    import json as _json

    day = date or _today()
    with get_db() as conn:
        msg_count = conn.execute(
            "SELECT COUNT(*) FROM messages WHERE DATE(created_at) = ? AND role = 'user'",
            (day,),
        ).fetchone()[0]
        avg_row = conn.execute(
            """SELECT AVG(c) FROM (
                   SELECT COUNT(*) AS c FROM messages
                   WHERE role = 'user' AND DATE(created_at) < ?
                     AND DATE(created_at) >= DATE(?, '-14 days')
                   GROUP BY DATE(created_at))""",
            (day, day),
        ).fetchone()
        msg_avg = float(avg_row[0] or 0.0)
        voice_count = conn.execute(
            """SELECT COUNT(*) FROM messages m JOIN conversations c ON c.id = m.conversation_id
               WHERE c.agent = 'voice' AND DATE(m.created_at) = ?""", (day,)
        ).fetchone()[0]
        screen_minutes = (conn.execute(
            "SELECT COALESCE(SUM(duration_seconds), 0) FROM app_usage WHERE date = ?", (day,)
        ).fetchone()[0] or 0) / 60
        late_night = conn.execute(
            """SELECT COUNT(*) FROM screen_activity
               WHERE DATE(created_at) = ?
                 AND (CAST(strftime('%H', created_at) AS INTEGER) >= 23
                      OR CAST(strftime('%H', created_at) AS INTEGER) < 5)""",
            (day,),
        ).fetchone()[0]

    deviation = None
    if msg_avg > 0:
        deviation = round((msg_count - msg_avg) / msg_avg * 100, 1)

    flags: list[str] = []
    if late_night >= 10:
        flags.append("activite_nocturne")
    if deviation is not None and msg_avg >= 5:
        if deviation >= 80:
            flags.append("hyperactivite")
        elif deviation <= -60:
            flags.append("silence_inhabituel")
    if screen_minutes > 600:
        flags.append("marathon_ecran")

    signal = {
        "date": day,
        "msg_count": msg_count,
        "msg_avg_14d": round(msg_avg, 1),
        "deviation_pct": deviation,
        "voice_count": voice_count,
        "screen_minutes": round(screen_minutes, 1),
        "late_night_points": late_night,
        "flags": _json.dumps(flags, ensure_ascii=False),
    }
    from database import upsert_mood_signal

    upsert_mood_signal(day, signal)

    if flags:
        labels = {
            "activite_nocturne": "activité nocturne inhabituelle",
            "hyperactivite": "volume d'échanges nettement au-dessus de votre moyenne",
            "silence_inhabituel": "journée nettement plus silencieuse que d'habitude",
            "marathon_ecran": "temps d'écran très élevé",
        }
        title = f"Signal du jour — {day}"
        with get_db() as conn:
            dup = conn.execute(
                "SELECT 1 FROM notifications WHERE title = ? LIMIT 1", (title,)
            ).fetchone()
        if not dup:
            content = (
                "Simple observation, Monsieur, pas un diagnostic : "
                + ", ".join(labels[f] for f in flags) + "."
            )
            create_notification(source="system", title=title, content=content, priority="low")
    signal["flags"] = flags
    return signal


# ═══════════════════════════════════════════════════════════
# 7. Citation ironique du jour
# ═══════════════════════════════════════════════════════════

async def daily_quote() -> dict:
    """Une ligne, zéro pitié. Stockée pour le widget TV."""
    existing = get_daily_ritual(_today())
    if existing and existing.get("quote"):
        return {"quote": existing["quote"], "cached": True}

    try:
        result = await llm.chat(
            messages=[{"role": "user", "content": "La citation ironique du jour."}],
            model=config.DEEPSEEK_FAST_MODEL,
            system=(
                _PERSONA_RULES + " Invente UNE citation ironique et mordante sur la "
                "productivité, la procrastination ou l'ambition. UNE seule ligne, "
                "20 mots max, pas de guillemets, pas d'attribution."
            ),
            max_tokens=60,
            temperature=1.0,
        )
        quote = result["content"].strip().strip('"').splitlines()[0]
    except Exception as e:
        logger.warning("[rituals] citation LLM indisponible : %s", e)
        idx = datetime.now().toordinal() % len(_FALLBACK_QUOTES)
        quote = _FALLBACK_QUOTES[idx]

    set_daily_ritual(_today(), "quote", quote)
    logger.info("[rituals] citation : %s", quote)
    return {"quote": quote, "cached": False}
