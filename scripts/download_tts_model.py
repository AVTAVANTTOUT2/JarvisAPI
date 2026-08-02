#!/usr/bin/env python3
"""Installation des poids du moteur vocal local — **jamais** appelé au runtime.

JARVIS ne télécharge rien pendant une conversation : c'est une règle du projet,
pas une préférence. Ce script est le seul chemin d'installation, il est
explicite, il se relance et il reprend là où il s'est arrêté.

    python scripts/download_tts_model.py                # modèle par défaut
    python scripts/download_tts_model.py --check        # vérifie sans rien écrire
    python scripts/download_tts_model.py --dest ~/models/fish

Le téléchargement est volumineux (environ 6,7 Go en 8 bits). Sur une liaison
lente, laissez-le tourner : chaque relance reprend les octets déjà obtenus.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

REPO_8BIT = "mlx-community/fish-audio-s2-pro-8bit"
REPO_BF16 = "mlx-community/fish-audio-s2-pro-bf16"

# Le dépôt ne redistribue aucun poids : la licence Fish Audio (recherche et
# usage non commercial) s'applique au modèle téléchargé.
LICENSE_URL = "https://huggingface.co/fishaudio/s2-pro/blob/main/LICENSE"


def _repo_for(precision: str) -> str:
    return REPO_BF16 if precision == "bf16" else REPO_8BIT


def check_installed(model: str) -> Path | None:
    """Retourne le répertoire local des poids, sans rien télécharger."""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from native_audio.fish_local import FishModelMissing, resolve_local_model_dir

    try:
        return resolve_local_model_dir(model)
    except FishModelMissing:
        return None


def download(model: str, dest: Path | None) -> Path:
    """Télécharge les poids. Reprend un transfert interrompu."""
    from huggingface_hub import snapshot_download

    kwargs: dict[str, object] = {"max_workers": 2}
    if dest is not None:
        dest.mkdir(parents=True, exist_ok=True)
        kwargs["local_dir"] = str(dest)

    started = time.time()
    for attempt in range(1, 1000):
        try:
            path = snapshot_download(model, **kwargs)  # type: ignore[arg-type]
        except KeyboardInterrupt:
            raise
        except Exception as exc:  # noqa: BLE001 — coupure distante : on reprend
            print(
                f"  tentative {attempt} interrompue ({type(exc).__name__}) — reprise…",
                flush=True,
            )
            time.sleep(3)
            continue
        print(f"\nTerminé en {(time.time() - started) / 60:.1f} min : {path}")
        return Path(path)
    raise SystemExit("échec : trop de tentatives")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--precision", choices=("8bit", "bf16"), default="8bit",
        help="8bit (~6,7 Go, recommandé sur 16-32 Go de RAM) ou bf16 (~11 Go)",
    )
    parser.add_argument("--model", default="", help="Dépôt ou chemin explicite")
    parser.add_argument("--dest", default="", help="Répertoire cible (défaut : cache HF)")
    parser.add_argument(
        "--check", action="store_true", help="Vérifie la présence locale et sort",
    )
    args = parser.parse_args(argv)

    model = args.model.strip() or _repo_for(args.precision)

    installed = check_installed(model)
    if args.check:
        if installed is None:
            print(f"absent : {model}")
            return 1
        print(f"présent : {installed}")
        return 0

    if installed is not None:
        print(f"Déjà installé : {installed}")
        return 0

    print(f"Téléchargement de {model}")
    print(f"Licence du modèle (recherche / usage non commercial) : {LICENSE_URL}")
    print("Aucune clé d'API n'est utilisée. Ctrl-C interrompt ; la reprise est possible.\n")

    os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "0")
    dest = Path(args.dest).expanduser() if args.dest else None
    path = download(model, dest)

    print("\nÀ reporter dans .env si vous avez utilisé --dest :")
    print(f"  TTS_MODEL_PATH={path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
