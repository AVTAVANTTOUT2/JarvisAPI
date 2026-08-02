"""Contrat de la synthèse vocale locale.

Ce fichier vérifie deux natures de propriétés :

1. **Comportement** — préchauffage, diffusion, ordre, contre-pression,
   annulation, erreurs, réarmement. Ils s'exécutent sur un fournisseur factice
   qui satisfait réellement le protocole : un test qui passerait ici mais
   échouerait sur un vrai backend signalerait une divergence de contrat.
2. **Structure** — la propriété « 100 % local » ne se teste pas en exécutant du
   code, puisqu'elle affirme l'absence de quelque chose. Elle se vérifie par un
   scan du dépôt : aucune clé d'API, aucun hôte de service vocal, aucun moteur
   réseau, aucun téléchargement au runtime.
"""

from __future__ import annotations

import asyncio
import inspect
import re
import tempfile
from dataclasses import replace
from pathlib import Path

import pytest

from jarvis.audio.tts import (
    AudioChunk,
    LocalTTSProvider,
    TTSModelNotFoundError,
    TTSUnavailableError,
    TTSUnsupportedDeviceError,
    create_local_tts_provider,
    load_tts_settings,
    reset_local_tts_provider,
)
from jarvis.audio.tts import events
from jarvis.audio.tts.config import KNOWN_PROVIDERS
from tests.local_tts_stub import StubProvider

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture()
def settings():
    return load_tts_settings()


# ── 1. Configuration locale ──────────────────────────────────────────────────


def test_settings_resolve_from_config(settings):
    assert settings.provider in KNOWN_PROVIDERS
    assert settings.model_path
    assert settings.voice_path
    assert settings.sample_rate > 0
    assert settings.channels >= 1


def test_settings_contain_no_secret_and_no_url(settings):
    """Un fournisseur qui réclamerait une clé ne pourrait pas être configuré."""
    values = " ".join(str(value) for value in vars(settings).values())
    assert "://" not in values.replace("mlx-community/", "")
    assert "key" not in values.lower()
    assert "token" not in values.lower()


def test_unknown_provider_is_refused():
    """La table est fermée : aucun backend n'apparaît par configuration."""
    broken = replace(load_tts_settings(), provider="fish_cloud")
    with pytest.raises(TTSUnavailableError) as failure:
        create_local_tts_provider(broken)
    assert "fish_cloud" in str(failure.value)


@pytest.mark.parametrize("provider", sorted(KNOWN_PROVIDERS))
def test_every_known_provider_declares_itself_offline(provider: str):
    info = create_local_tts_provider(
        replace(load_tts_settings(), provider=provider)
    ).info()
    assert info.offline is True
    assert info.streaming in {"native", "segmented"}


def test_provider_construction_loads_no_model(settings):
    """Construire doit être instantané : le chargement appartient au warmup."""
    provider = create_local_tts_provider(settings)
    assert isinstance(provider, LocalTTSProvider)


# ── 2. Modèle et voix absents ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_missing_model_raises_an_actionable_error(tmp_path):
    absent = replace(
        load_tts_settings(), provider="fish_local", model_path=str(tmp_path / "nope"),
    )
    provider = create_local_tts_provider(absent)
    with pytest.raises(TTSModelNotFoundError) as failure:
        await provider.warmup()
    # Un message qui n'indique pas quoi faire oblige à lire le code.
    assert "download_tts_model" in str(failure.value)


@pytest.mark.asyncio
async def test_unsupported_device_is_refused_before_loading(tmp_path):
    """« cuda » est le défaut de l'implémentation de référence de Fish :
    l'accepter en silence sur un Mac donnerait un échec incompréhensible."""
    cuda = replace(load_tts_settings(), provider="fish_local", device="cuda")
    provider = create_local_tts_provider(cuda)
    with pytest.raises(TTSUnsupportedDeviceError):
        await provider.warmup()


def test_missing_voice_reference_is_tolerated(tmp_path):
    """Une installation neuve doit pouvoir parler avant d'avoir une voix."""
    voiceless = replace(load_tts_settings(), voice_path=str(tmp_path / "voices"))
    assert voiceless.reference_audio() is None
    assert voiceless.reference_text() is None


def test_reference_audio_without_transcript_is_detected(tmp_path):
    (tmp_path / "reference.wav").write_bytes(b"RIFF")
    incomplete = replace(load_tts_settings(), voice_path=str(tmp_path))
    assert incomplete.reference_audio() is not None
    assert incomplete.reference_text() is None


def test_a_single_voice_is_configured(settings):
    """Pas de sélecteur multi-voix : JARVIS est une entité, pas un catalogue."""
    voices_dir = PROJECT_ROOT / "voices"
    directories = [p for p in voices_dir.iterdir() if p.is_dir()]
    assert len(directories) == 1
    assert directories[0].name == settings.voice_id


# ── 3. Cycle de vie ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_warmup_is_idempotent():
    provider = StubProvider()
    await provider.warmup()
    await provider.warmup()
    assert provider.warmups == 2  # le stub compte les appels ; le vrai backend court-circuite


@pytest.mark.asyncio
async def test_shared_provider_is_a_singleton(settings):
    from jarvis.audio.tts import get_local_tts_provider

    await reset_local_tts_provider()
    first = get_local_tts_provider(settings)
    second = get_local_tts_provider(settings)
    assert first is second
    await reset_local_tts_provider()


@pytest.mark.asyncio
async def test_reset_closes_the_provider(monkeypatch, settings):
    from jarvis.audio.tts import factory

    stub = StubProvider()
    monkeypatch.setattr(factory, "_provider", stub)
    await reset_local_tts_provider()
    assert stub.closed is True


# ── 4. Diffusion ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_stream_yields_declared_audio_chunks():
    provider = StubProvider()
    chunks = [
        chunk
        async for chunk in provider.stream("Bonjour.", request_id="r", utterance_id="u")
    ]
    assert chunks
    assert all(isinstance(chunk, AudioChunk) for chunk in chunks)
    assert chunks[-1].is_final is True
    assert chunks[0].sample_format == "pcm_s16le"
    assert chunks[0].sample_rate == provider.sample_rate


@pytest.mark.asyncio
async def test_chunk_order_is_preserved():
    class Ordered(StubProvider):
        async def stream(self, text, *, request_id, utterance_id):
            for index in range(5):
                yield AudioChunk(data=bytes([index, 0]), sample_rate=self.sample_rate)

    received = [
        chunk.data[0]
        async for chunk in Ordered().stream("x", request_id="r", utterance_id="u")
    ]
    assert received == [0, 1, 2, 3, 4]


@pytest.mark.asyncio
async def test_playback_applies_backpressure_on_a_slow_sink(monkeypatch):
    """Un moteur plus rapide que la lecture ne doit pas remplir la mémoire."""
    from jarvis.audio.tts import playback

    produced = 0
    consumed: list[bytes] = []

    async def _chunks():
        nonlocal produced
        for _ in range(100):
            produced += 1
            yield AudioChunk(data=b"\x00\x01", sample_rate=24000)

    class SlowOutput:
        available = True

        async def play_stream_from_async(self, stream, *, sample_rate, on_first_chunk=None):
            async for chunk in stream:
                if on_first_chunk is not None and not consumed:
                    on_first_chunk()
                consumed.append(chunk)
                await asyncio.sleep(0)  # cède la main : lecture plus lente
            return True

        def stop(self) -> None:
            return None

    monkeypatch.setattr(
        "audio.audio_output.native_audio_output", SlowOutput(), raising=False,
    )
    result = await playback.play_chunks(_chunks(), sample_rate=24000)
    assert result.chunks == 100
    assert len(consumed) == 100
    assert result.started is True


@pytest.mark.asyncio
async def test_playback_rejects_a_sample_rate_change_mid_stream(monkeypatch, caplog):
    """Changer de fréquence en cours de flux déforme la voix sans lever."""
    from jarvis.audio.tts import playback

    played: list[bytes] = []

    class Output:
        available = True

        async def play_stream_from_async(self, stream, *, sample_rate, on_first_chunk=None):
            async for chunk in stream:
                played.append(chunk)
            return True

        def stop(self) -> None:
            return None

    async def _chunks():
        yield AudioChunk(data=b"\x00\x01", sample_rate=24000)
        yield AudioChunk(data=b"\x02\x03", sample_rate=44100)

    monkeypatch.setattr(
        "audio.audio_output.native_audio_output", Output(), raising=False,
    )
    result = await playback.play_chunks(_chunks(), sample_rate=24000)
    assert played == [b"\x00\x01"]
    assert result.bytes_played == 2


# ── 5. Annulation et erreurs ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cancel_is_correlated_by_request_id():
    """Une annulation tardive ne doit jamais couper la réponse suivante."""
    from jarvis.audio.tts.backends.sidecar import SidecarClient

    client = SidecarClient(["/bin/true"], label="test")
    client.cancel("ancien-tour")
    assert "ancien-tour" in client._cancelled
    client.cancel("")
    assert "" not in client._cancelled


@pytest.mark.asyncio
async def test_cancel_on_an_unknown_request_is_harmless():
    provider = StubProvider()
    await provider.cancel("jamais-lancé")
    assert provider.cancelled == ["jamais-lancé"]


@pytest.mark.asyncio
async def test_synthesis_error_does_not_break_the_next_turn():
    class Flaky(StubProvider):
        def __init__(self):
            super().__init__()
            self.attempts = 0

        async def stream(self, text, *, request_id, utterance_id):
            self.attempts += 1
            if self.attempts == 1:
                raise RuntimeError("échec de synthèse")
            async for chunk in StubProvider.stream(
                self, text, request_id=request_id, utterance_id=utterance_id
            ):
                yield chunk

    provider = Flaky()
    with pytest.raises(RuntimeError):
        async for _ in provider.stream("un", request_id="r1", utterance_id="u1"):
            pass

    chunks = [
        chunk
        async for chunk in provider.stream("deux", request_id="r2", utterance_id="u2")
    ]
    assert chunks, "le tour suivant doit fonctionner après une erreur"


@pytest.mark.asyncio
async def test_two_consecutive_turns_reuse_the_same_provider():
    provider = StubProvider()
    for index in range(2):
        async for _ in provider.stream(
            f"phrase {index}", request_id=f"r{index}", utterance_id="u"
        ):
            pass
    assert provider.calls == ["phrase 0", "phrase 1"]


# ── 6. Instrumentation ───────────────────────────────────────────────────────


def test_metrics_never_carry_content():
    """Une allowlist, pas une convention : le texte ne peut pas passer."""
    published = events.emit_tts_event(
        events.SYNTHESIS_STARTED,
        provider="fish_local",
        chars=42,
        text="Bonjour Monsieur, voici votre briefing",
        transcript="secret",
        api_key="jamais",
    )
    assert published == {"provider": "fish_local", "chars": 42}


def test_unknown_metric_names_are_refused():
    with pytest.raises(ValueError):
        events.emit_tts_event("tts.inventé")


def test_every_required_lifecycle_event_exists():
    required = {
        "tts.provider.created", "tts.warmup.started", "tts.warmup.completed",
        "tts.queue.entered", "tts.segment.received", "tts.synthesis.started",
        "tts.first_chunk", "tts.playback.started", "tts.synthesis.completed",
        "tts.playback.completed", "tts.cancelled", "tts.failed",
    }
    assert required <= events.KNOWN_EVENTS


def test_latency_trace_declares_the_tts_steps():
    from audio import voice_latency as vl

    for step in (
        vl.TTS_QUEUE_ENTERED, vl.TTS_MODEL_READY, vl.TTS_SYNTHESIS_STARTED,
        vl.TTS_FIRST_AUDIO_CHUNK, vl.TTS_PLAYBACK_STARTED,
        vl.TTS_SYNTHESIS_COMPLETED, vl.TTS_PLAYBACK_COMPLETED,
    ):
        assert step in vl.KNOWN_EVENTS


# ── 7. Propriétés structurelles : rien ne sort de la machine ─────────────────

# Les répertoires de code réellement exécuté. La documentation historique est
# volontairement exclue : elle décrit le passé, elle ne l'exécute pas.
SCANNED_DIRS = ("api", "audio", "jarvis", "native_audio", "scripts", "agents")
SCANNED_FILES = ("config.py", "main.py", "pipeline.py", "requirements.txt")

FORBIDDEN_PATTERNS = (
    "FISH_API_KEY",
    "api.fish.audio",
    "fish.audio/v1",
    "edge-tts",
    "edge_tts",
    "piper",
    "xtts",
)


def _python_sources() -> list[Path]:
    files: list[Path] = []
    for directory in SCANNED_DIRS:
        root = PROJECT_ROOT / directory
        if not root.is_dir():
            continue
        files += [
            path
            for path in root.rglob("*.py")
            if "__pycache__" not in path.parts and "tests" not in path.parts
        ]
    files += [PROJECT_ROOT / name for name in SCANNED_FILES if (PROJECT_ROOT / name).is_file()]
    return files


# `config.RETIRED_ENV_VARS` nomme les variables disparues — c'est son rôle :
# avertir l'utilisateur dont le `.env` les définit encore. Ces lignes déclarent
# une absence, elles ne réintroduisent rien.
_RETIRED_DECLARATION = re.compile(r'^\s*"[A-Z0-9_]+":')


@pytest.mark.parametrize("pattern", FORBIDDEN_PATTERNS)
def test_no_cloud_or_legacy_engine_reference_in_source(pattern: str):
    offenders: list[str] = []
    for path in _python_sources():
        text = path.read_text(encoding="utf-8", errors="replace")
        for number, line in enumerate(text.splitlines(), start=1):
            if pattern.lower() not in line.lower():
                continue
            if path.name == "config.py" and _RETIRED_DECLARATION.match(line):
                continue
            offenders.append(f"{path.relative_to(PROJECT_ROOT)}:{number}")
    assert offenders == [], f"référence interdite « {pattern} » : {offenders}"


def test_no_tts_api_key_is_read_anywhere():
    """Aucune variable de clé vocale n'est lue, même en repli."""
    suspicious = re.compile(r"(FISH|TTS|VOICE)_(API_)?(KEY|TOKEN|SECRET)")
    offenders = [
        f"{path.relative_to(PROJECT_ROOT)}"
        for path in _python_sources()
        if suspicious.search(path.read_text(encoding="utf-8", errors="replace"))
    ]
    assert offenders == []


def test_tts_package_never_imports_a_network_client():
    """La pile vocale ne doit importer ni httpx, ni requests, ni websockets."""
    package = PROJECT_ROOT / "jarvis" / "audio" / "tts"
    banned = re.compile(r"^\s*(import|from)\s+(httpx|requests|aiohttp|websockets)\b", re.M)
    offenders = [
        str(path.relative_to(PROJECT_ROOT))
        for path in package.rglob("*.py")
        if banned.search(path.read_text(encoding="utf-8"))
    ]
    assert offenders == []


def test_no_runtime_download_is_possible():
    """Le sidecar résout un chemin local et n'appelle jamais un téléchargement."""
    from native_audio import fish_local

    source = inspect.getsource(fish_local)
    # Chaque appel — et non chaque mention — doit être borné au cache local :
    # un `snapshot_download` sans `local_files_only` déclencherait un transfert
    # de plusieurs gigaoctets au milieu d'un tour de parole.
    calls = re.findall(r"snapshot_download\(([^)]*)\)", source)
    assert calls, "la résolution locale doit passer par snapshot_download"
    assert all("local_files_only=True" in call for call in calls)
    assert "HF_HUB_OFFLINE" in source


def test_launcher_forces_offline_mode():
    launcher = (PROJECT_ROOT / "native_audio" / "fish_synthesize").read_text(encoding="utf-8")
    assert "HF_HUB_OFFLINE=1" in launcher


def test_no_temporary_file_in_the_realtime_path():
    """Le texte prononcé est une donnée personnelle : il ne passe pas par /tmp."""
    package = PROJECT_ROOT / "jarvis" / "audio" / "tts"
    offenders = [
        str(path.relative_to(PROJECT_ROOT))
        for path in package.rglob("*.py")
        if "tempfile" in path.read_text(encoding="utf-8")
    ]
    assert offenders == []


def test_no_interpreter_is_spawned_per_phrase():
    """Un processus par phrase rechargerait le modèle à chaque réponse."""
    from jarvis.audio.tts.backends import sidecar

    source = inspect.getsource(sidecar)
    # La création de processus vit dans `start()`, pas dans `stream()`.
    stream_source = inspect.getsource(sidecar.SidecarClient.stream)
    assert "create_subprocess_exec" in source
    assert "create_subprocess_exec" not in stream_source


def test_no_silent_fallback_between_providers():
    """Un échec ne doit pas faire entendre une voix que l'utilisateur n'a pas choisie."""
    from jarvis.audio.tts import factory

    source = inspect.getsource(factory)
    assert "except" not in source.split("def create_local_tts_provider")[1].split("def ")[0]


def test_temp_dir_is_untouched_by_a_synthesis(tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path), raising=False)
    provider = StubProvider()

    async def _run():
        async for _ in provider.stream("Bonjour.", request_id="r", utterance_id="u"):
            pass

    asyncio.run(_run())
    assert list(tmp_path.iterdir()) == []
