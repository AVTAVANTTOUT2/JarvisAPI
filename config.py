"""Configuration centralisée JARVIS — charge .env et expose les settings."""

import os
import socket
from pathlib import Path
from dotenv import load_dotenv

# Charge .env depuis la racine du projet
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


def _get(key: str, default: str = "") -> str:
    return os.getenv(key, default)


# ── DeepSeek API ──────────────────────────────────────────────
DEEPSEEK_API_KEY = _get("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = _get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_FAST_MODEL = _get("DEEPSEEK_FAST_MODEL", "deepseek-v4-flash")
DEEPSEEK_MAIN_MODEL = _get("DEEPSEEK_MAIN_MODEL", "deepseek-v4-pro")

# ── Audio — ElevenLabs (STT Scribe + TTS) ────────────────────
ELEVENLABS_API_KEY = _get("ELEVENLABS_API_KEY")
ELEVENLABS_VOICE_ID = _get("ELEVENLABS_VOICE_ID")

TTS_ENGINE = _get("TTS_ENGINE", "edge")
TTS_VOICE = _get("TTS_VOICE", "fr-FR-VivienneMultilingualNeural")
KOKORO_VOICE = _get("KOKORO_VOICE", "af_nicole")
KOKORO_LANG = _get("KOKORO_LANG", "fr-fr")
WAKE_WORD = _get("WAKE_WORD", "jarvis")

# Mode conversation mains libres (client : détection silence ; valeurs envoyées dans le status HTTP)
VOICE_SILENCE_DURATION_MS = int(_get("VOICE_SILENCE_DURATION_MS", "1200"))
VOICE_MIN_SPEECH_MS = int(_get("VOICE_MIN_SPEECH_MS", "400"))
VOICE_MAX_TOKENS = int(_get("VOICE_MAX_TOKENS", "500"))

# Localisation (GPS / lieux nommés)
LOCATION_TRACKING = _get("LOCATION_TRACKING", "true").lower() == "true"
LOCATION_PLACE_RADIUS = int(_get("LOCATION_PLACE_RADIUS", "100"))

# Mode écoute continue (enregistrement long → transcription → synthèse)
RECORDING_MAX_DURATION_MIN = int(_get("RECORDING_MAX_DURATION_MIN", "180"))  # refus au-delà
RECORDING_CHUNK_SIZE_MB = int(_get("RECORDING_CHUNK_SIZE_MB", "20"))        # taille max par requête Scribe
RECORDING_SUMMARY_ONLY = _get("RECORDING_SUMMARY_ONLY", "false").lower() == "true"  # n’inclut pas la transcription dans les réponses API/liste

# ── Intégrations ────────────────────────────────────────────
# Mail / Calendar : Apple Mail + Calendar.app en AppleScript — aucune OAuth.
WEATHER_API_KEY = _get("WEATHER_API_KEY")
WEATHER_CITY = _get("WEATHER_CITY", "Lille")
TAVILY_API_KEY = _get("TAVILY_API_KEY")

# ── iMessage bridge (macOS uniquement) ──────────────────────
# Polling de ~/Library/Messages/chat.db + envoi via osascript.
# Nécessite : Full Disk Access (lecture chat.db) + Automation (Messages.app).
IMESSAGE_TARGET = _get("IMESSAGE_TARGET", "")            # numéro ou email iMessage
IMESSAGE_POLLING_INTERVAL = float(_get("IMESSAGE_POLLING_INTERVAL", "3.0"))
IMESSAGE_PREFIX = _get("IMESSAGE_PREFIX", "")            # vide = traite tout
                                                          # défini = traite seulement les msgs commençant par ce mot

# ── iMessage — sourcing (lecture) et envoi (séparés, jamais couplés) ──
IMESSAGE_SOURCING_ENABLED = _get("IMESSAGE_SOURCING_ENABLED", "true").lower() == "true"
IMESSAGE_SEND_ENABLED = _get("IMESSAGE_SEND_ENABLED", "false").lower() == "true"
IMESSAGE_SCAN_INTERVAL = int(_get("IMESSAGE_SCAN_INTERVAL", "300"))  # secondes entre 2 scans (défaut 5min)

# ── Système ─────────────────────────────────────────────────
DB_PATH = _get("DB_PATH", "./data/jarvis.db")
UPLOAD_DIR = _get("UPLOAD_DIR", "./data/uploads")
SCHOOL_OUTPUT_DIR = _get("SCHOOL_OUTPUT_DIR", "./data/outputs/school")
DEV_PROJECTS_ROOT = _get("DEV_PROJECTS_ROOT", str(BASE_DIR / "dev_projects"))
DEVAGENT_EXEC_TIMEOUT = int(_get("DEVAGENT_EXEC_TIMEOUT", "120"))
LANGUAGE = _get("LANGUAGE", "fr")
TIMEZONE = _get("TIMEZONE", "Europe/Paris")
USER_NAME = _get("USER_NAME", "Nolann")
WEB_PORT = int(_get("WEB_PORT", "8080"))
# "0.0.0.0" = toutes les interfaces IPv4. "127.0.0.1" = machine locale uniquement.
# "::" = IPv6 seul sur certains OS (peut casser http://127.0.0.1:PORT) — à n’utiliser que si tu sais pourquoi.
WEB_HOST = _get("WEB_HOST", "0.0.0.0")
# HTTPS optionnel : si false, ignore les certs et démarre en HTTP.
# Utile pour le proxy server-side du PWA (Next.js refuse les certs self-signed).
# Pour l'accès direct iPhone via Tailscale → mettre WEB_HTTPS=true + certs/cert.pem.
WEB_HTTPS = _get("WEB_HTTPS", "false").lower() == "true"

# ── Contrôle ordinateur local (macOS) ────────────────────────
COMPUTER_ACCESS = _get("COMPUTER_ACCESS", "true")
COMPUTER_SHELL = _get("COMPUTER_SHELL", "/bin/zsh")
COMPUTER_TIMEOUT = int(_get("COMPUTER_TIMEOUT", "30"))
# TV contrôle ADB
TV_IP = _get("TV_IP", "192.168.3.82")
TV_ADB_PORT = _get("TV_ADB_PORT", "5555")

# ── Exécution de code avancée ────────────────────────────────
CODE_EXECUTOR_ENABLED = _get("CODE_EXECUTOR_ENABLED", "true").lower() == "true"
CODE_EXECUTOR_TIMEOUT = int(_get("CODE_EXECUTOR_TIMEOUT", "120"))
CODE_EXECUTOR_MODEL = _get("CODE_EXECUTOR_MODEL", "") or DEEPSEEK_MAIN_MODEL

# Notifications bureau macOS (`display notification`)
DESKTOP_NOTIFICATIONS = _get("DESKTOP_NOTIFICATIONS", "true").lower() == "true"
NOTIFICATION_SOUND = _get("NOTIFICATION_SOUND", "Glass")

# Résumé de la dernière conversation terminée — injecté dans le contexte mémoire à la reconnexion WS.
PRIOR_SESSION_SUMMARY: str = ""

# ── MLX local model (package jarvis/) ──────────────────────
JARVIS_LOCAL_MODEL = _get("JARVIS_LOCAL_MODEL", "mlx-community/Qwen3-30B-A3B-4bit")
JARVIS_VENV = _get("JARVIS_VENV", os.path.expanduser("~/mlx-env"))

# ── Tâches lourdes (production longue) ──────────────────────
# Les productions longues (exercices complets, dissertations, code, rapports,
# fichiers, flashcards en masse) restent sur DEEPSEEK_MAIN_MODEL mais avec un
# plafond de tokens élevé. Détection via llm.classify_task_type().
HEAVY_TASK_MAX_TOKENS = int(_get("HEAVY_TASK_MAX_TOKENS", "8192"))

# ── Briefings ───────────────────────────────────────────────
MORNING_BRIEFING_TIME = _get("MORNING_BRIEFING_TIME", "07:30")
EVENING_SUMMARY_TIME = _get("EVENING_SUMMARY_TIME", "22:00")

# ── Surveillance email proactive ────────────────────────────
# Intervalle (en secondes) entre chaque check des nouveaux emails par
# `scripts/email_watcher.py`. Le watcher analyse chaque mail non lu via
# Haiku (~$0.001/email) et crée des tâches/rappels/notifications auto.
EMAIL_CHECK_INTERVAL = float(_get("EMAIL_CHECK_INTERVAL", "120"))

# ── Daemon JARVIS (sentinelle permanente) ───────────────────
# Le daemon tourne en parallèle du serveur web : screen watcher,
# notifications proactives, wake word, TTS local.
DAEMON_ENABLED = _get("DAEMON_ENABLED", "true").lower() == "true"
SCREEN_WATCHER_ENABLED = _get("SCREEN_WATCHER_ENABLED", "true").lower() == "true"
SCREEN_WATCHER_INTERVAL = int(_get("SCREEN_WATCHER_INTERVAL", "12"))      # secondes
SCREEN_CHANGE_THRESHOLD = float(_get("SCREEN_CHANGE_THRESHOLD", "5"))     # % minimum
SCREEN_ANALYSIS_THRESHOLD = float(_get("SCREEN_ANALYSIS_THRESHOLD", "15"))  # % pour LLM
SCREEN_RESIZE_WIDTH = int(_get("SCREEN_RESIZE_WIDTH", "1280"))
SCREEN_RESIZE_HEIGHT = int(_get("SCREEN_RESIZE_HEIGHT", "800"))
SCREEN_RESIZE: tuple[int, int] = (SCREEN_RESIZE_WIDTH, SCREEN_RESIZE_HEIGHT)
SCREEN_MAX_ANALYSIS_WIDTH = int(_get("SCREEN_MAX_ANALYSIS_WIDTH", "1280"))
SCREEN_JPEG_QUALITY = int(_get("SCREEN_JPEG_QUALITY", "70"))
SCREEN_VISION_MODEL = _get("SCREEN_VISION_MODEL", "qwen2.5vl:7b")
TRIAGE_MODEL = _get("TRIAGE_MODEL", "qwen2.5:7b")
OLLAMA_URL = _get("OLLAMA_URL", "http://localhost:11434")

# Identité de la machine — sert pour register_device + screen_watcher
DEVICE_ID = _get("DEVICE_ID", socket.gethostname())
DEVICE_NAME = _get("DEVICE_NAME", "Mac Mini")

# Wake word "Jarvis" via Porcupine (Picovoice — gratuit usage perso)
WAKE_WORD_ENABLED = _get("WAKE_WORD_ENABLED", "false").lower() == "true"
PORCUPINE_ACCESS_KEY = _get("PORCUPINE_ACCESS_KEY", "")

# Anti-spam vocal en mode veille : minimum N secondes entre deux notifs voix
DAEMON_TTS_COOLDOWN = int(_get("DAEMON_TTS_COOLDOWN", "30"))

# Phrases de fin de conversation vocale (union audio_daemon + jarvis_daemon)
END_PHRASES: tuple[str, ...] = (
    "merci jarvis", "c'est bon jarvis", "c'est tout jarvis",
    "merci c'est bon", "c'est fini", "bonne nuit jarvis",
    "a plus jarvis", "à plus jarvis", "ok merci", "au revoir", "stop",
    "arrête", "arrête-toi",
)

# ── Audio Daemon (micro natif Mac Mini — wake word + conversation mains libres) ──
AUDIO_DAEMON_ENABLED = _get("AUDIO_DAEMON_ENABLED", "false").lower() == "true"
AUDIO_DAEMON_SAMPLE_RATE = int(_get("AUDIO_DAEMON_SAMPLE_RATE", "16000"))
AUDIO_DAEMON_SPEECH_THRESHOLD = float(_get("AUDIO_DAEMON_SPEECH_THRESHOLD", "0.02"))
AUDIO_DAEMON_SILENCE_MS = int(_get("AUDIO_DAEMON_SILENCE_MS", "1200"))
AUDIO_DAEMON_MIN_SPEECH_MS = int(_get("AUDIO_DAEMON_MIN_SPEECH_MS", "600"))
AUDIO_DAEMON_MAX_UTTERANCE_S = int(_get("AUDIO_DAEMON_MAX_UTTERANCE_S", "15"))
AUDIO_DAEMON_CONVERSATION_TIMEOUT = float(_get("AUDIO_DAEMON_CONVERSATION_TIMEOUT", "15.0"))
AUDIO_DAEMON_INPUT_DEVICE = _get("AUDIO_DAEMON_INPUT_DEVICE", "")  # vide = auto Blue Snowball sinon defaut systeme
AUDIO_DAEMON_WAKE_SOUND = _get("AUDIO_DAEMON_WAKE_SOUND", "true").lower() == "true"
AUDIO_DAEMON_STT_ENGINE = _get("AUDIO_DAEMON_STT_ENGINE", "").strip().lower()  # "local" pour faster-whisper, "" = ElevenLabs Scribe
AUDIO_DAEMON_STT_MODEL = _get("AUDIO_DAEMON_STT_MODEL", "small")  # small (244Mo, bon FR) | base (142Mo) | tiny (75Mo)

# ── VAD (Voice Activity Detection) ────────────────────────────
SILERO_VAD_THRESHOLD = float(_get("SILERO_VAD_THRESHOLD", "0.5"))  # 0.3=tres sensible, 0.5=defaut, 0.7=strict

# ── Mode autonome /loop (DeepSeek sans limite configurable) ──
LOOP_UNLIMITED = _get("LOOP_UNLIMITED", "true").lower() == "true"
LOOP_MAX_STEPS = int(_get("LOOP_MAX_STEPS", "0"))  # 0 = illimité (garde-fou technique 500)
LOOP_MAX_OUTPUT_CHARS = int(_get("LOOP_MAX_OUTPUT_CHARS", "0"))  # 0 = illimité
LOOP_MAX_LLM_CALLS = int(_get("LOOP_MAX_LLM_CALLS", "0"))  # 0 = illimité
LOOP_MAX_TOKENS = int(_get("LOOP_MAX_TOKENS", "1024"))
LOOP_MAX_CONSECUTIVE_FAILURES = int(_get("LOOP_MAX_CONSECUTIVE_FAILURES", "3"))
LOOP_MODEL = _get("LOOP_MODEL", "") or DEEPSEEK_MAIN_MODEL
LOOP_DECISION_MODEL = _get("LOOP_DECISION_MODEL", "") or DEEPSEEK_FAST_MODEL

# ── Fiabilité — sauvegardes, rétention, budget LLM, heures calmes ──
BACKUP_ENABLED = _get("BACKUP_ENABLED", "true").lower() == "true"
BACKUP_DIR = _get("BACKUP_DIR", "./data/backups")
BACKUP_KEEP = int(_get("BACKUP_KEEP", "7"))            # nb de sauvegardes conservées

# Rétention des tables volumineuses (jours). 0 = conserver indéfiniment.
RETENTION_SCREEN_DAYS = int(_get("RETENTION_SCREEN_DAYS", "30"))
RETENTION_LOCATION_DAYS = int(_get("RETENTION_LOCATION_DAYS", "90"))
RETENTION_NOTIF_READ_DAYS = int(_get("RETENTION_NOTIF_READ_DAYS", "60"))
RETENTION_LLM_LOGS_DAYS = int(_get("RETENTION_LLM_LOGS_DAYS", "90"))

# Budget LLM mensuel en dollars. 0 = pas d'alerte.
LLM_BUDGET_MONTHLY = float(_get("LLM_BUDGET_MONTHLY", "20"))
LLM_BUDGET_ALERT_PCT = int(_get("LLM_BUDGET_ALERT_PCT", "80"))

# Heures calmes : pas de TTS daemon ni d'iMessage proactif dans la plage.
# Format "HH:MM" ; les deux vides = désactivé. Gère les plages nocturnes
# (23:30 → 07:00). Les notifications restent enregistrées en base.
QUIET_HOURS_START = _get("QUIET_HOURS_START", "")
QUIET_HOURS_END = _get("QUIET_HOURS_END", "")


def is_quiet_hours(now=None) -> bool:
    """True si l'heure courante tombe dans la plage d'heures calmes."""
    import datetime as _dt

    if not QUIET_HOURS_START or not QUIET_HOURS_END:
        return False
    try:
        sh, sm = (int(x) for x in QUIET_HOURS_START.split(":"))
        eh, em = (int(x) for x in QUIET_HOURS_END.split(":"))
    except (ValueError, AttributeError):
        return False
    now = now or _dt.datetime.now()
    cur = now.hour * 60 + now.minute
    start = sh * 60 + sm
    end = eh * 60 + em
    if start == end:
        return False
    if start < end:                     # plage diurne (13:00 → 14:00)
        return start <= cur < end
    return cur >= start or cur < end    # plage nocturne (23:30 → 07:00)


# ── Rituels quotidiens (roast, debrief, citation, anniversaires) ──
RITUALS_ENABLED = _get("RITUALS_ENABLED", "true").lower() == "true"
ROAST_TIME = _get("ROAST_TIME", "18:30")            # critique sèche des tâches non faites
DEBRIEF_TIME = _get("DEBRIEF_TIME", "21:45")        # bilan de journée, ton concerned
QUOTE_TIME = _get("QUOTE_TIME", "07:00")            # citation ironique du jour
BIRTHDAY_CHECK_TIME = _get("BIRTHDAY_CHECK_TIME", "08:00")
RITUALS_TTS = _get("RITUALS_TTS", "true").lower() == "true"  # roast/debrief parlés via daemon

# ── Debrief hebdo vocal + mood tracking discret ──────────────
WEEKLY_DEBRIEF_TIME = _get("WEEKLY_DEBRIEF_TIME", "21:00")   # dimanche soir
MOOD_SIGNAL_TIME = _get("MOOD_SIGNAL_TIME", "23:15")         # calcul du signal quotidien

# ── Présence au bureau (détection par le son, micro daemon audio) ──
# Arrivée : un son dépasse PRESENCE_NOISE_RMS → « Vous êtes là, Monsieur. »
# Départ : aucun son pendant PRESENCE_TIMEOUT_MIN minutes.
PRESENCE_ENABLED = _get("PRESENCE_ENABLED", "true").lower() == "true"
PRESENCE_TIMEOUT_MIN = int(_get("PRESENCE_TIMEOUT_MIN", "60"))
PRESENCE_NOISE_RMS = float(_get("PRESENCE_NOISE_RMS", "0.015"))  # < seuil parole (0.02)
PRESENCE_GREETING = _get("PRESENCE_GREETING", "Vous êtes là, Monsieur. Bon retour.")

# ── Alerte pause café (écran sans interruption) ──────────────
BREAK_ALERT_MINUTES = int(_get("BREAK_ALERT_MINUTES", "90"))   # durée continue avant alerte ; 0 = off
BREAK_GAP_MINUTES = int(_get("BREAK_GAP_MINUTES", "15"))       # trou considéré comme une pause
BREAK_COOLDOWN_MINUTES = int(_get("BREAK_COOLDOWN_MINUTES", "90"))

# ── Mapping modèles par agent ───────────────────────────────
AGENT_MODELS = {
    "orchestrator": DEEPSEEK_FAST_MODEL,
    "school": DEEPSEEK_MAIN_MODEL,
    "productivity_triage": DEEPSEEK_FAST_MODEL,
    "productivity_draft": DEEPSEEK_MAIN_MODEL,
    "coach": DEEPSEEK_MAIN_MODEL,
    "coach_deep": DEEPSEEK_MAIN_MODEL,  # DeepSeek v4 suffit pour l'escalade
    "info": DEEPSEEK_FAST_MODEL,
    "journal": DEEPSEEK_MAIN_MODEL,
    "memory": DEEPSEEK_FAST_MODEL,
}
