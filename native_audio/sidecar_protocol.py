"""Protocole commun des sidecars de synthèse — trames, stdout, PCM, poids.

Un sidecar de synthèse fait toujours les mêmes quatre choses, quel que soit le
moteur : il réserve stdout pour du binaire, il résout des poids locaux sans
jamais télécharger, il convertit du float en PCM16, et il parle un protocole de
trames. Ce module porte ces quatre choses une seule fois.

Ce qui reste propre à chaque moteur — le chargement du modèle, la voix, les
paramètres d'échantillonnage — vit dans le module du moteur. La frontière est
volontairement là : ajouter un backend ne doit pas obliger à recopier un
protocole binaire, parce qu'une copie qui dérive d'un octet produit un flux
audio corrompu sans lever la moindre exception.
"""

from __future__ import annotations

import io
import json
import os
import struct
import sys
from pathlib import Path
from typing import Any

# ── Trames ──────────────────────────────────────────────────────────────────
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


class ModelMissing(RuntimeError):
    """Poids absents ou incomplets — action humaine requise, jamais un repli."""


def missing_error(reason: str, install_hint: str) -> ModelMissing:
    """Erreur de poids manquants — **toujours** avec le geste à faire.

    Un message qui n'indique pas quoi faire oblige à lire le code. Cette
    fabrique existe pour qu'aucun chemin de sortie ne puisse l'oublier.
    """
    return ModelMissing(f"{reason} Installation : {install_hint}")


def _looks_like_a_path(raw: str) -> bool:
    """Le réglage désigne-t-il un répertoire plutôt qu'un dépôt distant ?

    Un identifiant Hugging Face s'écrit ``organisation/modele`` ; un chemin
    commence par une racine, un point ou un tilde. La distinction évite de
    parler de « huggingface_hub » à quelqu'un qui a simplement écrit un chemin
    qui n'existe pas.
    """
    return raw.startswith(("/", "./", "../", "~")) or raw in {".", ".."}


def missing_pieces(
    model_dir: Path,
    *,
    required_files: tuple[str, ...],
    weight_globs: tuple[str, ...],
) -> list[str]:
    """Fichiers requis absents ou visiblement tronqués.

    Un répertoire de cache peut exister avec seulement les petits fichiers : un
    téléchargement interrompu s'arrête presque toujours sur les poids. Sans
    cette vérification, l'installation serait déclarée présente et l'échec
    n'apparaîtrait qu'au premier énoncé, dans une trace MLX illisible.
    """
    missing = [name for name in required_files if not (model_dir / name).is_file()]
    for pattern in weight_globs:
        if not any(model_dir.glob(pattern)):
            missing.append(pattern)
    if any(model_dir.rglob("*.incomplete")):
        missing.append("téléchargement interrompu (*.incomplete)")
    return missing


def resolve_local_model_dir(
    spec: str,
    *,
    default_model: str,
    install_hint: str,
    required_files: tuple[str, ...],
    weight_globs: tuple[str, ...] = ("model*.safetensors",),
) -> Path:
    """Chemin local des poids, sans jamais déclencher de téléchargement.

    ``spec`` est soit un répertoire, soit un identifiant de dépôt Hugging Face
    déjà présent dans le cache local. Tout le reste lève ``ModelMissing``.
    """
    raw = (spec or default_model).strip()
    candidate = Path(raw).expanduser()
    if candidate.is_dir():
        resolved = candidate
    elif _looks_like_a_path(raw):
        # Chemin explicite et absent : inutile de parler d'un cache distant.
        raise missing_error(
            f"répertoire de poids introuvable : {candidate}.", install_hint
        )
    else:
        try:
            from huggingface_hub import snapshot_download
            from huggingface_hub.errors import (  # type: ignore[attr-defined]
                LocalEntryNotFoundError,
            )
        except ImportError as exc:
            raise missing_error(
                f"modèle « {raw} » introuvable localement, et huggingface_hub "
                f"n'est pas installé pour consulter le cache.",
                install_hint,
            ) from exc

        try:
            resolved = Path(snapshot_download(raw, local_files_only=True))
        except (LocalEntryNotFoundError, OSError, ValueError) as exc:
            raise missing_error(
                f"modèle « {raw} » absent du cache local.", install_hint
            ) from exc

    missing = missing_pieces(
        resolved, required_files=required_files, weight_globs=weight_globs
    )
    if missing:
        raise missing_error(
            f"modèle « {raw} » incomplet dans {resolved} — manque : "
            f"{', '.join(missing)}.",
            install_hint,
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


def serve_loop(out: Any, synthesize, *, label: str) -> int:
    """Boucle serveur : une requête JSON par ligne, des trames PCM en retour.

    Chaque requête produit N trames ``CHK`` puis une trame ``END``. Une erreur
    produit ``ERR`` puis ``END`` : le client n'attend jamais indéfiniment, même
    sur un texte impossible à synthétiser.
    """
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
            for pcm in synthesize(request):
                out.write(encode_frame(TAG_CHUNK, pcm))
                out.flush()  # sans ça, le premier fragment attendrait le dernier
        except BrokenPipeError:
            return 0
        except Exception as exc:  # noqa: BLE001 — un texte fautif ne tue pas le serveur
            message = f"{type(exc).__name__}: {exc}"
            print(f"[{label}] erreur synthese: {message}", file=sys.stderr)
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


__all__ = [
    "FRAME_HEADER",
    "TAG_CHUNK",
    "TAG_END",
    "TAG_ERROR",
    "TAG_READY",
    "ModelMissing",
    "audio_to_pcm16",
    "claim_binary_stdout",
    "encode_frame",
    "missing_error",
    "missing_pieces",
    "pcm16_to_wav",
    "resolve_local_model_dir",
    "serve_loop",
]
