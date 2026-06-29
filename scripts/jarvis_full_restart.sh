#!/usr/bin/env bash
# Redémarrage propre du stack JARVIS : arrêt des écouteurs connus, nettoyage des caches
# légers, relance du backend (et optionnellement Vite en mode dev).
#
# Usage :
#   ./scripts/jarvis_full_restart.sh              # backend au premier plan
#   ./scripts/jarvis_full_restart.sh --dev        # + Vite (5173) en arrière-plan, backend au premier plan
#   ./scripts/jarvis_full_restart.sh --daemon     # backend en arrière-plan (logs dans data/.jarvis_restart/)
#   ./scripts/jarvis_full_restart.sh --daemon --dev
#   ./scripts/jarvis_full_restart.sh --no-clean   # ne pas supprimer __pycache__ / cache Vite
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

WITH_WEB=false
CLEAN=true
DAEMON=false

usage() {
  sed -n '1,12p' "$0" | tail -n +2
  exit "${1:-0}"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dev|--with-web) WITH_WEB=true ;;
    --no-clean) CLEAN=false ;;
    --daemon|-d) DAEMON=true ;;
    -h|--help) usage 0 ;;
    *) echo "Option inconnue : $1" >&2; usage 1 ;;
  esac
  shift
done

# WEB_PORT depuis .env (aligné sur config.py, défaut 8080)
read_env_int() {
  local key="$1" default="$2"
  local raw
  if [[ -f .env ]]; then
    raw="$(grep -E "^[[:space:]]*${key}[[:space:]]*=" .env 2>/dev/null | tail -1 || true)"
    raw="${raw#*=}"
    raw="${raw%%#*}"
    raw="$(echo "$raw" | tr -d '[:space:]')"
    if [[ -n "$raw" ]] && [[ "$raw" =~ ^[0-9]+$ ]]; then
      echo "$raw"
      return
    fi
  fi
  echo "$default"
}

WEB_PORT="$(read_env_int WEB_PORT 8080)"
VITE_PORT="$(read_env_int VITE_DEV_PORT 5173)"

log() { printf '%s\n' "$*"; }

kill_port_listen() {
  local port="$1" label="${2:-port $port}"
  local pids
  pids="$(lsof -nP -iTCP:"$port" -sTCP:LISTEN -t 2>/dev/null || true)"
  if [[ -z "$pids" ]]; then
    log "  (rien sur $label)"
    return 0
  fi
  log "  Arrêt $label (PID : $pids)"
  kill -TERM $pids 2>/dev/null || true
  sleep 1
  pids="$(lsof -nP -iTCP:"$port" -sTCP:LISTEN -t 2>/dev/null || true)"
  if [[ -n "$pids" ]]; then
    kill -KILL $pids 2>/dev/null || true
  fi
}

clean_caches() {
  log "Nettoyage des caches (Python hors venv, prébundle Vite)…"
  find "$ROOT" \
    \( -path "$ROOT/venv" -o -path "$ROOT/web/node_modules" -o -path "$ROOT/.git" \) -prune -o \
    -type d -name __pycache__ -print 2>/dev/null | while read -r d; do
      rm -rf "$d"
    done
  find "$ROOT" \
    \( -path "$ROOT/venv" -o -path "$ROOT/web/node_modules" -o -path "$ROOT/.git" \) -prune -o \
    -type f -name '*.pyc' -print 2>/dev/null | while read -r f; do
      rm -f "$f"
    done
  rm -rf "$ROOT/web/node_modules/.vite" 2>/dev/null || true
  rm -rf "$ROOT/.pytest_cache" 2>/dev/null || true
  log "  Terminé."
}

RUN_DIR="$ROOT/data/.jarvis_restart"
mkdir -p "$RUN_DIR"

log "── JARVIS — redémarrage propre ──"
log "Répertoire : $ROOT"
log "WEB_PORT (depuis .env ou défaut) : $WEB_PORT"

log "Arrêt des processus en écoute…"
kill_port_listen "$WEB_PORT" "backend (port $WEB_PORT)"
if [[ "$WITH_WEB" == true ]]; then
  kill_port_listen "$VITE_PORT" "Vite (port $VITE_PORT)"
fi

if [[ "$CLEAN" == true ]]; then
  clean_caches
else
  log "Nettoyage des caches ignoré (--no-clean)."
fi

if [[ ! -f "$ROOT/venv/bin/activate" ]]; then
  log "Erreur : venv introuvable ($ROOT/venv). Crée-le avec : python3.12 -m venv venv" >&2
  exit 1
fi

# shellcheck source=/dev/null
source "$ROOT/venv/bin/activate"

WEB_PID=""
cleanup_web() {
  if [[ -n "${WEB_PID:-}" ]] && kill -0 "$WEB_PID" 2>/dev/null; then
    kill -TERM "$WEB_PID" 2>/dev/null || true
    wait "$WEB_PID" 2>/dev/null || true
  fi
}

if [[ "$WITH_WEB" == true ]]; then
  if ! command -v pnpm >/dev/null 2>&1; then
    log "Erreur : pnpm absent du PATH (requis pour --dev)." >&2
    exit 1
  fi
fi

if [[ "$DAEMON" == true ]]; then
  log "Démarrage backend en arrière-plan → logs : $RUN_DIR/backend.log"
  nohup python "$ROOT/main.py" >>"$RUN_DIR/backend.log" 2>&1 &
  echo $! >"$RUN_DIR/backend.pid"
  log "  PID backend : $(cat "$RUN_DIR/backend.pid")"
  if [[ "$WITH_WEB" == true ]]; then
    log "Démarrage Vite en arrière-plan → logs : $RUN_DIR/web.log"
    (
      cd "$ROOT/web" || exit 1
      nohup pnpm dev >>"$RUN_DIR/web.log" 2>&1 &
      echo $! >"$RUN_DIR/web.pid"
    )
    sleep 0.5
    if [[ -f "$RUN_DIR/web.pid" ]]; then
      log "  PID Vite : $(cat "$RUN_DIR/web.pid")"
    fi
  fi
  log "Prêt. Backend : http://127.0.0.1:$WEB_PORT"
  [[ "$WITH_WEB" == true ]] && log "Front dev : http://127.0.0.1:$VITE_PORT"
  log "Suivi des logs : tail -f $RUN_DIR/backend.log"
  exit 0
fi

if [[ "$WITH_WEB" == true ]]; then
  trap cleanup_web EXIT INT TERM
  log "Démarrage Vite (arrière-plan, port $VITE_PORT)…"
  (cd "$ROOT/web" && exec pnpm dev) &
  WEB_PID=$!
  sleep 1
fi

log "Démarrage du backend (premier plan, Ctrl+C pour arrêter)…"
if [[ "$WITH_WEB" == true ]]; then
  log "  Vite tourne en parallèle (PID $WEB_PID) ; il sera arrêté à la sortie du backend."
  python "$ROOT/main.py"
  cleanup_web
else
  exec python "$ROOT/main.py"
fi
