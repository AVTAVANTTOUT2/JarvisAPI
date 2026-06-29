"""Base agent et registry."""

import logging
import re
from abc import ABC, abstractmethod
from pathlib import Path

import config
import llm
from agents.display_text import strip_assistant_code_fences
from database import save_episode, save_message

logger = logging.getLogger(__name__)


PROMPTS_DIR = Path(__file__).parent.parent / "prompts"
PERSONA_PATH = PROMPTS_DIR / "persona.txt"


def _get_horodatage() -> str:
    """Horodatage français recalculé à chaque appel. Injecté dans tous les prompts.

    Format : ``[HORODATAGE] lundi 29 janvier 2026, 18:30 — Europe/Paris``
    """
    from datetime import datetime
    now = datetime.now()
    JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
    MOIS = ["janvier", "février", "mars", "avril", "mai", "juin",
            "juillet", "août", "septembre", "octobre", "novembre", "décembre"]
    return (
        f"[HORODATAGE] {JOURS[now.weekday()]} {now.day} {MOIS[now.month - 1]} "
        f"{now.year}, {now.strftime('%H:%M')} — Europe/Paris"
    )


def _load_persona() -> str:
    """Charge la persona JARVIS commune (mise en cache au niveau module)."""
    if PERSONA_PATH.exists():
        return PERSONA_PATH.read_text(encoding="utf-8")
    return ""


class BaseAgent(ABC):
    """Classe de base pour tous les agents JARVIS.

    `inject_persona` (défaut True) : si True, le system prompt commence par
    `prompts/persona.txt` (la personnalité JARVIS commune). À désactiver pour
    les agents internes qui ne parlent pas à l'utilisateur (orchestrateur,
    mémoire) — leur output est consommé par le code, pas affiché.
    """

    name: str = "base"
    description: str = ""
    model: str = config.DEEPSEEK_MAIN_MODEL
    inject_persona: bool = True

    def load_prompt(self) -> str:
        """Charge le system prompt spécifique depuis prompts/{name}.txt."""
        path = PROMPTS_DIR / f"{self.name}.txt"
        if path.exists():
            return path.read_text(encoding="utf-8")
        return ""

    def build_system_prompt(self, context: dict = None) -> str:
        """Construit le system prompt complet : [horodatage] + [persona] + [agent-specific].

        L'horodatage est calculé dynamiquement à chaque appel (jamais caché)
        et inséré EN PREMIER dans le system prompt, avant la persona commune
        et le prompt spécifique de l'agent.

        La persona JARVIS est injectée en début pour que CHAQUE agent parle
        avec la même voix (majordome Iron Man, pas de chatbot, pas d'emoji).
        Les placeholders {{...}} sont appliqués sur l'ensemble.
        """
        # ── Horodatage calculé à chaque appel ──────────────────────────────
        horodatage = _get_horodatage()

        agent_prompt = self.load_prompt()
        if self.inject_persona:
            persona = _load_persona()
            base = f"{horodatage}\n\n{persona}\n\n---\n\n{agent_prompt}" if persona else f"{horodatage}\n\n{agent_prompt}"
        else:
            base = f"{horodatage}\n\n{agent_prompt}"

        if context:
            for key, value in context.items():
                if key in ("voice_mode", "history"):
                    continue
                base = base.replace(f"{{{{{key}}}}}", str(value))
            if context.get("voice_mode"):
                base += (
                    "\n\n---\n"
                    "DIRECTIVE VOCALE : Tu parles actuellement à l'oral. "
                    "Tes réponses doivent être extrêmement concises, naturelles et conversationnelles. "
                    "Pas de Markdown, pas de listes à puces, pas de longs paragraphes. "
                    "3 phrases maximum sauf si l'utilisateur demande explicitement un développement.\n\n"
                    "IMPORTANT — Tu peux et DOIS utiliser les blocs ```action {{...}}``` "
                    "pour exécuter des commandes réelles : météo, ouvrir une app, créer une tâche, "
                    "consulter le calendrier, donner l'heure, terminal, etc. "
                    "Les actions sont exécutées automatiquement puis le résultat te sera "
                    "communiqué pour que tu le reformules à l'oral. "
                    "Ne dis JAMAIS « je n'ai pas accès à l'heure / à la météo / au calendrier » "
                    "— utilise systématiquement l'action appropriée."
                )
        return base

    _EMOTION_RE = re.compile(r"^\s*\[(\w+)\]\s*\n?", re.MULTILINE)
    _VALID_EMOTIONS = {"neutral", "warm", "serious", "concerned", "amused", "urgent", "encouraging"}

    def _extract_emotion(self, response: str) -> tuple[str, str]:
        """Extrait le tag [emotion] en début de réponse.

        Retourne (emotion, texte_sans_tag). Si pas de tag → ("neutral", response).
        """
        if not response:
            return "neutral", response
        m = self._EMOTION_RE.match(response)
        if m and m.group(1).lower() in self._VALID_EMOTIONS:
            emotion = m.group(1).lower()
            cleaned = response[m.end():]
            return emotion, cleaned
        return "neutral", response

    @abstractmethod
    async def handle(self, user_message: str, conversation_id: int = None,
                     context: dict = None) -> dict:
        """Traite un message. Retourne {response, agent, model, tokens_in, tokens_out, cost, emotion}."""
        pass

    async def _call_claude(self, user_message: str, conversation_id: int = None,
                           context: dict = None, history: list = None,
                           model: str = None, temperature: float = 0.7,
                           voice_mode: bool = False,
                           max_tokens: int | None = None,
                           persist: bool = True,
                           strip_fences: bool = True,
                           ) -> dict:
        """Helper : appelle Claude avec le bon system prompt + historique.

        L'historique est lu depuis ``context["history"]`` (injecté par
        l'orchestrateur à partir de la DB) ou, en fallback, depuis le
        paramètre ``history``.  Seuls les rôles *user* et *assistant*
        sont conservés.
        """
        system = self.build_system_prompt(context or {})
        messages = []

        ctx_history = (context or {}).get("history")
        if ctx_history:
            messages.extend(ctx_history)
        elif history:
            messages.extend(history)

        messages.append({"role": "user", "content": user_message})

        eff_model = model or self.model
        mt = max_tokens if max_tokens is not None else 4096
        is_voice = voice_mode or bool((context or {}).get("voice_mode"))
        if is_voice:
            eff_model = config.DEEPSEEK_FAST_MODEL
            cap = getattr(config, "VOICE_MAX_TOKENS", 500)
            mt = min(mt, cap)

        result = await llm.chat(
            messages=messages,
            model=eff_model,
            system=system,
            temperature=temperature,
            max_tokens=mt,
        )

        emotion, clean_text = self._extract_emotion(result["content"])
        if strip_fences:
            clean_text = strip_assistant_code_fences(clean_text, include_save=False)

        if conversation_id and persist:
            save_message(conversation_id, "assistant", clean_text,
                         agent=self.name, model=result["model"],
                         tokens_in=result["tokens_in"], tokens_out=result["tokens_out"],
                         cost=result["cost"])

        return {
            "response": clean_text,
            "agent": self.name,
            "model": result["model"],
            "tokens_in": result["tokens_in"],
            "tokens_out": result["tokens_out"],
            "cost": result["cost"],
            "emotion": emotion,
        }

    # Alias de compatibilité — l'ancien nom reste utilisable par les agents existants.
    _call_llm = _call_claude

    async def _call_gemini(self, user_message: str, conversation_id: int = None,
                           system: str = "") -> dict:
        """Helper : délègue à Gemini CLI (subprocess, gratuit).

        À utiliser pour les contenus longs/autonomes (exos, dissertations, code…)
        qui n'ont pas besoin du contexte mémoire JARVIS.
        """
        result = await llm.gemini_chat(user_message, system=system)

        emotion, clean_text = self._extract_emotion(result["content"])
        clean_text = strip_assistant_code_fences(clean_text, include_save=False)

        if conversation_id:
            save_message(
                conversation_id, "assistant", clean_text,
                agent=self.name, model=result["model"],
                tokens_in=0, tokens_out=0, cost=0.0,
            )

        logger.info(f"[{self.name}] Gemini CLI : production terminée (gratuit)")

        return {
            "response": clean_text,
            "agent": self.name,
            "model": result["model"],
            "tokens_in": 0,
            "tokens_out": 0,
            "cost": 0.0,
            "emotion": emotion,
        }

    _ACTION_RE = re.compile(
        r"```action\s*\n(.*?)\n```",
        re.DOTALL | re.IGNORECASE,
    )

    def _extract_action(self, response: str) -> tuple[dict | None, str]:
        """Extrait un bloc ```action {JSON} ``` de la réponse.

        Retourne (action_dict, texte_sans_bloc) ou (None, response) si aucun bloc.
        """
        import json

        m = self._ACTION_RE.search(response)
        if not m:
            return None, response

        json_str = m.group(1).strip()
        clean_text = (response[: m.start()] + response[m.end() :]).strip()

        try:
            action = json.loads(json_str)
            if not isinstance(action, dict) or "type" not in action:
                logger.warning("[action] JSON invalide (pas de champ 'type') : %r", json_str[:100])
                return None, clean_text
            return action, clean_text
        except json.JSONDecodeError as e:
            logger.warning("[action] JSON malformé : %s — %r", e, json_str[:100])
            return None, clean_text

    async def _route_task(self, user_message: str, conversation_id: int = None,
                          context: dict = None, history: list = None) -> dict:
        """Routeur intra-agent : décide entre Claude (analyse/contexte) et Gemini (production lourde).

        Workflow Gemini :
            1. Claude Haiku produit un brief court (5 lignes max) extrayant
               type d'exercice / matière / consignes / format / niveau BTS.
            2. Le brief + la demande originale sont envoyés à Gemini CLI.
            3. Le system prompt JARVIS de l'agent est passé en system à Gemini.

        Workflow Claude : appel standard via `_call_claude` avec contexte mémoire.
        """
        ctx_raw = context or {}
        if ctx_raw.get("voice_mode"):
            return await self._call_claude(
                user_message,
                conversation_id=conversation_id,
                context=context,
                history=history,
                voice_mode=True,
                max_tokens=getattr(config, "VOICE_MAX_TOKENS", 500),
            )

        route = await llm.classify_task_type(user_message)

        if route == "gemini":
            logger.info(f"[{self.name}] Route → Gemini CLI (tâche lourde)")

            brief = await llm.chat(
                messages=[{"role": "user", "content": user_message}],
                model=config.DEEPSEEK_FAST_MODEL,
                system=(
                    "Analyse cette demande. Extrais en 5 lignes max : "
                    "le type d'exercice, la matière, les consignes précises, "
                    "le format attendu, le niveau BTS. "
                    "C'est un brief pour Gemini qui va produire le contenu."
                ),
                max_tokens=300,
                temperature=0.0,
            )

            full_prompt = (
                f"BRIEF :\n{brief['content']}\n\n"
                f"DEMANDE ORIGINALE :\n{user_message}"
            )
            system_prompt = self.build_system_prompt(context or {})

            return await self._call_gemini(
                full_prompt,
                conversation_id=conversation_id,
                system=system_prompt,
            )

        logger.info(f"[{self.name}] Route → Claude (analyse)")
        return await self._call_claude(
            user_message,
            conversation_id=conversation_id,
            context=context,
            history=history,
        )


# Registry global des agents
AGENTS: dict[str, BaseAgent] = {}


def register_agent(agent: BaseAgent):
    AGENTS[agent.name] = agent


def get_agent(name: str) -> BaseAgent | None:
    return AGENTS.get(name)
