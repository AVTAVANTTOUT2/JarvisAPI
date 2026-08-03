"""Qwen3-TTS 12 Hz local (MLX) — logique du sidecar de synthèse.

Exécuté par ``native_audio/qwen3_synthesize`` sous ``JARVIS_VENV`` (le venv qui
porte ``mlx-audio``), jamais dans l'interpréteur de JARVIS : les deux
environnements n'ont ni la même version de Python ni les mêmes dépendances.

Ce moteur remplace Fish Audio S2 Pro pour une raison mesurée, pas esthétique.
Fish produit **21,53 trames par seconde** d'audio, et chaque trame coûte une
passe complète d'un backbone de 4 milliards de paramètres (4,26 Go lus) plus
dix passes de son décodeur de profondeur (4,51 Go). Sur ce Mac mini M4, dont la
bande passante mémoire soutenue mesurée est d'environ 70 Go/s, la seule passe
du backbone prend 54,7 ms : même avec un décodeur audio gratuit, le moteur
plafonne à 18,3 trames/s là où il en faut 21,53. Le temps réel y est
inatteignable, quelle que soit la qualité de l'implémentation.

Qwen3-TTS déplace les deux termes du rapport : **12,5 trames par seconde** au
lieu de 21,53, et un talker de 0,6 milliard de paramètres au lieu de 4. Le
budget par trame passe de 46 ms à 80 ms pendant que le coût s'effondre.

Deux différences de nature avec le sidecar Fish méritent d'être explicites :

- **La diffusion est native.** ``generate(stream=True)`` rend l'audio au fil de
  la génération, par blocs de ``streaming_interval × 12,5`` trames, décodés
  incrémentalement par ``speech_tokenizer.streaming_decode``. Fish levait
  ``NotImplementedError`` sur ce mode et obligeait JARVIS à découper le texte
  lui-même. Le découpage côté JARVIS reste utile, mais il n'est plus la seule
  source de fragments.
- **La voix est un vecteur, pas un préfixe.** Le modèle Base encode
  l'échantillon de référence par son encodeur de locuteur, au lieu de préfixer
  toute la référence dans le contexte à chaque énoncé.

**Aucun téléchargement.** ``resolve_model_dir`` n'accepte qu'un chemin existant
ou un dépôt déjà présent dans le cache Hugging Face local ; l'appel part avec
``HF_HUB_OFFLINE=1``. Un modèle absent produit une erreur explicite, jamais un
téléchargement de plusieurs gigaoctets au milieu d'un tour de parole.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from native_audio.sidecar_protocol import (
    TAG_READY,
    ModelMissing,
    audio_to_pcm16,
    claim_binary_stdout,
    encode_frame,
    pcm16_to_wav,
    resolve_local_model_dir,
    serve_loop,
)

DEFAULT_MODEL = "mlx-community/Qwen3-TTS-12Hz-0.6B-Base-6bit"

# Le modèle rend son audio à 24 kHz — c'est aussi le défaut du pipeline JARVIS,
# donc aucun rééchantillonnage n'est nécessaire en sortie (Fish sortait à
# 44,1 kHz et imposait une conversion).
DEFAULT_SAMPLE_RATE = 24000

# Débit de trames du tokenizer de parole. Sert à convertir un intervalle de
# diffusion en nombre de trames, exactement comme le fait mlx-audio.
FRAME_RATE_HZ = 12.5

DEFAULT_MAX_TOKENS = 4096
DEFAULT_TEMPERATURE = 0.9
DEFAULT_TOP_P = 1.0
DEFAULT_TOP_K = 50

# Le chemin ICL (voix de référence) impose déjà un minimum de 1,5 côté
# mlx-audio pour éviter la dégénérescence des codes sur un préfixe long. On
# n'essaie pas de descendre en dessous : ce serait annulé silencieusement.
DEFAULT_REPETITION_PENALTY = 1.05

# Intervalle de diffusion, en secondes d'audio par bloc. 0,4 s = 5 trames :
# assez court pour que le premier son parte vite, assez long pour que le
# décodeur incrémental garde un contexte exploitable.
DEFAULT_STREAMING_INTERVAL = 0.4
DEFAULT_STREAMING_CONTEXT = 25

WARMUP_TEXT = "Bonjour."

INSTALL_HINT = "python scripts/download_tts_model.py"

# Sans ces fichiers le moteur ne peut pas parler. Les poids du tokenizer de
# parole sont listés à part : ce sont eux qui portent l'encodeur de locuteur,
# donc c'est leur absence qui ferait perdre la voix clonée sans autre symptôme
# qu'une voix générique.
REQUIRED_FILES: tuple[str, ...] = ("config.json", "tokenizer_config.json")
WEIGHT_GLOBS: tuple[str, ...] = (
    "model*.safetensors",
    "speech_tokenizer/model*.safetensors",
)


class Qwen3ModelMissing(ModelMissing):
    """Poids absents ou incomplets — action humaine requise, pas un repli."""


def resolve_model_dir(spec: str) -> Path:
    """Chemin local des poids, sans jamais déclencher de téléchargement."""
    try:
        return resolve_local_model_dir(
            spec,
            default_model=DEFAULT_MODEL,
            install_hint=INSTALL_HINT,
            required_files=REQUIRED_FILES,
            weight_globs=WEIGHT_GLOBS,
        )
    except ModelMissing as exc:
        raise Qwen3ModelMissing(str(exc)) from exc


class Qwen3TTSServer:
    """Modèle Qwen3-TTS chargé une fois, puis une synthèse par requête.

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
        streaming_interval: float = DEFAULT_STREAMING_INTERVAL,
    ) -> None:
        self._model_dir = model_dir
        self._ref_audio_path = ref_audio
        self._ref_text = ref_text
        self._streaming_interval = max(0.08, float(streaming_interval))
        self._model: Any = None
        self._ref_audio: Any = None
        self._sample_rate = DEFAULT_SAMPLE_RATE

    # ── Chargement ──────────────────────────────────────────────────────────

    def load(self) -> None:
        """Charge le modèle, le tokenizer de parole et la voix, puis compile.

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
            print(f"[qwen3-local] warmup ignoré: {exc}", file=sys.stderr)

    def _load_reference(self) -> None:
        """Charge l'échantillon de voix et le ramène à la fréquence du modèle.

        Le chargement passe **par chemin de fichier**, une seule fois, et
        jamais par les caches d'échantillons du profil. Ce n'est pas une
        commodité : ``load_audio`` de mlx-audio renvoie un ``mx.array`` tel
        quel, sans rien convertir — il ne rééchantillonne que ce qu'il lit
        lui-même depuis un fichier, où il trouve la fréquence dans l'en-tête.
        Lui passer un tableau d'échantillons revient donc à affirmer qu'ils
        sont déjà à la fréquence du modèle.

        Le profil ``jarvis-fr`` est aujourd'hui à 24 kHz, comme ce modèle, si
        bien qu'aucune conversion n'est nécessaire en pratique. Mais rien ne
        garantit qu'un profil régénéré le restera, et une divergence ne se
        signalerait par aucune exception : elle produirait une voix transposée,
        plus grave et plus lente, à la seule écoute. Charger par chemin rend
        cette erreur impossible plutôt que de compter sur une coïncidence.

        Le cache ``.npy`` nu est ignoré pour la même raison : il ne porte
        aucune fréquence, donc l'utiliser reviendrait à deviner.

        Sans référence, le modèle parle avec sa voix par défaut. C'est un
        compromis assumé : refuser de parler tant qu'aucune voix personnalisée
        n'est déposée rendrait JARVIS muet à l'installation.
        """
        if self._ref_audio_path is None:
            return
        wav = self._ref_audio_path
        if not wav.is_file():
            print(
                f"[qwen3-local] voix de référence absente: {wav} — voix par défaut",
                file=sys.stderr,
            )
            return
        try:
            from mlx_audio.utils import load_audio

            self._ref_audio = load_audio(str(wav), sample_rate=self._sample_rate)
            samples = int(getattr(self._ref_audio, "size", 0) or 0)
            print(
                f"[qwen3-local] voix de référence chargée ({samples} échantillons "
                f"@ {self._sample_rate} Hz après conversion)",
                file=sys.stderr,
            )
        except Exception as exc:  # noqa: BLE001 — voix illisible ≠ panne du moteur
            print(f"[qwen3-local] voix de référence ignorée: {exc}", file=sys.stderr)
            self._ref_audio = None

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    @property
    def voice_cloned(self) -> bool:
        """Vrai seulement si la référence **et** son transcript sont présents.

        Le chemin ICL de mlx-audio exige les deux ; un audio sans transcript
        retombe silencieusement sur la voix générique du modèle.
        """
        return self._ref_audio is not None and bool(self._ref_text)

    # ── Synthèse ────────────────────────────────────────────────────────────

    def _generate(self, text: str, **overrides: Any) -> Any:
        if self._model is None:
            raise RuntimeError("modèle non chargé")
        cloned = self.voice_cloned
        return self._model.generate(
            text=text,
            ref_audio=self._ref_audio if cloned else None,
            ref_text=self._ref_text if cloned else None,
            max_tokens=int(overrides.get("max_tokens", DEFAULT_MAX_TOKENS)),
            temperature=float(overrides.get("temperature", DEFAULT_TEMPERATURE)),
            top_p=float(overrides.get("top_p", DEFAULT_TOP_P)),
            top_k=int(overrides.get("top_k", DEFAULT_TOP_K)),
            repetition_penalty=float(
                overrides.get("repetition_penalty", DEFAULT_REPETITION_PENALTY)
            ),
            stream=True,
            streaming_interval=float(
                overrides.get("streaming_interval", self._streaming_interval)
            ),
            streaming_context_size=int(
                overrides.get("streaming_context_size", DEFAULT_STREAMING_CONTEXT)
            ),
            verbose=False,
        )

    def synthesize_chunks(self, request: dict[str, Any]) -> Any:
        """Génère le PCM au fil de la production (générateur de bytes)."""
        text = str(request.get("text") or "").strip()
        if not text:
            return
        for result in self._generate(
            text,
            max_tokens=request.get("max_tokens", DEFAULT_MAX_TOKENS),
            temperature=request.get("temperature", DEFAULT_TEMPERATURE),
            top_p=request.get("top_p", DEFAULT_TOP_P),
            top_k=request.get("top_k", DEFAULT_TOP_K),
            repetition_penalty=request.get(
                "repetition_penalty", DEFAULT_REPETITION_PENALTY
            ),
            streaming_interval=request.get(
                "streaming_interval", self._streaming_interval
            ),
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
            "voice_cloned": self.voice_cloned,
            "streaming": "native",
            "frame_rate_hz": FRAME_RATE_HZ,
        }).encode("utf-8")
        out.write(encode_frame(TAG_READY, ready))
        out.flush()
        return serve_loop(out, self.synthesize_chunks, label="qwen3-local")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="JARVIS Qwen3-TTS local (MLX)")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--voice-dir", default=None)
    parser.add_argument("--text", default=None)
    parser.add_argument("--format", choices=("pcm", "wav"), default="pcm")
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--probe", action="store_true")
    parser.add_argument(
        "--streaming-interval", type=float, default=DEFAULT_STREAMING_INTERVAL
    )
    return parser


def _voice_assets(voice_dir: str | None) -> tuple[Path | None, str | None]:
    """Échantillon et transcript du profil vocal, s'ils existent."""
    if not voice_dir:
        return None, None
    base = Path(voice_dir).expanduser()
    wav = base / "reference.wav"
    txt = base / "transcript.txt"
    text = None
    if txt.is_file():
        try:
            text = txt.read_text(encoding="utf-8").strip() or None
        except OSError:
            text = None
    return (wav if wav.is_file() else None), text


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        model_dir = resolve_model_dir(args.model)
    except Qwen3ModelMissing as exc:
        print(f"[qwen3-local] {exc}", file=sys.stderr)
        return 2

    if args.probe:
        print(json.dumps({"model_dir": str(model_dir), "ok": True}))
        return 0

    ref_audio, ref_text = _voice_assets(args.voice_dir)
    server = Qwen3TTSServer(
        model_dir,
        ref_audio=ref_audio,
        ref_text=ref_text,
        streaming_interval=args.streaming_interval,
    )

    if args.serve:
        return server.serve()

    if not args.text:
        print("[qwen3-local] --text requis hors mode --serve", file=sys.stderr)
        return 2

    out = claim_binary_stdout()
    server.load()
    pcm = b"".join(server.synthesize_chunks({"text": args.text}))
    out.write(pcm16_to_wav(pcm, server.sample_rate) if args.format == "wav" else pcm)
    out.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
