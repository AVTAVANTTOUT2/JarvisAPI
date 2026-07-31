#!/bin/bash
# ============================================================
# JARVIS TV Browser Launcher
# Lance Kiwi Browser sur la TV Philips avec le dashboard War Room
# et configure le bridge CDP pour l'acces MCP.
# ============================================================
set -euo pipefail

TV_IP="${TV_IP:-}"
TV_ADB_PORT="${TV_ADB_PORT:-5555}"
CDP_LOCAL_PORT="${CDP_LOCAL_PORT:-9222}"
DASHBOARD_URL="${1:-${TV_DASHBOARD_URL:-}}"
TV_AUTH_TOKEN="${TV_AUTH_TOKEN:-}"
TV_ALLOW_NETWORK_ADB="${TV_ALLOW_NETWORK_ADB:-false}"

KIWI_PACKAGE="com.kiwibrowser.browser"
KIWI_ACTIVITY="${KIWI_PACKAGE}/com.google.android.apps.chrome.Main"
ADB="${ADB:-adb}"
ADB_TARGET="${TV_IP}:${TV_ADB_PORT}"
ADB_DEVICE=("$ADB" -s "$ADB_TARGET")

GREEN='\033[0;32m'
CYAN='\033[0;36m'
NC='\033[0m'

log()  { echo -e "${CYAN}[TV]${NC} $*"; }
ok()   { echo -e "${GREEN}[OK]${NC}  $*"; }

case "$TV_ALLOW_NETWORK_ADB" in
    1|true|TRUE|yes|YES|on|ON) ;;
    *) echo "[ERROR] ADB réseau refusé. Définir TV_ALLOW_NETWORK_ADB=true explicitement." >&2; exit 1 ;;
esac

if [ -z "$TV_IP" ] || [ -z "$DASHBOARD_URL" ] || [ -z "$TV_AUTH_TOKEN" ]; then
    echo "[ERROR] TV_IP, TV_DASHBOARD_URL et TV_AUTH_TOKEN sont obligatoires." >&2
    exit 1
fi

DASHBOARD_AUTH_URL=$(TV_DASHBOARD_URL="$DASHBOARD_URL" TV_AUTH_TOKEN="$TV_AUTH_TOKEN" python3 -c '
import os
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

url = urlsplit(os.environ["TV_DASHBOARD_URL"])
if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password:
    raise SystemExit("TV_DASHBOARD_URL invalide")
query = [(key, value) for key, value in parse_qsl(url.query) if key != "token"]
query.append(("token", os.environ["TV_AUTH_TOKEN"]))
print(urlunsplit((url.scheme, url.netloc, url.path, urlencode(query), url.fragment)))
')

# 1. ADB connect
log "Connexion ADB à ${ADB_TARGET}..."
if ! "$ADB" connect "$ADB_TARGET" >/dev/null 2>&1; then
    echo "[ERROR] Échec de la connexion ADB à la cible déclarée." >&2
    exit 1
fi
if ! "$ADB" devices | awk -v target="$ADB_TARGET" '$1 == target && $2 == "device" { found=1 } END { exit !found }'; then
    echo "[ERROR] La cible ADB n'est pas connectée." >&2
    exit 1
fi
ok "ADB connecte"

# 2. Reveiller TV
log "Reveil TV..."
"${ADB_DEVICE[@]}" shell input keyevent KEYCODE_WAKEUP 2>/dev/null || true
"${ADB_DEVICE[@]}" shell input keyevent KEYCODE_DPAD_CENTER 2>/dev/null || true
sleep 2

# 3. Mode immersif
"${ADB_DEVICE[@]}" shell 'settings put global policy_control immersive.full=*' 2>/dev/null || true
ok "Mode immersif active"

# 4. Lancer Kiwi
KIWI_RUNNING=$("${ADB_DEVICE[@]}" shell pidof "${KIWI_PACKAGE}" 2>/dev/null || echo "")
if [ -n "$KIWI_RUNNING" ]; then
    log "Kiwi deja lance, navigation..."
    "${ADB_DEVICE[@]}" shell am start -n "${KIWI_ACTIVITY}" -d "${DASHBOARD_AUTH_URL}" -f 0x10000000 2>/dev/null
else
    log "Lancement Kiwi Browser..."
    "${ADB_DEVICE[@]}" shell am start -n "${KIWI_ACTIVITY}" -d "${DASHBOARD_AUTH_URL}" 2>/dev/null
    for i in $(seq 1 10); do
        sleep 1
        if "${ADB_DEVICE[@]}" shell pidof "${KIWI_PACKAGE}" 2>/dev/null | grep -q .; then break; fi
    done
fi
ok "Kiwi Browser lance"

# 5. CDP forward
log "Bridge CDP port ${CDP_LOCAL_PORT}..."
"${ADB_DEVICE[@]}" forward "tcp:${CDP_LOCAL_PORT}" localabstract:chrome_devtools_remote 2>/dev/null
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
