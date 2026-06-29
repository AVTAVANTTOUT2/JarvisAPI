#!/usr/bin/env python3
"""Diagnostic standalone Kokoro TTS — teste le modèle hors de FastAPI.

Usage :
    python scripts/test_kokoro.py
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

BOLD = "\033[1m"
RESET = "\033[0m"
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
DIM = "\033[2m"

BASE_DIR = Path(__file__).resolve().parent.parent
MODEL_PATH = BASE_DIR / "models" / "kokoro" / "kokoro-v0_19.onnx"
VOICES_PATH = BASE_DIR / "models" / "kokoro" / "voices.bin"

TEST_TEXT = "Bonjour monsieur, le test audio de Kokoro est un succès."
TEST_VOICE = "af_nicole"
FALLBACK_VOICE = "af_heart"
OUTPUT_FILE = BASE_DIR / "test_kokoro_output.wav"


def ok(msg: str) -> None:
    print(f"  {GREEN}OK{RESET}  {msg}")


def fail(msg: str, detail: str = "") -> None:
    print(f"  {RED}ECHEC{RESET}  {msg}")
    if detail:
        print(f"         {YELLOW}{detail}{RESET}")


def section(title: str) -> None:
    print(f"\n{BOLD}--- {title} ---{RESET}")


def main() -> int:
    print(f"\n{BOLD}=== JARVIS — Diagnostic Kokoro TTS ==={RESET}")

    # 1) Fichiers modèle
    section("1. Fichiers modele")
    missing = False
    if MODEL_PATH.exists():
        size_mb = MODEL_PATH.stat().st_size / (1024 * 1024)
        ok(f"Modele ONNX : {MODEL_PATH.name} ({size_mb:.0f} MB)")
    else:
        fail(f"Modele ONNX introuvable : {MODEL_PATH}")
        print(f"         {YELLOW}Telecharger :{RESET}")
        print(f"         curl -L -o models/kokoro/kokoro-v0_19.onnx \\")
        print(f"           https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files/kokoro-v0_19.onnx")
        missing = True

    if VOICES_PATH.exists():
        size_mb = VOICES_PATH.stat().st_size / (1024 * 1024)
        ok(f"Voices : {VOICES_PATH.name} ({size_mb:.1f} MB)")
    else:
        fail(f"Voices introuvable : {VOICES_PATH}")
        print(f"         {YELLOW}Telecharger :{RESET}")
        print(f"         curl -L -o models/kokoro/voices.json \\")
        print(f"           https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files/voices.json")
        missing = True

    if missing:
        print(f"\n{RED}Fichiers manquants — impossible de continuer.{RESET}")
        return 1

    # 2) Import kokoro_onnx
    section("2. Import kokoro_onnx")
    try:
        from kokoro_onnx import Kokoro
        ok("kokoro_onnx importable")
    except ImportError as e:
        fail(f"ImportError : {e}")
        print(f"         {YELLOW}pip install kokoro-onnx{RESET}")
        return 1

    # 3) Import soundfile + numpy
    section("3. Dependances audio")
    try:
        import soundfile as sf
        ok(f"soundfile {sf.__version__}")
    except ImportError as e:
        fail(f"soundfile manquant : {e}")
        print(f"         {YELLOW}pip install soundfile{RESET}")
        return 1
    try:
        import numpy as np
        ok(f"numpy {np.__version__}")
    except ImportError as e:
        fail(f"numpy manquant : {e}")
        return 1

    # 4) Chargement du modèle
    section("4. Chargement du modele ONNX")
    t0 = time.perf_counter()
    try:
        kokoro = Kokoro(str(MODEL_PATH), str(VOICES_PATH))
        load_time = time.perf_counter() - t0
        ok(f"Modele charge en {load_time:.2f}s")
    except Exception as e:
        fail(f"Echec chargement : {type(e).__name__}: {e}")
        return 1

    # 5) Voix disponibles
    section("5. Voix disponibles")
    try:
        voices = kokoro.get_voices()
        ok(f"{len(voices)} voix : {', '.join(voices[:8])}{'...' if len(voices) > 8 else ''}")
    except Exception as e:
        fail(f"get_voices() : {e}")
        voices = []

    voice = TEST_VOICE if TEST_VOICE in voices else (FALLBACK_VOICE if FALLBACK_VOICE in voices else (voices[0] if voices else TEST_VOICE))

    # 6) Synthese
    section(f"6. Synthese (voix={voice}, lang=fr-fr)")
    t0 = time.perf_counter()
    try:
        samples, sample_rate = kokoro.create(
            TEST_TEXT, voice=voice, speed=1.0, lang="fr-fr"
        )
        synth_time = time.perf_counter() - t0
        duration_s = len(samples) / sample_rate
        ok(f"Genere en {synth_time:.2f}s — {duration_s:.1f}s audio, {sample_rate} Hz, {len(samples)} samples")
    except RuntimeError as e:
        fail(f"RuntimeError : {e}")
        return 1
    except Exception as e:
        fail(f"{type(e).__name__}: {e}")
        return 1

    # 7) Sauvegarde WAV
    section("7. Sauvegarde WAV")
    try:
        sf.write(str(OUTPUT_FILE), samples, sample_rate)
        size_kb = OUTPUT_FILE.stat().st_size / 1024
        ok(f"Ecrit : {OUTPUT_FILE.name} ({size_kb:.0f} KB)")
    except Exception as e:
        fail(f"Ecriture WAV : {e}")
        return 1

    # 8) Conversion in-memory (meme logique que tts.py)
    section("8. Conversion in-memory (WAV bytes)")
    import io
    try:
        buf = io.BytesIO()
        sf.write(buf, samples, sample_rate, format="WAV")
        wav_bytes = buf.getvalue()
        ok(f"Buffer WAV : {len(wav_bytes)} bytes")
        if len(wav_bytes) < 100:
            fail("Buffer suspicieusement petit — probleme de conversion ?")
            return 1
    except Exception as e:
        fail(f"Conversion in-memory : {e}")
        return 1

    # Resume
    section("RESULTAT")
    print(f"  {GREEN}Kokoro TTS fonctionne correctement.{RESET}")
    print(f"  {DIM}Fichier de test : {OUTPUT_FILE}{RESET}")
    print(f"  {DIM}Pour ecouter : open {OUTPUT_FILE}{RESET}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
