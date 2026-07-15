"""Tests : raccourci « répète », TTS spéculatif, session vocale persistante."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import config  # noqa: E402


# ── Détection « répète » ─────────────────────────────────────

def test_repeat_request_matching():
    from audio.tts_cache import is_repeat_request

    assert is_repeat_request("répète") is True
    assert is_repeat_request("Jarvis, tu peux répéter ?") is True
    assert is_repeat_request("qu'est-ce que tu as dit") is True
    assert is_repeat_request("j'ai pas entendu") is True
    # pas de faux positifs
    assert is_repeat_request("répète-moi la table de 9 puis explique les logarithmes en détail") is False
    assert is_repeat_request("quel temps fait-il") is False
    assert is_repeat_request("") is False


def test_last_tts_store_and_get():
    from audio.tts_cache import LastTTS

    cache = LastTTS()
    assert cache.get() is None
    cache.store("Bonjour Monsieur.", "warm", b"MP3DATA", "audio/mpeg")
    entry = cache.get()
    assert entry["text"] == "Bonjour Monsieur." and entry["audio"] == b"MP3DATA"
    cache.store("", "warm", b"x")     # texte vide ignoré
    cache.store("y", "warm", b"")     # audio vide ignoré
    assert cache.get()["text"] == "Bonjour Monsieur."


# ── TTS spéculatif ───────────────────────────────────────────

class _FakeEngine:
    available = True

    def __init__(self):
        self.calls = 0

    async def synthesize(self, text, emotion="neutral"):
        self.calls += 1
        return f"AUDIO:{text}:{emotion}".encode()


@pytest.mark.asyncio
async def test_speculative_put_get_normalized(monkeypatch):
    from audio.tts_cache import SpeculativeTTS

    monkeypatch.setattr("config.SPECULATIVE_TTS_ENABLED", True)
    spec = SpeculativeTTS()
    eng = _FakeEngine()

    assert await spec.put("Bien, Monsieur.", "neutral", eng) is True
    # correspondance insensible à la casse/accents/ponctuation finale
    assert spec.get("bien, monsieur", "neutral") == b"AUDIO:Bien, Monsieur.:neutral"
    assert spec.get("Bien, Monsieur.", "warm") is None      # émotion différente
    # put idempotent : pas de re-synthèse
    assert await spec.put("BIEN, MONSIEUR.", "neutral", eng) is True
    assert eng.calls == 1


@pytest.mark.asyncio
async def test_speculative_invalidated_on_voice_change(monkeypatch):
    from audio.tts_cache import SpeculativeTTS

    monkeypatch.setattr("config.SPECULATIVE_TTS_ENABLED", True)
    # La signature du cache est par moteur : TTS_VOICE n'invalide que pour edge.
    monkeypatch.setattr("config.TTS_ENGINE", "edge")
    monkeypatch.setattr("config.TTS_VOICE", "voix-A")
    spec = SpeculativeTTS()
    await spec.put("Bien, Monsieur.", "neutral", _FakeEngine())
    assert spec.get("Bien, Monsieur.") is not None

    monkeypatch.setattr("config.TTS_VOICE", "voix-B")
    assert spec.get("Bien, Monsieur.") is None
    assert spec.stats()["entries"] == 0


@pytest.mark.asyncio
async def test_speculative_invalidated_on_kokoro_voice_change(monkeypatch):
    from audio.tts_cache import SpeculativeTTS

    monkeypatch.setattr("config.SPECULATIVE_TTS_ENABLED", True)
    monkeypatch.setattr("config.TTS_ENGINE", "kokoro")
    monkeypatch.setattr("config.KOKORO_VOICE", "af_nicole")
    spec = SpeculativeTTS()
    await spec.put("Bien, Monsieur.", "neutral", _FakeEngine())
    assert spec.get("Bien, Monsieur.") is not None

    monkeypatch.setattr("config.KOKORO_VOICE", "af_bella")
    assert spec.get("Bien, Monsieur.") is None
    assert spec.stats()["entries"] == 0


@pytest.mark.asyncio
async def test_speculative_disabled(monkeypatch):
    from audio.tts_cache import SpeculativeTTS

    monkeypatch.setattr("config.SPECULATIVE_TTS_ENABLED", False)
    spec = SpeculativeTTS()
    assert await spec.put("Bien, Monsieur.", "neutral", _FakeEngine()) is False
    assert spec.get("Bien, Monsieur.") is None


@pytest.mark.asyncio
async def test_speculative_engine_failure_is_silent(monkeypatch):
    from audio.tts_cache import SpeculativeTTS

    monkeypatch.setattr("config.SPECULATIVE_TTS_ENABLED", True)
    spec = SpeculativeTTS()
    broken = _FakeEngine()
    broken.synthesize = AsyncMock(side_effect=RuntimeError("down"))
    assert await spec.put("Bien, Monsieur.", "neutral", broken) is False


# ── Session vocale persistante ───────────────────────────────

def test_ws_session_grace(monkeypatch, tmp_path):
    db_path = tmp_path / "test_jarvis.db"
    monkeypatch.setattr("config.DB_PATH", str(db_path))
    monkeypatch.setattr("database.DB_PATH", db_path)
    from database import init_db

    init_db()
    import main

    monkeypatch.setattr("config.VOICE_SESSION_GRACE_S", 180)
    monkeypatch.setitem(main._ws_last_session, "conversation_id", None)
    monkeypatch.setitem(main._ws_last_session, "closed_at", 0.0)

    # première connexion : création
    cid1, resumed = main._resume_or_create_conversation(now=1000.0)
    assert resumed is False

    # coupure courte → reprise de la même conversation
    main._ws_last_session["conversation_id"] = cid1
    main._ws_last_session["closed_at"] = 1000.0
    cid2, resumed = main._resume_or_create_conversation(now=1060.0)
    assert resumed is True and cid2 == cid1

    # coupure au-delà de la grâce → nouvelle conversation
    cid3, resumed = main._resume_or_create_conversation(now=1000.0 + 300)
    assert resumed is False and cid3 != cid1
