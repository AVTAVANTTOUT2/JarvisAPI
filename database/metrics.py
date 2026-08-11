"""Séries temporelles locales pour l'observabilité JARVIS."""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

import config

from .core import get_db

DEFAULT_BUCKET_SECONDS = 300
_STATE_SCORES = {
    "healthy": 100.0,
    "degraded": 50.0,
    "unavailable": 0.0,
    "unknown": 25.0,
}


def _bucket_start(recorded_at: datetime, bucket_seconds: int) -> str:
    utc = recorded_at.astimezone(timezone.utc)
    epoch = int(utc.timestamp())
    bucket_epoch = epoch - (epoch % max(60, int(bucket_seconds)))
    return datetime.fromtimestamp(bucket_epoch, timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _metric_component_name(value: Any) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", str(value or "unknown").casefold())
    return normalized.strip("_") or "unknown"


def record_metric_samples(
    samples: Mapping[str, tuple[float, str]],
    *,
    recorded_at: datetime | None = None,
    bucket_seconds: int = DEFAULT_BUCKET_SECONDS,
) -> None:
    """Agrège plusieurs relevés dans un bucket sans croissance non bornée."""
    if not samples:
        return
    now = recorded_at or datetime.now(timezone.utc)
    bucket_at = _bucket_start(now, bucket_seconds)
    timestamp = now.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    with get_db() as conn:
        for metric, (raw_value, unit) in samples.items():
            value = float(raw_value)
            conn.execute(
                """
                INSERT INTO metric_samples
                    (metric, bucket_at, value, last_value, unit, sample_count, recorded_at)
                VALUES (?, ?, ?, ?, ?, 1, ?)
                ON CONFLICT(metric, bucket_at) DO UPDATE SET
                    value = (
                        metric_samples.value * metric_samples.sample_count + excluded.value
                    ) / (metric_samples.sample_count + 1),
                    last_value = excluded.last_value,
                    unit = excluded.unit,
                    sample_count = metric_samples.sample_count + 1,
                    recorded_at = excluded.recorded_at
                """,
                (metric, bucket_at, value, value, unit, timestamp),
            )


def record_health_snapshot(
    report: Mapping[str, Any],
    *,
    recorded_at: datetime | None = None,
    bucket_seconds: int = DEFAULT_BUCKET_SECONDS,
) -> None:
    """Convertit le diagnostic santé en métriques numériques stables."""
    samples: dict[str, tuple[float, str]] = {
        "health.score": (_STATE_SCORES.get(str(report.get("status")), 25.0), "percent"),
        "health.duration_ms": (float(report.get("duration_ms") or 0), "ms"),
    }
    for component in report.get("components") or []:
        if not isinstance(component, Mapping):
            continue
        name = _metric_component_name(component.get("name"))
        state = str(component.get("state") or "unknown")
        samples[f"health.{name}.score"] = (_STATE_SCORES.get(state, 25.0), "percent")
        details = component.get("details")
        if isinstance(details, Mapping):
            latency = details.get("latency_ms")
            if isinstance(latency, (int, float)) and not isinstance(latency, bool):
                samples[f"health.{name}.latency_ms"] = (float(latency), "ms")
    record_metric_samples(
        samples,
        recorded_at=recorded_at,
        bucket_seconds=bucket_seconds,
    )


def get_metric_history(hours: int = 24) -> dict[str, Any]:
    """Retourne les buckets et tendances pondérées de la période demandée."""
    bounded_hours = max(1, min(int(hours), 24 * 365))
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT metric, bucket_at, value, last_value, unit, sample_count
            FROM metric_samples
            WHERE bucket_at >= datetime('now', ?)
            ORDER BY metric, bucket_at
            """,
            (f"-{bounded_hours} hours",),
        ).fetchall()

    grouped: dict[str, list[dict[str, Any]]] = {}
    grouped_rows: dict[str, list[Any]] = {}
    for row in rows:
        grouped_rows.setdefault(row["metric"], []).append(row)
        grouped.setdefault(row["metric"], []).append(
            {
                "timestamp": f"{row['bucket_at']}Z",
                "value": round(float(row["value"]), 2),
                "last_value": round(float(row["last_value"]), 2),
                "samples": int(row["sample_count"]),
            }
        )

    series = []
    for metric, points in grouped.items():
        metric_rows = grouped_rows[metric]
        sample_count = sum(int(row["sample_count"]) for row in metric_rows)
        weighted_total = sum(
            float(row["value"]) * int(row["sample_count"]) for row in metric_rows
        )
        first = float(metric_rows[0]["last_value"])
        latest = float(metric_rows[-1]["last_value"])
        trend_pct = (
            None
            if len(metric_rows) < 2 or first == 0
            else round(((latest - first) / abs(first)) * 100, 1)
        )
        series.append(
            {
                "metric": metric,
                "unit": metric_rows[-1]["unit"],
                "points": points,
                "summary": {
                    "latest": round(latest, 2),
                    "average": round(weighted_total / sample_count, 2),
                    "minimum": round(min(float(row["value"]) for row in metric_rows), 2),
                    "maximum": round(max(float(row["value"]) for row in metric_rows), 2),
                    "trend_pct": trend_pct,
                    "samples": sample_count,
                },
            }
        )

    return {
        "hours": bounded_hours,
        "bucket_seconds": DEFAULT_BUCKET_SECONDS,
        "retention_days": int(config.RETENTION_METRICS_DAYS),
        "series": series,
    }
