"""Géo localisation pure — haversine et résolution de lieu nommé."""

from __future__ import annotations

from pathlib import Path

import pytest

from database.location_helpers import haversine, resolve_place


def test_haversine_meme_point_est_zero():
    assert haversine(50.6292, 3.0573, 50.6292, 3.0573) == pytest.approx(0.0, abs=1e-6)


def test_haversine_paris_lille_environ_204_km():
    # Coordonnées centre-ville approximatives ; distance routière ~225 km,
    # distance orthodromique ~204 km.
    meters = haversine(48.8566, 2.3522, 50.6292, 3.0573)
    assert meters == pytest.approx(204_000, rel=0.03)


def test_haversine_est_symetrique():
    a = haversine(48.85, 2.35, 50.63, 3.06)
    b = haversine(50.63, 3.06, 48.85, 2.35)
    assert a == pytest.approx(b, abs=1e-6)


@pytest.fixture
def places_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    db_path = tmp_path / "location-geo.db"
    monkeypatch.setattr("config.DB_PATH", str(db_path))
    monkeypatch.setattr("database.DB_PATH", db_path)
    from database import init_db
    from database.location_helpers import create_place

    init_db()
    create_place("Maison", "home", 50.6292, 3.0573, radius=120)
    create_place("Bureau", "work", 50.6400, 3.0700, radius=80)
    return db_path


def test_resolve_place_retourne_le_plus_proche_dans_le_rayon(places_db):
    hit = resolve_place(50.6293, 3.0574)
    assert hit is not None
    assert hit["name"] == "Maison"


def test_resolve_place_hors_rayon_retourne_none(places_db):
    # ~1,5 km au nord de Maison, hors des deux rayons.
    assert resolve_place(50.6430, 3.0573) is None


def test_resolve_place_choisit_le_plus_proche_quand_deux_chevauchent(monkeypatch):
    monkeypatch.setattr(
        "database.location_helpers.get_all_places",
        lambda: [
            {
                "id": 1,
                "name": "Loin",
                "latitude": 50.6300,
                "longitude": 3.0600,
                "radius_meters": 500,
            },
            {
                "id": 2,
                "name": "Pres",
                "latitude": 50.6292,
                "longitude": 3.0573,
                "radius_meters": 500,
            },
        ],
    )
    hit = resolve_place(50.6292, 3.0573)
    assert hit is not None
    assert hit["name"] == "Pres"


def test_resolve_place_ignore_coordonnees_invalides(monkeypatch):
    monkeypatch.setattr(
        "database.location_helpers.get_all_places",
        lambda: [
            {
                "id": 1,
                "name": "Cassé",
                "latitude": "pas-un-nombre",
                "longitude": 3.0,
                "radius_meters": 100,
            },
            {
                "id": 2,
                "name": "Valide",
                "latitude": 50.0,
                "longitude": 3.0,
                "radius_meters": 200,
            },
        ],
    )
    hit = resolve_place(50.0001, 3.0001)
    assert hit is not None
    assert hit["name"] == "Valide"
