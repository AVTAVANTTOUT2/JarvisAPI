#!/usr/bin/env python3
"""Génère des démonstrations audio locales de la voix ``jarvis-fr``.

Écrit sous ``data/voice-tests/`` (gitignoré) :

- ``demo_01_greeting.wav``
- ``demo_02_status.wav``
- ``demo_03_long.wav``
- ``demo_report.json`` — latences de premier fragment et synthèse

Requiert les poids Qwen3 installés (``python scripts/download_tts_model.py``).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

DEMO_LINES: tuple[tuple[str, str], ...] = (
    ("demo_01_greeting", "Bonjour Monsieur. Que puis-je faire pour vous ?"),
    (
        "demo_02_status",
        "Tous les systèmes sont opérationnels. Aucune alerte critique.",
    ),
    (
        "demo_03_long",
        (
            "Lille, dix-huit degrés, ciel couvert. Une averse est possible "
            "en fin d'après-midi. Je vous recommande de prendre un parapluie."
        ),
    ),
)


@dataclass
class DemoResult:
    name: str
    text: str
    path: str
    first_chunk_ms: float
    synthesis_ms: float
    bytes: int
    sample_rate: int
    voice_cloned: bool


async def _synthesize_one(
    provider,
    name: str,
    text: str,
    out_dir: Path,
) -> DemoResult:
    from jarvis.audio.tts.wav import pcm_to_wav

    started = time.perf_counter()
    first_ms = -1.0
    chunks: list[bytes] = []
    sample_rate = provider.info().sample_rate
    async for chunk in provider.stream(
        text, request_id=f"demo-{name}", utterance_id=name
    ):
        if first_ms < 0:
            first_ms = (time.perf_counter() - started) * 1000.0
        chunks.append(chunk.data)
        sample_rate = chunk.sample_rate

    pcm = b"".join(chunks)
    wav_path = out_dir / f"{name}.wav"
    wav_path.write_bytes(pcm_to_wav(pcm, sample_rate=sample_rate))
    return DemoResult(
        name=name,
        text=text,
        path=str(wav_path),
        first_chunk_ms=round(first_ms, 1),
        synthesis_ms=round((time.perf_counter() - started) * 1000.0, 1),
        bytes=len(pcm),
        sample_rate=sample_rate,
        voice_cloned=bool(getattr(provider, "_voice_cloned", False)),
    )


async def run_demos(out_dir: Path) -> list[DemoResult]:
    from dataclasses import replace

    from jarvis.audio.tts import create_local_tts_provider, load_tts_settings, reset_local_tts_provider

    await reset_local_tts_provider()
    settings = replace(
        load_tts_settings(),
        provider="qwen3_local",
        voice_path=str(REPO_ROOT / "voices" / "jarvis-fr"),
    )
    provider = create_local_tts_provider(settings)
    await provider.warmup()

    out_dir.mkdir(parents=True, exist_ok=True)
    results: list[DemoResult] = []
    try:
        for name, text in DEMO_LINES:
            results.append(await _synthesize_one(provider, name, text, out_dir))
    finally:
        await provider.close()
        await reset_local_tts_provider()
    return results


def main(argv: list[str] | None = None) -> int:
    # Permet `python scripts/demo_jarvis_voice.py` sans exporter PYTHONPATH.
    repo = str(REPO_ROOT)
    if repo not in sys.path:
        sys.path.insert(0, repo)

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        default=str(REPO_ROOT / "data" / "voice-tests"),
        help="Répertoire des WAV de démonstration",
    )
    args = parser.parse_args(argv)
    out_dir = Path(args.out).expanduser()

    results = asyncio.run(run_demos(out_dir))
    report = {
        "provider": "qwen3_local",
        "voice": "jarvis-fr",
        "demos": [asdict(item) for item in results],
    }
    report_path = out_dir / "demo_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"\nRapport : {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
