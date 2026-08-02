"""Kokoro-82M via mlx-audio — logique du sidecar TTS local FR.

Utilisé par ``native_audio/kokoro_synthesize`` (lanceur) et les tests unitaires.

Deux modes :

- **one-shot** (historique) : une synthèse, WAV PCM16 mono sur stdout (ou PCM
  brut si ``--format pcm_s16le``).
- **serveur** (``--serve``) : le processus reste en vie, charge le modèle une
  seule fois, puis répond aux requêtes JSON lues sur stdin en diffusant des
  trames PCM sur stdout. C'est ce mode qui supprime le rechargement du modèle à
  chaque réponse — coût dominant du TTS mesuré (~6 s pour 138 caractères).
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import re
import struct
import sys
from pathlib import Path
from typing import Any

DEFAULT_MODEL = "mlx-community/Kokoro-82M-bf16"
DEFAULT_VOICE = "ff_siwis"
DEFAULT_LANG_CODE = "f"
DEFAULT_SPEED = 0.96
DEFAULT_MAX_TOKENS = 180
DEFAULT_SAMPLE_RATE = 24000

# Premier fragment volontairement court : la première phrase part en lecture
# pendant que la suite se synthétise. Au-delà, on regroupe pour l'efficacité.
DEFAULT_FIRST_CHUNK_MAX_TOKENS = 12

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?…])\s+|\n+")

# ── Protocole du mode serveur ───────────────────────────────────────────────
# Trame = tag ASCII 4 octets + longueur big-endian 4 octets + charge utile.
# Binaire de bout en bout : aucun encodage texte ne peut corrompre le PCM.
FRAME_HEADER = struct.Struct(">4sI")
TAG_READY = b"RDY\0"
TAG_CHUNK = b"CHK\0"
TAG_END = b"END\0"
TAG_ERROR = b"ERR\0"


def encode_frame(tag: bytes, payload: bytes = b"") -> bytes:
    """Encode une trame du protocole serveur."""
    return FRAME_HEADER.pack(tag, len(payload)) + payload


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


def split_text_for_kokoro(
    text: str,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    first_chunk_max_tokens: int | None = None,
) -> list[str]:
    """Segments ≤ ``max_tokens``, le premier plafonné séparément si demandé.

    mlx-audio (EspeakG2P FR) ne chunk pas encore : sans découpage, les longs
    textes sont tronqués. ``first_chunk_max_tokens`` sert au streaming : un
    premier segment court part en lecture pendant que le reste se synthétise.
    """
    cleaned = (text or "").strip()
    if not cleaned:
        return []
    limit = max(1, int(max_tokens))
    first_limit = max(1, int(first_chunk_max_tokens)) if first_chunk_max_tokens else limit
    sentences = [s.strip() for s in _SENTENCE_SPLIT.split(cleaned) if s and s.strip()]
    if not sentences:
        return [cleaned]

    chunks: list[str] = []
    current: list[str] = []
    current_tokens = 0

    def _active_limit() -> int:
        return first_limit if not chunks else limit

    def _flush() -> None:
        nonlocal current, current_tokens
        if current:
            chunks.append(" ".join(current))
            current = []
            current_tokens = 0

    for sentence in sentences:
        n = estimate_tokens(sentence)
        if n > _active_limit():
            # Une phrase seule dépasse le plafond : on la coupe en mots, mais
            # jamais avant d'avoir écoulé ce qui l'attendait.
            _flush()
            words = sentence.split()
            start = 0
            while start < len(words):
                size = _active_limit()
                chunks.append(" ".join(words[start : start + size]))
                start += size
            continue
        if current and current_tokens + n > _active_limit():
            _flush()
        current.append(sentence)
        current_tokens += n
        if len(chunks) == 0 and current_tokens >= first_limit:
            _flush()
    _flush()
    return chunks


def chunk_text_for_kokoro(
    text: str,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    first_chunk_max_tokens: int | None = None,
) -> str:
    """Même découpage, rendu sous forme d'un texte séparé par ``\\n``."""
    return "\n".join(split_text_for_kokoro(text, max_tokens, first_chunk_max_tokens))


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


@contextlib.contextmanager
def _silence_stdout() -> Any:
    """mlx-audio écrit sur stdout ; stdout porte ici le flux binaire."""
    old = sys.stdout
    sys.stdout = sys.stderr
    try:
        yield
    finally:
        sys.stdout = old


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
    """Charge Kokoro MLX, génère l'audio, retourne WAV ou PCM16.

    mlx-audio imprime des messages (``Creating new KokoroPipeline…``) sur
    stdout : on redirige stdout → stderr pendant la génération pour ne pas
    polluer le flux binaire du sidecar.
    """
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

    with _silence_stdout():
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


class KokoroServer:
    """Modèle chargé une fois, puis une synthèse par requête lue sur stdin.

    Chaque requête produit N trames ``CHK`` (PCM16 24 kHz, un fragment de texte
    chacune) puis une trame ``END``. Une erreur produit ``ERR`` puis ``END`` :
    le client n'attend jamais indéfiniment, même sur un texte impossible.
    """

    def __init__(self, model_id: str, *, warmup_text: str = "Bonjour.") -> None:
        self._model_id = model_id
        self._warmup_text = warmup_text
        self._model: Any = None
        self._sample_rate = DEFAULT_SAMPLE_RATE

    # ── Chargement ──────────────────────────────────────────────────────────

    def load(self, *, voice: str, lang_code: str, speed: float) -> None:
        """Charge le modèle et exécute une synthèse à blanc.

        Le premier appel réel paierait sinon la compilation MLX et
        l'initialisation espeak — précisément ce qu'on veut sortir du chemin
        de réponse.
        """
        from mlx_audio.tts.utils import load_model

        with _silence_stdout():
            self._model = load_model(self._model_id)
            try:
                for _ in self._model.generate(
                    text=self._warmup_text, voice=voice, speed=speed, lang_code=lang_code,
                ):
                    break
            except Exception as exc:  # noqa: BLE001 — le warmup ne doit pas tuer le serveur
                print(f"[kokoro-mlx] warmup ignoré: {exc}", file=sys.stderr)

    # ── Synthèse ────────────────────────────────────────────────────────────

    def synthesize_chunks(self, request: dict[str, Any]) -> Any:
        """Génère le PCM fragment par fragment (générateur de bytes)."""
        import numpy as np

        text = str(request.get("text") or "").strip()
        if not text:
            return
        voice = str(request.get("voice") or DEFAULT_VOICE)
        lang_code = str(request.get("lang_code") or DEFAULT_LANG_CODE)
        speed = float(request.get("speed") or DEFAULT_SPEED)
        max_tokens = int(request.get("max_tokens") or DEFAULT_MAX_TOKENS)
        first_max = int(
            request.get("first_chunk_max_tokens") or DEFAULT_FIRST_CHUNK_MAX_TOKENS
        )

        for fragment in split_text_for_kokoro(text, max_tokens, first_max):
            with _silence_stdout():
                pieces: list[Any] = []
                for result in self._model.generate(
                    text=fragment, voice=voice, speed=speed, lang_code=lang_code,
                ):
                    audio = getattr(result, "audio", None)
                    if audio is None:
                        continue
                    pieces.append(np.asarray(audio, dtype=np.float32).reshape(-1))
                    sr = int(getattr(result, "sample_rate", 0) or 0)
                    if sr > 0:
                        self._sample_rate = sr
            if not pieces:
                continue
            joined = np.concatenate(pieces) if len(pieces) > 1 else pieces[0]
            pcm = audio_to_pcm16(joined)
            if pcm:
                yield pcm

    # ── Boucle ──────────────────────────────────────────────────────────────

    def serve(self, *, voice: str, lang_code: str, speed: float) -> int:
        out = sys.stdout.buffer
        self.load(voice=voice, lang_code=lang_code, speed=speed)
        out.write(encode_frame(TAG_READY))
        out.flush()

        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                request = json.loads(line)
            except (ValueError, TypeError) as exc:
                out.write(encode_frame(TAG_ERROR, f"requete invalide: {exc}".encode()))
                out.write(encode_frame(TAG_END))
                out.flush()
                continue

            try:
                for pcm in self.synthesize_chunks(request):
                    out.write(encode_frame(TAG_CHUNK, pcm))
                    out.flush()  # sans ça, le premier fragment attendrait le dernier
            except BrokenPipeError:
                return 0
            except Exception as exc:  # noqa: BLE001 — un texte fautif ne tue pas le serveur
                message = f"{type(exc).__name__}: {exc}"
                print(f"[kokoro-mlx] erreur synthese: {message}", file=sys.stderr)
                try:
                    out.write(encode_frame(TAG_ERROR, message.encode()[:500]))
                except BrokenPipeError:
                    return 0
            try:
                out.write(encode_frame(TAG_END))
                out.flush()
            except BrokenPipeError:
                return 0
        return 0


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
    parser.add_argument(
        "--serve",
        action="store_true",
        help="Mode serveur : modèle chargé une fois, requêtes JSON sur stdin",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.serve:
        server = KokoroServer(resolve_model_id(args.model))
        try:
            return server.serve(
                voice=(args.voice or DEFAULT_VOICE).strip() or DEFAULT_VOICE,
                lang_code=(args.lang_code or DEFAULT_LANG_CODE).strip() or DEFAULT_LANG_CODE,
                speed=float(args.speed),
            )
        except (BrokenPipeError, KeyboardInterrupt):
            return 0
        except Exception as exc:  # noqa: BLE001 — sidecar : tout échec → code 1
            print(f"[kokoro-mlx] serveur: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1

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
