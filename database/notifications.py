"""Persistance des notifications, Web Push et journal des actions LLM."""

from __future__ import annotations

import logging
import sqlite3
from typing import Any

import config
from jarvis.log_privacy import redact_action_log_payload, sanitize_log_label

from .core import get_db
from .push import delete_push_subscription, get_all_push_subscriptions

logger = logging.getLogger(__name__)


def _insert_notification(
    source: str,
    title: str,
    content: str | None,
    priority: str,
    email_id: str | None,
    deduplication_window_seconds: int | None,
) -> tuple[int, bool]:
    """Insère atomiquement une notification et indique si elle est nouvelle."""
    with get_db() as conn:
        if deduplication_window_seconds is None:
            cur = conn.execute(
                """INSERT INTO notifications (source, title, content, priority, email_id)
                   VALUES (?, ?, ?, ?, ?)""",
                (source, title, content, priority, email_id),
            )
            return int(cur.lastrowid), True

        window_seconds = max(0, int(deduplication_window_seconds))
        if window_seconds == 0:
            cur = conn.execute(
                """INSERT INTO notifications (source, title, content, priority, email_id)
                   VALUES (?, ?, ?, ?, ?)""",
                (source, title, content, priority, email_id),
            )
            return int(cur.lastrowid), True

        cur = conn.execute(
            """
            INSERT INTO notifications (source, title, content, priority, email_id)
            SELECT ?, ?, ?, ?, ?
            WHERE NOT EXISTS (
                SELECT 1
                FROM notifications
                WHERE source = ?
                  AND title = ?
                  AND email_id IS ?
                  AND created_at >= datetime('now', ?)
            )
            """,
            (
                source,
                title,
                content,
                priority,
                email_id,
                source,
                title,
                email_id,
                f"-{window_seconds} seconds",
            ),
        )
        if conn.execute("SELECT changes()").fetchone()[0] == 1:
            return int(cur.lastrowid), True

        existing = conn.execute(
            """
            SELECT id
            FROM notifications
            WHERE source = ?
              AND title = ?
              AND email_id IS ?
              AND created_at >= datetime('now', ?)
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            (source, title, email_id, f"-{window_seconds} seconds"),
        ).fetchone()
        if existing is not None:
            return int(existing["id"]), False

        # Défense contre un état SQLite inattendu entre l'INSERT et la lecture.
        cur = conn.execute(
            """INSERT INTO notifications (source, title, content, priority, email_id)
               VALUES (?, ?, ?, ?, ?)""",
            (source, title, content, priority, email_id),
        )
        return int(cur.lastrowid), True


def create_notification(
    source: str,
    title: str,
    content: str | None = None,
    priority: str = "medium",
    email_id: str | None = None,
) -> int:
    """Façade historique : délègue au :class:`NotificationService`.

    L'import local évite que la couche ``database`` dépende du service lors de
    son initialisation, tout en gardant exactement cette API publique.
    """
    from jarvis.notification_service import notification_service

    return notification_service.create(source, title, content, priority, email_id)


def _dispatch_push_notification(title: str, content: str | None, priority: str) -> None:
    """Envoi Web Push + FCM Android — en arrière-plan, jamais bloquant."""
    import threading

    def _send():
        try:
            import push

            for sub in get_all_push_subscriptions():
                subscription = {
                    "endpoint": sub["endpoint"],
                    "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]},
                }
                ok, status = push.send_web_push(
                    subscription, {"title": title, "body": content or "", "priority": priority}
                )
                if not ok and status in (404, 410):
                    delete_push_subscription(sub["endpoint"])

            from database.mobile import clear_mobile_push_token, get_active_mobile_push_tokens
            from integrations.fcm import send_fcm_notification

            for token in get_active_mobile_push_tokens():
                ok, status = send_fcm_notification(token, title, content, priority)
                if not ok and status in (404, 410):
                    clear_mobile_push_token(token)
        except Exception:
            logger.debug("[push] dispatch échoué (best-effort)", exc_info=True)

    threading.Thread(target=_send, daemon=True).start()


def get_unread_notifications(limit: int = 50) -> list:
    """Notifications non lues, triées par priorité puis récence."""
    priority_order = (
        "CASE priority "
        "WHEN 'urgent' THEN 0 WHEN 'high' THEN 1 "
        "WHEN 'medium' THEN 2 ELSE 3 END"
    )
    with get_db() as conn:
        rows = conn.execute(
            f"""SELECT * FROM notifications
                WHERE read = 0
                ORDER BY {priority_order}, created_at DESC
                LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_recent_notifications(limit: int = 50) -> list:
    """Notifications récentes (lues + non lues)."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM notifications ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


def mark_notification_read(notif_id: int) -> bool:
    with get_db() as conn:
        cur = conn.execute(
            "UPDATE notifications SET read = 1 WHERE id = ?", (notif_id,)
        )
        return cur.rowcount > 0


def mark_all_notifications_read() -> int:
    with get_db() as conn:
        cur = conn.execute("UPDATE notifications SET read = 1 WHERE read = 0")
        return cur.rowcount


def log_llm_action(
    agent: str,
    action_type: str,
    payload: Any,
    status: str,
    execution_time_ms: int | None = None,
) -> int:
    """Persiste un log d'action LLM après rédaction centralisée."""
    if status not in ("success", "error", "pending"):
        status = "pending"
    safe_agent = sanitize_log_label(agent)
    safe_action_type = sanitize_log_label(action_type)
    payload_text = redact_action_log_payload(payload, safe_action_type)
    with get_db() as conn:
        conn.execute(
            "DELETE FROM llm_action_logs WHERE created_at < datetime('now', ?)",
            (f"-{config.RETENTION_LLM_LOGS_DAYS} days",),
        )
        cur = conn.execute(
            """
            INSERT INTO llm_action_logs (agent, action_type, payload, status, execution_time_ms)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                safe_agent,
                safe_action_type,
                payload_text,
                status,
                execution_time_ms,
            ),
        )
        return int(cur.lastrowid)


def clear_llm_logs() -> dict[str, int]:
    """Efface les journaux visibles dans l'écran Logs."""
    deleted = {"llm_action_logs": 0, "dev_loop_log": 0}
    with get_db() as conn:
        cur = conn.execute("DELETE FROM llm_action_logs")
        deleted["llm_action_logs"] = max(0, int(cur.rowcount))
        try:
            cur = conn.execute("DELETE FROM dev_loop_log")
            deleted["dev_loop_log"] = max(0, int(cur.rowcount))
        except sqlite3.OperationalError:
            # La table DevAgent est optionnelle dans les anciennes bases.
            logger.debug("Table dev_loop_log absente pendant l'effacement", exc_info=True)
    return deleted


def get_llm_logs(limit: int = 100, action_type: str | None = None) -> list[dict]:
    """Retourne les logs LLM les plus recents, optionnellement filtres par type.

    Si ``action_type`` vaut ``devagent`` ou commence par ``devagent_``, lit
    ``dev_loop_log``. Sans filtre, fusionne ``llm_action_logs`` et DevAgent.
    """
    from database.devagent import get_dev_loop_logs

    lim = max(1, min(int(limit), 1000))

    if action_type == "devagent" or (
        action_type and action_type.startswith("devagent_")
    ):
        phase = None
        if action_type and action_type.startswith("devagent_"):
            phase = action_type.removeprefix("devagent_")
        logs = get_dev_loop_logs(limit=lim)
        if phase:
            logs = [row for row in logs if row.get("action_type") == action_type]
        return logs[:lim]

    with get_db() as conn:
        conn.execute(
            "DELETE FROM llm_action_logs WHERE created_at < datetime('now', ?)",
            (f"-{config.RETENTION_LLM_LOGS_DAYS} days",),
        )
        if action_type:
            rows = conn.execute(
                """
                SELECT *
                FROM llm_action_logs
                WHERE action_type = ?
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                (action_type, lim),
            ).fetchall()
            return [dict(r) for r in rows]

        rows = conn.execute(
            """
            SELECT *
            FROM llm_action_logs
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (lim,),
        ).fetchall()
        llm_logs = [dict(r) for r in rows]

    dev_logs = get_dev_loop_logs(limit=lim)
    merged = llm_logs + dev_logs
    merged.sort(key=lambda item: (item.get("created_at") or "", item.get("id") or 0), reverse=True)
    return merged[:lim]
