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
- Le débit des vérifications est limité par client (identifiant haché, jamais
  l'IP brute), avec délai progressif puis verrouillage temporaire. Un plafond
  global secondaire, volontairement bien plus haut, freine les attaques
  distribuées sans permettre à un seul client de bloquer tous les autres.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import math
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import config
from database import get_setting, set_setting

_SETTING_SECRET_HASH = "auth_secret_hash"
_GLOBAL_RATE_KEY = "__global__"

_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RateLimitStatus:
    """État public minimal d'un verrou anti-brute-force."""

    blocked: bool = False
    retry_after: int = 0
    scope: str | None = None
    hard: bool = False


def _hash_secret(secret: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(
        secret.encode("utf-8"), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=32
    )
    return f"{salt.hex()}${digest.hex()}"


def _verify_secret(secret: str, stored: str) -> bool:
    try:
        salt_hex, digest_hex = stored.split("$", 1)
        salt = bytes.fromhex(salt_hex)
    except (TypeError, ValueError):
        return False
    candidate = hashlib.scrypt(
        secret.encode("utf-8"), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=32
    )
    return hmac.compare_digest(candidate.hex(), digest_hex)


def is_configured() -> bool:
    """True si un PIN/passphrase a déjà été défini."""
    return bool(get_setting(_SETTING_SECRET_HASH, ""))


def validate_secret_strength(secret: str) -> None:
    """Applique la politique : PIN de 6 chiffres ou passphrase de 10 caractères."""
    if not secret:
        raise ValueError("Le secret est requis.")
    if secret.isascii() and secret.isdigit():
        if len(secret) < 6:
            raise ValueError("Le PIN doit contenir au moins 6 chiffres.")
        return
    if len(secret) < 10:
        raise ValueError("La passphrase doit contenir au moins 10 caractères.")


def setup_secret(secret: str) -> None:
    """Définit le secret initial. Refuse si déjà configuré (utiliser `change_secret`)."""
    if is_configured():
        raise ValueError("Un secret est déjà configuré — utilisez change_secret().")
    validate_secret_strength(secret)
    set_setting(_SETTING_SECRET_HASH, _hash_secret(secret))


def change_secret(current: str, new: str, client_key: str | None = None) -> bool:
    """Change le secret si ``current`` est correct, avec le verrou anti-bruteforce."""
    if not verify_only(current, client_key=client_key, channel="web"):
        return False
    validate_secret_strength(new)
    set_setting(_SETTING_SECRET_HASH, _hash_secret(new))
    return True


# ── Anti-brute-force par client + plafond global ──────────────

def client_rate_key(identifier: str, channel: str = "web") -> str:
    """Retourne une empreinte stable sans persister l'identifiant client brut."""
    normalized_channel = (channel or "unknown").strip().lower()
    normalized_identifier = (identifier or "unknown").strip()
    value = f"jarvis-auth:{normalized_channel}:{normalized_identifier}"
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _default_client_key() -> str:
    return client_rate_key("internal", channel="local")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _row_status(
    row: dict | None,
    *,
    scope: str,
    hard_threshold: int,
    now: datetime,
) -> RateLimitStatus:
    if not row:
        return RateLimitStatus()
    blocked_until = _parse_datetime(row.get("blocked_until"))
    if not blocked_until or blocked_until <= now:
        return RateLimitStatus()
    remaining = max(1, math.ceil((blocked_until - now).total_seconds()))
    attempts = max(0, int(row.get("failed_attempts") or 0))
    return RateLimitStatus(
        blocked=True,
        retry_after=remaining,
        scope=scope,
        hard=attempts >= max(1, hard_threshold),
    )


def rate_limit_status(
    client_key: str | None = None,
    *,
    hard_only: bool = False,
    include_global: bool = True,
    now: datetime | None = None,
) -> RateLimitStatus:
    """Retourne le verrou actif pour ce client, puis le plafond global."""
    from database import get_auth_rate_limit

    now = now or _utc_now()
    key = client_key or _default_client_key()
    client_status = _row_status(
        get_auth_rate_limit(key),
        scope="client",
        hard_threshold=config.AUTH_LOCKOUT_MAX_ATTEMPTS,
        now=now,
    )
    statuses = [client_status]
    if include_global:
        statuses.insert(
            0,
            _row_status(
                get_auth_rate_limit(_GLOBAL_RATE_KEY),
                scope="global",
                hard_threshold=config.AUTH_GLOBAL_MAX_ATTEMPTS,
                now=now,
            ),
        )
    for status in statuses:
        if status.blocked and (status.hard or not hard_only):
            return status
    return RateLimitStatus()


def is_locked_out(client_key: str | None = None) -> tuple[bool, int]:
    """Compatibilité : retourne ``(verrouillé, secondes restantes)``."""
    status = rate_limit_status(client_key)
    return status.blocked, status.retry_after


def _active_attempts(
    row: dict | None,
    now: datetime,
    *,
    hard_threshold: int | None = None,
) -> tuple[int, datetime]:
    window_start = _parse_datetime(row.get("window_started_at")) if row else None
    window = timedelta(minutes=max(1, config.AUTH_RATE_WINDOW_MINUTES))
    if window_start is None or now - window_start >= window:
        return 0, now
    attempts = max(0, int(row.get("failed_attempts") or 0))
    blocked_until = _parse_datetime(row.get("blocked_until")) if row else None
    if (
        hard_threshold is not None
        and attempts >= max(1, hard_threshold)
        and (blocked_until is None or blocked_until <= now)
    ):
        return 0, now
    return attempts, window_start


def _next_failure(
    row: dict | None,
    *,
    now: datetime,
    threshold: int,
    hard_minutes: int,
    progressive: bool,
) -> tuple[int, datetime, datetime]:
    attempts, window_start = _active_attempts(
        row,
        now,
        hard_threshold=threshold,
    )
    attempts += 1
    if attempts >= max(1, threshold):
        blocked_until = now + timedelta(minutes=max(1, hard_minutes))
    elif progressive and config.AUTH_PROGRESSIVE_DELAY_SECONDS > 0:
        exponent = max(0, attempts - 1)
        delay = min(
            max(0, config.AUTH_PROGRESSIVE_DELAY_MAX_SECONDS),
            config.AUTH_PROGRESSIVE_DELAY_SECONDS * (2**exponent),
        )
        blocked_until = now + timedelta(seconds=max(0, delay))
    else:
        blocked_until = now
    return attempts, window_start, blocked_until


def _audit_failed_attempt(
    client_key: str,
    channel: str,
    attempts: int,
    *,
    client_hard_lock: bool,
    global_hard_lock: bool,
) -> None:
    """Journalise sans secret ni IP et alerte seulement au passage en verrou dur."""
    try:
        from database import log_llm_action

        log_llm_action(
            "auth",
            "auth_failed",
            {
                "client_fingerprint": client_key[:12],
                "channel": channel,
                "attempt": attempts,
            },
            "error",
        )
    except Exception:
        logger.warning("Impossible de journaliser l'échec d'authentification", exc_info=True)

    if not (client_hard_lock or global_hard_lock):
        return
    try:
        from jarvis.notification_service import notification_service

        scope = "globale" if global_hard_lock else "client"
        notification_service.create(
            "auth",
            "Tentatives de déverrouillage bloquées",
            f"Protection {scope} activée après des échecs répétés.",
            "high",
        )
    except Exception:
        logger.warning("Impossible de notifier le verrou d'authentification", exc_info=True)


def record_failed_attempt(
    client_key: str | None = None,
    *,
    channel: str = "local",
    now: datetime | None = None,
) -> None:
    """Enregistre atomiquement un échec client et l'agrégat global."""
    from database import get_db

    deterministic_now = now is not None
    now = now or _utc_now()
    key = client_key or _default_client_key()
    now_text = now.isoformat(timespec="seconds")
    client_hard_lock = False
    global_hard_lock = False

    with get_db() as conn:
        # Empêche deux requêtes simultanées de perdre un incrément.
        conn.execute("BEGIN IMMEDIATE")
        stale_before = now - timedelta(days=1)
        conn.execute(
            "DELETE FROM auth_rate_limits WHERE updated_at < ?",
            (stale_before.isoformat(timespec="seconds"),),
        )

        def _read(rate_key: str) -> dict | None:
            row = conn.execute(
                "SELECT * FROM auth_rate_limits WHERE client_key = ?",
                (rate_key,),
            ).fetchone()
            return dict(row) if row else None

        def _write(
            rate_key: str,
            attempts: int,
            window_start: datetime,
            blocked_until: datetime,
        ) -> None:
            conn.execute(
                """
                INSERT INTO auth_rate_limits (
                    client_key, failed_attempts, window_started_at,
                    blocked_until, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(client_key) DO UPDATE SET
                    failed_attempts = excluded.failed_attempts,
                    window_started_at = excluded.window_started_at,
                    blocked_until = excluded.blocked_until,
                    updated_at = excluded.updated_at
                """,
                (
                    rate_key,
                    attempts,
                    window_start.isoformat(timespec="seconds"),
                    blocked_until.isoformat(timespec="seconds"),
                    now_text,
                ),
            )

        client_row = _read(key)
        previous_client_attempts, _ = _active_attempts(
            client_row,
            now,
            hard_threshold=config.AUTH_LOCKOUT_MAX_ATTEMPTS,
        )
        client_attempts, client_window, client_until = _next_failure(
            client_row,
            now=now,
            threshold=config.AUTH_LOCKOUT_MAX_ATTEMPTS,
            hard_minutes=config.AUTH_LOCKOUT_MINUTES,
            progressive=True,
        )
        client_hard_lock = (
            previous_client_attempts < max(1, config.AUTH_LOCKOUT_MAX_ATTEMPTS)
            <= client_attempts
        )
        _write(key, client_attempts, client_window, client_until)

        global_row = _read(_GLOBAL_RATE_KEY)
        previous_global_attempts, _ = _active_attempts(
            global_row,
            now,
            hard_threshold=config.AUTH_GLOBAL_MAX_ATTEMPTS,
        )
        global_attempts, global_window, global_until = _next_failure(
            global_row,
            now=now,
            threshold=config.AUTH_GLOBAL_MAX_ATTEMPTS,
            hard_minutes=config.AUTH_GLOBAL_LOCKOUT_MINUTES,
            progressive=False,
        )
        global_hard_lock = (
            previous_global_attempts < max(1, config.AUTH_GLOBAL_MAX_ATTEMPTS)
            <= global_attempts
        )
        _write(_GLOBAL_RATE_KEY, global_attempts, global_window, global_until)

    _audit_failed_attempt(
        key,
        channel,
        client_attempts,
        client_hard_lock=client_hard_lock,
        global_hard_lock=global_hard_lock,
    )

    if deterministic_now:
        return

    # Le coût du journal/notification ne doit pas consommer le délai avant
    # même que le client reçoive sa réponse. L'UPDATE conditionnel évite
    # d'écraser une tentative ou un verrou plus récent arrivé en parallèle.
    finished_at = _utc_now()
    extensions = (
        (key, client_attempts, client_until),
        (_GLOBAL_RATE_KEY, global_attempts, global_until),
    )
    with get_db() as conn:
        for rate_key, attempts, original_until in extensions:
            duration = original_until - now
            if duration.total_seconds() <= 0:
                continue
            conn.execute(
                """
                UPDATE auth_rate_limits
                SET blocked_until = ?, updated_at = ?
                WHERE client_key = ?
                  AND failed_attempts = ?
                  AND blocked_until = ?
                """,
                (
                    (finished_at + duration).isoformat(timespec="seconds"),
                    finished_at.isoformat(timespec="seconds"),
                    rate_key,
                    attempts,
                    original_until.isoformat(timespec="seconds"),
                ),
            )


def reset_failed_attempts(client_key: str | None = None) -> None:
    """Efface uniquement le compteur du client authentifié."""
    from database import clear_auth_rate_limit

    clear_auth_rate_limit(client_key or _default_client_key())


def clear_all_rate_limits() -> int:
    """Récupération locale : efface les verrous client et global."""
    from database import clear_all_auth_rate_limits

    return clear_all_auth_rate_limits()


def verify_recovery_secret(secret: str) -> bool:
    """Vérification directe réservée à la route de récupération locale."""
    if not is_configured():
        return False
    return _verify_secret(secret, get_setting(_SETTING_SECRET_HASH, ""))


def verify_only(
    secret: str,
    client_key: str | None = None,
    *,
    channel: str = "local",
) -> bool:
    """Vérifie le secret sans créer de session (ré-authentification écran verrouillé).

    Les appels HTTP contrôlent le délai progressif avant d'appeler cette
    fonction. Ici, un verrou dur reste toujours bloquant afin que les appels
    internes ne puissent pas le contourner accidentellement.
    """
    key = client_key or _default_client_key()
    if rate_limit_status(key, hard_only=True).blocked:
        return False
    if not is_configured():
        return False
    ok = _verify_secret(secret, get_setting(_SETTING_SECRET_HASH, ""))
    if ok:
        reset_failed_attempts(key)
    else:
        record_failed_attempt(key, channel=channel)
    return ok


# ── Sessions ───────────────────────────────────────────────────

def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def csrf_token_for_session(token: str) -> str:
    """Jeton CSRF non réversible lié au jeton de session httpOnly."""
    return hashlib.sha256(f"jarvis-csrf:{token}".encode("utf-8")).hexdigest()


def verify_csrf_token(session_token: str | None, csrf_token: str | None) -> bool:
    if not session_token or not csrf_token:
        return False
    expected = csrf_token_for_session(session_token)
    return hmac.compare_digest(expected, csrf_token)


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
