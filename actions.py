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
        elif action_type == "tv":
            out = await _action_tv(action)
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
        else:
            # Fallback : le code_executor n'est pas disponible (Open Interpreter absent).
            # On utilise le LLM pour générer des commandes shell exécutables.
            logger.warning("[terminal] code_executor indisponible, fallback LLM shell")
            return await _execute_via_llm_fallback(command, int(action.get("timeout", 120)))

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


async def _execute_via_llm_fallback(instruction: str, timeout: int = 120) -> dict:
    """Fallback quand code_executor n'est pas disponible.

    Utilise le LLM pour traduire une instruction en langage naturel
    en commandes shell macOS exécutables, puis les exécute via ``computer.run``.
    """
    from integrations.computer import computer
    import llm as llm_module
    import config as cfg

    if not computer or not computer.allowed:
        return {"ok": False, "message": "Accès ordinateur désactivé."}

    try:
        result = await llm_module.chat(
            messages=[{
                "role": "user",
                "content": (
                    "Traduis cette instruction en commandes shell exécutables "
                    f"sur macOS (zsh) :\n\n{instruction}\n\n"
                    "RÈGLES :\n"
                    "- Une commande par ligne, rien d'autre.\n"
                    "- Pas de markdown, pas d'explications.\n"
                    "- Utilise des chemins absolus si nécessaire.\n"
                    "- Pas plus de 8 commandes.\n"
                    "- Si la tâche nécessite du code Python, utilise python3 -c \"...\""
                ),
            }],
            model=getattr(cfg, "DEEPSEEK_FAST_MODEL", "deepseek-chat"),
            max_tokens=500,
            temperature=0.0,
        )
    except Exception as e:
        return {"ok": False, "message": f"LLM fallback indisponible : {e}"}

    commands = [
        cmd.strip()
        for cmd in result.get("content", "").split("\n")
        if cmd.strip() and not cmd.strip().startswith("#")
    ]

    if not commands:
        return {"ok": False, "message": "Aucune commande générée par le fallback LLM."}

    outputs: list[str] = []
    code_blocks: list[dict] = []
    errors: list[str] = []

    for cmd in commands[:8]:
        outputs.append(f"$ {cmd}")
        try:
            res = await computer.run(cmd, timeout=timeout)
            stdout = res.get("stdout", res.get("output", ""))
            if stdout:
                outputs.append(str(stdout)[:3000])
            if res.get("stderr"):
                errors.append(str(res["stderr"])[:1000])
            code_blocks.append({"language": "shell", "code": cmd})
            if not res.get("ok"):
                outputs.append(f"[ERREUR] {res.get('stderr', res.get('message', 'commande échouée'))[:500]}")
        except Exception as e:
            outputs.append(f"[EXCEPTION] {e}")

    return {
        "ok": len(errors) == 0,
        "output": "\n".join(outputs)[:5000],
        "code": code_blocks,
        "errors": errors[:3],
        "summary": outputs[-1][:500] if outputs else "Exécution terminée.",
    }


async def _action_open_app(action: dict) -> dict:
    from integrations.computer import computer

    name = (
        (action.get("app_name") or action.get("name") or action.get("app") or action.get("application") or "").strip()
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

    name = (
        action.get("name")
        or action.get("place_name")
        or action.get("lieu")
        or ""
    ).strip()
    if not name:
        return {"ok": False, "message": "Nom du lieu manquant."}

    category = (action.get("category") or "other").strip().lower()
    VALID_CATEGORIES = {
        "home", "school", "work", "gym", "restaurant", "shop",
        "friend", "family", "medical", "transport", "leisure", "other",
    }
    if category not in VALID_CATEGORIES:
        category = "other"

    cur = get_current_location()
    if not cur:
        return {
            "ok": False,
            "message": (
                "Impossible de nommer ce lieu : aucune position récente n'a été "
                "reçue du téléphone."
            ),
            "code": "NO_RECENT_LOCATION",
        }

    try:
        lat = float(cur["latitude"])
        lng = float(cur["longitude"])
    except (KeyError, TypeError, ValueError):
        return {
            "ok": False,
            "message": (
                "Impossible de nommer ce lieu : aucune position récente n'a été "
                "reçue du téléphone."
            ),
            "code": "NO_RECENT_LOCATION",
        }

    pid = create_place(
        name=name,
        category=category,
        lat=lat,
        lng=lng,
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


# ── TV Control ──────────────────────────────────────────────────────────────

# Anti-spam : dernière tentative d'allumage TV (évite boucle WoL→fail→WoL)
_LAST_TV_ON_ATTEMPT: float = 0.0

# Mapping commandes → keycodes Android
_TV_COMMANDS: dict[str, str] = {
    "power": "KEYCODE_POWER",
    "off": "KEYCODE_POWER",
    "on": "KEYCODE_WAKEUP",
    "wake": "KEYCODE_WAKEUP",
    "wol": "WOL",  # Wake-on-LAN — envoie un magic packet, pas un keyevent
    "wake_on_lan": "WOL",
    "home": "KEYCODE_HOME",
    "back": "KEYCODE_BACK",
    "menu": "KEYCODE_MENU",
    "up": "DPAD_UP",
    "down": "DPAD_DOWN",
    "left": "DPAD_LEFT",
    "right": "DPAD_RIGHT",
    "center": "DPAD_CENTER",
    "enter": "KEYCODE_ENTER",
    "ok": "DPAD_CENTER",
    "select": "DPAD_CENTER",
    "vol_up": "KEYCODE_VOLUME_UP",
    "volume_up": "KEYCODE_VOLUME_UP",
    "vol_down": "KEYCODE_VOLUME_DOWN",
    "volume_down": "KEYCODE_VOLUME_DOWN",
    "mute": "KEYCODE_VOLUME_MUTE",
    "play": "KEYCODE_MEDIA_PLAY_PAUSE",
    "pause": "KEYCODE_MEDIA_PLAY_PAUSE",
    "stop": "KEYCODE_MEDIA_STOP",
    "next": "KEYCODE_MEDIA_NEXT",
    "prev": "KEYCODE_MEDIA_PREVIOUS",
    "rewind": "KEYCODE_MEDIA_REWIND",
    "ffwd": "KEYCODE_MEDIA_FAST_FORWARD",
}


def _send_wol(mac_address: str, broadcast_ip: str = "255.255.255.255") -> bool:
    """Envoie un magic packet Wake-on-LAN pour reveiller la TV.

    Le magic packet contient 6 octets 0xFF suivis de la MAC repetee 16 fois,
    envoye en broadcast UDP sur le port 9.
    """
    import socket
    import struct

    # Normaliser la MAC (supprimer separateurs, pad a 2 digits par octet)
    mac_clean = "".join(c for c in mac_address if c.isalnum())
    if len(mac_clean) != 12:
        logger.warning("[tv] MAC invalide pour WoL : %s", mac_address)
        return False

    mac_bytes = bytes.fromhex(mac_clean)
    magic_packet = b"\xff" * 6 + mac_bytes * 16

    sock: socket.socket | None = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.settimeout(2.0)
        sock.sendto(magic_packet, (broadcast_ip, 9))
        sock.sendto(magic_packet, (broadcast_ip, 7))  # port fallback
        logger.info("[tv] WoL magic packet envoye a %s (broadcast=%s)", mac_address, broadcast_ip)
        return True
    except Exception as e:
        logger.warning("[tv] WoL echoue : %s", e)
        return False
    finally:
        if sock:
            sock.close()


async def _adb_connect_ensure(tv_ip: str, tv_port: int, timeout: float = 5.0) -> str | None:
    """Assure la connexion ADB a la TV. Retourne None si OK, ou un message d'erreur."""
    import subprocess

    # Verifier si deja connecte
    try:
        proc = await asyncio.create_subprocess_exec(
            "adb", "devices",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=3.0)
        devices_text = stdout.decode(errors="replace")
        target = f"{tv_ip}:{tv_port}"
        if target in devices_text and "\tdevice" in devices_text.split(target)[1][:20]:
            return None  # deja connecte
    except Exception:
        pass  # continuer et tenter la connexion

    # Tenter la connexion
    try:
        proc = await asyncio.create_subprocess_exec(
            "adb", "connect", f"{tv_ip}:{tv_port}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        output = (stdout + stderr).decode(errors="replace").strip()
        logger.info("[tv] ADB connect %s:%d → %s", tv_ip, tv_port, output[:120])
        if "connected" in output.lower() or "already" in output.lower():
            return None
        return f"ADB connexion echouee : {output[:200]}"
    except asyncio.TimeoutError:
        return f"ADB timeout — la TV ({tv_ip}) ne repond pas. Elle est peut-etre eteinte."
    except FileNotFoundError:
        return "ADB introuvable. Installe `brew install android-platform-tools`."
    except Exception as e:
        return f"Erreur ADB : {e}"


async def _wake_tv_via_cast(tv_ip: str, dashboard_url: str, timeout: float = 20.0) -> tuple[bool, str]:
    """Reveille la TV Philips via Google Cast (Chromecast integre).

    Les TV Philips Android/OLED entrent en deep standby apres 5-10 minutes
    d'arret. Dans cet etat, le WoL classique echoue, mais le module Google
    Cast integre reste actif (ports 8008/8009). catt envoie un stream vers
    la TV, ce qui la reveille ET ouvre le dashboard.

    Args:
        tv_ip: Adresse IP de la TV.
        dashboard_url: URL du dashboard JARVIS a afficher.
        timeout: Timeout en secondes pour l'operation catt.

    Returns:
        Tuple (succes, message).
    """
    import shutil

    catt_bin = shutil.which("catt")
    if not catt_bin:
        return False, "catt non installe. Installe-le avec : pip install catt"

    logger.info("[tv:cast] Tentative de reveil Google Cast vers %s → %s", tv_ip, dashboard_url)

    try:
        proc = await asyncio.create_subprocess_exec(
            catt_bin,
            "-d", tv_ip,
            "cast_site", dashboard_url,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        stdout_text = stdout.decode(errors="replace").strip()
        stderr_text = stderr.decode(errors="replace").strip()

        if proc.returncode == 0:
            logger.info("[tv:cast] Google Cast OK — TV reveillee + dashboard ouvert")
            return True, "Google Cast : TV reveillee + dashboard ouvert"
        else:
            err_msg = stderr_text or stdout_text or f"exit code {proc.returncode}"
            logger.warning("[tv:cast] Google Cast echoue : %s", err_msg[:200])
            return False, f"Google Cast echoue : {err_msg[:150]}"
    except asyncio.TimeoutError:
        logger.warning("[tv:cast] Google Cast timeout apres %.0fs", timeout)
        return False, f"Google Cast timeout ({timeout:.0f}s). La TV ne repond pas."
    except FileNotFoundError:
        return False, "catt non installe. `pip install catt`"
    except Exception as e:
        logger.exception("[tv:cast] Erreur Google Cast : %s", e)
        return False, f"Erreur Google Cast : {e}"


async def _open_tv_dashboard(tv_ip: str, dashboard_url: str) -> bool:
    """Ouvre le dashboard JARVIS sur le navigateur Kiwi de la TV via ADB.

    Args:
        tv_ip: Adresse IP de la TV (pas utilisee directement, ADB deja connecte).
        dashboard_url: URL du dashboard a ouvrir.

    Returns:
        True si l'ouverture a reussi, False sinon.
    """
    kiwi_package = "com.kiwibrowser.browser"
    kiwi_activity = f"{kiwi_package}/com.google.android.apps.chrome.Main"

    try:
        # Verifier si Kiwi est deja lance
        proc = await asyncio.create_subprocess_exec(
            "adb", "shell", "pidof", kiwi_package,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5.0)
        kiwi_running = stdout.decode(errors="replace").strip()

        if kiwi_running:
            logger.info("[tv] Kiwi deja lance, navigation vers dashboard")
            proc = await asyncio.create_subprocess_exec(
                "adb", "shell", "am", "start",
                "-n", kiwi_activity,
                "-d", dashboard_url,
                "-f", "0x10000000",  # FLAG_ACTIVITY_NEW_TASK | FLAG_ACTIVITY_CLEAR_TOP
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        else:
            logger.info("[tv] Lancement Kiwi avec dashboard")
            proc = await asyncio.create_subprocess_exec(
                "adb", "shell", "am", "start",
                "-n", kiwi_activity,
                "-d", dashboard_url,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10.0)
        stderr_text = (stderr or b"").decode(errors="replace").strip()
        if proc.returncode != 0 and "error" in stderr_text.lower():
            logger.warning("[tv] Echec ouverture dashboard: %s", stderr_text[:200])
            return False
        logger.info("[tv] Dashboard ouvert sur la TV")
        return True
    except asyncio.TimeoutError:
        logger.warning("[tv] Timeout ouverture dashboard")
        return False
    except Exception as e:
        logger.warning("[tv] Erreur ouverture dashboard: %s", e)
        return False


async def _action_tv(action: dict) -> dict:
    """Contrôle la TV Philips via ADB + Wake-on-LAN.

    Commandes supportees :
    - ``on`` / ``wake`` : allumer/reveiller (WoL + ADB KEYCODE_WAKEUP)
    - ``off`` / ``power`` : eteindre (KEYCODE_POWER)
    - ``wol`` / ``wake_on_lan`` : envoyer uniquement le magic packet WoL
    - ``home``, ``back``, ``menu`` : navigation Android TV
    - ``up``, ``down``, ``left``, ``right``, ``center`` : DPAD
    - ``vol_up``, ``vol_down``, ``mute`` : volume
    - ``play``, ``pause``, ``stop``, ``next``, ``prev`` : media
    """
    import config as cfg
    import subprocess
    import time as _time

    tv_ip = getattr(cfg, "TV_IP", "192.168.3.82")
    tv_port = int(getattr(cfg, "TV_ADB_PORT", "5555") or "5555")

    command = (action.get("command") or action.get("action") or "").strip().lower()
    if not command:
        return {"ok": False, "message": "Commande TV manquante. Commandes : on, off, home, back, vol_up, vol_down, mute..."}

    keycode = _TV_COMMANDS.get(command)
    if not keycode:
        suggestions = [k for k in _TV_COMMANDS if command in k]
        available = ", ".join(sorted(set(_TV_COMMANDS.keys()) - {"wake_on_lan"}))
        hint = f" Vouliez-vous dire : {', '.join(suggestions[:3])} ?" if suggestions else ""
        return {
            "ok": False,
            "message": f"Commande TV inconnue : '{command}'. Commandes disponibles : {available}.{hint}",
        }

    # ── Commande WoL (Wake-on-LAN sans keyevent) ──────────────────────────
    if keycode == "WOL":
        tv_mac = getattr(cfg, "TV_MAC", "")
        if not tv_mac:
            return {"ok": False, "message": "Adresse MAC TV non configuree. Ajoute TV_MAC dans .env"}
        ok = await asyncio.get_running_loop().run_in_executor(None, _send_wol, tv_mac)
        if ok:
            return {"ok": True, "message": "Magic packet WoL envoye a la TV. La TV devrait s'allumer d'ici 10-20 secondes.", "command": "wol"}
        return {"ok": False, "message": "Echec de l'envoi du magic packet WoL. Verifie que la TV est sur le meme reseau."}

    # ── Commande "on" / "wake" : WoL d'abord, puis ADB ───────────────────
    if command in ("on", "wake"):
        tv_mac = getattr(cfg, "TV_MAC", "")
        steps: list[str] = []

        # Anti-spam : si on a déjà tenté d'allumer la TV dans les 30 dernieres
        # secondes, ne pas réessayer (evite la boucle WoL→fail→WoL→fail)
        global _LAST_TV_ON_ATTEMPT
        now_ts = _time.time()
        if _LAST_TV_ON_ATTEMPT > 0 and (now_ts - _LAST_TV_ON_ATTEMPT) < 30:
            logger.debug("[tv] Anti-spam : dernière tentative TV 'on' il y a %.0fs — skip", now_ts - _LAST_TV_ON_ATTEMPT)
            return {
                "ok": False,
                "message": (
                    "J'ai deja envoye le signal de reveil a la TV il y a quelques secondes. "
                    "Patientez 20-30 secondes qu'elle demarre, puis reessayez."
                ),
            }
        _LAST_TV_ON_ATTEMPT = now_ts

        if tv_mac:
            # Broadcast local plus efficace que 255.255.255.255 sur certains routeurs
            subnet_broadcast = ".".join(tv_ip.split(".")[:3]) + ".255"
            ok_wol = _send_wol(tv_mac, broadcast_ip=subnet_broadcast)
            if not ok_wol:
                # Fallback broadcast global
                ok_wol = _send_wol(tv_mac, broadcast_ip="255.255.255.255")
            if ok_wol:
                steps.append("Magic packet WoL envoye")
                await asyncio.sleep(8.0)
            else:
                steps.append("WoL echoue")

        # Tenter ADB
        dashboard_url = getattr(cfg, "TV_DASHBOARD_URL", "http://192.168.3.52:5174/")
        adb_err = await _adb_connect_ensure(tv_ip, tv_port, timeout=8.0)
        if adb_err:
            # WoL + ADB echoue → la TV est probablement en deep standby (>5-10 min d'arret)
            # Fallback : Google Cast (Chromecast integre) qui reste actif meme en deep standby
            steps.append(f"ADB indisponible ({adb_err[:100]})")
            cast_enabled = getattr(cfg, "TV_CAST_ENABLED", True)
            cast_timeout = float(getattr(cfg, "TV_CAST_TIMEOUT", "20") or "20")

            if cast_enabled:
                logger.info("[tv] ADB echoue, tentative Google Cast fallback (deep standby probable)")
                ok_cast, cast_msg = await _wake_tv_via_cast(tv_ip, dashboard_url, timeout=cast_timeout)
                steps.append(cast_msg)
                if ok_cast:
                    # Google Cast a reveille la TV → attendre que le systeme demarre, puis retenter ADB
                    await asyncio.sleep(5.0)
                    adb_err2 = await _adb_connect_ensure(tv_ip, tv_port, timeout=10.0)
                    if adb_err2:
                        steps.append(f"ADB toujours indisponible ({adb_err2[:100]})")
                        return {
                            "ok": False,
                            "message": (
                                f"Google Cast a reveille la TV (dashboard ouvert), "
                                f"mais ADB ne repond pas encore. "
                                f"Patientez 10-15 secondes puis reessayez. "
                                f"Erreur : {adb_err2[:150]}"
                            ),
                        }
                    # ADB connecte → KEYCODE_WAKEUP pour allumer l'ecran si besoin
                    try:
                        proc = await asyncio.create_subprocess_exec(
                            "adb", "shell", "input", "keyevent", keycode,
                            stdout=asyncio.subprocess.PIPE,
                            stderr=asyncio.subprocess.PIPE,
                        )
                        await asyncio.wait_for(proc.communicate(), timeout=10.0)
                    except Exception:
                        pass  # l'ecran est peut-etre deja allume via Cast
                    # Ouvrir le dashboard (deja ouvert via Cast, mais refocus via ADB)
                    await _open_tv_dashboard(tv_ip, dashboard_url)
                    return {
                        "ok": True,
                        "message": f"TV reveillee, dashboard ouvert ({'; '.join(steps)})",
                        "command": command,
                        "keycode": keycode,
                    }
                else:
                    # Cast aussi echoue → echec total
                    return {
                        "ok": False,
                        "message": (
                            f"Impossible de reveiller la TV. "
                            f"WoL : {'OK' if tv_mac and ok_wol else 'echec'}. "
                            f"ADB : {adb_err[:120]}. "
                            f"Google Cast : {cast_msg[:120]}. "
                            f"Verifie que la TV est branchee et sur le meme reseau."
                        ),
                    }
            else:
                # Cast desactive → pas de fallback
                return {
                    "ok": False,
                    "message": (
                        f"J'ai envoye le signal de reveil a la TV"
                        + (f" ({steps[0]})" if steps else "")
                        + f", mais ADB ne repond pas. "
                        f"La TV est en deep standby (eteinte depuis >10 min). "
                        f"Active TV_CAST_ENABLED=true dans .env pour le fallback Chromecast, "
                        f"ou allume la TV manuellement."
                    ),
                }

        # ADB connecte → envoyer KEYCODE_WAKEUP
        try:
            proc = await asyncio.create_subprocess_exec(
                "adb", "shell", "input", "keyevent", keycode,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10.0)
            stderr_text = (stderr or b"").decode(errors="replace").strip()
            if proc.returncode != 0 and "error" in stderr_text.lower():
                return {"ok": False, "message": f"Erreur ADB : {stderr_text[:200]}"}
        except asyncio.TimeoutError:
            return {"ok": False, "message": "La TV n'a pas repondu au keyevent. Reessayez."}
        except Exception as e:
            return {"ok": False, "message": f"Erreur ADB : {e}"}

        # Ouvrir le dashboard JARVIS sur la TV
        dashboard_ok = await _open_tv_dashboard(tv_ip, dashboard_url)
        if dashboard_ok:
            steps.append("dashboard ouvert")
        else:
            steps.append("dashboard non ouvert")

        return {"ok": True, "message": "TV allumee, dashboard ouvert" + (f" ({'; '.join(steps)})" if steps else ""), "command": command, "keycode": keycode}

    # ── Toutes les autres commandes : ADB standard ────────────────────────
    adb_err = await _adb_connect_ensure(tv_ip, tv_port)
    if adb_err:
        return {"ok": False, "message": f"Impossible de se connecter a la TV : {adb_err}"}

    adb_cmd = ["adb", "shell", "input", "keyevent", keycode]
    try:
        proc = await asyncio.create_subprocess_exec(
            *adb_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10.0)
    except asyncio.TimeoutError:
        logger.warning("[tv] ADB timeout pour %s", command)
        return {"ok": False, "message": "La TV n'a pas repondu a temps. Verifie la connexion."}
    except FileNotFoundError:
        return {"ok": False, "message": "ADB introuvable. Installe `brew install android-platform-tools`."}
    except Exception as e:
        logger.exception("[tv] ADB error for %s: %s", command, e)
        return {"ok": False, "message": f"Erreur ADB : {e}"}

    stderr_text = (stderr or b"").decode(errors="replace").strip()
    if proc.returncode != 0 or stderr_text:
        logger.warning("[tv] ADB exited %s stderr=%r", proc.returncode, stderr_text[:200])
        if "error" in stderr_text.lower() or "cannot" in stderr_text.lower():
            return {"ok": False, "message": f"Erreur ADB : {stderr_text[:200]}"}

    friendly: str = {
        "KEYCODE_POWER": "TV eteinte",
        "KEYCODE_HOME": "Accueil TV",
        "KEYCODE_BACK": "Retour",
        "KEYCODE_VOLUME_UP": "Volume +",
        "KEYCODE_VOLUME_DOWN": "Volume -",
        "KEYCODE_VOLUME_MUTE": "Muet",
        "KEYCODE_MEDIA_PLAY_PAUSE": "Lecture/Pause",
        "KEYCODE_MEDIA_STOP": "Stop",
        "KEYCODE_MEDIA_NEXT": "Suivant",
        "KEYCODE_MEDIA_PREVIOUS": "Precedent",
        "KEYCODE_MEDIA_REWIND": "Retour rapide",
        "KEYCODE_MEDIA_FAST_FORWARD": "Avance rapide",
        "KEYCODE_WAKEUP": "TV reveillee",
        "KEYCODE_ENTER": "OK",
        "KEYCODE_MENU": "Menu",
    }.get(keycode, f"Commande '{command}' envoyee")

    logger.info("[tv] %s → %s", command, friendly)
    return {"ok": True, "message": friendly, "command": command, "keycode": keycode}
