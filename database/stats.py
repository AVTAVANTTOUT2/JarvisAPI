"""Agrégats de coût LLM et d'activité quotidienne."""

from __future__ import annotations

from datetime import datetime, timedelta

import config

from .core import get_db


def get_cost_summary() -> dict:
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    week_start = (now.date() - timedelta(days=6)).isoformat()
    month_start = now.strftime("%Y-%m-01")
    with get_db() as conn:
        def aggregate(where: str, params: tuple) -> dict:
            row = conn.execute(
                f"""SELECT COUNT(*) AS msg_count,
                           COALESCE(SUM(cost), 0) AS cost,
                           COALESCE(SUM(tokens_in), 0) AS tokens_in,
                           COALESCE(SUM(tokens_out), 0) AS tokens_out
                    FROM messages WHERE {where}""",
                params,
            ).fetchone()
            return dict(row)

        by_model = [
            dict(row)
            for row in conn.execute(
                """SELECT COALESCE(model, 'inconnu') AS model,
                          COUNT(*) AS msg_count, COALESCE(SUM(cost), 0) AS cost
                   FROM messages
                   WHERE DATE(created_at) >= ? AND model IS NOT NULL
                   GROUP BY COALESCE(model, 'inconnu') ORDER BY cost DESC""",
                (month_start,),
            )
        ]
        today_stats = aggregate("DATE(created_at) = ?", (today,))
        week_stats = aggregate("DATE(created_at) >= ?", (week_start,))
        month_stats = aggregate("DATE(created_at) >= ?", (month_start,))
    return {
        "today": today_stats,
        "last_7_days": week_stats,
        "month": month_stats,
        "by_model_month": by_model,
        "budget_monthly": config.LLM_BUDGET_MONTHLY,
        "budget_alert_pct": config.LLM_BUDGET_ALERT_PCT,
    }


def get_daily_activity_stats(days: int = 7) -> list[dict]:
    days = max(1, min(days, 90))
    today = datetime.now().date()
    start = (today - timedelta(days=days - 1)).isoformat()
    with get_db() as conn:
        rows = conn.execute(
            """SELECT DATE(m.created_at) AS date,
                      COUNT(*) AS msg_count,
                      COALESCE(SUM(CASE WHEN c.agent = 'voice' THEN 1 ELSE 0 END), 0) AS voice_count,
                      COALESCE(SUM(m.tokens_in), 0) AS tokens_in,
                      COALESCE(SUM(m.tokens_out), 0) AS tokens_out,
                      COALESCE(SUM(m.cost), 0) AS cost
               FROM messages m
               LEFT JOIN conversations c ON c.id = m.conversation_id
               WHERE DATE(m.created_at) >= ?
               GROUP BY DATE(m.created_at)""",
            (start,),
        ).fetchall()
    by_date = {row["date"]: dict(row) for row in rows}
    return [
        by_date.get(
            (today - timedelta(days=index)).isoformat(),
            {
                "date": (today - timedelta(days=index)).isoformat(),
                "msg_count": 0,
                "voice_count": 0,
                "tokens_in": 0,
                "tokens_out": 0,
                "cost": 0.0,
            },
        )
        for index in range(days - 1, -1, -1)
    ]
