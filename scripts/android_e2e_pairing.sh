#!/usr/bin/env bash
# Scénario reproductible : backend HTTPS + pairage mobile (sans secrets dans les logs).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
source venv/bin/activate

export WEB_HTTPS=true
export WEB_PORT="${WEB_PORT:-8081}"

bash scripts/verify_backend_https.sh

python - <<'PY'
import sys
from pathlib import Path
sys.path.insert(0, str(Path(".").resolve()))
import tempfile
import config
import database
from database import init_db
from tests.conftest import authenticate, TEST_AUTH_SECRET
import main
from fastapi.testclient import TestClient

db_path = Path(tempfile.mkdtemp()) / "pairing-e2e.db"
config.DB_PATH = str(db_path)
database.DB_PATH = db_path
config.WEB_HTTPS = False
init_db()

with TestClient(main.app) as client:
    authenticate(client)
    start = client.post("/api/mobile/pairing/start")
    assert start.status_code == 200, start.text
    code = start.json()["code"]
    complete = client.post(
        "/api/mobile/pairing/complete",
        json={"code": code, "device_id": "e2e-emulator", "name": "E2E Test"},
    )
    assert complete.status_code == 200, complete.text
    token = complete.json()["token"]
    assert len(token) > 20
    client.cookies.clear()
    session = client.post("/api/mobile/session", headers={"Authorization": f"Bearer {token}"})
    assert session.status_code == 200, session.text
    revoke = client.post("/api/mobile/devices/e2e-emulator/revoke")
    authenticate(client)
    assert revoke.status_code == 200, revoke.text
    denied = client.post("/api/mobile/session", headers={"Authorization": f"Bearer {token}"})
    assert denied.status_code == 401
print("Pairing E2E script OK (backend contract)")
PY

echo "Pour l'émulateur : adb install android/app/build/outputs/apk/debug/app-debug.apk"
echo "URL serveur émulateur : https://10.0.2.2:${WEB_PORT}"
