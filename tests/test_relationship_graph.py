"""Tests : graphe vivant des relations (nœuds people, arêtes DB)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture
def tmp_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    db_path = tmp_path / "test_jarvis.db"
    monkeypatch.setattr("config.DB_PATH", str(db_path))
    monkeypatch.setattr("database.DB_PATH", db_path)
    monkeypatch.setattr("config.USER_NAME", "Nolann")
    from database import init_db

    init_db()
    return db_path


def _add_person(name: str, relationship: str = "ami") -> int:
    from database import get_db

    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO people (name, relationship) VALUES (?, ?)", (name, relationship)
        )
        return cur.lastrowid


def test_empty_db_returns_only_user_node(tmp_db):
    from scripts.relationship_graph import build_relationship_graph

    graph = build_relationship_graph()
    assert len(graph["nodes"]) == 1
    assert graph["nodes"][0]["id"] == "user"
    assert graph["nodes"][0]["name"] == "Nolann"
    assert graph["edges"] == []


def test_each_person_gets_a_node_and_user_edge(tmp_db):
    from scripts.relationship_graph import build_relationship_graph

    _add_person("Karim")
    _add_person("Léa")

    graph = build_relationship_graph()
    person_nodes = [n for n in graph["nodes"] if n["type"] == "person"]
    assert {n["name"] for n in person_nodes} == {"Karim", "Léa"}

    user_edges = [e for e in graph["edges"] if e["source"] == "user"]
    assert len(user_edges) == 2


def test_cross_insight_creates_person_to_person_edge(tmp_db):
    from database import add_cross_insight
    from scripts.relationship_graph import build_relationship_graph

    pid_karim = _add_person("Karim")
    pid_lea = _add_person("Léa")
    add_cross_insight(
        "shared_pattern", "Karim et Léa évoquent souvent le même sujet",
        people_involved=["Karim", "Léa"],
    )

    graph = build_relationship_graph()
    cross_edges = [e for e in graph["edges"] if e["type"] == "cross_insight"]
    assert len(cross_edges) == 1
    edge = cross_edges[0]
    assert {edge["source"], edge["target"]} == {f"person:{pid_karim}", f"person:{pid_lea}"}


def test_no_cross_edge_without_multi_person_insight(tmp_db):
    from database import add_cross_insight
    from scripts.relationship_graph import build_relationship_graph

    _add_person("Karim")
    add_cross_insight("solo_pattern", "Pattern individuel", people_involved=["Karim"])

    graph = build_relationship_graph()
    cross_edges = [e for e in graph["edges"] if e["type"] == "cross_insight"]
    assert cross_edges == []


def test_cross_insight_ignores_unknown_names(tmp_db):
    from database import add_cross_insight
    from scripts.relationship_graph import build_relationship_graph

    _add_person("Karim")
    add_cross_insight(
        "shared_pattern", "Mentionne un inconnu",
        people_involved=["Karim", "Personne Fantome"],
    )

    graph = build_relationship_graph()
    cross_edges = [e for e in graph["edges"] if e["type"] == "cross_insight"]
    assert cross_edges == []


def test_person_node_includes_relationship_profile_fields(tmp_db):
    from database import upsert_relationship_profile
    from scripts.relationship_graph import build_relationship_graph

    pid = _add_person("Karim", relationship="frère")
    upsert_relationship_profile(pid, sentiment="positif", trust_level="haute")

    graph = build_relationship_graph()
    karim_node = next(n for n in graph["nodes"] if n["name"] == "Karim")
    assert karim_node["relationship"] == "frère"
    assert karim_node["sentiment"] == "positif"
    assert karim_node["trust_level"] == "haute"


def test_relationship_graph_endpoint(tmp_db):
    import main
    from fastapi.testclient import TestClient

    _add_person("Karim")

    with TestClient(main.app) as client:
        r = client.get("/api/relationship-graph")
    assert r.status_code == 200
    body = r.json()
    assert any(n["name"] == "Karim" for n in body["nodes"])
