#!/usr/bin/env bash
# Vérifie que le backend JARVIS répond bien en HTTPS quand WEB_HTTPS=true.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
source venv/bin/activate 2>/dev/null || true
PORT="${WEB_PORT:-8081}"
python - <<'PY'
import sys
import httpx
import config

port = config.WEB_PORT
if not config.WEB_HTTPS:
    print("WEB_HTTPS=false — vérification HTTPS ignorée (mode HTTP attendu).")
    sys.exit(0)
if not config.WEB_SSL_AVAILABLE:
    print("ERREUR: WEB_HTTPS=true mais certificats absents", file=sys.stderr)
    sys.exit(1)
url = f"https://127.0.0.1:{port}/api/auth/status"
try:
    r = httpx.get(url, verify=str(config.SSL_CERT_PATH), timeout=5.0)
except Exception as exc:
    print(f"ERREUR HTTPS: {exc}", file=sys.stderr)
    sys.exit(1)
print(f"HTTPS OK {url} -> HTTP {r.status_code}")
try:
    httpx.get(f"http://127.0.0.1:{port}/api/auth/status", timeout=2.0)
    print("AVERTISSEMENT: le port répond aussi en HTTP (processus mixte ou proxy).", file=sys.stderr)
except Exception:
    print("HTTP refusé sur le port (attendu en mode HTTPS seul).")
PY
