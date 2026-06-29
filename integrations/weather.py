"""Météo via OpenWeatherMap (gratuit avec clé API).

Pas d'OAuth — juste une clé dans WEATHER_API_KEY.
Endpoint courant + forecast 5 jours / 3h.
"""

import logging
from collections import defaultdict
from datetime import datetime

import httpx

import config

logger = logging.getLogger(__name__)

BASE_URL = "https://api.openweathermap.org/data/2.5"
TIMEOUT = 10.0

# Code OpenWeatherMap → emoji. Les codes sont des entiers (200..804)
# https://openweathermap.org/weather-conditions
def _icon_for_code(code: int) -> str:
    if 200 <= code <= 232:
        return "⛈️"
    if 300 <= code <= 321:
        return "🌦️"
    if 500 <= code <= 531:
        return "🌧️"
    if 600 <= code <= 622:
        return "❄️"
    if 700 <= code <= 781:
        return "🌫️"
    if code == 800:
        return "☀️"
    if code == 801:
        return "🌤️"
    if 802 <= code <= 803:
        return "⛅"
    if code == 804:
        return "☁️"
    return "🌡️"


class WeatherClient:
    """Client OpenWeatherMap stateless."""

    def __init__(self):
        self.api_key = config.WEATHER_API_KEY
        self.default_city = config.WEATHER_CITY
        if not self.api_key:
            logger.warning("[Weather] Pas de WEATHER_API_KEY → météo désactivée")

    def is_available(self) -> bool:
        return bool(self.api_key)

    async def _get(self, path: str, params: dict) -> dict | None:
        params = {**params, "appid": self.api_key, "units": "metric", "lang": "fr"}
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                r = await client.get(f"{BASE_URL}/{path}", params=params)
                if r.status_code == 401:
                    logger.error("[Weather] Clé API invalide (401)")
                    return None
                if r.status_code == 404:
                    logger.warning(f"[Weather] Ville inconnue : {params.get('q')}")
                    return None
                r.raise_for_status()
                return r.json()
        except httpx.HTTPError as e:
            logger.error(f"[Weather] Erreur HTTP : {e}")
            return None

    async def get_current(self, city: str = None) -> dict | None:
        """Météo actuelle. Retourne `None` en cas d'erreur."""
        if not self.is_available():
            return None
        city = city or self.default_city
        data = await self._get("weather", {"q": city})
        if not data:
            return None

        weather = (data.get("weather") or [{}])[0]
        main = data.get("main", {})
        wind = data.get("wind", {})

        return {
            "city": data.get("name", city),
            "temp": round(main.get("temp", 0), 1),
            "feels_like": round(main.get("feels_like", 0), 1),
            "description": weather.get("description", ""),
            "humidity": main.get("humidity", 0),
            "wind_speed": round(wind.get("speed", 0) * 3.6, 1),  # m/s → km/h
            "icon": _icon_for_code(weather.get("id", 0)),
        }

    async def get_forecast(self, city: str = None, days: int = 3) -> list[dict]:
        """Prévisions agrégées par jour (min/max). API gratuite = forecast 3h sur 5 jours."""
        if not self.is_available():
            return []
        city = city or self.default_city
        data = await self._get("forecast", {"q": city, "cnt": days * 8})
        if not data or "list" not in data:
            return []

        per_day: dict[str, dict] = defaultdict(lambda: {
            "temps": [], "descriptions": [], "codes": [],
        })

        for entry in data["list"]:
            ts = entry.get("dt")
            if not ts:
                continue
            day = datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
            per_day[day]["temps"].append(entry.get("main", {}).get("temp"))
            w = (entry.get("weather") or [{}])[0]
            per_day[day]["descriptions"].append(w.get("description", ""))
            per_day[day]["codes"].append(w.get("id", 0))

        forecast = []
        for day, agg in sorted(per_day.items())[:days]:
            temps = [t for t in agg["temps"] if t is not None]
            # Description et code les plus représentatifs (mode simple)
            desc = max(set(agg["descriptions"]), key=agg["descriptions"].count) if agg["descriptions"] else ""
            code = max(set(agg["codes"]), key=agg["codes"].count) if agg["codes"] else 0
            forecast.append({
                "date": day,
                "temp_min": round(min(temps), 1) if temps else None,
                "temp_max": round(max(temps), 1) if temps else None,
                "description": desc,
                "icon": _icon_for_code(code),
            })
        return forecast


weather = WeatherClient()
