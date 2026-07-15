"""Contrat minimal Firebase Cloud Messaging HTTP v1."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


def _credentials(tmp_path):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode("ascii")
    path = tmp_path / "firebase.json"
    path.write_text(
        json.dumps(
            {
                "project_id": "jarvis-test",
                "client_email": "jarvis@example.test",
                "private_key": private_pem,
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        ),
        encoding="utf-8",
    )
    return path


def test_fcm_exchanges_oauth_token_and_sends_android_message(tmp_path, monkeypatch):
    import config
    from integrations import fcm

    monkeypatch.setattr(config, "FCM_SERVICE_ACCOUNT_FILE", str(_credentials(tmp_path)))
    monkeypatch.setattr(config, "FCM_PROJECT_ID", "")
    fcm.reset_token_cache()

    oauth = MagicMock()
    oauth.raise_for_status.return_value = None
    oauth.json.return_value = {"access_token": "access-token", "expires_in": 3600}
    sent = MagicMock(status_code=200, is_success=True)
    with patch("httpx.post", side_effect=[oauth, sent]) as post:
        ok, status = fcm.send_fcm_notification(
            "registration-token", "Alerte", "Action requise", "urgent"
        )

    assert (ok, status) == (True, 200)
    assertion = post.call_args_list[0].kwargs["data"]["assertion"]
    assert len(assertion.split(".")) == 3
    request = post.call_args_list[1]
    assert request.args[0].endswith("/projects/jarvis-test/messages:send")
    assert request.kwargs["headers"]["Authorization"] == "Bearer access-token"
    assert request.kwargs["json"]["message"]["android"]["priority"] == "HIGH"


def test_fcm_is_disabled_without_service_account(monkeypatch):
    import config
    from integrations.fcm import send_fcm_notification

    monkeypatch.setattr(config, "FCM_SERVICE_ACCOUNT_FILE", "")
    assert send_fcm_notification("token", "title", None, "medium") == (False, 0)
