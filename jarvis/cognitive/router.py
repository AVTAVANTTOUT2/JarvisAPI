"""Routeur cognitif central — Flash / Main / Cursor / outils déterministes.

Partagé par chat WebSocket, REST, Android, voix, iMessage, briefings et /loop.
Les règles déterministes passent avant tout appel LLM de classification.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from typing import Any

import config
from jarvis.cognitive.models import TaskIntent

logger = logging.getLogger(__name__)

_FAST = lambda: getattr(config, "VOICE_REASONING_MODEL", None) or config.DEEPSEEK_FAST_MODEL
_MAIN = lambda: getattr(config, "MAIN_REASONING_MODEL", None) or config.DEEPSEEK_MAIN_MODEL

# Signaux techniques FORTS → Cursor directement (action de code explicite)
_TECH_STRONG_PATTERNS = (
    r"\b(stack\s*trace|traceback|crash\s+loop|exception\s+backend)\b",
    r"\b(pull\s*request|ouvre\s+une\s+PR|rebase|merge\s+la\s+branche)\b",
    r"\b(corrige|fixe|répare|repare|debug(?:ue)?)\b.*\b(bug|code|test|ci|build|crash|erreur|backend|frontend|android|app(?:li)?)\b",
    r"\b(implémente|implemente|développe|developpe|code)\b.*\b(fonctionnalité|fonctionnalite|feature|page|endpoint|écran|ecran|module)\b",
    r"\b(crée|cree|ajoute)\b.*\b(migration|endpoint|page|composant|test[s]?\s+unitaires?)\b",
    r"\b(répare|repare|corrige)\s+(la\s+)?ci\b",
    r"\b(refactor|refactorise|compile|déploie|deploie|build\s+l)\b",
    r"\b(analyse\s+l['']architecture|audit\s+(de\s+)?(sécu|secu|perf|code))\b",
    r"\b(auto[-\s]?réparation|auto[-\s]?reparation|self[-\s]?repair|worktree)\b",
    r"\bmodifie\b.*\b(le\s+)?(code|dépôt|depot|repo|fichier\s+source)\b",
)

# Noms techniques FAIBLES : ne délèguent que combinés à un verbe d'action
_TECH_WEAK_NOUNS = (
    r"\b(code|bug|dépôt|depot|repo|commit|branche|branch|git)\b",
    r"\b(migration|sqlite|schema|dockerfile|docker|ci\b|pytest|unittest)\b",
    r"\b(apk|gradle|release|deploy|build)\b",
    r"\b(frontend|backend|android|api\s*router|endpoint)\b",
    r"\b(cursor)\b",
)

_TECH_ACTION_VERBS = (
    r"\b(corrige|fixe|répare|repare|implémente|implemente|ajoute|modifie|développe|"
    r"developpe|crée|cree|optimise|déploie|deploie|compile|refactor|répare|installe|"
    r"applique|met[s]?\s+à\s+jour|mets\s+a\s+jour|travaille\s+sur|debug)\b",
)

# Questions techniques purement explicatives → DeepSeek (pas Cursor)
_EXPLAIN_PATTERNS = (
    r"\b(explique|c['']est\s+quoi|qu['']est[- ]ce\s+qu|définition|definition|comment\s+ça\s+marche|difference\s+entre)\b",
)

# Réflexion lourde non technique → Main
_HEAVY_REASONING_PATTERNS = (
    r"\b(stratégie|strategie|planifie|organise\s+ma\s+journée|organise\s+ma\s+journee)\b",
    r"\b(analyse\s+(profonde|relationnelle|complète|complete)|décision\s+importante|decision\s+importante)\b",
    r"\b(compar(e|er)\s+les\s+options|plan\s+d['']action|prépare\s+la\s+réunion|prepare\s+la\s+reunion)\b",
    r"\b(dissertation|rapport\s+long|rédige\s+un|redige\s+un)\b",
    r"\b(briefing\s+complet|fais[- ]moi\s+un\s+plan)\b",
)

# Outils déterministes — les motifs les plus spécifiques d'abord
_TOOL_PATTERNS: list[tuple[str, str]] = [
    (r"\b(météo|meteo|parapluie|température|temperature|quel temps|le temps qu)\b", "info"),
    (r"\b(crée|cree|ajoute)\s+(une?\s+)?tâche", "productivity"),
    (r"\b(agenda|calendrier|rendez[- ]vous|rdv|demain|aujourd['']hui)\b", "productivity"),
    (r"\b(ouvre|lance)\s+\w+", "system"),
    (r"\b(où\s+suis[- ]je|localisation|où\s+est)\b", "location"),
    (r"\b(envoie|envoi|message|sms|imessage)\s+(à|a)\b", "contacts"),
    (r"\b(numéro|numero|téléphone|telephone|email)\s+(de|d[''])", "contacts"),
    (r"\b(télé|tele|tv\b|philips)\b", "tv"),
]

_CONTACT_PATTERNS = (
    r"\b(appelle|contact|maman|papa|numéro|numero|message\s+à|message\s+a)\b",
)

_BRIEFING_PATTERNS = (
    r"\b(briefing|fais[- ]moi\s+le\s+point|quoi\s+de\s+neuf|résumé\s+du\s+(matin|soir)|resume\s+du\s+(matin|soir))\b",
    r"\b(version\s+courte|seulement\s+(les\s+urgences|le\s+travail)|qu['']est[- ]ce\s+qui\s+a\s+changé)\b",
    r"\b(fais\s+le\s+point\s+sur\s+jarvis|qu['']est[- ]ce\s+que\s+cursor\s+a\s+(terminé|termine|fini))\b",
)

_VOICE_ACK_CURSOR = (
    "Je m'en occupe, Monsieur. Je confie l'analyse et la correction à Cursor "
    "et je vous rends compte dès que les tests sont terminés."
)
_VOICE_ACK_HEAVY = (
    "Très bien, Monsieur. J'analyse l'ensemble et je vous prépare une réponse structurée."
)
_VOICE_ACK_FEATURE = (
    "Très bien, Monsieur. Je prépare le cahier des charges et je délègue "
    "l'implémentation à Cursor."
)


def _fold(text: str) -> str:
    """Normalise accents + casse pour matching robuste."""
    nfkd = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower()


def _matches_any(text: str, patterns: tuple[str, ...] | list[str]) -> bool:
    return any(re.search(p, text, re.IGNORECASE) for p in patterns)


def _detect_template(text: str) -> str:
    t = _fold(text)
    if any(k in t for k in ("android", "apk", "compose", "kotlin")):
        return "android_feature"
    if any(k in t for k in ("frontend", "react", "next.js", "tailwind")):
        return "frontend_feature"
    if any(k in t for k in ("migration", "sqlite", "schema")):
        return "database_migration"
    if any(k in t for k in ("ci", "github actions", "pipeline")):
        return "ci_repair"
    if any(k in t for k in ("perf", "latence", "lent", "optimisation")):
        return "performance_audit"
    if any(k in t for k in ("securite", "security", "owasp", "vuln")):
        return "security_audit"
    if any(k in t for k in ("voix", "voice", "stt", "tts", "vad")):
        return "voice_pipeline"
    if any(k in t for k in ("test", "pytest", "coverage")):
        return "test_creation"
    if any(k in t for k in ("refactor",)):
        return "refactor_safe"
    if any(k in t for k in ("bug", "corrige", "fixe", "crash", "erreur")):
        return "bug_fix"
    if any(k in t for k in ("auto-reparation", "self repair", "self-heal")):
        return "self_repair"
    if any(k in t for k in ("ameliore", "amélioration", "self improvement")):
        return "self_improvement"
    if any(k in t for k in ("ajoute", "cree", "implémente", "implemente", "fonctionnalite")):
        return "feature_implementation"
    return "backend_feature"


class CognitiveRouter:
    """Routeur unique et observable."""

    def route(
        self,
        text: str,
        *,
        interaction_mode: str = "chat",
        force_domain: str | None = None,
    ) -> TaskIntent:
        folded = _fold(text)
        mode = interaction_mode if interaction_mode in (
            "voice", "chat", "imessage", "scheduled", "loop", "android"
        ) else "chat"
        is_voice = mode in ("voice", "android")

        # 1) Briefings
        if _matches_any(folded, _BRIEFING_PATTERNS):
            return TaskIntent(
                interaction_mode=mode,  # type: ignore[arg-type]
                domain="briefing",
                complexity="heavy" if "complet" in folded else "standard",
                execution_type="answer",
                reasoning_model=_MAIN() if "complet" in folded else _FAST(),
                prompt_model=_MAIN(),
                reason="demande de briefing",
                context_budget="briefing",
                voice_ack="Je prépare votre briefing, Monsieur." if is_voice else None,
                expected_duration="seconds",
            )

        # 2) Technique explicatif → DeepSeek (pas Cursor)
        is_tech_strong = _matches_any(folded, _TECH_STRONG_PATTERNS)
        is_tech_weak = _matches_any(folded, _TECH_WEAK_NOUNS) and _matches_any(
            folded, _TECH_ACTION_VERBS
        )
        is_tech = is_tech_strong or is_tech_weak
        is_explain = _matches_any(folded, _EXPLAIN_PATTERNS)
        if (is_tech or _matches_any(folded, _TECH_WEAK_NOUNS)) and is_explain and not _matches_any(
            folded,
            (r"\b(crée|cree|applique|corrige|fixe|implémente|implemente|modifie|ajoute)\b",),
        ):
            return TaskIntent(
                interaction_mode=mode,  # type: ignore[arg-type]
                domain="dev_explain",
                complexity="standard",
                execution_type="answer",
                reasoning_model=_FAST() if is_voice else _MAIN(),
                prompt_model=_MAIN(),
                reason="question technique explicative — DeepSeek, pas Cursor",
                context_budget="minimal",
            )

        # 3) Exécution technique → Cursor (si la capacité est disponible)
        if is_tech:
            # Lecture directe de la config + cache CLI (pas le singleton du
            # registre : il peut être rafraîchi après un changement de config).
            cursor_available = bool(getattr(config, "CURSOR_DELEGATION_ENABLED", True))
            if cursor_available:
                try:
                    from integrations.cursor_delegation import cursor_delegation

                    cached = cursor_delegation._cli_info
                    if cached is not None:
                        cursor_available = (
                            bool(cached.available) and cached.authenticated is not False
                        )
                except Exception:  # cache indisponible → optimiste, l'enqueue revalidera
                    pass
            template = _detect_template(folded)
            ack = _VOICE_ACK_FEATURE if "feature" in template or "ajoute" in folded else _VOICE_ACK_CURSOR
            if cursor_available:
                return TaskIntent(
                    interaction_mode=mode,  # type: ignore[arg-type]
                    domain="dev",
                    complexity="heavy",
                    execution_type="cursor",
                    reasoning_model=_FAST() if is_voice else _MAIN(),
                    prompt_model=_MAIN(),
                    reason="modification de code / tâche technique demandée",
                    risk_level="medium",
                    requires_confirmation=False,
                    expected_duration="minutes",
                    template_id=template,
                    context_budget="dev",
                    voice_ack=ack if is_voice else None,
                )
            # Cursor indisponible → réponse honnête via Main, jamais de fausse promesse
            return TaskIntent(
                interaction_mode=mode,  # type: ignore[arg-type]
                domain="dev",
                complexity="heavy",
                execution_type="answer",
                reasoning_model=_FAST() if is_voice else _MAIN(),
                prompt_model=_MAIN(),
                reason="tâche technique mais Cursor CLI indisponible — réponse conseil",
                risk_level="low",
                context_budget="dev",
            )

        # 4) Contacts
        if force_domain == "contacts" or _matches_any(folded, _CONTACT_PATTERNS):
            return TaskIntent(
                interaction_mode=mode,  # type: ignore[arg-type]
                domain="contacts",
                complexity="instant",
                execution_type="tool",
                reasoning_model=_FAST(),
                reason="résolution de contact déterministe + formulation Flash",
                context_budget="contact",
                risk_level="medium" if "envoie" in folded or "envoi" in folded else "low",
                requires_confirmation="envoie" in folded or "envoi" in folded or "appelle" in folded,
            )

        # 5) Outils déterministes
        for pattern, domain in _TOOL_PATTERNS:
            if re.search(pattern, folded, re.IGNORECASE):
                return TaskIntent(
                    interaction_mode=mode,  # type: ignore[arg-type]
                    domain=domain,
                    complexity="instant",
                    execution_type="tool",
                    reasoning_model=_FAST(),
                    reason=f"action déterministe domaine={domain}",
                    context_budget="tool",
                )

        # 6) Réflexion lourde non technique
        if _matches_any(folded, _HEAVY_REASONING_PATTERNS):
            return TaskIntent(
                interaction_mode=mode,  # type: ignore[arg-type]
                domain="strategy",
                complexity="heavy",
                execution_type="answer",
                reasoning_model=_FAST() if is_voice else _MAIN(),
                prompt_model=_MAIN(),
                reason="réflexion lourde non technique → DeepSeek Main",
                context_budget="standard",
                expected_duration="minutes",
                voice_ack=_VOICE_ACK_HEAVY if is_voice else None,
            )

        # 7) Défaut conversationnel — Flash partout (voix comme texte)
        return TaskIntent(
            interaction_mode=mode,  # type: ignore[arg-type]
            domain=force_domain or "general",
            complexity="instant" if is_voice else "standard",
            execution_type="answer",
            reasoning_model=_FAST(),
            prompt_model=_FAST(),
            reason="conversation standard — DeepSeek Flash",
            context_budget="minimal" if is_voice else "standard",
        )

    async def route_async(
        self,
        text: str,
        *,
        interaction_mode: str = "chat",
        force_domain: str | None = None,
        use_llm_fallback: bool = False,
    ) -> TaskIntent:
        """Route synchrone + classification LLM optionnelle si ambigu."""
        intent = self.route(text, interaction_mode=interaction_mode, force_domain=force_domain)
        if not use_llm_fallback or intent.execution_type != "answer" or intent.domain != "general":
            return intent

        try:
            import llm

            label = await llm.quick_classify(
                text,
                ["ANSWER", "TOOL", "CURSOR", "HEAVY"],
                model=_FAST(),
            )
            if label == "CURSOR":
                intent.execution_type = "cursor"
                intent.domain = "dev"
                intent.prompt_model = _MAIN()
                intent.template_id = _detect_template(_fold(text))
                intent.reason = "classification LLM → Cursor"
                if interaction_mode in ("voice", "android"):
                    intent.voice_ack = _VOICE_ACK_CURSOR
            elif label == "HEAVY":
                intent.complexity = "heavy"
                intent.prompt_model = _MAIN()
                if interaction_mode not in ("voice", "android"):
                    intent.reasoning_model = _MAIN()
                intent.reason = "classification LLM → raisonnement Main"
            elif label == "TOOL":
                intent.execution_type = "tool"
                intent.reason = "classification LLM → outil"
        except Exception as exc:
            logger.debug("[cognitive] fallback LLM skip: %s", exc)
        return intent


_default_router = CognitiveRouter()


def route_request(
    text: str,
    *,
    interaction_mode: str = "chat",
    force_domain: str | None = None,
) -> TaskIntent:
    """Point d'entrée synchrone partagé."""
    return _default_router.route(
        text, interaction_mode=interaction_mode, force_domain=force_domain
    )


def get_cognitive_router() -> CognitiveRouter:
    return _default_router
