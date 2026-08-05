#!/usr/bin/env bash
# Préparation idempotente de la pile audio locale JARVIS (STT + TTS).
# Ne télécharge les modèles lourds qu'avec consentement explicite (--download).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

DOWNLOAD=false
if [[ "${1:-}" == "--download" ]]; then
  DOWNLOAD=true
fi

echo "=== JARVIS — setup audio local ==="
echo "Architecture: $(uname -m)"
echo ""

# ── Dépendances Python ─────────────────────────────────────────────────────
if [[ -d "venv/bin" ]]; then
  # shellcheck disable=SC1091
  source venv/bin/activate
elif [[ -d ".venv/bin" ]]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi

echo "→ Vérification faster-whisper…"
python - <<'PY'
import importlib.util
missing = []
for pkg in ("faster_whisper", "soundfile", "numpy"):
    if importlib.util.find_spec(pkg) is None:
        missing.append(pkg)
if missing:
    raise SystemExit(
        "Paquets manquants : "
        + ", ".join(missing)
        + "\nInstallez : pip install --require-hashes -r requirements/locks/production-macos-arm64-py312.txt"
    )
print("  OK — dépendances Python présentes")
PY

# Versions utiles au diagnostic
python - <<'PY'
try:
    import faster_whisper
    print(f"  faster-whisper: {getattr(faster_whisper, '__version__', 'unknown')}")
except Exception as exc:
    print(f"  faster-whisper: erreur {exc}")
try:
    import ctranslate2
    print(f"  ctranslate2: {getattr(ctranslate2, '__version__', 'unknown')}")
except Exception as exc:
    print(f"  ctranslate2: non installé ({exc})")
PY

# ── Synthèse vocale locale ─────────────────────────────────────────────────
# JARVIS ne télécharge jamais de poids : ce script dit seulement s'ils sont là,
# et par quelle commande les installer.
echo ""
echo "→ Moteur vocal local"
python - <<'VOICEPY'
from jarvis.audio.tts import load_tts_settings

settings = load_tts_settings()
print(f"  fournisseur : {settings.provider}")
print(f"  modele      : {settings.model_path}")
print(f"  voix        : {settings.voice_path}")

from native_audio.qwen3_local import Qwen3ModelMissing, resolve_model_dir

try:
    print(f"  OK — poids presents : {resolve_model_dir(settings.model_path)}")
except Qwen3ModelMissing as exc:
    print(f"  MANQUANT — {exc}")
    print("  Installation : python scripts/download_tts_model.py")
VOICEPY

# ── Faster-Whisper large-v3-turbo (~1,5 Go) ────────────────────────────────
WHISPER_CACHE="${HOME}/.cache/faster-whisper"
STT_MODEL="large-v3-turbo"
FALLBACK_MODEL="large-v3"

echo ""
echo "→ Faster-Whisper STT : modèle $STT_MODEL (cache $WHISPER_CACHE)"
echo "  Espace disque recommandé : ~2 Go pour turbo + fallback $FALLBACK_MODEL"

if $DOWNLOAD; then
  echo "  Téléchargement avec consentement explicite…"
  python - <<PY
from faster_whisper import WhisperModel
for model in ("$STT_MODEL", "$FALLBACK_MODEL"):
    print(f"  → {model}…")
    WhisperModel(model, device="auto", compute_type="auto", download_root="$WHISPER_CACHE")
print("  OK — modèles Whisper en cache")
PY
else
  python - <<PY
import os
from faster_whisper import WhisperModel
cache = os.path.expanduser("$WHISPER_CACHE")
for model in ("$STT_MODEL", "$FALLBACK_MODEL"):
    try:
        WhisperModel(model, device="auto", compute_type="auto", download_root=cache, local_files_only=True)
        print(f"  OK — {model} présent")
    except Exception as exc:
        print(f"  MANQUANT — {model} ({exc})")
        print("  Relancez : bash scripts/setup_local_audio.sh --download")
PY
fi

echo ""
echo "Configuration par défaut attendue (.env.config) :"
echo "  STT_ENGINE=faster-whisper"
echo "  STT_MODEL=large-v3-turbo"
echo "  TTS_PROVIDER=qwen3_local"
echo "  TTS_MODEL_PATH=mlx-community/Qwen3-TTS-12Hz-0.6B-Base-6bit"
echo "  TTS_VOICE_PATH=./voices/jarvis-fr"
echo ""
echo "Terminé."
