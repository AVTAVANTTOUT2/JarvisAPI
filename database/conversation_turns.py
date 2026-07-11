"""Persistance des tours de parole issus de la diarisation."""

from __future__ import annotations

from .core import get_db


def save_conversation_turns(recording_id: int, turns: list[dict]) -> int:
    with get_db() as conn:
        for index, turn in enumerate(turns):
            conn.execute(
                """INSERT INTO conversation_turns
                   (recording_id, turn_order, speaker_label, text, start_ms, end_ms)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    recording_id,
                    index,
                    turn["speaker_label"],
                    turn["text"],
                    turn.get("start_ms"),
                    turn.get("end_ms"),
                ),
            )
    return len(turns)


def get_conversation_turns(recording_id: int) -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            """SELECT ct.*, p.name AS person_name FROM conversation_turns ct
               LEFT JOIN people p ON p.id = ct.person_id
               WHERE ct.recording_id = ? ORDER BY ct.turn_order ASC""",
            (recording_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def get_unlabeled_speakers(recording_id: int) -> list[str]:
    with get_db() as conn:
        rows = conn.execute(
            """SELECT DISTINCT speaker_label FROM conversation_turns
               WHERE recording_id = ? AND person_id IS NULL
               ORDER BY speaker_label""",
            (recording_id,),
        ).fetchall()
    return [row["speaker_label"] for row in rows]


def assign_speaker_to_person(
    recording_id: int, speaker_label: str, person_id: int
) -> int:
    with get_db() as conn:
        cursor = conn.execute(
            """UPDATE conversation_turns SET person_id = ?
               WHERE recording_id = ? AND speaker_label = ?""",
            (person_id, recording_id, speaker_label),
        )
    return cursor.rowcount
