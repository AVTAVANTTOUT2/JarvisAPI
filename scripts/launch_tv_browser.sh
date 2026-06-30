#!/bin/bash
# ============================================================
# JARVIS TV Browser Launcher
# Lance Kiwi Browser sur la TV Philips avec le dashboard War Room
# et configure le bridge CDP pour l'acces MCP.
# ============================================================
set -euo pipefail

TV_IP="${TV_IP:-192.168.3.82}"
TV_ADB_PORT="${TV_ADB_PORT:-5555}"
CDP_LOCAL_PORT="${CDP_LOCAL_PORT:-9222}"
DASHBOARD_URL="${1:-http://192.168.3.52:5174/}"

KIWI_PACKAGE="com.kiwibrowser.browser"
KIWI_ACTIVITY="${KIWI_PACKAGE}/com.google.android.apps.chrome.Main"
ADB="${ADB:-adb}"

GREEN='\033[0;32m'
CYAN='\033[0;36m'
NC='\033[0m'

log()  { echo -e "${CYAN}[TV]${NC} $*"; }
ok()   { echo -e "${GREEN}[OK]${NC}  $*"; }

# 1. ADB connect
log "Connexion ADB a ${TV_IP}:${TV_ADB_PORT}..."
$ADB connect "${TV_IP}:${TV_ADB_PORT}" 2>/dev/null || true
ok "ADB connecte"

# 2. Reveiller TV
log "Reveil TV..."
$ADB shell input keyevent KEYCODE_WAKEUP 2>/dev/null || true
$ADB shell input keyevent KEYCODE_DPAD_CENTER 2>/dev/null || true
sleep 2

# 3. Mode immersif
$ADB shell 'settings put global policy_control immersive.full=*' 2>/dev/null || true
ok "Mode immersif active"

# 4. Lancer Kiwi
KIWI_RUNNING=$($ADB shell pidof "${KIWI_PACKAGE}" 2>/dev/null || echo "")
if [ -n "$KIWI_RUNNING" ]; then
    log "Kiwi deja lance, navigation..."
    $ADB shell am start -n "${KIWI_ACTIVITY}" -d "${DASHBOARD_URL}" -f 0x10000000 2>/dev/null
else
    log "Lancement Kiwi Browser..."
    $ADB shell am start -n "${KIWI_ACTIVITY}" -d "${DASHBOARD_URL}" 2>/dev/null
    for i in $(seq 1 10); do
        sleep 1
        if $ADB shell pidof "${KIWI_PACKAGE}" 2>/dev/null | grep -q .; then break; fi
    done
fi
ok "Kiwi Browser lance"

# 5. CDP forward
log "Bridge CDP port ${CDP_LOCAL_PORT}..."
$ADB forward "tcp:${CDP_LOCAL_PORT}" localabstract:chrome_devtools_remote 2>/dev/null
sleep 2

if curl -s "http://localhost:${CDP_LOCAL_PORT}/json/version" > /dev/null 2>&1; then
    ok "Bridge CDP actif -> http://localhost:${CDP_LOCAL_PORT}"
else
    echo "[WARN] CDP en cours d'initialisation..."
fi

echo ""
echo -e "${GREEN}=============================================${NC}"
echo -e "${GREEN}  TV Browser Ready${NC}"
echo -e "${GREEN}  Dashboard: ${DASHBOARD_URL}${NC}"
echo -e "${GREEN}  CDP: http://localhost:${CDP_LOCAL_PORT}${NC}"
echo -e "${GREEN}=============================================${NC}"
