"""Kokoro-82M via mlx-audio — logique du sidecar TTS local FR.

Utilisé par ``native_audio/kokoro_synthesize`` (lanceur) et les tests unitaires.
Sortie : WAV PCM16 mono sur stdout (ou PCM brut si ``--format pcm_s16le``).
"""

from __future__ import annotations

import argparse
import io
import re
import sys
from pathlib import Path

DEFAULT_MODEL = "mlx-community/Kokoro-82M-bf16"
DEFAULT_VOICE = "ff_siwis"
DEFAULT_LANG_CODE = "f"
DEFAULT_SPEED = 0.96
DEFAULT_MAX_TOKENS = 180
DEFAULT_SAMPLE_RATE = 24000

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?…])\s+|\n+")


def resolve_model_id(model: str) -> str:
    """Résout un id Hugging Face ou un chemin local."""
    key = (model or "").strip()
    if not key:
        return DEFAULT_MODEL
    path = Path(key).expanduser()
    if path.exists():
        return str(path)
    return key


def estimate_tokens(text: str) -> int:
    """Proxy tokens pour le découpage G2P (mots ≈ tokens phonétiques FR)."""
    parts = (text or "").split()
    return max(1, len(parts)) if text and text.strip() else 0


def chunk_text_for_kokoro(text: str, max_tokens: int = DEFAULT_MAX_TOKENS) -> str:
    """Découpe le texte en segments ≤ ``max_tokens``, séparés par ``\\n``.

    mlx-audio (EspeakG2P FR) ne chunk pas encore : sans ``\\n``, les longs
    textes sont tronqués. Chaque segment respecte le plafond demandé.
    """
    cleaned = (text or "").strip()
    if not cleaned:
        return ""
    limit = max(1, int(max_tokens))
    sentences = [s.strip() for s in _SENTENCE_SPLIT.split(cleaned) if s and s.strip()]
    if not sentences:
        return cleaned

    chunks: list[str] = []
    current: list[str] = []
    current_tokens = 0

    def _flush() -> None:
        nonlocal current, current_tokens
        if current:
            chunks.append(" ".join(current))
            current = []
            current_tokens = 0

    for sentence in sentences:
        n = estimate_tokens(sentence)
        if n > limit:
            _flush()
            words = sentence.split()
            for i in range(0, len(words), limit):
                chunks.append(" ".join(words[i : i + limit]))
            continue
        if current and current_tokens + n > limit:
            _flush()
        current.append(sentence)
        current_tokens += n
    _flush()
    return "\n".join(chunks)


def audio_to_pcm16(audio) -> bytes:
    """Convertit un buffer float mono en PCM signed 16-bit little-endian."""
    import numpy as np

    arr = np.asarray(audio, dtype=np.float32).reshape(-1)
    if arr.size == 0:
        return b""
    arr = np.clip(arr, -1.0, 1.0)
    return (arr * 32767.0).astype(np.int16).tobytes()


def audio_to_wav_bytes(audio, sample_rate: int = DEFAULT_SAMPLE_RATE) -> bytes:
    """Encode un buffer float mono en WAV PCM16 complet (stdlib ``wave``)."""
    import wave

    import numpy as np

    arr = np.asarray(audio, dtype=np.float32).reshape(-1)
    if arr.size == 0:
        return b""
    pcm = np.clip(arr, -1.0, 1.0)
    pcm_i16 = (pcm * 32767.0).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(int(sample_rate))
        wf.writeframes(pcm_i16.tobytes())
    return buf.getvalue()


def synthesize(
    *,
    text: str,
    model_id: str,
    voice: str,
    lang_code: str,
    speed: float,
    max_tokens: int,
    audio_format: str = "wav",
) -> bytes:
    """Charge Kokoro MLX, génère l'audio, retourne WAV ou PCM16."""
    import numpy as np
    from mlx_audio.tts.utils import load_model

    prepared = chunk_text_for_kokoro(text, max_tokens=max_tokens)
    if not prepared:
        return b""

    print(
        f"[kokoro-mlx] model={model_id} voice={voice} lang={lang_code} "
        f"speed={speed} max_tokens={max_tokens}",
        file=sys.stderr,
    )
    model = load_model(model_id)
    segments: list = []
    sample_rate = DEFAULT_SAMPLE_RATE
    for result in model.generate(
        text=prepared,
        voice=voice,
        speed=speed,
        lang_code=lang_code,
    ):
        audio = getattr(result, "audio", None)
        if audio is None:
            continue
        segments.append(np.asarray(audio, dtype=np.float32).reshape(-1))
        sr = int(getattr(result, "sample_rate", 0) or 0)
        if sr > 0:
            sample_rate = sr

    if not segments:
        return b""
    joined = np.concatenate(segments) if len(segments) > 1 else segments[0]
    if audio_format == "pcm_s16le":
        return audio_to_pcm16(joined)
    return audio_to_wav_bytes(joined, sample_rate=sample_rate)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="JARVIS Kokoro MLX sidecar")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--voice", default=DEFAULT_VOICE)
    parser.add_argument("--lang-code", default=DEFAULT_LANG_CODE)
    parser.add_argument("--speed", type=float, default=DEFAULT_SPEED)
    parser.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS)
    parser.add_argument(
        "--format",
        default="wav",
        choices=("wav", "pcm_s16le"),
        help="wav (défaut, header RIFF) ou pcm_s16le brut 24 kHz",
    )
    parser.add_argument("--text", default="", help="Texte à synthétiser (sinon stdin)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    text = (args.text or "").strip()
    if not text:
        text = sys.stdin.read().strip()
    if not text:
        print("[kokoro-mlx] texte vide", file=sys.stderr)
        return 2

    try:
        audio = synthesize(
            text=text,
            model_id=resolve_model_id(args.model),
            voice=(args.voice or DEFAULT_VOICE).strip() or DEFAULT_VOICE,
            lang_code=(args.lang_code or DEFAULT_LANG_CODE).strip() or DEFAULT_LANG_CODE,
            speed=float(args.speed),
            max_tokens=int(args.max_tokens),
            audio_format=args.format,
        )
    except BrokenPipeError:
        return 0
    except Exception as exc:  # noqa: BLE001 — sidecar : tout échec → code 1
        print(f"[kokoro-mlx] erreur: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    if not audio:
        print("[kokoro-mlx] aucune sortie audio", file=sys.stderr)
        return 1

    try:
        sys.stdout.buffer.write(audio)
        sys.stdout.buffer.flush()
    except BrokenPipeError:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
