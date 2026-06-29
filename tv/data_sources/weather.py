"""Données météo — Open-Meteo API (gratuit, sans clé).

Récupère les conditions actuelles et prévisions 3 jours pour Lille.
Cache interne de 15 minutes géré par le serveur.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

import config as cfg

logger = logging.getLogger(__name__)

# Mapping codes météo Open-Meteo → description + icône ASCII
WMO_CODES: dict[int, str] = {
    0: "☀ Clair",
    1: "🌤 Plutôt clair",
    2: "⛅ Partiellement nuageux",
    3: "☁ Couvert",
    45: "🌫 Brouillard",
    48: "🌫 Brouillard givrant",
    51: "🌧 Bruine légère",
    53: "🌧 Bruine modérée",
    55: "🌧 Bruine dense",
    61: "🌧 Pluie légère",
    63: "🌧 Pluie modérée",
    65: "🌧 Pluie forte",
    71: "❄ Neige légère",
    73: "❄ Neige modérée",
    75: "❄ Neige forte",
    77: "❄ Grains de neige",
    80: "🌧 Averses légères",
    81: "🌧 Averses modérées",
    82: "🌧 Averses violentes",
    85: "❄ Averses de neige légères",
    86: "❄ Averses de neige fortes",
    95: "⛈ Orage",
    96: "⛈ Orage avec grêle légère",
    99: "⛈ Orage avec grêle forte",
}

OPEN_METEO_URL = (
    "https://api.open-meteo.com/v1/forecast"
    "?latitude={lat}&longitude={lon}"
    "&current=temperature_2m,wind_speed_10m,weather_code"
    "&daily=temperature_2m_max,temperature_2m_min,weather_code"
    "&timezone={tz}&forecast_days=3"
)


def fetch_weather() -> dict[str, Any]:
    """Appelle Open-Meteo et retourne les données formatées."""
    url = OPEN_METEO_URL.format(
        lat=cfg.WEATHER_LAT,
        lon=cfg.WEATHER_LON,
        tz=cfg.TIMEZONE,
    )
    try:
        resp = httpx.get(url, timeout=10.0)
        resp.raise_for_status()
        raw = resp.json()
    except Exception as exc:
        logger.warning("Open-Meteo indisponible: %s", exc)
        return {"ok": False, "error": str(exc)}

    current = raw.get("current", {})
    daily = raw.get("daily", {})

    current_code = current.get("weather_code", 0)
    wind = current.get("wind_speed_10m", 0)

    forecast: list[dict[str, Any]] = []
    dates = daily.get("time", [])
    tmaxs = daily.get("temperature_2m_max", [])
    tmins = daily.get("temperature_2m_min", [])
    codes = daily.get("weather_code", [])
    for i in range(min(len(dates), len(tmaxs), len(tmins), len(codes))):
        forecast.append({
            "date": dates[i],
            "max": tmaxs[i],
            "min": tmins[i],
            "description": WMO_CODES.get(codes[i], "Inconnu") if isinstance(codes[i], int) else "Inconnu",
        })

    return {
        "ok": True,
        "city": "Lille",
        "current": {
            "temperature": current.get("temperature_2m"),
            "wind_speed": wind,
            "description": WMO_CODES.get(current_code, "Inconnu") if isinstance(current_code, int) else "Inconnu",
        },
        "forecast": forecast,
    }
