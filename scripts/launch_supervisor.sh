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
    echo "  python3.12 -m venv venv && source venv/bin/activate && pip install -r requirements.txt"
    exit 1
fi

# ── Logs ───────────────────────────────────────────────────
mkdir -p data/logs

# ── Tuer un eventuel superviseur existant ──────────────────
# 1. Decharger launchd d'abord (sinon KeepAlive le relance)
launchctl bootout gui/$(id -u)/com.jarvis.supervisor 2>/dev/null || true
sleep 0.5

# 2. Tuer les processus sur le port 9000
EXISTING=$(lsof -nP -iTCP:"${SUPERVISOR_PORT}" -sTCP:LISTEN -t 2>/dev/null || true)
if [[ -n "$EXISTING" ]]; then
    echo "[INFO] Processus existants sur port ${SUPERVISOR_PORT} (PIDs: $EXISTING) → arret..."
    for pid in $EXISTING; do
        kill -TERM "$pid" 2>/dev/null || true
    done
    sleep 1
    for pid in $EXISTING; do
        kill -KILL "$pid" 2>/dev/null || true
    done
    sleep 0.5
fi

# 3. Verifier que le port est bien libre
ATTEMPTS=0
while lsof -nP -iTCP:"${SUPERVISOR_PORT}" -sTCP:LISTEN -t 2>/dev/null | grep -q .; do
    ATTEMPTS=$((ATTEMPTS + 1))
    if [[ $ATTEMPTS -gt 5 ]]; then
        echo "[ERREUR] Impossible de liberer le port ${SUPERVISOR_PORT} apres 5 tentatives."
        exit 1
    fi
    echo "[INFO] Port ${SUPERVISOR_PORT} toujours occupe, tentative $ATTEMPTS/5..."
    lsof -nP -iTCP:"${SUPERVISOR_PORT}" -sTCP:LISTEN -t 2>/dev/null | xargs kill -KILL 2>/dev/null || true
    sleep 1
done
echo "[INFO] Port ${SUPERVISOR_PORT} libre."

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
