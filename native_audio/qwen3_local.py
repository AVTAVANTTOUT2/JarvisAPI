"""Qwen3-TTS 12 Hz local (MLX) — logique du sidecar de synthèse.

Exécuté par ``native_audio/qwen3_synthesize`` sous ``JARVIS_VENV`` (le venv qui
porte ``mlx-audio``), jamais dans l'interpréteur de JARVIS : les deux
environnements n'ont ni la même version de Python ni les mêmes dépendances.

Le choix de ce modèle repose sur un rapport mesuré, pas sur une préférence. Le
temps réel exige de produire une seconde d'audio en moins d'une seconde de
calcul ; ce qui décide, c'est le nombre de trames à générer par seconde d'audio
multiplié par le coût d'une trame. Ici : **12,5 trames par seconde** pour un
talker de 0,6 milliard de paramètres, soit environ 3,4 Go de poids lus par
trame — quand ce Mac mini M4 soutient environ 70 Go/s. Le budget par trame est
de 80 ms ; la dépense réelle tient largement dessous.

Deux propriétés du sidecar méritent d'être explicites :

- **La diffusion est native.** ``generate(stream=True)`` rend l'audio au fil de
  la génération, par blocs de ``streaming_interval × 12,5`` trames, décodés
  incrémentalement par ``speech_tokenizer.streaming_decode``. Le découpage du
  texte côté JARVIS reste utile — il borne le premier énoncé et donne une
  frontière d'annulation propre — mais il n'est plus la seule source de
  fragments.
- **La voix est un vecteur, pas un préfixe.** Le modèle Base encode
  l'échantillon de référence par son encodeur de locuteur ; lorsqu'un
  transcript accompagne l'échantillon, mlx-audio emprunte en plus la voie
  *in-context learning*.

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

# Le modèle rend son audio à 24 kHz — c'est aussi le défaut du pipeline JARVIS
# et la fréquence du profil vocal, donc aucun rééchantillonnage nulle part.
DEFAULT_SAMPLE_RATE = 24000

# Débit de trames du tokenizer de parole. Sert à convertir un intervalle de
# diffusion en nombre de trames, exactement comme le fait mlx-audio.
FRAME_RATE_HZ = 12.5

DEFAULT_MAX_TOKENS = 4096

# Ces trois valeurs décident de la **stabilité du locuteur**, pas seulement de
# l'expressivité. Mesuré sur ce Mac mini M4, dérive de F0 entre le premier et le
# dernier tiers d'une réponse de trois phrases : 0.9/1.0/50 donne -20,5 Hz,
# 0.5/0.9/30 donne -4,2 Hz. Sur une voix dont la médiane est à 151 Hz, la
# première valeur produit des fins de phrase en registre féminin.
DEFAULT_TEMPERATURE = 0.5
DEFAULT_TOP_P = 0.9
DEFAULT_TOP_K = 30

# Queue de décodeur. En diffusion, mlx-audio appelle `streaming_step()` sans le
# « trim to valid length » que font ses deux chemins non streamés : les jetons
# excédentaires générés après la fin de l'énoncé sont décodés tels quels et
# s'entendent comme un souffle bref. On coupe donc la traîne du **dernier**
# fragment, sous un seuil relatif au pic de l'énoncé.
TAIL_SILENCE_RATIO = 0.05      # 5 % du pic : franchement sous le niveau de parole
TAIL_RELEASE_MS = 60           # extinction naturelle conservée après le dernier son
TAIL_MAX_TRIM_MS = 500         # borne de sécurité : jamais plus que ça

# Le chemin ICL (voix de référence) impose déjà un minimum de 1,5 côté
# mlx-audio pour éviter la dégénérescence des codes sur un préfixe long. On
# n'essaie pas de descendre en dessous : ce serait annulé silencieusement.
DEFAULT_REPETITION_PENALTY = 1.05

# Intervalle de diffusion, en secondes d'audio par bloc. 0,4 s = 5 trames :
# assez court pour que le premier son parte vite, assez long pour que le
# décodeur incrémental garde un contexte exploitable.
DEFAULT_STREAMING_INTERVAL = 0.4
DEFAULT_STREAMING_CONTEXT = 25

# Langue transmise au modèle. ``auto`` n'est pas neutre : mlx-audio ne résout
# alors **aucun** identifiant de langue et le conditionnement disparaît. JARVIS
# parle français, donc on le dit. Les dix langues connues du modèle sont dans
# ``talker_config.codec_language_id`` ; un code absent de cette table serait
# ignoré silencieusement, d'où la validation au chargement.
DEFAULT_LANGUAGE = "french"

# Comment la référence est reproduite. ``icl`` fournit la référence **et** son
# transcript, ce qui fait emprunter à mlx-audio la voie « in-context learning »
# en plus du vecteur de locuteur ; ``speaker_embedding`` ne fournit que
# l'audio, donc seul le vecteur est calculé. Mesuré sur ce Mac mini M4 :
# environ 520 ms contre 215 ms avant le premier son. Les deux tiennent le
# temps réel ; le choix se tranche à l'oreille.
CLONE_MODE_ICL = "icl"
CLONE_MODE_EMBEDDING = "speaker_embedding"
DEFAULT_CLONE_MODE = CLONE_MODE_ICL

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
        language: str = DEFAULT_LANGUAGE,
        clone_mode: str = DEFAULT_CLONE_MODE,
        temperature: float = DEFAULT_TEMPERATURE,
        top_p: float = DEFAULT_TOP_P,
        top_k: int = DEFAULT_TOP_K,
    ) -> None:
        self._model_dir = model_dir
        self._ref_audio_path = ref_audio
        self._ref_text = ref_text
        self._streaming_interval = max(0.08, float(streaming_interval))
        self._language = (language or DEFAULT_LANGUAGE).strip().lower()
        self._clone_mode = (clone_mode or DEFAULT_CLONE_MODE).strip().lower()
        self._temperature = float(temperature)
        self._top_p = float(top_p)
        self._top_k = int(top_k)
        self._model: Any = None
        self._ref_audio: Any = None
        self._sample_rate = DEFAULT_SAMPLE_RATE
        self._reference_ms = 0.0

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
        self._check_language()
        self._load_reference()
        try:
            for _ in self._generate(WARMUP_TEXT):
                break
        except Exception as exc:  # noqa: BLE001 — le warmup ne tue pas le serveur
            print(f"[qwen3-local] warmup ignoré: {exc}", file=sys.stderr)
        self._report_voice_ready()

    def _check_language(self) -> None:
        """Refuse en clair une langue que le modèle ignorerait en silence.

        mlx-audio ne résout un identifiant de langue que si le code figure dans
        ``codec_language_id`` ; sinon il n'émet aucun avertissement et le
        conditionnement disparaît. Une faute de frappe coûterait la prosodie
        sans le moindre symptôme.
        """
        known = self._known_languages()
        if not known or self._language in known:
            return
        print(
            f"[qwen3-local] langue « {self._language} » inconnue du modèle "
            f"(connues : {', '.join(sorted(known))}) — repli sur "
            f"{DEFAULT_LANGUAGE}",
            file=sys.stderr,
        )
        self._language = DEFAULT_LANGUAGE if DEFAULT_LANGUAGE in known else "auto"

    def _known_languages(self) -> set[str]:
        talker = getattr(getattr(self._model, "config", None), "talker_config", None)
        table = getattr(talker, "codec_language_id", None) or {}
        return {str(k).lower() for k in table}

    def _report_voice_ready(self) -> None:
        """État vocal effectif, en clair, une fois le moteur réellement prêt.

        ``voice_cloned: true`` ne disait pas *comment* la voix est reproduite.
        Ces lignes nomment le chemin réellement emprunté, la durée de la
        référence et la langue transmise — les trois choses dont dépend le
        timbre obtenu, et qu'aucun booléen ne peut porter.
        """
        for line in (
            "Qwen3 voice ready",
            f"voice={self._voice_id()}",
            f"clone_mode={self.clone_mode}",
            f"reference_duration_ms={int(self._reference_ms)}",
            f"reference_text_used={'true' if self._use_transcript else 'false'}",
            f"language={self._language}",
            "streaming=native",
            f"streaming_interval_s={self._streaming_interval}",
            f"frame_rate_hz={FRAME_RATE_HZ}",
            f"sample_rate={self._sample_rate}",
        ):
            print(f"[qwen3-local] {line}", file=sys.stderr)

    def _voice_id(self) -> str:
        return self._ref_audio_path.parent.name if self._ref_audio_path else "default"

    @property
    def clone_mode(self) -> str:
        """Chemin de clonage réellement emprunté par mlx-audio.

        Trois valeurs possibles, et la nuance compte : ``icl+speaker_embedding``
        est le cas nominal ici. mlx-audio prend la voie *in-context learning*
        dès que la référence **et** son transcript sont fournis et que le
        tokenizer de parole porte un encodeur ; cette voie appelle en plus
        ``extract_speaker_embedding`` sur la référence. Les deux mécanismes
        opèrent donc ensemble — l'annoncer comme un choix binaire entre
        « speaker_embedding » et « icl » serait faux.
        """
        if self._ref_audio is None:
            return "none"
        if self._clone_mode == CLONE_MODE_EMBEDDING or not self._ref_text:
            return CLONE_MODE_EMBEDDING
        tokenizer = getattr(self._model, "speech_tokenizer", None)
        if getattr(tokenizer, "has_encoder", False):
            return "icl+speaker_embedding"
        return CLONE_MODE_EMBEDDING

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
            self._reference_ms = samples * 1000.0 / max(1, self._sample_rate)
            print(
                f"[qwen3-local] voix de référence chargée ({samples} échantillons "
                f"@ {self._sample_rate} Hz, {self._reference_ms / 1000:.2f} s)",
                file=sys.stderr,
            )
        except Exception as exc:  # noqa: BLE001 — voix illisible ≠ panne du moteur
            print(f"[qwen3-local] voix de référence ignorée: {exc}", file=sys.stderr)
            self._ref_audio = None

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    @property
    def _use_transcript(self) -> bool:
        """Le transcript n'est transmis qu'en mode ICL.

        C'est lui, et lui seul, qui fait basculer mlx-audio sur la voie
        in-context : le retirer suffit à obtenir le mode par vecteur de
        locuteur, sans toucher au profil vocal sur le disque.
        """
        return self._clone_mode == CLONE_MODE_ICL and bool(self._ref_text)

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
        return self._model.generate(
            text=text,
            lang_code=self._language,
            ref_audio=self._ref_audio,
            ref_text=self._ref_text if self._use_transcript else None,
            max_tokens=int(overrides.get("max_tokens", DEFAULT_MAX_TOKENS)),
            temperature=float(overrides.get("temperature", self._temperature)),
            top_p=float(overrides.get("top_p", self._top_p)),
            top_k=int(overrides.get("top_k", self._top_k)),
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
            temperature=request.get("temperature", self._temperature),
            top_p=request.get("top_p", self._top_p),
            top_k=request.get("top_k", self._top_k),
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
            "clone_mode": self.clone_mode,
            "reference_duration_ms": int(self._reference_ms),
            "reference_text_used": self._use_transcript,
            "language": self._language,
            "streaming": "native",
            "streaming_interval_s": self._streaming_interval,
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
    parser.add_argument("--temperature", type=float, default=DEFAULT_TEMPERATURE)
    parser.add_argument("--top-p", type=float, default=DEFAULT_TOP_P)
    parser.add_argument("--top-k", type=int, default=DEFAULT_TOP_K)
    parser.add_argument(
        "--clone-mode", default=DEFAULT_CLONE_MODE,
        choices=(CLONE_MODE_ICL, CLONE_MODE_EMBEDDING),
        help="icl (référence + transcript) ou speaker_embedding (référence seule)",
    )
    parser.add_argument(
        "--language", default=DEFAULT_LANGUAGE,
        help="langue transmise au modèle (défaut : french ; « auto » la retire)",
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
        language=args.language,
        clone_mode=args.clone_mode,
        temperature=args.temperature,
        top_p=args.top_p,
        top_k=args.top_k,
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
