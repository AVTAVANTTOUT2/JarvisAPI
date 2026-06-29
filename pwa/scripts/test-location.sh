#!/bin/bash
# ──────────────────────────────────────────────────────────────
# Test E2E des endpoints localisation (proxy Next.js -> FastAPI)
#
# Usage : ./pwa/scripts/test-location.sh
# ──────────────────────────────────────────────────────────────

set -u

BASE="${BASE_URL:-http://localhost:3000}"
PASS=0
FAIL=0

GREEN='\033[0;32m'
RED='\033[0;31m'
DIM='\033[2m'
RESET='\033[0m'

check_code() {
  local got=$1 expected=$2 desc=$3
  if [ "$got" = "$expected" ]; then
    printf "  ${GREEN}OK${RESET}    %-50s (%s) %s\n" "$desc" "$got" ""
    PASS=$((PASS + 1))
    return 0
  else
    printf "  ${RED}FAIL${RESET}  %-50s (got %s, expected %s)\n" "$desc" "$got" "$expected"
    FAIL=$((FAIL + 1))
    return 1
  fi
}

show_preview() {
  local file=$1 max=${2:-120}
  if [ -f "$file" ]; then
    printf "        ${DIM}%s${RESET}\n" "$(head -c "$max" "$file")"
  fi
}

echo "=== JARVIS PWA — Tests E2E localisation ==="
echo "Base URL : $BASE"
echo ""

# 1. POST /api/location — point de test (Lille centre)
echo "[1] POST /api/location"
CODE=$(curl -s -o /tmp/jarvis-loc.json -w "%{http_code}" \
  -X POST "$BASE/api/location" \
  -H "Content-Type: application/json" \
  -d '{"latitude":50.6292,"longitude":3.0573,"accuracy":10,"source":"test_script"}')
check_code "$CODE" 200 "Enregistrer un point GPS"
show_preview /tmp/jarvis-loc.json

# 2. GET /api/location/status — verifier la derniere position
echo "[2] GET /api/location/status"
CODE=$(curl -s -o /tmp/jarvis-loc-status.json -w "%{http_code}" "$BASE/api/location/status")
check_code "$CODE" 200 "Statut de la localisation"
show_preview /tmp/jarvis-loc-status.json 180

# 3. GET /api/location/history?hours=1
echo "[3] GET /api/location/history?hours=1"
CODE=$(curl -s -o /tmp/jarvis-loc-hist.json -w "%{http_code}" "$BASE/api/location/history?hours=1")
check_code "$CODE" 200 "Historique GPS 1h"

# 4. GET /api/places
echo "[4] GET /api/places"
CODE=$(curl -s -o /tmp/jarvis-places.json -w "%{http_code}" "$BASE/api/places")
check_code "$CODE" 200 "Liste des lieux"
PLACE_COUNT=$(python3 -c "
import sys, json
try:
    d = json.load(open('/tmp/jarvis-places.json'))
    items = d.get('places', d) if isinstance(d, dict) else d
    print(len(items) if isinstance(items, list) else 0)
except Exception:
    print(0)
")
printf "        ${DIM}%s lieu(x) connu(s)${RESET}\n" "$PLACE_COUNT"

# 5. GET /api/visits/today
echo "[5] GET /api/visits/today"
CODE=$(curl -s -o /tmp/jarvis-visits.json -w "%{http_code}" "$BASE/api/visits/today")
check_code "$CODE" 200 "Visites du jour"

# 6. GET /api/trips?days=7
echo "[6] GET /api/trips?days=7"
CODE=$(curl -s -o /dev/null -w "%{http_code}" "$BASE/api/trips?days=7")
check_code "$CODE" 200 "Trajets 7 derniers jours"

# 7. GET /api/location/patterns
echo "[7] GET /api/location/patterns"
CODE=$(curl -s -o /tmp/jarvis-patterns.json -w "%{http_code}" "$BASE/api/location/patterns")
check_code "$CODE" 200 "Patterns geographiques"

# 8. POST /api/location/batch — 2 points
echo "[8] POST /api/location/batch"
CODE=$(curl -s -o /dev/null -w "%{http_code}" \
  -X POST "$BASE/api/location/batch" \
  -H "Content-Type: application/json" \
  -d '{"points":[{"latitude":50.6292,"longitude":3.0573,"source":"test_script"},{"latitude":50.6300,"longitude":3.0580,"source":"test_script"}]}')
check_code "$CODE" 200 "Batch de points GPS"

# 9. POST /api/location/name-current — creation de lieu test
#    (category doit etre dans la liste autorisee par le CHECK SQL : home/work/other/...)
echo "[9] POST /api/location/name-current"
CODE=$(curl -s -o /tmp/jarvis-name.json -w "%{http_code}" \
  -X POST "$BASE/api/location/name-current" \
  -H "Content-Type: application/json" \
  -d '{"name":"Test JARVIS PWA","category":"other"}')
check_code "$CODE" 200 "Nommer le lieu courant"
show_preview /tmp/jarvis-name.json

# 10. Cleanup : supprimer le lieu test
PLACE_ID=$(python3 -c "
import sys, json
try:
    d = json.load(open('/tmp/jarvis-name.json'))
    pid = d.get('id') or d.get('place_id') or d.get('place', {}).get('id')
    print(pid if pid is not None else '')
except Exception:
    print('')
")
if [ -n "$PLACE_ID" ]; then
  echo "[10] DELETE /api/places/$PLACE_ID (cleanup)"
  CODE=$(curl -s -o /dev/null -w "%{http_code}" -X DELETE "$BASE/api/places/$PLACE_ID")
  check_code "$CODE" 200 "Supprimer lieu test (cleanup)"
else
  printf "  ${DIM}--${RESET}    Cleanup skipped (pas d'id retourne)\n"
fi

echo ""
echo "─────────────────────────────────────────────"
if [ "$FAIL" = 0 ]; then
  printf "${GREEN}=== %s / %s OK ===${RESET}\n" "$PASS" "$((PASS + FAIL))"
  exit 0
else
  printf "${RED}=== %s OK · %s FAIL sur %s ===${RESET}\n" "$PASS" "$FAIL" "$((PASS + FAIL))"
  exit 1
fi
