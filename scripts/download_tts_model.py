#!/usr/bin/env python3
"""Installation des poids du moteur vocal local — **jamais** appelé au runtime.

JARVIS ne télécharge rien pendant une conversation : c'est une règle du projet,
pas une préférence. Ce script est le seul chemin d'installation, il est
explicite, il se relance et il reprend là où il s'est arrêté.

    python scripts/download_tts_model.py                # moteur par défaut
    python scripts/download_tts_model.py --check        # vérifie sans rien écrire
    python scripts/download_tts_model.py --dest ~/models/voix

Sur une liaison lente, laissez-le tourner : chaque relance reprend les octets
déjà obtenus.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Engine:
    """Un moteur installable : ses dépôts, sa licence, sa vérification.

    La licence est portée ici parce que le dépôt ne redistribue **aucun** poids :
    ce que l'utilisateur télécharge est régi par la licence de son auteur, et
    l'afficher avant le transfert est la seule façon honnête de le dire.
    """

    name: str
    repos: dict[str, str]
    default_precision: str
    license_label: str
    license_url: str
    size_hint: str
    resolver: str


ENGINES: dict[str, Engine] = {
    # Moteur de production. 12,5 trames par seconde d'audio pour un talker de
    # 0,6 milliard de paramètres : c'est ce rapport qui rend le temps réel
    # atteignable sur un Mac mini M4.
    "qwen3": Engine(
        name="qwen3",
        repos={"6bit": "mlx-community/Qwen3-TTS-12Hz-0.6B-Base-6bit"},
        default_precision="6bit",
        license_label="Apache 2.0",
        license_url="https://huggingface.co/Qwen/Qwen3-TTS-12Hz-0.6B-Base",
        size_hint="environ 1,9 Go",
        resolver="native_audio.qwen3_local",
    ),
}

DEFAULT_ENGINE = "qwen3"


def _repo_for(engine: Engine, precision: str) -> str:
    return engine.repos.get(precision) or engine.repos[engine.default_precision]


def check_installed(engine: Engine, model: str) -> Path | None:
    """Retourne le répertoire local des poids, sans rien télécharger.

    La vérification appartient au moteur : le tokenizer de parole vit dans un
    sous-répertoire et porte l'encodeur de locuteur, donc un répertoire qui
    semble complet à première vue peut avoir perdu la voix clonée.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    module = __import__(engine.resolver, fromlist=["*"])

    try:
        return module.resolve_model_dir(model)
    except module.Qwen3ModelMissing:
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
        "--precision", default="",
        help=f"précision des poids. Défaut : {ENGINES[DEFAULT_ENGINE].default_precision}.",
    )
    parser.add_argument("--model", default="", help="Dépôt ou chemin explicite")
    parser.add_argument("--dest", default="", help="Répertoire cible (défaut : cache HF)")
    parser.add_argument(
        "--check", action="store_true", help="Vérifie la présence locale et sort",
    )
    args = parser.parse_args(argv)

    engine = ENGINES[DEFAULT_ENGINE]
    precision = args.precision.strip() or engine.default_precision
    if not args.model.strip() and precision not in engine.repos:
        print(
            f"précision « {precision} » inconnue pour {engine.name} — "
            f"disponibles : {', '.join(sorted(engine.repos))}",
            file=sys.stderr,
        )
        return 2
    model = args.model.strip() or _repo_for(engine, precision)

    installed = check_installed(engine, model)
    if args.check:
        if installed is None:
            print(f"absent : {model}")
            return 1
        print(f"présent : {installed}")
        return 0

    if installed is not None:
        print(f"Déjà installé : {installed}")
        return 0

    print(f"Téléchargement de {model} ({engine.size_hint})")
    print(f"Licence du modèle ({engine.license_label}) : {engine.license_url}")
    print("Aucune clé d'API n'est utilisée. Ctrl-C interrompt ; la reprise est possible.\n")

    os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "0")
    dest = Path(args.dest).expanduser() if args.dest else None
    path = download(model, dest)

    print("\nÀ reporter dans .env si vous avez utilisé --dest :")
    print(f"  TTS_MODEL_PATH={path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
