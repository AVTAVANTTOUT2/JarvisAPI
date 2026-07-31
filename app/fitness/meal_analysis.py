"""Analyse nutritionnelle de repas — texte libre (DeepSeek) et photo (Ollama vision).

Pipeline photo :
  1. Vision locale (Ollama) → aliments + portions estimées
  2. DeepSeek → macros structurées à partir de la description vision

Pipeline texte :
  1. DeepSeek → JSON structuré (items + totaux)

Les deux chemins convergent vers le même contrat ``MealAnalysisResult``.
"""

from __future__ import annotations

import base64
import json
import logging
import re
from pathlib import Path
from typing import Any

import config
from integrations.ollama_client import ollama_generate

logger = logging.getLogger(__name__)

_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*\n?(.*?)\n?```", re.DOTALL | re.IGNORECASE)
_PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"
_MEAL_TYPES = frozenset({"petit_dej", "dejeuner", "diner", "collation"})

MEAL_IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".webp"})


class MealAnalysisError(ValueError):
    """Échec d'analyse nutritionnelle destiné à une réponse HTTP 4xx/503."""

    def __init__(self, detail: str, *, status_code: int = 422):
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


def _load_prompt(name: str) -> str:
    path = _PROMPTS_DIR / name
    return path.read_text(encoding="utf-8").strip()


def _parse_json_object(raw: str) -> dict[str, Any]:
    """Parse tolérant : JSON brut, bloc markdown, ou objet noyé."""
    if not raw or not str(raw).strip():
        raise MealAnalysisError("Réponse d'analyse vide")
    text = str(raw).strip()
    match = _JSON_BLOCK_RE.search(text)
    payload = match.group(1).strip() if match else text
    if not payload.startswith("{"):
        start = payload.find("{")
        end = payload.rfind("}")
        if start == -1 or end <= start:
            raise MealAnalysisError("Réponse d'analyse non JSON")
        payload = payload[start : end + 1]
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise MealAnalysisError(f"JSON d'analyse invalide: {exc}") from exc
    if not isinstance(data, dict):
        raise MealAnalysisError("L'analyse doit retourner un objet JSON")
    return data


def _clamp_float(value: Any, *, low: float, high: float, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number != number:  # NaN
        return default
    return max(low, min(high, number))


def _clamp_int(value: Any, *, low: int, high: int, default: int) -> int:
    try:
        number = int(round(float(value)))
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def _normalize_item(raw: dict[str, Any]) -> dict[str, Any] | None:
    name = str(raw.get("name") or "").strip()
    if not name:
        return None
    calories = _clamp_int(raw.get("calories"), low=0, high=20_000, default=0)
    protein = _clamp_float(raw.get("protein_g"), low=0, high=1_000, default=0.0)
    carbs = _clamp_float(raw.get("carbs_g"), low=0, high=2_000, default=0.0)
    fat = _clamp_float(raw.get("fat_g"), low=0, high=1_000, default=0.0)
    fiber_raw = raw.get("fiber_g")
    fiber = (
        None
        if fiber_raw is None
        else _clamp_float(fiber_raw, low=0, high=500, default=0.0)
    )
    quantity_raw = raw.get("quantity_g")
    quantity_g = (
        None
        if quantity_raw is None
        else _clamp_float(quantity_raw, low=0, high=10_000, default=0.0)
    )
    label = raw.get("quantity_label")
    quantity_label = str(label).strip()[:160] if label else None
    confidence = _clamp_float(raw.get("confidence"), low=0, high=1, default=0.5)
    return {
        "name": name[:160],
        "quantity_g": quantity_g,
        "quantity_label": quantity_label or None,
        "calories": calories,
        "protein_g": round(protein, 1),
        "carbs_g": round(carbs, 1),
        "fat_g": round(fat, 1),
        "fiber_g": None if fiber is None else round(fiber, 1),
        "confidence": round(confidence, 3),
    }


def _sum_macros(items: list[dict[str, Any]]) -> dict[str, Any]:
    calories = sum(int(item["calories"]) for item in items)
    protein = round(sum(float(item["protein_g"]) for item in items), 1)
    carbs = round(sum(float(item["carbs_g"]) for item in items), 1)
    fat = round(sum(float(item["fat_g"]) for item in items), 1)
    fiber_values = [
        float(item["fiber_g"]) for item in items if item.get("fiber_g") is not None
    ]
    fiber = round(sum(fiber_values), 1) if fiber_values else None
    if items and calories > 0:
        weighted = sum(
            float(item["confidence"]) * int(item["calories"]) for item in items
        )
        confidence = round(weighted / calories, 3)
    elif items:
        confidence = round(
            sum(float(item["confidence"]) for item in items) / len(items),
            3,
        )
    else:
        confidence = 0.0
    return {
        "calories_estimate": calories,
        "protein_g": protein,
        "carbs_g": carbs,
        "fat_g": fat,
        "fiber_g": fiber,
        "confidence": confidence,
    }


def normalize_analysis_payload(
    data: dict[str, Any],
    *,
    meal_type_hint: str | None = None,
) -> dict[str, Any]:
    """Valide et normalise le JSON LLM vers le contrat métier interne."""
    raw_items = data.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        raise MealAnalysisError("Aucun aliment identifiable dans l'analyse")
    items: list[dict[str, Any]] = []
    for entry in raw_items:
        if not isinstance(entry, dict):
            continue
        item = _normalize_item(entry)
        if item is not None:
            items.append(item)
    if not items:
        raise MealAnalysisError("Aucun aliment valide après normalisation")

    totals = _sum_macros(items)
    # Si le modèle a fourni des totaux cohérents, on privilégie la somme
    # recalculée (source de vérité) pour éviter les dérives.
    meal_type = data.get("meal_type")
    if meal_type in _MEAL_TYPES:
        resolved_type = str(meal_type)
    elif meal_type_hint in _MEAL_TYPES:
        resolved_type = meal_type_hint
    else:
        resolved_type = None

    description = str(data.get("description") or "").strip()
    if not description:
        description = ", ".join(item["name"] for item in items[:6])
    notes = data.get("notes")
    notes_text = str(notes).strip()[:500] if notes else None

    overall = _clamp_float(
        data.get("confidence"),
        low=0,
        high=1,
        default=float(totals["confidence"]),
    )
    return {
        "meal_type": resolved_type,
        "description": description[:2_000],
        "items": items,
        "calories_estimate": int(totals["calories_estimate"]),
        "protein_g": float(totals["protein_g"]),
        "carbs_g": float(totals["carbs_g"]),
        "fat_g": float(totals["fat_g"]),
        "fiber_g": totals["fiber_g"],
        "confidence": round(overall, 3),
        "notes": notes_text,
    }


async def _deepseek_nutrition(
    user_content: str,
    *,
    meal_type_hint: str | None = None,
) -> dict[str, Any]:
    from llm import chat

    system = _load_prompt("fitness_meal_analyzer.txt")
    hint = (
        f"\nContexte meal_type suggéré: {meal_type_hint}."
        if meal_type_hint in _MEAL_TYPES
        else ""
    )
    result = await chat(
        messages=[{"role": "user", "content": f"{user_content}{hint}"}],
        model=config.DEEPSEEK_MAIN_MODEL,
        system=system,
        max_tokens=int(getattr(config, "FITNESS_MEAL_ANALYSIS_MAX_TOKENS", 2_048)),
        temperature=0.1,
    )
    return normalize_analysis_payload(
        _parse_json_object(result.get("content") or ""),
        meal_type_hint=meal_type_hint,
    )


async def analyze_meal_text(
    text: str,
    *,
    meal_type_hint: str | None = None,
) -> dict[str, Any]:
    """Parse un journal alimentaire libre en repas structuré."""
    cleaned = (text or "").strip()
    if not cleaned:
        raise MealAnalysisError("Texte alimentaire vide")
    if len(cleaned) > 8_000:
        raise MealAnalysisError("Texte alimentaire trop long (max 8000 caractères)")
    analysis = await _deepseek_nutrition(
        f"Journal alimentaire de l'utilisateur:\n{cleaned}",
        meal_type_hint=meal_type_hint,
    )
    analysis["analysis_source"] = "text_ai"
    analysis["raw_input"] = cleaned[:8_000]
    return analysis


async def _vision_identify_foods(image_b64: str) -> dict[str, Any]:
    prompt = _load_prompt("fitness_meal_vision.txt")
    model = getattr(config, "FITNESS_MEAL_VISION_MODEL", None) or config.SCREEN_VISION_MODEL
    try:
        response = await ollama_generate(
            config.OLLAMA_URL,
            model=model,
            prompt=prompt,
            images=[image_b64],
            options={
                "temperature": 0.1,
                "num_predict": int(
                    getattr(config, "FITNESS_MEAL_VISION_MAX_TOKENS", 800)
                ),
            },
            keep_alive="60s",
            timeout=float(getattr(config, "FITNESS_MEAL_VISION_TIMEOUT_S", 90.0)),
        )
    except Exception as exc:  # réseau / modèle absent
        logger.error("[fitness.meal] vision Ollama indisponible: %s", exc)
        raise MealAnalysisError(
            "Analyse photo indisponible (vision locale hors service)",
            status_code=503,
        ) from exc
    raw = response.get("response") if isinstance(response, dict) else None
    data = _parse_json_object(str(raw or ""))
    if data.get("visible") is False:
        raise MealAnalysisError("Aucun repas identifiable sur la photo")
    items = data.get("items")
    if not isinstance(items, list) or not items:
        raise MealAnalysisError("La vision n'a détecté aucun aliment")
    return data


async def analyze_meal_photo(
    image_bytes: bytes,
    *,
    meal_type_hint: str | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    """Identifie l'assiette via vision locale puis estime les macros via DeepSeek."""
    if not image_bytes:
        raise MealAnalysisError("Image vide")
    max_bytes = int(getattr(config, "FITNESS_MEAL_PHOTO_MAX_BYTES", 8_000_000))
    if len(image_bytes) > max_bytes:
        raise MealAnalysisError(
            f"Photo trop lourde (max {max_bytes} octets)",
            status_code=413,
        )

    image_b64 = base64.b64encode(image_bytes).decode("ascii")
    vision = await _vision_identify_foods(image_b64)

    lines = [
        "Estimation nutritionnelle à partir d'une photo d'assiette.",
        f"Description vision: {vision.get('meal_guess') or 'non fournie'}",
        "Aliments détectés:",
    ]
    for entry in vision.get("items") or []:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "").strip()
        if not name:
            continue
        qty = entry.get("quantity_g")
        label = entry.get("quantity_label") or ""
        conf = entry.get("confidence")
        lines.append(
            f"- {name} | ~{qty} g | {label} | confiance vision={conf}"
        )
    if vision.get("notes"):
        lines.append(f"Notes vision: {vision['notes']}")
    if note and str(note).strip():
        lines.append(f"Précision utilisateur: {str(note).strip()[:1_000]}")

    analysis = await _deepseek_nutrition(
        "\n".join(lines),
        meal_type_hint=meal_type_hint,
    )
    # Confiance globale = min(vision, nutrition) pour rester conservateur.
    vision_conf = _clamp_float(vision.get("confidence"), low=0, high=1, default=0.6)
    analysis["confidence"] = round(min(float(analysis["confidence"]), vision_conf), 3)
    analysis["analysis_source"] = "photo_ai"
    analysis["raw_input"] = (note or vision.get("meal_guess") or analysis["description"])[
        :8_000
    ]
    if vision.get("notes"):
        extra = str(vision["notes"]).strip()
        if extra:
            existing = analysis.get("notes") or ""
            analysis["notes"] = f"{existing} | Vision: {extra}".strip(" |")[:500]
    return analysis
