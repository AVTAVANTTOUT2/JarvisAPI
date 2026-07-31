"""Parser vocal fitness déterministe, étroit et strictement fail-open."""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import time
import unicodedata
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal

from pydantic import ValidationError

from .models import MealCreate, WaterCreate, WellbeingCreate, WorkoutCreate
from .services import FitnessService, current_local_date, fitness_service

logger = logging.getLogger("jarvis")

VoiceMethod = Literal["GET", "POST"]

_WORKOUT_ALIASES = {
    "poussee": ("poussee", "poussée"),
    "tirage": ("tirage", "tirage"),
    "dos": ("tirage", "dos"),
    "jambes": ("jambes", "jambes"),
    "full body": ("full_body", "full body"),
    "natation": ("natation", "natation"),
}
_PAST_CONTEXT_PATTERN = re.compile(
    r"\b(hier|avant hier|la semaine derniere|le mois dernier|autrefois|quand)\b"
)
_JOURNAL_CONTEXT_TTL_SECONDS = 90.0


def _fold(text: str) -> str:
    """Normalise accents, apostrophes et espaces sans matching approximatif."""
    normalized = unicodedata.normalize("NFKD", text or "")
    without_accents = "".join(
        character for character in normalized if not unicodedata.combining(character)
    )
    without_accents = without_accents.lower().replace("’", "'")
    without_accents = re.sub(r"['-]", " ", without_accents)
    without_accents = re.sub(r"[!?;:]", " ", without_accents)
    return re.sub(r"\s+", " ", without_accents).strip(" .,")


def _strip_wake_word(text: str) -> str:
    """Retire uniquement un wake word placé au début de la transcription."""
    return re.sub(
        r"^\s*jarvis\s*[,;:]?\s*",
        "",
        text or "",
        count=1,
        flags=re.IGNORECASE,
    ).strip()


@dataclass(frozen=True, slots=True)
class FitnessVoiceIntent:
    """Mapping explicite d'une transcription vers un contrat HTTP fitness."""

    method: VoiceMethod | None
    endpoint: str | None
    payload: dict[str, Any]
    confirmation: str


class FitnessVoiceParser:
    """Reconnaît uniquement les formulations fitness explicites et ancrées."""

    def __init__(self) -> None:
        self._wellbeing_contexts: dict[int, float] = {}

    def _post_intent(
        self,
        endpoint: str,
        today: date,
        payload: dict[str, Any],
        confirmation: str,
    ) -> FitnessVoiceIntent:
        return FitnessVoiceIntent(
            method="POST",
            endpoint=endpoint,
            payload={
                "date": today.isoformat(),
                **payload,
                "source": "voice",
            },
            confirmation=confirmation,
        )

    def _parse_workout(
        self,
        folded: str,
        today: date,
    ) -> FitnessVoiceIntent | None:
        match = re.fullmatch(
            r"(?:note|enregistre|ajoute)(?: moi)? "
            r"(?:ma|une) seance(?: de| du)? "
            r"(poussee|tirage|dos|jambes|full body|natation)"
            r"(?: aujourd hui)?",
            folded,
        )
        if match is None:
            return None
        workout_type, spoken_label = _WORKOUT_ALIASES[match.group(1)]
        return self._post_intent(
            "/api/fitness/workouts",
            today,
            {"type": workout_type},
            f"Séance {spoken_label} enregistrée.",
        )

    def _parse_scheduled_session(
        self,
        folded: str,
        today: date,
    ) -> FitnessVoiceIntent | None:
        if re.fullmatch(
            r"(?:j ai fait|je viens de finir|marque comme fait|valide) "
            r"(?:(?:ma|la) seance|mon sport|mon entrainement)(?: aujourd hui)?",
            folded,
        ):
            return self._post_intent(
                "/api/fitness/sessions/today/complete",
                today,
                {"status": "done"},
                "Séance du jour marquée comme faite.",
            )
        if re.fullmatch(
            r"(?:je n ai pas fait|marque comme non fait|j ai rate) "
            r"(?:(?:ma|la) seance|mon sport|mon entrainement)(?: aujourd hui)?",
            folded,
        ):
            return self._post_intent(
                "/api/fitness/sessions/today/skip",
                today,
                {"status": "skipped"},
                "Séance du jour marquée comme non faite.",
            )
        return None

    def _parse_meal(
        self,
        original: str,
        folded: str,
        today: date,
    ) -> FitnessVoiceIntent | None:
        match = re.fullmatch(
            r"(?:j ai mange|je viens de manger|"
            r"(?:note|enregistre)(?: moi)? (?:mon repas|que j ai mange)) "
            r"(.+)",
            folded,
        )
        if match is None:
            return None
        description_folded = match.group(1).strip()
        if _PAST_CONTEXT_PATTERN.search(
            description_folded
        ) or description_folded.startswith("avec "):
            return None

        original_match = re.match(
            r"^(?:j['’]ai\s+mang[ée]|je\s+viens\s+de\s+manger|"
            r"(?:note|enregistre)(?:-moi)?\s+"
            r"(?:mon\s+repas|que\s+j['’]ai\s+mang[ée]))\s+(.+?)\s*[.!]?$",
            original,
            flags=re.IGNORECASE,
        )
        if original_match is None:
            return None
        description = original_match.group(1).strip()
        calories: int | None = None
        calories_match = re.search(
            r"[, ]+(?:environ\s+)?(\d{1,5})\s*(?:kcal|calories?)$",
            description,
            flags=re.IGNORECASE,
        )
        if calories_match is not None:
            calories = int(calories_match.group(1))
            description = description[: calories_match.start()].rstrip(" ,")
        if not description:
            return None

        return self._post_intent(
            "/api/fitness/meals",
            today,
            {
                "meal_type": None,
                "description": description,
                "calories_estimate": calories,
            },
            "Repas enregistré.",
        )

    @staticmethod
    def _water_amount_ml(amount: str, unit: str) -> int | None:
        """Convertit uniquement les quantités et unités explicitement reconnues."""
        word_values = {
            "un": 1.0,
            "une": 1.0,
            "un demi": 0.5,
            "une demi": 0.5,
        }
        try:
            value = (
                word_values[amount]
                if amount in word_values
                else float(amount.replace(",", "."))
            )
        except ValueError:
            return None
        multipliers = {
            "ml": 1,
            "millilitre": 1,
            "millilitres": 1,
            "cl": 10,
            "centilitre": 10,
            "centilitres": 10,
            "l": 1_000,
            "litre": 1_000,
            "litres": 1_000,
        }
        converted = round(value * multipliers[unit])
        return converted if 0 < converted <= 20_000 else None

    def _parse_water(
        self,
        folded: str,
        today: date,
    ) -> FitnessVoiceIntent | None:
        prefix = (
            r"(?:j ai bu|note(?: moi)?(?: que)? j ai bu|enregistre(?: que)? j ai bu)"
        )
        vessel_match = re.fullmatch(
            prefix + r" (un verre|une bouteille)(?: d eau)?",
            folded,
        )
        if vessel_match is not None:
            amount_ml = 250 if vessel_match.group(1) == "un verre" else 500
            return self._post_intent(
                "/api/fitness/water",
                today,
                {"amount_ml": amount_ml},
                f"{amount_ml} millilitres d'eau enregistrés.",
            )

        quantity_match = re.fullmatch(
            prefix + r" (un demi|une demi|un|une|\d+(?:[.,]\d+)?) ?"
            r"(ml|millilitres?|cl|centilitres?|l|litres?)"
            r"(?: d eau)?",
            folded,
        )
        if quantity_match is not None:
            amount_ml = self._water_amount_ml(
                quantity_match.group(1),
                quantity_match.group(2),
            )
            if amount_ml is None:
                return FitnessVoiceIntent(
                    method=None,
                    endpoint=None,
                    payload={},
                    confirmation="Quelle quantité d'eau avez-vous bue ?",
                )
            return self._post_intent(
                "/api/fitness/water",
                today,
                {"amount_ml": amount_ml},
                f"{amount_ml} millilitres d'eau enregistrés.",
            )

        if re.fullmatch(
            r"(?:j ai bu de l eau|note mon hydratation|enregistre mon hydratation)",
            folded,
        ):
            return FitnessVoiceIntent(
                method=None,
                endpoint=None,
                payload={},
                confirmation="Quelle quantité d'eau avez-vous bue ?",
            )
        return None

    def _parse_wellbeing(
        self,
        original: str,
        folded: str,
        conversation_id: int,
        today: date,
    ) -> FitnessVoiceIntent | None:
        rating_match = re.fullmatch(
            r"(?:mon bien etre|(?:note|enregistre)(?: moi)? mon bien etre)"
            r"(?: est)?(?: a| de)? (10|[1-9])(?: sur 10)?(?: aujourd hui)?",
            folded,
        )
        if rating_match is not None:
            rating = int(rating_match.group(1))
            return self._post_intent(
                "/api/fitness/wellbeing",
                today,
                {"rating": rating, "journal_text": None},
                f"Bien-être noté à {rating} sur 10.",
            )

        journal_match = re.match(
            r"^(?:note|enregistre|ajoute)(?:-moi)?\s+"
            r"(?:dans\s+)?(?:mon\s+)?journal\s+(?:de\s+)?bien[- ]être"
            r"(?:\s+que|\s*:)?\s+(.+?)\s*[.!]?$",
            original,
            flags=re.IGNORECASE,
        )
        if journal_match is not None:
            journal_text = journal_match.group(1).strip()
            return self._post_intent(
                "/api/fitness/wellbeing",
                today,
                {"rating": None, "journal_text": journal_text},
                "Journal de bien-être enregistré.",
            )

        if re.fullmatch(
            r"(?:ouvre|demarre|commence) (?:mon )?journal(?: de)? bien etre",
            folded,
        ):
            self._wellbeing_contexts[conversation_id] = (
                time.monotonic() + _JOURNAL_CONTEXT_TTL_SECONDS
            )
            return FitnessVoiceIntent(
                method=None,
                endpoint=None,
                payload={},
                confirmation="Que souhaitez-vous noter dans votre journal de bien-être ?",
            )
        return None

    def parse(
        self,
        text: str,
        *,
        conversation_id: int,
        today: date | None = None,
    ) -> FitnessVoiceIntent | None:
        """Retourne une intention uniquement pour un match explicite complet."""
        original = _strip_wake_word(text)
        if not original:
            return None
        folded = _fold(original)
        local_today = today or current_local_date()

        for parser in (
            lambda: self._parse_scheduled_session(folded, local_today),
            lambda: self._parse_workout(folded, local_today),
            lambda: self._parse_meal(original, folded, local_today),
            lambda: self._parse_water(folded, local_today),
            lambda: self._parse_wellbeing(
                original,
                folded,
                conversation_id,
                local_today,
            ),
        ):
            intent = parser()
            if intent is not None:
                return intent

        if re.fullmatch(
            r"(?:(?:resume|fais moi le resume de) ma journee(?: fitness| sante)?|"
            r"comment(?: est ce que)? je me porte aujourd hui)",
            folded,
        ):
            return FitnessVoiceIntent(
                method="GET",
                endpoint="/api/fitness/summary/today",
                payload={},
                confirmation="",
            )

        if re.fullmatch(
            r"(?:quel est|donne moi|affiche) (?:mon )?programme(?: de sport)? "
            r"(?:du jour|aujourd hui)",
            folded,
        ):
            return FitnessVoiceIntent(
                method="GET",
                endpoint="/api/fitness/dashboard",
                payload={},
                confirmation="",
            )

        expires_at = self._wellbeing_contexts.get(conversation_id)
        if expires_at is None:
            return None
        del self._wellbeing_contexts[conversation_id]
        if expires_at < time.monotonic():
            return None
        return self._post_intent(
            "/api/fitness/wellbeing",
            local_today,
            {"rating": None, "journal_text": original},
            "Journal de bien-être enregistré.",
        )


def _format_summary(service: FitnessService) -> str:
    """Formule une synthèse courte destinée au TTS existant."""
    summary = service.summary_today()
    workout = (
        f"{summary.workout_count} séance{'s' if summary.workout_count > 1 else ''}"
        if summary.workout_done
        else "aucune séance"
    )
    water = (
        f"{summary.water_ml / 1000:g} litre{'s' if summary.water_ml >= 2000 else ''}"
        if summary.water_ml >= 1000
        else f"{summary.water_ml} millilitres"
    )
    wellbeing = (
        f" Bien-être à {summary.wellbeing.rating} sur 10."
        if summary.wellbeing is not None and summary.wellbeing.rating is not None
        else ""
    )
    return (
        f"Aujourd'hui : {workout}, {summary.meal_count} repas, "
        f"environ {summary.calories_estimate} kilocalories et {water} d'eau."
        f"{wellbeing}"
    )


def _format_daily_program(service: FitnessService) -> str:
    dashboard = service.dashboard()
    session = dashboard.scheduled_session
    if session is None:
        next_title = dashboard.next_session.title if dashboard.next_session else "aucune"
        return f"Aucune séance prévue aujourd'hui. Prochaine séance : {next_title}."
    status = dashboard.progress.status.value if dashboard.progress else "planned"
    if status == "done":
        return f"La séance {session.title} est déjà marquée comme faite aujourd'hui."
    exercise_names = ", ".join(item.name for item in session.exercises)
    return f"Aujourd'hui, séance {session.title} : {exercise_names}."


def _result(
    intent: FitnessVoiceIntent,
    reply: str,
    *,
    input_text: str,
    stt_ms: int,
) -> dict[str, Any]:
    """Construit le contrat de réponse déjà consommé par Kokoro TTS."""
    action = (
        {
            "method": intent.method,
            "endpoint": intent.endpoint,
            "payload": intent.payload,
        }
        if intent.endpoint is not None
        else None
    )
    return {
        "text": reply,
        "emotion": "neutral",
        "cost": 0.0,
        "action": action,
        "latency_ms": int(stt_ms or 0),
        "debug_trace": {
            "input_text": input_text,
            "response_clean": reply,
            "model": "fitness-deterministic",
            "latency_stt_ms": int(stt_ms or 0),
            "action_detected": action,
        },
    }


voice_parser = FitnessVoiceParser()


def maybe_handle_fitness_voice(
    text: str,
    conversation_id: int,
    *,
    stt_ms: int = 0,
    service: FitnessService = fitness_service,
) -> dict[str, Any] | None:
    """Traite un match fitness fort ; retourne ``None`` au moindre non-match."""
    intent = voice_parser.parse(text, conversation_id=conversation_id)
    if intent is None:
        return None
    if intent.endpoint is None:
        return _result(
            intent,
            intent.confirmation,
            input_text=text,
            stt_ms=stt_ms,
        )

    try:
        if intent.endpoint == "/api/fitness/workouts":
            service.create_workout(
                WorkoutCreate.model_validate_json(json.dumps(intent.payload))
            )
            reply = intent.confirmation
        elif intent.endpoint == "/api/fitness/meals":
            service.create_meal(
                MealCreate.model_validate_json(json.dumps(intent.payload))
            )
            reply = intent.confirmation
        elif intent.endpoint == "/api/fitness/water":
            service.create_water(
                WaterCreate.model_validate_json(json.dumps(intent.payload))
            )
            reply = intent.confirmation
        elif intent.endpoint == "/api/fitness/wellbeing":
            service.create_wellbeing(
                WellbeingCreate.model_validate_json(json.dumps(intent.payload))
            )
            reply = intent.confirmation
        elif intent.endpoint == "/api/fitness/summary/today":
            reply = _format_summary(service)
        elif intent.endpoint == "/api/fitness/dashboard":
            reply = _format_daily_program(service)
        elif intent.endpoint == "/api/fitness/sessions/today/complete":
            service.set_scheduled_session_status("done")
            reply = intent.confirmation
        elif intent.endpoint == "/api/fitness/sessions/today/skip":
            service.set_scheduled_session_status("skipped")
            reply = intent.confirmation
        else:
            return None
    except (ValidationError, ValueError, sqlite3.Error) as error:
        logger.error("[fitness.voice] commande reconnue mais non exécutée : %s", error)
        reply = "Je n'ai pas pu enregistrer cette donnée fitness."
    return _result(intent, reply, input_text=text, stt_ms=stt_ms)
