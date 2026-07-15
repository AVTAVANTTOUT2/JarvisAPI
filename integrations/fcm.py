"""Firebase Cloud Messaging HTTP v1 pour les téléphones Android appairés."""

from __future__ import annotations

import base64
import json
import logging
import threading
import time
from pathlib import Path

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

import config

logger = logging.getLogger(__name__)

_SCOPE = "https://www.googleapis.com/auth/firebase.messaging"
_token_lock = threading.Lock()
_cached_access_token = ""
_cached_access_token_expiry = 0.0


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _load_credentials() -> dict | None:
    filename = str(config.FCM_SERVICE_ACCOUNT_FILE or "").strip()
    if not filename:
        return None
    path = Path(filename).expanduser()
    if not path.is_absolute():
        path = config.BASE_DIR / path
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.warning("[fcm] compte de service illisible: %s", path)
        return None


def _service_account_assertion(credentials: dict, now: int) -> str:
    header = _b64url(b'{"alg":"RS256","typ":"JWT"}')
    claims = _b64url(
        json.dumps(
            {
                "iss": credentials["client_email"],
                "scope": _SCOPE,
                "aud": credentials.get("token_uri", "https://oauth2.googleapis.com/token"),
                "iat": now,
                "exp": now + 3600,
            },
            separators=(",", ":"),
        ).encode("utf-8")
    )
    signing_input = f"{header}.{claims}".encode("ascii")
    private_key = serialization.load_pem_private_key(
        credentials["private_key"].encode("utf-8"), password=None
    )
    signature = private_key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    return f"{header}.{claims}.{_b64url(signature)}"


def _access_token(credentials: dict) -> str:
    global _cached_access_token, _cached_access_token_expiry

    now = time.time()
    with _token_lock:
        if _cached_access_token and now < _cached_access_token_expiry - 60:
            return _cached_access_token

        assertion = _service_account_assertion(credentials, int(now))
        response = httpx.post(
            credentials.get("token_uri", "https://oauth2.googleapis.com/token"),
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                "assertion": assertion,
            },
            timeout=15.0,
        )
        response.raise_for_status()
        payload = response.json()
        _cached_access_token = str(payload["access_token"])
        _cached_access_token_expiry = now + int(payload.get("expires_in", 3600))
        return _cached_access_token


def send_fcm_notification(
    registration_token: str,
    title: str,
    content: str | None,
    priority: str,
) -> tuple[bool, int]:
    """Envoie une notification Android. Retourne ``(ok, status_http)``."""
    credentials = _load_credentials()
    if not credentials:
        return False, 0
    project_id = str(config.FCM_PROJECT_ID or credentials.get("project_id") or "").strip()
    if not project_id:
        logger.warning("[fcm] FCM_PROJECT_ID absent")
        return False, 0

    channel = "jarvis_urgent" if priority in {"urgent", "high"} else "jarvis_default"
    message = {
        "message": {
            "token": registration_token,
            "notification": {"title": title[:120], "body": (content or "")[:4000]},
            "data": {"priority": priority, "path": "/notifications"},
            "android": {
                "priority": "HIGH" if priority in {"urgent", "high"} else "NORMAL",
                "notification": {"channel_id": channel, "default_sound": True},
            },
        }
    }
    try:
        token = _access_token(credentials)
        response = httpx.post(
            f"https://fcm.googleapis.com/v1/projects/{project_id}/messages:send",
            headers={"Authorization": f"Bearer {token}"},
            json=message,
            timeout=15.0,
        )
        if response.status_code == 401:
            global _cached_access_token, _cached_access_token_expiry
            with _token_lock:
                _cached_access_token = ""
                _cached_access_token_expiry = 0.0
        return response.is_success, response.status_code
    except Exception:
        logger.debug("[fcm] envoi échoué", exc_info=True)
        return False, 0


def reset_token_cache() -> None:
    """Réinitialisation explicite, principalement utile aux tests et rotations de clé."""
    global _cached_access_token, _cached_access_token_expiry
    with _token_lock:
        _cached_access_token = ""
        _cached_access_token_expiry = 0.0
