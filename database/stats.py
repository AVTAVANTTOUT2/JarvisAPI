"""Agrégats de coût LLM et d'activité quotidienne."""

from __future__ import annotations

from datetime import datetime, timedelta

import config

from .core import get_db
from .time_buckets import local_datetime, utc_bounds_for_local_dates


def get_cost_summary(*, now: datetime | None = None) -> dict:
    local_now = local_datetime(now)
    today = local_now.date()
    tomorrow = today + timedelta(days=1)
    week_start = today - timedelta(days=6)
    month_start = today.replace(day=1)
    next_month = (
        month_start.replace(year=month_start.year + 1, month=1)
        if month_start.month == 12
        else month_start.replace(month=month_start.month + 1)
    )
    today_bounds = utc_bounds_for_local_dates(today, tomorrow)
    week_bounds = utc_bounds_for_local_dates(week_start, tomorrow)
    month_bounds = utc_bounds_for_local_dates(month_start, next_month)

    with get_db() as conn:
        def aggregate(bounds: tuple[str, str]) -> dict:
            row = conn.execute(
                """SELECT COUNT(*) AS msg_count,
                           COALESCE(SUM(cost), 0.0) AS cost,
                           COALESCE(SUM(tokens_in), 0) AS tokens_in,
                           COALESCE(SUM(tokens_out), 0) AS tokens_out
                    FROM messages
                    WHERE created_at >= ? AND created_at < ?""",
                bounds,
            ).fetchone()
            return dict(row)

        by_model = [
            dict(row)
            for row in conn.execute(
                """SELECT COALESCE(model, 'inconnu') AS model,
                          COUNT(*) AS msg_count, COALESCE(SUM(cost), 0) AS cost
                   FROM messages
                   WHERE created_at >= ? AND created_at < ?
                     AND model IS NOT NULL
                   GROUP BY COALESCE(model, 'inconnu') ORDER BY cost DESC""",
                month_bounds,
            )
        ]
        today_stats = aggregate(today_bounds)
        week_stats = aggregate(week_bounds)
        month_stats = aggregate(month_bounds)
    return {
        "today": today_stats,
        "last_7_days": week_stats,
        "month": month_stats,
        "by_model_month": by_model,
        "budget_monthly": config.LLM_BUDGET_MONTHLY,
        "budget_alert_pct": config.LLM_BUDGET_ALERT_PCT,
    }


def get_daily_activity_stats(
    days: int = 7,
    *,
    now: datetime | None = None,
) -> list[dict]:
    days = max(1, min(days, 90))
    today = local_datetime(now).date()
    dates = [today - timedelta(days=index) for index in range(days - 1, -1, -1)]
    bounds = [
        (day.isoformat(), *utc_bounds_for_local_dates(day, day + timedelta(days=1)))
        for day in dates
    ]
    values_sql = ", ".join("(?, ?, ?)" for _ in bounds)
    params = tuple(value for bound in bounds for value in bound)

    with get_db() as conn:
        rows = conn.execute(
            f"""WITH day_bounds(date, start_utc, end_utc) AS (
                   VALUES {values_sql}
               )
               SELECT day_bounds.date AS date,
                      COUNT(m.id) AS msg_count,
                      COALESCE(SUM(CASE WHEN c.agent = 'voice' THEN 1 ELSE 0 END), 0) AS voice_count,
                      COALESCE(SUM(CASE WHEN m.role = 'user' THEN 1 ELSE 0 END), 0) AS turn_count,
                      COALESCE(SUM(m.tokens_in), 0) AS tokens_in,
                      COALESCE(SUM(m.tokens_out), 0) AS tokens_out,
                      COALESCE(SUM(m.cost), 0.0) AS cost
               FROM day_bounds
               LEFT JOIN messages m
                 ON m.created_at >= day_bounds.start_utc
                AND m.created_at < day_bounds.end_utc
               LEFT JOIN conversations c ON c.id = m.conversation_id
               GROUP BY day_bounds.date
               ORDER BY day_bounds.date""",
            params,
        ).fetchall()
    return [dict(row) for row in rows]
