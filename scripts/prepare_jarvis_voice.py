#!/usr/bin/env python3
"""Prépare automatiquement le profil vocal français ``jarvis-fr``.

À partir d'un master WAV (typiquement
``data/private/voice-sources/jarvis-fr/master.wav``), ce script :

1. analyse l'énergie et la stabilité de la parole ;
2. sélectionne le meilleur extrait continu (10–30 s) — sans choix manuel ;
3. transcrit l'extrait en local (faster-whisper, lecture seule) ;
4. écrit ``voices/jarvis-fr/{metadata.json,reference.wav,transcript.txt}`` ;
5. met en cache un tenseur float32 ``data/voices/jarvis-fr/reference.npy``
   pour un chargement rapide au warmup du sidecar Fish.

Aucun appel réseau. Aucune modification du moteur STT : on importe seulement
le modèle déjà installé pour produire le transcript de référence.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import shutil
import wave
from array import array
from dataclasses import asdict, dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_SOURCE_CANDIDATES: tuple[Path, ...] = (
    REPO_ROOT / "data/private/voice-sources/jarvis-fr/master.wav",
    REPO_ROOT / "data/private/voice-sources/VoixJARVIS_source_clonage_24k.wav",
    Path.home() / "Downloads/VoixJARVIS_source_clonage_24k.wav",
    Path.home() / "Desktop/VoixJARVIS_source_clonage_24k.wav",
    REPO_ROOT / "VoixJARVIS_source_clonage_24k.wav",
    Path("/tmp/VoixJARVIS_source_clonage_24k.wav"),
)

FALLBACK_NAMES: tuple[str, ...] = (
    "VoixJARVIS_source_clonage_24k.wav",
    "VoixJARVIS_amelioree_naturelle.wav",
    "VoixJARVIS_complet.mp3",
)

TARGET_VOICE_ID = "jarvis-fr"
TARGET_SAMPLE_RATE = 24000
MIN_CLIP_S = 10.0
TARGET_CLIP_S = 20.0
MAX_CLIP_S = 28.0
WINDOW_S = 0.25
ANALYSIS_DIR_NAME = "analysis"


@dataclass(frozen=True)
class ClipCandidate:
    """Fenêtre candidate pour le clonage vocal."""

    start_s: float
    end_s: float
    score: float
    speech_ratio: float
    rms: float
    peak: float
    clip_ratio: float
    zcr: float

    @property
    def duration_s(self) -> float:
        return self.end_s - self.start_s


@dataclass(frozen=True)
class PreparationReport:
    """Compte-rendu reproductible de la préparation."""

    source: str
    master: str
    voice_dir: str
    reference_wav: str
    transcript_path: str
    npy_cache: str
    selected: dict[str, float | str]
    transcript_preview: str
    candidates_kept: int


def find_source(explicit: Path | None = None) -> Path:
    """Localise le master selon l'ordre de priorité du projet."""
    if explicit is not None:
        path = explicit.expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"source introuvable : {path}")
        return path

    for candidate in DEFAULT_SOURCE_CANDIDATES:
        if candidate.is_file():
            return candidate.resolve()

    search_roots = (
        REPO_ROOT,
        Path.home() / "Downloads",
        Path.home() / "Desktop",
        Path("/tmp"),
    )
    for root in search_roots:
        if not root.exists():
            continue
        for name in FALLBACK_NAMES:
            # Préférer le WAV 24 kHz au MP3 de repli.
            if name.endswith(".mp3"):
                continue
            hit = root / name
            if hit.is_file():
                return hit.resolve()
            for nested in root.rglob(name):
                if nested.is_file() and not nested.name.endswith(".mp3"):
                    return nested.resolve()

    # Dernier recours : le MP3 seulement si aucun WAV n'existe.
    for root in search_roots:
        if not root.exists():
            continue
        for nested in root.rglob("VoixJARVIS_complet.mp3"):
            if nested.is_file():
                return nested.resolve()

    raise FileNotFoundError(
        "aucun fichier VoixJARVIS_* trouvé — placez "
        "VoixJARVIS_source_clonage_24k.wav dans Downloads ou "
        "data/private/voice-sources/"
    )


def ensure_master(source: Path, master_path: Path) -> Path:
    """Copie la source vers l'emplacement privé canonique."""
    master_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if master_path.resolve() != source.resolve():
        shutil.copy2(source, master_path)
    master_path.chmod(0o600)
    return master_path


def _read_mono_pcm16(path: Path) -> tuple[array, int]:
    """Lit un WAV mono PCM16 (convertit stéréo → mono si besoin)."""
    with wave.open(str(path), "rb") as handle:
        channels = handle.getnchannels()
        sample_width = handle.getsampwidth()
        sample_rate = handle.getframerate()
        frames = handle.getnframes()
        raw = handle.readframes(frames)

    if sample_width != 2:
        raise ValueError(
            f"{path} : largeur d'échantillon {sample_width} non supportée "
            "(PCM16 requis)"
        )

    samples = array("h")
    samples.frombytes(raw)
    if channels == 1:
        return samples, sample_rate
    if channels < 1:
        raise ValueError(f"{path} : aucun canal audio")

    mono = array("h")
    for index in range(0, len(samples), channels):
        chunk = samples[index : index + channels]
        mono.append(int(sum(chunk) / len(chunk)))
    return mono, sample_rate


def _window_metrics(
    samples: array,
    sample_rate: int,
    *,
    window_s: float = WINDOW_S,
) -> list[dict[str, float]]:
    """Calcule RMS / pic / ZCR par fenêtre glissante non chevauchante."""
    window = max(1, int(sample_rate * window_s))
    metrics: list[dict[str, float]] = []
    for start in range(0, len(samples) - window + 1, window):
        chunk = samples[start : start + window]
        energy = 0.0
        peak = 0
        crossings = 0
        previous = 0
        for value in chunk:
            energy += float(value) * float(value)
            abs_value = abs(value)
            if abs_value > peak:
                peak = abs_value
            if previous == 0:
                previous = value
                continue
            if (value >= 0) != (previous >= 0):
                crossings += 1
            previous = value
        rms = math.sqrt(energy / len(chunk))
        zcr = crossings / len(chunk)
        clip_hits = sum(1 for value in chunk if abs(value) >= 32000)
        metrics.append(
            {
                "start_s": start / sample_rate,
                "end_s": (start + window) / sample_rate,
                "rms": rms,
                "peak": float(peak),
                "zcr": zcr,
                "clip_ratio": clip_hits / len(chunk),
            }
        )
    if not metrics:
        raise ValueError("fichier trop court pour l'analyse par fenêtres")
    return metrics


def _speech_threshold(metrics: list[dict[str, float]]) -> float:
    rms_values = sorted(row["rms"] for row in metrics)
    median = rms_values[len(rms_values) // 2]
    # Seuil bas : on veut des fenêtres clairement parlées, pas du souffle.
    return max(500.0, median * 0.40)


def rank_candidates(
    metrics: list[dict[str, float]],
    *,
    min_s: float = MIN_CLIP_S,
    target_s: float = TARGET_CLIP_S,
    max_s: float = MAX_CLIP_S,
) -> list[ClipCandidate]:
    """Classe les fenêtres continues de parole ; le meilleur score gagne."""
    threshold = _speech_threshold(metrics)
    speech_flags = [row["rms"] >= threshold for row in metrics]
    window_s = metrics[0]["end_s"] - metrics[0]["start_s"]
    target_windows = max(1, int(round(target_s / window_s)))
    min_windows = max(1, int(round(min_s / window_s)))
    max_windows = max(min_windows, int(round(max_s / window_s)))

    candidates: list[ClipCandidate] = []
    total = len(metrics)
    # Pas dans les 3 % du début / de la fin (génériques, silence, jingles).
    margin = max(1, int(total * 0.03))

    for length in range(min_windows, max_windows + 1):
        for start_idx in range(margin, total - length - margin + 1):
            end_idx = start_idx + length
            window = metrics[start_idx:end_idx]
            flags = speech_flags[start_idx:end_idx]
            speech_ratio = sum(1 for flag in flags if flag) / length
            if speech_ratio < 0.85:
                continue

            rms = sum(row["rms"] for row in window) / length
            peak = max(row["peak"] for row in window)
            clip_ratio = sum(row["clip_ratio"] for row in window) / length
            zcr = sum(row["zcr"] for row in window) / length

            # ZCR typique de la parole ~0.02–0.15 ; hors de ça = bruit / musique.
            zcr_penalty = 0.0
            if zcr < 0.015 or zcr > 0.22:
                zcr_penalty = 0.35

            duration = length * window_s
            duration_score = 1.0 - min(1.0, abs(duration - target_s) / target_s)
            # Préférer le milieu du fichier (souvent le débit le plus stable).
            center = (start_idx + end_idx) / 2.0
            mid = total / 2.0
            centrality = 1.0 - min(1.0, abs(center - mid) / mid)

            score = (
                0.35 * speech_ratio
                + 0.25 * min(1.0, rms / 6000.0)
                + 0.20 * duration_score
                + 0.10 * centrality
                + 0.10 * (1.0 - min(1.0, clip_ratio * 20.0))
                - zcr_penalty
            )
            # Pénalité si la longueur s'éloigne trop de la cible idéale.
            if abs(length - target_windows) > 8:
                score -= 0.05

            candidates.append(
                ClipCandidate(
                    start_s=window[0]["start_s"],
                    end_s=window[-1]["end_s"],
                    score=score,
                    speech_ratio=speech_ratio,
                    rms=rms,
                    peak=peak,
                    clip_ratio=clip_ratio,
                    zcr=zcr,
                )
            )

    candidates.sort(key=lambda item: item.score, reverse=True)
    # Dédupliquer les fenêtres trop proches (chevauchement > 50 %).
    kept: list[ClipCandidate] = []
    for candidate in candidates:
        if any(
            _overlap_ratio(candidate, previous) > 0.5 for previous in kept
        ):
            continue
        kept.append(candidate)
        if len(kept) >= 8:
            break
    if not kept:
        raise RuntimeError(
            "aucun extrait de parole stable trouvé — vérifiez le master WAV"
        )
    return kept


def _overlap_ratio(left: ClipCandidate, right: ClipCandidate) -> float:
    start = max(left.start_s, right.start_s)
    end = min(left.end_s, right.end_s)
    if end <= start:
        return 0.0
    return (end - start) / min(left.duration_s, right.duration_s)


def extract_clip(
    samples: array,
    sample_rate: int,
    candidate: ClipCandidate,
    destination: Path,
) -> Path:
    """Écrit le WAV de référence mono PCM16 à ``sample_rate`` natif."""
    start = max(0, int(candidate.start_s * sample_rate))
    end = min(len(samples), int(candidate.end_s * sample_rate))
    clip = samples[start:end]
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with wave.open(str(destination), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(clip.tobytes())
    destination.chmod(0o600)
    return destination


def transcribe_local(wav_path: Path, language: str = "fr") -> str:
    """Transcription locale via faster-whisper — sans toucher au code STT."""
    from faster_whisper import WhisperModel

    # Petit modèle : l'extrait fait ~20 s, la précision d'alignement prime.
    model_name = "small"
    model = WhisperModel(model_name, device="cpu", compute_type="int8")
    segments, _info = model.transcribe(
        str(wav_path),
        language=language,
        beam_size=5,
        vad_filter=False,
        condition_on_previous_text=False,
    )
    text = " ".join(segment.text.strip() for segment in segments).strip()
    text = " ".join(text.split())
    if not text:
        raise RuntimeError(f"transcription vide pour {wav_path}")
    # Ponctuation finale attendue par le cloneur.
    if text[-1] not in ".!?…":
        text += "."
    return text


def write_npy_cache(wav_path: Path, cache_path: Path) -> Path:
    """Pré-encode la référence en float32 pour un warmup Fish immédiat."""
    import numpy as np

    samples, rate = _read_mono_pcm16(wav_path)
    arr = np.asarray(samples, dtype=np.float32) / 32768.0
    cache_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # ``.npz`` porte les métadonnées ; on expose aussi ``.npy`` (samples seuls)
    # pour les outils qui n'attendent qu'un tenseur.
    np.savez_compressed(
        cache_path.with_suffix(".npz"),
        samples=arr,
        sample_rate=np.asarray(int(rate), dtype=np.int32),
    )
    np.save(cache_path, arr)
    cache_path.chmod(0o600)
    cache_path.with_suffix(".npz").chmod(0o600)
    return cache_path


def write_metadata(voice_dir: Path) -> Path:
    metadata = {
        "id": TARGET_VOICE_ID,
        "language": "fr-FR",
        "gender": "male",
        "source": "local_reference",
        "license": "user-provided",
        "reference_audio": "reference.wav",
        "reference_text": "transcript.txt",
        "reference_cache": "reference.npy",
        "consent": (
            "Voix fournie par le propriétaire de JARVIS pour clonage local "
            "exclusif. Aucun fichier audio n'est versionné dans ce dépôt."
        ),
    }
    path = voice_dir / "metadata.json"
    path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def prepare_voice(
    *,
    source: Path | None = None,
    voice_dir: Path | None = None,
    master_path: Path | None = None,
    skip_transcribe: bool = False,
    transcript_override: str | None = None,
) -> PreparationReport:
    """Pipeline complet : source → profil ``jarvis-fr`` prêt pour Fish."""
    resolved_source = find_source(source)
    master = ensure_master(
        resolved_source,
        master_path
        or (REPO_ROOT / "data/private/voice-sources/jarvis-fr/master.wav"),
    )

    voice_dir = voice_dir or (REPO_ROOT / "voices" / TARGET_VOICE_ID)
    voice_dir.mkdir(parents=True, exist_ok=True)
    analysis_dir = (
        REPO_ROOT / "data/private/voice-sources/jarvis-fr" / ANALYSIS_DIR_NAME
    )
    analysis_dir.mkdir(parents=True, exist_ok=True, mode=0o700)

    samples, sample_rate = _read_mono_pcm16(master)
    if sample_rate != TARGET_SAMPLE_RATE:
        logger.warning(
            "master à %d Hz (attendu %d) — conservation du débit natif",
            sample_rate,
            TARGET_SAMPLE_RATE,
        )

    metrics = _window_metrics(samples, sample_rate)
    candidates = rank_candidates(metrics)
    best = candidates[0]

    # Conserver les 3 meilleurs extraits pour inspection locale.
    for index, candidate in enumerate(candidates[:3], start=1):
        extract_clip(
            samples,
            sample_rate,
            candidate,
            analysis_dir / f"candidate_{index:02d}.wav",
        )

    reference_wav = extract_clip(
        samples, sample_rate, best, voice_dir / "reference.wav"
    )
    # Copie privée hors voices/ (gitignore double).
    private_ref = (
        REPO_ROOT / "data/private/voice-sources/jarvis-fr/reference.wav"
    )
    shutil.copy2(reference_wav, private_ref)
    private_ref.chmod(0o600)

    if transcript_override:
        transcript = transcript_override.strip()
    elif skip_transcribe:
        raise ValueError("skip_transcribe exige --transcript")
    else:
        transcript = transcribe_local(reference_wav)

    transcript_path = voice_dir / "transcript.txt"
    transcript_path.write_text(transcript + "\n", encoding="utf-8")
    transcript_path.chmod(0o600)
    (analysis_dir / "transcript.txt").write_text(transcript + "\n", encoding="utf-8")

    write_metadata(voice_dir)

    npy_voice = write_npy_cache(reference_wav, voice_dir / "reference.npy")
    npy_data = write_npy_cache(
        reference_wav,
        REPO_ROOT / "data/voices" / TARGET_VOICE_ID / "reference.npy",
    )

    report = PreparationReport(
        source=str(resolved_source),
        master=str(master),
        voice_dir=str(voice_dir),
        reference_wav=str(reference_wav),
        transcript_path=str(transcript_path),
        npy_cache=str(npy_data),
        selected={
            "start_s": round(best.start_s, 3),
            "end_s": round(best.end_s, 3),
            "duration_s": round(best.duration_s, 3),
            "score": round(best.score, 4),
            "speech_ratio": round(best.speech_ratio, 4),
            "rms": round(best.rms, 1),
            "sample_rate": sample_rate,
            "npy_voice": str(npy_voice),
        },
        transcript_preview=transcript[:240],
        candidates_kept=len(candidates),
    )

    report_path = analysis_dir / "preparation_report.json"
    report_path.write_text(
        json.dumps(asdict(report), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    # Ancien profil ``voices/jarvis`` : pointer vers jarvis-fr sans le conserver
    # comme seconde voix active (le test d'unicité l'exige).
    legacy = REPO_ROOT / "voices" / "jarvis"
    if legacy.is_dir() and legacy.resolve() != voice_dir.resolve():
        shutil.rmtree(legacy)

    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        default="",
        help="Chemin explicite vers le master WAV (sinon recherche auto)",
    )
    parser.add_argument(
        "--voice-dir",
        default="",
        help="Répertoire de profil (défaut : voices/jarvis-fr)",
    )
    parser.add_argument(
        "--transcript",
        default="",
        help="Transcript fourni (saute la transcription STT)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Écrit le rapport PreparationReport sur stdout",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = build_parser().parse_args(argv)
    report = prepare_voice(
        source=Path(args.source) if args.source else None,
        voice_dir=Path(args.voice_dir) if args.voice_dir else None,
        transcript_override=args.transcript or None,
    )
    if args.json:
        print(json.dumps(asdict(report), ensure_ascii=False, indent=2))
    else:
        print(
            f"Voix {TARGET_VOICE_ID} prête\n"
            f"  source     : {report.source}\n"
            f"  extrait    : {report.selected['start_s']}s → "
            f"{report.selected['end_s']}s "
            f"({report.selected['duration_s']}s, score={report.selected['score']})\n"
            f"  référence  : {report.reference_wav}\n"
            f"  transcript : {report.transcript_preview!r}\n"
            f"  cache npy  : {report.npy_cache}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
