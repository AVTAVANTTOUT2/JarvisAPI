"""Verrouillage de l'application — PIN/passphrase, sessions, anti-brute-force.

Application mono-utilisateur : un seul secret (PIN ou passphrase) protège
tout. Tant qu'aucun secret n'a été configuré, l'application refuse de servir
la moindre donnée (fail-closed) — c'est l'inverse du comportement par défaut
précédent (tout ouvert, aucune authentification).

- Le secret n'est jamais stocké en clair : ``hashlib.scrypt`` (coûteux à
  bruteforcer, aucune dépendance externe) avec sel aléatoire par entrée.
- Les sessions sont des jetons opaques (``secrets.token_urlsafe``) ; seul
  leur hash SHA-256 est persisté (`sessions.token_hash`) — une fuite de la
  base ne permet pas de rejouer une session active.
- Verrou anti-brute-force global (simple, cohérent avec un usage
  mono-utilisateur) : après `config.AUTH_LOCKOUT_MAX_ATTEMPTS` échecs,
  blocage de `config.AUTH_LOCKOUT_MINUTES` minutes.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta

import config
from database import get_setting, set_setting

_SETTING_SECRET_HASH = "auth_secret_hash"
_SETTING_FAILED_ATTEMPTS = "auth_failed_attempts"
_SETTING_LOCKOUT_UNTIL = "auth_lockout_until"

_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1


def _hash_secret(secret: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(
        secret.encode("utf-8"), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=32
    )
    return f"{salt.hex()}${digest.hex()}"


def _verify_secret(secret: str, stored: str) -> bool:
    try:
        salt_hex, digest_hex = stored.split("$", 1)
    except ValueError:
        return False
    salt = bytes.fromhex(salt_hex)
    candidate = hashlib.scrypt(
        secret.encode("utf-8"), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=32
    )
    return hmac.compare_digest(candidate.hex(), digest_hex)


def is_configured() -> bool:
    """True si un PIN/passphrase a déjà été défini."""
    return bool(get_setting(_SETTING_SECRET_HASH, ""))


def setup_secret(secret: str) -> None:
    """Définit le secret initial. Refuse si déjà configuré (utiliser `change_secret`)."""
    if is_configured():
        raise ValueError("Un secret est déjà configuré — utilisez change_secret().")
    if not secret or len(secret) < 4:
        raise ValueError("Le secret doit contenir au moins 4 caractères.")
    set_setting(_SETTING_SECRET_HASH, _hash_secret(secret))


def change_secret(current: str, new: str) -> bool:
    """Change le secret si `current` est correct. Ne touche pas au verrou anti-brute-force."""
    if not is_configured() or not _verify_secret(current, get_setting(_SETTING_SECRET_HASH, "")):
        return False
    if not new or len(new) < 4:
        raise ValueError("Le nouveau secret doit contenir au moins 4 caractères.")
    set_setting(_SETTING_SECRET_HASH, _hash_secret(new))
    return True


# ── Anti-brute-force (verrou global) ──────────────────────────

def is_locked_out() -> tuple[bool, int]:
    """(verrouillé ?, secondes restantes)."""
    until = get_setting(_SETTING_LOCKOUT_UNTIL, "")
    if not until:
        return False, 0
    try:
        until_dt = datetime.fromisoformat(until)
    except ValueError:
        return False, 0
    remaining = (until_dt - datetime.now()).total_seconds()
    if remaining <= 0:
        return False, 0
    return True, int(remaining)


def record_failed_attempt() -> None:
    attempts = int(get_setting(_SETTING_FAILED_ATTEMPTS, "0") or "0") + 1
    set_setting(_SETTING_FAILED_ATTEMPTS, str(attempts))
    if attempts >= config.AUTH_LOCKOUT_MAX_ATTEMPTS:
        until = datetime.now() + timedelta(minutes=config.AUTH_LOCKOUT_MINUTES)
        set_setting(_SETTING_LOCKOUT_UNTIL, until.isoformat(timespec="seconds"))


def reset_failed_attempts() -> None:
    set_setting(_SETTING_FAILED_ATTEMPTS, "0")
    set_setting(_SETTING_LOCKOUT_UNTIL, "")


def verify_only(secret: str) -> bool:
    """Vérifie le secret sans créer de session (ré-authentification écran verrouillé).

    Toujours soumis au verrou anti-brute-force.
    """
    locked, _ = is_locked_out()
    if locked:
        return False
    if not is_configured():
        return False
    ok = _verify_secret(secret, get_setting(_SETTING_SECRET_HASH, ""))
    if ok:
        reset_failed_attempts()
    else:
        record_failed_attempt()
    return ok


# ── Sessions ───────────────────────────────────────────────────

def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _session_expiry() -> datetime:
    return datetime.now() + timedelta(days=config.SESSION_INACTIVITY_DAYS)


def create_session(
    user_agent: str = "", ip: str = "", mobile_device_id: str | None = None
) -> tuple[str, datetime]:
    """Crée une session et retourne (jeton brut — à ne renvoyer qu'une fois, expiration)."""
    from database import create_session_row

    token = secrets.token_urlsafe(32)
    expires_at = _session_expiry()
    create_session_row(
        hash_token(token),
        expires_at.isoformat(timespec="seconds"),
        user_agent,
        ip,
        mobile_device_id,
    )
    return token, expires_at


def verify_mobile_token(token: str | None) -> dict | None:
    """Valide le jeton natif d'un téléphone et actualise sa dernière activité."""
    if not token:
        return None
    from database import get_mobile_device_by_token_hash, touch_mobile_device

    device = get_mobile_device_by_token_hash(hash_token(token))
    if device:
        touch_mobile_device(str(device["device_id"]))
    return device


def verify_session(token: str | None) -> dict | None:
    """Vérifie un jeton de session. Fait glisser l'expiration (inactivité) si valide.

    Retourne None si absent, invalide, révoqué, ou expiré (absolu ou inactivité).
    """
    if not token:
        return None
    from database import get_session_by_token_hash, touch_session

    token_hash = hash_token(token)
    row = get_session_by_token_hash(token_hash)
    if not row:
        return None

    now = datetime.now()
    try:
        expires_at = datetime.fromisoformat(row["expires_at"])
        created_at = datetime.fromisoformat(row["created_at"])
    except (ValueError, TypeError):
        return None

    if now >= expires_at:
        return None
    if now - created_at > timedelta(days=config.SESSION_MAX_AGE_DAYS):
        return None

    new_expiry = min(_session_expiry(), created_at + timedelta(days=config.SESSION_MAX_AGE_DAYS))
    touch_session(token_hash, new_expiry.isoformat(timespec="seconds"))
    return row


def revoke_session(token: str) -> bool:
    from database import revoke_session_by_token_hash

    return revoke_session_by_token_hash(hash_token(token))
