#!/bin/bash
# ── JARVIS Backend Launcher ──────────────────────────────────────────────
# Ce script doit etre lance via Terminal.app (pas Cursor, pas iTerm, pas screen)
# car macOS exige une connexion au window server pour le dialogue de permission
# microphone. Terminal.app herite de cette connexion.
#
# Usage :
#   open -a Terminal scripts/launch_backend.sh
#
# Premier lancement : accepter le dialogue "Terminal souhaite acceder au microphone"
# ──────────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  JARVIS Backend"
echo "  Micro : Blue Snowball"
echo "  STT   : faster-whisper tiny (local)"
echo "  TTS   : macOS say + afconvert (local)"
echo "  LLM   : DeepSeek v4-flash / v4-pro"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# Verifier que le venv existe
if [ ! -f venv/bin/python ]; then
    echo "ERREUR : venv introuvable. Executer : python3 -m venv venv && pip install -r requirements.txt"
    exit 1
fi

# Activation
source venv/bin/activate

# Premier lancement : le dialogue micro apparait ici
echo "Si un dialogue 'Terminal souhaite acceder au microphone' apparait, accepte-le."
echo ""

exec python main.py
