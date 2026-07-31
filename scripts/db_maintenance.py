"""Fiabilité de la base SQLite — sauvegardes, rétention, budget LLM.

Trois responsabilités, appelées par le scheduler et exposées en REST :

- ``run_backup()``    : snapshot cohérent via ``VACUUM INTO`` + rotation,
  chiffrement Fernet versionné activé par défaut.
- ``restore_backup()``: restauration (déchiffre si besoin) — prend d'abord
  un snapshot de sécurité de la base courante avant d'écraser quoi que ce soit.
- ``run_maintenance()``: purge des tables volumineuses et des uploads orphelins
  selon la rétention configurée, optimisation FTS, checkpoint WAL,
  ``PRAGMA optimize``.
- ``check_llm_budget()``: alerte (table ``notifications``) quand la dépense
  LLM du mois franchit ``LLM_BUDGET_ALERT_PCT`` % puis 100 % du budget —
  une seule notification par seuil et par mois.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import secrets
import sqlite3
import time
from datetime import datetime
from pathlib import Path

import config
from database import get_connection, get_cost_summary, get_db
from database.core import harden_sqlite_permissions
from core.file_security import (
    ensure_private_directory,
    ensure_private_file,
    write_private_bytes,
)
from jarvis.notification_service import notification_service

logger = logging.getLogger(__name__)

_BACKUP_V2_MAGIC = b"JARVIS-BACKUP-V2\x00"
_BACKUP_V2_SALT_BYTES = 16
_BACKUP_KDF_ITERATIONS = 600_000


# ═══════════════════════════════════════════════════════════
# Sauvegardes
# ═══════════════════════════════════════════════════════════

def _backup_dir() -> Path:
    d = Path(config.BACKUP_DIR)
    return ensure_private_directory(d)


def harden_backup_permissions() -> None:
    """Force les permissions privées des sauvegardes et de leur clé locale."""
    backup_dir = _backup_dir()
    for backup in backup_dir.glob("jarvis-*.db*"):
        if backup.is_file():
            ensure_private_file(backup)
    key_path = _backup_key_file()
    if key_path.exists():
        ensure_private_file(key_path)


def _backup_key_file() -> Path:
    path = Path(config.BACKUP_ENCRYPTION_KEY_FILE).expanduser()
    return path if path.is_absolute() else config.BASE_DIR / path


def _read_local_backup_secret(*, create: bool) -> str | None:
    """Lit ou crée la clé locale de secours, toujours en permissions 0600."""
    key_path = _backup_key_file()
    if key_path.exists():
        ensure_private_file(key_path)
        secret = key_path.read_text(encoding="utf-8").strip()
        if not secret:
            raise RuntimeError(f"clé de sauvegarde vide : {key_path}")
        return secret
    if not create:
        return None

    secret = secrets.token_urlsafe(48)
    try:
        write_private_bytes(key_path, f"{secret}\n".encode("utf-8"), exclusive=True)
    except FileExistsError:
        return _read_local_backup_secret(create=False)
    return secret


def _backup_secret_candidates(*, create_key: bool) -> list[str]:
    """Retourne la passphrase explicite puis la clé locale existante."""
    candidates: list[str] = []
    passphrase = config.BACKUP_ENCRYPTION_PASSPHRASE.strip()
    if passphrase:
        candidates.append(passphrase)
    try:
        local_secret = _read_local_backup_secret(create=create_key and not candidates)
    except Exception:
        if not candidates:
            raise
        logger.warning(
            "[backup] clé locale illisible, passphrase explicite utilisée",
            exc_info=True,
        )
        local_secret = None
    if local_secret and local_secret not in candidates:
        candidates.append(local_secret)
    return candidates


def _derive_legacy_fernet_key(secret: str) -> bytes:
    """KDF historique, conservée uniquement pour restaurer les anciens backups."""
    digest = hashlib.sha256(secret.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def _derive_v2_fernet_key(secret: str, salt: bytes) -> bytes:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=_BACKUP_KDF_ITERATIONS,
    )
    return base64.urlsafe_b64encode(kdf.derive(secret.encode("utf-8")))


def _encrypt_backup_file(path: Path) -> Path:
    """Chiffre en V2 salé puis supprime le snapshot SQLite en clair."""
    from cryptography.fernet import Fernet

    candidates = _backup_secret_candidates(create_key=True)
    if not candidates:
        raise RuntimeError("aucune clé de chiffrement disponible")
    salt = os.urandom(_BACKUP_V2_SALT_BYTES)
    key = _derive_v2_fernet_key(candidates[0], salt)
    token = Fernet(key).encrypt(path.read_bytes())
    enc_path = path.with_suffix(path.suffix + ".enc")
    write_private_bytes(
        enc_path,
        _BACKUP_V2_MAGIC + salt + token,
        exclusive=True,
    )
    path.unlink()
    return enc_path


def _decrypt_backup_bytes(path: Path) -> bytes:
    from cryptography.fernet import Fernet, InvalidToken

    payload = path.read_bytes()
    candidates = _backup_secret_candidates(create_key=False)
    if not candidates:
        raise RuntimeError("aucune clé de déchiffrement disponible")

    if payload.startswith(_BACKUP_V2_MAGIC):
        offset = len(_BACKUP_V2_MAGIC)
        salt = payload[offset : offset + _BACKUP_V2_SALT_BYTES]
        token = payload[offset + _BACKUP_V2_SALT_BYTES :]
        if len(salt) != _BACKUP_V2_SALT_BYTES or not token:
            raise ValueError("enveloppe de sauvegarde V2 invalide")
        for secret in candidates:
            try:
                return Fernet(_derive_v2_fernet_key(secret, salt)).decrypt(token)
            except InvalidToken:
                continue
    else:
        # Compatibilité avec le format Fernet historique sans en-tête ni sel.
        for secret in candidates:
            try:
                return Fernet(_derive_legacy_fernet_key(secret)).decrypt(payload)
            except InvalidToken:
                continue
    raise InvalidToken


def _validated_restore_source(data: bytes, backup_dir: Path) -> Path:
    """Écrit une image SQLite privée temporaire et vérifie son intégrité."""
    source_path = backup_dir / f".restore-{secrets.token_hex(16)}.db"
    write_private_bytes(source_path, data, exclusive=True)
    try:
        connection = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)
        try:
            result = connection.execute("PRAGMA integrity_check").fetchone()
        finally:
            connection.close()
        if not result or result[0] != "ok":
            raise ValueError("intégrité SQLite invalide")
        return source_path
    except Exception:
        source_path.unlink(missing_ok=True)
        raise


def run_backup() -> dict:
    """Sauvegarde cohérente de la base (VACUUM INTO) puis rotation.

    ``VACUUM INTO`` produit un fichier compacté et transactionnellement
    cohérent même pendant que JARVIS écrit (mode WAL). Si
    ``BACKUP_ENCRYPTION_ENABLED``, le fichier est ensuite chiffré et l'original
    en clair supprimé. Sans passphrase explicite, une clé locale 0600 est créée.
    Retourne un rapport {ok, path, size_bytes, duration_s, removed, encrypted}.
    """
    src = Path(config.DB_PATH)
    if not src.exists():
        return {"ok": False, "error": f"base introuvable : {src}"}

    backup_dir = _backup_dir()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")

    def _candidate_taken(p: Path) -> bool:
        # Le nom final peut devenir `.db.enc` après chiffrement — il faut
        # vérifier les deux variantes pour éviter d'écraser une sauvegarde
        # existante prise à la même seconde.
        return p.exists() or p.with_suffix(p.suffix + ".enc").exists()

    dest = backup_dir / f"jarvis-{stamp}.db"
    n = 1
    while _candidate_taken(dest):
        dest = backup_dir / f"jarvis-{stamp}-{n}.db"
        n += 1

    t0 = time.monotonic()
    conn = get_connection()
    try:
        conn.execute("VACUUM INTO ?", (str(dest),))
    except sqlite3.Error as e:
        dest.unlink(missing_ok=True)
        logger.error("[backup] VACUUM INTO : %s", e)
        return {"ok": False, "error": str(e)}
    finally:
        conn.close()
    try:
        ensure_private_file(dest)
    except Exception as exc:
        dest.unlink(missing_ok=True)
        logger.error("[backup] permissions privées impossibles : %s", exc)
        return {"ok": False, "error": "Permissions privées de sauvegarde impossibles"}

    encrypted = False
    if config.BACKUP_ENCRYPTION_ENABLED:
        try:
            dest = _encrypt_backup_file(dest)
            encrypted = True
        except Exception as exc:
            dest.unlink(missing_ok=True)
            dest.with_suffix(dest.suffix + ".enc").unlink(missing_ok=True)
            logger.error("[backup] chiffrement obligatoire impossible : %s", exc)
            return {
                "ok": False,
                "error": "Chiffrement de la sauvegarde impossible",
            }
    else:
        try:
            ensure_private_file(dest)
        except Exception as exc:
            dest.unlink(missing_ok=True)
            logger.error("[backup] permissions privées impossibles : %s", exc)
            return {"ok": False, "error": "Permissions privées de sauvegarde impossibles"}

    removed = _rotate_backups(backup_dir)
    report = {
        "ok": True,
        "path": str(dest),
        "size_bytes": dest.stat().st_size,
        "duration_s": round(time.monotonic() - t0, 2),
        "removed": removed,
        "encrypted": encrypted,
    }
    logger.info(
        "[backup] %s (%.1f Mo, %.2fs, chiffré=%s, rotation: %d supprimée(s))",
        dest.name, report["size_bytes"] / 1e6, report["duration_s"], encrypted, len(removed),
    )
    return report


def restore_backup(name: str) -> dict:
    """Restaure une sauvegarde (déchiffre si `.enc`) en écrasant la base courante.

    Sécurité : `name` doit être un simple nom de fichier dans `BACKUP_DIR`
    (aucun `..`/chemin absolu accepté) ; un snapshot de sécurité de la base
    courante est pris via `run_backup()` avant toute écrasement.
    """
    backup_dir = _backup_dir().resolve()
    candidate = (backup_dir / name).resolve()
    if candidate.parent != backup_dir or not candidate.is_file():
        return {"ok": False, "error": "Sauvegarde introuvable"}

    # Lire (et déchiffrer) la sauvegarde cible AVANT de prendre le snapshot de
    # sécurité — sinon un nom de fichier généré à la même seconde peut écraser
    # `candidate` (VACUUM INTO + chiffrement partagent le même horodatage).
    try:
        if candidate.suffix == ".enc":
            data = _decrypt_backup_bytes(candidate)
        else:
            data = candidate.read_bytes()
    except Exception as e:
        logger.error("[restore] déchiffrement de %s : %s", name, e)
        return {"ok": False, "error": "Déchiffrement impossible (passphrase incorrecte ?)"}

    try:
        source_path = _validated_restore_source(data, backup_dir)
    except Exception as exc:
        logger.error("[restore] image SQLite invalide %s : %s", name, exc)
        return {"ok": False, "error": "Sauvegarde SQLite invalide"}

    try:
        safety = run_backup()
        if not safety.get("ok"):
            return {
                "ok": False,
                "error": f"Snapshot de sécurité impossible : {safety.get('error')}",
            }

        source: sqlite3.Connection | None = None
        destination: sqlite3.Connection | None = None
        try:
            source = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)
            destination = get_connection()
            source.backup(destination)
            destination.commit()
        finally:
            if destination is not None:
                destination.close()
            if source is not None:
                source.close()
        harden_sqlite_permissions()
    except Exception as exc:
        logger.error("[restore] restauration SQLite de %s : %s", name, exc)
        return {"ok": False, "error": "Restauration SQLite impossible"}
    finally:
        source_path.unlink(missing_ok=True)

    logger.warning(
        "[restore] base restaurée depuis %s (snapshot de sécurité : %s)",
        name,
        safety.get("path"),
    )
    return {"ok": True, "restored_from": name, "safety_backup": safety.get("path")}


def _rotate_backups(backup_dir: Path, keep: int | None = None) -> list[str]:
    """Ne conserve que les ``keep`` sauvegardes les plus récentes (par mtime)."""
    keep = config.BACKUP_KEEP if keep is None else keep
    if keep <= 0:
        return []
    files = sorted(backup_dir.glob("jarvis-*.db*"), key=lambda f: f.stat().st_mtime)
    removed: list[str] = []
    for f in files[:-keep] if len(files) > keep else []:
        try:
            f.unlink()
            removed.append(f.name)
        except OSError as e:
            logger.warning("[backup] rotation %s : %s", f.name, e)
    return removed


def list_backups() -> list[dict]:
    """Sauvegardes présentes, plus récente en premier."""
    backup_dir = Path(config.BACKUP_DIR)
    if not backup_dir.is_dir():
        return []
    ensure_private_directory(backup_dir)
    out = []
    for f in sorted(backup_dir.glob("jarvis-*.db*"),
                    key=lambda f: f.stat().st_mtime, reverse=True):
        st = f.stat()
        ensure_private_file(f)
        out.append({
            "name": f.name,
            "size_bytes": st.st_size,
            "created_at": datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"),
            "encrypted": f.suffix == ".enc",
        })
    return out


# ═══════════════════════════════════════════════════════════
# Maintenance / rétention
# ═══════════════════════════════════════════════════════════

def run_maintenance() -> dict:
    """Purge les tables/fichiers volumineux, optimise l'index FTS et le WAL.

    La rétention vient de la config (0 = conserver indéfiniment). Les
    notifications ne sont purgées que si elles sont **lues**. ``created_at``
    est en UTC (DEFAULT CURRENT_TIMESTAMP), comparé à ``datetime('now')``
    (UTC aussi) — cohérent.
    """
    purged: dict[str, int] = {}
    rules = [
        ("screen_activity", config.RETENTION_SCREEN_DAYS),
        ("location_history", config.RETENTION_LOCATION_DAYS),
        ("llm_action_logs", config.RETENTION_LLM_LOGS_DAYS),
        ("dev_loop_log", config.RETENTION_LLM_LOGS_DAYS),
    ]
    referenced_uploads: set[str] = set()
    with get_db() as conn:
        for table, days in rules:
            if days <= 0:
                continue
            cur = conn.execute(
                f"DELETE FROM {table} WHERE created_at < datetime('now', ?)",  # noqa: S608 — tables internes
                (f"-{int(days)} days",),
            )
            purged[table] = cur.rowcount
        from database.scheduler_runs import purge_scheduler_runs

        purged["scheduler_job_runs"] = purge_scheduler_runs(
            int(config.RETENTION_SCHEDULER_RUNS_DAYS)
        )
        if config.RETENTION_NOTIF_READ_DAYS > 0:
            cur = conn.execute(
                "DELETE FROM notifications WHERE read = 1 AND created_at < datetime('now', ?)",
                (f"-{int(config.RETENTION_NOTIF_READ_DAYS)} days",),
            )
            purged["notifications_read"] = cur.rowcount
        try:
            conn.execute("INSERT INTO messages_fts(messages_fts) VALUES ('optimize')")
        except sqlite3.OperationalError:
            pass  # FTS5 absent — le fallback LIKE est déjà en place
        for table in ("conversation_documents", "school_documents"):
            referenced_uploads.update(
                str(row["file_path"])
                for row in conn.execute(
                    f"SELECT file_path FROM {table} WHERE file_path IS NOT NULL AND file_path != ''"
                ).fetchall()
            )

    from jarvis.uploads import purge_orphan_uploads

    upload_orphans = purge_orphan_uploads(referenced_uploads)

    # Hors transaction : compacte le WAL et rafraîchit les stats du planner.
    conn2 = get_connection()
    try:
        conn2.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn2.execute("PRAGMA optimize")
    finally:
        conn2.close()

    report = {
        "ok": True,
        "purged": purged,
        "upload_orphans": upload_orphans,
        "db_size_bytes": Path(config.DB_PATH).stat().st_size if Path(config.DB_PATH).exists() else 0,
    }
    logger.info(
        "[maintenance] purge DB : %s ; uploads orphelins : %s",
        purged or "rien à purger",
        upload_orphans,
    )
    return report


# ═══════════════════════════════════════════════════════════
# Budget LLM
# ═══════════════════════════════════════════════════════════

def check_llm_budget() -> dict | None:
    """Alerte si la dépense LLM du mois franchit un seuil du budget.

    Seuils : ``LLM_BUDGET_ALERT_PCT`` % (priorité medium) puis 100 %
    (priorité high). Dédoublonnage par titre : une notification par seuil
    et par mois civil. Retourne le rapport si une alerte est créée, sinon None.
    """
    budget = config.LLM_BUDGET_MONTHLY
    if budget <= 0:
        return None

    summary = get_cost_summary()
    spent = float(summary["month"]["cost"])
    pct = spent / budget * 100

    if pct >= 100:
        threshold, priority = 100, "high"
    elif pct >= config.LLM_BUDGET_ALERT_PCT:
        threshold, priority = config.LLM_BUDGET_ALERT_PCT, "medium"
    else:
        return None

    month_key = datetime.now().strftime("%Y-%m")
    title = f"Budget LLM {threshold}% — {month_key}"
    with get_db() as conn:
        exists = conn.execute(
            "SELECT 1 FROM notifications WHERE title = ? LIMIT 1", (title,)
        ).fetchone()
    if exists:
        return None

    content = (
        f"{spent:.2f}$ dépensés sur un budget de {budget:.2f}$ ce mois-ci "
        f"({pct:.0f} %)."
    )
    notification_service.create(source="system", title=title, content=content, priority=priority)
    logger.warning("[budget] %s — %s", title, content)
    return {"threshold": threshold, "spent": round(spent, 4), "budget": budget, "pct": round(pct, 1)}
