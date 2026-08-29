#!/bin/bash
set -euo pipefail

VOICE_DISPLAY_URL="${VOICE_DISPLAY_URL:-http://127.0.0.1:8080/voice-display?kiosk=1}"

case "$VOICE_DISPLAY_URL" in
  http://*|https://*) ;;
  *) echo "VOICE_DISPLAY_URL doit être une URL HTTP(S)." >&2; exit 2 ;;
esac

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "Ce lanceur utilise le mode kiosk natif de macOS." >&2
  exit 1
fi

open -na "Google Chrome" --args --kiosk --no-first-run --disable-session-crashed-bubble "$VOICE_DISPLAY_URL"
