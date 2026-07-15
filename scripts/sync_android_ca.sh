#!/usr/bin/env bash
# Synchronise le certificat public JARVIS vers la ressource Android (pinning TLS).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$ROOT/certs/cert.pem"
DST="$ROOT/android/app/src/main/res/raw/jarvis_ca.crt"
if [[ ! -f "$SRC" ]]; then
  echo "Certificat source absent : $SRC" >&2
  echo "Générez-le avec : bash scripts/generate_ssl.sh" >&2
  exit 1
fi
cp "$SRC" "$DST"
echo "CA synchronisé → $DST"
openssl x509 -in "$DST" -noout -subject -dates -ext subjectAltName
