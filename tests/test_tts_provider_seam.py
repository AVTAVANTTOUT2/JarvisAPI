"""Frontière entre le pipeline vocal et le moteur de synthèse.

Kokoro est un fournisseur **transitoire**. Ces tests garantissent que son
retrait — au profit de Fish Audio ou d'un autre moteur — reste une opération
bornée : supprimer ses modules et sa branche de résolution, sans toucher au
VAD, au STT, à l'orchestration ni au lecteur audio.

Ce sont des contrats structurels, pas des tests de comportement : ils échouent
au moment où quelqu'un recouple le pipeline générique à un moteur précis.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from audio.tts_provider import (
    PROVIDER_AGNOSTIC_MODULES,
    PROVIDER_SPECIFIC_MODULES,
    StreamingTTSProvider,
    provider_sample_rate,
    supports_pcm_streaming,
)

ROOT = Path(__file__).resolve().parent.parent

# Noms de fournisseurs qui ne doivent apparaître dans aucun module générique.
PROVIDER_NAMES = ("kokoro", "ttskit", "fish audio", "fishaudio")


def _code_without_comments(path: Path) -> str:
    """Corps du fichier sans commentaires ni docstrings de module.

    Une mention en commentaire est une gêne de lecture ; une mention dans le
    code est un couplage. Seul le second casse une migration.
    """
    source = path.read_text(encoding="utf-8")
    # Docstrings triple-quote
    source = re.sub(r'""".*?"""', "", source, flags=re.DOTALL)
    source = re.sub(r"'''.*?'''", "", source, flags=re.DOTALL)
    # Commentaires de fin de ligne
    source = re.sub(r"#.*$", "", source, flags=re.MULTILINE)
    return source


@pytest.mark.parametrize("module", PROVIDER_AGNOSTIC_MODULES)
def test_generic_modules_name_no_provider(module: str):
    """Aucun branchement sur un nom de moteur dans le code générique.

    C'est la propriété qui rend la migration bon marché : le daemon interroge
    un contrat (`synthesize_stream_pcm`, `warmup`, `SAMPLE_RATE`), il ne teste
    jamais « est-ce que c'est Kokoro ».
    """
    path = ROOT / module
    assert path.is_file(), f"module générique introuvable : {module}"
    code = _code_without_comments(path).lower()
    offenders = [name for name in PROVIDER_NAMES if name in code]
    assert offenders == [], (
        f"{module} référence un fournisseur ({offenders}) — "
        "le retrait du moteur obligerait à modifier ce fichier générique."
    )


def test_provider_specific_modules_exist_and_are_listed():
    """L'inventaire des modules à supprimer est exact, donc actionnable."""
    for provider, modules in PROVIDER_SPECIFIC_MODULES.items():
        for module in modules:
            assert (ROOT / module).is_file(), (
                f"{module} listé pour « {provider} » mais absent : "
                "l'inventaire de retrait est faux."
            )


def test_kokoro_specific_code_stays_inside_its_own_modules():
    """Le sidecar chaud et le découpage du 1er fragment ne fuient pas ailleurs.

    Ce sont les deux optimisations propres à Kokoro. `audio/tts.py` porte la
    chaîne de résolution des moteurs — il est donc légitimement au courant —
    mais rien d'autre ne doit l'être.
    """
    allowed = {
        "native_audio/kokoro_mlx.py",
        "native_audio/kokoro_bridge.py",
        "audio/tts.py",          # chaîne de résolution des moteurs locaux
        "audio/tts_native.py",   # idem
        "audio/tts_provider.py", # inventaire documenté
        "config.py",             # réglages exposés
    }
    markers = ("KokoroWorker", "KOKORO_WARM_WORKER", "KOKORO_FIRST_CHUNK_MAX_TOKENS")

    leaks: list[str] = []
    for path in list(ROOT.glob("audio/*.py")) + list(ROOT.glob("api/*.py")) + [
        ROOT / "scripts/audio_daemon.py", ROOT / "pipeline.py", ROOT / "llm.py",
    ]:
        rel = str(path.relative_to(ROOT))
        if rel in allowed:
            continue
        text = path.read_text(encoding="utf-8")
        if any(m in text for m in markers):
            leaks.append(rel)

    assert leaks == [], f"code spécifique Kokoro hors de son périmètre : {leaks}"


# ── Contrat du fournisseur ──────────────────────────────────────────────────


def test_kokoro_engine_satisfies_the_provider_contract():
    from audio.tts import kokoro_tts

    assert isinstance(kokoro_tts, StreamingTTSProvider)
    assert supports_pcm_streaming(kokoro_tts)
    assert provider_sample_rate(kokoro_tts) == 24000


def test_ttskit_engine_satisfies_the_provider_contract():
    from audio.tts_native import ttskit_tts

    assert isinstance(ttskit_tts, StreamingTTSProvider)
    assert supports_pcm_streaming(ttskit_tts)
    assert provider_sample_rate(ttskit_tts) == 24000


def test_sample_rate_comes_from_the_engine_not_from_its_name():
    """Un moteur inconnu déclarant sa fréquence doit être servi correctement.

    C'est ce qui permettra à Fish Audio de brancher sa propre fréquence sans
    toucher au lecteur audio.
    """
    class _FutureProvider:
        SAMPLE_RATE = 44100

        def get_backend_name(self) -> str:
            return "fournisseur-futur"

    assert provider_sample_rate(_FutureProvider()) == 44100
    # Aucune déclaration → repli explicite, jamais une déduction par le nom.
    assert provider_sample_rate(object()) == 24000
    assert provider_sample_rate(None) == 24000


def test_macos_engine_keeps_its_historical_sample_rate():
    """Régression : le nom « macos » donnait 44100 via un branchement."""
    from audio.tts import macos_tts

    assert provider_sample_rate(macos_tts) == 44100


def test_engine_without_streaming_is_not_forced_down_the_stream_path():
    """Un moteur non diffusant doit retomber sur la synthèse complète."""
    class _BufferedOnly:
        SAMPLE_RATE = 22050

        def get_backend_name(self) -> str:
            return "bufferise"

        async def synthesize(self, text, emotion="neutral"):
            return b"RIFF"

    assert supports_pcm_streaming(_BufferedOnly()) is False
