"""Fiabilité de la base SQLite — sauvegardes, rétention, budget LLM.

Trois responsabilités, appelées par le scheduler et exposées en REST :

- ``run_backup()``    : snapshot cohérent via ``VACUUM INTO`` + rotation,
  chiffrement optionnel (``BACKUP_ENCRYPTION_ENABLED``).
- ``restore_backup()``: restauration (déchiffre si besoin) — prend d'abord
  un snapshot de sécurité de la base courante avant d'écraser quoi que ce soit.
- ``run_maintenance()``: purge des tables volumineuses selon la rétention
  configurée, optimisation FTS, checkpoint WAL, ``PRAGMA optimize``.
- ``check_llm_budget()``: alerte (table ``notifications``) quand la dépense
  LLM du mois franchit ``LLM_BUDGET_ALERT_PCT`` % puis 100 % du budget —
  une seule notification par seuil et par mois.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import sqlite3
import time
from datetime import datetime
from pathlib import Path

import config
from database import create_notification, get_connection, get_cost_summary, get_db

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════
# Sauvegardes
# ═══════════════════════════════════════════════════════════

def _backup_dir() -> Path:
    d = Path(config.BACKUP_DIR)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _derive_fernet_key(passphrase: str) -> bytes:
    """Dérive une clé Fernet (32 octets base64) d'une passphrase — déterministe."""
    digest = hashlib.sha256(passphrase.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def _encrypt_backup_file(path: Path) -> Path:
    """Chiffre `path` en place (Fernet/AES) et supprime le fichier en clair. Retourne le nouveau chemin."""
    from cryptography.fernet import Fernet

    key = _derive_fernet_key(config.BACKUP_ENCRYPTION_PASSPHRASE)
    token = Fernet(key).encrypt(path.read_bytes())
    enc_path = path.with_suffix(path.suffix + ".enc")
    enc_path.write_bytes(token)
    path.unlink()
    return enc_path


def _decrypt_backup_bytes(path: Path) -> bytes:
    from cryptography.fernet import Fernet

    key = _derive_fernet_key(config.BACKUP_ENCRYPTION_PASSPHRASE)
    return Fernet(key).decrypt(path.read_bytes())


def run_backup() -> dict:
    """Sauvegarde cohérente de la base (VACUUM INTO) puis rotation.

    ``VACUUM INTO`` produit un fichier compacté et transactionnellement
    cohérent même pendant que JARVIS écrit (mode WAL). Si
    ``BACKUP_ENCRYPTION_ENABLED``, le fichier est ensuite chiffré (Fernet,
    clé dérivée de ``BACKUP_ENCRYPTION_PASSPHRASE``) et l'original en clair
    supprimé. Retourne un rapport {ok, path, size_bytes, duration_s, removed, encrypted}.
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
        logger.error("[backup] VACUUM INTO : %s", e)
        return {"ok": False, "error": str(e)}
    finally:
        conn.close()

    encrypted = False
    if config.BACKUP_ENCRYPTION_ENABLED:
        if not config.BACKUP_ENCRYPTION_PASSPHRASE:
            logger.error(
                "[backup] BACKUP_ENCRYPTION_ENABLED=true mais BACKUP_ENCRYPTION_PASSPHRASE "
                "vide — sauvegarde laissée en clair"
            )
        else:
            dest = _encrypt_backup_file(dest)
            encrypted = True

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
            if not config.BACKUP_ENCRYPTION_PASSPHRASE:
                return {"ok": False, "error": "BACKUP_ENCRYPTION_PASSPHRASE requise pour déchiffrer"}
            data = _decrypt_backup_bytes(candidate)
        else:
            data = candidate.read_bytes()
    except Exception as e:
        logger.error("[restore] déchiffrement de %s : %s", name, e)
        return {"ok": False, "error": "Déchiffrement impossible (passphrase incorrecte ?)"}

    safety = run_backup()

    Path(config.DB_PATH).write_bytes(data)
    logger.warning("[restore] base restaurée depuis %s (snapshot de sécurité : %s)",
                    name, safety.get("path"))
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
    out = []
    for f in sorted(backup_dir.glob("jarvis-*.db*"),
                    key=lambda f: f.stat().st_mtime, reverse=True):
        st = f.stat()
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
    """Purge les tables volumineuses, optimise l'index FTS et le fichier WAL.

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
    ]
    with get_db() as conn:
        for table, days in rules:
            if days <= 0:
                continue
            cur = conn.execute(
                f"DELETE FROM {table} WHERE created_at < datetime('now', ?)",  # noqa: S608 — tables internes
                (f"-{int(days)} days",),
            )
            purged[table] = cur.rowcount
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
        "db_size_bytes": Path(config.DB_PATH).stat().st_size if Path(config.DB_PATH).exists() else 0,
    }
    logger.info("[maintenance] purge : %s", purged or "rien à purger")
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
    create_notification(source="system", title=title, content=content, priority=priority)
    logger.warning("[budget] %s — %s", title, content)
    return {"threshold": threshold, "spent": round(spent, 4), "budget": budget, "pct": round(pct, 1)}
