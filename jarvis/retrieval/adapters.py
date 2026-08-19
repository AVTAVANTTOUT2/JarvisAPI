"""Adaptateurs SQL bornes vers les principales sources de verite JARVIS."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from database.core import get_db

from .models import RetrievalRequest
from .registry import KnowledgeDocument, RetrievalAdapter


_WORD_RE = re.compile(r"[^\W_]+", re.UNICODE)
_STOPWORDS = frozenset(
    {
        "a",
        "ai",
        "au",
        "aux",
        "avec",
        "ce",
        "ces",
        "dans",
        "de",
        "des",
        "du",
        "elle",
        "en",
        "est",
        "et",
        "il",
        "je",
        "la",
        "le",
        "les",
        "lui",
        "mail",
        "mails",
        "email",
        "emails",
        "courriel",
        "courriels",
        "boite",
        "boîte",
        "me",
        "mes",
        "mon",
        "moi",
        "ne",
        "nous",
        "on",
        "ou",
        "par",
        "pas",
        "pour",
        "que",
        "qui",
        "quoi",
        "sa",
        "se",
        "ses",
        "son",
        "sur",
        "ta",
        "te",
        "tes",
        "toi",
        "tu",
        "un",
        "une",
        "vous",
        "y",
        "résume",
        "resume",
        "résumer",
        "resumer",
        "lis",
        "lire",
        "lises",
        "veux",
        "veut",
        "voudrais",
        "donne",
        "montre",
        "trouve",
        "retrouve",
        "quoi",
        "passe",
        "passé",
        "arrive",
        "arrivé",
        "dernier",
        "derniers",
        "dernière",
        "dernières",
        "recent",
        "recents",
        "récent",
        "récents",
        "hier",
        "aujourd",
        "hui",
        "demain",
        "semaine",
        "avant",
        "janvier",
        "février",
        "fevrier",
        "mars",
        "avril",
        "mai",
        "juin",
        "juillet",
        "août",
        "aout",
        "septembre",
        "octobre",
        "novembre",
        "décembre",
        "decembre",
        "deux",
        "trois",
        "quatre",
        "cinq",
        "six",
        "sept",
        "huit",
        "one",
        "two",
        "three",
        "four",
        "five",
        "seven",
        "eight",
        "agenda",
        "calendrier",
        "calendar",
        "message",
        "messages",
        "imessage",
        "note",
        "notes",
        "vocale",
        "vocales",
        "enregistrement",
        "enregistrements",
        "tâche",
        "tâches",
        "tache",
        "taches",
        "projet",
        "projets",
        "agent",
        "agents",
        "document",
        "documents",
        "journal",
        "the",
        "and",
        "from",
        "what",
    }
)


@dataclass(frozen=True, slots=True)
class SQLProjectionAdapter(RetrievalAdapter):
    key: str
    source_type: str
    select_sql: str
    indexable: bool = True

    def search(self, request: RetrievalRequest, limit: int) -> list[KnowledgeDocument]:
        clauses: list[str] = []
        params: list[Any] = []
        tokens = _query_terms(request.effective_query)
        if tokens:
            matches = []
            for token in tokens[:12]:
                pattern = _like_pattern(token)
                matches.append(
                    "(title LIKE ? ESCAPE '\\' COLLATE NOCASE "
                    "OR searchable_text LIKE ? ESCAPE '\\' COLLATE NOCASE "
                    "OR summary LIKE ? ESCAPE '\\' COLLATE NOCASE "
                    "OR people_text LIKE ? ESCAPE '\\' COLLATE NOCASE)"
                )
                params.extend((pattern, pattern, pattern, pattern))
            clauses.append("(" + " OR ".join(matches) + ")")
        if request.person:
            pattern = _like_pattern(request.person)
            clauses.append(
                "(people_text LIKE ? ESCAPE '\\' COLLATE NOCASE "
                "OR title LIKE ? ESCAPE '\\' COLLATE NOCASE "
                "OR searchable_text LIKE ? ESCAPE '\\' COLLATE NOCASE)"
            )
            params.extend((pattern, pattern, pattern))
        if request.from_iso:
            clauses.append(
                "datetime(COALESCE(occurred_at, source_updated_at)) >= datetime(?)"
            )
            params.append(request.from_iso)
        if request.to_iso:
            clauses.append(
                "datetime(COALESCE(occurred_at, source_updated_at)) <= datetime(?)"
            )
            params.append(request.to_iso)

        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        result_limit = max(1, min(50, int(limit)))
        sql = (
            f"SELECT * FROM ({self.select_sql}) AS source{where} "
            "ORDER BY COALESCE(occurred_at, source_updated_at, '') DESC, _cursor DESC "
            "LIMIT ?"
        )
        with get_db() as conn:
            rows = conn.execute(sql, (*params, result_limit)).fetchall()
        return [self._to_document(row) for row in rows]

    def get(self, source_id: str) -> KnowledgeDocument | None:
        with get_db() as conn:
            row = conn.execute(
                f"SELECT * FROM ({self.select_sql}) AS source WHERE source_id = ? LIMIT 1",
                (str(source_id),),
            ).fetchone()
        return self._to_document(row) if row is not None else None

    def iter_batch(
        self, cursor: str | None, limit: int
    ) -> tuple[list[KnowledgeDocument], str | None]:
        # Les séries denses (usage écran, visites, etc.) restent interrogées
        # directement et ne sont pas transformées en milliers de fragments.
        if not self.indexable:
            return [], None
        current = max(0, int(cursor or 0))
        result_limit = max(1, min(1_000, int(limit)))
        with get_db() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM ({self.select_sql}) AS source
                WHERE _cursor > ?
                ORDER BY _cursor
                LIMIT ?
                """,
                (current, result_limit),
            ).fetchall()
        documents = [self._to_document(row) for row in rows]
        next_cursor = str(int(rows[-1]["_cursor"])) if rows else None
        return documents, next_cursor

    def _to_document(self, row: Any) -> KnowledgeDocument:
        value = dict(row)
        people = tuple(
            dict.fromkeys(
                part.strip()
                for part in re.split(r"[|\n]", str(value.get("people_text") or ""))
                if part.strip()
            )
        )
        metadata = {"adapter": self.key}
        for key, item in value.items():
            if key.startswith("meta_") and item is not None:
                metadata[key.removeprefix("meta_")] = item
        conversation_id = value.get("conversation_id")
        return KnowledgeDocument(
            source_type=self.source_type,
            source_id=str(value["source_id"]),
            title=str(value.get("title") or "").strip(),
            searchable_text=str(value.get("searchable_text") or "").strip(),
            summary=str(value.get("summary") or "").strip(),
            conversation_id=(
                int(conversation_id) if conversation_id is not None else None
            ),
            people=people,
            occurred_at=_optional_text(value.get("occurred_at")),
            source_updated_at=_optional_text(value.get("source_updated_at")),
            sensitivity=str(value.get("sensitivity") or "personal"),
            cloud_policy=str(value.get("cloud_policy") or "redact"),
            trust=str(value.get("trust") or "untrusted_stored_data"),
            metadata=metadata,
            indexable=self.indexable,
        )


def _table_adapter(
    key: str,
    source_type: str,
    from_sql: str,
    *,
    source_id: str,
    title: str,
    searchable_text: str,
    summary: str = "''",
    people_text: str = "''",
    occurred_at: str,
    source_updated_at: str | None = None,
    conversation_id: str = "NULL",
    sensitivity: str = "'personal'",
    cloud_policy: str = "'redact'",
    trust: str = "'untrusted_stored_data'",
    metadata: tuple[tuple[str, str], ...] = (),
    where: str = "",
    indexable: bool = True,
) -> SQLProjectionAdapter:
    """Construit une projection SQL statique au contrat commun du retrieval."""

    metadata_sql = "".join(
        f", {expression} AS meta_{name}" for name, expression in metadata
    )
    select_sql = f"""
        SELECT t.rowid AS _cursor, CAST({source_id} AS TEXT) AS source_id,
               COALESCE({title}, '') AS title,
               COALESCE({searchable_text}, '') AS searchable_text,
               COALESCE({summary}, '') AS summary,
               COALESCE({people_text}, '') AS people_text,
               {occurred_at} AS occurred_at,
               {source_updated_at or occurred_at} AS source_updated_at,
               {conversation_id} AS conversation_id,
               {sensitivity} AS sensitivity, {cloud_policy} AS cloud_policy,
               {trust} AS trust{metadata_sql}
        FROM {from_sql}
        {where}
    """
    return SQLProjectionAdapter(
        key=key,
        source_type=source_type,
        select_sql=select_sql,
        indexable=indexable,
    )


def _fine_grained_adapters() -> tuple[RetrievalAdapter, ...]:
    """Sources de domaine conservées séparément pour provenance et permissions."""

    adapters: list[RetrievalAdapter] = [
        _table_adapter(
            "notifications",
            "notification",
            "notifications t",
            source_id="t.id",
            title="COALESCE(NULLIF(t.title, ''), 'Notification')",
            searchable_text=(
                "TRIM(COALESCE(t.title, '') || ' ' || COALESCE(t.content, '') "
                "|| ' ' || COALESCE(t.source, ''))"
            ),
            summary="COALESCE(t.content, '')",
            occurred_at="t.created_at",
            metadata=(
                ("source", "t.source"),
                ("priority", "t.priority"),
                ("read", "t.read"),
            ),
        ),
        _table_adapter(
            "conversation_turns",
            "conversation_turn",
            (
                "conversation_turns t LEFT JOIN recordings r ON r.id = t.recording_id "
                "LEFT JOIN people p ON p.id = t.person_id"
            ),
            source_id="t.id",
            title=(
                "COALESCE(NULLIF(t.speaker_label, ''), 'Tour de conversation') "
                "|| ' — ' || COALESCE(NULLIF(r.label, ''), 'Enregistrement')"
            ),
            searchable_text=(
                "TRIM(COALESCE(t.text, '') || ' ' || COALESCE(t.speaker_label, '') "
                "|| ' ' || COALESCE(r.label, ''))"
            ),
            summary="COALESCE(t.text, '')",
            people_text="COALESCE(p.name, t.speaker_label, '')",
            occurred_at="COALESCE(r.created_at, t.created_at)",
            conversation_id="r.conversation_id",
            metadata=(
                ("recording_id", "t.recording_id"),
                ("speaker", "t.speaker_label"),
            ),
        ),
        _table_adapter(
            "user_notes",
            "note",
            "episodes t",
            source_id="t.id",
            title="'Note'",
            searchable_text=(
                "TRIM(COALESCE(t.content, '') || ' ' || COALESCE(t.summary, '') "
                "|| ' ' || COALESCE(t.tags, ''))"
            ),
            summary="COALESCE(t.summary, t.content, '')",
            occurred_at="t.created_at",
            metadata=(("importance", "t.importance"), ("tags", "t.tags")),
            where="WHERE t.agent = 'user'",
        ),
        _table_adapter(
            "journal",
            "journal",
            "jarvis_journal t",
            source_id="t.id",
            title="'Journal — ' || COALESCE(t.date, '')",
            searchable_text="COALESCE(t.entry, '')",
            summary="COALESCE(t.entry, '')",
            occurred_at="COALESCE(t.date, t.created_at)",
        ),
        _table_adapter(
            "life_context",
            "life_context",
            "life_context t",
            source_id="'context:' || t.id",
            title="COALESCE(NULLIF(t.context_type, ''), 'Contexte de vie')",
            searchable_text=(
                "TRIM(COALESCE(t.description, '') || ' ' || COALESCE(t.impact_on_mood, '') "
                "|| ' ' || COALESCE(t.impact_on_productivity, ''))"
            ),
            summary="COALESCE(t.description, '')",
            occurred_at="COALESCE(t.period_end, t.period_start, t.created_at)",
            metadata=(("active", "t.active"), ("context_type", "t.context_type")),
        ),
        _table_adapter(
            "life_profile",
            "life_context",
            "life_profile t",
            source_id="'profile:' || t.id",
            title="'Profil — ' || COALESCE(t.category, '')",
            searchable_text="COALESCE(t.content, '')",
            summary="COALESCE(t.content, '')",
            occurred_at="t.updated_at",
            metadata=(("category", "t.category"),),
        ),
        _table_adapter(
            "patterns",
            "pattern",
            "patterns t",
            source_id="t.id",
            title="'Pattern — ' || COALESCE(t.pattern_type, '')",
            searchable_text="COALESCE(t.description, '')",
            summary="COALESCE(t.description, '')",
            occurred_at="COALESCE(t.last_seen, t.first_seen)",
            trust="'derived_insight'",
            metadata=(("status", "t.status"), ("occurrences", "t.occurrences")),
        ),
        _table_adapter(
            "cross_insights",
            "insight",
            "cross_insights t",
            source_id="'cross:' || t.id",
            title="'Insight — ' || COALESCE(t.insight_type, '')",
            searchable_text=(
                "TRIM(COALESCE(t.content, '') || ' ' || COALESCE(t.evidence, '') "
                "|| ' ' || COALESCE(t.actionable, ''))"
            ),
            summary="COALESCE(t.content, '')",
            people_text="COALESCE(t.people_involved, '')",
            occurred_at="COALESCE(t.last_seen, t.first_seen)",
            trust="'derived_insight'",
            metadata=(("status", "t.status"), ("type", "t.insight_type")),
        ),
        _table_adapter(
            "message_insights",
            "insight",
            "message_insights t",
            source_id="'message:' || t.id",
            title="'Analyse de messages'",
            searchable_text="COALESCE(t.result_json, '')",
            summary="COALESCE(t.result_json, '')",
            occurred_at="t.created_at",
            trust="'derived_insight'",
            metadata=(
                ("message_count", "t.message_count"),
                ("acknowledged", "t.acknowledged"),
            ),
        ),
        _table_adapter(
            "daily_briefings",
            "briefing",
            "daily_briefings t",
            source_id="'daily:' || t.id",
            title="'Synthèse quotidienne — ' || COALESCE(t.date, '')",
            searchable_text=(
                "TRIM(COALESCE(t.morning_briefing, '') || ' ' "
                "|| COALESCE(t.evening_summary, ''))"
            ),
            summary="COALESCE(t.evening_summary, t.morning_briefing, '')",
            occurred_at="COALESCE(t.date, t.created_at)",
            trust="'derived_insight'",
        ),
        _table_adapter(
            "weekly_summaries",
            "briefing",
            "weekly_summaries t",
            source_id="'weekly:' || t.id",
            title="'Synthèse hebdomadaire — ' || COALESCE(t.week_start, '')",
            searchable_text=(
                "TRIM(COALESCE(t.summary, '') || ' ' || COALESCE(t.patterns_spotted, '') "
                "|| ' ' || COALESCE(t.recommendations, ''))"
            ),
            summary="COALESCE(t.summary, '')",
            occurred_at="COALESCE(t.week_start, t.created_at)",
            trust="'derived_insight'",
        ),
        _table_adapter(
            "commitments",
            "commitment",
            "commitments t",
            source_id="t.id",
            title="'Engagement' || CASE WHEN t.made_to IS NOT NULL THEN ' envers ' || t.made_to ELSE '' END",
            searchable_text=(
                "TRIM(COALESCE(t.content, '') || ' ' || COALESCE(t.made_to, '') "
                "|| ' ' || COALESCE(t.due_hint, '') || ' ' || COALESCE(t.status, ''))"
            ),
            summary="COALESCE(t.content, '')",
            people_text="COALESCE(t.made_to, '')",
            occurred_at="COALESCE(t.resolved_at, t.created_at)",
            metadata=(("status", "t.status"), ("source", "t.source")),
        ),
        _table_adapter(
            "places",
            "location",
            "places t",
            source_id="'place:' || t.id",
            title="COALESCE(NULLIF(t.name, ''), 'Lieu')",
            searchable_text=(
                "TRIM(COALESCE(t.name, '') || ' ' || COALESCE(t.category, '') "
                "|| ' ' || COALESCE(t.address, '') || ' ' || COALESCE(t.notes, ''))"
            ),
            summary="COALESCE(t.notes, t.address, '')",
            occurred_at="COALESCE(t.last_visit, t.created_at)",
            metadata=(("category", "t.category"), ("visit_count", "t.visit_count")),
        ),
        _table_adapter(
            "location_history_current",
            "location",
            "location_history t LEFT JOIN places p ON p.id = t.place_id",
            source_id="'point:' || t.id",
            title="'Position' || CASE WHEN p.name IS NOT NULL THEN ' — ' || p.name ELSE '' END",
            searchable_text=(
                "TRIM(COALESCE(p.name, '') || ' latitude ' || COALESCE(t.latitude, '') "
                "|| ' longitude ' || COALESCE(t.longitude, '') || ' ' "
                "|| COALESCE(t.source, ''))"
            ),
            summary="COALESCE(p.name, 'Position GPS')",
            occurred_at="t.created_at",
            metadata=(("place_id", "t.place_id"), ("accuracy", "t.accuracy")),
            indexable=False,
        ),
        _table_adapter(
            "visits",
            "location",
            "visits t LEFT JOIN places p ON p.id = t.place_id",
            source_id="'visit:' || t.id",
            title="'Visite — ' || COALESCE(p.name, 'Lieu inconnu')",
            searchable_text=(
                "TRIM(COALESCE(p.name, '') || ' ' || COALESCE(p.category, '') "
                "|| ' ' || COALESCE(t.notes, ''))"
            ),
            summary="COALESCE(t.notes, '')",
            occurred_at="COALESCE(t.arrived_at, t.created_at)",
            source_updated_at="COALESCE(t.departed_at, t.arrived_at, t.created_at)",
            metadata=(("place_id", "t.place_id"), ("duration_min", "t.duration_min")),
            indexable=False,
        ),
        _table_adapter(
            "location_patterns",
            "location",
            "location_patterns t LEFT JOIN places p ON p.id = t.place_id",
            source_id="'pattern:' || t.id",
            title="'Habitude de lieu — ' || COALESCE(p.name, t.pattern_type, '')",
            searchable_text="TRIM(COALESCE(t.description, '') || ' ' || COALESCE(p.name, ''))",
            summary="COALESCE(t.description, '')",
            occurred_at="COALESCE(t.last_seen, t.first_seen)",
            trust="'derived_insight'",
            metadata=(("status", "t.status"), ("occurrences", "t.occurrences")),
        ),
        _table_adapter(
            "mood_log",
            "wellbeing",
            "mood_log t",
            source_id="'mood:' || t.id",
            title="'Humeur ' || COALESCE(t.mood_score, '') || '/10'",
            searchable_text=(
                "TRIM(COALESCE(t.context, '') || ' ' || COALESCE(t.triggers, '') "
                "|| ' énergie ' || COALESCE(t.energy_level, ''))"
            ),
            summary="COALESCE(t.context, '')",
            occurred_at="t.created_at",
            metadata=(
                ("mood_score", "t.mood_score"),
                ("energy_level", "t.energy_level"),
            ),
        ),
        _table_adapter(
            "wellbeing_logs",
            "wellbeing",
            "wellbeing_logs t",
            source_id="'wellbeing:' || t.id",
            title="'Bien-être — ' || COALESCE(t.date, '')",
            searchable_text="COALESCE(t.journal_text, '')",
            summary="COALESCE(t.journal_text, '')",
            occurred_at="COALESCE(t.date, t.created_at)",
            metadata=(("rating", "t.rating"), ("source", "t.source")),
        ),
        _table_adapter(
            "fitness_weight_logs",
            "wellbeing",
            "fitness_weight_logs t",
            source_id="'weight:' || t.id",
            title="'Poids — ' || COALESCE(t.date, '')",
            searchable_text="TRIM(COALESCE(t.notes, '') || ' ' || COALESCE(t.weight_kg, '') || ' kg')",
            summary="COALESCE(t.notes, '')",
            occurred_at="COALESCE(t.date, t.created_at)",
            metadata=(("weight_kg", "t.weight_kg"),),
        ),
        _table_adapter(
            "meals",
            "wellbeing",
            "meals t",
            source_id="'meal:' || t.id",
            title="COALESCE(NULLIF(t.meal_type, ''), 'Repas') || ' — ' || COALESCE(t.date, '')",
            searchable_text=(
                "TRIM(COALESCE(t.description, '') || ' ' || COALESCE(t.items_json, '') "
                "|| ' ' || COALESCE(t.source, ''))"
            ),
            summary="COALESCE(t.description, '')",
            occurred_at="COALESCE(t.date, t.created_at)",
            metadata=(
                ("calories", "t.calories_estimate"),
                ("confidence", "t.confidence"),
            ),
        ),
        _table_adapter(
            "food_preferences",
            "wellbeing",
            "food_preferences t",
            source_id="'food-pref:' || t.id",
            title="'Préférence alimentaire — ' || COALESCE(t.key, '')",
            searchable_text="TRIM(COALESCE(t.key, '') || ' ' || COALESCE(t.value, ''))",
            summary="COALESCE(t.value, '')",
            occurred_at="t.updated_at",
            metadata=(("confidence", "t.confidence"), ("sample_size", "t.sample_size")),
        ),
        _table_adapter(
            "food_orders",
            "wellbeing",
            "food_orders t",
            source_id="'food-order:' || t.id",
            title="'Commande — ' || COALESCE(t.restaurant, '')",
            searchable_text=(
                "TRIM(COALESCE(t.restaurant, '') || ' ' || COALESCE(t.items_json, '') "
                "|| ' ' || COALESCE(t.status, '') || ' ' || COALESCE(t.delivery_status, ''))"
            ),
            summary="COALESCE(t.status, '')",
            occurred_at="COALESCE(t.delivered_at, t.created_at)",
            metadata=(("status", "t.status"), ("delivery_status", "t.delivery_status")),
        ),
        _table_adapter(
            "fitness_programs",
            "wellbeing",
            "fitness_programs t",
            source_id="'fitness-program:' || t.id",
            title="COALESCE(NULLIF(t.name, ''), 'Programme fitness')",
            searchable_text="TRIM(COALESCE(t.name, '') || ' ' || COALESCE(t.goal, ''))",
            summary="COALESCE(t.goal, '')",
            occurred_at="COALESCE(t.updated_at, t.created_at)",
            metadata=(
                ("active", "t.active"),
                ("weekly_min_sessions", "t.weekly_min_sessions"),
            ),
        ),
        _table_adapter(
            "fitness_program_sessions",
            "wellbeing",
            "fitness_program_sessions t",
            source_id="'fitness-session:' || t.id",
            title="COALESCE(NULLIF(t.title, ''), 'Séance fitness')",
            searchable_text=(
                "TRIM(COALESCE(t.title, '') || ' ' || COALESCE(t.description, '') "
                "|| ' ' || COALESCE(t.exercises_json, '') || ' ' || COALESCE(t.notes, ''))"
            ),
            summary="COALESCE(t.description, '')",
            occurred_at="COALESCE(t.updated_at, t.created_at)",
            metadata=(("program_id", "t.program_id"), ("active", "t.active")),
        ),
        _table_adapter(
            "fitness_session_progress",
            "wellbeing",
            "fitness_session_progress t",
            source_id="'fitness-progress:' || t.id",
            title="'Progression fitness — ' || COALESCE(t.date, '')",
            searchable_text=(
                "TRIM(COALESCE(t.status, '') || ' ' || COALESCE(t.exercise_results_json, '') "
                "|| ' ' || COALESCE(t.notes, ''))"
            ),
            summary="COALESCE(t.notes, t.status, '')",
            occurred_at="COALESCE(t.completed_at, t.date, t.created_at)",
            source_updated_at="COALESCE(t.updated_at, t.completed_at, t.created_at)",
            metadata=(("status", "t.status"), ("duration_min", "t.duration_min")),
        ),
        _table_adapter(
            "mood_signals",
            "wellbeing",
            "mood_signals t",
            source_id="'mood-signal:' || t.id",
            title="'Signaux d’humeur — ' || COALESCE(t.date, '')",
            searchable_text="TRIM(COALESCE(t.flags, '') || ' ' || COALESCE(t.deviation_pct, ''))",
            summary="COALESCE(t.flags, '')",
            occurred_at="COALESCE(t.date, t.created_at)",
            trust="'derived_insight'",
        ),
        _table_adapter(
            "app_usage",
            "activity",
            "app_usage t",
            source_id="'app:' || t.id",
            title="'Usage — ' || COALESCE(t.app, '')",
            searchable_text="TRIM(COALESCE(t.device, '') || ' ' || COALESCE(t.app, ''))",
            summary="COALESCE(t.app, '')",
            occurred_at="COALESCE(t.date, t.created_at)",
            metadata=(
                ("duration_seconds", "t.duration_seconds"),
                ("sessions", "t.session_count"),
            ),
            indexable=False,
        ),
        _table_adapter(
            "screen_activity",
            "activity",
            "screen_activity t",
            source_id="'screen:' || t.id",
            title="'Écran — ' || COALESCE(t.app, t.device, '')",
            searchable_text=(
                "TRIM(COALESCE(t.device, '') || ' ' || COALESCE(t.app, '') || ' ' "
                "|| COALESCE(t.activity, '') || ' ' || COALESCE(t.notable, ''))"
            ),
            summary="COALESCE(t.notable, t.activity, '')",
            occurred_at="t.created_at",
            metadata=(("mood", "t.mood"),),
            indexable=False,
        ),
        _table_adapter(
            "presence_sessions",
            "activity",
            "presence_sessions t",
            source_id="'presence:' || t.id",
            title="'Présence à domicile'",
            searchable_text="'présence domicile arrivée départ'",
            summary="''",
            occurred_at="t.arrived_at",
            source_updated_at="COALESCE(t.left_at, t.arrived_at)",
            metadata=(("duration_min", "t.duration_min"),),
            indexable=False,
        ),
        _table_adapter(
            "person_month_chapters",
            "person_month",
            "person_month_chapters t LEFT JOIN people p ON p.id = t.person_id",
            source_id="t.id",
            title=(
                "'Chapitre ' || COALESCE(p.name, '') || ' ' || COALESCE(t.year_month, '')"
            ),
            searchable_text=(
                "TRIM(COALESCE(p.name, '') || ' ' || COALESCE(t.year_month, '') || ' ' "
                "|| COALESCE(t.narrative, '') || ' ' || COALESCE(t.highlights_json, '') "
                "|| ' ' || COALESCE(t.mood_arc, ''))"
            ),
            summary="COALESCE(t.narrative, '')",
            people_text="COALESCE(p.name, '')",
            occurred_at="t.period_start_utc",
            source_updated_at="t.updated_at",
            sensitivity="'private'",
            cloud_policy="'redact'",
            metadata=(
                ("person_id", "t.person_id"),
                ("year_month", "t.year_month"),
                ("status", "t.status"),
            ),
        ),
        _table_adapter(
            "people_events",
            "people_event",
            "people_events t LEFT JOIN people p ON p.id = t.person_id",
            source_id="t.id",
            title="COALESCE(p.name, 'Personne') || ' — ' || COALESCE(t.event_type, 'événement')",
            searchable_text=(
                "TRIM(COALESCE(p.name, '') || ' ' || COALESCE(t.content, '') "
                "|| ' ' || COALESCE(t.lesson_learned, ''))"
            ),
            summary="COALESCE(t.content, '')",
            people_text="COALESCE(p.name, '')",
            occurred_at="t.created_at",
            metadata=(("person_id", "t.person_id"), ("event_type", "t.event_type")),
        ),
        _table_adapter(
            "relationship_profiles",
            "relationship",
            "relationship_profiles t LEFT JOIN people p ON p.id = t.person_id",
            source_id="t.id",
            title="'Relation — ' || COALESCE(p.name, t.handle, '')",
            searchable_text=(
                "TRIM(COALESCE(p.name, '') || ' ' || COALESCE(t.handle, '') || ' ' "
                "|| COALESCE(t.communication_style, '') || ' ' || COALESCE(t.topics, '') "
                "|| ' ' || COALESCE(t.sentiment, '') || ' ' || COALESCE(t.power_dynamic, ''))"
            ),
            summary="COALESCE(t.communication_style, '')",
            people_text="TRIM(COALESCE(p.name, '') || '|' || COALESCE(t.handle, ''))",
            occurred_at="COALESCE(t.last_analyzed, t.updated_at, t.created_at)",
            trust="'derived_insight'",
            metadata=(("person_id", "t.person_id"), ("trust_level", "t.trust_level")),
        ),
        _table_adapter(
            "relationship_events",
            "relationship_event",
            "relationship_events t LEFT JOIN people p ON p.id = t.person_id",
            source_id="t.id",
            title="COALESCE(p.name, 'Relation') || ' — ' || COALESCE(t.event_type, 'événement')",
            searchable_text=(
                "TRIM(COALESCE(p.name, '') || ' ' || COALESCE(t.summary, '') || ' ' "
                "|| COALESCE(t.impact_on_user, '') || ' ' || COALESCE(t.lessons, ''))"
            ),
            summary="COALESCE(t.summary, '')",
            people_text="COALESCE(p.name, '')",
            occurred_at="COALESCE(t.event_date, t.created_at)",
            metadata=(("person_id", "t.person_id"), ("event_type", "t.event_type")),
        ),
        _table_adapter(
            "school_documents_fine",
            "school_document",
            "school_documents t",
            source_id="t.id",
            title="COALESCE(NULLIF(t.title, ''), 'Document scolaire')",
            searchable_text="TRIM(COALESCE(t.title, '') || ' ' || COALESCE(t.content, ''))",
            summary="COALESCE(t.content, '')",
            occurred_at="t.created_at",
            metadata=(("doc_type", "t.doc_type"), ("subject_id", "t.subject_id")),
        ),
        _table_adapter(
            "conversation_documents_fine",
            "conversation_document",
            "conversation_documents t",
            source_id="t.id",
            title="'Document de conversation'",
            searchable_text=(
                "TRIM(COALESCE(t.summary, '') || ' ' || COALESCE(t.extracted_text, ''))"
            ),
            summary="COALESCE(t.summary, '')",
            occurred_at="t.created_at",
            conversation_id="t.conversation_id",
            cloud_policy="CASE WHEN t.cloud_consent = 1 THEN 'redact' ELSE 'local_only' END",
            metadata=(
                ("file_type", "t.file_type"),
                ("cloud_consent", "t.cloud_consent"),
            ),
        ),
        _table_adapter(
            "control_task_plans",
            "control_plan",
            "control_task_plans t",
            source_id="t.plan_id",
            title="'Plan v' || COALESCE(t.version, '') || ' — ' || COALESCE(t.objective, '')",
            searchable_text=(
                "TRIM(COALESCE(t.objective, '') || ' ' || COALESCE(t.summary, '') || ' ' "
                "|| COALESCE(t.context_understood, '') || ' ' || COALESCE(t.decision, '') "
                "|| ' ' || COALESCE(t.decision_comment, ''))"
            ),
            summary="COALESCE(t.summary, t.objective, '')",
            occurred_at="t.created_at",
            sensitivity="'internal'",
            metadata=(
                ("task_id", "t.task_id"),
                ("version", "t.version"),
                ("decision", "t.decision"),
            ),
        ),
        _table_adapter(
            "control_task_comments",
            "control_comment",
            "control_task_comments t",
            source_id="t.comment_id",
            title="'Commentaire — ' || COALESCE(t.author, '')",
            searchable_text="COALESCE(t.body, '')",
            summary="COALESCE(t.body, '')",
            people_text="COALESCE(t.author, '')",
            occurred_at="t.created_at",
            sensitivity="'internal'",
            metadata=(("task_id", "t.task_id"), ("run_id", "t.run_id")),
        ),
        _table_adapter(
            "control_task_reports",
            "control_report",
            "control_task_reports t",
            source_id="t.report_id",
            title="'Rapport v' || COALESCE(t.version, '') || ' — ' || COALESCE(t.result_status, '')",
            searchable_text="TRIM(COALESCE(t.summary, '') || ' ' || COALESCE(t.markdown, ''))",
            summary="COALESCE(t.summary, '')",
            occurred_at="t.created_at",
            sensitivity="'internal'",
            metadata=(("task_id", "t.task_id"), ("status", "t.result_status")),
        ),
        _table_adapter(
            "control_task_activity",
            "control_activity",
            "control_task_activity t",
            source_id="t.activity_id",
            title="COALESCE(NULLIF(t.event_type, ''), 'Activité de tâche')",
            searchable_text=(
                "TRIM(COALESCE(t.summary, '') || ' ' || COALESCE(t.agent_role, '') || ' ' "
                "|| COALESCE(t.phase, '') || ' ' || COALESCE(t.tool_name, '') || ' ' "
                "|| COALESCE(t.status, ''))"
            ),
            summary="COALESCE(t.summary, '')",
            occurred_at="t.created_at",
            sensitivity="'internal'",
            metadata=(
                ("task_id", "t.task_id"),
                ("run_id", "t.run_id"),
                ("status", "t.status"),
            ),
        ),
        _table_adapter(
            "agent_steps",
            "agent_step",
            "agent_steps t",
            source_id="t.step_id",
            title="COALESCE(NULLIF(t.title, ''), 'Étape agent')",
            searchable_text=(
                "TRIM(COALESCE(t.title, '') || ' ' || COALESCE(t.status, '') "
                "|| ' ' || COALESCE(t.error_code, ''))"
            ),
            summary="COALESCE(t.status, '')",
            occurred_at="COALESCE(t.started_at, t.finished_at)",
            source_updated_at="COALESCE(t.finished_at, t.started_at)",
            sensitivity="'internal'",
            metadata=(
                ("run_id", "t.run_id"),
                ("sequence", "t.sequence"),
                ("status", "t.status"),
            ),
        ),
        _table_adapter(
            "agent_approvals",
            "agent_approval",
            "agent_approvals t",
            source_id="t.approval_id",
            title="'Approbation — ' || COALESCE(t.action, t.tool, '')",
            searchable_text=(
                "TRIM(COALESCE(t.summary, '') || ' ' || COALESCE(t.action, '') || ' ' "
                "|| COALESCE(t.tool, '') || ' ' || COALESCE(t.decision, ''))"
            ),
            summary="COALESCE(t.summary, '')",
            occurred_at="COALESCE(t.decision_at, t.created_at)",
            sensitivity="'internal'",
            metadata=(
                ("run_id", "t.run_id"),
                ("decision", "t.decision"),
                ("scope", "t.scope"),
            ),
        ),
        _table_adapter(
            "agent_artifacts",
            "agent_artifact",
            "agent_artifacts t",
            source_id="t.artifact_id",
            title="'Artefact — ' || COALESCE(t.artifact_type, '')",
            searchable_text="TRIM(COALESCE(t.artifact_type, '') || ' ' || COALESCE(t.reference, ''))",
            summary="COALESCE(t.reference, '')",
            occurred_at="t.created_at",
            sensitivity="'internal'",
            cloud_policy=(
                "CASE WHEN t.visibility IN ('local_only', 'strict_local') "
                "THEN 'local_only' ELSE 'redact' END"
            ),
            metadata=(
                ("run_id", "t.run_id"),
                ("visibility", "t.visibility"),
                ("retention", "t.retention"),
            ),
        ),
        _table_adapter(
            "agentic_workflows",
            "agentic_workflow",
            "agentic_workflows t",
            source_id="t.id",
            title="'Workflow agentique — ' || COALESCE(t.status, '')",
            searchable_text=(
                "TRIM(COALESCE(t.user_message, '') || ' ' || COALESCE(t.final_synthesis, '') "
                "|| ' ' || COALESCE(t.status, ''))"
            ),
            summary="COALESCE(t.final_synthesis, '')",
            occurred_at="COALESCE(t.started_at, t.completed_at)",
            source_updated_at="COALESCE(t.completed_at, t.started_at)",
            conversation_id="t.conversation_id",
            sensitivity="'internal'",
            metadata=(("status", "t.status"), ("total_steps", "t.total_steps")),
        ),
        _table_adapter(
            "cursor_delegation_jobs",
            "cursor_job",
            "cursor_delegation_jobs t",
            source_id="COALESCE(t.job_id, CAST(t.id AS TEXT))",
            title="COALESCE(NULLIF(t.title, ''), 'Délégation Cursor')",
            searchable_text=(
                "TRIM(COALESCE(t.title, '') || ' ' || COALESCE(t.user_request, '') || ' ' "
                "|| COALESCE(t.status, '') || ' ' || COALESCE(t.repository, '') || ' ' "
                "|| COALESCE(t.error_message, ''))"
            ),
            summary="COALESCE(t.user_request, '')",
            occurred_at="COALESCE(t.started_at, t.created_at)",
            source_updated_at="COALESCE(t.finished_at, t.updated_at, t.created_at)",
            sensitivity="'internal'",
            metadata=(
                ("status", "t.status"),
                ("repository", "t.repository"),
                ("risk_level", "t.risk_level"),
            ),
        ),
        _table_adapter(
            "scheduler_job_runs",
            "scheduler_job",
            "scheduler_job_runs t",
            source_id="t.id",
            title="'Job planifié — ' || COALESCE(t.job_id, '')",
            searchable_text=(
                "TRIM(COALESCE(t.job_id, '') || ' ' || COALESCE(t.trigger, '') "
                "|| ' ' || COALESCE(t.status, ''))"
            ),
            summary="COALESCE(t.status, '')",
            occurred_at="COALESCE(t.started_at, t.finished_at)",
            source_updated_at="COALESCE(t.finished_at, t.started_at)",
            sensitivity="'internal'",
            metadata=(("status", "t.status"), ("duration_ms", "t.duration_ms")),
        ),
        _table_adapter(
            "work_sessions",
            "work_session",
            "work_sessions t",
            source_id="t.id",
            title="'Session — ' || COALESCE(t.app, t.device, '')",
            searchable_text=(
                "TRIM(COALESCE(t.device, '') || ' ' || COALESCE(t.app, '') "
                "|| ' ' || COALESCE(t.description, ''))"
            ),
            summary="COALESCE(t.description, '')",
            occurred_at="t.started_at",
            source_updated_at="COALESCE(t.ended_at, t.started_at)",
            sensitivity="'internal'",
            metadata=(("duration_min", "t.duration_min"),),
            indexable=False,
        ),
    ]
    return tuple(adapters)


def build_default_adapters() -> tuple[RetrievalAdapter, ...]:
    """Construit les adaptateurs sans conserver de connexion entre les appels."""

    return (
        SQLProjectionAdapter(
            "conversations",
            "conversation",
            """
            SELECT c.rowid AS _cursor, CAST(c.id AS TEXT) AS source_id,
                   COALESCE(NULLIF(c.title, ''), 'Conversation') AS title,
                   TRIM(COALESCE(c.summary, '') || ' ' || COALESCE(c.tags, ''))
                       AS searchable_text,
                   COALESCE(c.summary, '') AS summary, '' AS people_text,
                   c.started_at AS occurred_at,
                   COALESCE(c.title_updated_at, c.last_message_at, c.ended_at, c.started_at)
                       AS source_updated_at,
                   c.id AS conversation_id, 'personal' AS sensitivity,
                   'redact' AS cloud_policy, 'untrusted_stored_data' AS trust,
                   c.agent AS meta_agent, c.archived AS meta_archived,
                   c.pinned AS meta_pinned, c.message_count AS meta_message_count
            FROM conversations c
            """,
        ),
        SQLProjectionAdapter(
            "conversation_messages",
            "message",
            """
            SELECT m.rowid AS _cursor, CAST(m.id AS TEXT) AS source_id,
                   COALESCE(NULLIF(c.title, ''), 'Message de conversation') AS title,
                   m.content AS searchable_text, '' AS summary, '' AS people_text,
                   m.created_at AS occurred_at, m.created_at AS source_updated_at,
                   m.conversation_id AS conversation_id, 'personal' AS sensitivity,
                   'redact' AS cloud_policy, 'untrusted_stored_data' AS trust,
                   m.role AS meta_role, m.agent AS meta_agent, m.model AS meta_model
            FROM messages m
            LEFT JOIN conversations c ON c.id = m.conversation_id
            """,
        ),
        SQLProjectionAdapter(
            "email_cache",
            "email",
            """
            SELECT e.rowid AS _cursor, CAST(e.id AS TEXT) AS source_id,
                   COALESCE(NULLIF(e.subject, ''), 'Email') AS title,
                   TRIM(COALESCE(e.sender, '') || ' ' || COALESCE(e.subject, '') || ' '
                        || COALESCE(e.body, '') || ' ' || COALESCE(e.summary, ''))
                       AS searchable_text,
                   COALESCE(e.summary, '') AS summary,
                   COALESCE(e.sender, '') AS people_text,
                   COALESCE(NULLIF(e.received_at_utc, ''), NULLIF(e.received_at, ''),
                            e.processed_at, NULLIF(e.created_at, ''))
                       AS occurred_at,
                   COALESCE(NULLIF(e.source_updated_at_utc, ''),
                            NULLIF(e.created_at, ''), e.processed_at) AS source_updated_at,
                   NULL AS conversation_id, 'private' AS sensitivity,
                   'redact' AS cloud_policy, 'untrusted_stored_data' AS trust,
                   e.gmail_id AS meta_external_id, e.sender AS meta_sender,
                   e.account_id AS meta_account_id, e.mailbox_id AS meta_mailbox_id,
                   e.category AS meta_category, e.priority AS meta_priority,
                   e.is_read AS meta_is_read, e.action_needed AS meta_action_needed,
                   e.content_complete AS meta_content_complete,
                   e.ingestion_completeness AS meta_content_completeness
            FROM email_summaries e
            """,
        ),
        SQLProjectionAdapter(
            "calendar_cache",
            "calendar",
            """
            SELECT e.rowid AS _cursor, CAST(e.id AS TEXT) AS source_id,
                   e.title AS title,
                   TRIM(e.title || ' ' || COALESCE(e.calendar_name, '') || ' '
                        || COALESCE(e.location, '') || ' ' || COALESCE(e.notes, ''))
                       AS searchable_text,
                   COALESCE(e.notes, '') AS summary, '' AS people_text,
                   e.start_at AS occurred_at, e.updated_at AS source_updated_at,
                   NULL AS conversation_id, 'personal' AS sensitivity,
                   'redact' AS cloud_policy, 'untrusted_stored_data' AS trust,
                   e.external_id AS meta_external_id, e.calendar_name AS meta_calendar,
                   e.end_at AS meta_end_at, e.location AS meta_location,
                   e.is_all_day AS meta_is_all_day
            FROM calendar_events e
            """,
        ),
        SQLProjectionAdapter(
            "imessage_messages",
            "imessage",
            """
            SELECT m.rowid AS _cursor, CAST(m.id AS TEXT) AS source_id,
                   COALESCE(NULLIF(ci.display_name, ''), NULLIF(c.display_name, ''),
                            NULLIF(h.display_name, ''),
                            NULLIF(h.handle, ''), 'iMessage')
                       AS title,
                   TRIM(COALESCE(ci.display_name, '') || ' ' ||
                        COALESCE(c.display_name, '') || ' ' ||
                        COALESCE(h.display_name, '') || ' ' ||
                        COALESCE(h.handle, '') || ' ' || COALESCE(m.text, '') || ' ' ||
                        COALESCE((
                            SELECT GROUP_CONCAT(
                                TRIM(COALESCE(a.transfer_name, '') || ' ' ||
                                     COALESCE(a.filename, '') || ' ' ||
                                     COALESCE(a.mime_type, '')),
                                ' '
                            )
                            FROM imessage_message_attachments ma
                            JOIN imessage_attachments a ON a.id = ma.attachment_id
                            WHERE ma.message_id = m.id
                        ), '') || ' ' ||
                        COALESCE((
                            SELECT GROUP_CONCAT('reaction ' || r.reaction_type, ' ')
                            FROM imessage_reactions r WHERE r.message_id = m.id
                        ), ''))
                       AS searchable_text,
                   '' AS summary,
                   TRIM(COALESCE(ci.display_name, '') || '|' ||
                        COALESCE(c.display_name, '') || '|' ||
                        COALESCE(h.display_name, '') || '|' || COALESCE(h.handle, ''))
                       AS people_text,
                   COALESCE(m.occurred_at_utc, m.created_at) AS occurred_at,
                   COALESCE(m.source_updated_at_utc, m.occurred_at_utc, m.created_at)
                       AS source_updated_at,
                   NULL AS conversation_id, 'private' AS sensitivity,
                   'redact' AS cloud_policy, 'untrusted_stored_data' AS trust,
                   m.guid AS meta_guid, m.date AS meta_apple_date,
                   m.is_from_me AS meta_is_from_me, m.is_read AS meta_is_read,
                   c.chat_identifier AS meta_chat_identifier,
                   c.display_name AS meta_chat_name, h.handle AS meta_handle,
                   ci.display_name AS meta_contact_name,
                   ci.person_id AS meta_person_id,
                   m.content_complete AS meta_content_complete,
                   m.ingestion_completeness AS meta_ingestion_completeness,
                   (SELECT COUNT(*) FROM imessage_message_attachments ma
                    WHERE ma.message_id = m.id) AS meta_attachment_count,
                   (SELECT COUNT(*) FROM imessage_reactions r
                    WHERE r.message_id = m.id) AS meta_reaction_count
            FROM imessage_messages m
            LEFT JOIN imessage_handles h ON h.id = m.handle_id
            LEFT JOIN imessage_chats c ON c.id = m.chat_id
            LEFT JOIN contact_identities ci
              ON ci.id = h.contact_identity_id
              OR (h.contact_identity_id IS NULL
              AND ci.identity_type = CASE
                    WHEN INSTR(h.handle, '@') > 0 THEN 'email' ELSE 'phone'
                 END
             AND ci.normalized_value = CASE
                    WHEN INSTR(h.handle, '@') > 0 THEN LOWER(TRIM(h.handle, '<>'))
                    ELSE REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(
                         h.handle, ' ', ''), '-', ''), '(', ''), ')', ''), '.', '')
                 END)
            WHERE COALESCE(m.text, '') <> ''
               OR EXISTS (
                    SELECT 1 FROM imessage_message_attachments ma
                    WHERE ma.message_id = m.id
               )
               OR EXISTS (
                    SELECT 1 FROM imessage_reactions r WHERE r.message_id = m.id
               )
            """,
        ),
        SQLProjectionAdapter(
            "recordings",
            "recording",
            """
            SELECT r.rowid AS _cursor, CAST(r.id AS TEXT) AS source_id,
                   COALESCE(NULLIF(r.title, ''), NULLIF(r.label, ''), 'Note vocale')
                       AS title,
                   TRIM(COALESCE(r.transcription, '') || ' ' || COALESCE(r.summary, '')
                        || ' ' || COALESCE(r.synthesis, '') || ' '
                        || COALESCE(r.actions_taken, '') || ' '
                        || COALESCE((SELECT GROUP_CONCAT(t.text, ' ')
                                     FROM conversation_turns t
                                     WHERE t.recording_id = r.id), '')) AS searchable_text,
                   COALESCE(r.summary, '') AS summary,
                   COALESCE((SELECT GROUP_CONCAT(DISTINCT COALESCE(p.name, t.speaker_label))
                             FROM conversation_turns t
                             LEFT JOIN people p ON p.id = t.person_id
                             WHERE t.recording_id = r.id), '') AS people_text,
                   r.created_at AS occurred_at, r.created_at AS source_updated_at,
                   r.conversation_id AS conversation_id, 'private' AS sensitivity,
                   'redact' AS cloud_policy, 'untrusted_stored_data' AS trust,
                   r.duration_seconds AS meta_duration_seconds,
                   r.label AS meta_label, r.audio_size_kb AS meta_audio_size_kb
            FROM recordings r
            """,
        ),
        SQLProjectionAdapter(
            "episodes",
            "episode",
            """
            SELECT e.rowid AS _cursor, CAST(e.id AS TEXT) AS source_id,
                   COALESCE(NULLIF(e.summary, ''), 'Note') AS title,
                   TRIM(e.content || ' ' || COALESCE(e.summary, '') || ' '
                        || COALESCE(e.tags, '')) AS searchable_text,
                   COALESCE(e.summary, '') AS summary, '' AS people_text,
                   e.created_at AS occurred_at, e.created_at AS source_updated_at,
                   NULL AS conversation_id, 'personal' AS sensitivity,
                   'redact' AS cloud_policy, 'untrusted_stored_data' AS trust,
                   e.agent AS meta_agent, e.importance AS meta_importance,
                   e.tags AS meta_tags
            FROM episodes e
            """,
        ),
        SQLProjectionAdapter(
            "user_facts",
            "fact",
            """
            SELECT f.rowid AS _cursor, CAST(f.id AS TEXT) AS source_id,
                   f.category AS title, f.content AS searchable_text,
                   f.content AS summary, '' AS people_text,
                   f.created_at AS occurred_at, f.updated_at AS source_updated_at,
                   NULL AS conversation_id, 'personal' AS sensitivity,
                   'redact' AS cloud_policy, 'untrusted_stored_data' AS trust,
                   f.source AS meta_source, f.confidence AS meta_confidence
            FROM user_facts f
            WHERE f.is_current = 1
            """,
        ),
        SQLProjectionAdapter(
            "school_documents",
            "document",
            """
            SELECT d.rowid AS _cursor, 'school:' || CAST(d.id AS TEXT) AS source_id,
                   d.title AS title, COALESCE(d.content, '') AS searchable_text,
                   '' AS summary, '' AS people_text,
                   d.created_at AS occurred_at, d.created_at AS source_updated_at,
                   NULL AS conversation_id, 'private' AS sensitivity,
                   'redact' AS cloud_policy, 'untrusted_stored_data' AS trust,
                   'school' AS meta_document_kind, d.doc_type AS meta_file_type,
                   d.subject_id AS meta_subject_id
            FROM school_documents d
            """,
        ),
        SQLProjectionAdapter(
            "conversation_documents",
            "document",
            """
            SELECT d.rowid AS _cursor,
                   'conversation:' || CAST(d.id AS TEXT) AS source_id,
                   'Document de conversation' AS title,
                   TRIM(COALESCE(d.extracted_text, '') || ' ' || COALESCE(d.summary, ''))
                       AS searchable_text,
                   COALESCE(d.summary, '') AS summary, '' AS people_text,
                   d.created_at AS occurred_at, d.created_at AS source_updated_at,
                   d.conversation_id AS conversation_id, 'private' AS sensitivity,
                    CASE WHEN d.cloud_consent = 1 THEN 'redact' ELSE 'local_only' END
                       AS cloud_policy,
                   'untrusted_stored_data' AS trust,
                   'conversation' AS meta_document_kind,
                   d.file_type AS meta_file_type, d.file_size AS meta_file_size,
                   d.cloud_consent AS meta_cloud_consent
            FROM conversation_documents d
            """,
        ),
        SQLProjectionAdapter(
            "people",
            "person",
            """
            SELECT p.rowid AS _cursor, CAST(p.id AS TEXT) AS source_id,
                   p.name AS title,
                   TRIM(p.name || ' ' || COALESCE(p.relationship, '') || ' '
                        || COALESCE(p.personality_notes, '') || ' '
                        || COALESCE(p.dynamics, '') || ' ' || COALESCE(p.patterns, '')
                        || ' ' || COALESCE(p.ai_description, '') || ' '
                        || COALESCE((SELECT GROUP_CONCAT(pe.content, ' ')
                                     FROM people_events pe WHERE pe.person_id = p.id), '')
                        || ' ' || COALESCE((SELECT GROUP_CONCAT(re.summary, ' ')
                                            FROM relationship_events re
                                            WHERE re.person_id = p.id), '')) AS searchable_text,
                   COALESCE(p.ai_description, '') AS summary, p.name AS people_text,
                   COALESCE(p.last_mentioned, p.created_at) AS occurred_at,
                   COALESCE(p.timeline_updated_at, p.last_mentioned, p.created_at)
                       AS source_updated_at,
                   NULL AS conversation_id, 'private' AS sensitivity,
                   'redact' AS cloud_policy, 'untrusted_stored_data' AS trust,
                   p.relationship AS meta_relationship,
                   p.imessage_count AS meta_imessage_count,
                   p.birthday AS meta_birthday
            FROM people p
            """,
        ),
        SQLProjectionAdapter(
            "tasks",
            "task",
            """
            SELECT t.rowid AS _cursor, CAST(t.id AS TEXT) AS source_id,
                   t.title AS title,
                   TRIM(t.title || ' ' || COALESCE(t.description, '') || ' '
                        || COALESCE(t.category, '') || ' ' || COALESCE(t.status, ''))
                       AS searchable_text,
                   COALESCE(t.description, '') AS summary, '' AS people_text,
                   COALESCE(t.due_date, t.created_at) AS occurred_at,
                   COALESCE(t.completed_at, t.created_at) AS source_updated_at,
                   NULL AS conversation_id, 'personal' AS sensitivity,
                   'redact' AS cloud_policy, 'untrusted_stored_data' AS trust,
                   t.status AS meta_status, t.priority AS meta_priority,
                   t.due_date AS meta_due_at, t.category AS meta_category
            FROM tasks t
            """,
        ),
        SQLProjectionAdapter(
            "control_tasks",
            "control_task",
            """
            SELECT t.rowid AS _cursor, t.task_id AS source_id, t.title AS title,
                   TRIM(t.title || ' ' || COALESCE(t.description, '') || ' '
                        || COALESCE(t.source_excerpt, '') || ' '
                        || COALESCE(t.current_phase, '') || ' '
                        || COALESCE(t.result_status, '')) AS searchable_text,
                   COALESCE(t.description, '') AS summary, '' AS people_text,
                   COALESCE(t.due_at, t.created_at) AS occurred_at,
                   t.updated_at AS source_updated_at,
                   CASE WHEN t.conversation_id GLOB '[0-9]*'
                        THEN CAST(t.conversation_id AS INTEGER) ELSE NULL END
                       AS conversation_id,
                   'personal' AS sensitivity, 'redact' AS cloud_policy,
                   'untrusted_stored_data' AS trust,
                   t.status AS meta_status, t.priority AS meta_priority,
                   t.project_id AS meta_project_id,
                   t.attention_required AS meta_attention_required,
                   t.progress AS meta_progress
            FROM control_tasks t
            """,
        ),
        SQLProjectionAdapter(
            "dev_projects",
            "project",
            """
            SELECT p.rowid AS _cursor, CAST(p.id AS TEXT) AS source_id,
                   p.name AS title,
                   TRIM(p.name || ' ' || p.slug || ' ' || COALESCE(p.project_type, '')
                        || ' ' || COALESCE(p.status, '')) AS searchable_text,
                   COALESCE(p.project_type, '') AS summary, '' AS people_text,
                   p.created_at AS occurred_at, p.updated_at AS source_updated_at,
                   NULL AS conversation_id, 'internal' AS sensitivity,
                   'redact' AS cloud_policy, 'untrusted_stored_data' AS trust,
                   p.slug AS meta_slug, p.status AS meta_status,
                   p.project_type AS meta_project_type
            FROM dev_projects p
            """,
        ),
        SQLProjectionAdapter(
            "agent_runs",
            "agent_run",
            """
            SELECT r.rowid AS _cursor, r.run_id AS source_id, r.title AS title,
                   TRIM(r.title || ' ' || COALESCE(r.category, '') || ' '
                        || COALESCE(r.status, '') || ' ' || COALESCE(r.phase, '')
                        || ' ' || COALESCE(r.origin, '')) AS searchable_text,
                   TRIM(COALESCE(r.status, '') || ' / ' || COALESCE(r.phase, ''))
                       AS summary,
                   '' AS people_text, r.created_at AS occurred_at,
                   r.updated_at AS source_updated_at,
                   CASE WHEN r.conversation_id GLOB '[0-9]*'
                        THEN CAST(r.conversation_id AS INTEGER) ELSE NULL END
                       AS conversation_id,
                   'internal' AS sensitivity, 'redact' AS cloud_policy,
                   'untrusted_stored_data' AS trust,
                   r.status AS meta_status, r.phase AS meta_phase,
                   r.category AS meta_category, r.origin AS meta_origin,
                   r.runtime_id AS meta_runtime_id
            FROM agent_runs r
            """,
        ),
    ) + _fine_grained_adapters()


def _query_terms(value: str) -> list[str]:
    tokens = []
    for token in _WORD_RE.findall(str(value).casefold()):
        if len(token) < 2 or token.isdigit() or token in _STOPWORDS:
            continue
        if token not in tokens:
            tokens.append(token)
    return tokens


def _like_pattern(value: str) -> str:
    escaped = str(value).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None
