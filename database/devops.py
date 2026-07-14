"""Migrations versionnées, performance, audits et traces de diagnostic."""

from __future__ import annotations

import json
from typing import Any

from .core import get_db


def get_applied_migrations() -> dict[str, str]:
    """{filename: checksum} de toutes les migrations déjà appliquées."""
    with get_db() as conn:
        rows = conn.execute("SELECT filename, checksum FROM schema_migrations").fetchall()
        return {r["filename"]: r["checksum"] for r in rows}


def record_migration(filename: str, checksum: str) -> None:
    with get_db() as conn:
        conn.execute(
            "INSERT INTO schema_migrations (filename, checksum) VALUES (?, ?)",
            (filename, checksum),
        )


def record_perf_benchmark(scope: str, commit_sha: str | None, duration_ms: float) -> None:
    with get_db() as conn:
        conn.execute(
            "INSERT INTO perf_benchmarks (scope, commit_sha, duration_ms) VALUES (?, ?, ?)",
            (scope, commit_sha, duration_ms),
        )


def get_perf_history(scope: str, limit: int = 20) -> list[dict]:
    """Historique récent (plus récent en premier).

    Tri secondaire sur ``id DESC`` : ``created_at`` a une résolution de la
    seconde, insuffisante pour départager des benchmarks enregistrés dans la
    même seconde (courant en test, possible en usage réel sur une machine
    rapide).
    """
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM perf_benchmarks WHERE scope = ? ORDER BY created_at DESC, id DESC LIMIT ?",
            (scope, limit),
        ).fetchall()
        return [dict(r) for r in rows]


def get_perf_baseline(scope: str, window: int = 5) -> float | None:
    """Médiane des `window` derniers benchmarks de ce scope. None si historique insuffisant."""
    history = get_perf_history(scope, limit=window)
    if len(history) < 2:  # au moins 2 points avant de comparer à un 3e
        return None
    durations = sorted(h["duration_ms"] for h in history)
    n = len(durations)
    mid = n // 2
    return durations[mid] if n % 2 else (durations[mid - 1] + durations[mid]) / 2


def upsert_security_finding(file: str, line: int, rule: str, severity: str,
                            snippet: str | None = None) -> bool:
    """Insère un constat s'il est nouveau. Retourne True si c'est une nouveauté."""
    with get_db() as conn:
        cur = conn.execute(
            """INSERT OR IGNORE INTO security_findings (file, line, rule, severity, snippet)
               VALUES (?, ?, ?, ?, ?)""",
            (file, line, rule, severity, snippet),
        )
        return cur.rowcount > 0


def get_security_findings(status: str = "open", limit: int = 200) -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            """SELECT * FROM security_findings WHERE status = ?
               ORDER BY CASE severity WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END,
                        created_at DESC LIMIT ?""",
            (status, limit),
        ).fetchall()
        return [dict(r) for r in rows]


def update_security_finding_status(finding_id: int, status: str) -> bool:
    if status not in ("open", "fixed", "ignored"):
        raise ValueError(f"statut invalide : {status}")
    with get_db() as conn:
        cur = conn.execute(
            "UPDATE security_findings SET status = ? WHERE id = ?", (status, finding_id),
        )
        return cur.rowcount > 0


def upsert_duplicate_finding(file_a: str, start_a: int, end_a: int,
                             file_b: str, start_b: int, end_b: int, lines_count: int) -> bool:
    with get_db() as conn:
        cur = conn.execute(
            """INSERT OR IGNORE INTO duplicate_findings
                   (file_a, start_a, end_a, file_b, start_b, end_b, lines_count)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (file_a, start_a, end_a, file_b, start_b, end_b, lines_count),
        )
        return cur.rowcount > 0


def get_duplicate_findings(status: str = "open", limit: int = 100) -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            """SELECT * FROM duplicate_findings WHERE status = ?
               ORDER BY lines_count DESC LIMIT ?""",
            (status, limit),
        ).fetchall()
        return [dict(r) for r in rows]


def _save_voice_debug_trace(trace: dict[str, Any]) -> None:
    """Sauvegarde une trace de debug vocal en DB (fire-and-forget, silencieux).

    Args:
        trace: dict contenant les champs du debug trace (input_text, system_prompt,
               messages_sent, raw_response, response_clean, emotion, action_detected,
               model, tokens_in, tokens_out, cost, latency_*).
    """
    import json as _json

    try:
        with get_db() as conn:
            conn.execute(
                """INSERT INTO voice_debug_log
                   (input_text, system_prompt, messages_json, raw_response, response_clean,
                    emotion, action_json, model, tokens_in, tokens_out, cost,
                    latency_stt_ms, latency_llm1_ms, latency_llm2_ms, latency_tts_ms,
                    latency_total_ms, stt_engine, tts_engine, audio_duration_ms)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    str(trace.get("input_text", ""))[:3000] if trace.get("input_text") else "",
                    str(trace.get("system_prompt", ""))[:50000] if trace.get("system_prompt") else "",
                    _json.dumps(trace.get("messages_sent", []), ensure_ascii=False) if trace.get("messages_sent") else "",
                    str(trace.get("raw_response", ""))[:10000] if trace.get("raw_response") else "",
                    str(trace.get("response_clean", ""))[:5000] if trace.get("response_clean") else "",
                    str(trace.get("emotion", "")),
                    _json.dumps(trace.get("action_detected"), ensure_ascii=False) if trace.get("action_detected") else None,
                    str(trace.get("model", "")),
                    int(trace.get("tokens_in", 0)),
                    int(trace.get("tokens_out", 0)),
                    float(trace.get("cost", 0.0)),
                    int(trace.get("latency_stt_ms", 0)),
                    int(trace.get("latency_llm_pass1_ms", 0)),
                    int(trace.get("latency_llm_pass2_ms", 0)),
                    int(trace.get("latency_tts_ms", 0)),
                    int(trace.get("latency_total_ms", 0)),
                    str(trace.get("stt_engine", "")),
                    str(trace.get("tts_engine", "")),
                    int(trace.get("audio_duration_ms", 0)),
                ),
            )
    except Exception:
        pass


def get_voice_debug_logs(limit: int = 50) -> list[dict[str, Any]]:
    """Récupère les dernières traces de debug vocal.

    Args:
        limit: Nombre maximum de traces à retourner (défaut 50).

    Returns:
        Liste de dicts, ordre décroissant par id (plus récent d'abord).
    """
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM voice_debug_log ORDER BY id DESC LIMIT ?",
            (max(1, min(int(limit), 500)),),
        ).fetchall()
        return [dict(r) for r in rows]
