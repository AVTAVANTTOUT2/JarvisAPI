"""Agent JOURNAL — entrées de journal intime + extraction structurée.

Le prompt `prompts/journal.txt` demande à Claude de :
  1. Répondre humainement (2-3 phrases)
  2. Joindre un bloc ```json``` avec mood, energy, people_mentioned,
     topics, key_insights, pattern_match, action_items

On parse ce JSON pour alimenter mood_log, people, episodes, patterns, tasks.
TOUJOURS le modèle principal en mode standard — extraction fine, pas de rédaction longue.
"""

import asyncio
import json
import logging
import re
from typing import AsyncGenerator

import config
from agents import BaseAgent
from agents.display_text import finalize_assistant_display_text
from database import (
    add_people_event,
    create_task,
    find_or_create_pattern,
    get_active_patterns,
    get_all_people,
    save_episode,
    save_mood,
    upsert_person,
)

logger = logging.getLogger(__name__)

JSON_BLOCK_RE = re.compile(r"```json\s*\n(.*?)\n```", re.DOTALL)
STREAM_CHUNK_SIZE = 20


def _format_people(people: list[dict], max_items: int = 15) -> str:
    if not people:
        return "(aucune personne enregistrée)"
    return "\n".join(
        f"- {p['name']} ({p.get('relationship') or '?'})" for p in people[:max_items]
    )


def _format_patterns(patterns: list[dict]) -> str:
    if not patterns:
        return "(aucun pattern actif)"
    return "\n".join(
        f"- {p['description']} ×{p['occurrences']}" for p in patterns[:10]
    )


class JournalAgent(BaseAgent):
    """Reçoit du texte libre, répond brièvement, extrait des données structurées."""

    name = "journal"
    description = "Journal intime — extraction d'insights"
    model = config.DEEPSEEK_MAIN_MODEL

    def _enrich_context(self, context: dict | None) -> dict:
        ctx = dict(context or {})
        try:
            people = get_all_people()
            patterns = get_active_patterns()
        except Exception as e:
            logger.error(f"[journal] enrich : {e}")
            people, patterns = [], []
        ctx["people_context"] = _format_people(people)
        ctx["patterns_context"] = _format_patterns(patterns)
        ctx.setdefault("user_name", config.USER_NAME)
        ctx.setdefault("memory_context", "")
        ctx.setdefault("life_profile", "")
        return ctx

    async def handle(self, user_message: str, conversation_id: int = None,
                     context: dict = None) -> dict:
        ctx = self._enrich_context(context)
        result = await self._call_claude(
            user_message,
            conversation_id=conversation_id,
            context=ctx,
            persist=False,
            strip_fences=False,
        )
        raw = result.get("response", "")
        extracted = self._process_journal_data(raw)
        # Conserver ```action``` pour le pipeline (comme school) ; le JSON journal
        # est déjà consommé par _process_journal_data.
        response_for_pipeline = re.sub(
            r"```json\s*\n?.*?```", "", raw, flags=re.DOTALL | re.IGNORECASE
        ).strip() or finalize_assistant_display_text(raw)
        result["response"] = response_for_pipeline
        if extracted:
            result["extracted"] = extracted
        return result

    async def handle_stream(self, user_message: str, conversation_id: int = None,
                            context: dict = None) -> AsyncGenerator[dict, None]:
        yield {"type": "classification", "agent": self.name}

        ctx = self._enrich_context(context)
        result = await self._call_claude(
            user_message,
            conversation_id=conversation_id,
            context=ctx,
            persist=False,
            strip_fences=False,
        )
        raw = result.get("response", "")
        extracted = self._process_journal_data(raw)
        display = finalize_assistant_display_text(raw)

        for i in range(0, len(display), STREAM_CHUNK_SIZE):
            yield {"type": "chunk", "content": display[i:i + STREAM_CHUNK_SIZE]}
            await asyncio.sleep(0.01)

        yield {
            "type": "done",
            "agent": self.name,
            "model": result.get("model"),
            "tokens_in": result.get("tokens_in", 0),
            "tokens_out": result.get("tokens_out", 0),
            "cost": result.get("cost", 0.0),
            "content": display,
        }

        if extracted:
            yield {"type": "journal_extracted", "data": extracted}

    def _process_journal_data(self, response: str) -> dict | None:
        """Parse le bloc ```json``` et alimente mood/people/episodes/patterns/tasks."""
        match = JSON_BLOCK_RE.search(response or "")
        if not match:
            logger.info("[journal] Aucun bloc ```json``` trouvé")
            return None

        try:
            data = json.loads(match.group(1).strip())
        except json.JSONDecodeError as e:
            logger.warning(f"[journal] JSON invalide : {e}")
            return None

        mood = data.get("mood")
        energy = data.get("energy")
        people_mentioned = data.get("people_mentioned") or []
        topics = data.get("topics") or []
        key_insights = data.get("key_insights") or []
        pattern_match = data.get("pattern_match")
        action_items = data.get("action_items") or []

        # 1. Mood
        if isinstance(mood, (int, float)) and isinstance(energy, (int, float)):
            try:
                topics_str = ", ".join(topics) if topics else None
                save_mood(int(mood), int(energy), context=topics_str)
                logger.info(f"[journal] Mood sauvé : {mood}/10, énergie {energy}/10")
            except Exception as e:
                logger.error(f"[journal] save_mood : {e}")

        # 2. People
        for person in people_mentioned:
            if not isinstance(person, dict):
                continue
            name = person.get("name")
            if not name:
                continue
            try:
                upsert_person(name)
                ctx = person.get("context") or person.get("sentiment")
                if ctx:
                    add_people_event(name, event_type="journal_mention", content=ctx)
            except Exception as e:
                logger.error(f"[journal] people {name} : {e}")

        # 3. Episodes (insights)
        for insight in key_insights:
            if not isinstance(insight, str) or not insight.strip():
                continue
            try:
                save_episode(
                    agent="journal", content=insight,
                    importance=6, tags=topics,
                )
            except Exception as e:
                logger.error(f"[journal] save_episode : {e}")

        # 4. Pattern
        if isinstance(pattern_match, str) and pattern_match.strip():
            try:
                find_or_create_pattern(pattern_match.strip(), pattern_type="journal")
            except Exception as e:
                logger.error(f"[journal] pattern : {e}")

        # 5. Tâches
        for item in action_items:
            if not isinstance(item, str) or not item.strip():
                continue
            try:
                create_task(title=item.strip(), category="perso")
            except Exception as e:
                logger.error(f"[journal] create_task : {e}")

        logger.info(
            f"[journal] Extraction OK : mood={mood}, people={len(people_mentioned)}, "
            f"insights={len(key_insights)}, pattern={'oui' if pattern_match else 'non'}, "
            f"tasks={len(action_items)}"
        )
        return data


journal_agent = JournalAgent()
