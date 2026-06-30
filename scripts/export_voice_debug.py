#!/usr/bin/env python3
"""Exporte toutes les traces voice_debug_log en un fichier Markdown lisible.

Usage:
    python scripts/export_voice_debug.py [--output OUTPUT.md]

Output par défaut : data/exports/voice_debug_YYYY-MM-DD_HHMM.md
"""

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = os.environ.get("DB_PATH", str(ROOT / "data" / "jarvis.db"))
DEFAULT_OUTPUT = ROOT / "data" / "exports" / f"voice_debug_{datetime.now().strftime('%Y-%m-%d_%H%M')}.md"


def _fmt_ms(ms: int) -> str:
    """Formate une duree en millisecondes de maniere lisible."""
    if ms <= 0:
        return "—"
    if ms < 1000:
        return f"{ms} ms"
    sec = ms / 1000
    if sec < 60:
        return f"{sec:.1f} s"
    min_ = int(sec // 60)
    s = sec % 60
    return f"{min_} min {s:.0f} s"


def _fmt_cost(cost: float) -> str:
    """Formate un cout en dollars."""
    if cost <= 0:
        return "$0"
    if cost < 0.001:
        return f"${cost * 1000000:.0f} µ$"
    return f"${cost:.4f}"


def _safe_json(s: str | None) -> Any:
    """Parse JSON de maniere tolerante."""
    if not s:
        return None
    try:
        return json.loads(s)
    except (json.JSONDecodeError, TypeError):
        return s


def _truncate(text: str | None, max_len: int = 3000) -> str:
    """Tronque un texte long avec indication."""
    if not text:
        return "(vide)"
    if len(text) <= max_len:
        return text
    return text[:max_len] + f"\n\n… [TRONQUÉ — {len(text)} caractères au total]"


def build_markdown(rows: list[dict[str, Any]]) -> str:
    """Construit un document markdown a partir des traces voice_debug_log."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines: list[str] = []

    lines.append(f"# Voice Debug — Export complet")
    lines.append(f"")
    lines.append(f"**Export genere le {now}**  ")
    lines.append(f"**Nombre de traces : {len(rows)}**  ")
    lines.append(f"**Base de donnees : `{DB_PATH}`**")
    lines.append(f"")
    lines.append(f"---")
    lines.append(f"")

    if not rows:
        lines.append("> Aucune trace dans la table `voice_debug_log`.")
        lines.append("")
        return "\n".join(lines)

    for idx, row in enumerate(rows, start=1):
        trace_id = row.get("id", "?")
        created_at = row.get("created_at", "inconnu")
        input_text = row.get("input_text") or "(pas de transcription)"
        system_prompt = row.get("system_prompt")
        messages_json = row.get("messages_json")
        raw_response = row.get("raw_response")
        response_clean = row.get("response_clean")
        emotion = row.get("emotion") or "neutral"
        action_json = row.get("action_json")
        model = row.get("model") or "inconnu"
        tokens_in = row.get("tokens_in", 0) or 0
        tokens_out = row.get("tokens_out", 0) or 0
        cost = row.get("cost", 0) or 0
        stt_latency = row.get("latency_stt_ms", 0) or 0
        llm1_latency = row.get("latency_llm1_ms", 0) or 0
        llm2_latency = row.get("latency_llm2_ms", 0) or 0
        tts_latency = row.get("latency_tts_ms", 0) or 0
        total_latency = row.get("latency_total_ms", 0) or 0
        stt_engine = row.get("stt_engine") or "inconnu"
        tts_engine = row.get("tts_engine") or "inconnu"
        audio_duration = row.get("audio_duration_ms", 0) or 0

        # ── Entete de la trace ──
        lines.append(f"## Trace #{trace_id} — {created_at}")
        lines.append(f"")
        lines.append(f"| Metrique | Valeur |")
        lines.append(f"|---|---|")
        lines.append(f"| ID | {trace_id} |")
        lines.append(f"| Horodatage | `{created_at}` |")
        lines.append(f"| Modele | `{model}` |")
        lines.append(f"| Tokens IN / OUT | {tokens_in} / {tokens_out} |")
        lines.append(f"| Cout | {_fmt_cost(cost)} |")
        lines.append(f"| Emotion | `{emotion}` |")
        lines.append(f"| Audio duree | {_fmt_ms(audio_duration)} |")
        lines.append(f"| STT ({stt_engine}) | {_fmt_ms(stt_latency)} |")
        lines.append(f"| LLM Pass 1 | {_fmt_ms(llm1_latency)} |")
        lines.append(f"| LLM Pass 2 | {_fmt_ms(llm2_latency)} |")
        lines.append(f"| TTS ({tts_engine}) | {_fmt_ms(tts_latency)} |")
        lines.append(f"| **Latence totale** | **{_fmt_ms(total_latency)}** |")
        lines.append(f"")

        # ── Transcription ──
        lines.append(f"### 1. Transcription (STT)")
        lines.append(f"")
        lines.append(f"> {input_text}")
        lines.append(f"")

        # ── System Prompt ──
        if system_prompt:
            lines.append(f"### 2. System Prompt")
            lines.append(f"")
            lines.append(f"```text")
            lines.append(_truncate(system_prompt, 5000))
            lines.append(f"```")
            lines.append(f"")

        # ── Messages envoyes (historique) ──
        if messages_json:
            messages = _safe_json(messages_json)
            if isinstance(messages, list) and messages:
                lines.append(f"### 3. Messages envoyes au LLM (historique)")
                lines.append(f"")
                lines.append(f"| # | Role | Contenu |")
                lines.append(f"|---|---|---|")
                for mi, msg in enumerate(messages):
                    if isinstance(msg, dict):
                        role = msg.get("role", "?")
                        content = msg.get("content", "")
                        content_short = content[:200].replace("\n", " ").replace("|", "\\|")
                        if len(content) > 200:
                            content_short += "…"
                        lines.append(f"| {mi + 1} | `{role}` | {content_short} |")
                lines.append(f"")
                lines.append(f"<details><summary>Contenu complet des messages</summary>")
                lines.append(f"")
                lines.append(f"```json")
                lines.append(json.dumps(messages, ensure_ascii=False, indent=2))
                lines.append(f"```")
                lines.append(f"</details>")
                lines.append(f"")

        # ── Reponse brute ──
        if raw_response:
            lines.append(f"### 4. Reponse brute (LLM)")
            lines.append(f"")
            lines.append(f"```text")
            lines.append(_truncate(raw_response, 5000))
            lines.append(f"```")
            lines.append(f"")

        # ── Reponse clean ──
        if response_clean and response_clean != raw_response:
            lines.append(f"### 5. Reponse clean (apres extraction emotion)")
            lines.append(f"")
            lines.append(f"> {response_clean}")
            lines.append(f"")

        # ── Action detectee ──
        if action_json:
            action = _safe_json(action_json)
            lines.append(f"### 6. Action detectee")
            lines.append(f"")
            lines.append(f"```json")
            if isinstance(action, (dict, list)):
                lines.append(json.dumps(action, ensure_ascii=False, indent=2))
            else:
                lines.append(str(action))
            lines.append(f"```")
            lines.append(f"")

        # ── Separateur ──
        lines.append(f"---")
        lines.append(f"")

    # ── Footer ──
    lines.append(f"")
    lines.append(f"*Export automatise par `scripts/export_voice_debug.py` — {now}*")
    lines.append(f"")

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Exporte les traces voice_debug_log en Markdown")
    parser.add_argument(
        "--output", "-o",
        type=str,
        default=str(DEFAULT_OUTPUT),
        help=f"Chemin du fichier de sortie (defaut : {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Nombre max de traces (0 = toutes).",
    )
    args = parser.parse_args()

    if not os.path.isfile(DB_PATH):
        print(f"[ERREUR] Base de donnees introuvable : {DB_PATH}", file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    try:
        # Verifie que la table existe
        table_check = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='voice_debug_log'"
        ).fetchone()
        if not table_check:
            print("[ERREUR] La table 'voice_debug_log' n'existe pas dans la base.", file=sys.stderr)
            sys.exit(1)

        if args.limit > 0:
            cursor = conn.execute(
                "SELECT * FROM voice_debug_log ORDER BY id ASC LIMIT ?",
                (args.limit,),
            )
        else:
            cursor = conn.execute(
                "SELECT * FROM voice_debug_log ORDER BY id ASC"
            )

        rows: list[dict[str, Any]] = [dict(r) for r in cursor.fetchall()]
        print(f"[INFO] {len(rows)} traces trouvees dans voice_debug_log")

    finally:
        conn.close()

    md_content = build_markdown(rows)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(md_content, encoding="utf-8")

    size_kb = len(md_content.encode("utf-8")) / 1024
    print(f"[OK] Export termine : {output_path} ({size_kb:.1f} KB, {len(rows)} traces)")


if __name__ == "__main__":
    main()
