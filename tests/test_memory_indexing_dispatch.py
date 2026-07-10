"""Tests : indexation sémantique automatique déclenchée par save_episode/save_recording."""

from __future__ import annotations

import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture(autouse=True)
def _no_background_db_threads():
    """Surcharge la fixture globale (conftest) : ici on teste précisément le déclenchement."""
    yield


@pytest.fixture
def tmp_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    db_path = tmp_path / "test_jarvis.db"
    monkeypatch.setattr("config.DB_PATH", str(db_path))
    monkeypatch.setattr("database.DB_PATH", db_path)
    from database import init_db

    init_db()
    return db_path


class _SyncThread:
    """Remplace threading.Thread pour exécuter la cible immédiatement (tests déterministes)."""

    def __init__(self, target=None, daemon=None, **kwargs):
        self._target = target

    def start(self):
        self._target()


def test_save_episode_dispatches_indexing(tmp_db):
    from database import save_episode

    with patch("threading.Thread", _SyncThread):
        with patch("scripts.semantic_search.index_text") as mock_index:
            episode_id = save_episode(agent="coach", content="Contenu de l'épisode", summary="Résumé court")

    mock_index.assert_called_once_with("episode", episode_id, "Résumé court")


def test_save_episode_falls_back_to_content_without_summary(tmp_db):
    from database import save_episode

    with patch("threading.Thread", _SyncThread):
        with patch("scripts.semantic_search.index_text") as mock_index:
            episode_id = save_episode(agent="coach", content="Contenu seul, pas de résumé")

    mock_index.assert_called_once_with("episode", episode_id, "Contenu seul, pas de résumé")


def test_save_recording_dispatches_indexing(tmp_db):
    from database import save_recording

    with patch("threading.Thread", _SyncThread):
        with patch("scripts.semantic_search.index_text") as mock_index:
            rec_id = save_recording(
                conversation_id=None, label="test", duration_seconds=60,
                transcription="Transcription complète ici", summary="Résumé de la réunion",
                synthesis={}, actions={}, audio_size_kb=5,
            )

    mock_index.assert_called_once_with("recording", rec_id, "Résumé de la réunion")


def test_indexing_never_raises_when_semantic_search_unavailable(tmp_db):
    from database import save_episode
    from scripts.semantic_search import SemanticSearchUnavailable

    with patch("threading.Thread", _SyncThread):
        with patch("scripts.semantic_search.index_text", side_effect=SemanticSearchUnavailable("no model")):
            episode_id = save_episode(agent="coach", content="x")

    assert episode_id is not None


def test_indexing_never_raises_on_unexpected_error(tmp_db):
    from database import save_episode

    with patch("threading.Thread", _SyncThread):
        with patch("scripts.semantic_search.index_text", side_effect=RuntimeError("boom")):
            episode_id = save_episode(agent="coach", content="x")

    assert episode_id is not None


def test_indexing_runs_in_background_thread(tmp_db):
    from database import save_episode

    with patch("scripts.semantic_search.index_text") as mock_index:
        save_episode(agent="coach", content="x", summary="y")
        for _ in range(50):
            if mock_index.called:
                break
            time.sleep(0.02)

    assert mock_index.called
