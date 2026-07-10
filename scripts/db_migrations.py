"""Migrations SQLite versionnées — sauvegarde automatique avant application.

Voir ``database/migrations/README.md`` pour le format et le rôle de ce
système par rapport aux migrations idempotentes de ``database/__init__.py``.

Garanties :
- Une migration n'est jamais appliquée deux fois (trace dans
  ``schema_migrations``, unique sur ``filename``).
- Si le contenu d'une migration déjà appliquée a changé depuis (checksum
  différent), on lève au lieu de ré-appliquer silencieusement un contenu
  différent de celui qui a produit l'état actuel de la base.
- S'il y a des migrations en attente, une sauvegarde (``VACUUM INTO``) est
  prise avant la première — en cas de casse, la restauration est immédiate.
- Application dans une transaction par fichier ; arrêt à la première erreur
  (les migrations suivantes ne sont jamais appliquées après un échec).
"""

from __future__ import annotations

import hashlib
import logging
import sqlite3
from pathlib import Path

import config
from database import get_applied_migrations, get_connection, record_migration

logger = logging.getLogger(__name__)


class MigrationIntegrityError(RuntimeError):
    """Le contenu d'une migration déjà appliquée a changé depuis son application."""


def _migrations_dir() -> Path:
    return Path(config.DB_MIGRATIONS_DIR)


def _checksum(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def discover_migrations() -> list[Path]:
    """Fichiers .sql du dossier de migrations, triés par nom (ordre d'application)."""
    d = _migrations_dir()
    if not d.is_dir():
        return []
    return sorted(d.glob("*.sql"))


def pending_migrations() -> list[Path]:
    """Migrations non encore appliquées.

    Lève ``MigrationIntegrityError`` si une migration déjà appliquée a un
    contenu différent de celui enregistré (falsification ou édition après
    application).
    """
    applied = get_applied_migrations()
    pending: list[Path] = []
    for path in discover_migrations():
        content = path.read_text(encoding="utf-8")
        checksum = _checksum(content)
        if path.name in applied:
            if applied[path.name] != checksum:
                raise MigrationIntegrityError(
                    f"Migration '{path.name}' déjà appliquée avec un contenu différent "
                    f"(checksum attendu {applied[path.name][:12]}…, trouvé {checksum[:12]}…). "
                    "Ne jamais éditer une migration déjà appliquée — créez-en une nouvelle."
                )
            continue
        pending.append(path)
    return pending


def migration_status() -> dict:
    """Vue d'ensemble : migrations appliquées et en attente."""
    applied = get_applied_migrations()
    try:
        pending = pending_migrations()
        integrity_error = None
    except MigrationIntegrityError as e:
        pending = []
        integrity_error = str(e)
    return {
        "applied": sorted(applied.keys()),
        "pending": [p.name for p in pending],
        "integrity_error": integrity_error,
    }


def apply_pending_migrations() -> dict:
    """Applique les migrations en attente, sauvegarde préalable si nécessaire.

    Retourne ``{ok, applied: [...], backup: {...} | None, error: str | None}``.
    Si une migration échoue, les suivantes ne sont PAS appliquées — le rapport
    contient le nom du fichier fautif dans ``error`` et ``applied`` liste ce
    qui a effectivement réussi avant l'échec.
    """
    try:
        pending = pending_migrations()
    except MigrationIntegrityError as e:
        logger.error("[migrations] intégrité compromise : %s", e)
        return {"ok": False, "applied": [], "backup": None, "error": str(e)}

    if not pending:
        return {"ok": True, "applied": [], "backup": None, "error": None}

    from scripts.db_maintenance import run_backup

    backup_report = run_backup()
    if not backup_report.get("ok"):
        msg = f"Sauvegarde préalable échouée — migrations annulées : {backup_report.get('error')}"
        logger.error("[migrations] %s", msg)
        return {"ok": False, "applied": [], "backup": backup_report, "error": msg}

    applied: list[str] = []
    for path in pending:
        content = path.read_text(encoding="utf-8")
        conn = get_connection()
        try:
            conn.executescript(content)
            conn.commit()
        except sqlite3.Error as e:
            conn.rollback()
            conn.close()
            msg = f"Migration '{path.name}' échouée : {e}"
            logger.error("[migrations] %s", msg)
            return {"ok": False, "applied": applied, "backup": backup_report, "error": msg}
        conn.close()
        record_migration(path.name, _checksum(content))
        applied.append(path.name)
        logger.info("[migrations] appliquée : %s", path.name)

    return {"ok": True, "applied": applied, "backup": backup_report, "error": None}


def run_startup_migrations() -> None:
    """Point d'entrée au démarrage (lifespan) — n'échoue jamais bruyamment.

    Une migration cassée ne doit pas empêcher JARVIS de démarrer avec le
    schéma existant ; elle est loguée en erreur critique pour intervention
    manuelle (la sauvegarde préalable reste disponible pour investiguer).
    """
    if not config.DB_MIGRATIONS_AUTO_APPLY:
        return
    try:
        report = apply_pending_migrations()
        if report["applied"]:
            logger.info("[migrations] %d migration(s) appliquée(s) au démarrage : %s",
                       len(report["applied"]), report["applied"])
        if not report["ok"]:
            logger.critical("[migrations] échec au démarrage : %s", report["error"])
    except Exception as e:
        logger.critical("[migrations] erreur inattendue au démarrage : %s", e)
