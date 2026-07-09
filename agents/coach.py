"""Agent COACH — life coaching, relations, émotions, décisions.

Particularité : escalade automatique vers le modèle principal DeepSeek pour les sujets
structurants (décisions de carrière, ruptures, déménagement, crises). Détecte les mentions
de personnes connues et met à jour `last_mentioned` dans la table `people`.

JAMAIS le mode tâche lourde ici — le coaching exige le contexte mémoire JARVIS complet.
"""

import asyncio
import logging
import re
import unicodedata
from datetime import datetime
from typing import AsyncGenerator

import config
import llm
from agents import BaseAgent
from database import (
    get_active_patterns,
    get_all_people,
    get_recent_moods,
    upsert_person,
)

logger = logging.getLogger(__name__)

STREAM_CHUNK_SIZE = 20


def _strip_accents(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", s.lower())
        if not unicodedata.combining(c)
    )


def _format_people(people: list[dict], max_items: int = 15) -> str:
    if not people:
        return "(aucune personne enregistrée)"
    lines = []
    for p in people[:max_items]:
        rel = p.get("relationship") or "?"
        dyn = (p.get("dynamics") or "").replace("\n", " ")[:120]
        last = p.get("last_mentioned") or "?"
        lines.append(f"- {p['name']} ({rel}) — dynamique : {dyn or '…'} — vu : {last}")
    return "\n".join(lines)


def _format_patterns(patterns: list[dict]) -> str:
    if not patterns:
        return "(aucun pattern actif)"
    return "\n".join(
        f"- {p['description']} ×{p['occurrences']} (depuis {p['first_seen']})"
        for p in patterns[:15]
    )


def _format_moods(moods: list[dict]) -> str:
    if not moods:
        return "(aucun mood enregistré)"
    lines = []
    for m in moods:
        ctx = (m.get("context") or "").replace("\n", " ")[:80]
        lines.append(
            f"- {m.get('created_at', '?')} : mood {m.get('mood_score')}/10, "
            f"énergie {m.get('energy_level')}/10 — {ctx}"
        )
    return "\n".join(lines)


class CoachAgent(BaseAgent):
    """Coach personnel : DeepSeek principal, escalade pour les sujets structurants."""

    name = "coach"
    description = "Life coach — relations, émotions, patterns, décisions"
    model = config.DEEPSEEK_MAIN_MODEL

    def _enrich_context(self, context: dict | None) -> dict:
        ctx = dict(context or {})
        try:
            people = get_all_people()
            patterns = get_active_patterns()
            moods = get_recent_moods(limit=7)
        except Exception as e:
            logger.error(f"[coach] enrich_context : {e}")
            people, patterns, moods = [], [], []

        ctx["people_context"] = _format_people(people)
        ctx["patterns_context"] = _format_patterns(patterns)
        ctx["mood_context"] = _format_moods(moods)
        ctx.setdefault("user_name", config.USER_NAME)
        ctx.setdefault("memory_context", "")
        ctx.setdefault("life_profile", "")
        return ctx

    async def _should_escalate(self, user_message: str) -> bool:
        """Décide si le sujet est structurant (pré-check via modèle rapide)."""
        try:
            res = await llm.chat(
                messages=[{"role": "user", "content": user_message}],
                model=config.DEEPSEEK_FAST_MODEL,
                system=(
                    "Ce message aborde-t-il un sujet structurant "
                    "(décision de carrière, rupture amoureuse, déménagement, "
                    "investissement majeur, crise profonde, conflit impliquant 3+ personnes) ? "
                    "Réponds OUI ou NON. Un seul mot."
                ),
                max_tokens=5,
                temperature=0.0,
            )
            decision = "OUI" in res["content"].strip().upper()
            if decision:
                logger.info("[coach] Escalade — sujet structurant (modèle principal)")
            return decision
        except Exception as e:
            logger.error(f"[coach] should_escalate : {e}")
            return False

    def _extract_people_mentions(self, text: str) -> list[str]:
        """Détecte les noms de personnes connues dans le texte (case + accent insensitive).
        Met à jour last_mentioned. Retourne la liste des noms détectés.
        """
        try:
            people = get_all_people()
        except Exception as e:
            logger.error(f"[coach] get_all_people : {e}")
            return []

        text_norm = _strip_accents(text)
        detected = []
        for p in people:
            name = p.get("name") or ""
            if not name:
                continue
            # Match mot complet pour éviter "Marie" dans "Marie-Claire"
            pattern = r"\b" + re.escape(_strip_accents(name)) + r"\b"
            if re.search(pattern, text_norm):
                detected.append(name)
                try:
                    upsert_person(name, last_mentioned=datetime.now().isoformat())
                except Exception as e:
                    logger.error(f"[coach] upsert {name} : {e}")
        if detected:
            logger.info(f"[coach] Personnes détectées : {detected}")
        return detected

    async def _call_with_routing(self, user_message: str, conversation_id: int | None,
                                   context: dict) -> dict:
        """Appel DeepSeek avec escalade modèle principal si sujet structurant.

        En mode vocal, on bypass l'escalade (coûteuse en latence) et on délègue
        directement à _call_claude qui forcera le modèle rapide + VOICE_MAX_TOKENS.
        """
        if context.get("voice_mode"):
            return await self._call_claude(
                user_message, conversation_id=conversation_id,
                context=context, voice_mode=True,
            )

        escalate = await self._should_escalate(user_message)
        model = (
            config.AGENT_MODELS.get("coach_deep", config.DEEPSEEK_MAIN_MODEL)
            if escalate
            else config.DEEPSEEK_MAIN_MODEL
        )
        result = await self._call_claude(
            user_message, conversation_id=conversation_id,
            context=context, model=model,
        )
        if escalate:
            result["escalated"] = True
        return result

    async def handle(self, user_message: str, conversation_id: int = None,
                     context: dict = None) -> dict:
        ctx = self._enrich_context(context)
        result = await self._call_with_routing(user_message, conversation_id, ctx)
        # Detection des mentions sur le message user (pas la réponse)
        try:
            self._extract_people_mentions(user_message)
        except Exception as e:
            logger.error(f"[coach] extract mentions : {e}")
        return result

    async def handle_stream(self, user_message: str, conversation_id: int = None,
                            context: dict = None) -> AsyncGenerator[dict, None]:
        yield {"type": "classification", "agent": self.name}

        ctx = self._enrich_context(context)
        result = await self._call_with_routing(user_message, conversation_id, ctx)
        response_text = result.get("response", "")

        for i in range(0, len(response_text), STREAM_CHUNK_SIZE):
            yield {"type": "chunk", "content": response_text[i:i + STREAM_CHUNK_SIZE]}
            await asyncio.sleep(0.01)

        yield {
            "type": "done",
            "agent": self.name,
            "model": result.get("model"),
            "tokens_in": result.get("tokens_in", 0),
            "tokens_out": result.get("tokens_out", 0),
            "cost": result.get("cost", 0.0),
            "escalated": result.get("escalated", False),
        }

        # Background : extraction des mentions
        try:
            self._extract_people_mentions(user_message)
        except Exception as e:
            logger.error(f"[coach] post-stream extract : {e}")


coach_agent = CoachAgent()
