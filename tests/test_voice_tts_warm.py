"""Sidecar Kokoro chaud : modèle chargé une fois, PCM diffusé, replis sûrs.

Le coût dominant mesuré côté TTS n'était pas la synthèse mais le rechargement
du modèle à chaque réponse (un processus Python neuf par phrase). Ces tests
simulent le sidecar : ils vérifient le protocole, la réutilisation du
processus, le streaming et la dégradation en cas de panne — pas la vitesse de
Kokoro lui-même.
"""

from __future__ import annotations

import asyncio

import pytest

from native_audio.kokoro_mlx import (
    TAG_CHUNK,
    TAG_END,
    TAG_ERROR,
    TAG_READY,
    encode_frame,
)


class _FakeStdout:
    """Flux de trames préprogrammées, lu comme un StreamReader asyncio."""

    def __init__(self, frames: bytes) -> None:
        self._buf = bytearray(frames)

    def feed(self, data: bytes) -> None:
        self._buf.extend(data)

    async def readexactly(self, n: int) -> bytes:
        if len(self._buf) < n:
            raise asyncio.IncompleteReadError(bytes(self._buf), n)
        out = bytes(self._buf[:n])
        del self._buf[:n]
        return out


class _FakeStdin:
    def __init__(self) -> None:
        self.written: list[bytes] = []

    def write(self, data: bytes) -> None:
        self.written.append(data)

    async def drain(self) -> None:
        return None


class _FakeProc:
    def __init__(self, frames: bytes) -> None:
        self.stdout = _FakeStdout(frames)
        self.stdin = _FakeStdin()
        self.returncode = None
        self.terminated = False

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = -15

    async def wait(self) -> int:
        return self.returncode or 0


def _worker(frames: bytes, monkeypatch) -> tuple:
    from native_audio import kokoro_bridge

    proc = _FakeProc(frames)
    spawns = {"count": 0}

    async def _fake_exec(*_a, **_kw):
        spawns["count"] += 1
        return proc

    monkeypatch.setattr(kokoro_bridge.asyncio, "create_subprocess_exec", _fake_exec)
    monkeypatch.setattr(kokoro_bridge, "kokoro_mlx_binary", lambda: "/bin/true")
    monkeypatch.setattr(kokoro_bridge, "mlx_python_path", lambda: None)

    worker = kokoro_bridge.KokoroWorker(
        model="m", voice="bm_george", lang_code="f", speed=1.0,
        max_tokens=180, first_chunk_max_tokens=6,
    )
    return worker, proc, spawns


@pytest.mark.asyncio
async def test_worker_streams_chunks_then_ends(monkeypatch):
    frames = (
        encode_frame(TAG_READY)
        + encode_frame(TAG_CHUNK, b"\x01\x02")
        + encode_frame(TAG_CHUNK, b"\x03\x04")
        + encode_frame(TAG_END)
    )
    worker, proc, _ = _worker(frames, monkeypatch)

    chunks = [c async for c in worker.stream_pcm("Bonjour Monsieur.")]
    assert chunks == [b"\x01\x02", b"\x03\x04"]
    # La requête est bien partie en JSON sur stdin.
    assert proc.stdin.written
    assert b"Bonjour Monsieur." in proc.stdin.written[0]


@pytest.mark.asyncio
async def test_model_is_loaded_once_across_two_utterances(monkeypatch):
    """Deux réponses consécutives ne relancent pas le processus."""
    frames = (
        encode_frame(TAG_READY)
        + encode_frame(TAG_CHUNK, b"\xaa\xbb") + encode_frame(TAG_END)
        + encode_frame(TAG_CHUNK, b"\xcc\xdd") + encode_frame(TAG_END)
    )
    worker, _proc, spawns = _worker(frames, monkeypatch)

    first = [c async for c in worker.stream_pcm("Une.")]
    second = [c async for c in worker.stream_pcm("Deux.")]

    assert first == [b"\xaa\xbb"]
    assert second == [b"\xcc\xdd"]
    assert spawns["count"] == 1, "le sidecar a été relancé entre deux réponses"


@pytest.mark.asyncio
async def test_engine_error_frame_does_not_kill_the_worker(monkeypatch):
    frames = (
        encode_frame(TAG_READY)
        + encode_frame(TAG_ERROR, b"phonemiseur en panne") + encode_frame(TAG_END)
        + encode_frame(TAG_CHUNK, b"\x09\x09") + encode_frame(TAG_END)
    )
    worker, _proc, spawns = _worker(frames, monkeypatch)

    assert [c async for c in worker.stream_pcm("Texte impossible.")] == []
    # Le tour suivant fonctionne toujours, sans redémarrage.
    assert [c async for c in worker.stream_pcm("Texte normal.")] == [b"\x09\x09"]
    assert spawns["count"] == 1


@pytest.mark.asyncio
async def test_truncated_stream_degrades_to_empty_instead_of_raising(monkeypatch):
    """Un sidecar qui meurt en cours ne doit pas casser le tour de parole."""
    frames = encode_frame(TAG_READY) + encode_frame(TAG_CHUNK, b"\x01\x02")[:6]
    worker, _proc, _ = _worker(frames, monkeypatch)

    assert [c async for c in worker.stream_pcm("Bonjour.")] == []
    assert worker.ready is False  # marqué à relancer


@pytest.mark.asyncio
async def test_missing_binary_yields_nothing(monkeypatch):
    from native_audio import kokoro_bridge

    monkeypatch.setattr(kokoro_bridge, "kokoro_mlx_binary", lambda: None)
    worker = kokoro_bridge.KokoroWorker(
        model="m", voice="v", lang_code="f", speed=1.0,
        max_tokens=180, first_chunk_max_tokens=6,
    )
    assert [c async for c in worker.stream_pcm("Bonjour.")] == []
    assert await worker.start() is False


@pytest.mark.asyncio
async def test_empty_text_never_spawns_a_process(monkeypatch):
    frames = encode_frame(TAG_READY)
    worker, _proc, spawns = _worker(frames, monkeypatch)

    assert [c async for c in worker.stream_pcm("   ")] == []
    assert spawns["count"] == 0


# ── Chemin moteur : pas de fichier temporaire, repli explicite ──────────────


@pytest.mark.asyncio
async def test_warm_path_writes_no_temporary_file(monkeypatch, tmp_path):
    """La synthèse chaude reste en mémoire — aucun WAV intermédiaire sur disque."""
    import tempfile

    from audio.tts import KokoroTTSEngine

    engine = KokoroTTSEngine.__new__(KokoroTTSEngine)
    engine._backend = "mlx"
    engine._voice = "bm_george"
    engine._lang_code = "f"
    engine._model = "m"
    engine._speed = 1.0
    engine._max_tokens = 180
    engine._first_chunk_max_tokens = 6
    engine._worker_disabled = False
    engine._worker = None

    class _Worker:
        async def stream_pcm(self, _text):
            yield b"\x00\x01" * 100

    engine._worker = _Worker()

    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    before = set(tmp_path.iterdir())
    wav = await engine._synthesize_mlx("Bonjour Monsieur.")
    after = set(tmp_path.iterdir())

    assert wav.startswith(b"RIFF")
    assert before == after, "un fichier temporaire a été écrit"


@pytest.mark.asyncio
async def test_falls_back_to_one_shot_when_warm_worker_is_empty(monkeypatch):
    """Sidecar chaud muet → le chemin historique reprend la main."""
    from audio.tts import KokoroTTSEngine

    engine = KokoroTTSEngine.__new__(KokoroTTSEngine)
    engine._backend = "mlx"
    engine._voice = "bm_george"
    engine._lang_code = "f"
    engine._model = "m"
    engine._speed = 1.0
    engine._max_tokens = 180
    engine._first_chunk_max_tokens = 6
    engine._worker_disabled = False

    class _SilentWorker:
        async def stream_pcm(self, _text):
            return
            yield  # pragma: no cover — générateur vide

    engine._worker = _SilentWorker()

    called = {}

    async def _fake_one_shot(text, **kwargs):
        called["text"] = text
        return b"RIFFfallback"

    import native_audio.kokoro_bridge as kb

    monkeypatch.setattr(kb, "synthesize_bytes", _fake_one_shot)
    out = await engine._synthesize_mlx("Bonjour.")

    assert out == b"RIFFfallback"
    assert called["text"] == "Bonjour."


@pytest.mark.asyncio
async def test_warm_worker_can_be_disabled_by_config(monkeypatch):
    from audio.tts import KokoroTTSEngine

    engine = KokoroTTSEngine.__new__(KokoroTTSEngine)
    engine._backend = "mlx"
    engine._worker_disabled = True
    engine._worker = None

    assert engine._get_worker() is None
    assert await engine.warmup() is False
