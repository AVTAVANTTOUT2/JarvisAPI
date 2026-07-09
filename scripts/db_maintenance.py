"""Fiabilité de la base SQLite — sauvegardes, rétention, budget LLM.

Trois responsabilités, appelées par le scheduler et exposées en REST :

- ``run_backup()``    : snapshot cohérent via ``VACUUM INTO`` + rotation.
- ``run_maintenance()``: purge des tables volumineuses selon la rétention
  configurée, optimisation FTS, checkpoint WAL, ``PRAGMA optimize``.
- ``check_llm_budget()``: alerte (table ``notifications``) quand la dépense
  LLM du mois franchit ``LLM_BUDGET_ALERT_PCT`` % puis 100 % du budget —
  une seule notification par seuil et par mois.
"""

from __future__ import annotations

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


def run_backup() -> dict:
    """Sauvegarde cohérente de la base (VACUUM INTO) puis rotation.

    ``VACUUM INTO`` produit un fichier compacté et transactionnellement
    cohérent même pendant que JARVIS écrit (mode WAL). Retourne un rapport
    {ok, path, size_bytes, duration_s, removed}.
    """
    src = Path(config.DB_PATH)
    if not src.exists():
        return {"ok": False, "error": f"base introuvable : {src}"}

    backup_dir = _backup_dir()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = backup_dir / f"jarvis-{stamp}.db"
    n = 1
    while dest.exists():                     # collision même seconde
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

    removed = _rotate_backups(backup_dir)
    report = {
        "ok": True,
        "path": str(dest),
        "size_bytes": dest.stat().st_size,
        "duration_s": round(time.monotonic() - t0, 2),
        "removed": removed,
    }
    logger.info(
        "[backup] %s (%.1f Mo, %.2fs, rotation: %d supprimée(s))",
        dest.name, report["size_bytes"] / 1e6, report["duration_s"], len(removed),
    )
    return report


def _rotate_backups(backup_dir: Path, keep: int | None = None) -> list[str]:
    """Ne conserve que les ``keep`` sauvegardes les plus récentes (par mtime)."""
    keep = config.BACKUP_KEEP if keep is None else keep
    if keep <= 0:
        return []
    files = sorted(backup_dir.glob("jarvis-*.db"), key=lambda f: f.stat().st_mtime)
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
    for f in sorted(backup_dir.glob("jarvis-*.db"),
                    key=lambda f: f.stat().st_mtime, reverse=True):
        st = f.stat()
        out.append({
            "name": f.name,
            "size_bytes": st.st_size,
            "created_at": datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"),
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
