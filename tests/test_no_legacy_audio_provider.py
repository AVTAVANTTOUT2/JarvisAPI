"""Fitness functions empêchant le retour de l'ancien fournisseur audio."""

from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def test_retired_audio_provider_is_absent_from_tracked_tree() -> None:
    markers = (
        ("eleven" + "labs").encode(),
        ("scr" + "ibe_v1").encode(),
        ("scr" + "ibe_v2").encode(),
    )
    tracked = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    ).stdout.split(b"\0")

    violations: list[str] = []
    for raw_path in tracked:
        if not raw_path:
            continue
        path = ROOT / raw_path.decode()
        if not path.is_file():
            continue
        content = path.read_bytes().lower()
        if any(marker in content for marker in markers):
            violations.append(str(path.relative_to(ROOT)))

    assert violations == []


def test_only_supported_tts_engines_are_exposed() -> None:
    from api.misc_integrations import _VALID_TTS_ENGINES
    from audio.tts import TTS_ENGINE_NAMES

    expected = {"edge", "macos", "kokoro", "ttskit"}
    assert set(TTS_ENGINE_NAMES) == expected
    assert _VALID_TTS_ENGINES == expected
