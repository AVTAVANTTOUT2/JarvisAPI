#!/usr/bin/env python3
"""Mesure hors ligne fin de parole → premier PCM/écriture audio du chemin qualité.

Le STT primaire et le TTS sont préchauffés comme au démarrage du daemon. Le
modèle qualité reste froid ; son chargement et l'accusé anticipé démarrent en
parallèle. Le WAV doit être PCM16 mono et suffisamment difficile pour franchir
le seuil de relecture qualité configuré.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import wave
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _read_pcm16_mono(path: Path) -> tuple[bytes, int]:
    with wave.open(str(path), "rb") as wav:
        channels = wav.getnchannels()
        sample_width = wav.getsampwidth()
        sample_rate = wav.getframerate()
        pcm = wav.readframes(wav.getnframes())
    if channels != 1 or sample_width != 2:
        raise ValueError(
            f"WAV PCM16 mono requis, reçu channels={channels} width={sample_width}"
        )
    return pcm, sample_rate


async def _measure(args: argparse.Namespace) -> dict[str, Any]:
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    os.environ.setdefault("STT_ALLOW_MODEL_DOWNLOAD", "false")
    if args.tts_model_path:
        os.environ["TTS_MODEL_PATH"] = str(args.tts_model_path)
    if args.voice_path:
        os.environ["TTS_VOICE_PATH"] = str(args.voice_path)

    import config
    from audio.stt_daemon import FasterWhisperBackend, _needs_quality_fallback
    from jarvis.audio.tts.config import load_tts_settings
    from jarvis.audio.tts.factory import create_local_tts_provider
    from jarvis.audio.tts.playback import play_chunks

    pcm, sample_rate = _read_pcm16_mono(args.input_wav)
    primary_backend = FasterWhisperBackend(args.primary_model)
    quality_backend = FasterWhisperBackend(args.quality_model)
    provider = create_local_tts_provider(load_tts_settings())

    try:
        await provider.warmup()
        if not primary_backend.preload_sync():
            raise RuntimeError(f"STT primaire indisponible : {args.primary_model}")

        events: dict[str, float] = {}
        started = time.perf_counter()

        def mark(name: str) -> None:
            events.setdefault(name, round((time.perf_counter() - started) * 1000, 1))

        primary = await primary_backend.transcribe_pcm(
            pcm,
            sample_rate=sample_rate,
            language=args.language,
        )
        mark("small_done")
        if primary is None:
            raise RuntimeError("Le STT primaire n'a produit aucun résultat")
        if not _needs_quality_fallback(primary):
            raise RuntimeError(
                "Le WAV ne franchit pas le seuil de relecture qualité : "
                f"text={primary.text!r} avg_logprob={primary.avg_logprob!r}"
            )

        cache_check_started = time.perf_counter()
        cache_complete = quality_backend.is_available_locally()
        cache_check_ms = round((time.perf_counter() - cache_check_started) * 1000, 2)
        if not cache_complete:
            raise RuntimeError(f"Cache qualité incomplet : {args.quality_model}")
        mark("ack_and_quality_started")

        async def ack() -> Any:
            async def chunks():
                async for chunk in provider.stream(
                    args.ack,
                    request_id="voice-latency-benchmark",
                    utterance_id="voice-latency-benchmark",
                ):
                    mark("first_pcm")
                    yield chunk

            if args.no_playback:
                count = 0
                async for _ in chunks():
                    count += 1
                return {"started": False, "chunks": count}

            result = await play_chunks(
                chunks(),
                sample_rate=provider.info().sample_rate,
                on_playback_started=lambda: mark("coreaudio_first_write"),
            )
            return {"started": result.started, "chunks": result.chunks}

        async def quality_replay():
            loaded = await asyncio.to_thread(quality_backend.preload_sync)
            mark("quality_loaded")
            if not loaded:
                return None
            result = await quality_backend.transcribe_pcm(
                pcm,
                sample_rate=sample_rate,
                language=args.language,
            )
            mark("quality_done")
            return result

        playback, quality = await asyncio.gather(ack(), quality_replay())
        mark("all_done")
        first_sound_ms = events.get("coreaudio_first_write", events.get("first_pcm"))

        return {
            "offline": True,
            "input_wav": str(args.input_wav),
            "threshold": config.STT_QUALITY_FALLBACK_LOGPROB,
            "primary": {
                "model": args.primary_model,
                "text": primary.text,
                "avg_logprob": primary.avg_logprob,
                "inference_ms": primary.inference_ms,
            },
            "quality": None if quality is None else {
                "model": args.quality_model,
                "text": quality.text,
                "avg_logprob": quality.avg_logprob,
                "inference_ms": quality.inference_ms,
            },
            "cache_complete": cache_complete,
            "cache_check_ms": cache_check_ms,
            "playback": playback,
            "events_ms": events,
            "first_sound_ms": first_sound_ms,
            "target_ms": 2000,
            "target_met": first_sound_ms is not None and first_sound_ms < 2000,
            "margin_ms": None if first_sound_ms is None else round(2000 - first_sound_ms, 1),
        }
    finally:
        await provider.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_wav", type=Path)
    parser.add_argument("--primary-model", default="small")
    parser.add_argument("--quality-model", default="large-v3-turbo")
    parser.add_argument("--language", default="fr")
    parser.add_argument("--ack", default="Bien, Monsieur.")
    parser.add_argument("--tts-model-path", type=Path)
    parser.add_argument("--voice-path", type=Path)
    parser.add_argument("--no-playback", action="store_true")
    parser.add_argument("--json", type=Path, dest="json_path")
    args = parser.parse_args(argv)

    report = asyncio.run(_measure(args))
    rendered = json.dumps(report, indent=2, ensure_ascii=False)
    if args.json_path:
        args.json_path.parent.mkdir(parents=True, exist_ok=True)
        args.json_path.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if report["target_met"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
