"""Configuration centralisée JARVIS — charge .env.config + .env."""

import logging
import os
import socket
from pathlib import Path

from env_loader import load_jarvis_env

logger = logging.getLogger(__name__)

# Charge d'abord la configuration, puis les secrets qui peuvent la surcharger.
BASE_DIR = Path(__file__).resolve().parent
load_jarvis_env()


def _get(key: str, default: str = "") -> str:
    return os.getenv(key, default)


def _config_path(key: str, default: str) -> Path:
    """Résout un chemin de configuration relativement au dépôt."""
    value = Path(os.path.expanduser(_get(key, default) or default))
    return value if value.is_absolute() else BASE_DIR / value


def _positive_float(key: str, default: float) -> float:
    """Lit un délai strictement positif ; toute saisie invalide retombe au défaut.

    Un `.env` mal renseigné ne doit pas empêcher le backend de démarrer, et un
    délai nul ou négatif désarmerait la borne qu'il est censé poser.
    """
    raw = _get(key, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _positive_int(key: str, default: int) -> int:
    """Variante entière de `_positive_float` (bornes attendues en secondes pleines)."""
    value = int(_positive_float(key, float(default)))
    return value if value > 0 else default


def _normalize_deepseek_base_url(url: str) -> str:
    """Retourne l'origine API ; llm.py ajoute déjà /v1/chat/completions."""
    base = (url or "https://api.deepseek.com").strip().rstrip("/")
    if base.endswith("/v1"):
        return base[:-3]
    return base


# ── DeepSeek API ──────────────────────────────────────────────
DEEPSEEK_API_KEY = _get("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = _normalize_deepseek_base_url(
    _get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
)
DEEPSEEK_FAST_MODEL = _get("DEEPSEEK_FAST_MODEL", "deepseek-v4-flash")
DEEPSEEK_MAIN_MODEL = _get("DEEPSEEK_MAIN_MODEL", "deepseek-v4-pro")
# Alias cognitifs (évite les doublons sémantiques tout en restant surchargeables)
VOICE_REASONING_MODEL = _get("VOICE_REASONING_MODEL", "") or DEEPSEEK_FAST_MODEL
MAIN_REASONING_MODEL = _get("MAIN_REASONING_MODEL", "") or DEEPSEEK_MAIN_MODEL


class ConfigurationError(RuntimeError):
    """Configuration obligatoire absente ou laissée à sa valeur d'exemple."""


def validate_required_runtime_config() -> None:
    """Refuse de démarrer un backend incapable de servir les fonctions LLM."""
    api_key = (DEEPSEEK_API_KEY or "").strip()
    if not api_key or api_key == "sk-...":
        raise ConfigurationError(
            "DEEPSEEK_API_KEY est obligatoire. Configure-la dans .env avant de démarrer JARVIS."
        )


# ── Audio — STT local (faster-whisper) + TTS local (Qwen3-TTS) ──
DEFAULT_STT_ENGINE = "faster-whisper"
# Mesure reproductible M4 (3 phrases FR, 1,4–3,9 s, deux passages chauds) :
# small médiane 706 ms / similarité 0,894 ; large-v3-turbo 2 259 ms / 0,909.
# Le petit modèle tient le budget temps réel. Le turbo ne s'exécute que lorsque
# la confiance du premier passage est insuffisante.
DEFAULT_STT_MODEL = "small"
DEFAULT_STT_FALLBACK_MODEL = "large-v3-turbo"
DEFAULT_STT_LANGUAGE = "fr"
DEFAULT_STT_DEVICE = "auto"
# Mesuré sur Apple Silicon (large-v3-turbo, 2,66 s de parole FR, CPU) :
#   auto → int8   4609 ms   |   float32   2361 ms
# CTranslate2 n'a pas de noyau int8 accéléré ici : la quantification ajoute une
# déquantification par couche au lieu d'économiser du calcul. « auto » choisit
# pourtant int8, ce qui doublait le temps de transcription. On fixe float32.
DEFAULT_STT_COMPUTE_TYPE = "float32"
DEFAULT_STT_BEAM_SIZE = 1
DEFAULT_STT_VAD_FILTER = False
# Campagne locale du 5 août 2026 : huit phrases FR connues. Le seuil -0,30
# relisait une transcription correcte à 94 % (logprob=-0,3244). À -0,35, les
# six passages corrects restent sur `small` et les deux passages réellement
# dégradés (-0,4378 et -0,6470) sont améliorés par le modèle qualité.
DEFAULT_STT_QUALITY_FALLBACK_LOGPROB = -0.35
# Les segments très courts manquent de contexte pour qu'une seconde passe
# lourde apporte un gain fiable. Surtout, charger le modèle qualité pour un
# fragment de moins d'une seconde peut bloquer toute la boucle vocale pendant
# plusieurs dizaines de secondes.
DEFAULT_STT_QUALITY_FALLBACK_MIN_SPEECH_MS = 1200

# ── Segmentation VAD du daemon audio ────────────────────────
# Ces trois valeurs déterminent le délai entre la dernière syllabe et le
# démarrage du STT. Elles sont versionnées ici — et non laissées à un `.env`
# local — pour qu'une installation neuve hérite des valeurs mesurées.
# 1200 ms de silence (ancien réglage local) ajoutaient à eux seuls plus d'une
# seconde avant la moindre transcription.
DEFAULT_AUDIO_DAEMON_SILENCE_MS = 500     # fourchette utile : 300-600 ms
DEFAULT_AUDIO_DAEMON_MIN_SPEECH_MS = 200  # en dessous, on jette les claquements
DEFAULT_AUDIO_DAEMON_PRE_ROLL_MS = 300    # amorce conservée avant le seuil


def _normalize_stt_engine(engine: str) -> str:
    value = (engine or "").strip().lower()
    if value == "local":
        return DEFAULT_STT_ENGINE
    return value


STT_ENGINE = _normalize_stt_engine(
    _get("STT_ENGINE") or _get("AUDIO_DAEMON_STT_ENGINE") or DEFAULT_STT_ENGINE
)
STT_MODEL = (_get("STT_MODEL") or _get("AUDIO_DAEMON_STT_MODEL") or DEFAULT_STT_MODEL).strip()
STT_FALLBACK_MODEL = (
    _get("STT_FALLBACK_MODEL")
    or _get("AUDIO_DAEMON_STT_FALLBACK_MODEL")
    or DEFAULT_STT_FALLBACK_MODEL
).strip()
STT_LANGUAGE = _get("STT_LANGUAGE") or _get("LANGUAGE") or DEFAULT_STT_LANGUAGE
STT_DEVICE = _get("STT_DEVICE", DEFAULT_STT_DEVICE)
STT_COMPUTE_TYPE = _get("STT_COMPUTE_TYPE", DEFAULT_STT_COMPUTE_TYPE)
# Décodage temps réel : un seul faisceau, et pas de second VAD interne — le
# daemon segmente déjà l'énoncé avant d'appeler le moteur.
STT_BEAM_SIZE = int(_get("STT_BEAM_SIZE", str(DEFAULT_STT_BEAM_SIZE)))
STT_QUALITY_FALLBACK_LOGPROB = float(_get(
    "STT_QUALITY_FALLBACK_LOGPROB",
    str(DEFAULT_STT_QUALITY_FALLBACK_LOGPROB),
))
STT_QUALITY_FALLBACK_MIN_SPEECH_MS = int(_get(
    "STT_QUALITY_FALLBACK_MIN_SPEECH_MS",
    str(DEFAULT_STT_QUALITY_FALLBACK_MIN_SPEECH_MS),
))
# Appel Flash streamé pour la voix : donne l'instant du premier token. La
# lecture ne démarre pas pour autant avant la fin de la passe 1 (un bloc
# ``action`` remplacerait le texte prononcé).
VOICE_LLM_STREAMING = _get("VOICE_LLM_STREAMING", "true").lower() in ("true", "1", "yes")
VOICE_ANTICIPATORY_ACK_ENABLED = _get(
    "VOICE_ANTICIPATORY_ACK_ENABLED", "true",
).lower() in ("true", "1", "yes")
STT_VAD_FILTER = _get("STT_VAD_FILTER", str(DEFAULT_STT_VAD_FILTER)).lower() in (
    "true", "1", "yes",
)
STT_ALLOW_MODEL_DOWNLOAD = (
    _get("STT_ALLOW_MODEL_DOWNLOAD", _get("AUDIO_DAEMON_ALLOW_MODEL_DOWNLOAD", "false"))
    .lower()
    == "true"
)

# ── Synthèse vocale locale (pile définitive) ────────────────
# Un seul jeu de réglages, valable quel que soit le backend : c'est ce qui
# permet de remplacer le moteur sans toucher au pipeline. Ce bloc ne contient
# volontairement aucune clé, aucune URL et aucun hôte — un fournisseur qui en
# réclamerait ne pourrait pas être configuré ici.
# Les valeurs par défaut vivent dans `jarvis/audio/tts/config.py` ; elles sont
# répétées ici parce que `config` reste la façade lue par le reste du dépôt.
DEFAULT_TTS_PROVIDER = "qwen3_local"
DEFAULT_TTS_MODEL_PATH = "mlx-community/Qwen3-TTS-12Hz-0.6B-Base-6bit"
DEFAULT_TTS_VOICE_PATH = "./voices/jarvis-fr"
DEFAULT_TTS_DEVICE = "auto"
DEFAULT_TTS_STREAMING = True
DEFAULT_TTS_SAMPLE_RATE = 24000
DEFAULT_TTS_CHANNELS = 1
DEFAULT_TTS_WARMUP = True
DEFAULT_TTS_TIMEOUT_SECONDS = 30.0
DEFAULT_TTS_MIN_CHUNK_CHARS = 30
DEFAULT_TTS_TARGET_CHUNK_CHARS = 80
DEFAULT_TTS_MAX_CHUNK_CHARS = 180
DEFAULT_TTS_FLUSH_TIMEOUT_MS = 250
# Le premier segment est le seul dont la longueur se paie en silence pur : les
# suivants se synthétisent derrière une lecture déjà commencée. Mesuré sur ce
# Mac mini M4 : sans ces seuils courts, une phrase de 94 caractères sans point
# interne fait attendre 564 ms au lieu de 242 ms.
DEFAULT_TTS_FIRST_CHUNK_MIN_CHARS = 15
DEFAULT_TTS_FIRST_CHUNK_MAX_CHARS = 60
# Secondes d'audio par bloc diffusé (12,5 trames/s : 0,4 s = 5 trames).
DEFAULT_TTS_STREAMING_INTERVAL = 0.4
# `icl` (référence + transcript) ou `speaker_embedding` (référence seule).
# Les deux tiennent le temps réel ; le choix se tranche à l'oreille.
DEFAULT_TTS_CLONE_MODE = "icl"
# Échantillonnage acoustique : décide de la stabilité du locuteur. Mesuré —
# 0.9/1.0/50 dérive de 20,5 Hz sur trois phrases, 0.5/0.9/30 de 4,2 Hz.
DEFAULT_TTS_TEMPERATURE = 0.5
DEFAULT_TTS_TOP_P = 0.9
DEFAULT_TTS_TOP_K = 30

TTS_PROVIDER = (_get("TTS_PROVIDER") or DEFAULT_TTS_PROVIDER).strip().lower()
# Chemin d'un répertoire de poids **déjà installé**, ou identifiant d'un dépôt
# déjà présent dans le cache local. Aucun téléchargement n'est déclenché par
# JARVIS : voir `scripts/download_tts_model.py`.
TTS_MODEL_PATH = _get("TTS_MODEL_PATH", DEFAULT_TTS_MODEL_PATH)
TTS_VOICE_PATH = _get("TTS_VOICE_PATH", DEFAULT_TTS_VOICE_PATH)
TTS_DEVICE = _get("TTS_DEVICE", DEFAULT_TTS_DEVICE).strip().lower()
TTS_STREAMING_INTERVAL = float(
    _get("TTS_STREAMING_INTERVAL", str(DEFAULT_TTS_STREAMING_INTERVAL))
)
TTS_CLONE_MODE = _get("TTS_CLONE_MODE", DEFAULT_TTS_CLONE_MODE).strip().lower()
TTS_TEMPERATURE = float(_get("TTS_TEMPERATURE", str(DEFAULT_TTS_TEMPERATURE)))
TTS_TOP_P = float(_get("TTS_TOP_P", str(DEFAULT_TTS_TOP_P)))
TTS_TOP_K = int(_get("TTS_TOP_K", str(DEFAULT_TTS_TOP_K)))
TTS_STREAMING = _get("TTS_STREAMING", str(DEFAULT_TTS_STREAMING)).lower() in (
    "true", "1", "yes",
)
TTS_SAMPLE_RATE = _positive_int("TTS_SAMPLE_RATE", DEFAULT_TTS_SAMPLE_RATE)
TTS_CHANNELS = _positive_int("TTS_CHANNELS", DEFAULT_TTS_CHANNELS)
TTS_WARMUP = _get("TTS_WARMUP", str(DEFAULT_TTS_WARMUP)).lower() in ("true", "1", "yes")
TTS_TIMEOUT_SECONDS = _positive_float("TTS_TIMEOUT_SECONDS", DEFAULT_TTS_TIMEOUT_SECONDS)
TTS_MIN_CHUNK_CHARS = _positive_int("TTS_MIN_CHUNK_CHARS", DEFAULT_TTS_MIN_CHUNK_CHARS)
TTS_TARGET_CHUNK_CHARS = _positive_int(
    "TTS_TARGET_CHUNK_CHARS", DEFAULT_TTS_TARGET_CHUNK_CHARS
)
TTS_MAX_CHUNK_CHARS = _positive_int("TTS_MAX_CHUNK_CHARS", DEFAULT_TTS_MAX_CHUNK_CHARS)
TTS_FLUSH_TIMEOUT_MS = _positive_int(
    "TTS_FLUSH_TIMEOUT_MS", DEFAULT_TTS_FLUSH_TIMEOUT_MS
)
TTS_FIRST_CHUNK_MIN_CHARS = _positive_int(
    "TTS_FIRST_CHUNK_MIN_CHARS", DEFAULT_TTS_FIRST_CHUNK_MIN_CHARS
)
TTS_FIRST_CHUNK_MAX_CHARS = _positive_int(
    "TTS_FIRST_CHUNK_MAX_CHARS", DEFAULT_TTS_FIRST_CHUNK_MAX_CHARS
)

AUDIO_DAEMON_OUTPUT_DEVICE = _get("AUDIO_DAEMON_OUTPUT_DEVICE", "")  # vide = sortie défaut macOS
AUDIO_DAEMON_HALF_DUPLEX = _get("AUDIO_DAEMON_HALF_DUPLEX", "true").lower() == "true"
AUDIO_DAEMON_PRE_ROLL_MS = int(
    _get("AUDIO_DAEMON_PRE_ROLL_MS", str(DEFAULT_AUDIO_DAEMON_PRE_ROLL_MS))
)
WAKE_WORD = _get("WAKE_WORD", "jarvis")

# Mode conversation mains libres (client : détection silence ; valeurs envoyées dans le status HTTP)
VOICE_SILENCE_DURATION_MS = int(_get("VOICE_SILENCE_DURATION_MS", "1200"))
VOICE_MIN_SPEECH_MS = int(_get("VOICE_MIN_SPEECH_MS", "400"))
VOICE_MAX_TOKENS = int(_get("VOICE_MAX_TOKENS", "500"))
VOICE_EMPTY_RETRY_TOKENS = int(_get("VOICE_EMPTY_RETRY_TOKENS", "1000"))

# Localisation (GPS / lieux nommés)
LOCATION_TRACKING = _get("LOCATION_TRACKING", "true").lower() == "true"
LOCATION_PLACE_RADIUS = int(_get("LOCATION_PLACE_RADIUS", "100"))
LOCATION_BATCH_MAX_POINTS = int(_get("LOCATION_BATCH_MAX_POINTS", "50"))

# Mode écoute continue (enregistrement long → transcription → synthèse)
RECORDING_MAX_DURATION_MIN = int(_get("RECORDING_MAX_DURATION_MIN", "180"))  # refus au-delà
RECORDING_CHUNK_SIZE_MB = int(_get("RECORDING_CHUNK_SIZE_MB", "20"))        # taille max par segment local
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

# ── Import iMessage — paramètres de l'importeur chat.db → jarvis.db ──
IIMPORT_BATCH_SIZE = int(_get("IIMPORT_BATCH_SIZE", "5000"))       # messages par batch
IIMPORT_MAX_RETRIES = int(_get("IIMPORT_MAX_RETRIES", "3"))        # tentatives max par batch
IIMPORT_SYNC_INTERVAL = int(_get("IIMPORT_SYNC_INTERVAL", "300"))  # secondes entre 2 syncs auto

# ── Daemon iMessage — processus permanent d'acces a chat.db ──
IMESSAGE_DAEMON_ENABLED = _get("IMESSAGE_DAEMON_ENABLED", "true").lower() == "true"
IMESSAGE_DAEMON_URL = _get("IMESSAGE_DAEMON_URL", "http://127.0.0.1:8193")
IMESSAGE_DAEMON_PORT = int(_get("IMESSAGE_DAEMON_PORT", "8193"))

# ── Système ─────────────────────────────────────────────────
DB_PATH = _get("DB_PATH", "./data/jarvis.db")
UPLOAD_DIR = _get("UPLOAD_DIR", "./data/uploads")
# Une même politique protège tous les points d'entrée multipart.
UPLOAD_MAX_BYTES = int(_get("UPLOAD_MAX_BYTES", str(20 * 1024 * 1024)))
UPLOAD_QUOTA_BYTES = int(_get("UPLOAD_QUOTA_BYTES", str(1024 * 1024 * 1024)))
# La grâce évite qu'une maintenance concurrente ne purge un fichier avant son
# enregistrement DB ; les fragments échoués plus anciens sont aussi collectés.
UPLOAD_ORPHAN_GRACE_SECONDS = int(_get("UPLOAD_ORPHAN_GRACE_SECONDS", "86400"))
# Les documents restent locaux par défaut. Hors mode strict, chaque upload doit
# encore fournir un consentement explicite avant tout résumé DeepSeek.
DOCUMENT_STRICT_LOCAL = _get("DOCUMENT_STRICT_LOCAL", "true").lower() == "true"
DOCUMENT_CLOUD_MAX_CHARS = int(_get("DOCUMENT_CLOUD_MAX_CHARS", "5000"))
SCHOOL_OUTPUT_DIR = _get("SCHOOL_OUTPUT_DIR", "./data/outputs/school")
DEV_PROJECTS_ROOT = _get("DEV_PROJECTS_ROOT", str(BASE_DIR / "dev_projects"))
DEVAGENT_EXEC_TIMEOUT = int(_get("DEVAGENT_EXEC_TIMEOUT", "120"))
LANGUAGE = _get("LANGUAGE", "fr")
TIMEZONE = _get("TIMEZONE", "Europe/Paris")
USER_NAME = _get("USER_NAME", "Nolann")
WEB_PORT = int(_get("WEB_PORT", "8080"))
# Port du superviseur. Il sert le frontend bureau **et** proxifie /api/* vers le
# backend : c'est le point d'entrée complet de l'application. Le plan de
# contrôle (`/api/supervisor/*`, `/ws/supervisor`) n'existe que là, et le
# serveur exige `Origin == Host` dessus — il n'est donc joignable qu'en même
# origine. La valeur vit ici, et non dans le seul `supervisor.py`, pour que le
# backend puisse dire à l'utilisateur où se trouve le plan de contrôle.
SUPERVISOR_PORT = _positive_int("SUPERVISOR_PORT", 9000)
# Sécurité par défaut : l'UI et l'API ne sont accessibles que depuis ce Mac.
# Toute adresse non loopback exige WEB_ALLOW_NETWORK_BIND=true et des
# protections supplémentaires vérifiées au démarrage.
WEB_HOST = _get("WEB_HOST", "127.0.0.1")
WEB_ALLOW_NETWORK_BIND = _get("WEB_ALLOW_NETWORK_BIND", "false").lower() == "true"
# TLS direct Uvicorn. Toute écoute non loopback l'exige.
WEB_HTTPS = _get("WEB_HTTPS", "false").lower() == "true"
# Reverse proxy TLS de confiance (Tailscale Serve, Caddy, nginx) devant un
# backend HTTP strictement loopback. Active cookie Secure + HSTS sans charger
# de certificat dans Uvicorn.
WEB_HTTPS_BEHIND_PROXY = (
    _get("WEB_HTTPS_BEHIND_PROXY", "false").lower() == "true"
)
# Origines supplémentaires exactes autorisées pour les mutations par cookie.
# Vide en production : la même origine schéma+hôte+port reste toujours admise.
# Toute origine de développement supplémentaire doit être ajoutée explicitement.
CSRF_ALLOWED_ORIGINS = _get("CSRF_ALLOWED_ORIGINS", "")
SSL_CERT_PATH = _config_path("WEB_SSL_CERT_PATH", "certs/cert.pem")
SSL_KEY_PATH = _config_path("WEB_SSL_KEY_PATH", "certs/key.pem")
WEB_SSL_AVAILABLE = SSL_CERT_PATH.is_file() and SSL_KEY_PATH.is_file()
WEB_USE_HTTPS = WEB_HTTPS and WEB_SSL_AVAILABLE

# Firebase Cloud Messaging — notifications Android, même application fermée.
FCM_SERVICE_ACCOUNT_FILE = _get("FCM_SERVICE_ACCOUNT_FILE", "")
FCM_PROJECT_ID = _get("FCM_PROJECT_ID", "")
WEB_PUSH_ALLOWED_HOSTS = _get(
    "WEB_PUSH_ALLOWED_HOSTS",
    "fcm.googleapis.com,updates.push.services.mozilla.com,web.push.apple.com",
)

# ── Contrôle ordinateur local (macOS) ────────────────────────
COMPUTER_ACCESS = _get("COMPUTER_ACCESS", "false").lower() == "true"
COMPUTER_SHELL = _get("COMPUTER_SHELL", "/bin/zsh")
COMPUTER_TIMEOUT = int(_get("COMPUTER_TIMEOUT", "30"))
# Restriction facultative de l'action `open_app`, qui s'exécute sans
# confirmation : un bloc ```action``` émis sous injection de prompt peut donc
# lancer n'importe quelle application enregistrée. Les chemins sont déjà
# refusés en amont (`ComputerControl._validate_argv`), aucun argument n'est
# transmis à l'application. Liste vide = aucune restriction (comportement
# historique) ; liste renseignée = allowlist stricte, insensible à la casse.
COMPUTER_ALLOWED_APPS = frozenset(
    a.strip().lower() for a in _get("COMPUTER_ALLOWED_APPS", "").split(",") if a.strip()
)
# Les commandes proposées par un LLM sont confinées ici et doivent toujours
# être confirmées avant une exécution sans shell.
LLM_SHELL_WORKSPACE = _get(
    "LLM_SHELL_WORKSPACE",
    str(BASE_DIR / "data" / "llm_shell_workspace"),
)
LLM_SHELL_MAX_COMMANDS = int(_get("LLM_SHELL_MAX_COMMANDS", "8"))
LLM_SHELL_MAX_TIMEOUT = int(_get("LLM_SHELL_MAX_TIMEOUT", "120"))
LLM_SHELL_PLAN_TTL_SECONDS = int(_get("LLM_SHELL_PLAN_TTL_SECONDS", "600"))
# TV contrôle ADB + Google Cast fallback
TV_IP = _get("TV_IP", "")
TV_ADB_PORT = _get("TV_ADB_PORT", "5555")
TV_MAC = _get("TV_MAC", "")
TV_CAST_ENABLED = _get("TV_CAST_ENABLED", "false").lower() == "true"
TV_CAST_TIMEOUT = int(_get("TV_CAST_TIMEOUT", "20"))
TV_DASHBOARD_URL = _get("TV_DASHBOARD_URL", "")  # URL opt-in ouverte via Chromecast

# ── Canal WebSocket TV — /ws/tv/events (lecture seule) ───────
# Diffusion descendante vers l'écran mural, authentifiée par le jeton privé du
# canal supervisor et limitée à la boucle locale. Aucune commande n'y transite.
TV_EVENTS_ENABLED = _get("TV_EVENTS_ENABLED", "true").lower() == "true"
TV_EVENTS_DEVICE_ID = _get("TV_EVENTS_DEVICE_ID", "")  # vide → DEVICE_ID
# Les transcriptions sont du contenu de conversation : hors du canal par défaut,
# parce qu'un écran de salon n'a pas à afficher ce qui se dit dans la pièce.
TV_EVENTS_INCLUDE_TRANSCRIPTS = (
    _get("TV_EVENTS_INCLUDE_TRANSCRIPTS", "false").lower() == "true"
)
TV_EVENT_MAX_TEXT_CHARS = int(_get("TV_EVENT_MAX_TEXT_CHARS", "200"))
TV_WS_MAX_CONNECTIONS = int(_get("TV_WS_MAX_CONNECTIONS", "4"))
TV_WS_QUEUE_MAXSIZE = int(_get("TV_WS_QUEUE_MAXSIZE", "100"))
TV_WS_MAX_DROPPED_EVENTS = int(_get("TV_WS_MAX_DROPPED_EVENTS", "200"))
TV_WS_HEARTBEAT_SECONDS = float(_get("TV_WS_HEARTBEAT_SECONDS", "20"))
TV_WS_SEND_TIMEOUT_SECONDS = float(_get("TV_WS_SEND_TIMEOUT_SECONDS", "5"))
TV_WS_MAX_EVENT_BYTES = int(_get("TV_WS_MAX_EVENT_BYTES", "8192"))
TV_WS_MAX_CLIENT_MESSAGE_BYTES = int(_get("TV_WS_MAX_CLIENT_MESSAGE_BYTES", "4096"))
TV_WS_MAX_CLIENT_VIOLATIONS = int(_get("TV_WS_MAX_CLIENT_VIOLATIONS", "3"))

# ── Commande de repas (Uber Eats, automatisation navigateur) ─
# Aucune API consommateur publique chez Uber : la commande passe par un
# navigateur Playwright avec la session capturée manuellement. Dépenser de
# l'argent réel exige trois interrupteurs distincts : l'intégration activée,
# le mode simulation désactivé, et des sélecteurs marqués vérifiés.
UBER_EATS_ENABLED = _get("UBER_EATS_ENABLED", "false").lower() == "true"
UBER_EATS_DRY_RUN = _get("UBER_EATS_DRY_RUN", "true").lower() == "true"
UBER_EATS_BASE_URL = _get("UBER_EATS_BASE_URL", "https://www.ubereats.com")
UBER_EATS_STORAGE_STATE = _config_path(
    "UBER_EATS_STORAGE_STATE",
    "data/uber_eats_storage_state.json",
)
UBER_EATS_SELECTORS_FILE = _config_path(
    "UBER_EATS_SELECTORS_FILE",
    "integrations/uber_eats_selectors.json",
)
UBER_EATS_SCREENSHOT_DIR = _config_path(
    "UBER_EATS_SCREENSHOT_DIR",
    "data/uber_eats_screenshots",
)
# Les captures d'échec contiennent adresse, nom et moyens de paiement :
# stockage privé, rotation courte, jamais transmises à un LLM.
UBER_EATS_SCREENSHOT_KEEP = int(_get("UBER_EATS_SCREENSHOT_KEEP", "20"))
UBER_EATS_MAX_ORDER_PRICE = float(_get("UBER_EATS_MAX_ORDER_PRICE", "40"))
UBER_EATS_MAX_DAILY_SPEND = float(_get("UBER_EATS_MAX_DAILY_SPEND", "80"))
UBER_EATS_MAX_DAILY_ORDERS = int(_get("UBER_EATS_MAX_DAILY_ORDERS", "2"))
UBER_EATS_MAX_ITEMS = int(_get("UBER_EATS_MAX_ITEMS", "10"))
UBER_EATS_MAX_ITEM_QUANTITY = int(_get("UBER_EATS_MAX_ITEM_QUANTITY", "5"))
UBER_EATS_HEADLESS = _get("UBER_EATS_HEADLESS", "true").lower() == "true"
UBER_EATS_LOCALE = _get("UBER_EATS_LOCALE", "fr-FR")
UBER_EATS_NAV_TIMEOUT_MS = int(_get("UBER_EATS_NAV_TIMEOUT_MS", "30000"))
UBER_EATS_ACTION_TIMEOUT_MS = int(_get("UBER_EATS_ACTION_TIMEOUT_MS", "10000"))
UBER_EATS_PLAN_TTL_SECONDS = int(_get("UBER_EATS_PLAN_TTL_SECONDS", "600"))

# ── Suggestions de repas et suivi de livraison ───────────────
# Le relevé de menus est en lecture seule : il ne peut rien acheter, mais il
# reste une automatisation de navigateur soumise aux mêmes conditions d'usage.
FOOD_SUGGESTIONS_ENABLED = _get("FOOD_SUGGESTIONS_ENABLED", "false").lower() == "true"
FOOD_MENU_SCRAPE_ENABLED = _get("FOOD_MENU_SCRAPE_ENABLED", "false").lower() == "true"
FOOD_MENU_SCRAPE_MAX_RESTAURANTS = int(_get("FOOD_MENU_SCRAPE_MAX_RESTAURANTS", "8"))
FOOD_MENU_MAX_AGE_HOURS = int(_get("FOOD_MENU_MAX_AGE_HOURS", "48"))
FOOD_SUGGESTION_TTL_HOURS = int(_get("FOOD_SUGGESTION_TTL_HOURS", "12"))
FOOD_SUGGESTION_SLOTS = int(_get("FOOD_SUGGESTION_SLOTS", "3"))
FOOD_SUGGESTION_MIN_ORDERS = int(_get("FOOD_SUGGESTION_MIN_ORDERS", "3"))
# Marge acceptée entre l'estimation affichée et le total réel du panier. Le
# clic autorise l'estimation majorée de ce pourcentage ; au-delà, JARVIS
# n'engage rien et demande une confirmation explicite.
FOOD_QUICK_ORDER_PRICE_TOLERANCE = float(
    _get("FOOD_QUICK_ORDER_PRICE_TOLERANCE", "0.15")
)
FOOD_DELIVERY_POLL_SECONDS = int(_get("FOOD_DELIVERY_POLL_SECONDS", "120"))
FOOD_DELIVERY_POLL_MAX_MINUTES = int(_get("FOOD_DELIVERY_POLL_MAX_MINUTES", "120"))

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

# ── Kill-switches explicites des jobs planifiés ─────────────
# Chaque job coûteux ou proactif doit pouvoir être neutralisé sans détourner
# un réglage fonctionnel (notifications bureau, seuil métier, etc.).
MORNING_BRIEFING_ENABLED = _get("MORNING_BRIEFING_ENABLED", "true").lower() == "true"
EVENING_SUMMARY_ENABLED = _get("EVENING_SUMMARY_ENABLED", "true").lower() == "true"
WEEKLY_SUMMARY_ENABLED = _get("WEEKLY_SUMMARY_ENABLED", "true").lower() == "true"
OVERDUE_TASKS_ENABLED = _get("OVERDUE_TASKS_ENABLED", "true").lower() == "true"
LOCATION_ANALYSIS_ENABLED = _get("LOCATION_ANALYSIS_ENABLED", "true").lower() == "true"
RELATIONSHIP_ANALYSIS_ENABLED = _get("RELATIONSHIP_ANALYSIS_ENABLED", "true").lower() == "true"
RELATIONSHIP_ALERTS_ENABLED = _get("RELATIONSHIP_ALERTS_ENABLED", "true").lower() == "true"
DB_MAINTENANCE_ENABLED = _get("DB_MAINTENANCE_ENABLED", "true").lower() == "true"
LLM_BUDGET_CHECK_ENABLED = _get("LLM_BUDGET_CHECK_ENABLED", "true").lower() == "true"
BREAK_ALERTS_ENABLED = _get("BREAK_ALERTS_ENABLED", "true").lower() == "true"
MOOD_SIGNALS_ENABLED = _get("MOOD_SIGNALS_ENABLED", "true").lower() == "true"
BINGE_ALERTS_ENABLED = _get("BINGE_ALERTS_ENABLED", "true").lower() == "true"
DOOMSCROLL_ALERTS_ENABLED = _get("DOOMSCROLL_ALERTS_ENABLED", "true").lower() == "true"
MISSED_OPPORTUNITIES_ENABLED = _get("MISSED_OPPORTUNITIES_ENABLED", "true").lower() == "true"

# ── Surveillance email proactive ────────────────────────────
# Intervalle (en secondes) entre chaque check des nouveaux emails par
# `scripts/email_watcher.py`. Le watcher analyse chaque mail non lu via
# Haiku (~$0.001/email) et crée des tâches/rappels/notifications auto.
EMAIL_CHECK_INTERVAL = float(_get("EMAIL_CHECK_INTERVAL", "120"))
EMAIL_WATCHER_LOCK_PATH = _get("EMAIL_WATCHER_LOCK_PATH", "")

# ── Daemon JARVIS (sentinelle permanente) ───────────────────
# Le daemon tourne en parallèle du serveur web : screen watcher,
# notifications proactives, wake word, TTS local.
DAEMON_ENABLED = _get("DAEMON_ENABLED", "true").lower() == "true"
# Garde-fou RAM / process (politique A : JARVIS only — jamais Codex/Chrome/IDE)
RESOURCE_GUARD_ENABLED = _get("RESOURCE_GUARD_ENABLED", "true").lower() == "true"
RESOURCE_GUARD_INTERVAL_S = float(_get("RESOURCE_GUARD_INTERVAL_S", "30"))
RESOURCE_GUARD_WARN_FREE_MB = int(_get("RESOURCE_GUARD_WARN_FREE_MB", "2048"))
RESOURCE_GUARD_CRITICAL_FREE_MB = int(_get("RESOURCE_GUARD_CRITICAL_FREE_MB", "1024"))
RESOURCE_GUARD_OLLAMA_IDLE_STOP = _get(
    "RESOURCE_GUARD_OLLAMA_IDLE_STOP", "true"
).lower() == "true"
RESOURCE_GUARD_OLLAMA_IDLE_TTL_S = float(
    _get("RESOURCE_GUARD_OLLAMA_IDLE_TTL_S", "120")
)
RESOURCE_GUARD_TTS_MAX_WORKERS = int(_get("RESOURCE_GUARD_TTS_MAX_WORKERS", "1"))
RESOURCE_GUARD_KILL_ORPHANS = _get("RESOURCE_GUARD_KILL_ORPHANS", "true").lower() == "true"
RESOURCE_GUARD_DRY_RUN = _get("RESOURCE_GUARD_DRY_RUN", "false").lower() == "true"
SCREEN_WATCHER_ENABLED = _get("SCREEN_WATCHER_ENABLED", "true").lower() == "true"
SCREEN_WATCHER_INTERVAL = int(_get("SCREEN_WATCHER_INTERVAL", "15"))      # secondes
SCREEN_CHANGE_THRESHOLD = float(_get("SCREEN_CHANGE_THRESHOLD", "5"))     # % minimum
SCREEN_ANALYSIS_THRESHOLD = float(_get("SCREEN_ANALYSIS_THRESHOLD", "30"))  # % pour LLM vision
SCREEN_NOTIFICATION_TTL_S = float(_get("SCREEN_NOTIFICATION_TTL_S", "15"))
SCREEN_RESIZE_WIDTH = int(_get("SCREEN_RESIZE_WIDTH", "1280"))
SCREEN_RESIZE_HEIGHT = int(_get("SCREEN_RESIZE_HEIGHT", "800"))
SCREEN_RESIZE: tuple[int, int] = (SCREEN_RESIZE_WIDTH, SCREEN_RESIZE_HEIGHT)
SCREEN_MAX_ANALYSIS_WIDTH = int(_get("SCREEN_MAX_ANALYSIS_WIDTH", "768"))
SCREEN_JPEG_QUALITY = int(_get("SCREEN_JPEG_QUALITY", "55"))
REMOTE_SCREEN_MAX_IMAGE_BYTES = int(
    _get("REMOTE_SCREEN_MAX_IMAGE_BYTES", str(5 * 1024 * 1024))
)
REMOTE_SCREEN_MAX_PIXELS = int(_get("REMOTE_SCREEN_MAX_PIXELS", str(16 * 1024 * 1024)))
REMOTE_SCREEN_MAX_DIMENSION = int(_get("REMOTE_SCREEN_MAX_DIMENSION", "8192"))
# JSON + base64 ajoutent environ 4/3 au binaire. Le petit supplément couvre
# les métadonnées sans autoriser un corps arbitrairement gros avant parsing.
REMOTE_SCREEN_MAX_REQUEST_BYTES = int(
    _get(
        "REMOTE_SCREEN_MAX_REQUEST_BYTES",
        str(((REMOTE_SCREEN_MAX_IMAGE_BYTES + 2) // 3) * 4 + 128 * 1024),
    )
)
SCREEN_VISION_MODEL = _get(
    "SCREEN_VISION_MODEL",
    _get("SCREEN_WATCHER_VISION_MODEL", "qwen3-vl:4b"),
)
SCREEN_OLLAMA_MIN_INTERVAL_S = float(_get("SCREEN_OLLAMA_MIN_INTERVAL_S", "60"))  # delai min entre 2 analyses vision
TRIAGE_MODEL = _get("TRIAGE_MODEL", "") or DEEPSEEK_FAST_MODEL  # triage daemon = DeepSeek Flash
OLLAMA_URL = _get("OLLAMA_URL", _get("SCREEN_WATCHER_OLLAMA_URL", "http://127.0.0.1:11434"))
OLLAMA_AUTOSTART = _get("OLLAMA_AUTOSTART", "true").lower() == "true"
# Raisonnement local via Ollama : TOUJOURS false hors Screen Watcher
OLLAMA_REASONING_ENABLED = _get("OLLAMA_REASONING_ENABLED", "false").lower() == "true"
# Alias : démarrer SW au boot complet (même sémantique que SCREEN_WATCHER_ENABLED)
SCREEN_WATCHER_AUTOSTART = _get(
    "SCREEN_WATCHER_AUTOSTART",
    "true" if SCREEN_WATCHER_ENABLED else "false",
).lower() == "true"

# Identité de la machine — sert pour register_local_device + screen_watcher
# python-dotenv peut transformer `DEVICE_ID=   # commentaire` en valeur `# …` :
# on refuse toute valeur qui ressemble à un commentaire inline.
_DEVICE_ID_RAW = _get("DEVICE_ID", "").strip()
DEVICE_ID = (
    socket.gethostname()
    if (not _DEVICE_ID_RAW or _DEVICE_ID_RAW.startswith("#"))
    else _DEVICE_ID_RAW
)
DEVICE_NAME = _get("DEVICE_NAME", "Mac Mini")

# Wake word "Jarvis" via Porcupine (Picovoice — gratuit usage perso)
WAKE_WORD_ENABLED = _get("WAKE_WORD_ENABLED", "false").lower() == "true"
PORCUPINE_ACCESS_KEY = _get("PORCUPINE_ACCESS_KEY", "")

# Anti-spam vocal en mode veille : minimum N secondes entre deux notifs voix
DAEMON_TTS_COOLDOWN = int(_get("DAEMON_TTS_COOLDOWN", "15"))

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
AUDIO_DAEMON_SILENCE_MS = int(
    _get("AUDIO_DAEMON_SILENCE_MS", str(DEFAULT_AUDIO_DAEMON_SILENCE_MS))
)
AUDIO_DAEMON_MIN_SPEECH_MS = int(
    _get("AUDIO_DAEMON_MIN_SPEECH_MS", str(DEFAULT_AUDIO_DAEMON_MIN_SPEECH_MS))
)
AUDIO_DAEMON_MAX_UTTERANCE_S = int(_get("AUDIO_DAEMON_MAX_UTTERANCE_S", "30"))
AUDIO_DAEMON_CONVERSATION_TIMEOUT = float(_get("AUDIO_DAEMON_CONVERSATION_TIMEOUT", "30.0"))
AUDIO_DAEMON_INPUT_DEVICE = _get("AUDIO_DAEMON_INPUT_DEVICE", "")  # vide = entrée défaut macOS
AUDIO_DAEMON_WAKE_SOUND = _get("AUDIO_DAEMON_WAKE_SOUND", "true").lower() == "true"
AUDIO_DAEMON_STT_ENGINE = STT_ENGINE
AUDIO_DAEMON_STT_MODEL = STT_MODEL
AUDIO_DAEMON_STT_FALLBACK_MODEL = STT_FALLBACK_MODEL
AUDIO_DAEMON_WHISPERCPP_MODEL_PATH = _get(
    "AUDIO_DAEMON_WHISPERCPP_MODEL_PATH", str(Path.home() / "models" / "ggml-large-v3.bin")
)
AUDIO_DAEMON_ALLOW_MODEL_DOWNLOAD = STT_ALLOW_MODEL_DOWNLOAD

# ── VAD (Voice Activity Detection) ────────────────────────────
SILERO_VAD_THRESHOLD = float(_get("SILERO_VAD_THRESHOLD", "0.42"))  # hysteresis ON
SILERO_VAD_THRESHOLD_OFF = float(_get("SILERO_VAD_THRESHOLD_OFF", "0.28"))

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

# Rétention des tables volumineuses (jours). 0 = conserver indéfiniment, sauf
# pour les journaux d'actions : leur fenêtre est volontairement bornée.
RETENTION_SCREEN_DAYS = int(_get("RETENTION_SCREEN_DAYS", "30"))
RETENTION_LOCATION_DAYS = int(_get("RETENTION_LOCATION_DAYS", "90"))
RETENTION_METRICS_DAYS = max(7, min(int(_get("RETENTION_METRICS_DAYS", "90")), 365))
RETENTION_NOTIF_READ_DAYS = int(_get("RETENTION_NOTIF_READ_DAYS", "60"))
RETENTION_LLM_LOGS_DAYS = max(1, min(int(_get("RETENTION_LLM_LOGS_DAYS", "7")), 30))
RETENTION_SCHEDULER_RUNS_DAYS = max(
    1, min(int(_get("RETENTION_SCHEDULER_RUNS_DAYS", "14")), 90)
)
SCHEDULER_RUN_OUTPUT_MAX_CHARS = max(
    256,
    min(int(_get("SCHEDULER_RUN_OUTPUT_MAX_CHARS", "4000")), 16_384),
)
ACTION_LOG_MAX_PAYLOAD_CHARS = max(
    256,
    min(int(_get("ACTION_LOG_MAX_PAYLOAD_CHARS", "2048")), 16_384),
)

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

# ── Fitness proactif ───────────────────────────────────────
# Interrupteur global d'urgence. Les horaires et la cadence se règlent ensuite
# depuis l'écran Fitness et sont persistés dans fitness_programs.
FITNESS_REMINDERS_ENABLED = _get("FITNESS_REMINDERS_ENABLED", "true").lower() == "true"
# Vision locale pour photos d'assiette (défaut = modèle screen watcher).
FITNESS_MEAL_VISION_MODEL = _get("FITNESS_MEAL_VISION_MODEL", "") or SCREEN_VISION_MODEL
FITNESS_MEAL_VISION_TIMEOUT_S = float(_get("FITNESS_MEAL_VISION_TIMEOUT_S", "90"))
FITNESS_MEAL_VISION_MAX_TOKENS = int(_get("FITNESS_MEAL_VISION_MAX_TOKENS", "800"))
FITNESS_MEAL_ANALYSIS_MAX_TOKENS = int(_get("FITNESS_MEAL_ANALYSIS_MAX_TOKENS", "2048"))
FITNESS_MEAL_PHOTO_MAX_BYTES = int(_get("FITNESS_MEAL_PHOTO_MAX_BYTES", "8000000"))
FITNESS_MEAL_PHOTO_MAX_PIXELS = int(
    _get("FITNESS_MEAL_PHOTO_MAX_PIXELS", "12000000")
)
FITNESS_MEAL_PHOTO_MAX_DIMENSION = int(
    _get("FITNESS_MEAL_PHOTO_MAX_DIMENSION", "8192")
)

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

# ── Détection binge streaming (screen watcher) ────────────────
BINGE_ALERT_MINUTES = int(_get("BINGE_ALERT_MINUTES", "120"))  # streaming continu avant commentaire ; 0 = off
BINGE_GAP_MINUTES = int(_get("BINGE_GAP_MINUTES", "20"))
STREAMING_APPS = [a.strip().lower() for a in _get(
    "STREAMING_APPS", "netflix,youtube,twitch,prime video,disney,plex,molotov,mycanal"
).split(",") if a.strip()]

# ── Alerte trajet retour tard (GPS + heure) ───────────────────
LATE_RETURN_ENABLED = _get("LATE_RETURN_ENABLED", "true").lower() == "true"
LATE_RETURN_HOUR = int(_get("LATE_RETURN_HOUR", "23"))         # à partir de cette heure

# ── Voix : rejeu, session persistante, TTS spéculatif ─────────
SPECULATIVE_TTS_ENABLED = _get("SPECULATIVE_TTS_ENABLED", "false").lower() == "true"
VOICE_SESSION_GRACE_S = int(_get("VOICE_SESSION_GRACE_S", "180"))  # reprise après coupure courte
if SPECULATIVE_TTS_ENABLED:
    # Le préchauffage est sérialisé côté daemon, mais chaque phrase
    # pré-générée occupe quand même le GPU hors tour de parole.
    logger.warning(
        "SPECULATIVE_TTS_ENABLED=true : le préchauffage est sérialisé, mais "
        "chaque phrase pré-générée charge encore le GPU. Désactivez-le si la "
        "RAM est serrée."
    )

# ── Auto-résumé de réunions (micro daemon audio) ──────────────
# Opt-in : capture les transcriptions ambiantes du micro pour détecter une
# réunion (parole soutenue) et produire résumé + actions à la fin.
MEETING_CAPTURE_ENABLED = _get("MEETING_CAPTURE_ENABLED", "false").lower() == "true"
MEETING_MIN_SPEECH_S = int(_get("MEETING_MIN_SPEECH_S", "240"))   # parole cumulée pour ouvrir (4 min)
MEETING_WINDOW_MIN = int(_get("MEETING_WINDOW_MIN", "15"))        # fenêtre de cumul avant ouverture
MEETING_SILENCE_MIN = int(_get("MEETING_SILENCE_MIN", "10"))      # silence qui clôt la réunion

# ── Migrations SQLite versionnées (backup automatique préalable) ──
DB_MIGRATIONS_DIR = _get("DB_MIGRATIONS_DIR", str(BASE_DIR / "database" / "migrations"))
DB_MIGRATIONS_AUTO_APPLY = _get("DB_MIGRATIONS_AUTO_APPLY", "true").lower() == "true"

# ── Détection de régression de performance ──────────────────
PERF_REGRESSION_THRESHOLD_PCT = int(_get("PERF_REGRESSION_THRESHOLD_PCT", "40"))
PERF_BASELINE_WINDOW = int(_get("PERF_BASELINE_WINDOW", "5"))

# ── Scan de code dupliqué ────────────────────────────────────
DUPLICATE_SCAN_ENABLED = _get("DUPLICATE_SCAN_ENABLED", "true").lower() == "true"
DUPLICATE_SCAN_MIN_LINES = int(_get("DUPLICATE_SCAN_MIN_LINES", "6"))
DUPLICATE_SCAN_DIRS = _get("DUPLICATE_SCAN_DIRS", "agents,scripts,integrations,database")

# ── Audit sécurité (secrets, patterns dangereux) ────────────
SECURITY_AUDIT_ENABLED = _get("SECURITY_AUDIT_ENABLED", "true").lower() == "true"
SECURITY_AUDIT_DIRS = _get("SECURITY_AUDIT_DIRS", "agents,scripts,integrations,database,main.py,config.py,llm.py,actions.py")
SECURITY_AUTO_FIX_ENABLED = _get("SECURITY_AUTO_FIX_ENABLED", "false").lower() == "true"

# ── Génération auto de tests manquants (opt-in, coûte des tokens) ──
AUTO_TEST_GEN_ENABLED = _get("AUTO_TEST_GEN_ENABLED", "false").lower() == "true"
AUTO_TEST_GEN_TARGET_DIRS = _get("AUTO_TEST_GEN_TARGET_DIRS", "")  # vide = aucune cible, opt-in explicite

# ── DevAgent : PR auto, déploiement staging ─────────────────
DEVAGENT_AUTO_PR = _get("DEVAGENT_AUTO_PR", "true").lower() == "true"
DEVAGENT_AUTO_DEPLOY_STAGING = _get("DEVAGENT_AUTO_DEPLOY_STAGING", "true").lower() == "true"
DEVAGENT_AUTORUN_MAX_INTERVIEW_ROUNDS = int(_get("DEVAGENT_AUTORUN_MAX_INTERVIEW_ROUNDS", "6"))

# ── CI locale (pré-commit) ───────────────────────────────────
LOCAL_CI_RUN_FRONTEND_BUILD = _get("LOCAL_CI_RUN_FRONTEND_BUILD", "false").lower() == "true"

# ── Self-healing (diagnostic + délégation PR-only, désactivé par défaut) ──
SELF_HEALING_ENABLED = _get("SELF_HEALING_ENABLED", "false").lower() == "true"
SELF_HEALING_CRASH_THRESHOLD = int(_get("SELF_HEALING_CRASH_THRESHOLD", "3"))

# ── Autonomie cognitive (Cursor / auto-réparation / auto-amélioration) ──
CURSOR_DELEGATION_ENABLED = _get("CURSOR_DELEGATION_ENABLED", "true").lower() == "true"
CURSOR_CLI_PATH = _get("CURSOR_CLI_PATH", "")  # vide = auto-detect agent / cursor-agent
CURSOR_DEFAULT_TIMEOUT_SEC = int(_get("CURSOR_DEFAULT_TIMEOUT_SEC", "1800"))
CURSOR_MAX_CONCURRENT_JOBS = int(_get("CURSOR_MAX_CONCURRENT_JOBS", "2"))
CURSOR_WORKTREE_ROOT = _get("CURSOR_WORKTREE_ROOT", ".jarvis/worktrees")
CURSOR_ALLOW_COMMIT = _get("CURSOR_ALLOW_COMMIT", "true").lower() == "true"
# Fail-closed : push/PR off par défaut — opt-in explicite dans .env
CURSOR_ALLOW_PUSH = _get("CURSOR_ALLOW_PUSH", "false").lower() == "true"
CURSOR_ALLOW_PR = _get("CURSOR_ALLOW_PR", "false").lower() == "true"

# Fail-closed : autonomie off par défaut
SELF_REPAIR_ENABLED = _get("SELF_REPAIR_ENABLED", "false").lower() == "true"
SELF_IMPROVEMENT_ENABLED = _get("SELF_IMPROVEMENT_ENABLED", "false").lower() == "true"
SELF_IMPROVEMENT_SCHEDULE = _get("SELF_IMPROVEMENT_SCHEDULE", "weekly")

# ── Prédiction du prochain message ───────────────────────────
MESSAGE_PREDICTION_LOOKBACK_DAYS = int(_get("MESSAGE_PREDICTION_LOOKBACK_DAYS", "60"))

# ── Lieux favoris et opportunités ratées ─────────────────────
FAVORITE_PLACE_MIN_VISITS = int(_get("FAVORITE_PLACE_MIN_VISITS", "5"))
OPPORTUNITY_MIN_DAYS_NAMED = int(_get("OPPORTUNITY_MIN_DAYS_NAMED", "30"))  # lieu nommé depuis N jours...
OPPORTUNITY_MAX_VISITS = int(_get("OPPORTUNITY_MAX_VISITS", "0"))          # ...avec au plus N visites = raté

# ── Doomscrolling ─────────────────────────────────────────────
DOOMSCROLL_APPS = _get(
    "DOOMSCROLL_APPS", "instagram,tiktok,twitter,x,reddit,facebook,snapchat,threads"
)
DOOMSCROLL_DAILY_MINUTES = int(_get("DOOMSCROLL_DAILY_MINUTES", "90"))  # cumul quotidien, pas continu

# ── Coût de la procrastination ───────────────────────────────
PROCRASTINATION_ABANDONED_DAYS = int(_get("PROCRASTINATION_ABANDONED_DAYS", "30"))
PROCRASTINATION_HOURLY_VALUE = float(_get("PROCRASTINATION_HOURLY_VALUE", "0"))  # 0 = pas d'estimation monétaire

# ── Journal parallèle de JARVIS ──────────────────────────────
JARVIS_JOURNAL_ENABLED = _get("JARVIS_JOURNAL_ENABLED", "true").lower() == "true"
JARVIS_JOURNAL_TIME = _get("JARVIS_JOURNAL_TIME", "23:50")

# ── Recherche sémantique (embeddings locaux) ─────────────────
# Nécessite `sentence-transformers` (dépendance lourde optionnelle, comme
# torch/faster-whisper) — dégrade proprement (erreur claire, pas de crash)
# si absente. Le modèle (~90 Mo) se télécharge une fois puis reste en cache.
SEMANTIC_SEARCH_MODEL = _get("SEMANTIC_SEARCH_MODEL", "all-MiniLM-L6-v2")

# ── Diarisation (mode écoute) ─────────────────────────────────
# N'identifie PAS une personne réelle automatiquement — segmente juste les
# tours de parole ("A", "B"…) au sein d'UN enregistrement. Les labels ne
# sont cohérents que dans un seul appel STT : l'audio entier (chunks
# concaténés) est donc envoyé en un seul appel, plafonné à 100 Mo.
DIARIZATION_ENABLED = _get("DIARIZATION_ENABLED", "false").lower() == "true"

# ── Authentification / verrouillage app ──────────────────────
SESSION_COOKIE_NAME = _get("SESSION_COOKIE_NAME", "jarvis_session")
SESSION_MAX_AGE_DAYS = int(_get("SESSION_MAX_AGE_DAYS", "30"))       # expiration absolue
SESSION_INACTIVITY_DAYS = int(_get("SESSION_INACTIVITY_DAYS", "14"))  # ré-émise à chaque requête active
AUTH_LOCKOUT_MAX_ATTEMPTS = int(_get("AUTH_LOCKOUT_MAX_ATTEMPTS", "5"))
AUTH_LOCKOUT_MINUTES = int(_get("AUTH_LOCKOUT_MINUTES", "15"))
AUTH_RATE_WINDOW_MINUTES = int(_get("AUTH_RATE_WINDOW_MINUTES", "15"))
AUTH_PROGRESSIVE_DELAY_SECONDS = int(_get("AUTH_PROGRESSIVE_DELAY_SECONDS", "1"))
AUTH_PROGRESSIVE_DELAY_MAX_SECONDS = int(
    _get("AUTH_PROGRESSIVE_DELAY_MAX_SECONDS", "30")
)
AUTH_GLOBAL_MAX_ATTEMPTS = int(_get("AUTH_GLOBAL_MAX_ATTEMPTS", "50"))
AUTH_GLOBAL_LOCKOUT_MINUTES = int(_get("AUTH_GLOBAL_LOCKOUT_MINUTES", "5"))
AUTO_LOCK_MINUTES = int(_get("AUTO_LOCK_MINUTES", "5"))  # verrouillage écran côté client après inactivité

# ── Pairage des agents desktop distants ──────────────────────
DEVICE_PAIRING_TTL_MINUTES = int(_get("DEVICE_PAIRING_TTL_MINUTES", "10"))
DEVICE_PAIRING_MAX_ATTEMPTS = int(_get("DEVICE_PAIRING_MAX_ATTEMPTS", "5"))
DEVICE_PAIRING_ATTEMPT_WINDOW_MINUTES = int(
    _get("DEVICE_PAIRING_ATTEMPT_WINDOW_MINUTES", "10")
)
DEVICE_PAIRING_LOCKOUT_MINUTES = int(_get("DEVICE_PAIRING_LOCKOUT_MINUTES", "15"))

# ── Jetons pour intégrations non-navigateur ──────────────────
# Requis par POST /api/location sauf si un Bearer mobile valide est fourni.
LOCATION_API_TOKEN = _get("LOCATION_API_TOKEN", "")
LOCATION_RATE_LIMIT_REQUESTS = int(_get("LOCATION_RATE_LIMIT_REQUESTS", "120"))
LOCATION_RATE_LIMIT_WINDOW_SECONDS = int(
    _get("LOCATION_RATE_LIMIT_WINDOW_SECONDS", "60")
)

# ── Chiffrement des sauvegardes (activé par défaut) ──────────
BACKUP_ENCRYPTION_ENABLED = _get("BACKUP_ENCRYPTION_ENABLED", "true").lower() == "true"
BACKUP_ENCRYPTION_PASSPHRASE = _get("BACKUP_ENCRYPTION_PASSPHRASE", "")
BACKUP_ENCRYPTION_KEY_FILE = _get(
    "BACKUP_ENCRYPTION_KEY_FILE",
    "./data/.backup_encryption.key",
)

# ── Chiffrement complet de la base active (SQLCipher, opt-in) ──
# L'activation d'une base existante passe d'abord par
# `tools/database_encryption.py enable`. Sans passphrase explicite, chaque
# profil reçoit une clé aléatoire dans le Trousseau macOS.
DATABASE_ENCRYPTION_ENABLED = _get("DATABASE_ENCRYPTION_ENABLED", "false").lower() == "true"
DATABASE_ENCRYPTION_PASSPHRASE = _get("DATABASE_ENCRYPTION_PASSPHRASE", "")
DATABASE_ENCRYPTION_KEYCHAIN_SERVICE = _get(
    "DATABASE_ENCRYPTION_KEYCHAIN_SERVICE",
    "com.jarvis.database.sqlcipher",
)

# Interface mobile autonome (HTML/CSS/JS vanilla, servie sous /mobile/).
WEB_MOBILE_DIR = _get("WEB_MOBILE_DIR", str(BASE_DIR / "web_mobile"))

# Frontend bureau unique (Next.js 15).
FRONTEND_DIST_DIR = _get("FRONTEND_DIST_DIR", str(BASE_DIR / "frontend" / "out"))

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
    "food": DEEPSEEK_FAST_MODEL,
}

# ── Conversation vocale Android (push-to-talk) ─────────────────
MOBILE_VOICE_MAX_BYTES = int(_get("MOBILE_VOICE_MAX_BYTES", str(5 * 1024 * 1024)))
MOBILE_VOICE_MIN_BYTES = int(_get("MOBILE_VOICE_MIN_BYTES", "1000"))
MOBILE_VOICE_MAX_REQUEST_BYTES = int(
    _get("MOBILE_VOICE_MAX_REQUEST_BYTES", str(MOBILE_VOICE_MAX_BYTES + 256 * 1024))
)
MOBILE_VOICE_MAX_DURATION_SEC = int(_get("MOBILE_VOICE_MAX_DURATION_SEC", "60"))
MOBILE_VOICE_STT_TIMEOUT_SEC = float(_get("MOBILE_VOICE_STT_TIMEOUT_SEC", "120"))
MOBILE_VOICE_LLM_TIMEOUT_SEC = float(_get("MOBILE_VOICE_LLM_TIMEOUT_SEC", "90"))
MOBILE_VOICE_TTS_TIMEOUT_SEC = float(_get("MOBILE_VOICE_TTS_TIMEOUT_SEC", "60"))

# ── Variables retirées ───────────────────────────────────────
# Une capacité supprimée doit le dire : un `.env` laissé en place continuerait
# sinon d'affirmer une fonctionnalité que le code ne sert plus. On avertit une
# fois au chargement plutôt que d'ignorer la ligne en silence.
_RETIRED_TTS = (
    "La pile vocale est passée à un fournisseur local unique — utilisez "
    "TTS_PROVIDER, TTS_MODEL_PATH et TTS_VOICE_PATH."
)
_RETIRED_EDGE = (
    "Edge TTS a été retiré : la synthèse est entièrement locale, plus aucun "
    "appel réseau à borner."
)

_RETIRED_KOKORO = (
    "Kokoro et le backend transitoire `current_local` ont été retirés ; "
    "Qwen3-TTS local (`qwen3_local`) est le seul moteur TTS."
)

RETIRED_ENV_VARS: dict[str, str] = {
    "CODE_EXECUTOR_ENABLED": (
        "Open Interpreter a été retiré ; les tâches techniques passent par "
        "Cursor CLI et l'action terminal confinée."
    ),
    "CODE_EXECUTOR_TIMEOUT": "Open Interpreter a été retiré.",
    "CODE_EXECUTOR_MODEL": "Open Interpreter a été retiré.",
    # Pile vocale : un seul fournisseur local, choisi par TTS_PROVIDER. Un
    # `.env` qui définit encore l'ancien sélecteur de moteur doit le savoir,
    # plutôt que de croire configurer une voix qui n'existe plus.
    "TTS_ENGINE": _RETIRED_TTS,
    "TTS_VOICE": f"{_RETIRED_TTS} La voix vit dans TTS_VOICE_PATH.",
    "TTS_MODEL": f"{_RETIRED_TTS} Le modèle vit dans TTS_MODEL_PATH.",
    "TTS_LANGUAGE": _RETIRED_TTS,
    "TTS_SPEAKER": _RETIRED_TTS,
    "MACOS_TTS_VOICE": f"{_RETIRED_TTS} Le moteur macOS `say` a été retiré.",
    "EDGE_TTS_CONNECT_TIMEOUT_SEC": _RETIRED_EDGE,
    "EDGE_TTS_RECEIVE_TIMEOUT_SEC": _RETIRED_EDGE,
    "EDGE_TTS_TOTAL_TIMEOUT_SEC": _RETIRED_EDGE,
    "KOKORO_BACKEND": _RETIRED_KOKORO,
    "KOKORO_LANG": _RETIRED_KOKORO,
    "KOKORO_WARM_WORKER": _RETIRED_KOKORO,
    "KOKORO_MODEL": _RETIRED_KOKORO,
    "KOKORO_VOICE": _RETIRED_KOKORO,
    "KOKORO_LANG_CODE": _RETIRED_KOKORO,
    "KOKORO_SPEED": _RETIRED_KOKORO,
    "KOKORO_MAX_TOKENS": _RETIRED_KOKORO,
    "KOKORO_FIRST_CHUNK_MAX_TOKENS": _RETIRED_KOKORO,
}


def warn_retired_env_vars() -> list[str]:
    """Signale les variables encore définies alors que leur capacité a disparu.

    Retourne les noms rencontrés pour permettre une assertion en test.
    """
    found = [name for name in RETIRED_ENV_VARS if os.getenv(name) is not None]
    for name in found:
        logger.warning(
            "[config] %s est définie mais n'a plus aucun effet — %s",
            name,
            RETIRED_ENV_VARS[name],
        )
    return found


warn_retired_env_vars()
