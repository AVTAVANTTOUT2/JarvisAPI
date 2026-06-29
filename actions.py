"""Exécution des actions vocales JARVIS.

Appelé par le handler WebSocket de main.py quand un agent inclut un bloc
```action {JSON} ``` dans sa réponse.

Chaque action type retourne un dict {"ok": bool, "message": str, ...}.
"""

import logging
import re
import time
import asyncio

logger = logging.getLogger(__name__)

_TERMINAL_CONFIRM_PATTERNS = [
    re.compile(r"\brm\b", re.IGNORECASE),
    re.compile(r"\bmv\b.*~/", re.IGNORECASE),
    re.compile(r"\bsudo\b", re.IGNORECASE),
    re.compile(r"\bbrew\s+uninstall\b", re.IGNORECASE),
]


async def execute_action(action: dict) -> dict:
    """Dispatch vers le handler correspondant au type d'action."""
    action_type = action.get("type", "")
    logger.info("[action] type=%s", action_type)
    out: dict
    started = time.perf_counter()
    try:
        if action_type == "task":
            out = await _action_task(action)
        elif action_type == "reminder":
            out = await _action_reminder(action)
        elif action_type == "mail":
            out = await _action_mail(action)
        elif action_type == "mail_read":
            out = await _action_mail_read()
        elif action_type == "weather":
            out = await _action_weather(action)
        elif action_type == "calendar":
            out = await _action_calendar(action)
        elif action_type == "calendar_create":
            out = await _action_calendar_create(action)
        elif action_type == "mood":
            out = await _action_mood(action)
        elif action_type == "note":
            out = await _action_note(action)
        elif action_type == "terminal":
            out = await _action_terminal(action)
        elif action_type == "open_app":
            out = await _action_open_app(action)
        elif action_type == "find_file":
            out = await _action_find_file(action)
        elif action_type == "clipboard":
            out = await _action_clipboard(action)
        elif action_type == "system_info":
            out = await _action_system_info(action)
        elif action_type == "name_place":
            out = await _action_name_place(action)
        elif action_type == "where_am_i":
            out = await _action_where_am_i(action)
        elif action_type == "day_route":
            out = await _action_day_route(action)
        elif action_type == "search_conversations":
            out = await _action_search_conversations(action)
        else:
            out = {"ok": False, "message": f"Type d'action inconnu : {action_type}"}
    except Exception as e:
        logger.exception("[action] execute_action(%s) exception : %s", action_type, e)
        out = {"ok": False, "message": f"Erreur interne : {e}"}
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    _schedule_action_log(
        agent="action_executor",
        action_type=action_type or "unknown",
        payload={"action": action, "result": out},
        status="success" if out.get("ok") else "error",
        execution_time_ms=elapsed_ms,
    )
    logger.info("[action] type=%s ok=%s", action_type, out.get("ok"))
    return out


def _schedule_action_log(
    *,
    agent: str,
    action_type: str,
    payload: dict,
    status: str,
    execution_time_ms: int | None = None,
) -> None:
    """Log non bloquant vers SQLite."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # Hors event-loop: fallback synchrone
        try:
            from database import log_llm_action
            log_llm_action(agent, action_type, payload, status, execution_time_ms)
        except Exception:
            logger.debug("[action-log] fallback sync failed", exc_info=True)
        return

    async def _runner() -> None:
        try:
            from database import log_llm_action
            await loop.run_in_executor(
                None,
                lambda: log_llm_action(agent, action_type, payload, status, execution_time_ms),
            )
        except Exception:
            logger.debug("[action-log] async failed", exc_info=True)

    asyncio.create_task(_runner())


# ── Implémentations ────────────────────────────────────────────────────────

async def _action_task(action: dict) -> dict:
    from database import create_task
    task_id = create_task(
        title=action["title"],
        priority=action.get("priority", "medium"),
        due_date=action.get("due_date"),
        category=action.get("category"),
    )
    logger.info("[action] Tâche créée id=%d : %s", task_id, action["title"])
    return {"ok": True, "message": f"Tâche créée : {action['title']}", "task_id": task_id}


async def _action_reminder(action: dict) -> dict:
    from database import create_task
    title = action.get("title", "Rappel")
    task_id = create_task(
        title=title,
        priority="high",
        due_date=action.get("date"),
        category="rappel",
    )
    logger.info("[action] Rappel créé id=%d : %s", task_id, title)
    return {"ok": True, "message": f"Rappel créé : {title}", "task_id": task_id}


async def _action_mail(action: dict) -> dict:
    # Le mail n'est PAS envoyé directement — on retourne le brouillon pour
    # que le frontend affiche un bouton "Envoyer" / "Annuler".
    return {
        "ok": True,
        "message": "Brouillon prêt — en attente de confirmation.",
        "draft": {
            "to": action.get("to", ""),
            "subject": action.get("subject", ""),
            "body": action.get("body", ""),
        },
    }


async def _action_mail_read() -> dict:
    """Lit les emails non lus depuis la DB (instantané, pas d'AppleScript).

    Les emails sont pré-traités par ``email_watcher`` à leur arrivée
    (contenu intégral + résumé DeepSeek stockés en DB).
    """
    from database import get_unread_emails_from_db, get_email_stats

    stats = get_email_stats()
    emails = get_unread_emails_from_db(limit=12)

    # Format pour le LLM : résumés prêts à l'emploi
    formatted = [
        {
            "from": e.get("sender", ""),
            "subject": e.get("subject", ""),
            "summary": e.get("summary", ""),
            "category": e.get("category", "info"),
            "priority": e.get("priority", "low"),
            "date": e.get("received_at", ""),
        }
        for e in emails
    ]

    return {
        "ok": True,
        "type": "mail_read",
        "stats": stats,
        "emails": formatted,
        "data": formatted,
    }


async def _action_weather(action: dict) -> dict:
    try:
        from integrations.weather import weather  # type: ignore
        if weather and hasattr(weather, "is_available") and weather.is_available():
            city = action.get("city") or "Lille"
            data = await weather.get_current(city)
            return {"ok": True, "weather": data}
        return {"ok": False, "message": "Météo non configurée (WEATHER_API_KEY manquant)."}
    except Exception as e:
        logger.warning("[action] weather : %s", e)
        return {"ok": False, "message": f"Erreur météo : {e}"}


async def _action_calendar(action: dict) -> dict:
    try:
        from integrations.calendar_api import calendar_client  # type: ignore
        if calendar_client and hasattr(calendar_client, "is_available") and calendar_client.is_available():
            if action.get("range") == "week":
                events = await calendar_client.get_week_events()
            else:
                events = await calendar_client.get_today_events()
            logger.info("[action] calendar ok (%s événements)", len(events or []))
            return {"ok": True, "events": events or []}
        return {"ok": False, "message": "Calendrier Apple (Calendar.app) non disponible."}
    except Exception as e:
        logger.exception("[action] calendar : %s", e)
        return {"ok": False, "message": f"Erreur agenda : {e}"}


async def _action_calendar_create(action: dict) -> dict:
    try:
        from integrations.calendar_api import calendar_client

        if not calendar_client or not calendar_client.is_available():
            return {
                "ok": False,
                "message": "Calendar non disponible — ouvre Calendar.app et vérifie les permissions Automatisation.",
            }
        summary = action.get("summary", action.get("title", "Événement"))
        start = action.get("start", action.get("date", ""))
        end = action.get("end", "")
        location = action.get("location", "")
        notes = action.get("notes", action.get("description", ""))
        calendar_name = action.get("calendar")

        result = await calendar_client.create_event(
            summary=summary,
            start_date=start,
            end_date=end,
            calendar_name=calendar_name,
            location=location,
            notes=notes,
        )
        if result.get("ok"):
            logger.info("[action] Événement créé : %s", summary)
        else:
            logger.error("[action] Échec création événement : %s", result)
        return result
    except Exception as e:
        logger.exception("[action] calendar_create : %s", e)
        return {"ok": False, "message": str(e)}


async def _action_mood(action: dict) -> dict:
    from database import save_mood
    score = int(action.get("score", 5))
    energy = int(action.get("energy", 5))
    context = action.get("context", "")
    save_mood(score, energy, context)
    logger.info("[action] Humeur enregistrée : score=%d energy=%d", score, energy)
    return {"ok": True, "message": f"Humeur enregistrée ({score}/10)."}


async def _action_note(action: dict) -> dict:
    from database import save_episode
    content = action.get("content", "")
    tags = action.get("tags", [])
    ep_id = save_episode("user", content, importance=6, tags=tags)
    logger.info("[action] Note enregistrée id=%d", ep_id)
    return {"ok": True, "message": "Note enregistrée.", "episode_id": ep_id}


async def _action_terminal(action: dict) -> dict:
    from integrations.computer import computer

    command = (action.get("command") or "").strip()
    if not computer or not computer.allowed:
        return {"ok": False, "message": "Accès ordinateur désactivé ou indisponible."}

    is_complex = action.get("complex", False)

    if is_complex or _is_natural_language(command):
        from integrations.code_executor import code_executor
        if code_executor.available:
            timeout = int(action.get("timeout", 120))
            return await code_executor.execute(command, timeout=timeout)

    timeout = int(action.get("timeout", 30))
    needs_confirm = any(p.search(command) for p in _TERMINAL_CONFIRM_PATTERNS)
    if needs_confirm and not action.get("confirmed"):
        return {
            "ok": True,
            "needs_confirmation": True,
            "command": command,
            "timeout": timeout,
            "message": f"Je vais exécuter : `{command}`. Tu confirmes ?",
        }

    return await computer.run(command, timeout=timeout)


_NL_INDICATORS = frozenset([
    "crée", "installe", "configure", "lance", "déploie", "écris",
    "fais", "mets", "corrige", "trouve", "vérifie", "génère",
    "analyse", "convertis", "télécharge", "compile", "optimise",
    "build", "deploy", "create", "setup", "write",
    "fix", "debug",
])

_SHELL_PREFIXES = frozenset([
    "ls", "cd", "cat", "head", "tail", "grep", "rg", "find", "wc",
    "echo", "pwd", "mkdir", "cp", "mv", "rm", "touch", "chmod",
    "chown", "df", "du", "ps", "kill", "top", "htop", "which",
    "where", "man", "curl", "wget", "ssh", "scp", "git", "docker",
    "brew", "pip", "pip3", "npm", "npx", "pnpm", "yarn", "node",
    "python", "python3", "ruby", "go", "cargo", "rustc", "make",
    "cmake", "gcc", "clang", "javac", "java", "open", "pbcopy",
    "pbpaste", "osascript", "defaults", "pmset", "networksetup",
    "launchctl", "xcode-select", "xcrun", "swift", "say", "afplay",
    "tar", "zip", "unzip", "gzip", "sed", "awk", "sort", "uniq",
    "tr", "cut", "xargs", "tee", "diff", "patch", "env", "export",
    "source", "sudo", "su", "whoami", "hostname", "uname", "date",
    "cal", "bc", "yes", "true", "false", "test", "sleep", "wait",
    "nohup", "screen", "tmux", "vim", "nano", "less", "more",
    "file", "stat", "lsof", "netstat", "ifconfig", "ping",
    "traceroute", "dig", "nslookup", "nc", "telnet",
])


def _is_natural_language(text: str) -> bool:
    """Détecte si le texte est du langage naturel plutôt qu'une commande shell."""
    text_lower = text.strip().lower()
    first_word = text_lower.split()[0] if text_lower.split() else ""
    if first_word in _SHELL_PREFIXES:
        return False
    words = set(text_lower.split())
    return bool(words & _NL_INDICATORS)


async def _action_open_app(action: dict) -> dict:
    from integrations.computer import computer

    name = (
        (action.get("app_name") or action.get("name") or action.get("app") or "").strip()
    )
    if not computer or not computer.allowed:
        return {"ok": False, "message": "Accès ordinateur désactivé."}
    if not name:
        return {"ok": False, "message": "Nom d'application manquant."}
    return await computer.open_app(name)


async def _action_find_file(action: dict) -> dict:
    from integrations.computer import computer

    q = (action.get("query") or "").strip()
    path = action.get("path")
    if not computer or not computer.allowed:
        return {"ok": False, "message": "Accès ordinateur désactivé.", "files": []}
    if not q:
        return {"ok": False, "message": "Requête vide.", "files": []}
    files = await computer.find_files(q, path)
    return {"ok": True, "files": files, "count": len(files)}


async def _action_clipboard(action: dict) -> dict:
    from integrations.computer import computer

    if not computer or not computer.allowed:
        return {"ok": False, "message": "Accès ordinateur désactivé."}

    if action.get("action") == "set":
        return await computer.set_clipboard(action.get("text", ""))

    text = await computer.get_clipboard()
    return {"ok": True, "content": text}


async def _action_system_info(action: dict) -> dict:
    from integrations.computer import computer

    if not computer or not computer.allowed:
        return {"ok": False, "message": "Accès ordinateur désactivé."}

    info_type = (action.get("info") or "disk").lower()
    if info_type == "battery":
        d = await computer.get_battery()
        return {"ok": True, **d}
    if info_type == "wifi":
        d = await computer.get_wifi()
        return {"ok": True, **d}
    if info_type == "disk":
        d = await computer.get_disk_space()
        return {"ok": True, **d}
    if info_type == "apps":
        apps = await computer.get_running_apps()
        return {"ok": True, "apps": apps}
    return {"ok": False, "message": f"Info inconnue : {info_type}"}


async def _action_name_place(action: dict) -> dict:
    from database.location_helpers import create_place, get_current_location

    name = (action.get("name") or "").strip()
    if not name:
        return {"ok": False, "message": "Nom manquant."}
    cat = (action.get("category") or "other").strip()
    cur = get_current_location()
    if not cur:
        return {"ok": False, "message": "Pas de position GPS récente."}
    pid = create_place(
        name=name,
        category=cat,
        lat=float(cur["latitude"]),
        lng=float(cur["longitude"]),
        radius=float(action["radius"]) if action.get("radius") is not None else None,
    )
    logger.info("[action] Lieu nommé id=%s name=%s", pid, name)
    return {"ok": True, "message": f"Lieu enregistré : {name}", "place_id": pid}


async def _action_where_am_i(action: dict) -> dict:
    from integrations.location import location_manager

    st = await location_manager.get_status()
    vis = st.get("current_visit")
    loc = st.get("current_location")
    if vis:
        return {
            "ok": True,
            "message": f"{vis.get('place_name')} (visite en cours).",
            "visit": vis,
            "location": loc,
        }
    if loc and loc.get("place_name"):
        return {
            "ok": True,
            "message": f"Dernière position près de {loc.get('place_name')}.",
            "location": loc,
        }
    if loc:
        lat, lng = float(loc["latitude"]), float(loc["longitude"])
        return {
            "ok": True,
            "message": f"Coordonnées récentes {lat:.4f}, {lng:.4f} (hors lieu nommé).",
            "location": loc,
        }
    return {"ok": False, "message": "Position inconnue."}


async def _action_day_route(action: dict) -> dict:
    from integrations.location import location_manager

    s = await location_manager.get_daily_summary()
    names = [str(v.get("place_name") or "?") for v in (s.get("visits") or [])]
    route = " → ".join(names) if names else "Aucune visite enregistrée aujourd'hui."
    return {"ok": True, "message": route, "summary": s}


async def _action_search_conversations(action: dict) -> dict:
    """Recherche dans les anciennes conversations (titres + messages)."""
    from database import search_conversations
    query = (action.get("query") or "").strip()
    if not query:
        return {"ok": False, "message": "Requête de recherche manquante."}
    results = search_conversations(query, limit=10)
    if not results:
        return {"ok": True, "messages": "Aucune conversation trouvée pour cette recherche.", "count": 0}
    formatted = "\n".join([
        f"[Conv #{r['id']}: {r.get('title') or 'Sans titre'} — {str(r.get('match_date') or '')[:16]}]\n  → {str(r.get('matching_message') or '')[:200]}"
        for r in results
    ])
    return {"ok": True, "messages": formatted, "count": len(results)}
