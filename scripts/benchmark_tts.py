#!/usr/bin/env python3
"""Banc de mesure de la synthèse vocale locale — chiffres réels, pas de mock.

    python scripts/benchmark_tts.py                 # fournisseur configuré
    python scripts/benchmark_tts.py --provider qwen3_local
    python scripts/benchmark_tts.py --runs 5 --json rapport.json

Ce que le banc mesure et pourquoi :

- **chargement du modèle** — payé une fois, hors tour de parole. C'est ce que
  le préchauffage sort du chemin de réponse ;
- **premier fragment audio** — le seul chiffre que l'utilisateur ressent
  vraiment : le silence entre sa question et le premier son ;
- **synthèse totale** et **facteur temps réel** — un facteur supérieur à 1
  signifie que le moteur produit moins vite que la parole ne se prononce ; la
  lecture finira par attendre ;
- **mémoire résidente** — un modèle qui tient en RAM ne recharge pas.

Les mesures froides et chaudes sont distinguées : confondre les deux fait
croire à une latence deux fois plus faible qu'à l'ouverture de session.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import resource
import statistics
import sys
import time
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis.audio.tts import (  # noqa: E402
    TextStreamSegmenter,
    create_local_tts_provider,
    load_tts_settings,
)
from jarvis.audio.tts.errors import TTSError  # noqa: E402

PHRASES: dict[str, str] = {
    "courte_20": "Bonjour Monsieur.",
    "moyenne_100": (
        "Il fait dix-huit degrés à Lille, ciel couvert, et une averse est "
        "attendue en fin d'après-midi."
    ),
    "longue_multi": (
        "Bonjour Monsieur. Trois messages vous attendent ce matin. "
        "Le premier vient de votre école : la remise du dossier est avancée "
        "à vendredi. Le deuxième est une facture. Le dernier peut attendre."
    ),
}


def _rss_mb() -> float:
    """Mémoire résidente du processus courant, en mégaoctets.

    Sur macOS, ``ru_maxrss`` est en octets ; sur Linux, en kilooctets.
    """
    raw = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return raw / (1024 * 1024) if sys.platform == "darwin" else raw / 1024


def _sidecar_rss_mb() -> float:
    """Mémoire des processus enfants — c'est là que vit le modèle.

    Sans cette mesure, le rapport afficherait quelques dizaines de mégaoctets
    et laisserait croire que le moteur tient dans presque rien : les poids sont
    chargés par le sidecar, pas par l'interpréteur de JARVIS.
    """
    import subprocess

    try:
        children = subprocess.run(
            ["pgrep", "-P", str(os.getpid())],
            capture_output=True, text=True, timeout=5, check=False,
        ).stdout.split()
        total = 0.0
        for pid in children:
            output = subprocess.run(
                ["ps", "-o", "rss=", "-p", pid],
                capture_output=True, text=True, timeout=5, check=False,
            ).stdout.strip()
            if output:
                total += float(output) / 1024
        return total
    except Exception:  # noqa: BLE001 - une mesure absente ne casse pas le banc
        return 0.0


async def _measure(provider, label: str, text: str, index: int) -> dict[str, object]:
    """Un tour de synthèse complet, chronométré étape par étape."""
    segmenter = TextStreamSegmenter(load_tts_settings())
    segments = segmenter.feed(text) + segmenter.flush()

    started = time.perf_counter()
    first_segment_ms: float | None = None
    first_chunk_ms: float | None = None
    total_bytes = 0
    sample_rate = provider.info().sample_rate

    for position, segment in enumerate(segments):
        if first_segment_ms is None:
            first_segment_ms = (time.perf_counter() - started) * 1000
        async for chunk in provider.stream(
            segment, request_id=f"bench-{label}-{index}-{position}", utterance_id="bench",
        ):
            if first_chunk_ms is None:
                first_chunk_ms = (time.perf_counter() - started) * 1000
            total_bytes += len(chunk.data)
            sample_rate = chunk.sample_rate

    elapsed = time.perf_counter() - started
    audio_seconds = total_bytes / 2 / max(1, sample_rate)
    return {
        "phrase": label,
        "chars": len(text),
        "segments": len(segments),
        "first_segment_ms": round(first_segment_ms or 0.0, 1),
        "first_chunk_ms": round(first_chunk_ms or 0.0, 1),
        "synthesis_ms": round(elapsed * 1000, 1),
        "audio_ms": round(audio_seconds * 1000, 1),
        "real_time_factor": round(elapsed / audio_seconds, 3) if audio_seconds else None,
        "bytes": total_bytes,
        "sample_rate": sample_rate,
    }


async def run(provider_name: str | None, runs: int) -> dict[str, object]:
    settings = load_tts_settings()
    if provider_name:
        settings = replace(settings, provider=provider_name)

    provider = create_local_tts_provider(settings)
    info = provider.info()
    print(f"Fournisseur : {info.provider} ({info.backend}) device={info.device}")
    print(f"Modèle : {info.model} — voix : {info.voice} — diffusion : {info.streaming}")

    rss_before = _rss_mb()
    load_started = time.perf_counter()
    await provider.warmup()
    warmup_ms = (time.perf_counter() - load_started) * 1000
    rss_after_load = _rss_mb()
    sidecar_rss = _sidecar_rss_mb()
    print(
        f"Chargement du modèle : {warmup_ms:.0f} ms "
        f"(moteur : {sidecar_rss:.0f} Mo résidents)"
    )

    results: list[dict[str, object]] = []
    for label, text in PHRASES.items():
        # Premier passage = à froid pour cette phrase (compilation des noyaux,
        # caches internes) ; les suivants sont à chaud.
        for index in range(runs):
            measure = await _measure(provider, label, text, index)
            measure["state"] = "froid" if index == 0 else "chaud"
            results.append(measure)
            print(
                f"  {label:14s} {measure['state']:5s} "
                f"1er son {measure['first_chunk_ms']:8.1f} ms  "
                f"total {measure['synthesis_ms']:8.1f} ms  "
                f"RTF {measure['real_time_factor']}"
            )

    rss_peak = _rss_mb()
    await provider.close()

    warm = [r for r in results if r["state"] == "chaud"]
    summary = {
        "provider": info.provider,
        "backend": info.backend,
        "device": info.device,
        "model": info.model,
        "voice": info.voice,
        "streaming": info.streaming,
        "sample_rate": info.sample_rate,
        "warmup_ms": round(warmup_ms, 1),
        "rss_before_mb": round(rss_before, 1),
        "rss_after_load_mb": round(rss_after_load, 1),
        "rss_peak_mb": round(rss_peak, 1),
        "engine_rss_mb": round(sidecar_rss, 1),
        "runs": results,
    }
    if warm:
        summary["median_first_chunk_ms_warm"] = round(
            statistics.median(float(r["first_chunk_ms"]) for r in warm), 1
        )
        factors = [r["real_time_factor"] for r in warm if r["real_time_factor"]]
        if factors:
            summary["median_real_time_factor_warm"] = round(
                statistics.median(float(f) for f in factors), 3
            )
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", default="", help="Surcharge TTS_PROVIDER")
    parser.add_argument("--runs", type=int, default=4, help="Passages par phrase")
    parser.add_argument("--json", default="", help="Écrit le rapport dans ce fichier")
    args = parser.parse_args(argv)

    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    try:
        summary = asyncio.run(run(args.provider.strip() or None, max(1, args.runs)))
    except TTSError as exc:
        print(f"\nMoteur vocal indisponible : {exc}", file=sys.stderr)
        return 3

    print(
        f"\nMédiane à chaud — premier son : "
        f"{summary.get('median_first_chunk_ms_warm')} ms, "
        f"facteur temps réel : {summary.get('median_real_time_factor_warm')}"
    )
    print(
        f"Mémoire résidente — JARVIS {summary['rss_after_load_mb']} Mo, "
        f"moteur {summary['engine_rss_mb']} Mo"
    )

    if args.json:
        Path(args.json).write_text(
            json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(f"Rapport écrit : {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
