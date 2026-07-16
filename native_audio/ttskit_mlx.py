"""Qwen3-TTS CustomVoice via mlx-audio — logique du sidecar TTSKit.

Utilisé par ``native_audio/ttskit_synthesize`` (lanceur) et les tests unitaires.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

DEFAULT_MODEL_ALIAS = "qwen3-tts-0.6b"
DEFAULT_HF_MODEL = "mlx-community/Qwen3-TTS-12Hz-0.6B-CustomVoice-6bit"
DEFAULT_SPEAKER = "Ryan"
DEFAULT_SAMPLE_RATE = 24000

MODEL_ALIASES: dict[str, str] = {
    "qwen3-tts-0.6b": DEFAULT_HF_MODEL,
    "qwen3-tts-0.6b-customvoice": DEFAULT_HF_MODEL,
    "qwen3-tts-12hz-0.6b-customvoice": DEFAULT_HF_MODEL,
    "qwen3-tts-12hz-0.6b-customvoice-6bit": DEFAULT_HF_MODEL,
}

LANG_ALIASES: dict[str, str] = {
    "fr": "french",
    "fr-fr": "french",
    "fra": "french",
    "french": "french",
    "en": "english",
    "en-us": "english",
    "en-gb": "english",
    "english": "english",
    "auto": "auto",
}


def resolve_model_id(model: str, model_path: str = "") -> str:
    """Résout un alias court ou un chemin local vers un id Hugging Face / path."""
    path = (model_path or "").strip()
    if path:
        return str(Path(path).expanduser())
    key = (model or "").strip()
    if not key:
        return DEFAULT_HF_MODEL
    lowered = key.lower()
    if lowered in MODEL_ALIASES:
        return MODEL_ALIASES[lowered]
    return key


def resolve_language(language: str) -> str:
    """Normalise le code langue vers le vocabulaire Qwen3-TTS."""
    key = (language or "auto").strip().lower().replace("_", "-")
    return LANG_ALIASES.get(key, key)


def resolve_speaker(cli_speaker: str | None = None) -> str:
    """Speaker CustomVoice : CLI > TTS_SPEAKER > défaut Ryan (pas d'émotion)."""
    if cli_speaker and cli_speaker.strip():
        return cli_speaker.strip()
    env = (os.environ.get("TTS_SPEAKER") or "").strip()
    if env and not env.lower().startswith("fr-"):
        return env
    return DEFAULT_SPEAKER


def audio_to_pcm16(audio) -> bytes:
    """Convertit un buffer float mono en PCM signed 16-bit little-endian."""
    import numpy as np

    arr = np.asarray(audio, dtype=np.float32).reshape(-1)
    if arr.size == 0:
        return b""
    arr = np.clip(arr, -1.0, 1.0)
    return (arr * 32767.0).astype(np.int16).tobytes()


def synthesize_stream_pcm(
    *,
    text: str,
    model_id: str,
    language: str,
    speaker: str,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    streaming_interval: float = 0.32,
) -> None:
    """Charge le modèle, génère en streaming, écrit le PCM16 sur stdout."""
    from mlx_audio.tts.utils import load_model

    if sample_rate != DEFAULT_SAMPLE_RATE:
        print(
            f"[ttskit] sample-rate {sample_rate} non supporté — forcé à {DEFAULT_SAMPLE_RATE}",
            file=sys.stderr,
        )

    print(
        f"[ttskit] model={model_id} lang={language} speaker={speaker}",
        file=sys.stderr,
    )
    model = load_model(model_id)
    # Pas d'instruct : voix stable, sans balises émotionnelles.
    for result in model.generate(
        text=text,
        voice=speaker,
        instruct=None,
        lang_code=language,
        stream=True,
        streaming_interval=streaming_interval,
        verbose=False,
    ):
        audio = getattr(result, "audio", None)
        if audio is None:
            continue
        pcm = audio_to_pcm16(audio)
        if pcm:
            sys.stdout.buffer.write(pcm)
            sys.stdout.buffer.flush()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="JARVIS TTSKit sidecar (Qwen3-TTS MLX)")
    parser.add_argument("--model", default=DEFAULT_MODEL_ALIAS)
    parser.add_argument("--language", default="fr")
    parser.add_argument("--format", default="pcm_s16le")
    parser.add_argument("--sample-rate", type=int, default=DEFAULT_SAMPLE_RATE)
    parser.add_argument("--text", required=True)
    parser.add_argument("--model-path", default="")
    parser.add_argument(
        "--speaker",
        default="",
        help="Voix CustomVoice (sinon TTS_SPEAKER ou Ryan)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.format != "pcm_s16le":
        print(f"[ttskit] format non supporté: {args.format}", file=sys.stderr)
        return 2

    text = (args.text or "").strip()
    if not text:
        print("[ttskit] texte vide", file=sys.stderr)
        return 2

    model_id = resolve_model_id(args.model, args.model_path)
    language = resolve_language(args.language)
    speaker = resolve_speaker(args.speaker or None)

    try:
        synthesize_stream_pcm(
            text=text,
            model_id=model_id,
            language=language,
            speaker=speaker,
            sample_rate=args.sample_rate,
        )
    except BrokenPipeError:
        return 0
    except Exception as exc:  # noqa: BLE001 — sidecar : tout échec → code 1 + stderr
        print(f"[ttskit] erreur: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
