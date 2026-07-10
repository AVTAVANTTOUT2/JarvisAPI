"""Tests : recherche sémantique (similarité cosinus, stockage embeddings).

Le modèle sentence-transformers réel n'est jamais chargé dans ces tests
(aucun accès réseau pour télécharger les poids en environnement de test) —
`embed_text` est mocké pour retourner des vecteurs déterministes, ce qui
permet de vérifier tout le reste du pipeline (cosinus, stockage, classement)
sans dépendre du modèle lui-même.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture
def tmp_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    db_path = tmp_path / "test_jarvis.db"
    monkeypatch.setattr("config.DB_PATH", str(db_path))
    monkeypatch.setattr("database.DB_PATH", db_path)
    from database import init_db

    init_db()
    return db_path


# ── Similarité cosinus (fonction pure) ─────────────────────────

def test_cosine_similarity_identical_vectors_is_one():
    from scripts.semantic_search import cosine_similarity

    v = np.array([1.0, 2.0, 3.0])
    assert cosine_similarity(v, v) == pytest.approx(1.0)


def test_cosine_similarity_orthogonal_vectors_is_zero():
    from scripts.semantic_search import cosine_similarity

    a = np.array([1.0, 0.0])
    b = np.array([0.0, 1.0])
    assert cosine_similarity(a, b) == pytest.approx(0.0)


def test_cosine_similarity_opposite_vectors_is_minus_one():
    from scripts.semantic_search import cosine_similarity

    a = np.array([1.0, 0.0])
    b = np.array([-1.0, 0.0])
    assert cosine_similarity(a, b) == pytest.approx(-1.0)


def test_cosine_similarity_zero_vector_is_zero_not_nan():
    from scripts.semantic_search import cosine_similarity

    a = np.array([0.0, 0.0])
    b = np.array([1.0, 2.0])
    assert cosine_similarity(a, b) == 0.0


# ── Sérialisation embedding <-> blob ───────────────────────────

def test_embedding_blob_roundtrip():
    from scripts.semantic_search import blob_to_embedding, embedding_to_blob

    vec = np.array([0.1, -0.2, 0.3, 0.4], dtype=np.float32)
    blob = embedding_to_blob(vec)
    assert isinstance(blob, bytes)
    recovered = blob_to_embedding(blob)
    np.testing.assert_array_almost_equal(recovered, vec)


# ── index_text / semantic_search (embed_text mocké, DB réelle) ─

def _fake_embed(text: str) -> np.ndarray:
    """Vecteur déterministe dérivé du texte — même texte -> même vecteur."""
    seed = sum(ord(c) for c in text) % 1000
    rng = np.random.RandomState(seed)
    return rng.rand(8).astype(np.float32)


def test_index_text_stores_embedding(tmp_db):
    from database import get_all_memory_embeddings
    from scripts.semantic_search import index_text

    with patch("scripts.semantic_search.embed_text", side_effect=_fake_embed):
        index_text("episode", 1, "Jean a parlé du projet de vacances en Espagne")

    rows = get_all_memory_embeddings()
    assert len(rows) == 1
    assert rows[0]["source_type"] == "episode"
    assert rows[0]["source_id"] == 1


def test_index_text_skips_empty_text(tmp_db):
    from database import get_all_memory_embeddings
    from scripts.semantic_search import index_text

    with patch("scripts.semantic_search.embed_text", side_effect=_fake_embed):
        index_text("episode", 1, "   ")

    assert get_all_memory_embeddings() == []


def test_index_text_upserts_same_source(tmp_db):
    from database import get_all_memory_embeddings
    from scripts.semantic_search import index_text

    with patch("scripts.semantic_search.embed_text", side_effect=_fake_embed):
        index_text("episode", 1, "première version")
        index_text("episode", 1, "version mise à jour")

    rows = get_all_memory_embeddings()
    assert len(rows) == 1
    assert rows[0]["text_preview"] == "version mise à jour"


def test_semantic_search_ranks_by_similarity(tmp_db):
    from scripts.semantic_search import cosine_similarity, embed_text, index_text, semantic_search

    # Vecteurs contrôlés directement (pas de dépendance au hash du texte)
    close_vec = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    far_vec = np.array([0.0, 1.0, 0.0], dtype=np.float32)
    query_vec = np.array([0.9, 0.1, 0.0], dtype=np.float32)

    def fake_embed(text: str):
        return {"close text": close_vec, "far text": far_vec, "the query": query_vec}[text]

    with patch("scripts.semantic_search.embed_text", side_effect=fake_embed):
        index_text("episode", 1, "close text")
        index_text("episode", 2, "far text")
        results = semantic_search("the query", limit=10)

    assert len(results) == 2
    assert results[0]["source_id"] == 1  # plus proche du vecteur query
    assert results[0]["score"] > results[1]["score"]


def test_semantic_search_filters_by_source_type(tmp_db):
    def fake_embed(text: str):
        return np.array([1.0, 0.0], dtype=np.float32)

    from scripts.semantic_search import index_text, semantic_search

    with patch("scripts.semantic_search.embed_text", side_effect=fake_embed):
        index_text("episode", 1, "episode text")
        index_text("recording", 2, "recording text")
        results = semantic_search("query", source_type="recording")

    assert len(results) == 1
    assert results[0]["source_type"] == "recording"


def test_semantic_search_respects_limit(tmp_db):
    def fake_embed(text: str):
        return np.array([1.0, 0.0], dtype=np.float32)

    from scripts.semantic_search import index_text, semantic_search

    with patch("scripts.semantic_search.embed_text", side_effect=fake_embed):
        for i in range(5):
            index_text("episode", i, f"text {i}")
        results = semantic_search("query", limit=2)

    assert len(results) == 2


def test_semantic_search_empty_index_returns_empty(tmp_db):
    from scripts.semantic_search import semantic_search

    with patch("scripts.semantic_search.embed_text", side_effect=_fake_embed):
        assert semantic_search("anything") == []


# ── Dégradation propre si sentence-transformers absent ─────────

def test_get_model_raises_clear_error_when_import_fails(tmp_db):
    from scripts import semantic_search

    semantic_search._model = None
    with patch.dict(sys.modules, {"sentence_transformers": None}):
        with pytest.raises(semantic_search.SemanticSearchUnavailable):
            semantic_search._get_model()
