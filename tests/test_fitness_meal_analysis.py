"""Analyse nutritionnelle fitness — normalisation, texte IA et photo."""

from __future__ import annotations

import io
from datetime import date
from pathlib import Path

import pytest
from PIL import Image

from tests.conftest import authenticate


@pytest.fixture
def fitness_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    db_path = tmp_path / "fitness_meals.db"
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    monkeypatch.setattr("config.DB_PATH", str(db_path))
    monkeypatch.setattr("database.DB_PATH", db_path)
    monkeypatch.setattr("config.UPLOAD_DIR", str(upload_dir))

    from database import init_db

    init_db()
    return db_path


def _client():
    from fastapi.testclient import TestClient

    import main

    return TestClient(main.app)


def _tiny_jpeg() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (32, 32), color=(180, 90, 40)).save(buffer, format="JPEG")
    return buffer.getvalue()


SAMPLE_ANALYSIS = {
    "meal_type": "dejeuner",
    "description": "Poulet riz brocolis",
    "items": [
        {
            "name": "Poulet grillé",
            "quantity_g": 150,
            "quantity_label": "150 g",
            "calories": 250,
            "protein_g": 40,
            "carbs_g": 0,
            "fat_g": 8,
            "fiber_g": 0,
            "confidence": 0.9,
        },
        {
            "name": "Riz",
            "quantity_g": 180,
            "quantity_label": "1 bol",
            "calories": 230,
            "protein_g": 5,
            "carbs_g": 50,
            "fat_g": 1,
            "fiber_g": 1,
            "confidence": 0.8,
        },
    ],
    "calories_estimate": 999,
    "protein_g": 99,
    "carbs_g": 99,
    "fat_g": 99,
    "fiber_g": 99,
    "confidence": 0.5,
    "notes": None,
}


def test_normalize_analysis_recomputes_totals() -> None:
    from app.fitness.meal_analysis import normalize_analysis_payload

    result = normalize_analysis_payload(SAMPLE_ANALYSIS)
    assert result["calories_estimate"] == 480
    assert result["protein_g"] == 45.0
    assert result["carbs_g"] == 50.0
    assert result["fat_g"] == 9.0
    assert len(result["items"]) == 2


def test_normalize_analysis_rejects_empty_items() -> None:
    from app.fitness.meal_analysis import MealAnalysisError, normalize_analysis_payload

    with pytest.raises(MealAnalysisError):
        normalize_analysis_payload({"items": [], "description": "vide"})


def test_meal_schema_has_nutrition_enrichment_columns(fitness_db: Path) -> None:
    import sqlite3

    with sqlite3.connect(fitness_db) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(meals)")}
    assert {
        "carbs_g",
        "fat_g",
        "fiber_g",
        "items_json",
        "photo_path",
        "analysis_source",
        "confidence",
        "raw_input",
    } <= columns


def test_manual_meal_persists_extended_macros(fitness_db: Path) -> None:
    today = date.today().isoformat()
    with _client() as client:
        authenticate(client)
        created = client.post(
            "/api/fitness/meals",
            json={
                "date": today,
                "meal_type": "diner",
                "description": "Pâtes bolognaise",
                "calories_estimate": 700,
                "protein_g": 35,
                "carbs_g": 80,
                "fat_g": 22,
                "source": "pwa",
            },
        )
    assert created.status_code == 201
    body = created.json()
    assert body["carbs_g"] == 80
    assert body["fat_g"] == 22
    assert body["analysis_source"] == "manual"
    assert body["has_photo"] is False


def test_create_meal_from_text_persists_structured_items(
    fitness_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_analyze(text: str, *, meal_type_hint: str | None = None):
        payload = dict(SAMPLE_ANALYSIS)
        from app.fitness.meal_analysis import normalize_analysis_payload

        normalized = normalize_analysis_payload(payload, meal_type_hint=meal_type_hint)
        normalized["analysis_source"] = "text_ai"
        normalized["raw_input"] = text
        return normalized

    monkeypatch.setattr("app.fitness.meal_analysis.analyze_meal_text", fake_analyze)

    today = date.today().isoformat()
    with _client() as client:
        authenticate(client)
        response = client.post(
            "/api/fitness/meals/from-text",
            json={
                "date": today,
                "text": "Poulet 150g et riz",
                "meal_type": "dejeuner",
                "source": "pwa",
                "save": True,
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["persisted"] is True
    assert body["meal"]["analysis_source"] == "text_ai"
    assert body["meal"]["calories_estimate"] == 480
    assert len(body["meal"]["items"]) == 2
    assert body["meal"]["items"][0]["name"]


def test_create_meal_from_photo_stores_image_and_meal(
    fitness_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_photo(image_bytes: bytes, *, meal_type_hint=None, note=None):
        assert image_bytes
        from app.fitness.meal_analysis import normalize_analysis_payload

        normalized = normalize_analysis_payload(
            SAMPLE_ANALYSIS, meal_type_hint=meal_type_hint
        )
        normalized["analysis_source"] = "photo_ai"
        normalized["raw_input"] = note or "photo"
        return normalized

    monkeypatch.setattr("app.fitness.meal_analysis.analyze_meal_photo", fake_photo)

    today = date.today().isoformat()
    with _client() as client:
        authenticate(client)
        response = client.post(
            "/api/fitness/meals/from-photo",
            data={
                "date": today,
                "meal_type": "dejeuner",
                "source": "pwa",
                "note": "un peu d'huile d'olive",
                "save": "true",
            },
            files={"photo": ("assiette.jpg", _tiny_jpeg(), "image/jpeg")},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["persisted"] is True
        meal_id = body["meal"]["id"]
        assert body["meal"]["has_photo"] is True
        assert body["meal"]["photo_path"].startswith("fitness/meals/")
        photo = client.get(f"/api/fitness/meals/{meal_id}/photo")
        assert photo.status_code == 200
        assert photo.headers["content-type"].startswith("image/")


def test_ollama_allowlist_includes_meal_analysis() -> None:
    from jarvis.cognitive.ollama_guard import OLLAMA_ALLOWED_MODULES

    assert "app/fitness/meal_analysis.py" in OLLAMA_ALLOWED_MODULES
