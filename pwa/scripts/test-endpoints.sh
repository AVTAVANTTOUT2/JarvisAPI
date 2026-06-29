#!/bin/bash
# ──────────────────────────────────────────────────────────────
# Test E2E des endpoints PWA (proxy Next.js -> backend FastAPI)
#
# Usage : ./pwa/scripts/test-endpoints.sh
# ──────────────────────────────────────────────────────────────

set -u

BASE="${BASE_URL:-http://localhost:3000}"
PASS=0
FAIL=0

GREEN='\033[0;32m'
RED='\033[0;31m'
RESET='\033[0m'

test_endpoint() {
  local method=$1 url=$2 expected=$3 desc=$4
  local code
  code=$(curl -s -o /dev/null -w "%{http_code}" -X "$method" "$BASE$url" 2>/dev/null || echo "000")
  if [ "$code" = "$expected" ]; then
    printf "  ${GREEN}OK${RESET}   %-6s %-50s (%s) %s\n" "$method" "$url" "$code" "$desc"
    PASS=$((PASS + 1))
  else
    printf "  ${RED}FAIL${RESET} %-6s %-50s (got %s, expected %s) %s\n" "$method" "$url" "$code" "$expected" "$desc"
    FAIL=$((FAIL + 1))
  fi
}

echo "=== JARVIS PWA — Tests E2E des endpoints ==="
echo "Base URL : $BASE"
echo ""

# Calcul des bornes du jour pour /api/calendar
TODAY_START=$(date -u +"%Y-%m-%dT00:00:00Z")
TODAY_END=$(date -u +"%Y-%m-%dT23:59:59Z")

test_endpoint GET  "/api/status"                                   200 "Etat backend"
test_endpoint GET  "/api/integrations"                             200 "Etat integrations"
test_endpoint GET  "/api/tasks"                                    200 "Toutes les taches"
test_endpoint GET  "/api/tasks?status=todo"                        200 "Taches todo"
test_endpoint GET  "/api/briefing?kind=morning"                    200 "Briefing matin"
test_endpoint GET  "/api/briefing?kind=evening"                    200 "Briefing soir"
test_endpoint GET  "/api/notifications"                            200 "Notifs non lues"
test_endpoint GET  "/api/notifications/all?limit=50"               200 "Toutes notifs"
test_endpoint GET  "/api/people"                                   200 "Contacts"
test_endpoint GET  "/api/journal"                                  200 "Journal"
test_endpoint GET  "/api/patterns"                                 200 "Patterns"
test_endpoint GET  "/api/calendar?start=$TODAY_START&end=$TODAY_END" 200 "Calendar du jour"

echo ""
echo "─────────────────────────────────────────────"
if [ "$FAIL" = 0 ]; then
  printf "${GREEN}=== $PASS / $((PASS + FAIL)) OK ===${RESET}\n"
  exit 0
else
  printf "${RED}=== $PASS OK · $FAIL FAIL sur $((PASS + FAIL)) ===${RESET}\n"
  exit 1
fi
