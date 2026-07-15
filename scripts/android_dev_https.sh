#!/usr/bin/env bash
# Démarre JARVIS en HTTPS natif (sans pont TLS temporaire) pour l'app Android.
# L'émulateur accède au Mac via https://10.0.2.2:8081 (SAN présent dans certs/cert.pem).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export WEB_HTTPS=true
export WEB_HOST=0.0.0.0
export WEB_PORT="${WEB_PORT:-8081}"
if [[ ! -f certs/cert.pem || ! -f certs/key.pem ]]; then
  echo "Certificats manquants — lancez : bash scripts/generate_ssl.sh" >&2
  exit 1
fi
bash scripts/sync_android_ca.sh
echo "Démarrage JARVIS HTTPS sur 0.0.0.0:${WEB_PORT} (Ctrl+C pour arrêter)"
exec python main.py
