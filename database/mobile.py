"""Pairage, authentification et push des téléphones JARVIS."""

from __future__ import annotations

import json

from .core import get_db


def create_mobile_pairing_code(code_hash: str, expires_at: str) -> None:
    with get_db() as conn:
        conn.execute("DELETE FROM mobile_pairing_codes WHERE used_at IS NOT NULL OR datetime(expires_at) <= datetime('now')")
        conn.execute(
            "INSERT INTO mobile_pairing_codes (code_hash, expires_at) VALUES (?, ?)",
            (code_hash, expires_at),
        )


def consume_mobile_pairing_code(code_hash: str) -> bool:
    """Consomme atomiquement un code encore valide."""
    with get_db() as conn:
        cursor = conn.execute(
            """UPDATE mobile_pairing_codes
               SET used_at = CURRENT_TIMESTAMP
               WHERE code_hash = ? AND used_at IS NULL
                 AND datetime(expires_at) > datetime('now')""",
            (code_hash,),
        )
        return cursor.rowcount == 1


def upsert_mobile_device(
    device_id: str,
    name: str,
    model: str,
    token_hash: str,
    app_version: str = "",
) -> dict:
    with get_db() as conn:
        conn.execute(
            """INSERT INTO mobile_devices
                   (device_id, name, model, token_hash, app_version, revoked)
               VALUES (?, ?, ?, ?, ?, 0)
               ON CONFLICT(device_id) DO UPDATE SET
                   name = excluded.name,
                   model = excluded.model,
                   token_hash = excluded.token_hash,
                   app_version = excluded.app_version,
                   paired_at = CURRENT_TIMESTAMP,
                   last_seen_at = CURRENT_TIMESTAMP,
                   revoked = 0""",
            (device_id, name, model, token_hash, app_version),
        )
        row = conn.execute("SELECT * FROM mobile_devices WHERE device_id = ?", (device_id,)).fetchone()
    return dict(row)


def get_mobile_device_by_token_hash(token_hash: str) -> dict | None:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM mobile_devices WHERE token_hash = ? AND revoked = 0",
            (token_hash,),
        ).fetchone()
    return dict(row) if row else None


def touch_mobile_device(device_id: str) -> None:
    with get_db() as conn:
        conn.execute(
            "UPDATE mobile_devices SET last_seen_at = CURRENT_TIMESTAMP WHERE device_id = ? AND revoked = 0",
            (device_id,),
        )


def update_mobile_push_token(device_id: str, fcm_token: str | None) -> bool:
    with get_db() as conn:
        cursor = conn.execute(
            """UPDATE mobile_devices
               SET fcm_token = ?, last_seen_at = CURRENT_TIMESTAMP
               WHERE device_id = ? AND revoked = 0""",
            (fcm_token, device_id),
        )
    return cursor.rowcount == 1


def clear_mobile_push_token(fcm_token: str) -> None:
    with get_db() as conn:
        conn.execute("UPDATE mobile_devices SET fcm_token = NULL WHERE fcm_token = ?", (fcm_token,))


def update_mobile_capabilities(device_id: str, capabilities: dict) -> bool:
    with get_db() as conn:
        cursor = conn.execute(
            "UPDATE mobile_devices SET capabilities_json = ? WHERE device_id = ? AND revoked = 0",
            (json.dumps(capabilities, ensure_ascii=False, sort_keys=True), device_id),
        )
    return cursor.rowcount == 1


def list_mobile_devices() -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            """SELECT id, device_id, name, model, app_version, paired_at,
                      last_seen_at, revoked, fcm_token IS NOT NULL AS push_enabled,
                      capabilities_json
               FROM mobile_devices ORDER BY last_seen_at DESC"""
        ).fetchall()
    return [dict(row) for row in rows]


def get_active_mobile_push_tokens() -> list[str]:
    with get_db() as conn:
        rows = conn.execute(
            """SELECT fcm_token FROM mobile_devices
               WHERE revoked = 0 AND fcm_token IS NOT NULL AND fcm_token != ''"""
        ).fetchall()
    return [str(row["fcm_token"]) for row in rows]


def revoke_mobile_device(device_id: str) -> bool:
    with get_db() as conn:
        cursor = conn.execute(
            """UPDATE mobile_devices
               SET revoked = 1, fcm_token = NULL, token_hash = NULL
               WHERE device_id = ? AND revoked = 0""",
            (device_id,),
        )
        conn.execute(
            "UPDATE sessions SET revoked = 1 WHERE mobile_device_id = ? AND revoked = 0",
            (device_id,),
        )
    return cursor.rowcount == 1


def get_mobile_chat_dedup(device_id: str, client_message_id: str) -> dict | None:
    """Retourne la réponse JSON mise en cache pour un client_message_id, ou None."""
    with get_db() as conn:
        row = conn.execute(
            """SELECT response_json FROM mobile_chat_dedup
               WHERE device_id = ? AND client_message_id = ?""",
            (device_id, client_message_id),
        ).fetchone()
    if not row:
        return None
    try:
        return json.loads(row["response_json"])
    except (TypeError, json.JSONDecodeError):
        return None


def save_mobile_chat_dedup(
    device_id: str,
    client_message_id: str,
    conversation_id: int,
    response: dict,
) -> None:
    """Enregistre la réponse pour rejeu idempotent (INSERT OR IGNORE)."""
    payload = json.dumps(response, ensure_ascii=False, default=str)
    with get_db() as conn:
        conn.execute(
            """INSERT OR IGNORE INTO mobile_chat_dedup
               (device_id, client_message_id, conversation_id, response_json)
               VALUES (?, ?, ?, ?)""",
            (device_id, client_message_id, conversation_id, payload),
        )
