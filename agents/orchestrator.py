"""Agent Orchestrateur — classifie chaque message et le dispatche au bon agent.

Utilise Haiku pour une classification ultra-rapide (~50 tokens), puis appelle
l'agent spécialisé via le registry.
"""

import logging
from typing import AsyncGenerator

import config
import llm
from agents import BaseAgent, get_agent
from agents.display_text import finalize_assistant_display_text
from database import (
    build_full_context,
    get_active_patterns,
    get_all_people,
    get_conversation_history,
    get_life_profile,
    get_recent_email_summaries,
    get_recent_episodes,
    save_message,
)

logger = logging.getLogger(__name__)

# Mots-clés : question sur la boîte mail → enrichir memory_context avant l’agent (tous flux).
_MAIL_INJECT_KEYWORDS = (
    "mail",
    "mails",
    "email",
    "courrier",
    "inbox",
    "boîte mail",
    "boite mail",
    "non lu",
    "non lus",
    "message de",
    "m'a écrit",
    "m'a ecrit",
    "écrit par",
    "ecrit par",
    "envoyé",
    "envoye",
    "reçu",
    "recu",
    "expéditeur",
    "expediteur",
    "expéditrice",
    "objet:",
    "que me veut",
    "qu'est-ce que",
    "quest-ce que",
    "qu’est ce que",
)


def _user_message_requests_mail_context(msg: str, category: str) -> bool:
    if category == "PRODUCTIVITY":
        return True
    m = (msg or "").lower()
    if any(k in m for k in _MAIL_INJECT_KEYWORDS):
        return True
    try:
        for p in get_all_people():
            name = (p.get("name") or "").strip().lower()
            if len(name) >= 2 and name in m:
                return True
    except Exception:
        pass
    return False


def _format_email_summaries_block(summaries: list[dict]) -> str:
    """Résumés déjà en base (email_watcher) — utile si les mails ne sont plus « non lus »."""
    if not summaries:
        return ""
    lines = []
    for s in summaries[:20]:
        sender = s.get("sender") or "?"
        sub = s.get("subject") or "(sans objet)"
        summ = (s.get("summary") or "").replace("\n", " ").strip()[:400]
        lines.append(f"- De: {sender} | Objet: {sub} | Résumé: {summ}")
    body = "\n".join(lines)
    return (
        "[EMAIL_SUMMARIES_DB]\n"
        "Mails récemment analysés (y compris déjà lus) :\n"
        f"{body}\n"
        "[/EMAIL_SUMMARIES_DB]"
    )


def _format_live_emails_block(emails: list[dict], preview_max: int = 220) -> str:
    if not emails:
        return (
            "[EMAILS_CONTEXT]\n"
            "(Aucun mail non lu listé depuis Mail.app, ou liste vide.)\n"
            "[/EMAILS_CONTEXT]"
        )
    lines = []
    for e in emails[:12]:
        prev = (
            str(e.get("snippet") or e.get("preview") or "").replace("\n", " ").strip()
        )[:preview_max]
        frm = str(e.get("from") or "?")
        sub = str(e.get("subject") or "(sans objet)")
        lines.append(f"- De: {frm} | Objet: {sub} | Aperçu: {prev}")
    body = "\n".join(lines)
    return f"[EMAILS_CONTEXT]\nMails non lus récents (Mail.app) :\n{body}\n[/EMAILS_CONTEXT]"


async def append_recent_mails_to_context(ctx: dict, user_message: str, category: str) -> None:
    """Injecte une synthèse des non-lus dans memory_context pour questions « mail » / hors productivité.

    Les agents lisent cette zone via {{memory_context}} ; pas besoin de l’action mail_read.
    Catégorie PRODUCTIVITY : emails_context est fourni par `_collect_pro_context` — on évite le doublon.
    """
    if category == "PRODUCTIVITY":
        return
    if not _user_message_requests_mail_context(user_message, category):
        return
    block = ""
    try:
        from integrations.mail import mail_client

        ok = mail_client and mail_client.is_available()
        logger.info(
            "[mail] enrich context mail_client=%s available=%s",
            mail_client is not None,
            ok if mail_client else False,
        )
        if ok:
            emails = await mail_client.get_unread(12)
            block = _format_live_emails_block(emails or [])
        else:
            block = (
                "[EMAILS_CONTEXT]\n"
                "Apple Mail indisponible : ouvre Mail.app et autorise Automation pour Terminal.\n"
                "[/EMAILS_CONTEXT]"
            )
    except Exception as ex:
        logger.warning("[mail] enrich context erreur : %s", ex)
        block = f"[EMAILS_CONTEXT]\nLecture mails impossible ({type(ex).__name__}).\n[/EMAILS_CONTEXT]"

    summaries_block = ""
    try:
        summaries_block = _format_email_summaries_block(get_recent_email_summaries(limit=20))
    except Exception as ex:
        logger.warning("[mail] email_summaries enrich : %s", ex)

    mem = (ctx.get("memory_context") or "").strip()
    merged = block.strip()
    if summaries_block.strip() and summaries_block.strip() not in mem:
        merged = f"{merged}\n\n{summaries_block.strip()}".strip() if merged else summaries_block.strip()

    if merged.strip() and merged.strip() in mem:
        return
    ctx["memory_context"] = (mem + "\n\n" + merged).strip() if mem else merged


CATEGORIES = ["SCHOOL", "PRODUCTIVITY", "COACH", "INFO", "JOURNAL"]
CATEGORY_TO_AGENT = {
    "SCHOOL": "school",
    "PRODUCTIVITY": "productivity",
    "COACH": "coach",
    "INFO": "info",
    "JOURNAL": "journal",
}

# Agent fallback si le ciblé n'existe pas encore (Phase 1 : seul `info` est implémenté)
DEFAULT_AGENT = "info"


HISTORY_LIMIT = 30


class OrchestratorAgent(BaseAgent):
    """Routeur central : classifie + dispatche."""

    name = "orchestrator"
    description = "Classifie chaque message et route vers l'agent spécialisé."
    model = config.DEEPSEEK_FAST_MODEL
    inject_persona = False  # routeur interne, ne parle pas à l'utilisateur

    @staticmethod
    def _build_history(conversation_id: int | None, limit: int = HISTORY_LIMIT) -> list[dict]:
        """Récupère les N derniers messages de la conversation et les formate
        pour l'API Claude (liste de ``{role, content}``).

        Le dernier message *user* est exclu : c'est celui en cours de traitement,
        il sera ajouté par ``_call_claude`` (évite un doublon puisque
        ``_process_message`` (``main.py``) le persiste AVANT d'appeler l'orchestrateur).
        """
        if not conversation_id:
            return []
        try:
            rows = get_conversation_history(conversation_id, limit=limit)
        except Exception as exc:
            logger.error("Erreur récupération historique conv %s : %s", conversation_id, exc)
            return []

        messages: list[dict] = []
        for msg in rows:
            if msg["role"] not in ("user", "assistant"):
                continue
            content = (msg.get("content") or "").strip()
            if not content:
                continue
            messages.append({"role": msg["role"], "content": content})

        if messages and messages[-1]["role"] == "user":
            messages.pop()

        return messages

    async def classify(self, user_message: str) -> str:
        """Classifie le message dans une des catégories. Retourne la catégorie en MAJUSCULES."""
        system = self.build_system_prompt({"user_name": config.USER_NAME})

        try:
            result = await llm.chat(
                messages=[{"role": "user", "content": user_message}],
                model=self.model,
                system=system,
                max_tokens=20,
                temperature=0.0,
                use_cache=True,
            )
            raw = result["content"].strip().upper()
            for cat in CATEGORIES:
                if cat in raw:
                    return cat
            logger.warning(f"Classification ambiguë '{raw}' → fallback INFO")
            return "INFO"
        except Exception as e:
            logger.error(f"Erreur classification : {e}")
            return "INFO"

    def build_context(self) -> dict:
        """Construit le contexte dense pour Sonnet/Opus.

        Tout vient de la DB — données déjà structurées par Haiku.
        Sonnet ne voit JAMAIS de messages bruts. Caché via prompt caching.
        """
        try:
            ctx = build_full_context()
        except Exception as e:
            logger.error(f"Erreur build_context (full) : {e}")
            ctx = {
                "user_facts": {}, "life_profile": {},
                "active_patterns": [], "active_life_context": [],
                "recent_moods": [], "people_profiles": [],
                "cross_insights": [], "recent_episodes": [],
                "current_location": None, "current_visit": None,
                "today_visits": [], "location_patterns": [],
            }

        # Life profile
        profile_text = ""
        for cat, items in ctx["life_profile"].items():
            profile_text += f"\n{cat.upper()} :\n"
            for item in items:
                profile_text += f"- {item}\n"
        profile_text = profile_text.strip() or "(profil non encore renseigné)"

        # Faits utilisateur (dense)
        facts_text = ""
        for cat, facts in ctx["user_facts"].items():
            facts_text += f"\n{cat.upper()} :\n"
            for f in facts:
                facts_text += f"- {f['content']}\n"
        facts_text = facts_text.strip() or "(aucun fait enregistré)"

        # Profils relationnels (dense)
        people_text = ""
        for p in ctx["people_profiles"][:15]:
            name = p.get("name", "?")
            rel = p.get("relationship") or "?"
            people_text += f"\n### {name} ({rel})\n"
            if p.get("communication_style"):
                people_text += f"Style : {p['communication_style']}\n"
            if p.get("dynamics") or p.get("power_dynamic"):
                people_text += f"Dynamique : {p.get('power_dynamic') or p.get('dynamics', '?')}\n"
            if p.get("sentiment"):
                people_text += f"Sentiment : {p['sentiment']}\n"
            if p.get("topics"):
                people_text += f"Sujets : {p['topics']}\n"
            if p.get("trust_level"):
                people_text += f"Confiance : {p['trust_level']}\n"
        people_text = people_text.strip() or "(aucune personne enregistrée)"

        # Épisodes récents
        episodes_text = "\n".join(
            f"- [{e['agent']}] {e.get('summary') or e['content'][:120]}"
            for e in ctx["recent_episodes"]
        ) or "(aucun épisode récent)"

        # Patterns actifs
        patterns_text = "\n".join(
            f"- [{p['pattern_type']}] {p['description']} (×{p['occurrences']})"
            for p in ctx["active_patterns"]
        ) or "(aucun pattern actif)"

        # Insights cross-relations
        insights_text = "\n".join(
            f"- [{i['insight_type']}] {i['content']}"
            for i in ctx["cross_insights"]
        ) or "(aucun insight)"

        # Contexte de vie actuel
        life_ctx_text = "\n".join(
            f"- {lc['context_type']} : {lc['description']}"
            for lc in ctx["active_life_context"]
        ) or "(pas de contexte de vie actif)"

        # Moods récents (condensé)
        moods = ctx["recent_moods"]
        if moods:
            avg_mood = sum(m["mood_score"] for m in moods if m.get("mood_score")) / max(len(moods), 1)
            avg_energy = sum(m["energy_level"] for m in moods if m.get("energy_level")) / max(len(moods), 1)
            mood_text = f"Mood moyen 14j : {avg_mood:.1f}/10, Énergie : {avg_energy:.1f}/10"
        else:
            mood_text = "Pas de données mood"

        cloc = ctx.get("current_location")
        cvis = ctx.get("current_visit")
        today_v = ctx.get("today_visits") or []
        loc_pat = ctx.get("location_patterns") or []

        if cvis:
            location_text = (
                f"Lieu actuel (nommé) : {cvis.get('place_name', '?')} "
                f"(depuis {cvis.get('arrived_at')})"
            )
        elif cloc:
            try:
                lat, lng = float(cloc["latitude"]), float(cloc["longitude"])
                location_text = f"Position récente : {lat:.4f}, {lng:.4f} (hors lieu nommé ou en transit)"
            except (KeyError, TypeError, ValueError):
                location_text = "Position récente partielle."
        else:
            location_text = "Position récente inconnue (aucun point GPS < 10 min)."

        if today_v:
            names = [str(v.get("place_name") or "?") for v in today_v]
            location_text += "\nAujourd'hui — parcours : " + " → ".join(names)

        if loc_pat:
            bits = [str(p.get("description") or "")[:160] for p in loc_pat[:6]]
            location_text += "\nPatterns géo : " + " ; ".join(b for b in bits if b)

        # ── Horodatage (recalculé à CHAQUE appel, source unique) ──────────
        from agents import _get_horodatage
        datetime_block = _get_horodatage() + "\n"

        memory_context = (
            f"{datetime_block}\n"
            f"[LIFE_PROFILE]\n{profile_text}\n\n"
            f"[USER_FACTS]\n{facts_text}\n\n"
            f"[PEOPLE]\n{people_text}\n\n"
            f"[RECENT_EPISODES]\n{episodes_text}\n\n"
            f"[ACTIVE_PATTERNS]\n{patterns_text}\n\n"
            f"[CROSS_INSIGHTS]\n{insights_text}\n\n"
            f"[LIFE_CONTEXT]\n{life_ctx_text}\n\n"
            f"[MOOD]\n{mood_text}\n\n"
            f"[LOCATION]\n{location_text}"
        )
        prior = (getattr(config, "PRIOR_SESSION_SUMMARY", None) or "").strip()
        if prior:
            memory_context += f"\n\n[DERNIÈRE_SESSION]\n{prior}"

        return {
            "user_name": config.USER_NAME,
            "city": config.WEATHER_CITY,
            "language": config.LANGUAGE,
            "timezone": config.TIMEZONE,
            "memory_context": memory_context,
        }

    async def _prepare_dispatch_context(
        self,
        user_message: str,
        conversation_id: int | None,
        category: str,
        *,
        voice_mode: bool,
        base_context: dict | None = None,
    ) -> tuple[dict, BaseAgent | None]:
        """Contexte identique pour chat texte, streaming et voix (mails, historique, agent)."""
        ctx = dict(base_context) if base_context is not None else self.build_context()
        if voice_mode:
            ctx["voice_mode"] = True
        ctx["history"] = self._build_history(conversation_id)
        await append_recent_mails_to_context(ctx, user_message, category)

        agent_name = CATEGORY_TO_AGENT.get(category, DEFAULT_AGENT)
        agent = get_agent(agent_name) or get_agent(DEFAULT_AGENT)
        if agent is None:
            return ctx, None

        if agent.name == "productivity" and hasattr(agent, "_collect_pro_context"):
            ctx.update(await agent._collect_pro_context())
        elif hasattr(agent, "_enrich_context") and callable(getattr(agent, "_enrich_context")):
            ctx = agent._enrich_context(ctx)
        return ctx, agent

    async def handle(self, user_message: str, conversation_id: int = None,
                     context: dict = None, voice_mode: bool = False) -> dict:
        """Classifie → dispatche → retourne la réponse de l'agent ciblé.

        ``user_message`` est le texte brut utilisateur (transcription ou saisie).
        Si ``voice_mode`` est True, le message envoyé au LLM est préfixé
        ``[VOICE_MODE] `` et le contexte porte ``voice_mode=True`` (Haiku +
        ``VOICE_MAX_TOKENS`` via ``_route_task`` / ``_call_claude``).
        """
        category = await self.classify(user_message)

        base_ctx = self.build_context()
        if context:
            base_ctx.update(context)

        ctx, agent = await self._prepare_dispatch_context(
            user_message, conversation_id, category, voice_mode=voice_mode, base_context=base_ctx
        )

        if agent is None:
            return {
                "response": "Aucun agent disponible. La Phase 1 n'a pas encore enregistré d'agent par défaut.",
                "agent": "orchestrator",
                "category": category,
                "model": self.model,
                "tokens_in": 0,
                "tokens_out": 0,
                "cost": 0.0,
            }

        to_agent = f"[VOICE_MODE] {user_message}" if voice_mode else user_message
        result = await agent.handle(to_agent, conversation_id=conversation_id, context=ctx)
        result["category"] = category
        return result

    async def handle_stream(self, user_message: str, conversation_id: int = None,
                             context: dict = None, voice_mode: bool = False) -> AsyncGenerator[dict, None]:
        """Version streaming : yield {type, ...} successifs.

        Si l'agent ciblé expose `handle_stream()`, on lui délègue (ex : SchoolAgent
        fait du pseudo-streaming car il peut router vers Gemini CLI).
        Sinon : streaming Claude générique via `llm.chat_stream`.

        ``voice_mode=True`` : même enrichissement que ``handle(..., voice_mode=True)`` ;
        message LLM préfixé ``[VOICE_MODE] `` ; flux générique forcé en Haiku +
        ``VOICE_MAX_TOKENS``.

        Events :
            {type: "classification", category: str, agent: str}
            {type: "chunk", content: str}
            {type: "saved_file", path: str}    (optionnel, agents qui produisent des fichiers)
            {type: "done", tokens_in, tokens_out, cost, model, agent}
        """
        category = await self.classify(user_message)

        base_ctx = self.build_context()
        if context:
            base_ctx.update(context)

        ctx, agent = await self._prepare_dispatch_context(
            user_message, conversation_id, category, voice_mode=voice_mode, base_context=base_ctx
        )

        yield {"type": "classification", "category": category, "agent": agent.name if agent else DEFAULT_AGENT}

        if agent is None:
            yield {"type": "chunk", "content": "Aucun agent disponible."}
            yield {"type": "done", "tokens_in": 0, "tokens_out": 0, "cost": 0.0, "model": self.model, "agent": "orchestrator"}
            return

        to_agent = f"[VOICE_MODE] {user_message}" if voice_mode else user_message

        # Si l'agent a son propre streaming (cas school avec route Gemini), on lui délègue.
        # conversation_id=None pour éviter un double save (c'est le handler WebSocket qui persiste).
        if hasattr(agent, "handle_stream") and callable(getattr(agent, "handle_stream")):
            async for event in agent.handle_stream(to_agent, conversation_id=None, context=ctx):
                if event.get("type") == "classification":
                    event["category"] = category
                yield event
            return

        # Sinon : streaming Claude classique (ex : InfoAgent qui n'a pas de handle_stream)
        system = agent.build_system_prompt(ctx)
        full_response = ""
        emotion_tag_stripped = False
        detected_emotion = "neutral"

        history_messages = ctx.get("history", [])
        stream_messages = list(history_messages) + [{"role": "user", "content": to_agent}]

        eff_model = agent.model
        max_tok = 4096
        if voice_mode:
            eff_model = config.DEEPSEEK_FAST_MODEL
            max_tok = getattr(config, "VOICE_MAX_TOKENS", 500)

        async for chunk in llm.chat_stream(
            messages=stream_messages,
            model=eff_model,
            system=system,
            max_tokens=max_tok,
        ):
            full_response += chunk

            if not emotion_tag_stripped:
                import re
                m = re.match(r"^\s*\[(\w+)\]\s*\n?", full_response)
                if m and m.group(1).lower() in agent._VALID_EMOTIONS:
                    detected_emotion = m.group(1).lower()
                    clean = full_response[m.end():]
                    emotion_tag_stripped = True
                    if clean:
                        yield {"type": "chunk", "content": clean}
                    continue
                elif len(full_response) > 20:
                    emotion_tag_stripped = True
                    yield {"type": "chunk", "content": full_response}
                    continue
                continue
            else:
                yield {"type": "chunk", "content": chunk}

        emotion, clean_response = agent._extract_emotion(full_response)
        if emotion != "neutral":
            detected_emotion = emotion

        display_final = finalize_assistant_display_text(full_response)
        yield {
            "type": "done",
            "tokens_in": 0,
            "tokens_out": 0,
            "cost": 0.0,
            "model": eff_model,
            "agent": agent.name,
            "emotion": detected_emotion,
            "content": display_final,
        }


orchestrator = OrchestratorAgent()
