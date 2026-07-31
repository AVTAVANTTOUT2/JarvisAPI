"""Configuration du serveur TV JARVIS — War Room Dashboard.

Toutes les valeurs configurables sont centralisées ici avec des valeurs
par défaut adaptées à un environnement local Mac.
"""

import ipaddress
import os
from pathlib import Path
from typing import Final


def _env_bool(name: str, default: bool = False) -> bool:
    """Parse un booléen d'environnement sans valeur implicite permissive."""
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_csv(name: str, default: str = "") -> list[str]:
    """Retourne les valeurs CSV non vides d'une variable d'environnement."""
    return [value.strip() for value in os.getenv(name, default).split(",") if value.strip()]

# ── Chemins ──────────────────────────────────────────────────
BASE_DIR: Final = Path(__file__).resolve().parent
ROOT_DIR: Final = BASE_DIR.parent
DB_PATH: Final = str(ROOT_DIR / "data" / "jarvis.db")
LOGS_DIR: Final = str(ROOT_DIR / "logs")
FONTS_DIR: Final = str(BASE_DIR / "static" / "assets" / "fonts")
# ── Serveur HTTP ─────────────────────────────────────────────
TV_HOST: Final[str] = os.getenv("TV_HOST", "127.0.0.1")
TV_PORT: Final[int] = int(os.getenv("TV_PORT", "5174"))
TV_ALLOW_NETWORK_BIND: Final[bool] = _env_bool("TV_ALLOW_NETWORK_BIND")

# ── Backend principal JARVIS ─────────────────────────────────
BACKEND_HOST: Final[str] = os.getenv("BACKEND_HOST", "127.0.0.1")
BACKEND_PORT: Final[int] = int(os.getenv("BACKEND_PORT", "8081"))
BACKEND_BASE_URL: Final[str] = f"http://{BACKEND_HOST}:{BACKEND_PORT}"

# ── Sécurité ──────────────────────────────────────────────────
# Le dashboard contient des données personnelles. Son jeton dédié est donc
# obligatoire, y compris sur loopback. Un bind réseau demande en plus un opt-in.
TV_AUTH_TOKEN: Final[str] = os.getenv("TV_AUTH_TOKEN", "").strip()
TV_AUTH_COOKIE_NAME: Final[str] = "jarvis_tv_session"
TV_COOKIE_SECURE: Final[bool] = _env_bool("TV_COOKIE_SECURE")
MIN_TV_AUTH_TOKEN_LENGTH: Final[int] = 32

# Aucun réseau LAN/Tailscale n'est approuvé implicitement. L'opérateur doit
# déclarer à la fois le bind et les réseaux clients autorisés.
WHITELIST_NETWORKS: Final[list[str]] = _env_csv(
    "TV_ALLOWED_NETWORKS",
    "127.0.0.0/8,::1/128",
)

# X-Forwarded-For n'est consulté que si le pair TCP direct appartient à cette
# liste. Elle reste vide par défaut pour ne jamais faire confiance aux clients.
TRUSTED_PROXY_NETWORKS: Final[list[str]] = _env_csv("TV_TRUSTED_PROXIES")


def is_loopback_bind(host: str) -> bool:
    """Indique si ``host`` désigne explicitement la boucle locale."""
    normalized = host.strip().strip("[]").lower()
    if normalized == "localhost":
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def validate_security_config(
    *,
    host: str | None = None,
    auth_token: str | None = None,
    allow_network_bind: bool | None = None,
) -> None:
    """Refuse toute configuration TV qui ouvrirait une frontière non protégée."""
    bind_host = TV_HOST if host is None else host
    token = TV_AUTH_TOKEN if auth_token is None else auth_token
    network_opt_in = TV_ALLOW_NETWORK_BIND if allow_network_bind is None else allow_network_bind

    if len(token.strip()) < MIN_TV_AUTH_TOKEN_LENGTH:
        raise RuntimeError(
            f"TV_AUTH_TOKEN doit contenir au moins {MIN_TV_AUTH_TOKEN_LENGTH} caractères"
        )
    if not is_loopback_bind(bind_host) and not network_opt_in:
        raise RuntimeError(
            "Bind TV réseau refusé: définir TV_ALLOW_NETWORK_BIND=true et une allowlist explicite"
        )

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
