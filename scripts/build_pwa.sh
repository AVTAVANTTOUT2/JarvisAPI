#!/bin/bash
# build_pwa.sh — Build de la PWA mobile Next.js et déploiement dans pwa/out/.
#
# Usage :
#   bash scripts/build_pwa.sh            # build + déploiement
#   bash scripts/build_pwa.sh --watch    # build + watch (dev)
#
# La PWA est servie par FastAPI sous /m/ quand PWA_ENABLED=true.
# Le build statique (output: 'export') est déposé dans pwa/out/.
# Après build, redémarrer le backend pour que FastAPI monte le nouveau build.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
PWA_DIR="$PROJECT_DIR/pwa"
OUT_DIR="$PWA_DIR/out"

# ── Couleurs ──────────────────────────────────────────────────
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

log()  { echo -e "${GREEN}[PWA]${NC} $*"; }
warn() { echo -e "${YELLOW}[PWA]${NC} $*"; }
err()  { echo -e "${RED}[PWA]${NC} $*" >&2; }

# ── Vérifications ─────────────────────────────────────────────
if [ ! -d "$PWA_DIR" ]; then
    err "Répertoire pwa/ introuvable : $PWA_DIR"
    exit 1
fi

if ! command -v node &>/dev/null; then
    err "Node.js n'est pas installé"
    exit 1
fi

# ── Installation des dépendances (si nécessaire) ──────────────
cd "$PWA_DIR"

# Détecter le package manager
PM="npm"
if command -v pnpm &>/dev/null && [ -f "$PWA_DIR/pnpm-lock.yaml" ]; then
    PM="pnpm"
elif command -v yarn &>/dev/null && [ -f "$PWA_DIR/yarn.lock" ]; then
    PM="yarn"
fi

if [ ! -d "$PWA_DIR/node_modules" ]; then
    log "Installation des dépendances ($PM install)..."
    $PM install
fi

# ── Clean build précédent ─────────────────────────────────────
if [ -d "$OUT_DIR" ]; then
    log "Nettoyage du build précédent..."
    rm -rf "$OUT_DIR"
fi

# ── Build ─────────────────────────────────────────────────────
log "Build Next.js statique (output: 'export')..."
if [ "${1:-}" = "--watch" ]; then
    $PM run build --watch
else
    $PM run build
fi

# ── Vérification post-build ───────────────────────────────────
if [ ! -f "$OUT_DIR/index.html" ]; then
    err "Build échoué : pwa/out/index.html absent"
    exit 1
fi

ASSET_COUNT=$(find "$OUT_DIR" -type f | wc -l | tr -d ' ')
log "Build terminé : $ASSET_COUNT fichiers dans $OUT_DIR"

# ── Vérification des fichiers critiques ───────────────────────
MISSING=0
for f in "index.html" "dashboard.html" "sw.js" "manifest.json"; do
    if [ ! -f "$OUT_DIR/$f" ]; then
        warn "Fichier critique manquant : $f"
        MISSING=$((MISSING + 1))
    fi
done

if [ "$MISSING" -gt 0 ]; then
    warn "$MISSING fichier(s) critique(s) manquant(s) — la PWA peut ne pas fonctionner correctement"
else
    log "Tous les fichiers critiques sont présents"
fi

# ── Instructions ──────────────────────────────────────────────
echo ""
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}  PWA build prête. Redémarre le backend :${NC}"
echo -e "${GREEN}    python main.py${NC}"
echo -e "${GREEN}  Puis accède depuis un mobile :${NC}"
echo -e "${GREEN}    http://TON_IP:8081/          (redirection auto)${NC}"
echo -e "${GREEN}    http://TON_IP:8081/m/        (accès direct PWA)${NC}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
