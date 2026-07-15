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

echo "→ Vérification faster-whisper + kokoro-onnx…"
python - <<'PY'
import importlib.util
missing = []
for pkg in ("faster_whisper", "kokoro_onnx", "soundfile", "numpy"):
    if importlib.util.find_spec(pkg) is None:
        missing.append(pkg)
if missing:
    raise SystemExit(
        "Paquets manquants : "
        + ", ".join(missing)
        + "\nInstallez : pip install -r requirements.txt"
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

# ── Kokoro (modèles ONNX locaux) ───────────────────────────────────────────
KOKORO_DIR="$ROOT/models/kokoro"
KOKORO_ONNX="$KOKORO_DIR/kokoro-v0_19.onnx"
KOKORO_VOICES="$KOKORO_DIR/voices.bin"

echo ""
echo "→ Kokoro (TTS local, ~350 Mo) : $KOKORO_DIR"
if [[ -f "$KOKORO_ONNX" && -f "$KOKORO_VOICES" ]]; then
  echo "  OK — modèles Kokoro présents"
else
  echo "  MANQUANT — kokoro-v0_19.onnx et voices.bin requis"
  echo "  Placez-les dans models/kokoro/ (voir README) ou relancez avec --download"
  if $DOWNLOAD; then
    mkdir -p "$KOKORO_DIR"
    echo "  Téléchargement Kokoro non automatisé ici — copiez les fichiers depuis"
    echo "  https://github.com/thewh1teagle/kokoro-onnx/releases"
  fi
fi

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
echo "  TTS_ENGINE=kokoro"
echo "  KOKORO_VOICE=af_nicole"
echo "  KOKORO_LANG=fr-fr"
echo ""
echo "Terminé."
