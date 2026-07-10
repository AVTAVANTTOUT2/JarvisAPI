"""JARVIS — Entry point FastAPI + WebSocket.

Lance le serveur web local, sert l'interface SPA, et expose les routes API
+ le WebSocket de chat temps réel.

Usage :
    python main.py
    → http://localhost:8080
"""

import asyncio
import json
import logging
import re
import subprocess
from urllib.parse import unquote
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import fitz  # pymupdf — extraction texte PDF
import uvicorn
from fastapi import BackgroundTasks, Body, FastAPI, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import config
import llm
from actions import execute_action
from agents import get_agent, register_agent
from agents import easter_eggs
from agents.coach import coach_agent
from agents.info import info_agent
from agents.journal import journal_agent
from agents.memory import memory_agent
from agents.orchestrator import orchestrator
from agents.productivity import productivity_agent
from agents.school import school_agent
from agents.devops import devops_agent
from agents.devagent import (
    lock_spec,
    next_interview_step,
    run_loop,
    slugify,
    submit_answer,
)
from agents.devagent.models import InterviewAnswer
from database import devagent as devagent_db
from agents.autonomous_loop import parse_loop_command, run_autonomous_loop
from agents.display_text import (
    extract_leading_emotion,
    finalize_assistant_display_text,
    sanitize_streaming_display,
    strip_leading_emotion,
)
from jarvis.event_bus import JarvisEvent, event_bus

# Audio : STT (ElevenLabs Scribe) + TTS (ElevenLabs / Edge).
# Chargement conditionnel — l'absence d'audio ne doit pas empêcher le serveur de tourner.
try:
    from audio import stt, tts
except ImportError as _audio_err:
    stt = None
    tts = None
    logging.getLogger("jarvis").warning(
        f"Module audio indisponible ({_audio_err}). Mic/TTS désactivés."
    )

# Intégrations Phase 4+ (Mail.app, Calendar, Météo, iMessage). Toujours importables (init défensif).
from integrations import calendar_client, mail_client, imessage_bridge, weather
from scripts.email_watcher import email_watcher
from database import (
    add_life_profile_entry,
    clear_person_ai_description,
    count_memory_stats,
    create_conversation,
    create_task,
    delete_all_tasks,
    delete_conversation,
    delete_life_profile_entry,
    delete_task,
    end_conversation,
    get_active_device,
    get_active_patterns,
    get_all_devices,
    get_all_people,
    get_app_usage,
    get_app_usage_range,
    get_conversation_detail,
    get_conversation_documents,
    get_conversation_history,
    get_conversations,
    get_cost_summary,
    get_current_screen_context,
    get_daily_activity_stats,
    get_last_conversation_summary,
    get_life_profile,
    get_life_profile_entries,
    get_llm_logs,
    get_people_sorted_by_recent,
    get_person,
    get_recent_episodes,
    get_recent_email_summaries,
    get_recent_moods,
    get_recent_notifications,
    get_recording,
    get_recordings,
    get_relationship_profile,
    get_relationship_timeline,
    get_school_documents,
    get_screen_activity,
    get_task,
    get_tasks,
    get_unread_notifications,
    get_usage_stats,
    init_db,
    mark_all_notifications_read,
    mark_notification_read,
    patch_person,
    register_device,
    save_conversation_document,
    save_message,
    save_school_document,
    save_screen_activity,
    search_conversations,
    set_active_device,
    set_person_ai_description,
    log_llm_action,
    update_conversation,
    update_conversation_activity,
    update_device_heartbeat,
    update_life_profile_entry,
    update_task_status,
    upsert_person,
    _save_voice_debug_trace,
    get_voice_debug_logs,
)


def _parse_optional_point_time(body: dict[str, Any]) -> datetime | None:
    """ISO8601, unix (s ou ms), depuis les clés timestamp / created_at / point_time."""
    for key in ("timestamp", "created_at", "point_time"):
        v = body.get(key)
        if v is None:
            continue
        if isinstance(v, (int, float)):
            x = float(v)
            if x > 1e12:
                x /= 1000.0
            try:
                return datetime.fromtimestamp(x)
            except (OSError, OverflowError, ValueError):
                continue
        if isinstance(v, str):
            try:
                return datetime.fromisoformat(v.replace("Z", "+00:00"))
            except ValueError:
                continue
    return None


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("jarvis")

BASE_DIR = Path(__file__).resolve().parent

# File handlers pour les daemons critiques (diagnostic crash)
_logs_dir = BASE_DIR / "data" / "logs"
_logs_dir.mkdir(parents=True, exist_ok=True)
_daemon_formatter = logging.Formatter(
    "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
for _daemon_logger_name in ("audio_daemon", "scripts.jarvis_daemon"):
    _fh = logging.FileHandler(_logs_dir / f"{_daemon_logger_name}.log")
    _fh.setLevel(logging.DEBUG)
    _fh.setFormatter(_daemon_formatter)
    logging.getLogger(_daemon_logger_name).addHandler(_fh)
    logging.getLogger(_daemon_logger_name).setLevel(logging.DEBUG)
_WELCOME_MARKER = BASE_DIR / "data" / ".welcome_day"


def _decode_person_path(name: str) -> str:
    """Segment de chemin `/api/people/{name}` — décodage %XX supplémentaire si besoin."""
    return unquote(name).strip()


def _load_persona_block() -> str:
    try:
        raw = (BASE_DIR / "prompts" / "persona.txt").read_text(encoding="utf-8")
    except OSError:
        return ""
    return raw.replace("{{user_name}}", getattr(config, "USER_NAME", "l'utilisateur"))


def _resolve_imessage_handle(person: dict, profile: dict | None) -> str | None:
    if profile and profile.get("handle"):
        h = str(profile["handle"]).strip()
        if h:
            return h
    n = (person.get("name") or "").strip()
    if "@" in n:
        return n
    if re.match(r"^\+?\d[\d\s\-\(\)\.]+$", n):
        return re.sub(r"\s+", "", n)
    return None


def _resolve_handle_with_contacts(name: str) -> str | None:
    """Résout un nom de contact en handle iMessage.

    Ordre:
    1) relationship_profiles.handle
    2) imessage_analysis_cache + Contacts.resolve_handle
    3) cache Contacts (inverse nom -> handle)
    4) champ people.name si déjà un handle
    5) recherche iMessage directe (LIKE handle / texte)
    """
    from database import get_db, get_person

    key = (name or "").strip()
    if not key:
        logger.info("[resolve] %s -> %s", name, None)
        return None

    person = get_person(key)
    if not person:
        logger.info("[resolve] %s -> %s", key, None)
        return None

    # 1) relationship_profiles.handle
    try:
        with get_db() as conn:
            row = conn.execute(
                "SELECT handle FROM relationship_profiles WHERE person_id = ?",
                (person["id"],),
            ).fetchone()
            if row and row["handle"]:
                h = str(row["handle"]).strip()
                logger.info("[resolve] %s -> %s", key, h)
                return h
    except Exception as e:
        logger.debug("[resolve] relationship_profiles: %s", e)

    # 2) imessage_analysis_cache -> resolve via Contacts
    try:
        from integrations.contacts import contacts_reader

        contacts_reader.build_cache()
        with get_db() as conn:
            rows = conn.execute("SELECT handle FROM imessage_analysis_cache").fetchall()
        for row in rows:
            h = str(row["handle"] or "").strip()
            if not h:
                continue
            resolved = contacts_reader.resolve_handle(h)
            if resolved and resolved.strip().lower() == key.lower():
                logger.info("[resolve] %s -> %s", key, h)
                return h
    except Exception as e:
        logger.debug("[resolve] analysis_cache/contacts: %s", e)

    # 3) contacts cache inverse lookup
    try:
        from integrations.contacts import contacts_reader

        contacts_reader.build_cache()
        for handle, contact_name in contacts_reader._cache.items():
            cn = str(contact_name or "").strip().lower()
            if cn != key.lower():
                continue
            h = str(handle).strip()
            if h.startswith("+") or "@" in h or re.match(r"^\d{10,}$", h):
                logger.info("[resolve] %s -> %s", key, h)
                return h
    except Exception as e:
        logger.debug("[resolve] contacts inverse: %s", e)

    # 4) people.name déjà handle
    person_name = (person.get("name") or "").strip()
    if re.match(r"^[\+\d\s\-\.]+$", person_name) or "@" in person_name:
        logger.info("[resolve] %s -> %s", key, person_name)
        return person_name

    # 5) recherche iMessage directe
    try:
        from integrations.imessage_reader import imessage_reader

        if imessage_reader and imessage_reader.is_available():
            msgs = imessage_reader.get_conversation_with(key, limit=5)
            if msgs:
                for m in msgs:
                    if not m.get("is_from_me") and m.get("handle"):
                        h = str(m["handle"]).strip()
                        logger.info("[resolve] %s -> %s", key, h)
                        return h
                h0 = str(msgs[0].get("handle") or "").strip()
                if h0:
                    logger.info("[resolve] %s -> %s", key, h0)
                    return h0
    except Exception as e:
        logger.debug("[resolve] imessage direct: %s", e)

    logger.info("[resolve] %s -> %s", key, None)
    return None


def _format_contact_timeline(timeline: list) -> str:
    lines = []
    for ev in (timeline or [])[:18]:
        dt = ev.get("event_date") or (str(ev.get("created_at") or "")[:16])
        summary = (ev.get("summary") or "").strip()
        et = ev.get("event_type") or ""
        lines.append(f"- [{dt}] ({et}) {summary[:500]}")
    return "\n".join(lines) if lines else "(aucun événement structuré)"


def _format_people_events(events: list) -> str:
    lines = []
    for ev in (events or [])[:12]:
        dt = (str(ev.get("created_at") or ""))[:16]
        content = (ev.get("content") or "").strip()
        et = ev.get("event_type") or ""
        lines.append(f"- [{dt}] ({et}) {content[:400]}")
    return "\n".join(lines) if lines else "(aucun événement people_events)"


def _format_imessage_snippets(msgs: list, contact_label: str) -> str:
    lines = []
    for m in msgs[-35:]:
        who = "Moi" if m.get("is_from_me") else contact_label
        ts = m.get("date_short") or ""
        tx = (m.get("text") or "").replace("\n", " ")[:650]
        lines.append(f"{ts} · {who}: {tx}")
    return "\n".join(lines) if lines else "(aucun extrait iMessage — handle ou chat.db)"


async def _generate_person_ai_description(person: dict, profile: dict | None) -> tuple[str, dict]:
    """Génère une description courte (Haiku) et retourne (texte, meta llm)."""
    rp = profile or {}
    topics = rp.get("topics") or ""
    if isinstance(topics, (list, dict)):
        topics = json.dumps(topics, ensure_ascii=False)
    user_msg = f"""Génère une description concise de {person.get("name")} en 3-4 phrases, du point de vue de {config.USER_NAME}.

Déduis le genre de {person.get("name")} à partir du prénom et du contexte. Utilise les pronoms appropriés (il/elle).

Données :
Relation : {person.get("relationship") or "—"}
Dynamique : {person.get("dynamics") or "—"}
Personnalité : {person.get("personality_notes") or "—"}
Style comm : {rp.get("communication_style") or "—"}
Sentiment : {rp.get("sentiment") or "—"}
Sujets : {topics or "—"}
Fréquence : {rp.get("interaction_frequency") or "—"}

Écris comme un profil humain naturel, pas comme une fiche technique. Pas d'emoji. Français."""
    res = await llm.chat(
        messages=[{"role": "user", "content": user_msg}],
        model=config.DEEPSEEK_FAST_MODEL,
        system="Tu réponds uniquement par le texte de la description, sans titre ni préambule.",
        max_tokens=500,
        temperature=0.4,
        use_cache=False,
    )
    text = (res.get("content") or "").strip()
    text = strip_leading_emotion(text)
    return text, res


def _welcome_already_sent_today() -> bool:
    today = datetime.now().strftime("%Y-%m-%d")
    try:
        return _WELCOME_MARKER.read_text(encoding="utf-8").strip() == today
    except OSError:
        return False


def _mark_welcome_sent() -> None:
    try:
        _WELCOME_MARKER.parent.mkdir(parents=True, exist_ok=True)
        _WELCOME_MARKER.write_text(datetime.now().strftime("%Y-%m-%d"), encoding="utf-8")
    except OSError as e:
        logger.warning("welcome marker : %s", e)


async def _maybe_send_daily_welcome(ws: WebSocket) -> None:
    """Première connexion du jour : salutation contextualisée (Haiku)."""
    if _welcome_already_sent_today():
        return
    if not getattr(config, "DEEPSEEK_API_KEY", None):
        return
    try:
        moods = get_recent_moods(1)
        mood_line = ""
        if moods:
            m = moods[0]
            mood_line = (
                f"Dernier mood : {m.get('mood_score')}/10, énergie {m.get('energy_level')}/10."
            )
        h = datetime.now().hour
        if 5 <= h < 12:
            period = "matin"
        elif 12 <= h < 18:
            period = "après-midi"
        else:
            period = "soir"
        tasks = get_tasks()
        now = datetime.now()
        overdue_n = 0
        for t in tasks:
            dd = t.get("due_date")
            if not dd or t.get("status") == "done":
                continue
            try:
                ds = str(dd).replace("Z", "")
                if "T" in ds:
                    due = datetime.fromisoformat(ds[:19])
                else:
                    due = datetime.fromisoformat(ds[:10])
            except Exception:
                continue
            if due <= now:
                overdue_n += 1
        unread = 0
        if mail_client and mail_client.is_available():
            try:
                u = await mail_client.get_unread(80)
                unread = len(u or [])
            except Exception as e:
                logger.warning("welcome mail : %s", e)
        next_ev = ""
        if calendar_client and calendar_client.is_available():
            try:
                evs = await calendar_client.get_today_events()
                if evs:
                    e0 = evs[0]
                    next_ev = f"{e0.get('summary', '')} {e0.get('start', '')}"
            except Exception as e:
                logger.warning("welcome calendar : %s", e)
        prompt = (
            f"Une à deux phrases max. JARVIS, majordome britannique, français, "
            f"pas d'emoji, concis, poli. Période : {period}. {mood_line} "
            f"Tâches en retard (estimé) : {overdue_n}. Mails non lus (estimé) : {unread}. "
            f"Prochain événement aujourd’hui : {next_ev or 'aucun'}. "
            f"Utilisateur : {config.USER_NAME}."
        )
        r = await llm.chat(
            messages=[{"role": "user", "content": prompt}],
            model=config.DEEPSEEK_FAST_MODEL,
            system="Tu réponds uniquement par la salutation, sans balises.",
            max_tokens=120,
            temperature=0.7,
            use_cache=False,
        )
        text = (r.get("content") or "").strip()
        if text:
            await ws.send_json({"type": "welcome", "content": text})
            _mark_welcome_sent()
    except Exception as e:
        logger.exception("welcome message : %s", e)


WEB_DIST = BASE_DIR / "web" / "dist"
WEB_STATIC = BASE_DIR / "web" / "static"
WEB_TEMPLATES = BASE_DIR / "web" / "templates"

# Segments React Router (BrowserRouter) — whitelist historique + BIG BROTHER.
_SPA_SEGMENTS = frozenset({
    "chat", "voice", "tasks", "documents", "memory", "status",
    "dashboard", "contacts", "map", "analytics", "search", "data",
    "conversations", "calendar", "logs", "monitoring",
    "voice-debug",
})


def _setup_frontend(app: FastAPI) -> None:
    """Sert le build Vite (`web/dist`) si présent, sinon Jinja legacy."""
    if WEB_STATIC.is_dir():
        app.mount("/static", StaticFiles(directory=WEB_STATIC), name="static")

    index_file = WEB_DIST / "index.html"
    if index_file.is_file():
        assets_dir = WEB_DIST / "assets"
        if assets_dir.is_dir():
            app.mount("/assets", StaticFiles(directory=assets_dir), name="vite_assets")

        @app.get("/", include_in_schema=False)
        async def serve_spa_root():
            try:
                return FileResponse(
                    index_file,
                    media_type="text/html; charset=utf-8",
                    content_disposition_type="inline",
                )
            except OSError as e:
                logger.error(f"SPA index inaccessible : {e}")
                raise HTTPException(503, "Fichiers frontend illisibles (permissions ou volume).") from e

        @app.get("/{segment}", include_in_schema=False)
        async def serve_spa_segment(segment: str):
            if segment not in _SPA_SEGMENTS:
                raise HTTPException(404)
            try:
                return FileResponse(
                    index_file,
                    media_type="text/html; charset=utf-8",
                    content_disposition_type="inline",
                )
            except OSError as e:
                logger.error(f"SPA index inaccessible : {e}")
                raise HTTPException(503, "Fichiers frontend illisibles.") from e

        # Fallback SPA : routes imbriquees (/contacts/foo) sans extension fichier
        @app.get("/{parent}/{child:path}", include_in_schema=False)
        async def serve_spa_nested(parent: str, child: str):
            if parent in ("api", "assets", "static", "upload") or child.startswith("api/"):
                raise HTTPException(404)
            if parent not in _SPA_SEGMENTS:
                raise HTTPException(404)
            try:
                return FileResponse(
                    index_file,
                    media_type="text/html; charset=utf-8",
                    content_disposition_type="inline",
                )
            except OSError as e:
                logger.error(f"SPA nested inaccessible : {e}")
                raise HTTPException(503, "Fichiers frontend illisibles.") from e

        logger.info("Frontend React (Vite) : %s", WEB_DIST)
        return

    tmpl = WEB_TEMPLATES / "index.html"
    if tmpl.is_file():
        jinja = Jinja2Templates(directory=str(WEB_TEMPLATES))

        @app.get("/", response_class=HTMLResponse)
        async def serve_jinja(request: Request):
            return jinja.TemplateResponse(
                "index.html",
                {"request": request, "user_name": config.USER_NAME},
            )

        logger.info("Frontend legacy (Jinja) : %s", WEB_TEMPLATES)
        return

    logger.warning(
        "Aucun frontend : `cd web && pnpm install && pnpm build`, "
        "ou restaurez web/templates/index.html."
    )


# ── WebSocket broadcast (audio daemon → tous les clients) ────────────────────

connected_ws: set[WebSocket] = set()

# Session vocale persistante : à la reconnexion dans la fenêtre de grâce
# (coupure réseau courte), la même conversation reprend — le contexte survit.
_ws_last_session: dict[str, Any] = {"conversation_id": None, "closed_at": 0.0, "ws": None}


def _resume_or_create_conversation(now: float | None = None) -> tuple[int, bool]:
    """Reprend la conversation précédente si la coupure est plus courte que
    VOICE_SESSION_GRACE_S, sinon en crée une nouvelle. Retourne (id, reprise).

    Deux cas de reprise :
    - déconnexion détectée il y a moins de `grace` secondes ;
    - l'ancienne socket a déjà quitté `connected_ws` sans que sa clôture soit
      horodatée (coupure brutale, handler encore en cours) — même conversation.
    """
    import time as _time

    now = now or _time.time()
    grace = getattr(config, "VOICE_SESSION_GRACE_S", 180)
    prev_id = _ws_last_session.get("conversation_id")
    if prev_id:
        closed_at = _ws_last_session.get("closed_at") or 0.0
        prev_ws = _ws_last_session.get("ws")
        recently_closed = closed_at > 0.0 and (now - closed_at) < grace
        dropped = closed_at == 0.0 and prev_ws is not None and prev_ws not in connected_ws
        if recently_closed or dropped:
            logger.info("[ws] Reprise de la conversation #%s (coupure < %ds)", prev_id, grace)
            return prev_id, True
    return create_conversation(agent="orchestrator"), False


async def broadcast_ws(event: dict[str, Any]) -> None:
    """Envoie un event JSON à tous les clients WebSocket connectés."""
    dead: set[WebSocket] = set()
    for ws in connected_ws:
        try:
            await ws.send_json(event)
        except Exception:
            dead.add(ws)
    connected_ws -= dead


async def _auto_pull_ollama(model: str) -> None:
    """Pull un modele Ollama en background (ne bloque pas le demarrage)."""
    try:
        async with httpx.AsyncClient(timeout=600) as client:
            resp = await client.post(
                "http://localhost:11434/api/pull",
                json={"name": model, "stream": False},
            )
            if resp.status_code == 200:
                logger.info("[startup] Ollama : %s pulle avec succes", model)
            else:
                logger.warning("[startup] Ollama pull %s : HTTP %s", model, resp.status_code)
    except Exception as e:
        logger.warning("[startup] Ollama pull erreur : %s", e)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Démarrage : init DB + enregistrement des agents disponibles."""
    logger.info("Démarrage JARVIS…")
    init_db()

    try:
        from scripts.db_migrations import run_startup_migrations

        run_startup_migrations()
    except Exception as e:
        logger.critical("Erreur migrations au démarrage : %s", e)

    # Cache Contacts.app (résolution numéro / email → nom affiché)
    # build_cache() est synchrone et peut bloquer >20s : lancé en background
    # task pour ne pas retarder le démarrage FastAPI.
    async def _build_contacts_cache():
        try:
            from integrations.contacts import contacts_reader

            if contacts_reader.is_available():
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, contacts_reader.build_cache)
                logger.info("[contacts] Cache : %d entrées", len(contacts_reader._cache))
                for handle, cn in list(contacts_reader._cache.items())[:5]:
                    logger.info("[contacts]   %s → %s", handle, cn)
        except Exception as e:
            logger.warning("[contacts] init cache : %s", e)

    asyncio.create_task(_build_contacts_cache())

    # Diagnostic iMessage : lecture de chat.db (nécessite Full Disk Access pour le terminal / Cursor).
    try:
        import sqlite3 as _sqlite3

        _chat_db = Path.home() / "Library" / "Messages" / "chat.db"
        if _chat_db.exists():
            _conn = _sqlite3.connect(f"file:{_chat_db}?mode=ro", uri=True)
            _n = _conn.execute("SELECT COUNT(*) FROM message").fetchone()[0]
            _conn.close()
            logger.info("[imessage] chat.db accessible — %s messages dans la table message", _n)
        else:
            logger.warning("[imessage] chat.db absent à %s", _chat_db)
    except Exception as _e:
        logger.error("[imessage] Impossible de lire chat.db : %s", _e)
        logger.error(
            "[imessage] → Réglages Système > Confidentialité et sécurité > Accès complet au disque : "
            "ajoute Terminal, iTerm ou Cursor selon l’app qui lance JARVIS."
        )

    # Enregistrement des agents
    register_agent(info_agent)
    register_agent(school_agent)
    register_agent(productivity_agent)
    register_agent(coach_agent)
    register_agent(journal_agent)
    register_agent(memory_agent)
    register_agent(devops_agent)
    logger.info("Agents enregistrés : devops, info, school, productivity, coach, journal, memory")

    # Création des dossiers de sortie
    Path(config.SCHOOL_OUTPUT_DIR).mkdir(parents=True, exist_ok=True)
    Path(config.UPLOAD_DIR).mkdir(parents=True, exist_ok=True)

    # Calendar.app : lancement préventif pour éviter les -600 au premier AppleScript.
    try:
        subprocess.Popen(
            ["open", "-a", "Calendar"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        logger.info("[startup] Calendar.app ouvert")
    except Exception as e:
        logger.warning("[startup] Impossible d'ouvrir Calendar.app : %s", e)

    if not config.DEEPSEEK_API_KEY:
        logger.warning("⚠️  DEEPSEEK_API_KEY manquant — copie .env.example en .env et ajoute ta clé")

    # ── Helper : scan initial de l'analyse relationnelle ──
    async def _initial_relationship_scan(analyzer, reader) -> None:
        try:
            from database import get_analysis_cursor

            logger.info("[analyzer] Lancement du scan initial iMessage…")
            stats = await analyzer.run_initial_scan()
            logger.info("[analyzer] Scan initial terminé : %s", stats)
        except Exception as e:
            logger.error("[analyzer] Scan initial échoué : %s", e)

    # ── iMessage sourcing (lecture seule, chat.db) ──
    _imessage_scan_task = None
    _imessage_relationship_task = None
    try:
        if config.IMESSAGE_SOURCING_ENABLED:
            from integrations.imessage_reader import imessage_reader

            if imessage_reader.is_available():
                from scripts.relationship_analyzer import analyzer

                _imessage_relationship_task = asyncio.create_task(
                    _initial_relationship_scan(analyzer, imessage_reader),
                    name="imessage_relationship_scan",
                )
                _imessage_scan_task = asyncio.create_task(
                    imessage_reader.periodic_scan(config.IMESSAGE_SCAN_INTERVAL),
                    name="imessage_sourcing_scan",
                )
                logger.info(
                    "iMessage sourcing activé (lecture seule, scan %ss)",
                    config.IMESSAGE_SCAN_INTERVAL,
                )
            else:
                logger.warning(
                    "[startup] imessage_reader indisponible "
                    "(Full Disk Access manquant ?)"
                )
        else:
            logger.info(
                "iMessage sourcing désactivé (IMESSAGE_SOURCING_ENABLED=false)"
            )
    except ImportError:
        logger.warning(
            "[startup] modules iMessage reader / analyzer non importables"
        )
    except Exception as e:
        logger.warning("[startup] iMessage sourcing erreur : %s", e)

    # ── iMessage bridge (envoi) — VOLONTAIREMENT NON DÉMARRÉ ──
    # Le bridge n'est pas lancé au startup. L'envoi reste bloqué
    # au niveau de integrations/imessage.py tant que
    # IMESSAGE_SEND_ENABLED=false (défaut .env).

    # Email watcher — surveillance proactive des mails non lus.
    email_task = asyncio.create_task(email_watcher.start())
    logger.info(
        "Email watcher lancé — scan toutes les %.0fs",
        config.EMAIL_CHECK_INTERVAL,
    )

    try:
        from scripts.sync_contacts import sync_people_names

        asyncio.create_task(sync_people_names())
        logger.info("[startup] sync people ↔ Contacts.app programmée (background)")
    except Exception as e:
        logger.warning("[startup] sync contacts indisponible : %s", e)

    # Enregistrement de la machine locale (Mac Mini par défaut) + activation
    daemon_task = None
    try:
        local_device_id = config.DEVICE_ID or "mac_mini"
        register_device(
            device_id=local_device_id,
            device_name=config.DEVICE_NAME or f"Mac Mini ({local_device_id})",
            device_type="desktop",
        )
        if get_active_device() is None:
            set_active_device(local_device_id)
        logger.info("[startup] machine locale enregistrée : %s", local_device_id)
    except Exception as e:
        logger.warning("[startup] register_device locale : %s", e)

    # Daemon JARVIS — sentinelle permanente (screen watcher, notif proactives, wake word)
    if getattr(config, "DAEMON_ENABLED", True):
        try:
            from scripts.jarvis_daemon import daemon

            daemon_task = asyncio.create_task(daemon.start(), name="jarvis_daemon")
            logger.info("[startup] daemon JARVIS démarré (mode: veille)")
        except Exception as e:
            logger.warning("[startup] daemon JARVIS non démarré : %s", e)
    else:
        logger.info("[startup] daemon désactivé (DAEMON_ENABLED=false)")

    # Auto-pull du modèle vision Ollama si dispo mais modèle manquant
    try:
        import httpx as _httpx
        resp = _httpx.get("http://localhost:11434/api/tags", timeout=3)
        if resp.status_code == 200:
            models = [m["name"] for m in resp.json().get("models", [])]
            vision_model = getattr(config, "SCREEN_VISION_MODEL", "qwen2.5vl:7b")
            if not any(vision_model.split(":")[0] in m for m in models):
                logger.info("[startup] Ollama : pull %s en background...", vision_model)
                asyncio.create_task(_auto_pull_ollama(vision_model))
    except Exception:
        pass

    # Audio Daemon — micro natif Mac Mini (wake word + conversation mains libres)
    audio_daemon_task = None
    if getattr(config, "AUDIO_DAEMON_ENABLED", False):
        try:
            from scripts.audio_daemon import audio_daemon

            audio_daemon.set_broadcast(broadcast_ws)
            audio_daemon_task = asyncio.create_task(audio_daemon.start(), name="audio_daemon")
            logger.info("[startup] Audio daemon démarré (wake word + micro natif)")
        except Exception as e:
            logger.warning("[startup] Audio daemon non démarré : %s", e)

    logger.info(f"JARVIS prêt → http://localhost:{config.WEB_PORT}")

    from scripts.scheduler import start_scheduler

    start_scheduler()
    logger.info("Scheduler APScheduler démarré (briefing matin, tâches en retard)")

    yield

    from scripts.scheduler import shutdown_scheduler

    shutdown_scheduler()
    if imessage_bridge is not None:
        imessage_bridge.stop()
    # Annulation des tâches de sourcing iMessage
    for _task, _label in [
        (_imessage_scan_task, "imessage_scan"),
        (_imessage_relationship_task, "imessage_relationship"),
    ]:
        if _task is not None:
            _task.cancel()
            try:
                await _task
            except (asyncio.CancelledError, Exception):
                pass

    email_watcher.stop()
    if email_task is not None:
        email_task.cancel()
        try:
            await email_task
        except (asyncio.CancelledError, Exception):
            pass

    if daemon_task is not None:
        try:
            from scripts.jarvis_daemon import daemon as _daemon

            _daemon.stop()
        except Exception:
            pass
        daemon_task.cancel()
        try:
            await daemon_task
        except (asyncio.CancelledError, Exception):
            pass

    if audio_daemon_task is not None:
        try:
            from scripts.audio_daemon import audio_daemon as _audio_daemon

            await _audio_daemon.stop()
        except Exception:
            pass
        audio_daemon_task.cancel()
        try:
            await audio_daemon_task
        except (asyncio.CancelledError, Exception):
            pass

    logger.info("Arrêt JARVIS.")


app = FastAPI(
    title="JARVIS",
    description="Assistant personnel multi-agents",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://0.0.0.0:3000",
        "http://localhost:9000",
        "http://127.0.0.1:9000",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5174",
        "http://localhost:8080",
        "http://127.0.0.1:8080",
        "http://localhost:8081",
        "http://127.0.0.1:8081",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routes HTTP ─────────────────────────────────────────────


def _computer_status_payload() -> dict:
    try:
        from integrations.computer import computer as _c

        return {
            "available": _c.allowed,
            "shell": _c.shell,
        }
    except Exception:
        return {"available": False, "shell": config.COMPUTER_SHELL}


def _code_executor_status_payload() -> dict:
    try:
        from integrations.code_executor import code_executor
        return {
            "available": code_executor.available if code_executor else False,
            "engine": "advanced" if (code_executor and code_executor.available) else "basic",
        }
    except Exception:
        return {"available": False, "engine": "basic"}


def _safe_memory_stats() -> dict:
    try:
        return count_memory_stats()
    except Exception as e:
        logger.error("count_memory_stats : %s", e)
        return {}


@app.get("/api/status")
async def api_status():
    """Stats d'utilisation, agents actifs, coûts."""
    try:
        stats = get_usage_stats()
    except Exception as e:
        logger.error(f"Erreur get_usage_stats : {e}")
        stats = {"msg_count": 0, "total_in": 0, "total_out": 0, "total_cost": 0.0}

    loc_payload: dict[str, Any] = {}
    try:
        from database.location_helpers import get_active_location_patterns, get_today_visits
        from integrations.location import location_manager

        st = await location_manager.get_status()
        summary = await location_manager.get_daily_summary()
        loc_payload = {
            "tracking": getattr(config, "LOCATION_TRACKING", True),
            "place_radius_m": int(getattr(config, "LOCATION_PLACE_RADIUS", 100)),
            "status": st,
            "summary_today": {
                "place_count": len(summary.get("visits") or []),
                "trip_count": summary.get("trip_count"),
                "total_distance_km": summary.get("total_distance_km"),
            },
            "today_route": [
                v.get("place_name")
                for v in (get_today_visits() or [])
                if v.get("place_name")
            ],
            "pattern_count": len(get_active_location_patterns()),
        }
    except Exception as e:
        logger.debug("api_status location : %s", e)
        loc_payload = {"error": str(e)}

    return {
        "user": config.USER_NAME,
        "models": {
            "fast": config.DEEPSEEK_FAST_MODEL,
            "main": config.DEEPSEEK_MAIN_MODEL,
        },
        "agents_registered": ["info", "school", "productivity", "coach", "journal", "memory"],
        "today": stats,
        "audio": {
            "stt_available": stt is not None and getattr(stt, "available", False),
            "stt_engine": "elevenlabs_scribe" if (stt and getattr(stt, "available", False)) else "none",
            "tts_available": tts is not None and getattr(tts, "available", False),
            "tts_backend": tts.get_backend_name() if tts else "none",
            "tts_voice": config.TTS_VOICE,
        },
        "voice_conversation": {
            "silence_duration_ms": getattr(config, "VOICE_SILENCE_DURATION_MS", 1200),
            "min_speech_ms": getattr(config, "VOICE_MIN_SPEECH_MS", 400),
            "max_tokens": getattr(config, "VOICE_MAX_TOKENS", 500),
        },
        "imessage": {
            "available": imessage_bridge is not None and imessage_bridge.is_available(),
            "target": config.IMESSAGE_TARGET,
            "prefix": config.IMESSAGE_PREFIX or None,
            "sourcing_enabled": config.IMESSAGE_SOURCING_ENABLED,
            "send_enabled": config.IMESSAGE_SEND_ENABLED,
            "scan_interval": config.IMESSAGE_SCAN_INTERVAL,
        },
        "email_watcher": {
            "running": email_watcher.running,
            "check_interval": email_watcher.check_interval,
            "processed_count": len(email_watcher.last_processed_ids),
        },
        "computer": _computer_status_payload(),
        "code_executor": _code_executor_status_payload(),
        "memory": _safe_memory_stats(),
        "location": loc_payload,
        "audio_daemon": _audio_daemon_status_payload(),
    }


@app.get("/api/stats/weekly")
async def api_stats_weekly(days: int = 7):
    """Série d'activité quotidienne (messages, échanges vocaux, tokens, coût).

    Retourne aussi la variation jour/jour (dernier jour vs avant-dernier) pour
    les cartes de tendance du dashboard. `days` borné à [2, 90].
    """
    days = max(2, min(days, 90))
    try:
        daily = get_daily_activity_stats(days)
    except Exception as e:
        logger.error("get_daily_activity_stats : %s", e)
        raise HTTPException(500, "Statistiques indisponibles") from e

    def _pct(cur: float, prev: float) -> float | None:
        if prev <= 0:
            return None
        return round((cur - prev) / prev * 100, 1)

    last, prev = daily[-1], daily[-2]
    change = {
        "messages_pct": _pct(last["msg_count"], prev["msg_count"]),
        "voice_pct": _pct(last["voice_count"], prev["voice_count"]),
        "interactions_pct": _pct(
            last["tokens_in"] + last["tokens_out"],
            prev["tokens_in"] + prev["tokens_out"],
        ),
        "cost_pct": _pct(last["cost"], prev["cost"]),
    }
    totals = {
        "msg_count": sum(d["msg_count"] for d in daily),
        "voice_count": sum(d["voice_count"] for d in daily),
        "tokens_in": sum(d["tokens_in"] for d in daily),
        "tokens_out": sum(d["tokens_out"] for d in daily),
        "cost": round(sum(d["cost"] for d in daily), 6),
    }
    return {"days": daily, "change": change, "totals": totals}


@app.get("/api/costs")
async def api_costs():
    """Dépenses LLM (jour / 7 jours / mois, par modèle) + budget configuré."""
    try:
        return get_cost_summary()
    except Exception as e:
        logger.error("get_cost_summary : %s", e)
        raise HTTPException(500, "Coûts indisponibles") from e


@app.get("/api/backups")
async def api_backups_list():
    """Sauvegardes SQLite présentes (plus récente en premier)."""
    from scripts.db_maintenance import list_backups

    return {
        "backups": list_backups(),
        "dir": config.BACKUP_DIR,
        "keep": config.BACKUP_KEEP,
        "enabled": config.BACKUP_ENABLED,
    }


@app.post("/api/backups/run")
async def api_backups_run():
    """Déclenche une sauvegarde immédiate (VACUUM INTO + rotation)."""
    from scripts.db_maintenance import run_backup

    report = await asyncio.to_thread(run_backup)
    if not report.get("ok"):
        raise HTTPException(500, report.get("error", "Sauvegarde échouée"))
    return report


@app.post("/api/maintenance/run")
async def api_maintenance_run():
    """Purge de rétention + optimisation FTS/WAL immédiates."""
    from scripts.db_maintenance import run_maintenance

    try:
        return await asyncio.to_thread(run_maintenance)
    except Exception as e:
        logger.exception("run_maintenance : %s", e)
        raise HTTPException(500, "Maintenance échouée") from e


@app.get("/api/rituals/today")
async def api_rituals_today():
    """Rituels du jour : roast, debrief, citation, score productivité."""
    from scripts.rituals import compute_productivity_score

    from database import get_daily_ritual

    row = get_daily_ritual(datetime.now().strftime("%Y-%m-%d")) or {}
    return {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "roast": row.get("roast"),
        "debrief": row.get("debrief"),
        "quote": row.get("quote"),
        "weekly_debrief": row.get("weekly_debrief"),
        "productivity": compute_productivity_score(),
    }


@app.post("/api/rituals/{ritual}/run")
async def api_rituals_run(ritual: str):
    """Déclenche un rituel à la demande : roast, debrief, quote ou weekly."""
    from scripts import rituals

    runners = {
        "roast": rituals.daily_roast,
        "debrief": rituals.evening_debrief,
        "quote": rituals.daily_quote,
        "weekly": rituals.weekly_debrief,
    }
    fn = runners.get(ritual)
    if fn is None:
        raise HTTPException(404, f"Rituel inconnu : {ritual} (roast | debrief | quote)")
    try:
        return await fn()
    except Exception as e:
        logger.exception("rituel %s : %s", ritual, e)
        raise HTTPException(500, f"Rituel {ritual} échoué") from e


@app.get("/api/productivity/score")
async def api_productivity_score():
    """Score de productivité hebdomadaire (déterministe, 0-100)."""
    from scripts.rituals import compute_productivity_score

    return compute_productivity_score()


@app.get("/api/mood/signals")
async def api_mood_signals(days: int = 14):
    """Signaux comportementaux quotidiens (écran + messages). Aucun diagnostic."""
    from database import get_mood_signals

    return {"signals": get_mood_signals(days)}


@app.get("/api/predictions/messages")
async def api_predictions_messages(limit: int = 20):
    """Prédiction heuristique de qui va écrire prochainement (iMessage)."""
    from scripts.message_predictor import predict_for_all_contacts

    return {"predictions": await predict_for_all_contacts(limit=limit)}


@app.get("/api/places/favorites")
async def api_places_favorites(limit: int = 10):
    """Lieux les plus fréquentés (visit_count >= seuil configuré)."""
    from scripts.favorite_places import get_favorite_places

    return {"places": get_favorite_places(limit=limit)}


@app.get("/api/places/missed-opportunities")
async def api_places_missed_opportunities():
    """Lieux favoris délaissés depuis plus de `OPPORTUNITY_MIN_DAYS_NAMED` jours."""
    from scripts.favorite_places import detect_missed_opportunities

    return {"opportunities": detect_missed_opportunities()}


@app.get("/api/doomscroll")
async def api_doomscroll(days: int = 7):
    """Journées où le temps sur les apps à risque dépasse le seuil configuré."""
    from scripts.doomscroll_detector import detect_doomscrolling

    return {"days": detect_doomscrolling(days=days)}


@app.get("/api/procrastination/cost")
async def api_procrastination_cost():
    """Coût (temps + estimation monétaire optionnelle) des tâches laissées en plan."""
    from scripts.procrastination_cost import get_procrastination_cost

    return get_procrastination_cost()


@app.get("/api/jarvis-journal")
async def api_jarvis_journal(days: int = 7):
    """Journal de JARVIS — son point de vue sur les derniers jours."""
    from database import get_jarvis_journal_entries

    return {"entries": get_jarvis_journal_entries(days=days)}


@app.post("/api/jarvis-journal/generate")
async def api_jarvis_journal_generate(payload: dict | None = None):
    """Force la génération de l'entrée du jour (ou d'une date donnée)."""
    from scripts.jarvis_journal import generate_journal_entry

    date = (payload or {}).get("date")
    return await generate_journal_entry(date=date)


@app.get("/api/day-scores")
async def api_day_scores(metric: str = "exceptional_score", limit: int = 10, days: int = 90):
    """Top jours par score (jour exceptionnel ou indice de chance)."""
    from database import get_top_days

    if metric not in ("exceptional_score", "luck_score"):
        raise HTTPException(400, "metric ∈ {exceptional_score, luck_score}")
    return {"days": get_top_days(metric=metric, limit=limit, days=days)}


@app.get("/api/day-scores/{date}")
async def api_day_score_detail(date: str):
    """Score détaillé (exceptionnel + chance) d'une date donnée."""
    from database import get_day_score

    score = get_day_score(date)
    if not score:
        raise HTTPException(404, "Aucun score pour cette date")
    return score


@app.get("/api/presence")
async def api_presence():
    """Présence au bureau détectée par le son (micro daemon audio)."""
    from scripts.presence import get_today_sessions, presence_detector

    return {
        **presence_detector.get_status(),
        "today_sessions": get_today_sessions(),
    }


@app.get("/api/self-healing/status")
async def api_self_healing_status():
    """État du self-healing : activé ?, dernier patch, cooldown."""
    from scripts.self_healing import _load_state

    return {
        "enabled": config.SELF_HEALING_ENABLED,
        "auto_apply": config.SELF_HEALING_AUTO_APPLY,
        "state": _load_state(),
    }


@app.post("/api/self-healing/diagnose")
async def api_self_healing_diagnose(body: dict = None):
    """Déclenche un diagnostic (+ patch si auto-apply) à la demande, sur un log fourni."""
    from scripts.self_healing import handle_crash_loop

    log_tail = (body or {}).get("log_tail", "")
    if not log_tail.strip():
        raise HTTPException(400, "Le champ 'log_tail' est requis.")
    return await handle_crash_loop(log_tail)


@app.post("/api/quality/ci/run")
async def api_quality_ci_run():
    """Déclenche la CI locale (lint + tests + build front optionnel) à la demande."""
    from scripts.local_ci import run_local_ci

    return await asyncio.to_thread(run_local_ci)


@app.post("/api/quality/ci/install-hook")
async def api_quality_ci_install_hook(force: bool = False):
    """Installe le hook pre-commit qui déclenche la CI locale à chaque commit."""
    from scripts.install_git_hooks import install

    result = install(force=force)
    if not result.get("ok"):
        raise HTTPException(409, result.get("reason", "Installation du hook refusée."))
    return result


@app.get("/api/quality/duplicates")
async def api_quality_duplicates():
    """Blocs de code dupliqué détectés (scan périodique, rapport seul)."""
    from scripts.duplicate_scanner import list_open_duplicates

    return {"duplicates": list_open_duplicates()}


@app.post("/api/quality/duplicates/scan")
async def api_quality_duplicates_scan():
    """Déclenche un scan de code dupliqué immédiat sur la codebase JARVIS."""
    from scripts.duplicate_scanner import scan_and_report

    return await asyncio.to_thread(scan_and_report)


@app.get("/api/devagent/{project_id}/deployments")
async def api_devagent_deployments(project_id: int):
    """Historique des déploiements staging du projet."""
    from database.devagent import get_deployments

    project = devagent_db.get_project(project_id)
    if not project:
        raise HTTPException(404, "Projet DevAgent introuvable.")
    return {"deployments": get_deployments(project_id)}


@app.post("/api/devagent/{project_id}/deploy")
async def api_devagent_deploy(project_id: int):
    """Déploie manuellement le commit HEAD en staging et valide avec la suite de tests."""
    from pathlib import Path

    from agents.devagent.staging import deploy_to_staging

    project = devagent_db.get_project(project_id)
    if not project:
        raise HTTPException(404, "Projet DevAgent introuvable.")
    return await asyncio.to_thread(deploy_to_staging, project_id, Path(project["isolation_path"]))


@app.post("/api/devagent/{project_id}/pr")
async def api_devagent_pr(project_id: int, open_pr: bool = False):
    """Génère description + changelog de PR ; ouvre la PR si `gh` + remote disponibles."""
    from pathlib import Path

    from agents.devagent.pr import generate_pr_description, open_pull_request

    project = devagent_db.get_project(project_id)
    if not project:
        raise HTTPException(404, "Projet DevAgent introuvable.")
    project_path = Path(project["isolation_path"])

    result = await generate_pr_description(project_path, project.get("name") or project["slug"])
    if not result.get("ok"):
        return result
    if open_pr:
        body = Path(result["path"]).read_text(encoding="utf-8")
        result["gh"] = await asyncio.to_thread(open_pull_request, project_path, result["title"], body)
    return result


@app.post("/api/devagent/{project_id}/rebase")
async def api_devagent_rebase(project_id: int, onto: str = "main"):
    """Rebase sûr : résout les conflits triviaux, abandonne sinon (jamais partiel)."""
    from pathlib import Path

    from agents.devagent.git_ops import safe_rebase

    project = devagent_db.get_project(project_id)
    if not project:
        raise HTTPException(404, "Projet DevAgent introuvable.")
    return await asyncio.to_thread(safe_rebase, Path(project["isolation_path"]), onto)


@app.post("/api/devagent/{project_id}/refactor")
async def api_devagent_refactor(project_id: int):
    """Refactore le plus gros bloc dupliqué du projet (tests-gated, réversible)."""
    from pathlib import Path

    from agents.devagent.refactor import refactor_top_duplicate

    project = devagent_db.get_project(project_id)
    if not project:
        raise HTTPException(404, "Projet DevAgent introuvable.")
    return await refactor_top_duplicate(Path(project["isolation_path"]))


@app.get("/api/quality/security")
async def api_quality_security():
    """Constats de l'audit sécurité (secrets, patterns dangereux)."""
    from scripts.security_audit import list_open_findings

    return {"findings": list_open_findings()}


@app.post("/api/quality/security/scan")
async def api_quality_security_scan():
    """Déclenche un audit sécurité immédiat sur la codebase JARVIS."""
    from scripts.security_audit import scan_and_report

    return await asyncio.to_thread(scan_and_report)


@app.post("/api/quality/security/{finding_id}/fix")
async def api_quality_security_fix(finding_id: int):
    """Applique le correctif mécanique (redaction) — requiert SECURITY_AUTO_FIX_ENABLED."""
    from database import get_security_findings
    from scripts.security_audit import apply_safe_fix

    finding = next((f for f in get_security_findings("open", limit=1000) if f["id"] == finding_id), None)
    if not finding:
        raise HTTPException(404, "Constat introuvable ou déjà résolu.")
    return await asyncio.to_thread(apply_safe_fix, finding)


@app.post("/api/quality/tests/generate")
async def api_quality_generate_tests():
    """Génère des tests pour les fonctions non couvertes (opt-in, cf. .env.example)."""
    from scripts.test_coverage_scan import run_test_generation

    return await run_test_generation()


@app.get("/api/migrations/status")
async def api_migrations_status():
    """Migrations SQLite appliquées / en attente."""
    from scripts.db_migrations import migration_status

    return migration_status()


@app.post("/api/migrations/run")
async def api_migrations_run():
    """Applique les migrations en attente (sauvegarde automatique préalable)."""
    from scripts.db_migrations import apply_pending_migrations

    report = await asyncio.to_thread(apply_pending_migrations)
    if not report["ok"]:
        raise HTTPException(500, report["error"] or "Migration échouée")
    return report


@app.get("/api/stats/compare")
async def api_stats_compare():
    """Comparatif toi vs toi : cette semaine vs la précédente, ton neutre."""
    from database import get_week_comparison

    return get_week_comparison()


@app.get("/api/commitments")
async def api_commitments_list(status: str = "open"):
    """Engagements pris par l'utilisateur (promesses traquées)."""
    from database import get_commitments

    if status not in ("open", "kept", "dropped"):
        raise HTTPException(400, "status ∈ {open, kept, dropped}")
    return {"commitments": get_commitments(status)}


@app.patch("/api/commitments/{commitment_id}")
async def api_commitments_update(commitment_id: int, body: dict):
    """Marque un engagement tenu ('kept') ou abandonné ('dropped')."""
    from database import update_commitment_status

    status = (body or {}).get("status")
    if status not in ("open", "kept", "dropped"):
        raise HTTPException(400, "status ∈ {open, kept, dropped}")
    if not update_commitment_status(commitment_id, status):
        raise HTTPException(404, f"Engagement #{commitment_id} introuvable")
    return {"ok": True, "id": commitment_id, "status": status}


@app.get("/api/commitments/consistency")
async def api_commitments_consistency(days: int = 90):
    """Score de cohérence promesses/actions sur les `days` derniers jours."""
    from scripts.commitment_consistency import get_consistency_score

    return get_consistency_score(days=days)


@app.get("/api/dnd")
async def api_dnd_status():
    """État du mode « silence total sauf feu »."""
    from database import get_dnd_status

    return get_dnd_status()


@app.post("/api/dnd")
async def api_dnd_enable(body: dict = None):
    """Active le DND. body: {\"minutes\": 120} (défaut 120). Seul l'urgent passe."""
    from database import set_dnd

    minutes = int((body or {}).get("minutes") or 120)
    minutes = max(1, min(minutes, 24 * 60))
    until = set_dnd(minutes)
    return {"active": True, "until": until}


@app.delete("/api/dnd")
async def api_dnd_disable():
    """Coupe le DND immédiatement."""
    from database import clear_dnd, get_dnd_status

    clear_dnd()
    return get_dnd_status()


@app.get("/api/meetings")
async def api_meetings_list(limit: int = 10):
    """Réunions captées et résumées (table recordings, label 'réunion')."""
    from database import get_db

    lim = max(1, min(limit, 50))
    with get_db() as conn:
        rows = conn.execute(
            """SELECT id, title, created_at, duration_seconds, summary, actions_taken
               FROM recordings WHERE label = 'réunion'
               ORDER BY created_at DESC LIMIT ?""",
            (lim,),
        ).fetchall()
    return {"meetings": [dict(r) for r in rows]}


@app.get("/api/memory")
async def api_memory_get():
    """Retourne le life profile + les fiches people + documents école."""
    try:
        documents = get_school_documents(limit=50)
    except Exception as e:
        logger.error(f"Erreur get_school_documents : {e}")
        documents = []

    return {
        "life_profile": get_life_profile(),
        "people": get_all_people(),
        "recent_episodes": get_recent_episodes(limit=20),
        "school_documents": documents,
    }


@app.get("/api/recordings")
async def api_recordings_list(limit: int = 20):
    """Liste des enregistrements continus (sans transcription complète)."""
    lim = max(1, min(limit, 100))
    try:
        rows = get_recordings(limit=lim)
    except Exception as e:
        logger.exception("api_recordings_list : %s", e)
        raise HTTPException(500, str(e)) from e
    return {"recordings": rows}


@app.get("/api/recordings/{recording_id}")
async def api_recordings_detail(recording_id: int):
    """Détail d'un enregistrement (transcription + synthèse JSON)."""
    row = get_recording(recording_id)
    if not row:
        raise HTTPException(404, "Enregistrement introuvable")
    if config.RECORDING_SUMMARY_ONLY and row.get("transcription"):
        row = {**row, "transcription": "[omis — RECORDING_SUMMARY_ONLY dans la configuration]"}
    return row


# ── École : documents uploadés + fichiers produits ──────────

PDF_EXT = {".pdf"}
TEXT_EXT = {".txt", ".md"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp"}


def _extract_text_from_upload(filepath: Path) -> tuple[str, str]:
    """Extrait le texte d'un fichier uploadé. Retourne (texte, doc_type)."""
    ext = filepath.suffix.lower()

    if ext in PDF_EXT:
        try:
            doc = fitz.open(str(filepath))
            text = "\n\n".join(page.get_text() for page in doc)
            doc.close()
            return text.strip(), "cours"
        except Exception as e:
            logger.error(f"Erreur extraction PDF {filepath.name} : {e}")
            return "", "cours"

    if ext in TEXT_EXT:
        try:
            return filepath.read_text(encoding="utf-8", errors="replace").strip(), "cours"
        except Exception as e:
            logger.error(f"Erreur lecture texte {filepath.name} : {e}")
            return "", "cours"

    if ext in IMAGE_EXT:
        # OCR à brancher en Phase 4 (Tesseract ou Claude vision)
        return "", "image"

    return "", "autre"


@app.post("/upload")
async def upload(file: UploadFile):
    """Upload d'un document scolaire.

    - PDF : extraction texte via pymupdf (`fitz`)
    - .txt / .md : lecture directe
    - images : sauvegarde brute (OCR à venir)

    Le document est référencé dans `school_documents` (titre = nom sans extension,
    content = texte extrait, doc_type, file_path).
    """
    upload_dir = Path(config.UPLOAD_DIR)
    upload_dir.mkdir(parents=True, exist_ok=True)

    if not file.filename:
        raise HTTPException(400, "Nom de fichier manquant")

    safe_name = Path(file.filename).name  # vire les / éventuels
    dest = upload_dir / safe_name
    try:
        content = await file.read()
        dest.write_bytes(content)
    except Exception as e:
        logger.error(f"Erreur écriture upload {safe_name} : {e}")
        raise HTTPException(500, f"Échec écriture : {e}")

    text, doc_type = _extract_text_from_upload(dest)
    title = dest.stem

    try:
        doc_id = save_school_document(
            title=title, content=text, doc_type=doc_type, file_path=str(dest),
        )
    except Exception as e:
        logger.error(f"Erreur DB upload {safe_name} : {e}")
        doc_id = None

    logger.info(
        f"Upload : {safe_name} ({len(content)} octets, "
        f"texte extrait : {len(text)} chars, doc_id={doc_id})"
    )

    return {
        "status": "ok",
        "filename": safe_name,
        "size": len(content),
        "content_length": len(text),
        "doc_type": doc_type,
        "doc_id": doc_id,
    }


def _outputs_root() -> Path:
    """Racine résolue pour les fichiers servis par /api/outputs."""
    return Path(config.SCHOOL_OUTPUT_DIR).resolve().parent  # data/outputs/


@app.get("/api/outputs")
async def api_outputs_list():
    """Liste tous les fichiers produits dans data/outputs/school/ (récursif).

    Retourne pour chaque fichier : filename, subject (sous-dossier), path relatif,
    size_kb, created_at (mtime ISO).
    """
    school_dir = Path(config.SCHOOL_OUTPUT_DIR)
    school_dir.mkdir(parents=True, exist_ok=True)
    root = _outputs_root()

    files = []
    for path in school_dir.rglob("*"):
        if not path.is_file():
            continue
        try:
            stat = path.stat()
            relative = path.resolve().relative_to(root)
            # Le sous-dossier directement sous school/ = la matière
            try:
                subject = path.resolve().relative_to(school_dir.resolve()).parts[0]
            except (ValueError, IndexError):
                subject = "divers"
            files.append({
                "filename": path.name,
                "subject": subject,
                "path": str(relative),
                "size_kb": round(stat.st_size / 1024, 2),
                "created_at": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
            })
        except Exception as e:
            logger.warning(f"Skip {path} : {e}")

    files.sort(key=lambda f: f["created_at"], reverse=True)
    return {"files": files, "count": len(files)}


@app.get("/api/outputs/{filepath:path}")
async def api_outputs_download(filepath: str):
    """Télécharge un fichier produit. `filepath` est relatif à data/outputs/.

    Sécurité : le chemin résolu doit rester sous data/outputs/ (anti path traversal).
    """
    root = _outputs_root()
    try:
        target = (root / filepath).resolve()
    except Exception:
        raise HTTPException(400, "Chemin invalide")

    # Protection path traversal
    try:
        target.relative_to(root)
    except ValueError:
        logger.warning(f"Path traversal bloqué : {filepath}")
        raise HTTPException(403, "Chemin hors du dossier autorisé")

    if not target.exists() or not target.is_file():
        raise HTTPException(404, f"Fichier introuvable : {filepath}")

    return FileResponse(target, filename=target.name)


# ── Productivité : intégrations + tâches + briefings ────────


@app.get("/api/debug/resolve/{name}")
async def api_debug_resolve(name: str):
    """Debug : résolution du handle iMessage pour un contact."""
    from database import get_db
    decoded = _decode_person_path(name)
    person = get_person(decoded) or get_person(name.strip())
    handle = _resolve_handle_with_contacts(decoded)
    steps: dict[str, Any] = {}
    if person:
        pid = person.get("id")
        with get_db() as conn:
            rp = conn.execute(
                "SELECT handle FROM relationship_profiles WHERE person_id=? AND handle IS NOT NULL LIMIT 1",
                (pid,)
            ).fetchone()
            steps["relationship_profile_handle"] = rp[0] if rp else None
    return {
        "name": decoded,
        "person_found": person is not None,
        "resolved_handle": handle,
        "steps": steps,
    }


@app.get("/api/integrations")
async def api_integrations():
    """État de chaque intégration externe.

    Les checks osascript (Mail, Calendar) sont exécutés dans un thread séparé
    avec un timeout court pour ne jamais bloquer l'event loop.
    """
    async def _check(fn, fallback, timeout: float = 2.0):
        try:
            return await asyncio.wait_for(asyncio.to_thread(fn), timeout=timeout)
        except (asyncio.TimeoutError, Exception):
            return fallback

    mail_ok, cal_status, weather_ok = await asyncio.gather(
        _check(lambda: mail_client.is_available() if mail_client else False, False),
        _check(
            lambda: calendar_client.get_status() if calendar_client else {"available": False, "error": "Non initialisé"},
            {"available": False, "error": "Timeout"},
        ),
        _check(lambda: weather.is_available() if weather else False, False),
    )
    return {
        "mail": mail_ok,
        "calendar": cal_status,
        "weather": weather_ok,
        "imessage": imessage_bridge is not None and imessage_bridge.is_available(),
        "imessage_sourcing": config.IMESSAGE_SOURCING_ENABLED,
        "imessage_send": config.IMESSAGE_SEND_ENABLED,
        "email_watcher": email_watcher.running,
        "computer": _computer_status_payload(),
        "code_executor": _code_executor_status_payload(),
        "location_tracking": getattr(config, "LOCATION_TRACKING", True),
        "audio_daemon": _audio_daemon_status_payload(),
    }


def _audio_daemon_status_payload() -> dict[str, Any]:
    """Payload pour /api/status et /api/integrations."""
    try:
        from scripts.audio_daemon import audio_daemon as _ad
        return _ad.get_status()
    except Exception:
        return {"enabled": False, "state": "idle", "error": "indisponible"}


@app.get("/api/audio-daemon/status")
async def audio_daemon_status():
    """État complet du daemon audio."""
    return _audio_daemon_status_payload()


@app.post("/api/audio-daemon/start")
async def audio_daemon_start():
    """Démarre le daemon audio (micro + wake word)."""
    from scripts.audio_daemon import audio_daemon as _ad
    if _ad.enabled:
        return {"ok": True, "message": "Déjà actif"}
    _ad.set_broadcast(broadcast_ws)
    asyncio.create_task(_ad.start())
    return {"ok": True, "message": "Daemon audio démarré"}


@app.post("/api/audio-daemon/stop")
async def audio_daemon_stop():
    """Arrête le daemon audio."""
    from scripts.audio_daemon import audio_daemon as _ad
    if not _ad.enabled:
        return {"ok": True, "message": "Déjà inactif"}
    await _ad.stop()
    return {"ok": True, "message": "Daemon audio arrêté"}


@app.post("/api/audio-daemon/wake-word")
async def audio_daemon_wake_word(body: dict[str, Any]):
    """Active/désactive le wake word. Body: {"enabled": true/false}"""
    from scripts.audio_daemon import audio_daemon as _ad
    await _ad.set_wake_word(body.get("enabled", True))
    return {"ok": True, "wake_word_enabled": _ad.wake_word_enabled}


@app.post("/api/audio-daemon/continuous")
async def audio_daemon_continuous(body: dict[str, Any]):
    """Active/désactive le mode écoute continue. Body: {"enabled": true/false}"""
    from scripts.audio_daemon import audio_daemon as _ad
    await _ad.set_continuous_mode(body.get("enabled", True))
    return {"ok": True, "continuous_mode": _ad.continuous_mode}


@app.get("/api/voice-debug")
async def api_voice_debug_logs(limit: int = 50):
    """Retourne les dernières traces de debug du pipeline vocal."""
    try:
        logs = get_voice_debug_logs(limit=limit)
    except Exception as e:
        logger.error(f"voice_debug_logs : {e}")
        raise HTTPException(500, str(e))
    return {"logs": logs}


# ── Mission Control ──────────────────────────────────────────


@app.get("/api/events/stream")
async def events_stream():
    """SSE — flux temps réel de tous les événements JARVIS.

    Le frontend MissionControl.tsx consomme ce flux pour afficher
    l'activité en temps réel (pipeline vocal, orchestration, agents, TTS).
    """
    queue: asyncio.Queue[JarvisEvent] = event_bus.subscribe()

    async def generate():
        try:
            # Envoyer l'historique récent au connect
            for evt in event_bus.get_history(30):
                yield f"data: {json.dumps(evt, ensure_ascii=False)}\n\n"
            while True:
                event = await queue.get()
                yield event.to_sse()
        except asyncio.CancelledError:
            pass
        finally:
            event_bus.unsubscribe(queue)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/mission/prompt")
async def mission_prompt(payload: dict[str, Any]):
    """Prompt depuis Mission Control — passe par l'orchestrateur normal.

    Body: {"message": "...", "conversation_id": "..."}
    """
    message = payload.get("message", "")
    if not message or not message.strip():
        raise HTTPException(status_code=400, detail="Message requis")

    conversation_id = payload.get("conversation_id", "mission-control")
    conv_id_int: int | None = None

    if isinstance(conversation_id, str) and conversation_id != "mission-control":
        try:
            conv_id_int = int(conversation_id)
        except (ValueError, TypeError):
            pass
    elif isinstance(conversation_id, (int, float)):
        conv_id_int = int(conversation_id)

    if conv_id_int is None and conversation_id == "mission-control":
        from database import create_conversation
        try:
            conv_id_int = create_conversation(agent="mission_control")
        except Exception as e:
            logger.warning("[mission] create_conversation: %s", e)
            conv_id_int = None

    result = await orchestrator.handle(message, conv_id_int)
    return result


@app.post("/api/email-watcher/catchup")
async def api_email_watcher_catchup():
    """Force un cycle de rattrapage (réhydratation DB + analyse des non-lus absents de ``email_summaries``).

    Réinitialise aussi le cache de disponibilité Mail (contourne le cooldown 120s après timeout).
    Ouvre Mail.app avant d'appeler si le dernier test a expiré.
    """
    try:
        result = await email_watcher.run_catchup_cycle()
        return result
    except Exception as e:
        logger.exception("api_email_watcher_catchup : %s", e)
        raise HTTPException(status_code=500, detail=str(e)) from e


# ── Réglages dynamiques (sans redémarrage) ──────────────────

_VALID_TTS_ENGINES = {"elevenlabs", "edge", "macos", "kokoro"}


@app.get("/api/settings/tts")
async def api_get_tts_setting():
    """Retourne le moteur TTS actif (DB ou fallback .env)."""
    from database import get_setting as _gs
    engine = _gs("tts_engine", getattr(config, "TTS_ENGINE", "edge") or "edge")
    return {"engine": engine}


@app.patch("/api/settings/tts")
async def api_set_tts_setting(body: dict):
    """Change le moteur TTS à la volée (sans redémarrage).

    Payload : ``{"engine": "elevenlabs" | "edge" | "macos"}``
    """
    from database import set_setting as _ss
    from audio.tts import get_tts_by_name

    engine = (body.get("engine") or "").lower().strip()
    if engine not in _VALID_TTS_ENGINES:
        from fastapi import HTTPException
        raise HTTPException(
            status_code=422,
            detail=f"Moteur invalide : {engine!r}. Valeurs acceptées : {sorted(_VALID_TTS_ENGINES)}",
        )

    # Vérifie la disponibilité du moteur demandé
    target = get_tts_by_name(engine)
    if not getattr(target, "available", False):
        from fastapi import HTTPException
        raise HTTPException(
            status_code=422,
            detail=f"Le moteur '{engine}' n'est pas disponible sur ce système. "
                   f"Vérifiez ELEVENLABS_API_KEY/VOICE_ID (elevenlabs), "
                   f"edge-tts installé (edge) ou commandes say/afconvert (macos).",
        )

    _ss("tts_engine", engine)
    logger.info("[TTS] Moteur changé → %s", engine)
    return {"engine": engine, "ok": True}


# ── Notifications (email watcher + alertes patterns) ────────


@app.get("/api/notifications")
async def api_notifications_unread():
    """Liste des notifications non lues, triées par priorité."""
    try:
        return {"notifications": get_unread_notifications()}
    except Exception as e:
        logger.error("Erreur get_unread_notifications : %s", e)
        return {"notifications": []}


@app.get("/api/logs")
async def api_logs(type: str | None = None, limit: int = 100):
    """Logs systeme des actions LLM (recent -> ancien). Inclut DevAgent si pas de filtre."""
    try:
        logs = get_llm_logs(limit=limit, action_type=type)
        return {"logs": logs, "count": len(logs)}
    except Exception as e:
        logger.error("Erreur get_llm_logs : %s", e)
        return {"logs": [], "count": 0}


# ── DevAgent autonome (interview -> spec -> boucle dev isolee) ─────────────


@app.post("/api/devagent/autorun")
async def api_devagent_autorun(payload: dict):
    """Agent autonome bout en bout : interview auto-répondue → spec → boucle, zéro humain.

    Body : {"description": "...", "name": "..." (optionnel)}.
    """
    from agents.devagent.autorun import autorun_project

    description = (payload or {}).get("description", "").strip()
    if not description:
        raise HTTPException(400, "Le champ 'description' est requis.")
    try:
        return await autorun_project(description, name=(payload or {}).get("name"))
    except RuntimeError as e:
        raise HTTPException(502, str(e)) from e


@app.post("/api/devagent/start")
async def devagent_start(name: str):
    """Demarre un projet DevAgent et renvoie la premiere question d'interview."""
    if not name or not name.strip():
        raise HTTPException(400, "Le nom du projet est requis.")
    clean_name = name.strip()
    slug = slugify(clean_name)
    if devagent_db.get_project_by_slug(slug):
        raise HTTPException(409, f"Un projet avec le slug '{slug}' existe deja.")
    from agents.devagent.spec_builder import build_isolation_path

    isolation = str(build_isolation_path(slug))
    project_id = devagent_db.create_dev_project(slug, clean_name, isolation)
    first_question = await next_interview_step(project_id, {})
    return {"project_id": project_id, "first_question": first_question}


@app.post("/api/devagent/{project_id}/answer")
async def devagent_answer(project_id: int, payload: InterviewAnswer):
    """Soumet une reponse d'interview ; verrouille la spec si l'interview est terminee."""
    project = devagent_db.get_project(project_id)
    if not project:
        raise HTTPException(404, "Projet DevAgent introuvable.")
    if project.get("status") not in ("interviewing",):
        raise HTTPException(400, f"Interview deja terminee (status={project.get('status')}).")

    context = devagent_db.get_interview_context(project_id)
    result = await submit_answer(
        project_id, payload.question, payload.answer, context
    )

    if result.get("done"):
        spec_dict = result.get("spec")
        if not isinstance(spec_dict, dict):
            raise HTTPException(502, "Spec DeepSeek invalide.")
        spec = lock_spec(spec_dict)
        devagent_db.save_spec(project_id, spec.model_dump_json())
        devagent_db.complete_interview_session(project_id)
        devagent_db.save_interview_context(project_id, context)
        devagent_db.update_project_status(project_id, "spec_locked")
        return {"done": True, "spec": spec.model_dump()}

    devagent_db.save_interview_context(project_id, context)
    return {"done": False, "next_question": result}


@app.post("/api/devagent/{project_id}/run")
async def devagent_run(project_id: int, background_tasks: BackgroundTasks):
    """Lance la boucle autonome en arriere-plan."""
    project = devagent_db.get_project(project_id)
    if not project:
        raise HTTPException(404, "Projet DevAgent introuvable.")
    if project.get("status") not in ("spec_locked", "paused", "failed"):
        raise HTTPException(
            400,
            f"Impossible de lancer (status={project.get('status')}). Spec verrouillee requise.",
        )
    if not project.get("spec_json"):
        raise HTTPException(400, "Spec absente — terminez l'interview d'abord.")

    devagent_db.update_project_status(project_id, "running")
    background_tasks.add_task(run_loop, project_id)
    return {"status": "started"}


@app.get("/api/devagent/{project_id}/status")
async def devagent_status(project_id: int):
    """Etat du projet et de la boucle autonome."""
    payload = devagent_db.get_project_status_payload(project_id)
    if not payload:
        raise HTTPException(404, "Projet DevAgent introuvable.")
    return payload


@app.post("/api/devagent/{project_id}/pause")
async def devagent_pause(project_id: int):
    """Met en pause la boucle autonome."""
    project = devagent_db.get_project(project_id)
    if not project:
        raise HTTPException(404, "Projet DevAgent introuvable.")
    devagent_db.update_project_status(project_id, "paused")
    return {"status": "paused"}


@app.get("/api/notifications/all")
async def api_notifications_all(limit: int = 50):
    """Toutes les notifications récentes (lues + non lues), pour historique UI."""
    try:
        return {"notifications": get_recent_notifications(limit=limit)}
    except Exception as e:
        logger.error("Erreur get_recent_notifications : %s", e)
        return {"notifications": []}


@app.post("/api/notifications/{notif_id}/read")
async def api_notifications_mark_read(notif_id: int):
    if not mark_notification_read(notif_id):
        raise HTTPException(404, "Notification introuvable")
    return {"ok": True}


@app.post("/api/notifications/read-all")
async def api_notifications_mark_all_read():
    count = mark_all_notifications_read()
    return {"ok": True, "marked": count}


@app.get("/api/briefing")
async def api_briefing(kind: str = "morning"):
    """Génère un briefing à la demande. `kind` = 'morning' ou 'evening'."""
    try:
        if kind == "evening":
            text = await productivity_agent.evening_summary()
        else:
            text = await productivity_agent.morning_briefing()
        return {"kind": kind, "content": text}
    except Exception as e:
        logger.exception("Erreur briefing")
        raise HTTPException(500, f"Briefing impossible : {type(e).__name__}: {e}")


@app.get("/api/emails")
async def api_emails(limit: int = 20):
    """Resumes emails recents (email_summaries)."""
    from database import get_recent_email_summaries
    summaries = get_recent_email_summaries(limit=limit)
    return {"emails": summaries, "count": len(summaries)}


@app.get("/api/mood")
async def api_mood():
    """Dernier mood enregistre."""
    from database import get_recent_moods
    moods = get_recent_moods(limit=1)
    if moods:
        return {"mood": moods[0].get("mood_score"), "energy": moods[0].get("energy_level"), "context": moods[0].get("context", "")}
    return {"mood": None, "energy": None}


@app.get("/api/tasks")
async def api_tasks_list(status: str | None = None):
    """Liste les tâches. Filtre optionnel : `all`, `todo`, `doing`, `done`.
    Sans filtre = todo + doing (pas les `done`).
    """
    if status and status not in ("all", "todo", "doing", "done"):
        raise HTTPException(400, "`status` invalide. Valeurs acceptées : all, todo, doing, done")
    return {"tasks": get_tasks(status=status)}


@app.post("/api/tasks")
async def api_tasks_create(payload: dict):
    """Crée une tâche.

    Body JSON : `{title, description?, priority?, due_date?, category?}`.
    """
    title = (payload.get("title") or "").strip()
    if not title:
        raise HTTPException(400, "`title` requis")

    try:
        task_id = create_task(
            title=title,
            description=payload.get("description"),
            priority=payload.get("priority", "medium"),
            due_date=payload.get("due_date"),
            category=payload.get("category"),
        )
    except Exception as e:
        logger.error(f"Erreur create_task : {e}")
        raise HTTPException(500, str(e))

    return {"task": get_task(task_id)}


@app.patch("/api/tasks/{task_id}")
async def api_tasks_update(task_id: int, payload: dict):
    """Met à jour le status d'une tâche (`todo` → `doing` → `done`)."""
    status = (payload.get("status") or "").strip().lower()
    if status not in ("todo", "doing", "done"):
        raise HTTPException(400, "`status` doit être todo / doing / done")

    if not update_task_status(task_id, status):
        raise HTTPException(404, "Tâche introuvable")

    return {"task": get_task(task_id)}


@app.delete("/api/tasks/{task_id}")
async def api_tasks_delete(task_id: int):
    """Supprime une tâche individuelle."""
    if not delete_task(task_id):
        raise HTTPException(404, "Tâche introuvable")
    return {"ok": True, "deleted_id": task_id}


@app.delete("/api/tasks")
async def api_tasks_delete_all():
    """Supprime TOUTES les tâches (tous statuts confondus)."""
    deleted_count = delete_all_tasks()
    logger.info(f"[tasks] {deleted_count} tâche(s) supprimée(s) — purge totale")
    return {"ok": True, "deleted_count": deleted_count}


# ── Phase 5 : Life profile / People / Journal / Patterns ────


@app.get("/api/life-profile")
async def api_life_profile_get():
    """Retourne le life profile groupé par catégorie + version brute avec ids (pour édition)."""
    return {
        "grouped": get_life_profile(),
        "entries": get_life_profile_entries(),
    }


@app.post("/api/life-profile")
async def api_life_profile_create(payload: dict):
    category = (payload.get("category") or "").strip()
    content = (payload.get("content") or "").strip()
    if not category or not content:
        raise HTTPException(400, "`category` et `content` requis")
    if category not in ("values", "goals", "fears", "patterns", "strengths"):
        raise HTTPException(400, "Catégorie invalide")

    entry_id = add_life_profile_entry(category, content)
    return {"id": entry_id, "category": category, "content": content}


@app.put("/api/life-profile/{entry_id}")
async def api_life_profile_update(entry_id: int, payload: dict):
    content = (payload.get("content") or "").strip()
    if not content:
        raise HTTPException(400, "`content` requis")
    if not update_life_profile_entry(entry_id, content):
        raise HTTPException(404, "Entrée introuvable")
    return {"id": entry_id, "content": content}


@app.delete("/api/life-profile/{entry_id}")
async def api_life_profile_delete(entry_id: int):
    if not delete_life_profile_entry(entry_id):
        raise HTTPException(404, "Entrée introuvable")
    return {"status": "deleted", "id": entry_id}


@app.get("/api/life-context")
async def api_life_context_list(active_only: bool = False):
    """Périodes de vie détectées (déménagement, rupture, nouveau travail...).

    ``active_only=true`` ne retourne que les périodes en cours ; par défaut
    l'historique complet (actives + closes) est renvoyé, le plus récent en premier.
    """
    from database import get_active_life_context, get_all_life_context

    return {
        "periods": get_active_life_context() if active_only else get_all_life_context(),
    }


@app.post("/api/life-context")
async def api_life_context_create(payload: dict):
    from database import add_life_context

    context_type = (payload.get("context_type") or "").strip()
    description = (payload.get("description") or "").strip()
    if not context_type or not description:
        raise HTTPException(400, "`context_type` et `description` requis")

    context_id = add_life_context(
        context_type, description,
        period_start=payload.get("period_start"),
        period_end=payload.get("period_end"),
        impact_on_mood=payload.get("impact_on_mood"),
        impact_on_productivity=payload.get("impact_on_productivity"),
    )
    return {"id": context_id, "context_type": context_type, "description": description}


@app.post("/api/life-context/{context_id}/close")
async def api_life_context_close(context_id: int):
    from database import close_life_context

    if not close_life_context(context_id):
        raise HTTPException(404, "Période introuvable")
    return {"status": "closed", "id": context_id}


@app.get("/api/people")
async def api_people_list():
    return {"people": get_people_sorted_by_recent()}


@app.get("/api/people/{name}")
async def api_people_detail(name: str):
    decoded = _decode_person_path(name)
    person = get_person(decoded) or get_person(name.strip())
    if not person:
        raise HTTPException(404, f"Personne inconnue : {decoded}")
    return person


@app.patch("/api/people/{name}")
async def api_people_patch(name: str, payload: dict[str, Any] = Body(default_factory=dict)):
    """Met à jour une fiche contact (nom, relation, notes…) — `WHERE LOWER(name) = LOWER(?)`."""
    decoded = _decode_person_path(name)
    try:
        updated = patch_person(decoded, payload)
        if not updated:
            updated = patch_person(name.strip(), payload)
        if not updated:
            raise HTTPException(404, f"Personne inconnue : {decoded}")
        return updated
    except ValueError as e:
        raise HTTPException(409, str(e)) from e


@app.get("/api/people/{name}/analytics")
async def api_person_analytics(name: str):
    """Métriques iMessage calculées en Python (pas de LLM) — `scripts/contact_analytics.py`."""
    from scripts.contact_analytics import contact_analytics

    decoded = _decode_person_path(name)
    person = get_person(decoded) or get_person(name.strip())
    if not person:
        return JSONResponse(status_code=404, content={"error": "Contact non trouvé"})

    handle = _resolve_handle_with_contacts(person.get("name") or decoded)
    if not handle:
        return {
            "error": "Aucun handle iMessage (profil, numéro ou Contacts)",
            "proximity_score": {"score": 0},
        }

    try:
        data = contact_analytics.compute_all(
            handle, person.get("name") or decoded, days=730
        )
        return data
    except Exception as e:
        logger.exception("[api/people/analytics]")
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.get("/api/people/{name}/timeline")
async def api_person_timeline_haiku(name: str):
    """Retourne la timeline du contact depuis le cache DB.

    Si aucun cache n'existe encore, genere via Haiku, stocke le resultat,
    puis le retourne. Utiliser POST /timeline/regenerate pour forcer un refresh.
    """
    from scripts.timeline_generator import generate_timeline
    from database import get_person_timeline_cache, update_person_timeline_cache

    decoded = _decode_person_path(name)
    key = decoded or name.strip()

    cached = await asyncio.to_thread(get_person_timeline_cache, key)
    if cached is not None:
        return {"events": cached["events"], "updated_at": cached["updated_at"], "from_cache": True}

    person = get_person(key)
    handle = _resolve_handle_with_contacts(person.get("name") if person else key)
    try:
        events = await generate_timeline(key, handle_override=handle)
        await asyncio.to_thread(update_person_timeline_cache, key, events)
        cached2 = await asyncio.to_thread(get_person_timeline_cache, key)
        return {
            "events": events,
            "updated_at": cached2["updated_at"] if cached2 else None,
            "from_cache": False,
        }
    except Exception as e:
        logger.exception("[api/people/timeline]")
        raise HTTPException(500, str(e)) from e


@app.post("/api/people/{name}/timeline/regenerate")
async def api_person_timeline_regenerate(name: str):
    """Force la regeneration de la timeline via Haiku et ecrase le cache DB."""
    from scripts.timeline_generator import generate_timeline
    from database import update_person_timeline_cache, get_person_timeline_cache

    decoded = _decode_person_path(name)
    key = decoded or name.strip()
    person = get_person(key)
    handle = _resolve_handle_with_contacts(person.get("name") if person else key)
    try:
        events = await generate_timeline(key, handle_override=handle)
        await asyncio.to_thread(update_person_timeline_cache, key, events)
        cached = await asyncio.to_thread(get_person_timeline_cache, key)
        return {
            "events": events,
            "updated_at": cached["updated_at"] if cached else None,
            "from_cache": False,
        }
    except Exception as e:
        logger.exception("[api/people/timeline/regenerate]")
        raise HTTPException(500, str(e)) from e


@app.post("/api/people/{name}/send")
async def api_person_send_imessage(name: str, body: dict[str, Any] = Body(default_factory=dict)):
    from integrations.imessage import send_imessage_to_address

    text = (body.get("text") or "").strip()
    if not text:
        return {"ok": False, "message": "Texte vide"}

    decoded = _decode_person_path(name)
    person = get_person(decoded) or get_person(name.strip())
    if not person:
        return {"ok": False, "message": f"Contact inconnu : {decoded}"}

    handle = _resolve_handle_with_contacts(person.get("name") or decoded)
    if not handle:
        return {"ok": False, "message": f"Pas de numéro ou email iMessage pour {person.get('name')}"}

    try:
        loop = asyncio.get_event_loop()
        ok, msg = await loop.run_in_executor(
            None, lambda: send_imessage_to_address(handle, text)
        )
        if ok:
            return {"ok": True, "message": f"Message envoyé à {person.get('name')}"}
        return {"ok": False, "message": msg}
    except Exception as e:
        logger.exception("[api/people/send]")
        return {"ok": False, "message": str(e)}


@app.post("/api/people/{name}/suggest-message")
async def api_person_suggest_message(name: str):
    from scripts.contact_analytics import contact_analytics

    decoded = _decode_person_path(name)
    person = get_person(decoded) or get_person(name.strip())
    if not person:
        raise HTTPException(404, f"Contact inconnu : {decoded}")

    display = person.get("name") or decoded
    handle = _resolve_handle_with_contacts(display) or display

    analytics: dict = {}
    try:
        analytics = contact_analytics.compute_all(handle, display, days=365)
    except Exception as e:
        logger.warning("[suggest-message] analytics : %s", e)

    pxd = analytics.get("proximity_score") or {}
    if isinstance(pxd, dict):
        details = pxd.get("details") or {}
        days_last = details.get("days_since_last", "?")
    else:
        days_last = "?"
    last_ex = analytics.get("last_exchanges") or []

    system = f"""Tu es JARVIS. Génère un message iMessage court et naturel que l'utilisateur pourrait envoyer à {display}.
Relation : {person.get("relationship") or "?"}
Dernier échange (jours depuis) : {days_last}
Derniers messages (aperçu) : {last_ex!s}
Le message doit être naturel, pas formel, comme l'utilisateur parle vraiment. 1-2 phrases max. Retourne UNIQUEMENT le message, rien d'autre."""

    try:
        result = await llm.chat(
            messages=[
                {
                    "role": "user",
                    "content": f"Suggère un message court et naturel à envoyer à {display}.",
                }
            ],
            model=config.DEEPSEEK_FAST_MODEL,
            system=system,
            max_tokens=100,
            temperature=0.8,
            use_cache=False,
        )
        out = strip_leading_emotion((result.get("content") or "").strip())
        return {"suggestion": out, "model": result.get("model"), "cost": result.get("cost", 0.0)}
    except Exception as e:
        logger.exception("[api/people/suggest-message]")
        raise HTTPException(500, str(e)) from e


@app.post("/api/people/{name}/remind")
async def api_person_remind(name: str, body: dict[str, Any] = Body(default_factory=dict)):
    when = (body.get("when") or "").strip() or "bientôt"
    decoded = _decode_person_path(name)
    person = get_person(decoded) or get_person(name.strip())
    if not person:
        raise HTTPException(404, f"Contact inconnu : {decoded}")
    label = person.get("name") or decoded
    try:
        task_id = create_task(
            title=f"Recontacter {label}",
            description=when,
            priority="medium",
            category="relation",
        )
        return {"ok": True, "task_id": task_id}
    except Exception as e:
        logger.exception("[api/people/remind]")
        raise HTTPException(500, str(e)) from e


@app.post("/api/people/{name}/ask")
async def api_people_ask(name: str, payload: dict[str, Any] = Body(default_factory=dict)):
    """Pose une question contextualisée sur un contact (Sonnet + chat.db + profil)."""
    path_decoded = _decode_person_path(name)
    try:
        question = (payload.get("question") or "").strip()
        logger.info("[contact_chat] path=%r decoded=%r question_len=%d", name, path_decoded, len(question))
        if not question:
            raise HTTPException(400, "`question` requis")

        person = None
        try:
            person = get_person(path_decoded)
            if not person and path_decoded != name.strip():
                person = get_person(name.strip())
            logger.info(
                "[contact_chat] person trouvée=%s id=%s",
                person is not None,
                person.get("id") if person else None,
            )
        except Exception:
            logger.exception("[contact_chat] get_person")
            raise

        if not person:
            return {
                "response": f"Je n'ai pas de fiche pour « {path_decoded} ».",
                "model": None,
                "cost": 0.0,
            }

        pid = person["id"]
        profile = None
        try:
            profile = get_relationship_profile(pid)
            logger.info("[contact_chat] profil relationnel présent=%s", profile is not None)
        except Exception as e:
            logger.warning("[contact_chat] profil relationnel ignoré : %s", e)

        timeline: list = []
        try:
            timeline = get_relationship_timeline(pid, limit=28)
            logger.info("[contact_chat] timeline événements=%d", len(timeline))
        except Exception as e:
            logger.warning("[contact_chat] timeline ignorée : %s", e)

        msgs: list = []
        try:
            from integrations.imessage_reader import imessage_reader

            if imessage_reader and imessage_reader.is_available():
                handle = _resolve_handle_with_contacts(person.get("name") or path_decoded)
                if handle:
                    msgs = imessage_reader.get_recent_conversation(handle, limit=30)
                    logger.info("[contact_chat] iMessage via handle len=%d", len(msgs))
                else:
                    nm = person.get("name") or path_decoded
                    msgs = imessage_reader.get_conversation_with(nm, limit=30)
                    logger.info("[contact_chat] iMessage via nom len=%d", len(msgs))
            else:
                logger.info("[contact_chat] iMessage reader indisponible")
        except Exception as e:
            logger.warning("[contact_chat] erreur iMessage : %s", e)

        rp = profile or {}
        events_block = "(aucun événement)"
        try:
            events_block = (
                _format_people_events(person.get("events"))
                + "\n\n— Timeline relationnelle —\n"
                + _format_contact_timeline(timeline)
            )
        except Exception as e:
            logger.warning("[contact_chat] format événements : %s", e)

        snippets = "(aucun extrait iMessage — handle ou chat.db)"
        try:
            snippets = _format_imessage_snippets(msgs, person.get("name") or path_decoded)
        except Exception as e:
            logger.warning("[contact_chat] format extraits : %s", e)

        tpl = ""
        try:
            tpl = (BASE_DIR / "prompts" / "contact_chat.txt").read_text(encoding="utf-8")
            logger.info("[contact_chat] prompts/contact_chat.txt chargé (%d car.)", len(tpl))
        except OSError as e:
            logger.warning("[contact_chat] contact_chat.txt absent, fallback persona : %s", e)
            tpl = _load_persona_block()

        try:
            system = (
                tpl.replace("{{persona}}", _load_persona_block())
                .replace("{{contact_name}}", person.get("name") or path_decoded)
                .replace("{{relationship}}", str(person.get("relationship") or "—"))
                .replace("{{personality_notes}}", str(person.get("personality_notes") or "—"))
                .replace("{{dynamics}}", str(person.get("dynamics") or "—"))
                .replace("{{patterns}}", str(person.get("patterns") or "—"))
                .replace("{{communication_style}}", str(rp.get("communication_style") or "—"))
                .replace("{{sentiment}}", str(rp.get("sentiment") or "—"))
                .replace("{{trust_level}}", str(rp.get("trust_level") or "—"))
                .replace("{{events}}", events_block)
                .replace("{{recent_messages}}", snippets)
            )
            logger.info("[contact_chat] system prompt construit (%d car.)", len(system))
        except Exception:
            logger.exception("[contact_chat] construction du prompt système")
            raise

        # Construire un message enrichi avec tout le contexte de la personne
        profile_text = ""
        if profile:
            profile_text = (
                f"\n[PROFIL DE {(person.get('name') or path_decoded).upper()}]\n"
                f"Relation : {profile.get('relationship') or person.get('relationship') or '?'}\n"
                f"Sentiment : {profile.get('sentiment') or '?'}\n"
                f"Style communication : {profile.get('communication_style') or '?'}\n"
                f"Confiance : {profile.get('trust_level') or '?'}\n"
            )

        enriched_question = (
            f"[QUESTION SUR {(person.get('name') or path_decoded).upper()}]"
            f"{profile_text}"
            f"\nÉvénements récents :\n{events_block}"
            f"\n\nDerniers échanges iMessage :\n{snippets}"
            f"\n\nQuestion : {question}"
        )

        try:
            # Créer une conversation temporaire pour cette question contact
            conv_id = create_conversation(agent="contact_chat")
            save_message(conv_id, "user", question)
            update_conversation_activity(conv_id)

            # Passer par le pipeline unifié — bénéficie de TOUT le contexte
            result = await _process_message_internal(enriched_question, conv_id)
            logger.info("[contact_chat] pipeline unifié ok model=%s", result.get("model"))
            return {
                "response": result.get("text", ""),
                "model": result.get("model"),
                "cost": result.get("cost", 0.0),
            }
        except Exception as e:
            logger.exception("[contact_chat] pipeline unifié : %s — fallback LLM direct", e)
            # Fallback : appel LLM direct avec le prompt spécialisé contact_chat
            res = await llm.chat(
                messages=[{"role": "user", "content": question}],
                model=config.DEEPSEEK_MAIN_MODEL,
                system=system,
                max_tokens=1800,
                temperature=0.45,
                use_cache=False,
            )
            text = strip_leading_emotion((res.get("content") or "").strip())
            logger.info("[contact_chat] fallback LLM ok model=%s", res.get("model"))
            return {
                "response": text,
                "model": res.get("model"),
                "cost": res.get("cost", 0.0),
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[contact_chat] ERREUR : %s", e)
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.get("/api/people/{name}/description")
async def api_person_description(name: str):
    """Description IA courte (cache people.ai_description ou génération Haiku)."""
    decoded = _decode_person_path(name)
    person = get_person(decoded) or get_person(name.strip())
    if not person:
        raise HTTPException(404, f"Personne inconnue : {decoded}")
    cached = person.get("ai_description")
    if cached and str(cached).strip():
        return {"description": str(cached).strip()}
    pid = person["id"]
    profile = get_relationship_profile(pid)
    try:
        text, meta = await _generate_person_ai_description(person, profile)
        if text:
            set_person_ai_description(pid, text)
        return {
            "description": text,
            "model": meta.get("model"),
            "cost": meta.get("cost", 0.0),
        }
    except Exception as e:
        logger.exception("[api/people/description]")
        raise HTTPException(500, str(e)) from e


@app.post("/api/people/{name}/description/refresh")
async def api_person_description_refresh(name: str):
    decoded = _decode_person_path(name)
    person = get_person(decoded) or get_person(name.strip())
    if not person:
        raise HTTPException(404, f"Personne inconnue : {decoded}")
    clear_person_ai_description(person["id"])
    lookup_name = person["name"]
    person = get_person(lookup_name)
    profile = get_relationship_profile(person["id"])
    try:
        text, meta = await _generate_person_ai_description(person or {}, profile)
        if text:
            set_person_ai_description(person["id"], text)
        return {
            "description": text,
            "model": meta.get("model"),
            "cost": meta.get("cost", 0.0),
        }
    except Exception as e:
        logger.exception("[api/people/description/refresh]")
        raise HTTPException(500, str(e)) from e


@app.post("/api/people")
async def api_people_upsert(payload: dict):
    name = (payload.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "`name` requis")

    fields = {}
    for k in ("relationship", "personality_notes", "dynamics", "patterns"):
        v = payload.get(k)
        if v is not None:
            fields[k] = v

    person_id = upsert_person(name, **fields)
    return get_person(name) or {"id": person_id, "name": name}


@app.get("/api/journal")
async def api_journal_get():
    """Moods récents + épisodes journal récents."""
    return {
        "moods": get_recent_moods(limit=30),
        "episodes": get_recent_episodes(agent="journal", limit=30),
    }


@app.post("/api/journal")
async def api_journal_post(payload: dict):
    """Envoie une entrée de journal via le pipeline unifié. Retourne réponse + extraction."""
    content = (payload.get("content") or "").strip()
    if not content:
        raise HTTPException(400, "`content` requis")

    try:
        # Créer une conversation temporaire pour le journal
        conv_id = create_conversation(agent="journal")
        save_message(conv_id, "user", content)
        update_conversation_activity(conv_id)

        # Passer par le pipeline unifié — l'orchestrateur route vers JOURNAL automatiquement
        result = await _process_message_internal(content, conv_id)

        # Extraction JSON des insights via le journal_agent (traitement des données structurées)
        extracted = None
        try:
            extracted = journal_agent._process_journal_data(result.get("text", ""))
            _schedule_llm_log(
                agent="journal",
                action_type="journal_extract",
                payload={"conversation_id": conv_id, "has_extracted": bool(extracted)},
                status="success",
            )
        except Exception:
            _schedule_llm_log(
                agent="journal",
                action_type="journal_extract",
                payload={"conversation_id": conv_id},
                status="error",
            )

        return {
            "response": result.get("text"),
            "extracted": extracted,
            "model": result.get("model"),
            "cost": result.get("cost", 0.0),
        }
    except Exception as e:
        logger.exception("Erreur api_journal_post")
        raise HTTPException(500, f"Erreur journal : {type(e).__name__}: {e}")


@app.get("/api/patterns")
async def api_patterns_get():
    return {"patterns": get_active_patterns()}


# ── Calendar ──────────────────────────────────────────────────


@app.get("/api/calendar")
async def api_calendar_get(start: str = "", end: str = ""):
    """Récupère les événements Calendar.app entre deux dates ISO."""
    if not calendar_client or not calendar_client.is_available():
        raise HTTPException(503, "Calendar.app indisponible")
    if not start or not end:
        raise HTTPException(400, "Paramètres start et end requis (ISO 8601)")
    events = await calendar_client.get_events(start, end)
    return {"events": events, "count": len(events)}


@app.post("/api/calendar")
async def api_calendar_create(body: dict = Body(default_factory=dict)):
    """Crée un événement dans Calendar.app."""
    if not calendar_client or not calendar_client.is_available():
        raise HTTPException(503, "Calendar.app indisponible")
    title = (body.get("title") or body.get("summary") or "").strip()
    start = (body.get("start") or "").strip()
    end = (body.get("end") or "").strip()
    if not title or not start:
        raise HTTPException(400, "title/summary et start sont requis")
    result = await calendar_client.create_event(
        summary=title,
        start_date=start,
        end_date=end,
        calendar_name=body.get("calendar"),
        location=body.get("location", ""),
        notes=body.get("notes", ""),
    )
    if not result.get("ok"):
        raise HTTPException(500, result.get("message", "Erreur création événement"))
    return result


@app.post("/api/calendar/test")
async def api_calendar_test():
    """Crée un événement de test pour vérifier le pipeline Calendar."""
    if not calendar_client:
        return {"ok": False, "error": "calendar_client non initialisé"}
    if not calendar_client.is_available():
        return {"ok": False, "error": "Calendar non disponible"}

    start = datetime.now() + timedelta(hours=1)
    end = start + timedelta(minutes=30)
    return await calendar_client.create_event(
        summary="TEST JARVIS — à supprimer",
        start_date=start.strftime("%Y-%m-%d %H:%M"),
        end_date=end.strftime("%Y-%m-%d %H:%M"),
    )


# ── Conversations enrichies ──────────────────────────────────


@app.get("/api/conversations/search")
async def api_conversations_search(q: str = "", limit: int = 20):
    """Recherche dans titres et messages de toutes les conversations."""
    if not q.strip():
        return {"results": [], "count": 0}
    results = search_conversations(q.strip(), limit=limit)
    return {"results": results, "count": len(results)}


@app.get("/api/conversations")
async def api_conversations_list(archived: bool = False, limit: int = 50):
    """Liste des conversations triées par dernière activité."""
    convs = get_conversations(limit=limit, archived=archived)
    return {"conversations": convs}


@app.get("/api/conversations/{conv_id}")
async def api_conversation_get(conv_id: int):
    """Détail d'une conversation (messages + documents)."""
    conv = get_conversation_detail(conv_id)
    if not conv:
        return JSONResponse(status_code=404, content={"error": "Conversation non trouvée"})
    return conv


@app.patch("/api/conversations/{conv_id}")
async def api_conversation_update(conv_id: int, body: dict = Body(default_factory=dict)):
    """Met à jour les métadonnées d'une conversation (titre, pinned, archived…)."""
    allowed = {"title", "pinned", "archived", "tags"}
    fields = {k: v for k, v in body.items() if k in allowed}
    if not fields:
        raise HTTPException(400, "Aucun champ modifiable fourni")
    update_conversation(conv_id, **fields)
    return {"ok": True}


@app.delete("/api/conversations/{conv_id}")
async def api_conversation_delete(conv_id: int):
    """Supprime une conversation et tous ses messages."""
    delete_conversation(conv_id)
    return {"ok": True}


@app.post("/api/conversations/{conv_id}/archive")
async def api_conversation_archive(conv_id: int):
    """Archive une conversation."""
    update_conversation(conv_id, archived=True)
    return {"ok": True}


@app.post("/api/conversations/{conv_id}/pin")
async def api_conversation_pin(conv_id: int):
    """Bascule le statut épinglé d'une conversation."""
    conv = get_conversation_detail(conv_id)
    if not conv:
        raise HTTPException(404, "Conversation non trouvée")
    pinned = not bool(conv.get("pinned", False))
    update_conversation(conv_id, pinned=pinned)
    return {"ok": True, "pinned": pinned}


@app.post("/api/conversations/{conv_id}/upload")
async def api_conversation_upload(conv_id: int, file: UploadFile):
    """Upload et analyse un document dans le contexte d'une conversation."""
    import time as _time

    conv = get_conversation_detail(conv_id)
    if not conv:
        raise HTTPException(404, "Conversation non trouvée")

    upload_dir = Path(config.UPLOAD_DIR) / "conversations" / str(conv_id)
    upload_dir.mkdir(parents=True, exist_ok=True)

    filename = f"{int(_time.time())}_{file.filename}"
    filepath = upload_dir / filename
    content_bytes = await file.read()
    filepath.write_bytes(content_bytes)

    ext = Path(file.filename or "").suffix.lower()
    extracted = ""

    if ext == ".pdf":
        try:
            doc = fitz.open(str(filepath))
            extracted = "\n".join(page.get_text() for page in doc)
            doc.close()
        except Exception as e:
            logger.warning("[conv upload] PDF extraction : %s", e)
    elif ext in (".txt", ".md", ".csv", ".json", ".py", ".js", ".ts", ".html", ".css"):
        try:
            extracted = filepath.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            logger.warning("[conv upload] text read : %s", e)

    summary = None
    if len(extracted) > 500:
        try:
            res = await llm.chat(
                messages=[{"role": "user", "content": extracted[:5000]}],
                model=config.DEEPSEEK_FAST_MODEL,
                system="Résume ce document en 2-3 phrases. Sois factuel.",
                max_tokens=150,
                use_cache=False,
            )
            summary = (res.get("content") or "").strip()
        except Exception as e:
            logger.warning("[conv upload] résumé Haiku : %s", e)

    doc_id = save_conversation_document(
        conv_id,
        filename,
        file.filename or filename,
        str(filepath),
        ext.lstrip(".") or "bin",
        len(content_bytes),
        extracted or None,
        summary,
    )

    if ext == ".pdf" and extracted:
        try:
            save_school_document(
                title=Path(file.filename or filename).stem,
                content=extracted,
                doc_type="cours",
                file_path=str(filepath),
            )
        except Exception as e:
            logger.debug("[conv upload] school_doc : %s", e)

    logger.info("[conv upload] doc #%d dans conv #%d (%s, %d bytes)", doc_id, conv_id, file.filename, len(content_bytes))
    return {
        "ok": True,
        "doc_id": doc_id,
        "filename": file.filename,
        "file_type": ext.lstrip("."),
        "size": len(content_bytes),
        "content_length": len(extracted),
        "summary": summary,
    }


# ── Mémoire profonde : analyse relationnelle ────────────────


@app.post("/api/analyze-contact")
async def api_analyze_contact(payload: dict):
    """Lance l'analyse Haiku d'un contact iMessage. Body : {"name": "Bertille"}."""
    name = (payload.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "`name` requis")

    try:
        from scripts.relationship_analyzer import analyzer
        result = await analyzer.analyze_single_contact(name)
        if result is None:
            raise HTTPException(404, f"Aucun message trouvé pour '{name}'")
        return {"status": "ok", "profile": result}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Erreur analyze-contact")
        raise HTTPException(500, f"Erreur analyse : {e}")


@app.get("/api/relationship/{name}")
async def api_relationship_detail(name: str):
    """Profil relationnel complet d'un contact : people + relationship_profile + timeline."""
    decoded = _decode_person_path(name)
    person = get_person(decoded) or get_person(name.strip())
    if not person:
        raise HTTPException(404, f"Personne inconnue : {decoded}")

    profile = get_relationship_profile(person["id"]) if person.get("id") else None
    timeline = get_relationship_timeline(person["id"], limit=30) if person.get("id") else []

    return {
        "person": person,
        "relationship_profile": profile,
        "timeline": timeline,
    }


@app.get("/api/relationship-graph")
async def api_relationship_graph():
    """Graphe vivant des relations : utilisateur + contacts + liens multi-personnes détectés."""
    from scripts.relationship_graph import build_relationship_graph

    return build_relationship_graph()


@app.get("/api/time-machine/{date}")
async def api_time_machine(date: str):
    """Reconstruction chronologique d'une journée (messages, tâches, lieux, humeur, écran, journal)."""
    from scripts.time_machine import build_day_timeline

    try:
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(400, "Format de date invalide, attendu YYYY-MM-DD")

    return build_day_timeline(date)


# ── Localisation (GPS, lieux nommés, visites) ───────────────


@app.post("/api/location")
async def api_location_receive(body: dict[str, Any]):
    """Réception d'un point GPS (app native, raccourci iOS, etc.)."""
    try:
        lat = float(body["latitude"])
        lng = float(body["longitude"])
    except (KeyError, TypeError, ValueError) as e:
        raise HTTPException(400, f"latitude/longitude invalides : {e}") from e
    from integrations.location import location_manager

    pt = _parse_optional_point_time(body)
    return await location_manager.process_location(
        lat,
        lng,
        altitude=body.get("altitude"),
        accuracy=body.get("accuracy"),
        speed=body.get("speed"),
        heading=body.get("heading"),
        source=str(body.get("source") or "app"),
        point_time=pt,
        created_at=body.get("created_at") if isinstance(body.get("created_at"), str) else None,
    )


@app.post("/api/location/batch")
async def api_location_batch(body: dict[str, Any]):
    """Points groupés (ex. rattrapage hors ligne). Chaque point peut avoir timestamp."""
    points = body.get("points")
    if not isinstance(points, list):
        raise HTTPException(400, "Body attendu : {\"points\": [...]}")
    from integrations.location import location_manager

    results: list[dict[str, Any]] = []
    indexed: list[tuple[int, dict[str, Any]]] = []
    for i, p in enumerate(points):
        if isinstance(p, dict):
            indexed.append((i, p))

    def sort_key(item: tuple[int, dict[str, Any]]) -> tuple[float, int]:
        idx, p = item
        t = _parse_optional_point_time(p)
        if t is not None:
            return (t.timestamp(), idx)
        return (float(idx), idx)

    for _, p in sorted(indexed, key=sort_key):
        try:
            lat = float(p["latitude"])
            lng = float(p["longitude"])
        except (KeyError, TypeError, ValueError):
            continue
        pt = _parse_optional_point_time(p)
        r = await location_manager.process_location(
            lat,
            lng,
            altitude=p.get("altitude"),
            accuracy=p.get("accuracy"),
            speed=p.get("speed"),
            heading=p.get("heading"),
            source=str(p.get("source") or "app"),
            point_time=pt,
            created_at=p.get("created_at") if isinstance(p.get("created_at"), str) else None,
        )
        results.append(r)
    return {"processed": len(results), "results": results}


@app.get("/api/places")
async def api_places_list():
    from database.location_helpers import get_all_places

    return {"places": get_all_places()}


@app.post("/api/places")
async def api_places_create(body: dict[str, Any]):
    from database.location_helpers import create_place

    try:
        pid = create_place(
            name=str(body["name"]),
            category=str(body.get("category") or "other"),
            lat=float(body["latitude"]),
            lng=float(body["longitude"]),
            radius=float(body["radius"]) if body.get("radius") is not None else None,
            address=body.get("address"),
            notes=body.get("notes"),
        )
    except (KeyError, TypeError, ValueError) as e:
        raise HTTPException(400, str(e)) from e
    return {"id": pid, **body}


@app.put("/api/places/{place_id}")
async def api_places_update(place_id: int, body: dict[str, Any]):
    from database.location_helpers import update_place

    payload = dict(body)
    if "radius" in payload and "radius_meters" not in payload:
        payload["radius_meters"] = payload.pop("radius")
    update_place(place_id, **payload)
    return {"ok": True}


@app.delete("/api/places/{place_id}")
async def api_places_delete(place_id: int):
    from database.location_helpers import delete_place

    ok = delete_place(place_id)
    if not ok:
        raise HTTPException(404, "Lieu introuvable")
    return {"ok": True}


@app.get("/api/places/{place_id}/stats")
async def api_place_stats(place_id: int):
    from database.location_helpers import get_place, get_place_visit_stats

    if not get_place(place_id):
        raise HTTPException(404, "Lieu introuvable")
    return get_place_visit_stats(place_id)


@app.get("/api/location/status")
async def api_location_status():
    from integrations.location import location_manager

    return await location_manager.get_status()


@app.get("/api/location/history")
async def api_location_history(hours: int = 24):
    from database.location_helpers import get_location_history

    return {"points": get_location_history(hours=max(1, min(hours, 168)))}


@app.get("/api/visits")
async def api_visits_list(days: int = 7):
    from database.location_helpers import get_recent_visits

    return {"visits": get_recent_visits(max(1, min(days, 90)))}


@app.get("/api/visits/today")
async def api_visits_today():
    from database.location_helpers import get_today_visits

    return {"visits": get_today_visits()}


@app.get("/api/trips")
async def api_trips_list(days: int = 7):
    from database.location_helpers import get_recent_trips

    return {"trips": get_recent_trips(max(1, min(days, 30)))}


@app.get("/api/location/patterns")
async def api_location_patterns():
    from database.location_helpers import get_active_location_patterns

    return {"patterns": get_active_location_patterns()}


@app.post("/api/location/name-current")
async def api_location_name_current(body: dict[str, Any]):
    from database.location_helpers import create_place, get_current_location

    cur = get_current_location()
    if not cur:
        return {"ok": False, "message": "Pas de position GPS récente"}
    try:
        pid = create_place(
            name=str(body["name"]),
            category=str(body.get("category") or "other"),
            lat=float(cur["latitude"]),
            lng=float(cur["longitude"]),
        )
    except (KeyError, TypeError, ValueError) as e:
        raise HTTPException(400, str(e)) from e
    return {"ok": True, "place_id": pid}


# ── Recherche, export, contacts macOS (iMessage DB) ──────────


@app.get("/api/contacts")
async def api_mac_contacts():
    """Handles iMessage (chat.db) + résolution noms via Contacts.app si disponible."""
    try:
        from integrations.contacts import contacts_reader
        from integrations.imessage_reader import IMessageReader

        if contacts_reader.is_available():
            contacts_reader.build_cache()

        r = IMessageReader()
        raw = r.get_all_contacts()
        contacts = []
        for c in raw:
            handle = c.get("handle")
            if contacts_reader.is_available():
                disp = contacts_reader.resolve_handle(handle or "")
            else:
                disp = handle
            contacts.append({
                "handle": handle,
                "name": disp,
                "msg_count": c.get("msg_count"),
                "last_date": c.get("last_date"),
            })
        return {"contacts": contacts}
    except Exception as e:
        logger.warning("[api/contacts] %s", e)
        return {"contacts": [], "error": str(e)}


@app.post("/api/contacts/sync")
async def api_contacts_sync():
    """Re-synchronise les entrées `people` dont le nom est encore un numéro / email."""
    try:
        from scripts.sync_contacts import sync_people_names

        result = await sync_people_names()
        return result
    except Exception as e:
        logger.error("[api/contacts/sync] %s", e)
        raise HTTPException(500, str(e)) from e


@app.get("/api/search")
async def api_search(q: str = ""):
    """Recherche légère multi-sources (pas de LLM)."""
    needle = (q or "").strip().lower()
    if len(needle) < 2:
        return {"query": q, "results": []}

    results: list[dict] = []

    try:
        for p in get_all_people():
            name = (p.get("name") or "").strip()
            if needle in name.lower():
                results.append({
                    "type": "person",
                    "id": p.get("id"),
                    "title": name,
                    "subtitle": p.get("relationship") or "",
                    "meta": "people",
                })
    except Exception as e:
        logger.warning("search people : %s", e)

    try:
        for ep in get_recent_episodes(limit=80):
            blob = f"{ep.get('summary', '')} {ep.get('content', '')}".lower()
            if needle in blob:
                results.append({
                    "type": "episode",
                    "id": ep.get("id"),
                    "title": (ep.get("summary") or ep.get("content") or "")[:120],
                    "subtitle": str(ep.get("created_at") or ""),
                    "meta": "episode",
                })
    except Exception as e:
        logger.warning("search episodes : %s", e)

    try:
        docs = get_school_documents(limit=50)
        for d in docs:
            t = (d.get("title") or "").lower()
            if needle in t or needle in (d.get("content") or "").lower()[:2000]:
                results.append({
                    "type": "document",
                    "id": d.get("id"),
                    "title": d.get("title") or "(sans titre)",
                    "subtitle": d.get("doc_type") or "",
                    "meta": "school_document",
                })
    except Exception as e:
        logger.warning("search docs : %s", e)

    return {"query": q, "results": results[:80]}


@app.get("/api/export")
async def api_export_dump(format: str = "json"):
    """Dump JSON agrégé pour sauvegarde locale (pas de secrets tiers)."""
    if format.lower() != "json":
        raise HTTPException(400, "Seul format=json est supporté")

    try:
        from database.location_helpers import get_all_places

        payload = {
            "exported_at": datetime.now().isoformat(timespec="seconds"),
            "user": config.USER_NAME,
            "life_profile": get_life_profile(),
            "life_profile_entries": get_life_profile_entries(),
            "people": get_all_people(),
            "tasks": get_tasks(),
            "patterns": get_active_patterns(),
            "journal_moods": get_recent_moods(90),
            "recent_episodes": get_recent_episodes(limit=100),
            "school_documents_meta": get_school_documents(limit=200),
            "places": get_all_places(),
        }
        return payload
    except Exception as e:
        logger.exception("api/export : %s", e)
        raise HTTPException(500, str(e)) from e


# ── Daemon JARVIS — devices, écran, app usage ──────────────

# File d'attente TTS par device (clé = device_id). Le serveur dépose les MP3
# encodés base64 que l'agent distant viendra chercher via /api/devices/{id}/tts.
_device_tts_queues: dict[str, asyncio.Queue] = {}


def _get_device_tts_queue(device_id: str) -> asyncio.Queue:
    if device_id not in _device_tts_queues:
        _device_tts_queues[device_id] = asyncio.Queue(maxsize=10)
    return _device_tts_queues[device_id]


@app.post("/api/devices/register")
async def api_register_device(body: dict):
    """Enregistre une machine (ou met à jour les infos). Retourne un token unique."""
    device_id = (body.get("device_id") or "").strip()
    device_name = (body.get("device_name") or "").strip() or device_id
    if not device_id:
        raise HTTPException(400, "`device_id` requis")
    token = register_device(
        device_id=device_id,
        device_name=device_name,
        device_type=body.get("device_type", "desktop"),
        ip_tailscale=body.get("ip_tailscale"),
    )
    return {"ok": True, "token": token, "device_id": device_id}


@app.post("/api/devices/{device_id}/heartbeat")
async def api_device_heartbeat(device_id: str):
    update_device_heartbeat(device_id)
    return {"ok": True}


@app.post("/api/devices/{device_id}/screen")
async def api_device_screen(device_id: str, body: dict):
    """Reçoit un screenshot d'un agent distant et l'analyse localement (Ollama).

    Si l'analyse retourne un `notable`, on demande à Claude une notification
    courte qui est ensuite renvoyée au device via la file TTS dédiée.
    """
    image_b64 = body.get("image_b64")
    declared_app = body.get("app", "unknown")
    change_pct = float(body.get("change_pct") or 0.0)

    if not image_b64:
        return {"ok": False, "message": "Pas d'image"}

    try:
        import base64 as _b64
        from io import BytesIO

        from PIL import Image as _Image

        img_bytes = _b64.b64decode(image_b64)
        img = _Image.open(BytesIO(img_bytes))
        img.load()
    except Exception as e:
        logger.warning("[device_screen] décodage image : %s", e)
        return {"ok": False, "message": "Image invalide"}

    # Analyse Ollama vision locale (sur le Mac Mini)
    analysis: dict | None = None
    try:
        from scripts.screen_watcher import screen_watcher as _sw

        analysis = await _sw._analyze_with_ollama(img)
    except Exception as e:
        logger.warning("[device_screen] analyse Ollama : %s", e)

    if analysis:
        try:
            save_screen_activity(
                device=device_id,
                app=analysis.get("app") or declared_app,
                activity=analysis.get("activity", ""),
                mood=analysis.get("mood"),
                notable=analysis.get("notable"),
                change_pct=change_pct,
            )
        except Exception as e:
            logger.warning("[device_screen] save_screen_activity : %s", e)

        notable = analysis.get("notable")
        if notable:
            try:
                temp_conv = create_conversation(agent="daemon_screen_remote")
                prompt = (
                    f"[NOTIFICATION ÉCRAN DISTANT] L'utilisateur est sur "
                    f"{analysis.get('app', '?')} ({analysis.get('activity', '?')}) "
                    f"sur {device_id}. Observation : {notable}. "
                    "Propose une aide courte (1 phrase). Si pas pertinent, réponds NULL."
                )
                result = await _process_message_internal(prompt, temp_conv, voice_mode=True)
                text = (result or {}).get("text") or ""
                if text and "NULL" not in text.upper():
                    try:
                        from audio.tts import tts as _tts

                        if _tts:
                            audio = await _tts.synthesize(text, emotion="neutral")
                            if audio:
                                import base64 as _b64x

                                queue = _get_device_tts_queue(device_id)
                                try:
                                    queue.put_nowait(_b64x.b64encode(audio).decode())
                                except asyncio.QueueFull:
                                    logger.debug("[device_screen] TTS queue pleine pour %s", device_id)
                    except Exception as e:
                        logger.warning("[device_screen] TTS synth : %s", e)
            except Exception as e:
                logger.warning("[device_screen] formulation Claude : %s", e)
    else:
        # Stocke quand même l'activité brute si l'analyse a échoué
        try:
            save_screen_activity(
                device=device_id,
                app=declared_app,
                activity="remote_no_analysis",
                change_pct=change_pct,
            )
        except Exception:
            pass

    return {"ok": True, "analysis": analysis}


@app.get("/api/devices/{device_id}/tts")
async def api_device_tts(device_id: str):
    """Endpoint polling — l'agent distant récupère un MP3 base64 à jouer."""
    queue = _get_device_tts_queue(device_id)
    try:
        audio_b64 = queue.get_nowait()
        return {"audio_b64": audio_b64}
    except asyncio.QueueEmpty:
        return {"audio_b64": None}


@app.post("/api/devices/{device_id}/activate")
async def api_activate_device(device_id: str):
    set_active_device(device_id)
    return {"ok": True, "active": device_id}


@app.get("/api/devices")
async def api_list_devices():
    return {"devices": get_all_devices(), "active": get_active_device()}


@app.get("/api/screen-activity")
async def api_screen_activity(hours: int = 24, device: str | None = None):
    """Liste les analyses d'écran sur N heures."""
    return {"activity": get_screen_activity(hours=hours, device=device)}


@app.get("/api/screen-activity/current")
async def api_screen_activity_current(device: str | None = None):
    """Dernier contexte écran connu (≤ 5 minutes)."""
    return {"context": get_current_screen_context(device=device)}


@app.get("/api/app-usage")
async def api_app_usage(days: int = 7, device: str | None = None):
    """Temps cumulé par application sur N jours (style Screen Time)."""
    if days <= 1:
        return {"usage": get_app_usage(device=device), "days": 1}
    return {"usage": get_app_usage_range(days=days, device=device), "days": int(days)}


# ── Service Control ──────────────────────────────────────────

# Services internes contrôlables via /api/control/
INTERNAL_SERVICES = [
    "audio_daemon",
    "email_watcher",
    "jarvis_daemon",
    "screen_watcher",
    "scheduler",
    "relationship_analyzer",
]

# Tasks asyncio lancées dynamiquement — stockées pour pouvoir les annuler
_service_tasks: dict[str, asyncio.Task] = {}


def _get_all_services_status() -> list[dict[str, object]]:
    """Retourne l'état de chaque service (interne + externe)."""
    services: list[dict[str, object]] = []

    # ── Audio Daemon ──
    try:
        from scripts.audio_daemon import audio_daemon
        services.append({
            "id": "audio_daemon",
            "name": "Audio Daemon",
            "description": "Micro natif + wake word + TTS",
            "category": "audio",
            "running": audio_daemon.enabled,
            "state": audio_daemon.state,
            "can_control": True,
        })
    except Exception:
        services.append({"id": "audio_daemon", "name": "Audio Daemon", "running": False, "can_control": True, "category": "audio", "description": "Micro natif + wake word + TTS"})

    # ── Email Watcher ──
    try:
        from scripts.email_watcher import email_watcher as _ew
        running = getattr(_ew, '_running', False) or getattr(_ew, 'running', False)
        services.append({
            "id": "email_watcher",
            "name": "Email Watcher",
            "description": "Surveillance Mail.app (analyse Haiku)",
            "category": "integrations",
            "running": running,
            "can_control": True,
        })
    except Exception:
        services.append({"id": "email_watcher", "name": "Email Watcher", "running": False, "can_control": True, "category": "integrations", "description": "Surveillance Mail.app"})

    # ── JARVIS Daemon ──
    try:
        from scripts.jarvis_daemon import daemon as _jd
        running = getattr(_jd, '_running', False) or getattr(_jd, 'running', False)
        services.append({
            "id": "jarvis_daemon",
            "name": "JARVIS Daemon",
            "description": "Sentinelle permanente (triage notifications)",
            "category": "core",
            "running": running,
            "can_control": True,
        })
    except Exception:
        services.append({"id": "jarvis_daemon", "name": "JARVIS Daemon", "running": False, "can_control": True, "category": "core", "description": "Sentinelle permanente"})

    # ── Screen Watcher ──
    try:
        from scripts.screen_watcher import screen_watcher as _sw
        running = getattr(_sw, '_running', False) or getattr(_sw, 'running', False)
        services.append({
            "id": "screen_watcher",
            "name": "Screen Watcher",
            "description": "Analyse ecran Ollama vision",
            "category": "monitoring",
            "running": running,
            "can_control": True,
        })
    except Exception:
        services.append({"id": "screen_watcher", "name": "Screen Watcher", "running": False, "can_control": True, "category": "monitoring", "description": "Analyse ecran Ollama"})

    # ── Scheduler ──
    try:
        from scripts.scheduler import scheduler as _sched
        sched_running = _sched.running if hasattr(_sched, 'running') else getattr(_sched, 'state', 0) == 1
        jobs_count = len(_sched.get_jobs()) if sched_running else 0
        services.append({
            "id": "scheduler",
            "name": "Scheduler",
            "description": f"APScheduler ({jobs_count} jobs)",
            "category": "core",
            "running": sched_running,
            "can_control": True,
        })
    except Exception:
        services.append({"id": "scheduler", "name": "Scheduler", "running": False, "can_control": True, "category": "core", "description": "APScheduler"})

    # ── Relationship Analyzer ──
    try:
        from scripts.relationship_analyzer import analyzer as _rel
        running_rel = getattr(_rel, '_running', False)
        services.append({
            "id": "relationship_analyzer",
            "name": "Relationship Analyzer",
            "description": "Analyse iMessage -> profils relationnels",
            "category": "analysis",
            "running": running_rel,
            "can_control": True,
        })
    except Exception:
        services.append({"id": "relationship_analyzer", "name": "Relationship Analyzer", "running": False, "can_control": True, "category": "analysis", "description": "Analyse iMessage"})

    # ── Processus externes (lecture seule — check via subprocess) ──

    # Ollama
    import subprocess as _sp
    try:
        r = _sp.run(["pgrep", "-f", "ollama"], capture_output=True, timeout=3)
        ollama_running = r.returncode == 0
    except Exception:
        ollama_running = False
    services.append({
        "id": "ollama",
        "name": "Ollama",
        "description": "LLM local (qwen2.5-vl, triage)",
        "category": "external",
        "running": ollama_running,
        "can_control": True,
    })

    # TV Dashboard (port 5174)
    import socket as _sock
    tv_running = False
    try:
        with _sock.create_connection(("127.0.0.1", 5174), timeout=1):
            tv_running = True
    except Exception:
        pass
    services.append({
        "id": "tv_dashboard",
        "name": "TV Dashboard",
        "description": "Dashboard War Room (port 5174)",
        "category": "external",
        "running": tv_running,
        "can_control": True,
    })

    # Vite Dev Server (port 5173)
    vite_running = False
    try:
        with _sock.create_connection(("127.0.0.1", 5173), timeout=1):
            vite_running = True
    except Exception:
        pass
    services.append({
        "id": "vite_dev",
        "name": "Vite Dev Server",
        "description": "Frontend dev (port 5173)",
        "category": "external",
        "running": vite_running,
        "can_control": False,
    })

    return services


async def _start_service(service: str) -> dict[str, object]:
    """Demarre un service par son id."""
    svc = service.strip().lower()

    if svc == "audio_daemon":
        from scripts.audio_daemon import audio_daemon as _ad
        if _ad.enabled and _ad._running:
            return {"ok": True, "message": "Deja actif"}
        _service_tasks["audio_daemon"] = asyncio.create_task(_ad.start(), name="audio_daemon_ctrl")
        return {"ok": True, "message": "Audio daemon demarre"}

    if svc == "email_watcher":
        from scripts.email_watcher import email_watcher as _ew
        if getattr(_ew, '_running', False) or getattr(_ew, 'running', False):
            return {"ok": True, "message": "Deja actif"}
        _service_tasks["email_watcher"] = asyncio.create_task(_ew.start(), name="email_watcher_ctrl")
        return {"ok": True, "message": "Email watcher demarre"}

    if svc == "jarvis_daemon":
        from scripts.jarvis_daemon import daemon as _jd
        if getattr(_jd, '_running', False) or getattr(_jd, 'running', False):
            return {"ok": True, "message": "Deja actif"}
        _service_tasks["jarvis_daemon"] = asyncio.create_task(_jd.start(), name="jarvis_daemon_ctrl")
        return {"ok": True, "message": "JARVIS daemon demarre"}

    if svc == "screen_watcher":
        from scripts.screen_watcher import screen_watcher as _sw
        if getattr(_sw, "running", False):
            return {"ok": True, "message": "Deja actif"}
        try:
            from scripts.jarvis_daemon import daemon as _jd
            if getattr(_jd, "running", False):
                return {"ok": True, "message": "Deja actif via jarvis_daemon"}
        except Exception:
            pass
        _service_tasks["screen_watcher"] = asyncio.create_task(_sw.start(), name="screen_watcher_ctrl")
        return {"ok": True, "message": "Screen watcher demarre"}

    if svc == "scheduler":
        from scripts.scheduler import start_scheduler as _start_sched
        _start_sched()
        return {"ok": True, "message": "Scheduler demarre"}

    if svc == "relationship_analyzer":
        from scripts.relationship_analyzer import analyzer as _rel
        _service_tasks["relationship_analyzer"] = asyncio.create_task(
            _rel.run_initial_scan(), name="relationship_analyzer_ctrl"
        )
        return {"ok": True, "message": "Analyzer lance"}

    if svc == "ollama":
        subprocess.Popen(["ollama", "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return {"ok": True, "message": "Ollama lance"}

    if svc == "tv_dashboard":
        tv_dir = Path(__file__).resolve().parent / "tv"
        subprocess.Popen(
            ["python3", str(tv_dir / "server.py")],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return {"ok": True, "message": "TV dashboard lance"}

    return {"ok": False, "error": f"Service inconnu : {service}"}


async def _stop_service(service: str) -> dict[str, object]:
    """Arrete un service par son id."""
    svc = service.strip().lower()

    if svc == "audio_daemon":
        from scripts.audio_daemon import audio_daemon as _ad
        await _ad.stop()
        task = _service_tasks.pop("audio_daemon", None)
        if task and not task.done():
            task.cancel()
        return {"ok": True, "message": "Audio daemon arrete"}

    if svc == "email_watcher":
        from scripts.email_watcher import email_watcher as _ew
        _ew.stop()
        task = _service_tasks.pop("email_watcher", None)
        if task and not task.done():
            task.cancel()
        return {"ok": True, "message": "Email watcher arrete"}

    if svc == "jarvis_daemon":
        from scripts.jarvis_daemon import daemon as _jd
        _jd.stop()
        task = _service_tasks.pop("jarvis_daemon", None)
        if task and not task.done():
            task.cancel()
        return {"ok": True, "message": "JARVIS daemon arrete"}

    if svc == "screen_watcher":
        from scripts.screen_watcher import screen_watcher as _sw
        _sw.stop()
        task = _service_tasks.pop("screen_watcher", None)
        if task and not task.done():
            task.cancel()
        return {"ok": True, "message": "Screen watcher arrete"}

    if svc == "scheduler":
        from scripts.scheduler import shutdown_scheduler as _stop_sched
        _stop_sched()
        return {"ok": True, "message": "Scheduler arrete"}

    if svc == "relationship_analyzer":
        task = _service_tasks.pop("relationship_analyzer", None)
        if task and not task.done():
            task.cancel()
        return {"ok": True, "message": "Analyzer arrete"}

    if svc == "ollama":
        subprocess.run(["pkill", "-f", "ollama"], capture_output=True)
        return {"ok": True, "message": "Ollama arrete"}

    if svc == "tv_dashboard":
        result = subprocess.run(
            ["lsof", "-nP", "-iTCP:5174", "-sTCP:LISTEN", "-t"],
            capture_output=True, text=True, timeout=5,
        )
        pids = result.stdout.strip().split()
        for pid in pids:
            if pid:
                subprocess.run(["kill", "-TERM", pid], capture_output=True)
        return {"ok": True, "message": "TV dashboard arrete"}

    return {"ok": False, "error": f"Service inconnu : {service}"}


# TAG_MAP pour les logs : un tag par service pour filtrer backend.log
_SERVICE_LOG_TAGS: dict[str, str] = {
    "audio_daemon": "audio_daemon",
    "email_watcher": "email_watcher",
    "jarvis_daemon": "daemon",
    "screen_watcher": "screen",
    "scheduler": "scheduler",
    "relationship_analyzer": "analyzer",
    "ollama": "ollama",
    "tv_dashboard": "tv",
}


@app.get("/api/control/services")
async def control_list_services():
    """Liste tous les services avec leur etat."""
    return {"services": _get_all_services_status()}


@app.post("/api/control/{service}/start")
async def control_start_service(service: str):
    """Demarre un service specifique."""
    result = await _start_service(service)
    return result


@app.post("/api/control/{service}/stop")
async def control_stop_service(service: str):
    """Arrete un service specifique."""
    result = await _stop_service(service)
    return result


@app.post("/api/control/{service}/restart")
async def control_restart_service(service: str):
    """Redemarre un service (stop + start)."""
    await _stop_service(service)
    await asyncio.sleep(1.0)
    result = await _start_service(service)
    return result


@app.post("/api/control/restart-all")
async def control_restart_all():
    """Redemarre tous les services internes (pas le backend lui-meme)."""
    results: dict[str, object] = {}
    for svc in INTERNAL_SERVICES:
        try:
            await _stop_service(svc)
            await asyncio.sleep(0.5)
            r = await _start_service(svc)
            results[svc] = r
        except Exception as e:
            results[svc] = {"ok": False, "error": str(e)}
    return {"results": results}


@app.post("/api/control/stop-all")
async def control_stop_all():
    """Arrete tous les services internes."""
    results: dict[str, object] = {}
    for svc in INTERNAL_SERVICES:
        try:
            r = await _stop_service(svc)
            results[svc] = r
        except Exception as e:
            results[svc] = {"ok": False, "error": str(e)}
    return {"results": results}


@app.post("/api/control/start-all")
async def control_start_all():
    """Demarre tous les services internes."""
    results: dict[str, object] = {}
    for svc in INTERNAL_SERVICES:
        try:
            r = await _start_service(svc)
            results[svc] = r
        except Exception as e:
            results[svc] = {"ok": False, "error": str(e)}
    return {"results": results}


@app.get("/api/control/{service}/logs")
async def control_service_logs(service: str, lines: int = 50):
    """Retourne les dernieres lignes de log pertinentes pour un service."""
    tag = _SERVICE_LOG_TAGS.get(service, service)
    log_file = Path("data/.jarvis_restart/backend.log")

    if not log_file.exists():
        return {"logs": [], "message": "Pas de fichier de log"}

    try:
        result = subprocess.run(
            ["grep", "-i", tag, str(log_file)],
            capture_output=True, text=True, timeout=5,
        )
        all_lines = result.stdout.strip().split("\n")
        recent = all_lines[-lines:] if len(all_lines) > lines else all_lines
        return {"logs": [l for l in recent if l.strip()], "count": len(recent)}
    except Exception as e:
        return {"logs": [], "error": str(e)}


# ── WebSocket chat ──────────────────────────────────────────

# Mémoire de proposition en attente — quand JARVIS propose "Veux-tu que je fasse X ?"
# et que l'utilisateur répond "oui" / "vas-y", on exécute immédiatement.
_pending_proposal: dict | None = None

_ACTION_RE = re.compile(r"```action\s*\n?(.*?)```", re.DOTALL | re.IGNORECASE)

# Regex fallback pour JSON inline hors backticks
_ACTION_JSON_INLINE_RE = re.compile(
    r'\{\s*"type"\s*:\s*"(\w+)"\s*[,}].*?\}',
    re.DOTALL,
)

ACTIONS_WITH_FOLLOWUP = frozenset({
    "terminal",
    "find_file",
    "system_info",
    "clipboard",
    "search_conversations",
    "weather",
    "calendar",
    "calendar_create",
    "open_app",
    "mail_read",
    "name_place",
    "where_am_i",
    "day_route",
})

# Types d'actions qui peuvent déclencher la boucle agentique (multi-étapes)
AGENTIC_ACTION_TYPES = frozenset({"terminal"})


def _is_agentic_action(action: dict) -> bool:
    """Boucle agentique uniquement pour terminal + complex:true (pas un simple ls/grep)."""
    return (
        action.get("type") in AGENTIC_ACTION_TYPES
        and action.get("complex") is True
    )


async def _run_loop_mode_ws(
    ws: WebSocket,
    task: str,
    conversation_id: int,
    *,
    voice_mode: bool = False,
) -> dict:
    """Exécute le mode /loop autonome avec événements WebSocket temps réel."""
    context = await _build_enriched_context(task, conversation_id)
    if voice_mode:
        context["voice_mode"] = True

    async def _on_event(event_type: str, data: dict) -> None:
        await ws.send_json({"type": event_type, **data})

    await ws.send_json({
        "type": "status",
        "content": f"Mode autonome activé — {task[:120]}",
    })

    loop_result = await run_autonomous_loop(
        task,
        conversation_id,
        context,
        on_event=_on_event,
    )

    synthesis = loop_result.get("synthesis") or "Boucle terminée."
    emotion = "neutral"
    display_text = finalize_assistant_display_text(synthesis)

    try:
        save_message(
            conversation_id,
            "assistant",
            display_text,
            agent="loop",
            model=config.LOOP_MODEL,
            cost=float(loop_result.get("total_cost") or 0.0),
        )
        update_conversation_activity(conversation_id)
        asyncio.create_task(_maybe_title_conversation(conversation_id))
    except Exception as exc:
        logger.warning("[loop] save_message : %s", exc)

    await ws.send_json({
        "type": "response",
        "agent": "loop",
        "category": "LOOP",
        "content": display_text,
        "model": config.LOOP_MODEL,
        "cost": loop_result.get("total_cost", 0.0),
        "emotion": emotion,
        "loop": {
            "status": loop_result.get("final_status"),
            "steps": loop_result.get("step_count"),
            "llm_calls": loop_result.get("total_llm_calls"),
        },
    })

    return {"emotion": emotion, "response": display_text, "loop_result": loop_result}


async def _run_loop_mode_internal(
    task: str,
    conversation_id: int,
    *,
    voice_mode: bool = False,
) -> dict:
    """Mode /loop sans WebSocket (REST, daemon, iMessage)."""
    context = await _build_enriched_context(task, conversation_id)
    if voice_mode:
        context["voice_mode"] = True

    loop_result = await run_autonomous_loop(task, conversation_id, context)
    synthesis = loop_result.get("synthesis") or "Boucle terminée."
    display_text = finalize_assistant_display_text(synthesis)

    try:
        save_message(
            conversation_id,
            "assistant",
            display_text,
            agent="loop",
            model=config.LOOP_MODEL,
            cost=float(loop_result.get("total_cost") or 0.0),
        )
        update_conversation_activity(conversation_id)
    except Exception as exc:
        logger.warning("[loop] save_message internal : %s", exc)

    return {
        "text": display_text,
        "emotion": "neutral",
        "agent": "loop",
        "model": config.LOOP_MODEL,
        "cost": float(loop_result.get("total_cost") or 0.0),
        "loop_result": loop_result,
    }


_PROPOSAL_MARKERS = (
    "veux-tu", "veux tu", "voulez-vous", "souhaites-tu", "souhaites tu",
    "dois-je", "dois je", "puis-je", "puis je", "tu confirmes",
    "confirmer", "je peux le", "je peux la", "je peux les",
    "shall i", "want me to", "should i",
)


def _should_defer_action(display_text: str, action: dict) -> bool:
    """Reporte l'exécution si JARVIS pose une question de confirmation."""
    if action.get("type") == "mail" and not action.get("confirmed"):
        return False  # mail : brouillon immédiat, pending séparé
    text = (display_text or "").lower()
    if "?" not in text:
        return False
    return any(marker in text for marker in _PROPOSAL_MARKERS)


def _pop_pending_action_if_confirmed(text: str, conversation_id: int) -> dict | None:
    """Retire et retourne l'action pending si l'utilisateur confirme (« oui », « vas-y »…)."""
    global _pending_proposal

    if not _pending_proposal:
        return None

    if _pending_proposal.get("conversation_id") != conversation_id:
        _pending_proposal = None
        return None

    text_lower = text.strip().lower()
    confirmation_patterns = (
        "oui", "vas-y", "vas y", "fais-le", "fais le", "ok", "okay",
        "d'accord", "go", "lance", "exécute", "execute", "yes",
        "pourquoi pas", "je veux bien", "allez", "allé", "fonce",
        "oui vas-y", "oui vas y", "oui fais le", "oui stp", "oui merci",
    )

    is_confirmation = (
        text_lower in confirmation_patterns
        or any(text_lower.startswith(p) for p in confirmation_patterns if len(p) > 3)
    )

    if not is_confirmation:
        if _pending_proposal:
            logger.info("[pending] Proposition annulée (user a dit autre chose)")
        _pending_proposal = None
        return None

    action = {**_pending_proposal["action"], "confirmed": True}
    _pending_proposal = None
    logger.info(
        "[pending] Confirmation détectée « %s » → exécution de %s",
        text[:60], action.get("type"),
    )
    return action


def _maybe_store_pending_proposal(action: dict, conversation_id: int) -> None:
    """Stocke une proposition d'action en attente de confirmation de l'utilisateur.

    Quand JARVIS dit « Veux-tu que je fasse X ? » avec un bloc action,
    on mémorise l'action pour que si l'utilisateur répond « oui » / « vas-y »
    au message suivant, l'action soit exécutée immédiatement.
    """
    global _pending_proposal
    _pending_proposal = {
        "conversation_id": conversation_id,
        "action": action,
    }


async def _check_pending_proposal(
    ws, text: str, conversation_id: int,
) -> dict | None:
    """Vérifie si l'utilisateur confirme une proposition en attente.

    Retourne le résultat de l'action si confirmée, None sinon.
    """
    action = _pop_pending_action_if_confirmed(text, conversation_id)
    if action is None:
        return None

    await ws.send_json({
        "type": "status",
        "content": f"Exécution de l'action : {action.get('type')}…",
    })

    try:
        return await execute_action(action)
    except Exception as e:
        logger.exception("[pending] execute_action : %s", e)
        return {"ok": False, "message": str(e)}


def _schedule_llm_log(
    *,
    agent: str,
    action_type: str,
    payload: dict[str, Any] | str,
    status: str = "pending",
    execution_time_ms: int | None = None,
) -> None:
    """Log non bloquant des actions système/LLM."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        try:
            log_llm_action(agent, action_type, payload, status, execution_time_ms)
        except Exception:
            logger.debug("[llm-log] sync fallback failed", exc_info=True)
        return

    async def _runner() -> None:
        try:
            await loop.run_in_executor(
                None,
                lambda: log_llm_action(agent, action_type, payload, status, execution_time_ms),
            )
        except Exception:
            logger.debug("[llm-log] async failed", exc_info=True)

    asyncio.create_task(_runner())


def _format_action_result_for_followup(action: dict, action_result: dict) -> str:
    """Texte dense pour la 2e passe orchestrateur (réformulation)."""
    t = action.get("type", "")
    if t == "terminal":
        parts = [f"Instruction : {action.get('command', '')}"]
        if action_result.get("code"):
            for block in action_result["code"]:
                parts.append(f"Code {block.get('language', 'python')} :\n{str(block.get('code', ''))[:1000]}")
        if action_result.get("output"):
            parts.append("Résultat :\n" + str(action_result["output"])[:3000])
        if action_result.get("stdout"):
            parts.append("Sortie :\n" + str(action_result.get("stdout", "")))
        if action_result.get("stderr"):
            parts.append("Erreurs :\n" + str(action_result.get("stderr", "")))
        if action_result.get("errors"):
            parts.append("Erreurs :\n" + "\n".join(str(e) for e in action_result["errors"]))
        if action_result.get("error"):
            parts.append("Erreur : " + str(action_result["error"]))
        if action_result.get("summary"):
            parts.append("Résumé : " + str(action_result["summary"])[:500])
        return "\n\n".join(parts)
    if t == "find_file":
        files = action_result.get("files") or []
        if not files:
            return "Aucun fichier correspondant."
        return "Fichiers trouvés :\n" + "\n".join(files)
    if t == "clipboard":
        return "Contenu du presse-papier :\n" + str(action_result.get("content", ""))
    if t == "system_info":
        lines = [f"{k}: {v}" for k, v in action_result.items() if k != "ok"]
        return "\n".join(lines[:200])
    if t == "where_am_i":
        return action_result.get("message") or str(action_result.get("location") or "")
    if t == "day_route":
        return action_result.get("message") or ""
    if t == "weather":
        w = action_result.get("weather") or {}
        return (
            f"Météo {w.get('city', '?')} : {w.get('temp', '?')}°C, "
            f"{w.get('description', '?')}, humidité {w.get('humidity', '?')}%, "
            f"vent {w.get('wind_speed', '?')} km/h"
        )
    if t == "calendar":
        events = action_result.get("events") or []
        if not events:
            return "Aucun événement à l'agenda pour cette période."
        lines = [f"- {e.get('start', '?')} : {e.get('summary', e.get('title', '?'))}" for e in events[:20]]
        return "Événements :\n" + "\n".join(lines)
    if t == "calendar_create":
        return action_result.get("message") or "Événement créé."
    if t == "open_app":
        return action_result.get("message") or f"Application {action.get('name', '?')} ouverte."
    if t == "mail_read":
        emails = action_result.get("emails") or []
        if not emails:
            return "Aucun mail non lu."
        lines = [f"- De: {e.get('from', '?')} | {e.get('subject', '?')}" for e in emails[:10]]
        return "Mails non lus :\n" + "\n".join(lines)
    if t == "name_place":
        return action_result.get("message") or "Lieu enregistré."
    return str(action_result)[:8000]


def _extract_action_from_text(text: str) -> tuple[dict | None, str]:
    """Extrait un bloc ```action {JSON}``` d'une réponse — tolérant au format.

    Accepte :
    - `` ```action\\n{JSON}\\n``` `` (standard)
    - `` ```action {JSON}``` `` (sans nouvelle ligne)
    - JSON inline hors backticks (fallback)

    Retourne (action_dict, texte_propre) ou (None, text).
    """
    import json as _json

    # 1. Format standard / tolérant
    m = _ACTION_RE.search(text)
    if m:
        json_str = m.group(1).strip()
        clean = (text[: m.start()] + text[m.end():]).strip()
        try:
            action = _json.loads(json_str)
            if isinstance(action, dict) and "type" in action:
                return action, clean
        except _json.JSONDecodeError:
            pass

    # 2. Fallback : JSON inline avec "type"
    m2 = _ACTION_JSON_INLINE_RE.search(text)
    if m2:
        try:
            start = m2.start()
            depth = 0
            end = start
            for i, ch in enumerate(text[start:], start):
                if ch == '{':
                    depth += 1
                elif ch == '}':
                    depth -= 1
                    if depth == 0:
                        end = i + 1
                        break
            json_str = text[start:end]
            action = _json.loads(json_str)
            if isinstance(action, dict) and "type" in action:
                clean = (text[:start] + text[end:]).strip()
                return action, clean
        except (_json.JSONDecodeError, ValueError):
            pass

    return None, text


async def _maybe_title_conversation(conv_id: int) -> None:
    """Génère un titre court si la conversation n'en a pas encore et a au moins 1 user + 1 assistant."""
    try:
        conv = get_conversation_detail(conv_id)
        if not conv or conv.get("title"):
            return
        msgs = conv.get("messages", [])
        has_user = any(m.get("role") == "user" for m in msgs)
        has_assistant = any(m.get("role") == "assistant" for m in msgs)
        if not (has_user and has_assistant):
            return
        first_msgs = msgs[:4]
        context = "\n".join([f"{m['role']}: {m['content'][:100]}" for m in first_msgs])
        result = await llm.chat(
            messages=[{"role": "user", "content": context}],
            model=config.DEEPSEEK_FAST_MODEL,
            system="Génère un titre court (3-6 mots) pour cette conversation. Pas de guillemets, pas de ponctuation finale. Juste le titre. Exemples : 'Révision exam droit', 'Analyse relation Bertille', 'Planning semaine', 'Problème code Python'.",
            max_tokens=20,
            temperature=0.3,
            use_cache=False,
        )
        title = (result.get("content") or "").strip().strip('"').strip("'")
        if title:
            update_conversation(conv_id, title=title)
            _schedule_llm_log(
                agent="system",
                action_type="auto_title",
                payload={"conversation_id": conv_id, "title": title},
                status="success",
            )
            logger.info("[conv] Titre auto : #%d → %s", conv_id, title)
    except Exception as e:
        _schedule_llm_log(
            agent="system",
            action_type="auto_title",
            payload={"conversation_id": conv_id, "error": str(e)},
            status="error",
        )
        logger.debug("[conv] _maybe_title_conversation : %s", e)


async def _send_tts_streaming(ws: WebSocket, text: str, emotion: str) -> None:
    """Envoie `speaking`, chunks audio, puis `speech_done` (boucle cliente).

    Le moteur TTS est lu dynamiquement depuis `app_settings.tts_engine` à chaque
    appel — pas besoin de redémarrer le serveur pour changer de backend.
    """
    from audio.tts import get_tts_by_name
    from audio.audio_format import tts_audio_mime
    from database import get_setting as _get_setting

    from audio.tts_cache import last_tts, speculative_tts

    engine_name = _get_setting("tts_engine", getattr(config, "TTS_ENGINE", "edge") or "edge")
    active_engine = get_tts_by_name(engine_name)

    audio_mime = tts_audio_mime(engine_name)
    await ws.send_json({"type": "speaking", "emotion": emotion, "audio_mime": audio_mime})
    if not (text and text.strip()) or active_engine is None or not getattr(active_engine, "available", False):
        await ws.send_json({"type": "speech_done"})
        return

    # TTS spéculatif : la réponse correspond à un audio déjà pré-généré
    cached = speculative_tts.get(text, emotion)
    if cached:
        try:
            await ws.send_bytes(cached)
            last_tts.store(text, emotion, cached, audio_mime)
        except Exception as e:
            logger.error("[TTS] envoi cache spéculatif : %s", e)
        finally:
            await ws.send_json({"type": "speech_done"})
        return

    collected: list[bytes] = []
    try:
        async for chunk in active_engine.synthesize_stream(text, emotion=emotion):
            if chunk:
                collected.append(chunk)
                await ws.send_bytes(chunk)
    except Exception as e:
        logger.error("[TTS] Erreur streaming (%s) : %s", engine_name, e)
    finally:
        if collected:
            # « répète » rejouera exactement cet audio, sans re-génération
            last_tts.store(text, emotion, b"".join(collected), audio_mime)
        await ws.send_json({"type": "speech_done"})


async def _build_enriched_context(text: str, conversation_id: int) -> dict:
    """Construit le contexte enrichi à partir de toutes les sources de données.

    Appelé par _process_message (WS) ET _process_message_internal (REST).
    Contexte permanent : documents de la conversation.
    Contexte conditionnel : mails, calendar, météo, tâches, localisation, fichiers,
    enregistrements, conversations passées — détectés par mots-clés dans le texte.
    """
    context: dict = {}
    lower = text.lower()

    # ─── CONTEXTE PERMANENT ───────────────────────────────────────────────────
    # Documents attachés à la conversation
    try:
        conv_docs = get_conversation_documents(conversation_id)
        if conv_docs:
            docs_parts = [
                f"[DOCUMENT: {d['original_name']}]\n{str(d.get('extracted_text') or '')[:3000]}"
                for d in conv_docs
                if d.get("extracted_text")
            ]
            if docs_parts:
                context["documents_context"] = (
                    "[DOCUMENTS ATTACHÉS]\n" + "\n\n".join(docs_parts)
                )
    except Exception as e:
        logger.debug("[ctx] conv_docs : %s", e)

    # ─── CONTEXTE CONDITIONNEL ────────────────────────────────────────────────

    # Mails — mention explicite ou nom d'une personne connue
    mail_triggers = ["mail", "email", "courrier", "boîte", "inbox", "reçu", "envoyé",
                     "message de", "écrit", "mails", "messagerie"]
    people_names: list[str] = []
    try:
        people_names = [p["name"].lower() for p in get_all_people() if p.get("name")]
    except Exception:
        pass

    if any(t in lower for t in mail_triggers) or any(n in lower for n in people_names):
        try:
            if mail_client and mail_client.is_available():
                emails = await mail_client.get_unread(10)
                if emails:
                    context["emails_context"] = "\n".join([
                        f"- De: {e.get('from', '')} | Objet: {e.get('subject', '')} | "
                        f"{str(e.get('preview', '') or e.get('snippet', ''))[:300]}"
                        for e in emails
                    ])
        except Exception as ex:
            logger.warning("[ctx] mail : %s", ex)

    # Calendar — planning, agenda, dates
    calendar_triggers = ["planning", "agenda", "rdv", "rendez-vous", "calendrier",
                         "emploi du temps", "semaine", "demain", "aujourd'hui",
                         "ce soir", "ce matin", "cours", "quand", "horaire", "programme"]
    if any(t in lower for t in calendar_triggers):
        try:
            if calendar_client and calendar_client.is_available():
                events = await calendar_client.get_today_events()
                if events:
                    context["calendar_context"] = "\n".join([
                        f"- {e.get('start', '?')} → {e.get('end', '?')} : {e.get('summary', '?')}"
                        for e in events
                    ])
        except Exception as ex:
            logger.warning("[ctx] calendar : %s", ex)

    # Météo — conditions climatiques
    weather_triggers = ["météo", "meteo", "temps", "pluie", "soleil", "parapluie",
                        "température", "chaud", "froid", "dehors", "sortir"]
    if any(t in lower for t in weather_triggers):
        try:
            if weather and weather.is_available():
                w = await weather.get_current()
                if w:
                    context["weather_context"] = (
                        f"{w.get('city', '?')} : {w.get('temp', '?')}°C, "
                        f"{w.get('description', '?')}"
                    )
        except Exception as ex:
            logger.warning("[ctx] weather : %s", ex)

    # Tâches — todo, deadlines
    task_triggers = ["tâche", "tache", "todo", "faire", "à faire", "en retard",
                     "priorité", "rappel", "deadline", "échéance", "tâches"]
    if any(t in lower for t in task_triggers):
        try:
            tasks = get_tasks()
            if tasks:
                context["tasks_context"] = "\n".join([
                    f"- [{t['priority']}] {t['title']} ({t['status']})" +
                    (f" — échéance {t['due_date']}" if t.get("due_date") else "")
                    for t in tasks[:10]
                ])
        except Exception as ex:
            logger.warning("[ctx] tasks : %s", ex)

    # Localisation — lieu actuel, position GPS
    location_triggers = ["où", "position", "lieu", "ici", "maison", "bureau", "salle",
                         "adresse", "localisation", "trajet", "déplacement"]
    if any(t in lower for t in location_triggers):
        try:
            from integrations.location import location_manager
            status = await location_manager.get_status()
            if status:
                loc_text = ""
                if status.get("current_visit"):
                    loc_text = f"Actuellement à : {status['current_visit'].get('place_name', '?')}"
                elif status.get("current_location"):
                    loc = status["current_location"]
                    loc_text = f"Position : {loc.get('latitude', '?')}, {loc.get('longitude', '?')}"
                if loc_text:
                    context["location_context"] = loc_text
        except Exception:
            pass

    # Fichiers / documents scolaires
    file_triggers = ["fichier", "document", "cours", "pdf", "rapport", "devoir",
                     "dissertation", "fiche", "upload", "télécharger", "documents"]
    if any(t in lower for t in file_triggers):
        try:
            docs = get_school_documents(limit=10)
            if docs:
                context["school_docs_context"] = "\n".join([
                    f"- {d['title']} ({d.get('doc_type', '?')})"
                    for d in docs
                ])
        except Exception:
            pass
        try:
            recs = get_recordings(limit=5)
            if recs:
                context["recordings_context"] = "\n".join([
                    f"- {r.get('title', r.get('label', '?'))} ({r.get('duration_seconds', 0)}s)"
                    for r in recs
                ])
        except Exception:
            pass

    # Conversations passées — référence au passé
    memory_triggers = ["on avait", "la dernière fois", "tu te souviens", "on a parlé",
                       "rappelle", "avant", "hier on", "la semaine dernière", "souviens-toi"]
    if any(t in lower for t in memory_triggers):
        try:
            recent_convs = get_conversations(limit=5)
            if recent_convs:
                context["recent_conversations"] = "\n".join([
                    f"- [{c.get('title', 'Sans titre')}] {str(c.get('last_message', ''))[:100]}"
                    for c in recent_convs
                ])
        except Exception:
            pass

    # Contexte écran (toujours injecté si disponible — c'est gratuit côté tokens cachés)
    try:
        screen_ctx = get_current_screen_context()
        if screen_ctx:
            context["screen_context"] = (
                f"Écran : {screen_ctx.get('app', '?')} — "
                f"{screen_ctx.get('activity', '?')} (mood: {screen_ctx.get('mood', '?')})"
            )
    except Exception:
        pass

    # Temps par app aujourd'hui — uniquement si la question concerne la productivité
    screen_triggers = [
        "temps", "productivité", "productif", "travaillé", "passé combien",
        "app", "application", "écran", "screen time", "distrait", "procrastin",
    ]
    if any(t in lower for t in screen_triggers):
        try:
            usage = get_app_usage()
            if usage:
                top = sorted(usage, key=lambda x: x.get("duration_seconds", 0), reverse=True)[:10]
                context["screen_time_context"] = "\n".join([
                    f"- {u['app']} : {int(u.get('duration_seconds', 0)) // 60} min"
                    for u in top
                ])
        except Exception:
            pass

    _schedule_llm_log(
        agent="system",
        action_type="context_enrichment",
        payload={"conversation_id": conversation_id, "keys": sorted(list(context.keys())), "key_count": len(context)},
        status="success",
    )
    return context


async def _process_message_internal(
    text: str,
    conversation_id: int,
    voice_mode: bool = False,
) -> dict:
    """Pipeline JARVIS sans WebSocket — pour les endpoints REST (journal, contacts, etc.).

    Applique le même enrichissement de contexte que _process_message, appelle l'orchestrateur,
    exécute les actions avec 2e passe si nécessaire, sauvegarde le message assistant.

    Retourne {text, emotion, action, action_result, agent, model, cost}.
    """
    try:
        jarvis_patterns = (
            "noté, monsieur",
            "ajouté à l'agenda",
            "bien noté",
            "je m'en occupe",
        )
        if isinstance(text, str) and any(text.strip().lower().startswith(p) for p in jarvis_patterns):
            logger.warning("[anti-loop] Message ignoré (ressemble à une réponse JARVIS): %s", text[:80])
            return {
                "text": "",
                "emotion": "neutral",
                "action": None,
                "action_result": None,
                "agent": "none",
                "model": None,
                "cost": 0.0,
            }

        original_text = text
        lower = text.lower()

        # ── Mode autonome /loop ──
        loop_task = parse_loop_command(original_text)
        if loop_task is not None:
            if not loop_task.strip():
                return {
                    "text": "Usage : /loop [tâche à accomplir autonomement]",
                    "emotion": "neutral",
                    "action": None,
                    "action_result": None,
                    "agent": "loop",
                    "model": config.LOOP_MODEL,
                    "cost": 0.0,
                }
            try:
                save_message(conversation_id, "user", original_text)
            except Exception as exc:
                logger.debug("[loop] save user internal : %s", exc)
            return await _run_loop_mode_internal(
                loop_task.strip(),
                conversation_id,
                voice_mode=voice_mode,
            )

        # Confirmation « oui / vas-y » sur une action en attente (REST)
        pending_action = _pop_pending_action_if_confirmed(original_text, conversation_id)
        if pending_action is not None:
            try:
                action_result = await execute_action(pending_action)
            except Exception as e:
                logger.exception("[internal-pending] execute_action : %s", e)
                action_result = {"ok": False, "message": str(e)}

            display_text = str(action_result.get("message", "Action exécutée."))
            emotion = "neutral"
            final_meta: dict = {
                "agent": "orchestrator",
                "model": None,
                "tokens_in": 0,
                "tokens_out": 0,
                "cost": 0.0,
            }

            if (
                action_result.get("ok")
                and not action_result.get("needs_confirmation")
                and pending_action.get("type") in ACTIONS_WITH_FOLLOWUP
            ):
                try:
                    payload = _format_action_result_for_followup(pending_action, action_result)
                    fu = await orchestrator.handle(
                        (
                            f"Résultat brut de l'action :\n\n{payload}\n\n"
                            f"Question originale : {original_text}\n\n"
                            "Résume ce résultat de façon claire et utile. Pas de bloc action."
                        ),
                        conversation_id=conversation_id,
                        voice_mode=voice_mode,
                    )
                    emotion = fu.get("emotion", emotion)
                    display_text = finalize_assistant_display_text(fu.get("response", display_text))
                    final_meta = fu
                except Exception as e:
                    logger.exception("[internal-pending-followup] %s", e)

            display_text = re.sub(
                r'```(?:json|action|save)\s*\{[\s\S]*?\}\s*```', '', display_text
            ).strip() or display_text

            try:
                save_message(
                    conversation_id, "assistant", display_text,
                    agent=final_meta.get("agent"),
                    model=final_meta.get("model"),
                    tokens_in=final_meta.get("tokens_in", 0),
                    tokens_out=final_meta.get("tokens_out", 0),
                    cost=final_meta.get("cost", 0.0),
                )
            except Exception as e:
                logger.error("[internal-pending] save assistant : %s", e)

            return {
                "text": display_text,
                "emotion": emotion,
                "action": pending_action,
                "action_result": action_result,
                "agent": final_meta.get("agent"),
                "model": final_meta.get("model"),
                "cost": float(final_meta.get("cost") or 0.0),
            }

        context = await _build_enriched_context(text, conversation_id)

        if voice_mode:
            context["voice_mode"] = True

        if "documents_context" in context:
            text = context.pop("documents_context") + "\n\n" + text

        result = await orchestrator.handle(
            text, conversation_id=conversation_id, voice_mode=voice_mode, context=context
        )
        full_response = result.get("response", "")
        emotion_raw, _ = extract_leading_emotion(full_response)
        emotion = emotion_raw or result.get("emotion", "neutral")

        action, after_action = _extract_action_from_text(full_response)
        display_text = finalize_assistant_display_text(after_action)

        action_result: dict | None = None
        final_meta = result

        if action:
            _schedule_llm_log(
                agent=str(result.get("agent") or "orchestrator"),
                action_type=str(action.get("type") or "unknown"),
                payload={"conversation_id": conversation_id, "action": action},
                status="pending",
            )

            if _is_agentic_action(action):
                agent_name = result.get("agent", "orchestrator")
                agent_obj = get_agent(agent_name) or orchestrator
                loop_result = await agent_obj._run_agentic_loop(
                    user_message=original_text,
                    conversation_id=conversation_id,
                    context=context,
                    initial_action=action,
                )
                results_text = "\n".join([
                    f"Étape {r['step']}: "
                    f"{str(r['result'].get('output', r['result'].get('message', '')))[:1000]}"
                    for r in loop_result.get("results", [])
                    if isinstance(r.get("step"), int)
                ])
                action_result = {
                    "ok": loop_result.get("final_status") != "failed",
                    "output": results_text,
                    "agentic": True,
                }
                if results_text:
                    fu = await orchestrator.handle(
                        (
                            f"Résultats :\n\n{results_text}\n\n"
                            f"Question : {original_text}\n\n"
                            "Synthétise."
                        ),
                        conversation_id=conversation_id,
                        voice_mode=voice_mode,
                    )
                    emotion = fu.get("emotion", emotion)
                    display_text = finalize_assistant_display_text(
                        fu.get("response", display_text)
                    )
                    final_meta = fu
            else:
                if _should_defer_action(display_text, action):
                    _maybe_store_pending_proposal(action, conversation_id)
                    action_result = {
                        "ok": True,
                        "deferred": True,
                        "message": display_text,
                    }
                else:
                    try:
                        action_result = await execute_action(action)
                        logger.info(
                            "[internal-action] %s → ok=%s",
                            action.get("type"),
                            action_result.get("ok") if action_result else None,
                        )
                        if action_result.get("needs_confirmation"):
                            _maybe_store_pending_proposal(action, conversation_id)
                    except Exception as e:
                        logger.exception("[internal-action] execute_action : %s", e)
                        action_result = {"ok": False, "message": str(e)}

                # 2e passe pour les actions avec followup
                if (
                    action_result
                    and not action_result.get("deferred")
                    and action.get("type") in ACTIONS_WITH_FOLLOWUP
                    and not action_result.get("needs_confirmation")
                    and action_result.get("ok")
                ):
                    try:
                        payload = _format_action_result_for_followup(action, action_result)
                        fu = await orchestrator.handle(
                            (
                                f"Résultat brut de l'action :\n\n{payload}\n\n"
                                f"Question originale : {original_text}\n\n"
                                "Résume ce résultat de façon claire et utile. Pas de bloc action."
                            ),
                            conversation_id=conversation_id,
                            voice_mode=voice_mode,
                        )
                        emotion = fu.get("emotion", emotion)
                        display_text = finalize_assistant_display_text(fu.get("response", display_text))
                        final_meta = fu
                    except Exception as e:
                        logger.exception("[internal-followup] %s", e)

        # Nettoyage final
        display_text = re.sub(r'```(?:json|action|save)\s*\{[\s\S]*?\}\s*```', '', display_text).strip()
        display_text = re.sub(r'^\s*\[\w+\]\s*\n?', '', display_text).strip()
        if not display_text:
            display_text = "Bien noté."

        try:
            save_message(
                conversation_id, "assistant", display_text,
                agent=final_meta.get("agent"),
                model=final_meta.get("model"),
                tokens_in=final_meta.get("tokens_in", 0),
                tokens_out=final_meta.get("tokens_out", 0),
                cost=final_meta.get("cost", 0.0),
            )
        except Exception as e:
            logger.error("[internal] save assistant message : %s", e)

        try:
            update_conversation_activity(conversation_id)
        except Exception:
            pass

        asyncio.create_task(_maybe_title_conversation(conversation_id))

        return {
            "text": display_text,
            "emotion": emotion,
            "action": action,
            "action_result": action_result,
            "agent": final_meta.get("agent"),
            "model": final_meta.get("model"),
            "cost": float(final_meta.get("cost") or 0.0),
        }
    except Exception as e:
        logger.exception("[_process_message_internal] %s", e)
        return {
            "text": "Une erreur est survenue lors du traitement.",
            "emotion": "neutral",
            "action": None,
            "action_result": None,
            "agent": None,
            "model": None,
            "cost": 0.0,
        }


async def _process_voice_fast(text: str, conversation_id: int) -> dict:
    """Pipeline vocal ultra-rapide — 2 passes si action necessaire, avec tracing debug.

    Pass 1 : DeepSeek flash decide quoi faire (reponse directe OU bloc action).
    Pass 2 : si action -> execute -> DeepSeek reformule le resultat en reponse vocale.

    La reponse textuelle n'est generee qu'APRES l'execution de l'action,
    garantissant que le TTS vocalise l'information demandee (pas un "je reviens").

    Args:
        text: Transcription de la phrase prononcee.
        conversation_id: ID de la conversation daemon audio.

    Returns:
        dict avec cles: text, emotion, cost, action, latency_ms, debug_trace.
    """
    import time as _time
    from datetime import datetime as _datetime
    _t0 = _time.time()

    # ── Trace de debug ────────────────────────────────────────────────────────
    debug_trace: dict[str, Any] = {
        "timestamp": _datetime.now().strftime("%H:%M:%S"),
        "input_text": text,
        "system_prompt": "",
        "messages_sent": [],
        "raw_response": "",
        "response_clean": "",
        "emotion": "",
        "action_detected": None,
        "action_result": None,
        "pass2_prompt": None,
        "pass2_response": None,
        "latency_llm_pass1_ms": 0,
        "latency_llm_pass2_ms": 0,
        "latency_tts_ms": 0,
        "latency_total_ms": 0,
        "model": "",
        "tokens_in": 0,
        "tokens_out": 0,
        "cost": 0.0,
        "error": None,
    }

    # ── 0. Persona condensee pour le vocal (~50 tokens) ────────────────────────
    VOICE_PERSONA = (
        "Tu es JARVIS, majordome IA d'{}. Ton britannique, concis, sec. "
        "Tu l'appelles 'Monsieur' avec ironie bienveillante. "
        "Jamais d'emoji. Jamais de presentation ('je suis JARVIS'). "
        "Jamais de 'je reviens vers vous' ou 'un instant'. "
        "3 phrases max a l'oral. Pas de Markdown."
    ).format(config.USER_NAME)

    # ── 1. Contexte temporel minimal ──────────────────────────────────────────
    from agents import _get_horodatage
    horodatage = _get_horodatage()

    # ── 2. Historique recent (10 derniers messages, pas de build_full_context) ──
    history: list[dict[str, str]] = []
    try:
        raw = get_conversation_history(conversation_id, limit=10)
        history = [
            {"role": m["role"], "content": m["content"]}
            for m in raw
            if m.get("role") in ("user", "assistant") and m.get("content")
        ]
        if history and history[-1]["role"] == "user":
            history = history[:-1]
    except Exception as e:
        logger.debug("[voice_fast] get_conversation_history : %s", e)

    # ── Contexte ecran ──────────────────────────────────────────────────
    screen_context = ""
    try:
        ctx = get_current_screen_context()
        if ctx and ctx.get("app"):
            screen_context = f"\nECRAN : {ctx['app']}"
            if ctx.get("activity"):
                screen_context += f" — {ctx['activity']}"
            if ctx.get("mood"):
                screen_context += f" (mood: {ctx['mood']})"
    except Exception:
        pass

    # ── 3. System prompt compact — permet de répondre ET d'agir ──
    weather_city = getattr(config, "WEATHER_CITY", "Lille")

    ACTIONS_COMPACT = """ACTIONS (bloc ```action {"type":"...", ...} ``` — tu peux répondre ET agir) :
weather(city) | open_app(app_name) | task(title,priority) | reminder(title,due_date)
calendar(range?) | calendar_create(summary,start,end?) | mood(score)
mail(to,subject,body) | mail_read | note(content) | find_file(query)
clipboard(action,text?) | system_info(info) | name_place(name) | where_am_i | day_route
search_conversations(query) | search(query) | sleep | wake
terminal(command) — COMMANDE SHELL uniquement (ls, grep, python...), JAMAIS une question

RÈGLES :
- Questions d'actu, sport, résultats, infos : search(query) — pas la météo ni l'heure
- Météo : weather(city) — pas search
- Heure, date, aujourd'hui : réponds directement avec l'horodatage fourni
- Recherche dans tes conversations passées : search_conversations(query)
- Commande système : terminal(command) — le command doit être un shell valide
- Tâches complexes (code, analyse, debug) : terminal(command, complex:true)
- "mets-toi en veille" / "dors" / "pause" : sleep
- "réveille-toi" / "je suis là" : wake
- Si le contexte mémoire contient déjà l'info (météo chargée, calendar...) : réponds directement
- Tu peux répondre ET inclure un bloc action dans la même réponse.
- Pour les questions simples (heure, date, fait) : réponds directement.
- Pour les actions : ajoute le bloc action après ta réponse, ou uniquement le bloc action si c'est purement exécutif.
- Si l'utilisateur dit "oui" ou "vas-y" après ta proposition : produis immédiatement le bloc action."""

    system = f"""{horodatage}
{VOICE_PERSONA}
LIEU : {weather_city}, France{screen_context}

{ACTIONS_COMPACT}

RÈGLES SUPPLEMENTAIRES :
- Aucun bloc action = pas autorise a en inventer. Utilise uniquement les types decrits ci-dessus."""


    # ── Capture debug ─────────────────────────────────────────────────────────
    debug_trace["system_prompt"] = system
    debug_trace["messages_sent"] = [{"role": m["role"], "content": m["content"][:200]} for m in history]
    debug_trace["model"] = getattr(config, "DEEPSEEK_FAST_MODEL", "deepseek-chat")

    # ── 4. Pass 1 : DeepSeek flash decide (reponse directe OU action seule) ────
    messages = history + [{"role": "user", "content": text}]
    total_cost: float = 0.0

    _t_llm1 = _time.time()
    try:
        result = await llm.chat(
            messages=messages,
            model=config.DEEPSEEK_FAST_MODEL,
            system=system,
            max_tokens=250,
            temperature=0.5,
        )
        debug_trace["latency_llm_pass1_ms"] = round((_time.time() - _t_llm1) * 1000)
        raw_response = result.get("content", "") or ""
        debug_trace["raw_response"] = raw_response
        debug_trace["tokens_in"] = int(result.get("tokens_in", 0))
        debug_trace["tokens_out"] = int(result.get("tokens_out", 0))
        debug_trace["cost"] = float(result.get("cost", 0.0))
        total_cost += float(result.get("cost", 0.0))
    except Exception as e:
        logger.error("[voice_fast] LLM erreur pass 1 : %s", e)
        debug_trace["error"] = str(e)
        debug_trace["latency_llm_pass1_ms"] = round((_time.time() - _t_llm1) * 1000)
        debug_trace["latency_total_ms"] = round((_time.time() - _t0) * 1000)
        asyncio.create_task(_broadcast_voice_debug(debug_trace))
        _save_voice_debug_trace(debug_trace)
        return {
            "text": "Desole Monsieur, un probleme technique.",
            "emotion": "concerned",
            "cost": 0.0,
            "action": None,
            "latency_ms": debug_trace["latency_total_ms"],
            "debug_trace": debug_trace,
        }

    # ── 5. Extraire l'emotion (tag [emotion] en debut de reponse) ─────────────
    emotion = "neutral"
    emotion_match = re.match(r'^\s*\[(\w+)\]\s*\n?', raw_response)
    if emotion_match:
        emotion = emotion_match.group(1)
        raw_response = raw_response[emotion_match.end():]

    debug_trace["emotion"] = emotion

    # ── 6. Detecter un bloc action ────────────────────────────────────────────
    action_match = re.search(r'```action\s*\n?(.*?)```', raw_response, re.DOTALL | re.IGNORECASE)
    if not action_match:
        # Fallback : JSON brut inline avec "type"
        action_match = re.search(r'\{\s*"type"\s*:\s*"(\w+)"\s*[,}].*?\}', raw_response, re.DOTALL)

    if not action_match:
        # ── Pas d'action -> reponse directe (1 seul appel LLM) ─────────────────
        response_text = raw_response.strip()
        response_text = re.sub(r'```\w*\s*```', '', response_text).strip()
        debug_trace["response_clean"] = response_text
        debug_trace["latency_total_ms"] = round((_time.time() - _t0) * 1000)

        _save_voice_messages(conversation_id, text, response_text, total_cost)
        asyncio.create_task(_broadcast_voice_debug(debug_trace))
        _save_voice_debug_trace(debug_trace)

        latency_ms = debug_trace["latency_total_ms"]
        logger.info(
            "[voice_fast] %.0fms (direct) — «%s» → «%s»",
            latency_ms, text[:40], response_text[:60],
        )
        return {
            "text": response_text,
            "emotion": emotion,
            "cost": total_cost,
            "action": None,
            "latency_ms": latency_ms,
            "debug_trace": debug_trace,
        }

    # ── 7. Action detectee -> parser de maniere robuste ──────────────────────
    action_result: dict | None = None
    action: dict = {}
    try:
        if action_match:
            json_str = action_match.group(0)
            # Si c'est un match inline (pas de backticks), extraire l'objet JSON complet
            if not json_str.startswith("```"):
                # Trouver les bornes de l'objet JSON
                start = action_match.start()
                depth = 0
                end = start
                for i, ch in enumerate(raw_response[start:], start):
                    if ch == '{':
                        depth += 1
                    elif ch == '}':
                        depth -= 1
                        if depth == 0:
                            end = i + 1
                            break
                json_str = raw_response[start:end]
            else:
                # Format ```action ...``` → prendre le contenu
                inner = re.search(r'```action\s*\n?(.*?)```', json_str, re.DOTALL | re.IGNORECASE)
                if inner:
                    json_str = inner.group(1).strip()
                else:
                    json_str = action_match.group(1).strip()

            action = json.loads(json_str)
            debug_trace["action_detected"] = action

            action_type_direct = action.get("type", "").strip()

            if _is_agentic_action(action):
                from agents import get_agent as _get_agent
                agent_obj = _get_agent("devops") or _get_agent("info")
                if agent_obj:
                    loop_result = await agent_obj._run_agentic_loop(
                        user_message=text,
                        conversation_id=conversation_id,
                        context=None,
                        initial_action=action,
                    )
                    results_text = "\n".join([
                        f"Étape {r['step']}: "
                        f"{str(r['result'].get('output', r['result'].get('message', '')))[:1000]}"
                        for r in loop_result.get("results", [])
                        if isinstance(r.get("step"), int)
                    ])
                    action_result = {
                        "ok": loop_result.get("final_status") != "failed",
                        "output": results_text,
                        "agentic": True,
                    }
                else:
                    action_result = await execute_action(action)

            # ── Handlers directs bypass execute_action (latence zero) ────
            elif action_type_direct == "search":
                query = (action.get("query") or "").strip()
                if not query:
                    action_result = {"ok": True, "message": "Aucun terme de recherche fourni."}
                else:
                    try:
                        from integrations.web_search import web_search
                        summary = await web_search(query)
                        action_result = {"ok": True, "message": summary[:600], "query": query}
                    except Exception as e:
                        action_result = {"ok": False, "message": f"Recherche indisponible : {e}"}

            elif action_type_direct == "sleep":
                try:
                    from scripts.audio_daemon import audio_daemon
                    audio_daemon.enter_sleep_mode()
                    action_result = {"ok": True, "message": "Mode veille active — micro en sourdine"}
                except Exception as e:
                    action_result = {"ok": False, "message": f"Veille indisponible : {e}"}

            elif action_type_direct == "wake":
                try:
                    from scripts.audio_daemon import audio_daemon
                    audio_daemon.exit_sleep_mode()
                    action_result = {"ok": True, "message": "Mode ecoute reactive"}
                except Exception as e:
                    action_result = {"ok": False, "message": f"Reveil indisponible : {e}"}

            else:
                action_result = await execute_action(action)

            # ── Event bus : action detectee ──
            try:
                from jarvis.event_bus import JarvisEvent, event_bus as _eb
                _action_type = action.get("type", "?")
                _action_params = {k: v for k, v in action.items() if k != "type"}
                asyncio.create_task(_eb.emit(JarvisEvent(
                    type="agent.action",
                    agent="voice",
                    data={"action_type": _action_type, "action_params": _action_params},
                )))
            except Exception:
                pass

            debug_trace["action_result"] = action_result

            # ── Event bus : resultat action ──
            try:
                from jarvis.event_bus import JarvisEvent, event_bus as _eb
                _action_type = action.get("type", "?")
                _result_str = str(action_result.get("output", action_result.get("message", action_result)))[:300]
                asyncio.create_task(_eb.emit(JarvisEvent(
                    type="agent.action_result",
                    agent="voice",
                    data={
                        "action_type": _action_type,
                        "result": _result_str,
                        "latency_ms": int((_time.time() - _t_llm1) * 1000),
                    },
                )))
            except Exception:
                pass
    except json.JSONDecodeError as e:
        logger.warning("[voice_fast] JSON action invalide : %s", e)
        action_result = {"ok": False, "error": "JSON invalide"}
    except Exception as e:
        logger.warning("[voice_fast] Action erreur : %s", e)
        action_result = {"ok": False, "error": str(e)}

    if action_result is None:
        action_result = {"ok": False, "error": "Aucun resultat"}

    # ── 8. Pass 2 : DeepSeek reformule le resultat de l'action ─────────────────
    action_type = action.get("type", "?")
    result_summary = json.dumps(action_result, ensure_ascii=False, default=str)[:800]

    pass2_messages = history + [
        {"role": "user", "content": text},
        {"role": "assistant", "content": f"[Action executee : {action_type}]"},
        {
            "role": "user",
            "content": (
                f"Resultat de l'action {action_type} : {result_summary}\n\n"
                "Formule une reponse vocale naturelle et concise (1-3 phrases) a "
                "partir de ce resultat. Ne mentionne pas l'action elle-meme. "
                "Donne l'information directement."
            ),
        },
    ]

    pass2_system = f"""Tu es JARVIS, assistant personnel de {config.USER_NAME}. Tu parles a l'ORAL.
Formule une reponse naturelle a partir du resultat d'action ci-dessous.
1 a 3 phrases max. Pas de Markdown. Pas de "voici le resultat".
Donne l'information directement comme si tu la savais.
Date : {horodatage}."""

    debug_trace["pass2_prompt"] = pass2_system

    _t_llm2 = _time.time()
    try:
        result2 = await llm.chat(
            messages=pass2_messages,
            model=config.DEEPSEEK_FAST_MODEL,
            system=pass2_system,
            max_tokens=min(getattr(config, "VOICE_MAX_TOKENS", 500), 300),
            temperature=0.7,
        )
        debug_trace["latency_llm_pass2_ms"] = round((_time.time() - _t_llm2) * 1000)
        response_text = result2.get("content", "") or ""
        debug_trace["pass2_response"] = response_text
        total_cost += float(result2.get("cost", 0.0))
        debug_trace["cost"] = total_cost
        debug_trace["tokens_in"] += int(result2.get("tokens_in", 0))
        debug_trace["tokens_out"] += int(result2.get("tokens_out", 0))

        # Extraire emotion pass 2
        em2 = re.match(r'^\s*\[(\w+)\]\s*\n?', response_text)
        if em2:
            emotion = em2.group(1)
            response_text = response_text[em2.end():]

        debug_trace["emotion"] = emotion
        response_text = response_text.strip()

        # Fallback si le LLM pass 2 a genere une reponse vide
        if not response_text:
            response_text = _fallback_action_response(action_type, action_result)

    except Exception as e:
        logger.error("[voice_fast] LLM erreur pass 2 : %s", e)
        debug_trace["latency_llm_pass2_ms"] = round((_time.time() - _t_llm2) * 1000)
        debug_trace["error"] = str(e)
        response_text = _fallback_action_response(action_type, action_result)

    # ── 9. Sauvegarder et retourner ────────────────────────────────────────────
    debug_trace["response_clean"] = response_text
    debug_trace["latency_total_ms"] = round((_time.time() - _t0) * 1000)

    _save_voice_messages(conversation_id, text, response_text, total_cost)
    asyncio.create_task(_broadcast_voice_debug(debug_trace))
    _save_voice_debug_trace(debug_trace)

    latency_ms = debug_trace["latency_total_ms"]
    logger.info(
        "[voice_fast] %.0fms (action:%s) — «%s» → «%s»",
        latency_ms, action_type, text[:40], response_text[:60],
    )

    return {
        "text": response_text,
        "emotion": emotion,
        "cost": total_cost,
        "action": action_result,
        "latency_ms": latency_ms,
        "debug_trace": debug_trace,
    }


async def _broadcast_voice_debug(trace: dict[str, Any]) -> None:
    """Broadcast la trace de debug vocal via WebSocket (fire-and-forget)."""
    try:
        safe_trace = {
            k: v for k, v in trace.items()
            if isinstance(v, (str, int, float, bool, list, dict, type(None)))
        }
        await broadcast_ws({
            "type": "voice_debug_trace",
            **safe_trace,
        })
    except Exception as e:
        logger.debug("[voice_fast] broadcast debug: %s", e)


def _fallback_action_response(action_type: str, result: dict) -> str:
    """Reformulation basique si le LLM pass 2 echoue (pas d'appel API)."""
    if not result.get("ok"):
        return "Desole Monsieur, l'action a echoue."

    if action_type == "weather":
        data = result.get("data", {})
        city = data.get("city", config.WEATHER_CITY)
        temp = data.get("temp", "?")
        desc = data.get("description", "")
        return f"Il fait {temp} degres a {city}, {desc}."

    if action_type == "open_app":
        app_name = result.get("app_name", "l'application")
        return f"{app_name} est ouverte, Monsieur."

    if action_type == "task":
        return "Tache creee, Monsieur."

    if action_type == "reminder":
        return "Rappel cree, Monsieur."

    if action_type == "calendar":
        events = result.get("events", [])
        if not events:
            return "Votre agenda est vide, Monsieur."
        ev = events[0]
        return (
            f"Prochain evenement : {ev.get('summary', '?')} "
            f"a {ev.get('start', '?')}."
        )

    if action_type == "calendar_create":
        return "Evenement ajoute a votre agenda, Monsieur."

    if action_type == "terminal":
        output = result.get("output", "")[:100]
        return f"Commande executee. {output}" if output else "Commande executee, Monsieur."

    if action_type == "mood":
        return "Humeur enregistree, Monsieur."

    if action_type == "mail":
        return "Brouillon prepare, Monsieur."

    if action_type == "mail_read":
        emails = result.get("emails", [])
        count = len(emails) if emails else 0
        if count == 0:
            return "Vous n'avez aucun email non lu, Monsieur."

        stats = result.get("stats", {})
        urgent = stats.get("urgent", 0)
        response = f"Vous avez {count} email{'s' if count > 1 else ''} non lu{'s' if count > 1 else ''}"
        if urgent > 0:
            response += f" dont {urgent} urgent{'s' if urgent > 1 else ''}"
        response += "."

        # Ajouter les 3 premiers résumés
        summaries = []
        for e in emails[:3]:
            sender = e.get("from", "")
            s_name = sender.split("<")[0].strip() if "<" in sender else sender
            summary = (e.get("summary") or "").strip()
            if summary:
                summaries.append(f"{s_name} : {summary[:100]}")
        if summaries:
            response += " " + " | ".join(summaries)

        return response

    if action_type == "note":
        return "Note enregistree, Monsieur."

    if action_type == "find_file":
        files = result.get("files", [])
        count = len(files) if files else result.get("count", 0)
        if count == 0:
            return "Aucun fichier trouve, Monsieur."
        return f"{count} fichier(s) trouve(s), Monsieur."

    if action_type == "clipboard":
        if result.get("action") == "set" or "text" in result:
            return "Copie dans le presse-papiers, Monsieur."
        content = result.get("content", "")
        preview = content[:80] if content else ""
        return f"Presse-papiers : {preview}" if preview else "Presse-papiers vide, Monsieur."

    if action_type == "system_info":
        info_type = result.get("info", "")
        if "battery" in str(result) or info_type == "battery":
            pct = result.get("percentage", "?")
            return f"Batterie a {pct}%, Monsieur."
        if "wifi" in str(result) or info_type == "wifi":
            ssid = result.get("ssid", "inconnu")
            return f"Wi-Fi connecte a {ssid}, Monsieur."
        if "apps" in str(result) or info_type == "apps":
            apps = result.get("apps", [])
            return f"{len(apps)} applications ouvertes, Monsieur."
        # disk / fallback
        free = result.get("free", "?")
        return f"Espace disque disponible : {free}, Monsieur."

    if action_type == "name_place":
        name = result.get("name", result.get("message", "le lieu"))
        return f"Lieu nomme : {name}, Monsieur."

    if action_type == "where_am_i":
        msg = result.get("message", "Position inconnue.")
        return f"{msg}, Monsieur."

    if action_type == "day_route":
        msg = result.get("message", "Aucune visite aujourd'hui.")
        return f"{msg}, Monsieur."

    if action_type == "search_conversations":
        count = result.get("count", 0)
        if count == 0:
            return "Aucune conversation trouvee, Monsieur."
        return f"{count} conversation(s) trouvee(s), Monsieur."

    return "C'est fait, Monsieur."


def _save_voice_messages(
    conversation_id: int, user_text: str, assistant_text: str, cost: float
) -> None:
    """Sauvegarde les messages vocaux en DB (silencieux si erreur)."""
    try:
        save_message(conversation_id, "user", user_text)
        save_message(
            conversation_id,
            "assistant",
            assistant_text,
            agent="voice",
            model=config.DEEPSEEK_FAST_MODEL,
            cost=cost,
        )
        update_conversation_activity(conversation_id)
    except Exception as e:
        logger.debug("[voice_fast] save_message : %s", e)


async def _process_message(
    ws: WebSocket,
    content: str,
    conversation_id: int,
    *,
    voice_mode: bool = False,
    stream: bool = True,
    send_tts: bool = False,
) -> dict:
    """Pipeline unique texte + vocal : DB → orchestrateur (même enrichissement) →
    nettoyage affichage → actions → TTS optionnel.

    ``voice_mode=True`` : pas de streaming ; ``orchestrator.handle(..., voice_mode=True)``
    (préfixe ``[VOICE_MODE]``, Haiku + tokens courts via agents).

    Retourne ``{emotion, response}`` (``response`` = texte affichable nettoyé).
    """
    try:
        if voice_mode:
            stream = False

        original_text = content

        # Construire le contexte enrichi (mails, météo, calendar, tâches, etc.)
        try:
            extra_context = await _build_enriched_context(content, conversation_id)
        except Exception as e:
            logger.warning("[_process_message] _build_enriched_context : %s", e)
            extra_context = {}

        if "documents_context" in extra_context:
            content = extra_context.pop("documents_context") + "\n\n" + content

        try:
            save_message(conversation_id, "user", original_text)
        except Exception as e:
            logger.error("Erreur save user message : %s", e)

        try:
            update_conversation_activity(conversation_id)
        except Exception as e:
            logger.debug("[conv] update_activity user : %s", e)

        # ── Mode autonome /loop ──
        loop_task = parse_loop_command(original_text)
        if loop_task is not None:
            if not loop_task.strip():
                await ws.send_json({
                    "type": "error",
                    "message": "Usage : /loop [tâche à accomplir autonomement]",
                })
                return {"emotion": "neutral", "response": ""}
            return await _run_loop_mode_ws(
                ws,
                loop_task.strip(),
                conversation_id,
                voice_mode=voice_mode,
            )

        # ── Raccourci « répète » : rejoue le dernier audio TTS tel quel ──
        from audio.tts_cache import is_repeat_request, last_tts

        if is_repeat_request(original_text):
            entry = last_tts.get()
            if entry:
                try:
                    save_message(conversation_id, "assistant", entry["text"], agent="jarvis")
                except Exception as e:
                    logger.error("save répète : %s", e)
                await ws.send_json({
                    "type": "response",
                    "agent": "jarvis",
                    "content": entry["text"],
                    "emotion": entry["emotion"],
                    "model": "replay",
                    "tokens_in": 0, "tokens_out": 0, "cost": 0.0,
                })
                if send_tts:
                    await ws.send_json({
                        "type": "speaking",
                        "emotion": entry["emotion"],
                        "audio_mime": entry.get("mime", "audio/mpeg"),
                    })
                    await ws.send_bytes(entry["audio"])
                    await ws.send_json({"type": "speech_done"})
                return {"emotion": entry["emotion"], "response": entry["text"]}
            # rien à rejouer → le pipeline normal répond naturellement

        # ── Easter eggs vocaux : réplique codée en dur, zéro LLM ──
        egg = easter_eggs.match(original_text)
        if egg is not None:
            egg_text = egg["response"]
            egg_emotion = egg["emotion"]
            try:
                save_message(conversation_id, "assistant", egg_text, agent="jarvis")
            except Exception as e:
                logger.error("save easter egg : %s", e)
            await ws.send_json({
                "type": "response",
                "agent": "jarvis",
                "content": egg_text,
                "emotion": egg_emotion,
                "model": "easter-egg",
                "tokens_in": 0,
                "tokens_out": 0,
                "cost": 0.0,
            })
            if send_tts:
                await _send_tts_streaming(ws, egg_text, egg_emotion)
            return {"emotion": egg_emotion, "response": egg_text}

        # ── Vérifier si l'utilisateur confirme une proposition en attente ──
        pending_action = (
            dict(_pending_proposal["action"])
            if _pending_proposal
            and _pending_proposal.get("conversation_id") == conversation_id
            else None
        )
        pending_action_type = pending_action.get("type") if pending_action else None
        pending_result = await _check_pending_proposal(ws, content, conversation_id)
        if pending_result is not None:
            # L'utilisateur a dit "oui/vas-y" → on exécute l'action proposée
            await ws.send_json({
                "type": "action_result",
                "action": pending_action_type or "?",
                "action_payload": pending_action,
                "result": pending_result,
            })
            # 2e passe pour reformuler le résultat
            if pending_result.get("ok") and not pending_result.get("needs_confirmation"):
                fu_action = pending_action or {"type": pending_action_type or "unknown"}
                try:
                    payload = _format_action_result_for_followup(fu_action, pending_result)
                except Exception:
                    payload = str(pending_result)[:1000]
                fu = await orchestrator.handle(
                    (
                        f"Résultat de l'action exécutée :\n\n{payload}\n\n"
                        f"Question originale : {original_text}\n\n"
                        "Résume ce résultat pour l'utilisateur de façon concise."
                    ),
                    conversation_id=conversation_id,
                    voice_mode=voice_mode,
                )
                display_text = finalize_assistant_display_text(fu.get("response", ""))
                emotion = fu.get("emotion", "neutral")
                await ws.send_json({"type": "response_followup", "content": display_text})
            return {"emotion": emotion, "response": display_text or str(pending_result.get("message", ""))}

        full_response = ""
        final_meta: dict = {}
        emotion = "neutral"
        pending_done: dict | None = None
        stream_clean_sent = ""

        if stream:
            async for event in orchestrator.handle_stream(
                content, conversation_id=conversation_id, voice_mode=False, context=extra_context
            ):
                if event.get("type") == "done":
                    pending_done = event
                    final_meta = event
                    emotion = event.get("emotion", "neutral")
                    continue
                if event.get("type") == "chunk":
                    full_response += event["content"]
                    clean_now = sanitize_streaming_display(full_response)
                    delta = clean_now[len(stream_clean_sent):]
                    stream_clean_sent = clean_now
                    if delta:
                        await ws.send_json({"type": "chunk", "content": delta})
                    continue
                await ws.send_json(event)
        else:
            result = await orchestrator.handle(
                content, conversation_id=conversation_id, voice_mode=voice_mode,
                context=extra_context,
            )
            full_response = result["response"]
            emotion = result.get("emotion", "neutral")
            final_meta = result
            display_ns = finalize_assistant_display_text(full_response)
            await ws.send_json({
                "type": "response",
                "agent": result["agent"],
                "category": result.get("category"),
                "content": display_ns,
                "model": result["model"],
                "tokens_in": result["tokens_in"],
                "tokens_out": result["tokens_out"],
                "cost": result["cost"],
                "emotion": emotion,
            })

        raw_accumulated = full_response
        action, after_action = _extract_action_from_text(raw_accumulated)
        display_text = finalize_assistant_display_text(after_action)

        if stream:
            await ws.send_json({"type": "response_clean", "content": display_text or ""})
            if pending_done is not None:
                await ws.send_json(pending_done)
        elif display_text != full_response:
            await ws.send_json({"type": "response_clean", "content": display_text})

        action_result: dict | None = None
        if action:
            _schedule_llm_log(
                agent=str(final_meta.get("agent") or "orchestrator"),
                action_type=str(action.get("type") or "unknown"),
                payload={"conversation_id": conversation_id, "action": action},
                status="pending",
            )

            if _is_agentic_action(action):
                # Mode agent : boucle d'exécution multi-étapes
                agent_name = final_meta.get("agent", "orchestrator")
                agent = get_agent(agent_name) or orchestrator
                logger.info("[agentic] Démarrage boucle agentique pour %s", action.get("type"))

                await ws.send_json({
                    "type": "status",
                    "content": "Mode agent activé — exécution en cours…",
                })

                try:
                    loop_result = await agent._run_agentic_loop(
                        user_message=original_text,
                        conversation_id=conversation_id,
                        context=extra_context,
                        initial_action=action,
                    )
                except Exception as e:
                    logger.exception("[agentic] boucle : %s", e)
                    loop_result = {
                        "results": [{"step": 1, "action": action, "result": {"ok": False, "message": str(e)}}],
                        "step_count": 1,
                        "final_status": "failed",
                    }

                await ws.send_json({
                    "type": "agentic_result",
                    "steps": loop_result.get("step_count", 0),
                    "status": loop_result.get("final_status", "completed"),
                })

                # Synthèse finale des résultats
                results_text = "\n".join([
                    f"Étape {r['step']}: "
                    f"{str(r['result'].get('output', r['result'].get('message', '')))[:1000]}"
                    for r in loop_result.get("results", [])
                    if isinstance(r.get("step"), int)
                ])

                action_result = {
                    "ok": loop_result.get("final_status") != "failed",
                    "output": results_text,
                    "agentic": True,
                }

                if results_text:
                    fu = await orchestrator.handle(
                        (
                            f"Résultats des actions exécutées :\n\n{results_text}\n\n"
                            f"Question originale : {original_text}\n\n"
                            "Synthétise ces résultats de façon claire et utile."
                        ),
                        conversation_id=conversation_id,
                        voice_mode=voice_mode,
                    )
                    display_text = finalize_assistant_display_text(
                        fu.get("response", display_text)
                    )
                    emotion = fu.get("emotion", emotion)
                    final_meta = fu
                    await ws.send_json({
                        "type": "response_followup",
                        "content": display_text,
                    })
            else:
                # Mode simple : une action
                if _should_defer_action(display_text, action):
                    _maybe_store_pending_proposal(action, conversation_id)
                    action_result = {
                        "ok": True,
                        "deferred": True,
                        "message": display_text,
                    }
                    await ws.send_json({
                        "type": "action_pending",
                        "action": action,
                        "action_type": action.get("type"),
                        "message": display_text,
                    })
                    logger.info("[pending] Action différée (proposition utilisateur)")
                else:
                    if action.get("type") == "mail" and not action.get("confirmed"):
                        _maybe_store_pending_proposal(action, conversation_id)
                        logger.info("[pending] Proposition mail stockée pour confirmation")

                    try:
                        action_result = await execute_action(action)
                        await ws.send_json({
                            "type": "action_result",
                            "action": action.get("type"),
                            "action_payload": action,
                            "result": action_result,
                        })
                        if action_result.get("needs_confirmation"):
                            _maybe_store_pending_proposal(action, conversation_id)
                            logger.info(
                                "[pending] Action %s en attente de confirmation",
                                action.get("type"),
                            )
                        logger.info(
                            "[action] %s → ok=%s",
                            action.get("type"),
                            action_result.get("ok"),
                        )
                    except Exception as e:
                        logger.exception("[action] execute_action exception : %s", e)
                        action_result = {"ok": False, "message": str(e)}
                        await ws.send_json({
                            "type": "action_result",
                            "action": action.get("type"),
                            "action_payload": action,
                            "result": action_result,
                        })

                # 2e passe pour les actions avec followup
                if (
                    action_result
                    and not (action_result.get("deferred") or action_result.get("needs_confirmation"))
                    and action.get("type") in ACTIONS_WITH_FOLLOWUP
                    and action_result.get("ok")
                ):
                    try:
                        payload = _format_action_result_for_followup(
                            action, action_result
                        )
                        await ws.send_json({
                            "type": "status",
                            "content": "Synthèse du résultat…",
                        })
                        fu = await orchestrator.handle(
                            (
                                f"Résultat brut de l'action :\n\n{payload}\n\n"
                                f"Question originale : {original_text}\n\n"
                                "Résume ce résultat de façon claire et utile pour l'utilisateur. "
                                "Pas de bloc action."
                            ),
                            conversation_id=conversation_id,
                            voice_mode=voice_mode,
                        )
                        display_text = finalize_assistant_display_text(
                            fu.get("response", "")
                        )
                        emotion = fu.get("emotion", emotion)
                        final_meta = {
                            "agent": fu.get("agent", final_meta.get("agent")),
                            "model": fu.get("model", final_meta.get("model")),
                            "tokens_in": int(fu.get("tokens_in") or 0),
                            "tokens_out": int(fu.get("tokens_out") or 0),
                            "cost": float(fu.get("cost") or 0.0),
                        }
                        await ws.send_json({
                            "type": "response_followup",
                            "content": display_text,
                        })
                    except Exception as e:
                        logger.exception("[followup] action %s : %s", action.get("type"), e)

        if raw_accumulated:
            try:
                save_message(
                    conversation_id, "assistant", display_text,
                    agent=final_meta.get("agent"),
                    model=final_meta.get("model"),
                    tokens_in=final_meta.get("tokens_in", 0),
                    tokens_out=final_meta.get("tokens_out", 0),
                    cost=final_meta.get("cost", 0.0),
                )
            except Exception as e:
                logger.error("Erreur save assistant message : %s", e)

        try:
            update_conversation_activity(conversation_id)
        except Exception as e:
            logger.debug("[conv] update_activity assistant : %s", e)

        # Auto-titrage en background
        asyncio.create_task(_maybe_title_conversation(conversation_id))

        # Notifier le client que la conversation a été mise à jour
        try:
            conv_info = get_conversation_detail(conversation_id)
            if conv_info:
                await ws.send_json({
                    "type": "conversation_updated",
                    "conversation_id": conversation_id,
                    "title": conv_info.get("title"),
                    "message_count": conv_info.get("message_count", 0),
                })
        except Exception as e:
            logger.debug("[conv] conversation_updated event : %s", e)

        tts_text = display_text.strip() if display_text else ""
        if send_tts and tts_text:
            await _send_tts_streaming(ws, tts_text, emotion)

        return {"emotion": emotion, "response": display_text}
    except Exception as e:
        logger.exception("_process_message : %s", e)
        detail = f"{type(e).__name__}: {e}"[:200]
        try:
            await ws.send_json({
                "type": "error",
                "message": f"Erreur lors du traitement du message ({detail}).",
            })
        except Exception:
            pass
        return {"emotion": "neutral", "response": ""}


async def _handle_hands_free_blob(
    ws: WebSocket, audio_bytes: bytes, conv_session: dict,
) -> None:
    """Mains libres : STT → pipeline vocal rapide (`_process_voice_fast`) + TTS."""
    cid = conv_session["conversation_id"]
    conv_session["is_processing"] = True

    async def reset_listening(send_processing_done: bool = True):
        conv_session["is_processing"] = False
        conv_session["is_speaking"] = False
        if conv_session.get("active") and send_processing_done:
            await ws.send_json({"type": "listening"})

    try:
        await ws.send_json({"type": "processing"})

        if stt is None or not getattr(stt, "available", False):
            await ws.send_json({"type": "error", "message": "STT indisponible (ELEVENLABS_API_KEY manquante)."})
            await reset_listening()
            return

        if len(audio_bytes) < 1000:
            await reset_listening()
            return

        try:
            text = await stt.transcribe(audio_bytes, language=config.LANGUAGE)
        except Exception as e:
            logger.exception("STT mains libres : %s", e)
            await ws.send_json({"type": "error", "message": f"Transcription : {type(e).__name__}"})
            await reset_listening()
            return

        await ws.send_json({
            "type": "voice_debug",
            "blob_bytes": len(audio_bytes),
            "stt_raw": getattr(stt, "last_raw_text", "")[:220],
            "stt_clean": (text or "")[:220],
        })

        if not text or len(text.strip()) < 2:
            await reset_listening()
            return

        await ws.send_json({"type": "transcript", "content": text})

        conv_session["is_processing"] = False
        conv_session["is_speaking"] = True

        try:
            result = await _process_voice_fast(text, cid)
            display_text = finalize_assistant_display_text(result.get("text", ""))
            emotion = result.get("emotion", "neutral") or "neutral"
            await ws.send_json({
                "type": "response",
                "agent": "voice",
                "category": "VOICE",
                "content": display_text,
                "model": config.DEEPSEEK_FAST_MODEL,
                "tokens_in": 0,
                "tokens_out": 0,
                "cost": float(result.get("cost") or 0.0),
                "emotion": emotion,
            })
            await _send_tts_streaming(ws, display_text, emotion)
        except Exception as e:
            logger.exception("traitement message mains libres : %s", e)
            await ws.send_json({"type": "error", "message": f"Erreur agent : {type(e).__name__}"})
            conv_session["is_speaking"] = False
            await reset_listening()
            return

    except Exception as e:
        logger.exception("hands_free pipeline : %s", e)
        await reset_listening()


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    """Chat temps réel + mode conversation vocale continue.

    Accepte côté client :
    - JSON texte : `{type: "text"|"action_confirm"|"conversation_mode"|"done_playing", ...}`
    - Bytes bruts : audio enregistré au micro (webm/opus)

    Renvoie côté serveur :
    - Events streaming JSON (classification, chunk, done, saved_file, error)
    - `transcript` après STT
    - `speaking` avant envoi audio TTS (le client arrête le micro)
    - `listening` quand JARVIS a fini de parler (le client reprend le micro)
    - Bytes MP3 pour la réponse TTS
    """
    await ws.accept()
    logger.info("WS client connecté")
    connected_ws.add(ws)

    conversation_id = None
    conversation_mode = False  # ancien flux (conversation_audio + fragments)
    is_speaking = False       # chat / poussoir
    conv_audio_buffer: list[bytes] = []
    conv_session: dict | None = None   # mains libres (conversation_start)
    active_recording = None  # audio.continuous_recorder.ContinuousRecording | None

    try:
        conversation_id, resumed = _resume_or_create_conversation()
        _ws_last_session.update({"conversation_id": conversation_id, "closed_at": 0.0, "ws": ws})
        try:
            prev = get_last_conversation_summary()
            config.PRIOR_SESSION_SUMMARY = (prev or "").strip()
        except Exception as e:
            logger.exception("get_last_conversation_summary : %s", e)
            config.PRIOR_SESSION_SUMMARY = ""

        await ws.send_json({
            "type": "connected",
            "conversation_id": conversation_id,
            "user_name": config.USER_NAME,
            "resumed": resumed,
        })
        if not resumed:
            await _maybe_send_daily_welcome(ws)

        while True:
            packet = await ws.receive()

            if packet.get("type") == "websocket.disconnect":
                break

            # ── 1. Audio binaire ──────────────────────────────
            if "bytes" in packet and packet["bytes"] is not None:
                audio_bytes = packet["bytes"]

                if active_recording is not None and getattr(active_recording, "is_active", False):
                    active_recording.add_chunk(audio_bytes)
                    continue

                # Mains libres : un blob WebM complet par utterance (VAD navigateur)
                if conv_session and conv_session.get("active"):
                    if conv_session.get("is_speaking") or conv_session.get("is_processing"):
                        continue
                    await _handle_hands_free_blob(ws, audio_bytes, conv_session)
                    continue

                if is_speaking:
                    continue

                if conversation_mode:
                    conv_audio_buffer.append(audio_bytes)
                    continue

                # Poussoir (un blob)
                logger.info("Audio reçu poussoir : %d bytes", len(audio_bytes))

                if stt is None or not getattr(stt, "available", False):
                    await ws.send_json({
                        "type": "error",
                        "message": "STT indisponible (ELEVENLABS_API_KEY manquante).",
                    })
                    continue

                await ws.send_json({"type": "status", "content": "Transcription en cours…"})

                try:
                    text = await stt.transcribe(audio_bytes, language=config.LANGUAGE)
                except Exception as e:
                    logger.exception("Erreur STT : %s", e)
                    await ws.send_json({
                        "type": "error",
                        "message": f"Erreur transcription : {type(e).__name__}",
                    })
                    continue

                if not text or len(text) < 2:
                    await ws.send_json({
                        "type": "error",
                        "message": "Je n'ai pas compris, réessaie.",
                    })
                    continue

                await ws.send_json({"type": "transcript", "content": text})

                try:
                    await _process_message(
                        ws, text, conversation_id, voice_mode=True, stream=True, send_tts=True,
                    )
                    is_speaking = True  # jusqu'à done_playing (réponse vocale jouée)
                except Exception as e:
                    logger.exception("Erreur traitement message audio")
                    await ws.send_json({
                        "type": "error",
                        "message": f"Erreur agent : {type(e).__name__}: {e}",
                    })
                continue

            # ── 2. Message JSON texte ─────────────────────────
            if "text" in packet and packet["text"] is not None:
                raw = packet["text"]
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    await ws.send_json({"type": "error", "message": "JSON invalide"})
                    continue

                msg_type = msg.get("type", "text")

                if msg_type == "recording_start":
                    if stt is None or not getattr(stt, "available", False):
                        await ws.send_json({
                            "type": "error",
                            "message": "STT indisponible (ELEVENLABS_API_KEY manquante).",
                        })
                        continue
                    from audio.continuous_recorder import ContinuousRecording

                    label = str(msg.get("label") or "Enregistrement").strip()[:200]
                    active_recording = ContinuousRecording(conversation_id)
                    active_recording.label = label
                    active_recording.is_active = True
                    logger.info("[WS] Écoute continue — label=%s", label)
                    await ws.send_json({"type": "recording_started", "label": label})
                    continue

                if msg_type == "recording_stop":
                    if active_recording is None:
                        await ws.send_json({"type": "error", "message": "Aucun enregistrement en cours."})
                        continue
                    rec = active_recording
                    active_recording = None

                    async def _recording_progress(event: str, payload: dict) -> None:
                        await ws.send_json({"type": event, **payload})

                    await ws.send_json({"type": "recording_processing", "message": "Transcription en cours…"})
                    try:
                        result = await rec.stop_and_process(progress=_recording_progress)
                    except Exception as e:
                        logger.exception("[WS] recording_stop : %s", e)
                        await ws.send_json({
                            "type": "recording_done",
                            "result": {"ok": False, "error": str(e), "label": getattr(rec, "label", "")},
                        })
                        continue
                    await ws.send_json({"type": "recording_done", "result": result})
                    continue

                # ── Conversation mains libres (nouveau flux)
                if msg_type == "conversation_start":
                    conv_session = {
                        "active": True,
                        "conversation_id": create_conversation(agent="voice"),
                        "is_speaking": False,
                        "is_processing": False,
                    }
                    logger.info("[WS] Mains libres démarrées conv_id=%s", conv_session["conversation_id"])
                    await ws.send_json({
                        "type": "conversation_started",
                        "conversation_id": conv_session["conversation_id"],
                        "silence_duration_ms": config.VOICE_SILENCE_DURATION_MS,
                        "min_speech_ms": config.VOICE_MIN_SPEECH_MS,
                    })
                    await ws.send_json({"type": "listening"})
                    continue

                if msg_type == "conversation_stop":
                    if conv_session:
                        try:
                            end_conversation(conv_session["conversation_id"])
                        except Exception as e:
                            logger.error("end_conversation voice : %s", e)
                    conv_session = None
                    await ws.send_json({"type": "conversation_stopped"})
                    continue

                if msg_type == "conversation_mode":
                    conversation_mode = bool(msg.get("enabled", False))
                    conv_audio_buffer.clear()
                    is_speaking = False
                    await ws.send_json({
                        "type": "conversation_mode",
                        "enabled": conversation_mode,
                    })
                    if conversation_mode:
                        await ws.send_json({"type": "listening"})
                        logger.info("[WS] Mode conversation (legacy) activé")
                    else:
                        logger.info("[WS] Mode conversation (legacy) désactivé")
                    continue

                if msg_type == "done_playing":
                    is_speaking = False
                    if conv_session and conv_session.get("active"):
                        conv_session["is_speaking"] = False
                        await ws.send_json({"type": "listening"})
                        continue
                    if conversation_mode:
                        conv_audio_buffer.clear()
                        await ws.send_json({"type": "listening"})
                    continue

                if msg_type == "conversation_audio":
                    if is_speaking:
                        continue

                    audio_data = b"".join(conv_audio_buffer) if conv_audio_buffer else b""
                    conv_audio_buffer.clear()

                    if not audio_data:
                        if conversation_mode:
                            await ws.send_json({"type": "listening"})
                        continue

                    if stt is None or not getattr(stt, "available", False):
                        await ws.send_json({
                            "type": "error",
                            "message": "STT indisponible (ELEVENLABS_API_KEY manquante).",
                        })
                        if conversation_mode:
                            await ws.send_json({"type": "listening"})
                        continue

                    await ws.send_json({"type": "processing"})

                    try:
                        text = await stt.transcribe(audio_data, language=config.LANGUAGE)
                    except Exception as e:
                        logger.exception("Erreur STT conversation : %s", e)
                        await ws.send_json({
                            "type": "error",
                            "message": f"Transcription : {type(e).__name__}",
                        })
                        if conversation_mode:
                            await ws.send_json({"type": "listening"})
                        continue

                    if not text or len(text) < 2:
                        if conversation_mode:
                            await ws.send_json({"type": "listening"})
                        continue

                    await ws.send_json({"type": "transcript", "content": text})

                    try:
                        await _process_message(
                            ws, text, conversation_id, voice_mode=True, stream=True, send_tts=True,
                        )
                        is_speaking = True
                    except Exception as e:
                        logger.exception("Erreur conversation audio : %s", e)
                        await ws.send_json({
                            "type": "error",
                            "message": f"Erreur : {type(e).__name__}",
                        })
                        is_speaking = False
                        if conversation_mode:
                            await ws.send_json({"type": "listening"})
                    continue

                if msg_type == "action_confirm":
                    act = msg.get("action")
                    if not isinstance(act, dict) or not act.get("type"):
                        await ws.send_json({"type": "error", "message": "action invalide"})
                        continue
                    act = {**act, "confirmed": True}
                    _schedule_llm_log(
                        agent="orchestrator",
                        action_type=str(act.get("type") or "unknown"),
                        payload={"conversation_id": conversation_id, "action": act, "confirmed": True},
                        status="pending",
                    )
                    try:
                        res = await execute_action(act)
                    except Exception as e:
                        logger.exception("action_confirm : %s", e)
                        await ws.send_json({
                            "type": "action_result",
                            "action": act.get("type"),
                            "result": {"ok": False, "message": str(e)},
                        })
                        continue
                    await ws.send_json({
                        "type": "action_result",
                        "action": act.get("type"),
                        "action_payload": act,
                        "result": res,
                    })
                    if (
                        res.get("ok")
                        and act.get("type") in ACTIONS_WITH_FOLLOWUP
                        and not res.get("needs_confirmation")
                    ):
                        try:
                            payload = _format_action_result_for_followup(act, res)
                            await ws.send_json({"type": "status", "content": "Synthèse du résultat…"})
                            fu = await orchestrator.handle(
                                (
                                    f"Résultat brut de l'action :\n\n{payload}\n\n"
                                    "L'utilisateur a confirmé l'exécution. Résume le résultat de façon claire. "
                                    "Pas de bloc action."
                                ),
                                conversation_id=conversation_id,
                                voice_mode=False,
                            )
                            txt = finalize_assistant_display_text(fu.get("response", ""))
                            await ws.send_json({"type": "response_followup", "content": txt})
                            try:
                                save_message(
                                    conversation_id, "assistant", txt,
                                    agent=fu.get("agent"),
                                    model=fu.get("model"),
                                    tokens_in=int(fu.get("tokens_in") or 0),
                                    tokens_out=int(fu.get("tokens_out") or 0),
                                    cost=float(fu.get("cost") or 0.0),
                                )
                            except Exception as e:
                                logger.error("save followup action_confirm : %s", e)
                        except Exception as e:
                            logger.exception("[action_confirm] followup : %s", e)
                    continue

                if msg_type == "new_conversation":
                    try:
                        old_id = conversation_id
                        conversation_id = create_conversation(agent="orchestrator")
                        await ws.send_json({
                            "type": "conversation_switched",
                            "conversation_id": conversation_id,
                            "title": None,
                        })
                        logger.info("[ws] new_conversation #%d (remplace #%s)", conversation_id, old_id)
                    except Exception as e:
                        logger.exception("[ws] new_conversation : %s", e)
                        await ws.send_json({"type": "error", "message": f"Impossible de créer la conversation : {e}"})
                    continue

                if msg_type == "switch_conversation":
                    target_id = msg.get("conversation_id")
                    if not isinstance(target_id, int):
                        await ws.send_json({"type": "error", "message": "conversation_id manquant"})
                        continue
                    try:
                        conv = get_conversation_detail(target_id)
                        if not conv:
                            await ws.send_json({"type": "error", "message": f"Conversation #{target_id} introuvable"})
                            continue
                        conversation_id = target_id
                        await ws.send_json({
                            "type": "conversation_switched",
                            "conversation_id": conversation_id,
                            "title": conv.get("title"),
                        })
                        logger.info("[ws] switch_conversation → #%d", conversation_id)
                    except Exception as e:
                        logger.exception("[ws] switch_conversation : %s", e)
                        await ws.send_json({"type": "error", "message": f"Switch échoué : {e}"})
                    continue

                if msg_type == "loop":
                    task = (msg.get("task") or msg.get("content") or "").strip()
                    if not task:
                        await ws.send_json({
                            "type": "error",
                            "message": "Usage : { \"type\": \"loop\", \"task\": \"…\" }",
                        })
                        continue
                    try:
                        save_message(conversation_id, "user", f"/loop {task}")
                    except Exception as e:
                        logger.debug("[ws] loop save user : %s", e)
                    try:
                        await _run_loop_mode_ws(
                            ws, task, conversation_id, voice_mode=bool(msg.get("voice_mode")),
                        )
                    except Exception:
                        logger.exception("[ws] loop mode")
                        await ws.send_json({"type": "error", "message": "Erreur mode autonome"})
                    continue

                # Message texte classique
                content = (msg.get("content") or "").strip()
                stream = bool(msg.get("stream", True))
                tts_flag = bool(msg.get("tts", False))

                if msg_type != "text" or not content:
                    await ws.send_json({
                        "type": "error",
                        "message": "Message vide ou type non supporté",
                    })
                    continue

                try:
                    await _process_message(
                        ws, content, conversation_id, voice_mode=False, stream=stream, send_tts=tts_flag,
                    )
                    if tts_flag:
                        is_speaking = True
                except Exception:
                    logger.exception("Erreur lors du traitement message texte")
                    await ws.send_json({
                        "type": "error",
                        "message": "Erreur agent",
                    })

    except WebSocketDisconnect:
        logger.info("WS client déconnecté")
    except Exception as e:
        logger.exception("Erreur WS : %s", e)
    finally:
        connected_ws.discard(ws)
        # Fenêtre de grâce : une reconnexion rapide reprendra cette conversation.
        if conversation_id:
            import time as _time

            _ws_last_session["conversation_id"] = conversation_id
            _ws_last_session["closed_at"] = _time.time()
        if conv_session:
            try:
                end_conversation(conv_session["conversation_id"])
            except Exception as e:
                logger.error("Erreur end_conversation voice : %s", e)
            conv_session = None
        if conversation_id:
            try:
                history = get_conversation_history(conversation_id, limit=5)
                if len(history) > 2:
                    asyncio.create_task(_run_memory_in_background(conversation_id))
            except Exception as e:
                logger.error(f"Erreur memory background trigger : {e}")
            try:
                end_conversation(conversation_id)
            except Exception as e:
                logger.error(f"Erreur end_conversation : {e}")


async def _run_memory_in_background(conversation_id: int) -> None:
    """Traite la conversation par l'agent mémoire — silencieux côté UX."""
    try:
        applied = await memory_agent.process_conversation(conversation_id)
        if applied:
            logger.info(f"[memory bg] conv {conversation_id} → {applied}")
    except Exception as e:
        logger.error(f"[memory bg] conv {conversation_id} : {e}")


_setup_frontend(app)


# ── Entry point ─────────────────────────────────────────────


def main():
    """Lance Uvicorn.

    HTTPS activé uniquement si :
      - `WEB_HTTPS=true` dans .env
      - ET les fichiers `certs/cert.pem` + `certs/key.pem` existent.

    Sinon → HTTP. Ce mode est requis pour que le proxy server-side du PWA
    (Next.js rewrites) puisse joindre le backend sans erreur SSL self-signed.
    """
    from pathlib import Path as _Path

    _base = _Path(__file__).resolve().parent
    _cert = _base / "certs" / "cert.pem"
    _key  = _base / "certs" / "key.pem"
    _ssl  = config.WEB_HTTPS and _cert.exists() and _key.exists()

    _proto = "https" if _ssl else "http"
    if config.WEB_HTTPS and not _ssl:
        logger.warning("[uvicorn] WEB_HTTPS=true mais certs/cert.pem ou certs/key.pem manquants — fallback HTTP")
    logger.info(
        "[uvicorn] %s://0.0.0.0:%d%s",
        _proto,
        config.WEB_PORT,
        " (SSL activé)" if _ssl else " (HTTP — accès local + proxy PWA)",
    )

    _kwargs: dict = dict(
        host=config.WEB_HOST,
        port=config.WEB_PORT,
        reload=False,
        log_level="info",
    )
    if _ssl:
        _kwargs["ssl_certfile"] = str(_cert)
        _kwargs["ssl_keyfile"]  = str(_key)

    uvicorn.run("main:app", **_kwargs)


if __name__ == "__main__":
    main()
