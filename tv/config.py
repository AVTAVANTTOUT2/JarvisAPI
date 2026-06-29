"""Configuration du serveur TV JARVIS — War Room Dashboard.

Toutes les valeurs configurables sont centralisées ici avec des valeurs
par défaut adaptées à un environnement local Mac.
"""

import os
from pathlib import Path
from typing import Final

# ── Chemins ──────────────────────────────────────────────────
BASE_DIR: Final = Path(__file__).resolve().parent
ROOT_DIR: Final = BASE_DIR.parent
DB_PATH: Final = str(ROOT_DIR / "data" / "jarvis.db")
LOGS_DIR: Final = str(ROOT_DIR / "logs")
FONTS_DIR: Final = str(BASE_DIR / "static" / "assets" / "fonts")
IMESSAGE_DB: Final = str(Path.home() / "Library" / "Messages" / "chat.db")

# ── Serveur HTTP ─────────────────────────────────────────────
TV_HOST: Final[str] = os.getenv("TV_HOST", "0.0.0.0")
TV_PORT: Final[int] = int(os.getenv("TV_PORT", "5174"))

# ── Backend principal JARVIS ─────────────────────────────────
BACKEND_HOST: Final[str] = os.getenv("BACKEND_HOST", "127.0.0.1")
BACKEND_PORT: Final[int] = int(os.getenv("BACKEND_PORT", "8081"))
BACKEND_BASE_URL: Final[str] = f"https://{BACKEND_HOST}:{BACKEND_PORT}"

# ── Sécurité — IP Whitelist ──────────────────────────────────
WHITELIST_NETWORKS: Final[list[str]] = [
    "192.168.1.0/24",    # réseau local
    "100.64.0.0/10",     # Tailscale CGNAT
    "127.0.0.1",         # localhost
]

# ── Météo — Open-Meteo (gratuit, pas de clé) ─────────────────
WEATHER_LAT: Final[float] = 50.6292   # Lille
WEATHER_LON: Final[float] = 3.0573    # Lille
WEATHER_CACHE_SECONDS: Final[int] = 900  # 15 minutes

# ── Intervalles de refresh (secondes) ────────────────────────
REFRESH_CLOCK: Final[int] = 1
REFRESH_WEATHER: Final[int] = 900       # 15 min
REFRESH_MOOD: Final[int] = 300          # 5 min
REFRESH_STATS: Final[int] = 10          # 10 s
REFRESH_AUTOMATIONS: Final[int] = 30    # 30 s
REFRESH_CALENDAR: Final[int] = 300      # 5 min
REFRESH_TASKS: Final[int] = 120         # 2 min
REFRESH_MESSAGES: Final[int] = 30       # 30 s
REFRESH_EMAILS: Final[int] = 300        # 5 min
REFRESH_NOTIFICATIONS: Final[int] = 30  # 30 s
REFRESH_DEVICES: Final[int] = 60        # 1 min

# ── Limites d'affichage ──────────────────────────────────────
MAX_AUTOMATIONS: Final[int] = 15
MAX_TASKS: Final[int] = 8
MAX_MESSAGES: Final[int] = 10
MAX_IMESSAGES: Final[int] = 10
MAX_CHAT_MESSAGES: Final[int] = 5
MAX_EMAILS: Final[int] = 5
MAX_NOTIFICATIONS: Final[int] = 5

# ── Temps de rétention des données ───────────────────────────
AUTOMATIONS_HOURS: Final[int] = 24

# ── Mémoisation data sources ─────────────────────────────────
DATA_CACHE_TTL_SECONDS: Final[int] = 5  # cache générique court

# ── Timezone ─────────────────────────────────────────────────
TIMEZONE: Final[str] = "Europe/Paris"
