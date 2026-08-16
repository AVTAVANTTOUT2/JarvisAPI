"""Agent Orchestrateur — classifie chaque message et le dispatche au bon agent.

Utilise Haiku pour une classification ultra-rapide (~50 tokens), puis appelle
l'agent spécialisé via le registry.
"""

import asyncio
import logging
import re
import time as _time
from typing import AsyncGenerator

import config
import llm
from agents import BaseAgent, get_agent
from agents.display_text import finalize_assistant_display_text
from database import get_conversation_history
from jarvis.event_bus import JarvisEvent, event_bus
from jarvis.security.llm_data_boundary import sanitize_history_messages

logger = logging.getLogger(__name__)

CATEGORIES = ["SCHOOL", "PRODUCTIVITY", "COACH", "INFO", "JOURNAL", "DEVOPS", "FOOD"]
CATEGORY_TO_AGENT = {
    "SCHOOL": "school",
    "PRODUCTIVITY": "productivity",
    "COACH": "coach",
    "INFO": "info",
    "JOURNAL": "journal",
    "DEVOPS": "devops",
    "FOOD": "food",
}

# ── Classification par mots-clés (0 token, 0 latence) ──────────────────────
#
# Chaque liste est une priorité. L'ordre des listes détermine l'ordre
# de priorité : COACH > JOURNAL > SCHOOL > PRODUCTIVITY > DEVOPS > INFO.
# _match_any() vérifie si *au moins un* mot-clé apparaît dans le message.
# Le fallback LLM (quick_classify) n'est appelé que si aucun mot-clé ne matche.

DEVOPS_KEYWORDS = [
    "code",
    "bug",
    "debug",
    "erreur",
    "exception",
    "stack trace",
    "crash",
    "git",
    "commit",
    "push",
    "pull request",
    "merge",
    "branch",
    "repo",
    "api",
    "endpoint",
    "rest",
    "graphql",
    "webhook",
    "requête http",
    "serveur",
    "deploy",
    "déploiement",
    "production",
    "staging",
    "docker",
    "container",
    "image docker",
    "kubernetes",
    "k8s",
    "base de données",
    "sql",
    "sqlite",
    "postgres",
    "migration",
    "schema",
    "architecture",
    "infra",
    "infrastructure",
    "pipeline",
    "ci/cd",
    "sécurité",
    "vulnérabilité",
    "cve",
    "firewall",
    "tls",
    "ssl",
    "certificat",
    "script",
    "shell",
    "bash",
    "terminal",
    "process",
    "processus",
    "daemon",
    "cloudflare",
    "tailscale",
    "dns",
    "reverse proxy",
    "nginx",
    "ssh",
    "config",
    "variable d'environnement",
    "env",
    "log",
    "logs",
    "monitoring",
    "performance",
    "latence",
    "optimisation",
    "refactor",
    "test unitaire",
    "package",
    "dépendance",
    "venv",
    "requirements",
    "build",
    "compile",
]

# Commande de repas — volontairement étroit. Le mot « commande » seul est banni :
# « lance la commande git status » doit rester DEVOPS, pas une commande Uber Eats.
FOOD_PATTERNS = [
    "uber eats",
    "ubereats",
    "j'ai faim",
    "jai faim",
    "je meurs de faim",
    "je crève de faim",
    "commande à manger",
    "commande a manger",
    "commander à manger",
    "commander a manger",
    "commande de la nourriture",
    "commander de la nourriture",
    "commande un repas",
    "commander un repas",
    "commande le repas",
    "commande à bouffer",
    "commande a bouffer",
    "livraison de repas",
    "se faire livrer",
    "fais-toi livrer",
    "commande une pizza",
    "commander une pizza",
    "commande des sushis",
    "commander des sushis",
]

COACH_PATTERNS = [
    # Accentué
    "stressé",
    "anxiété",
    "triste",
    "déprimé",
    "peur",
    "dispute",
    "conflit",
    "fatigué",
    "épuisé",
    "découragé",
    "inquiet",
    # Sans accent (messages tapés au clavier)
    "stresse",
    "stress",
    "anxieux",
    "anxiete",
    "deprime",
    "fatigue",
    "epuise",
    "decourage",
    # Expressions
    "je me sens",
    "j'en peux plus",
    "je n'arrive pas",
]

SCHOOL_PATTERNS = [
    # Accentué
    "exercice",
    "devoir",
    "devoirs",
    "cours",
    "examen",
    "contrôle",
    "professeur",
    "prof",
    "note scolaire",
    "td",
    "tp",
    "partiel",
    "révision",
    "diplôme",
    # Sans accent
    "controle",
    "partiels",
    "diplome",
    "revision",
    "matiere",
]

PRODUCTIVITY_PATTERNS = [
    # Accentué
    "tâche",
    "rappel",
    "rendez-vous",
    "réunion",
    "délai",
    "échéance",
    # Sans accent
    "tache",
    "todo",
    "planning",
    "agenda",
    "deadline",
    "calendrier",
    "reunion",
    "delai",
    "echeance",
    "organiser ma journee",
    "organiser ma journée",
]

# Contrôle Mac / apps — avant SCHOOL/LLM pour éviter open_app stripé par school/journal
# Sans espace final : _match_any exige une frontière de mot (sinon « ouvre » + « Roblox » échoue).
COMPUTER_PATTERNS = [
    "ouvre",
    "ouvrir",
    "lance",
    "lancer",
    "ferme",
    "fermer",
    "open app",
    "open -a",
    "sur mon mac",
    "sur le mac",
    "quitte",
]

INFO_PATTERNS = [
    "météo",
    "meteo",
    "quel temps",
    "quelle heure",
    "définition",
    "definition",
    "c'est quoi",
    "combien de",
    "calcule",
    "convertir",
    "explique",
    "cherche",
    "trouve",
    "blague",
    "capitale",
    "raconte",
    "donne-moi",
    "donne moi",
    "salut",
    "ça va",
    "ca va",
    "bonjour",
]

JOURNAL_PATTERNS = [
    "aujourd'hui j'ai",
    "je voulais raconter",
    "ma journée",
    "j'ai vécu",
    "je tenais à noter",
]

VALID_CATEGORIES = (
    "COACH",
    "JOURNAL",
    "SCHOOL",
    "PRODUCTIVITY",
    "DEVOPS",
    "INFO",
    "FOOD",
)


def _match_any(message: str, patterns: list[str]) -> bool:
    """Retourne True si au moins un pattern est présent comme mot entier ou préfixe.

    Utilise des frontières de mots (caractères alphabétiques contigus) pour
    éviter les faux positifs : ``"api"`` ne matche PAS ``"capitale"``,
    ``"log"`` ne matche PAS ``"catalogue"``, ``"prof"`` ne matche PAS
    ``"professionnel"`` (à moins qu'il soit isolé).
    """
    msg = message.lower()
    for p in patterns:
        idx = msg.find(p)
        if idx == -1:
            continue
        before_ok = idx == 0 or not msg[idx - 1].isalpha()
        after_ok = (idx + len(p)) == len(msg) or not msg[idx + len(p)].isalpha()
        if before_ok and after_ok:
            return True
    return False


async def classify_category(message: str) -> str:
    """Classification par mots-clés avec fallback LLM (DeepSeek Flash).

    Priorité stricte :
        COACH > FOOD > JOURNAL > SCHOOL > PRODUCTIVITY > DEVOPS > INFO (filet sécurité)

    FOOD passe avant JOURNAL parce que « aujourd'hui j'ai faim, commande une
    pizza » est une demande d'action, pas un récit de journée.

    Si aucun mot-clé ne matche, appel ``llm.quick_classify`` (DeepSeek Flash,
    ~50 tokens, gratuit/discount) avec la liste complète des catégories.
    """
    t0 = _time.time()

    def _resolve(cat: str, method: str) -> str:
        elapsed = int((_time.time() - t0) * 1000)
        asyncio.create_task(
            event_bus.emit(
                JarvisEvent(
                    type="orchestrator.classify",
                    data={
                        "message": message[:80],
                        "category": cat,
                        "method": method,
                        "latency_ms": elapsed,
                    },
                )
            )
        )
        asyncio.create_task(
            event_bus.emit(
                JarvisEvent(
                    type="orchestrator.route",
                    data={"agent": cat.lower(), "message": message[:80]},
                )
            )
        )
        return cat

    if _match_any(message, COACH_PATTERNS):
        return _resolve("COACH", "keyword")
    if _match_any(message, FOOD_PATTERNS):
        return _resolve("FOOD", "keyword")
    if _match_any(message, JOURNAL_PATTERNS):
        return _resolve("JOURNAL", "keyword")
    # Ouvrir/lancer une app Mac → PRODUCTIVITY (persona open_app), avant SCHOOL/LLM
    if _match_any(message, COMPUTER_PATTERNS):
        return _resolve("PRODUCTIVITY", "keyword")
    if _match_any(message, SCHOOL_PATTERNS):
        return _resolve("SCHOOL", "keyword")
    if _match_any(message, PRODUCTIVITY_PATTERNS):
        return _resolve("PRODUCTIVITY", "keyword")
    if _match_any(message, DEVOPS_KEYWORDS):
        return _resolve("DEVOPS", "keyword")
    if _match_any(message, INFO_PATTERNS):
        return _resolve("INFO", "keyword")

    # Une transcription d'un seul mot est souvent un nom propre ou un fragment
    # de VAD. Elle ne contient pas assez de signal pour choisir un domaine par
    # défaut via le LLM.
    words = re.findall(r"[^\W_]+(?:['’-][^\W_]+)*", message, flags=re.UNICODE)
    if len(words) <= 1:
        return _resolve("INFO", "fragment")

    try:
        category = await llm.quick_classify(message, list(VALID_CATEGORIES))
        category = category.strip().upper()
        if category in VALID_CATEGORIES:
            return _resolve(category, "llm")
    except Exception as exc:
        logger.warning("quick_classify échec : %s", exc)

    return _resolve("INFO", "fallback")


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
    def _build_history(
        conversation_id: int | None, limit: int = HISTORY_LIMIT
    ) -> list[dict]:
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
            logger.error(
                "Erreur récupération historique conv %s : %s", conversation_id, exc
            )
            return []

        messages: list[dict] = []
        for msg in rows:
            if msg["role"] not in ("user", "assistant"):
                continue
            content = (msg.get("content") or "").strip()
            if not content:
                continue
            messages.append(
                {
                    "role": msg["role"],
                    "content": content,
                    "created_at": msg.get("created_at"),
                }
            )

        if messages and messages[-1]["role"] == "user":
            messages.pop()

        return messages

    async def classify(self, user_message: str) -> str:
        """Classifie le message dans une des catégories. Retourne la catégorie en MAJUSCULES.

        Stratégie optimisée :
        1. Heuristique par mots-clés (0 token, instantané)
        2. Si l'heuristique ne matche pas → appel LLM (DeepSeek Flash)
        3. Si LLM vide ou non reconnu → INFO (safe default)

        L'heuristique couvre ~85% des cas. Le LLM est le filet de sécurité
        pour les messages ambigus que les mots-clés ne capturent pas.
        """
        t0 = _time.time()
        msg_lower = user_message.lower()

        # ── 1. Heuristique (gratuite, instantanée) ──
        heuristic = None
        if _match_any(msg_lower, COACH_PATTERNS):
            heuristic = "COACH"
        elif _match_any(msg_lower, JOURNAL_PATTERNS):
            heuristic = "JOURNAL"
        elif _match_any(msg_lower, COMPUTER_PATTERNS):
            heuristic = "PRODUCTIVITY"
        elif _match_any(msg_lower, SCHOOL_PATTERNS):
            heuristic = "SCHOOL"
        elif _match_any(msg_lower, PRODUCTIVITY_PATTERNS):
            heuristic = "PRODUCTIVITY"
        elif _match_any(msg_lower, DEVOPS_KEYWORDS):
            heuristic = "DEVOPS"
        elif _match_any(msg_lower, INFO_PATTERNS):
            heuristic = "INFO"

        if heuristic:
            elapsed = int((_time.time() - t0) * 1000)
            asyncio.create_task(
                event_bus.emit(
                    JarvisEvent(
                        type="orchestrator.classify",
                        data={
                            "message": user_message[:80],
                            "category": heuristic,
                            "method": "keyword",
                            "latency_ms": elapsed,
                        },
                    )
                )
            )
            asyncio.create_task(
                event_bus.emit(
                    JarvisEvent(
                        type="orchestrator.route",
                        data={"agent": heuristic.lower(), "message": user_message[:80]},
                    )
                )
            )
            return heuristic

        # ── 2. LLM (pour les cas ambigus) ──
        system = self.build_system_prompt({"user_name": config.USER_NAME})
        raw = ""
        for attempt, tokens in enumerate((20, 50), start=1):
            try:
                result = await llm.chat(
                    messages=[{"role": "user", "content": user_message}],
                    model=self.model,
                    system=system,
                    max_tokens=tokens,
                    temperature=0.0,
                    use_cache=False,
                )
                raw = (result.get("content") or "").strip().upper()
            except Exception as e:
                logger.warning(
                    "Classification LLM échec (tentative %d) : %s",
                    attempt,
                    e,
                )
            if raw:
                break
            logger.debug("Classification LLM vide (tentative %d)", attempt)

        final_cat = "INFO"
        if raw:
            for cat in CATEGORIES:
                if cat in raw:
                    final_cat = cat
                    break
            if final_cat == "INFO":
                logger.info("Classification LLM '%s' → fallback INFO", raw[:40])

        elapsed = int((_time.time() - t0) * 1000)
        asyncio.create_task(
            event_bus.emit(
                JarvisEvent(
                    type="orchestrator.classify",
                    data={
                        "message": user_message[:80],
                        "category": final_cat,
                        "method": "llm",
                        "latency_ms": elapsed,
                    },
                )
            )
        )
        asyncio.create_task(
            event_bus.emit(
                JarvisEvent(
                    type="orchestrator.route",
                    data={"agent": final_cat.lower(), "message": user_message[:80]},
                )
            )
        )
        return final_cat

    def build_context(self) -> dict:
        """Conserve seulement l'identité stable ; le reste vient du retrieval."""

        stable_profile = (
            "[PROFIL_STABLE]\n"
            f"Nom : {config.USER_NAME}\n"
            f"Ville : {config.WEATHER_CITY}\n"
            f"Langue : {config.LANGUAGE}\n"
            f"Fuseau : {config.TIMEZONE}"
        )
        return {
            "user_name": config.USER_NAME,
            "city": config.WEATHER_CITY,
            "language": config.LANGUAGE,
            "timezone": config.TIMEZONE,
            "memory_context": stable_profile,
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
        """Contexte identique pour chat texte, streaming et voix."""
        ctx = dict(base_context) if base_context is not None else self.build_context()
        if voice_mode:
            ctx["voice_mode"] = True
        # `_build_enriched_context` charge l'historique en même temps que le
        # retrieval. Les appels directs à l'orchestrateur gardent ce fallback.
        ctx.setdefault("history", self._build_history(conversation_id))
        if not ctx.get("__retrieval_done"):
            from jarvis.retrieval import (
                RetrievalRequest,
                RetrievalResult,
                format_retrieval_context,
                search_knowledge,
            )

            recent_user_turns = tuple(
                str(message.get("content") or "")
                for message in ctx.get("history", [])
                if isinstance(message, dict) and message.get("role") == "user"
            )[-6:]
            request = RetrievalRequest(
                query=user_message,
                conversation_id=conversation_id,
                recent_user_turns=recent_user_turns,
                interaction_mode="voice" if voice_mode else "chat",
                freshness_budget_ms=700 if voice_mode else 1_500,
            )
            try:
                result = await asyncio.to_thread(search_knowledge, request)
            except Exception as exc:
                logger.warning(
                    "[retrieval] fallback orchestrateur indisponible: %s",
                    type(exc).__name__,
                )
                result = RetrievalResult(
                    status="unavailable",
                    query=user_message,
                    unavailable_sources=("knowledge",),
                    diagnostics=(f"orchestrator:{type(exc).__name__}",),
                )
            ctx["retrieval_context"] = format_retrieval_context(result)
            ctx["__retrieval_done"] = True
            ctx["__retrieval_status"] = result.status
            ctx["__retrieval_references"] = [
                {
                    "uid": hit.uid,
                    "source_type": hit.source_type,
                    "source_id": hit.source_id,
                }
                for hit in result.hits[:8]
            ]

        agent_name = CATEGORY_TO_AGENT.get(category, DEFAULT_AGENT)
        agent = get_agent(agent_name) or get_agent(DEFAULT_AGENT)
        if agent is None:
            return ctx, None

        # Productivity enrichit lui-même handle/handle_stream. Le faire ici
        # lançait une deuxième collecte Mail/Calendar dans le même tour.
        if (
            agent.name != "productivity"
            and hasattr(agent, "_enrich_context")
            and callable(getattr(agent, "_enrich_context"))
        ):
            ctx = agent._enrich_context(ctx)
        return ctx, agent

    async def handle(
        self,
        user_message: str,
        conversation_id: int = None,
        context: dict = None,
        voice_mode: bool = False,
    ) -> dict:
        """Classifie → dispatche → retourne la réponse de l'agent ciblé.

        ``user_message`` est le texte brut utilisateur (transcription ou saisie).
        Si ``voice_mode`` est True, le message envoyé au LLM est préfixé
        ``[VOICE_MODE] `` et le contexte porte ``voice_mode=True`` (Haiku +
        ``VOICE_MAX_TOKENS`` via ``_route_task`` / ``_call_claude``).
        """
        category = await classify_category(user_message)

        base_ctx = self.build_context()
        if context:
            base_ctx.update(context)

        ctx, agent = await self._prepare_dispatch_context(
            user_message,
            conversation_id,
            category,
            voice_mode=voice_mode,
            base_context=base_ctx,
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
        result = await agent.handle(
            to_agent, conversation_id=conversation_id, context=ctx
        )
        result["category"] = category
        return result

    async def handle_stream(
        self,
        user_message: str,
        conversation_id: int = None,
        context: dict = None,
        voice_mode: bool = False,
    ) -> AsyncGenerator[dict, None]:
        """Version streaming : yield {type, ...} successifs.

        Si l'agent ciblé expose `handle_stream()`, on lui délègue (ex : SchoolAgent
        fait du pseudo-streaming car _route_task produit la réponse d'un bloc).
        Sinon : streaming DeepSeek générique via `llm.chat_stream`.

        ``voice_mode=True`` : même enrichissement que ``handle(..., voice_mode=True)`` ;
        message LLM préfixé ``[VOICE_MODE] `` ; flux générique forcé en Haiku +
        ``VOICE_MAX_TOKENS``.

        Events :
            {type: "classification", category: str, agent: str}
            {type: "chunk", content: str}
            {type: "saved_file", path: str}    (optionnel, agents qui produisent des fichiers)
            {type: "done", tokens_in, tokens_out, cost, model, agent}
        """
        category = await classify_category(user_message)

        base_ctx = self.build_context()
        if context:
            base_ctx.update(context)

        ctx, agent = await self._prepare_dispatch_context(
            user_message,
            conversation_id,
            category,
            voice_mode=voice_mode,
            base_context=base_ctx,
        )

        yield {
            "type": "classification",
            "category": category,
            "agent": agent.name if agent else DEFAULT_AGENT,
        }

        if agent is None:
            yield {"type": "chunk", "content": "Aucun agent disponible."}
            yield {
                "type": "done",
                "tokens_in": 0,
                "tokens_out": 0,
                "cost": 0.0,
                "model": self.model,
                "agent": "orchestrator",
            }
            return

        to_agent = f"[VOICE_MODE] {user_message}" if voice_mode else user_message

        # Si l'agent a son propre streaming (cas school avec _route_task), on lui délègue.
        # conversation_id=None pour éviter un double save (c'est le handler WebSocket qui persiste).
        if hasattr(agent, "handle_stream") and callable(
            getattr(agent, "handle_stream")
        ):
            async for event in agent.handle_stream(
                to_agent, conversation_id=None, context=ctx
            ):
                if event.get("type") == "classification":
                    event["category"] = category
                yield event
            return

        # Sinon : streaming DeepSeek classique (ex : InfoAgent qui n'a pas de handle_stream)
        system = agent.build_system_prompt(ctx)
        full_response = ""
        stream_usage: dict = {}
        emotion_tag_stripped = False
        detected_emotion = "neutral"

        history_messages = sanitize_history_messages(ctx.get("history", []))
        stream_messages = history_messages + [{"role": "user", "content": to_agent}]

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
            on_usage=stream_usage.update,
        ):
            full_response += chunk

            if not emotion_tag_stripped:
                m = re.match(r"^\s*\[(\w+)\]\s*\n?", full_response)
                if m and m.group(1).lower() in agent._VALID_EMOTIONS:
                    detected_emotion = m.group(1).lower()
                    clean = full_response[m.end() :]
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
            "tokens_in": int(stream_usage.get("tokens_in") or 0),
            "tokens_out": int(stream_usage.get("tokens_out") or 0),
            "cache_hit": int(stream_usage.get("cache_hit") or 0),
            "cost": float(stream_usage.get("cost") or 0.0),
            "usage_estimated": bool(stream_usage.get("usage_estimated", False)),
            "stop_reason": stream_usage.get("stop_reason"),
            "model": eff_model,
            "agent": agent.name,
            "emotion": detected_emotion,
            "content": display_final,
        }


orchestrator = OrchestratorAgent()
