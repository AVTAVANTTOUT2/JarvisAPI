"""Agent MÉMOIRE — système nerveux transversal.

Ne parle JAMAIS à l'utilisateur. Appelé en background après chaque conversation
significative pour :
  1. Décider si l'échange mérite d'être mémorisé
  2. Extraire les updates structurées (life_profile, people, mood, patterns, tasks)
  3. Détecter des patterns en croisant avec la mémoire existante

Modèle : Haiku (rapide + pas cher) pour le traitement courant, Sonnet pour
le résumé hebdomadaire.
"""

import json
import logging
import re
from datetime import datetime, timedelta
from typing import AsyncGenerator

import config
import llm
from agents import BaseAgent
from database import (
    add_cross_insight,
    add_fact,
    add_life_context,
    add_life_profile_entry,
    add_people_event,
    add_relationship_event,
    create_task,
    find_or_create_pattern,
    get_pattern,
    get_active_patterns,
    get_all_people,
    get_conversation_history,
    get_recent_episodes,
    get_recent_moods,
    get_weekly_episodes,
    save_episode,
    save_mood,
    save_weekly_summary,
    upsert_person,
    upsert_relationship_profile,
)

logger = logging.getLogger(__name__)

JSON_BLOCK_RE = re.compile(r"```json\s*\n(.*?)\n```", re.DOTALL)
MIN_MESSAGES_FOR_PROCESSING = 3


def _format_episodes_for_memory(episodes: list[dict]) -> str:
    if not episodes:
        return "(aucun épisode)"
    return "\n".join(
        f"- [{e['agent']}] {(e.get('summary') or e['content'])[:120]}"
        for e in episodes[:10]
    )


def _format_patterns_for_memory(patterns: list[dict]) -> str:
    if not patterns:
        return "(aucun pattern actif)"
    return "\n".join(
        f"- {p['description']} ×{p['occurrences']}" for p in patterns[:10]
    )


def _format_people_list(people: list[dict]) -> str:
    if not people:
        return "(aucune personne)"
    return ", ".join(p["name"] for p in people[:30])


class MemoryAgent(BaseAgent):
    """Agent transversal silencieux. Appelé via `process_conversation()`."""

    name = "memory"
    description = "Mémoire transversale — extraction et patterns"
    model = config.DEEPSEEK_FAST_MODEL
    inject_persona = False  # silencieux, sortie consommée par le code (JSON)

    def _enrich_context(self, context: dict | None) -> dict:
        ctx = dict(context or {})
        try:
            episodes = get_recent_episodes(limit=10)
            patterns = get_active_patterns()
            people = get_all_people()
        except Exception as e:
            logger.error(f"[memory] enrich : {e}")
            episodes, patterns, people = [], [], []
        ctx["episodes_context"] = _format_episodes_for_memory(episodes)
        ctx["patterns_context"] = _format_patterns_for_memory(patterns)
        ctx["people_list"] = _format_people_list(people)
        return ctx

    async def handle(self, user_message: str, conversation_id: int = None,
                     context: dict = None) -> dict:
        """`user_message` est ici un RÉSUMÉ de l'échange, pas un message utilisateur."""
        ctx = self._enrich_context(context)
        system = self.build_system_prompt(ctx)

        try:
            result = await llm.chat(
                messages=[{"role": "user", "content": user_message}],
                model=self.model,
                system=system,
                max_tokens=1500,
                temperature=0.3,
            )
        except Exception as e:
            logger.error(f"[memory] LLM call : {e}")
            return {"response": "", "error": str(e), "agent": self.name,
                    "model": self.model, "tokens_in": 0, "tokens_out": 0, "cost": 0.0}

        response = result.get("content", "")
        parsed = self._parse_and_apply(response)

        if parsed and parsed.get("pattern_desktop_notify"):
            try:
                from integrations.notifications_macos import mac_notifier

                msg = str(parsed.get("pattern_alert") or "Pattern récurrent détecté.")[:200]
                if config.DESKTOP_NOTIFICATIONS and mac_notifier.is_available():
                    await mac_notifier.notify(
                        title="JARVIS — Pattern détecté",
                        message=msg,
                        sound="Ping",
                    )
            except Exception as e:
                logger.exception("[memory] notification macOS pattern : %s", e)

        return {
            "response": response,
            "agent": self.name,
            "model": result["model"],
            "tokens_in": result["tokens_in"],
            "tokens_out": result["tokens_out"],
            "cost": result["cost"],
            "applied": parsed,
        }

    async def handle_stream(self, user_message: str, conversation_id: int = None,
                            context: dict = None) -> AsyncGenerator[dict, None]:
        # L'agent mémoire ne stream pas — il fonctionne en background.
        # On expose handle_stream pour compat avec le registry / orchestrateur,
        # mais on n'émet qu'un seul event done.
        result = await self.handle(user_message, conversation_id, context)
        yield {"type": "done", "agent": self.name, "model": result.get("model"),
               "tokens_in": result.get("tokens_in", 0),
               "tokens_out": result.get("tokens_out", 0),
               "cost": result.get("cost", 0.0)}

    def _parse_and_apply(self, response: str) -> dict | None:
        """Parse le JSON et applique les updates en DB. Retourne ce qui a été appliqué."""
        match = JSON_BLOCK_RE.search(response or "")
        if not match:
            logger.info("[memory] Pas de bloc ```json``` dans la réponse")
            return None

        try:
            data = json.loads(match.group(1).strip())
        except json.JSONDecodeError as e:
            logger.warning(f"[memory] JSON invalide : {e}")
            return None

        applied = {"episode": False, "life_profile": 0, "people": 0,
                   "mood": False, "patterns": 0, "tasks": 0,
                   "facts": 0, "life_context": False,
                   "pattern_alert": data.get("pattern_alert")}

        # 1. Episode
        if data.get("should_store"):
            ep = data.get("episode") or {}
            try:
                save_episode(
                    agent="memory",
                    content=ep.get("summary", "(résumé vide)"),
                    summary=ep.get("summary"),
                    importance=int(ep.get("importance", 5)),
                    tags=ep.get("tags") or [],
                )
                applied["episode"] = True
            except Exception as e:
                logger.error(f"[memory] save_episode : {e}")

        updates = data.get("updates") or {}

        # 2. Life profile
        for entry in updates.get("life_profile") or []:
            if not isinstance(entry, dict):
                continue
            if entry.get("action") == "add":
                cat = entry.get("category")
                content = entry.get("content")
                if cat and content:
                    try:
                        add_life_profile_entry(cat, content)
                        applied["life_profile"] += 1
                    except Exception as e:
                        logger.error(f"[memory] life_profile : {e}")

        # 3. People
        for entry in updates.get("people") or []:
            if not isinstance(entry, dict):
                continue
            name = entry.get("name")
            if not name:
                continue
            action = entry.get("action")
            try:
                if action == "update":
                    field = entry.get("field")
                    value = entry.get("value")
                    if field == "last_mentioned" or value == "now":
                        upsert_person(name, last_mentioned=datetime.now().isoformat())
                    elif field and value:
                        upsert_person(name, **{field: value})
                    else:
                        upsert_person(name)
                    applied["people"] += 1
                elif action == "add_event":
                    add_people_event(
                        name,
                        event_type=entry.get("event_type", "note"),
                        content=entry.get("content", ""),
                        lesson_learned=entry.get("lesson_learned"),
                    )
                    applied["people"] += 1
                else:
                    upsert_person(name)
                    applied["people"] += 1
            except Exception as e:
                logger.error(f"[memory] people {name} : {e}")

        # 4. Mood
        mood_data = updates.get("mood") or {}
        if isinstance(mood_data, dict) and "score" in mood_data and "energy" in mood_data:
            try:
                save_mood(
                    int(mood_data["score"]),
                    int(mood_data["energy"]),
                    context=mood_data.get("context"),
                    triggers=mood_data.get("triggers"),
                )
                applied["mood"] = True
            except Exception as e:
                logger.error(f"[memory] mood : {e}")

        # 5. Patterns
        for p in updates.get("patterns") or []:
            if not isinstance(p, dict):
                continue
            desc = p.get("description")
            if not desc:
                continue
            try:
                pid = find_or_create_pattern(desc, pattern_type=p.get("type", "behavioral"))
                pat = get_pattern(pid)
                occ = int(pat.get("occurrences") or 0) if pat else 0
                if occ == 3 and data.get("pattern_alert"):
                    applied["pattern_desktop_notify"] = True
                applied["patterns"] += 1
            except Exception as e:
                logger.exception("[memory] pattern : %s", e)

        # 6. Tâches
        for t in updates.get("tasks") or []:
            if isinstance(t, str) and t.strip():
                try:
                    create_task(title=t.strip(), category="perso")
                    applied["tasks"] += 1
                except Exception as e:
                    logger.error(f"[memory] task : {e}")
            elif isinstance(t, dict) and t.get("title"):
                try:
                    create_task(
                        title=t["title"],
                        priority=t.get("priority", "medium"),
                        category=t.get("category", "perso"),
                        due_date=t.get("due_date"),
                    )
                    applied["tasks"] += 1
                except Exception as e:
                    logger.error(f"[memory] task : {e}")

        # 7. Facts (mémoire profonde) — stocke les faits atomiques
        for fact in updates.get("facts_learned") or []:
            if not isinstance(fact, dict) or not fact.get("content"):
                continue
            try:
                add_fact(
                    category=fact.get("category", "memory"),
                    content=fact["content"],
                    source="conversation",
                    confidence=fact.get("confidence", "medium"),
                )
                applied["facts"] += 1
            except Exception as e:
                logger.error(f"[memory] add_fact : {e}")

        # 8. Life context change
        lc = updates.get("life_context_change")
        if isinstance(lc, dict) and lc.get("description"):
            try:
                add_life_context(
                    context_type=lc.get("type", "routine_change"),
                    description=lc["description"],
                )
                applied["life_context"] = True
            except Exception as e:
                logger.error(f"[memory] life_context : {e}")

        # 9. Relationship enrichment (profiles + events)
        for entry in updates.get("people") or []:
            if not isinstance(entry, dict) or not entry.get("name"):
                continue
            name = entry["name"]
            if entry.get("action") == "add_event":
                try:
                    person = get_all_people()
                    pid = None
                    for p in person:
                        if p["name"].lower() == name.lower():
                            pid = p["id"]
                            break
                    if pid:
                        event_type = entry.get("event_type", "milestone")
                        if event_type in ("conflict", "support", "milestone",
                                          "deep_conversation", "mood_impact"):
                            add_relationship_event(
                                person_id=pid,
                                event_type=event_type,
                                summary=entry.get("content", ""),
                                source="conversation",
                            )
                except Exception as e:
                    logger.error(f"[memory] relationship_event : {e}")

        # 10. Cross insights (patterns multi-personnes)
        for ci in updates.get("cross_insights") or []:
            if not isinstance(ci, dict) or not ci.get("content"):
                continue
            try:
                add_cross_insight(
                    insight_type=ci.get("type", "pattern"),
                    content=ci["content"],
                    people_involved=ci.get("people"),
                    evidence=ci.get("evidence"),
                    actionable=ci.get("actionable"),
                )
            except Exception as e:
                logger.error(f"[memory] cross_insight : {e}")

        # 11. Pattern alert (juste log)
        if applied["pattern_alert"]:
            logger.info(f"[memory] PATTERN ALERT : {applied['pattern_alert']}")

        logger.info(f"[memory] Updates appliqués : {applied}")
        return applied

    async def process_conversation(self, conversation_id: int) -> dict | None:
        """Appelé en background après une conversation. Crée le résumé et délègue à `handle()`."""
        try:
            history = get_conversation_history(conversation_id, limit=50)
        except Exception as e:
            logger.error(f"[memory] get_history : {e}")
            return None

        if len(history) < MIN_MESSAGES_FOR_PROCESSING:
            logger.info(f"[memory] Conversation {conversation_id} triviale ({len(history)} msgs) — skip")
            return None

        # Résumé brut : juste la concat user/assistant pour que Haiku ait le contexte
        lines = []
        for m in history:
            role = m.get("role", "?")
            content = (m.get("content") or "").replace("\n", " ")[:300]
            lines.append(f"[{role}] {content}")
        summary_text = "\n".join(lines)

        logger.info(f"[memory] Traitement conversation {conversation_id} ({len(history)} msgs)")
        result = await self.handle(summary_text)
        return result.get("applied")

    async def weekly_summary(self) -> str:
        """Résumé hebdomadaire (Sonnet pour la qualité)."""
        try:
            episodes = get_weekly_episodes(days=7)
            patterns = get_active_patterns()
            moods = get_recent_moods(limit=14)
        except Exception as e:
            logger.error(f"[memory] weekly fetch : {e}")
            episodes, patterns, moods = [], [], []

        prompt = (
            f"Voici les données de la semaine de {config.USER_NAME}.\n\n"
            f"ÉPISODES ({len(episodes)}) :\n"
            + "\n".join(f"- [{e['agent']}] {(e.get('summary') or e['content'])[:200]}"
                        for e in episodes)
            + f"\n\nPATTERNS ACTIFS ({len(patterns)}) :\n"
            + "\n".join(f"- {p['description']} ×{p['occurrences']}" for p in patterns)
            + f"\n\nMOODS ({len(moods)}) :\n"
            + "\n".join(f"- {m['created_at']} : {m['mood_score']}/10 énergie {m['energy_level']}/10"
                        for m in moods)
            + "\n\nProduis un résumé structuré : "
              "1) Ce qui ressort de la semaine (3-4 lignes), "
              "2) Patterns observés, "
              "3) 2-3 recommandations concrètes pour la semaine prochaine. "
              "Réponds en JSON :\n"
              '{"summary": "...", "patterns_spotted": ["..."], "recommendations": ["..."]}'
        )

        try:
            result = await llm.chat(
                messages=[{"role": "user", "content": prompt}],
                model=config.DEEPSEEK_MAIN_MODEL,
                system="Tu produis le résumé hebdomadaire de la mémoire JARVIS. Sortie JSON structurée comme demandé.",
                max_tokens=2000,
                temperature=0.4,
            )
            response = result["content"]
        except Exception as e:
            logger.error(f"[memory] weekly LLM : {e}")
            return ""

        # Parse + persist
        match = JSON_BLOCK_RE.search(response) or re.search(r"\{.*\}", response, re.DOTALL)
        summary_text = response
        patterns_spotted: list = []
        recommendations: list = []
        if match:
            try:
                parsed = json.loads(match.group(1) if match.lastindex else match.group(0))
                summary_text = parsed.get("summary", response)
                patterns_spotted = parsed.get("patterns_spotted") or []
                recommendations = parsed.get("recommendations") or []
            except Exception:
                pass

        try:
            week_start = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
            save_weekly_summary(week_start, summary_text, patterns_spotted, recommendations)
        except Exception as e:
            logger.error(f"[memory] save_weekly : {e}")

        return response


memory_agent = MemoryAgent()
