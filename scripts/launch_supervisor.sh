#!/bin/bash
# ── JARVIS Supervisor — lancement permanent ──
# Process qui ne s'arrete jamais, sert le frontend + controle tous les services.
#
# Usage :
#   ./scripts/launch_supervisor.sh              # demarre tout (backend auto-start)
#   ./scripts/launch_supervisor.sh --no-backend # superviseur seul, backend manuel

set -euo pipefail

cd "$(dirname "$0")/.."
PROJECT_DIR="$(pwd)"
SUPERVISOR_PORT="${SUPERVISOR_PORT:-9000}"

# ── Verifications ──────────────────────────────────────────
if [[ ! -f "venv/bin/python" ]]; then
    echo "[ERREUR] venv/bin/python introuvable — creer l'environnement d'abord :"
    echo "  python3.12 -m venv venv && source venv/bin/activate && pip install --require-hashes -r requirements/locks/production-macos-arm64-py312.txt"
    exit 1
fi

# ── Logs ───────────────────────────────────────────────────
mkdir -p data/logs

# ── Tuer un eventuel superviseur existant ──────────────────
# 1. Decharger launchd d'abord (sinon KeepAlive le relance)
launchctl bootout gui/$(id -u)/com.jarvis.supervisor 2>/dev/null || true
sleep 0.5

# 2. Attendre que les PID JARVIS libèrent le port. Un occupant tiers bloque
#    le démarrage : un port occupé ne prouve jamais la propriété d'un processus.
export PYTHONPATH="${PROJECT_DIR}${PYTHONPATH:+:$PYTHONPATH}"
if ! venv/bin/python -c "
from pathlib import Path
import sys
from scripts.jarvis_stack import RestartBlocked, prepare_supervisor_bind
try:
    prepare_supervisor_bind(Path('.').resolve(), int('${SUPERVISOR_PORT}'))
except RestartBlocked as exc:
    print('[ERREUR]', exc)
    sys.exit(1)
"; then
    echo "[ERREUR] Démarrage superviseur refusé (port tiers ou arrêt incomplet)."
    exit 1
fi
echo "[INFO] Port ${SUPERVISOR_PORT} disponible pour JARVIS."

# ── Config ─────────────────────────────────────────────────
export SUPERVISOR_AUTO_START_BACKEND="true"
if [[ "${1:-}" == "--no-backend" ]]; then
    export SUPERVISOR_AUTO_START_BACKEND="false"
fi
JARVIS_WEB_SCHEME="$(venv/bin/python -c 'import config; print("https" if config.WEB_USE_HTTPS else "http")')"
JARVIS_WS_SCHEME="$( [[ "${JARVIS_WEB_SCHEME}" == "https" ]] && echo "wss" || echo "ws" )"

# ── Banner ─────────────────────────────────────────────────
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  JARVIS Supervisor"
echo "  Projet   : ${PROJECT_DIR}"
echo "  Port     : ${SUPERVISOR_PORT}"
echo "  Frontend : ${JARVIS_WEB_SCHEME}://localhost:${SUPERVISOR_PORT}"
echo "  API      : ${JARVIS_WEB_SCHEME}://localhost:${SUPERVISOR_PORT}/api/supervisor/status"
echo "  WS       : ${JARVIS_WS_SCHEME}://localhost:${SUPERVISOR_PORT}/ws/supervisor"
echo "  Backend  : $( [[ "${SUPERVISOR_AUTO_START_BACKEND}" == "true" ]] && echo 'auto-start' || echo 'manuel' )"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# ── Lancer le superviseur ──────────────────────────────────
source venv/bin/activate
exec python supervisor.py
