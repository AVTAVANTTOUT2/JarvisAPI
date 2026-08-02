"""Fish Audio S2 Pro local (MLX) — logique du sidecar de synthèse.

Exécuté par ``native_audio/fish_synthesize`` sous ``JARVIS_VENV`` (le venv qui
porte ``mlx-audio``), jamais dans l'interpréteur de JARVIS : les deux
environnements n'ont ni la même version de Python ni les mêmes dépendances.

Deux modes :

- **serveur** (``--serve``, celui qu'utilise le pipeline) : le modèle est
  chargé **une seule fois**, puis chaque requête JSON lue sur stdin produit des
  trames PCM sur stdout. C'est ce qui évite de repayer plusieurs secondes de
  chargement à chaque réponse ;
- **one-shot** : une synthèse, PCM16 ou WAV sur stdout — pour le diagnostic et
  le banc de mesure.

**Aucun téléchargement.** ``resolve_local_model_dir`` n'accepte qu'un chemin
existant ou un dépôt déjà présent dans le cache Hugging Face local ; l'appel
part avec ``HF_HUB_OFFLINE=1``. Un modèle absent produit une erreur explicite,
jamais un téléchargement de plusieurs gigaoctets au milieu d'un tour de parole.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import struct
import sys
from pathlib import Path
from typing import Any

DEFAULT_MODEL = "mlx-community/fish-audio-s2-pro-8bit"
DEFAULT_SAMPLE_RATE = 44100
DEFAULT_MAX_TOKENS = 1024
DEFAULT_TEMPERATURE = 0.7
DEFAULT_TOP_P = 0.7
DEFAULT_TOP_K = 30

# Le texte est déjà segmenté côté JARVIS (`jarvis.audio.tts.segmenter`). Une
# valeur haute ici empêche le modèle de re-découper un segment déjà court, ce
# qui ajouterait une génération et donc de la latence.
DEFAULT_CHUNK_LENGTH = 400

WARMUP_TEXT = "Bonjour."

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


class FishModelMissing(RuntimeError):
    """Poids absents ou incomplets — action humaine requise, pas un repli."""


# Fichiers sans lesquels le moteur ne peut pas parler. Un répertoire de cache
# peut exister avec seulement les petits fichiers (un téléchargement
# interrompu s'arrête presque toujours sur les poids) : sans cette
# vérification, l'installation serait déclarée présente et l'échec
# n'apparaîtrait qu'au premier énoncé, dans une trace MLX illisible.
REQUIRED_FILES: tuple[str, ...] = ("config.json", "tokenizer.json", "codec.safetensors")
WEIGHT_GLOB = "model*.safetensors"

INSTALL_HINT = "python scripts/download_tts_model.py"


def _missing_pieces(model_dir: Path) -> list[str]:
    """Fichiers requis absents ou visiblement tronqués."""
    missing = [name for name in REQUIRED_FILES if not (model_dir / name).is_file()]
    if not any(model_dir.glob(WEIGHT_GLOB)):
        missing.append(WEIGHT_GLOB)
    if any(model_dir.glob("*.incomplete")):
        missing.append("téléchargement interrompu (*.incomplete)")
    return missing


def resolve_local_model_dir(spec: str) -> Path:
    """Chemin local des poids, sans jamais déclencher de téléchargement.

    ``spec`` est soit un répertoire, soit un identifiant de dépôt Hugging Face
    déjà présent dans le cache local. Tout le reste lève ``FishModelMissing``
    avec la commande exacte à lancer — un message d'erreur qui n'indique pas
    quoi faire oblige à lire le code.
    """
    raw = (spec or DEFAULT_MODEL).strip()
    candidate = Path(raw).expanduser()
    if candidate.is_dir():
        resolved = candidate
    else:
        try:
            from huggingface_hub import snapshot_download
            from huggingface_hub.errors import (  # type: ignore[attr-defined]
                LocalEntryNotFoundError,
            )
        except ImportError as exc:  # pragma: no cover - dépend de l'environnement
            raise FishModelMissing(
                f"huggingface_hub absent : impossible de localiser {raw}"
            ) from exc

        try:
            resolved = Path(snapshot_download(raw, local_files_only=True))
        except (LocalEntryNotFoundError, OSError, ValueError) as exc:
            raise FishModelMissing(
                f"modèle « {raw} » absent du cache local. "
                f"Installation (une seule fois, hors conversation) : {INSTALL_HINT}"
            ) from exc

    missing = _missing_pieces(resolved)
    if missing:
        raise FishModelMissing(
            f"modèle « {raw} » incomplet dans {resolved} — manque : "
            f"{', '.join(missing)}. Reprise : {INSTALL_HINT}"
        )
    return resolved


def claim_binary_stdout() -> Any:
    """Réserve stdout pour le flux binaire et renvoie les logs vers stderr.

    Un simple ``sys.stdout = sys.stderr`` ne suffirait pas : mlx-audio et ses
    dépendances écrivent parfois sur le **descripteur** 1 directement. On
    duplique donc le vrai stdout pour nous, puis on fait pointer le descripteur
    1 vers stderr — toute impression parasite part dans les logs, et le PCM
    reste intact.

    Le faire une fois pour toutes (plutôt qu'un contexte autour de chaque
    génération) est ce qui permet de **diffuser** les fragments au fil de leur
    production : avec un contexte, il faudrait attendre la fin de la génération
    avant de pouvoir écrire quoi que ce soit.
    """
    binary_out = os.fdopen(os.dup(sys.stdout.fileno()), "wb")
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    sys.stdout = sys.stderr
    return binary_out


def audio_to_pcm16(audio: Any) -> bytes:
    """Convertit un buffer float mono en PCM signed 16-bit little-endian."""
    import numpy as np

    arr = np.asarray(audio, dtype=np.float32).reshape(-1)
    if arr.size == 0:
        return b""
    arr = np.clip(arr, -1.0, 1.0)
    return (arr * 32767.0).astype(np.int16).tobytes()


def pcm16_to_wav(pcm: bytes, sample_rate: int) -> bytes:
    """Encapsule du PCM16 mono en WAV (diagnostic et banc de mesure)."""
    import wave

    buf = io.BytesIO()
    with wave.open(buf, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(int(sample_rate))
        handle.writeframes(pcm)
    return buf.getvalue()


class FishSpeechServer:
    """Modèle Fish chargé une fois, puis une synthèse par requête.

    Chaque requête produit N trames ``CHK`` (PCM16 mono) puis une trame
    ``END``. Une erreur produit ``ERR`` puis ``END`` : le client n'attend
    jamais indéfiniment, même sur un texte impossible.
    """

    def __init__(
        self,
        model_dir: Path,
        *,
        ref_audio: Path | None = None,
        ref_text: str | None = None,
    ) -> None:
        self._model_dir = model_dir
        self._ref_audio_path = ref_audio
        self._ref_text = ref_text
        self._model: Any = None
        self._ref_audio: Any = None
        self._sample_rate = DEFAULT_SAMPLE_RATE

    # ── Chargement ──────────────────────────────────────────────────────────

    def load(self) -> None:
        """Charge le modèle, le codec et la voix, puis compile par un essai.

        La synthèse à blanc n'est pas décorative : le premier appel réel
        paierait sinon la compilation des noyaux MLX, soit plusieurs secondes
        au pire moment.
        """
        from mlx_audio.tts.utils import load_model

        self._model = load_model(str(self._model_dir))
        declared = int(getattr(self._model, "sample_rate", 0) or 0)
        if declared > 0:
            self._sample_rate = declared
        self._load_reference()
        try:
            for _ in self._generate(WARMUP_TEXT):
                break
        except Exception as exc:  # noqa: BLE001 — le warmup ne tue pas le serveur
            print(f"[fish-local] warmup ignoré: {exc}", file=sys.stderr)

    def _load_reference(self) -> None:
        """Charge l'échantillon de voix s'il existe — optionnel par choix.

        Sans référence, le modèle parle avec sa voix par défaut. C'est un
        compromis assumé : refuser de parler tant qu'aucune voix personnalisée
        n'est déposée rendrait JARVIS muet à l'installation.
        """
        if self._ref_audio_path is None or not self._ref_audio_path.is_file():
            return
        try:
            import mlx.core as mx
            import soundfile as sf

            samples, rate = sf.read(str(self._ref_audio_path), dtype="float32")
            if samples.ndim > 1:
                samples = samples.mean(axis=1)
            self._ref_audio = mx.array(samples)
            print(
                f"[fish-local] voix de référence chargée "
                f"({len(samples)} échantillons @ {rate} Hz)",
                file=sys.stderr,
            )
        except Exception as exc:  # noqa: BLE001 — voix illisible ≠ panne du moteur
            print(f"[fish-local] voix de référence ignorée: {exc}", file=sys.stderr)
            self._ref_audio = None

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    # ── Synthèse ────────────────────────────────────────────────────────────

    def _generate(self, text: str, **overrides: Any) -> Any:
        if self._model is None:
            raise RuntimeError("modèle non chargé")
        return self._model.generate(
            text=text,
            ref_audio=self._ref_audio,
            ref_text=self._ref_text if self._ref_audio is not None else None,
            max_tokens=int(overrides.get("max_tokens", DEFAULT_MAX_TOKENS)),
            temperature=float(overrides.get("temperature", DEFAULT_TEMPERATURE)),
            top_p=float(overrides.get("top_p", DEFAULT_TOP_P)),
            top_k=int(overrides.get("top_k", DEFAULT_TOP_K)),
            chunk_length=int(overrides.get("chunk_length", DEFAULT_CHUNK_LENGTH)),
            verbose=False,
        )

    def synthesize_chunks(self, request: dict[str, Any]) -> Any:
        """Génère le PCM segment par segment (générateur de bytes)."""
        text = str(request.get("text") or "").strip()
        if not text:
            return
        for result in self._generate(
            text,
            max_tokens=request.get("max_tokens", DEFAULT_MAX_TOKENS),
            temperature=request.get("temperature", DEFAULT_TEMPERATURE),
            top_p=request.get("top_p", DEFAULT_TOP_P),
            top_k=request.get("top_k", DEFAULT_TOP_K),
            chunk_length=request.get("chunk_length", DEFAULT_CHUNK_LENGTH),
        ):
            audio = getattr(result, "audio", None)
            if audio is None:
                continue
            rate = int(getattr(result, "sample_rate", 0) or 0)
            if rate > 0:
                self._sample_rate = rate
            pcm = audio_to_pcm16(audio)
            if pcm:
                yield pcm

    # ── Boucle ──────────────────────────────────────────────────────────────

    def serve(self) -> int:
        out = claim_binary_stdout()
        self.load()
        ready = json.dumps({
            "sample_rate": self._sample_rate,
            "channels": 1,
            "sample_format": "pcm_s16le",
            "device": "mlx",
            "voice_cloned": self._ref_audio is not None,
        }).encode("utf-8")
        out.write(encode_frame(TAG_READY, ready))
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
                print(f"[fish-local] erreur synthese: {message}", file=sys.stderr)
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
    parser = argparse.ArgumentParser(description="JARVIS Fish Audio local (MLX)")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--ref-audio", default="", help="Échantillon de voix (WAV)")
    parser.add_argument("--ref-text", default="", help="Transcript de l'échantillon")
    parser.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS)
    parser.add_argument("--temperature", type=float, default=DEFAULT_TEMPERATURE)
    parser.add_argument(
        "--format",
        default="pcm_s16le",
        choices=("wav", "pcm_s16le"),
        help="pcm_s16le (défaut) ou wav (header RIFF)",
    )
    parser.add_argument("--text", default="", help="Texte à synthétiser (sinon stdin)")
    parser.add_argument(
        "--serve",
        action="store_true",
        help="Mode serveur : modèle chargé une fois, requêtes JSON sur stdin",
    )
    parser.add_argument(
        "--probe",
        action="store_true",
        help="Vérifie la présence locale du modèle et sort (JSON sur stdout)",
    )
    return parser


def _server_from_args(args: argparse.Namespace) -> FishSpeechServer:
    ref_audio = Path(args.ref_audio).expanduser() if args.ref_audio else None
    ref_text = (args.ref_text or "").strip() or None
    return FishSpeechServer(
        resolve_local_model_dir(args.model),
        ref_audio=ref_audio,
        ref_text=ref_text,
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    # Ceinture et bretelles : même si un chemin d'appel oubliait la résolution
    # locale, la bibliothèque Hugging Face n'a pas le droit de sortir.
    os.environ.setdefault("HF_HUB_OFFLINE", "1")

    if args.probe:
        try:
            path = resolve_local_model_dir(args.model)
        except FishModelMissing as exc:
            print(json.dumps({"available": False, "reason": str(exc)}))
            return 1
        print(json.dumps({"available": True, "model_dir": str(path)}))
        return 0

    if args.serve:
        try:
            return _server_from_args(args).serve()
        except FishModelMissing as exc:
            print(f"[fish-local] {exc}", file=sys.stderr)
            return 3
        except (BrokenPipeError, KeyboardInterrupt):
            return 0
        except Exception as exc:  # noqa: BLE001 — sidecar : tout échec → code 1
            print(f"[fish-local] serveur: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1

    text = (args.text or "").strip() or sys.stdin.read().strip()
    if not text:
        print("[fish-local] texte vide", file=sys.stderr)
        return 2

    binary_out = claim_binary_stdout()
    try:
        server = _server_from_args(args)
        server.load()
        pcm = b"".join(server.synthesize_chunks({
            "text": text,
            "max_tokens": args.max_tokens,
            "temperature": args.temperature,
        }))
    except FishModelMissing as exc:
        print(f"[fish-local] {exc}", file=sys.stderr)
        return 3
    except Exception as exc:  # noqa: BLE001 — sidecar : tout échec → code 1
        print(f"[fish-local] erreur: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    if not pcm:
        print("[fish-local] aucune sortie audio", file=sys.stderr)
        return 1

    payload = pcm16_to_wav(pcm, server.sample_rate) if args.format == "wav" else pcm
    try:
        binary_out.write(payload)
        binary_out.flush()
    except BrokenPipeError:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
