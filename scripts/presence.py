"""Détection de présence au bureau par le son (micro du daemon audio).

Machine à états minimaliste, zéro LLM :

- absent → un son dépasse ``PRESENCE_NOISE_RMS`` → **arrivée** : ouverture
  d'une session en base, JARVIS salue (« Vous êtes là, Monsieur. »).
- présent → aucun son pendant ``PRESENCE_TIMEOUT_MIN`` (défaut 60 min) →
  **départ** : la session est fermée avec sa durée. Pas d'annonce vocale —
  il n'y a personne pour l'entendre.

``on_sound()`` est appelé par la boucle micro du daemon audio (thread
bloquant, ~50 fois/s quand il y a du bruit) : tout est en mémoire, la base
n'est touchée qu'aux transitions. Le son émis par JARVIS lui-même est déjà
filtré en amont (``_tts_playing_event``). Le contrôle de départ est fait
par ``tick()``, appelé par le scheduler toutes les 10 minutes.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime

import config
from database import close_presence_session, get_db, open_presence_session

logger = logging.getLogger(__name__)


class PresenceDetector:
    """État de présence piloté par les événements sonores du micro."""

    def __init__(self) -> None:
        self.present: bool = False
        self.last_sound: float = 0.0
        self.arrived_at: str | None = None
        self._session_id: int | None = None

    # ── Événements ───────────────────────────────────────────

    def on_sound(self, now: float | None = None) -> str | None:
        """Signale un son ambiant. Retourne "arrived" à la transition, sinon None.

        Appelé depuis le thread micro : rien de coûteux ici, écriture DB
        uniquement à la transition absent → présent.
        """
        if not config.PRESENCE_ENABLED:
            return None
        now = now or time.time()
        self.last_sound = now
        if self.present:
            return None

        self.present = True
        self.arrived_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            self._session_id = open_presence_session(self.arrived_at)
        except Exception as e:
            logger.error("[presence] ouverture session : %s", e)
            self._session_id = None
        logger.info("[presence] Arrivée détectée (%s)", self.arrived_at)
        return "arrived"

    def tick(self, now: float | None = None) -> str | None:
        """Contrôle périodique : ferme la session après le timeout de silence.

        Retourne "left" si un départ vient d'être acté, sinon None.
        """
        if not config.PRESENCE_ENABLED or not self.present:
            return None
        now = now or time.time()
        if now - self.last_sound < config.PRESENCE_TIMEOUT_MIN * 60:
            return None

        self.present = False
        left_at = datetime.fromtimestamp(self.last_sound).strftime("%Y-%m-%d %H:%M:%S")
        if self._session_id is not None:
            try:
                close_presence_session(self._session_id, left_at)
            except Exception as e:
                logger.error("[presence] fermeture session : %s", e)
        logger.info("[presence] Départ acté (dernier son : %s)", left_at)
        self._session_id = None
        self.arrived_at = None
        return "left"

    # ── Lecture ──────────────────────────────────────────────

    def get_status(self) -> dict:
        return {
            "enabled": config.PRESENCE_ENABLED,
            "present": self.present,
            "arrived_at": self.arrived_at,
            "last_sound_ago_s": round(time.time() - self.last_sound) if self.last_sound else None,
            "timeout_min": config.PRESENCE_TIMEOUT_MIN,
        }


def get_today_sessions() -> list[dict]:
    """Sessions de présence du jour (la plus récente en premier)."""
    today = datetime.now().strftime("%Y-%m-%d")
    with get_db() as conn:
        rows = conn.execute(
            """SELECT id, arrived_at, left_at, duration_min FROM presence_sessions
               WHERE DATE(arrived_at) = ? ORDER BY arrived_at DESC""",
            (today,),
        ).fetchall()
        return [dict(r) for r in rows]


presence_detector = PresenceDetector()
