"""Routeur principal du dual-LLM JARVIS — séparation stricte et définitive.

Règle absolue : les messages d'Elias (``chat``, ``summarize`` source MESSAGES)
restent en LOCAL. Tout le reste passe par DeepSeek, et toute PII est
pseudonymisée avant l'envoi puis restaurée à la réception. Le mapping PII est
détruit immédiatement après usage et n'est jamais loggué ni persisté.
"""

from __future__ import annotations

import logging
from typing import Optional

from jarvis.backends.deepseek import DeepSeekBackend
from jarvis.backends.local import LocalBackend
from jarvis.models import DataSource, EmailPayload, RouterStats
from jarvis.pii.anonymizer import AnonymizationResult, PIIAnonymizer
from jarvis.pii.boundary import DataBoundary

logger = logging.getLogger(__name__)

# System prompt imposé à DeepSeek quand des tokens PII sont présents.
_PII_SYSTEM_PREAMBLE = (
    "Tu es JARVIS, assistant de Monsieur. Certaines informations sensibles ont "
    "été masquées par des tokens de la forme [PERSON_1], [EMAIL_1], [ORG_1], "
    "etc. Garde STRICTEMENT ces tokens intacts dans ta réponse, sans les "
    "modifier, traduire ni deviner leur contenu."
)
_CHAT_SYSTEM_DEFAULT = (
    "Tu es JARVIS, l'assistant personnel d'Elias. Concis, précis, en français. "
    "Tu tournes en local : ces échanges sont strictement privés."
)


class JARVISRouter:
    """Point d'entrée unique : choisit le backend selon la nature de la donnée."""

    def __init__(
        self,
        local: Optional[LocalBackend] = None,
        deepseek: Optional[DeepSeekBackend] = None,
        anonymizer: Optional[PIIAnonymizer] = None,
        boundary: Optional[DataBoundary] = None,
        stats: Optional[RouterStats] = None,
    ) -> None:
        self.stats: RouterStats = stats or RouterStats()
        self.boundary: DataBoundary = boundary or DataBoundary()
        self.anonymizer: PIIAnonymizer = anonymizer or PIIAnonymizer()
        self.local: LocalBackend = local or LocalBackend()
        self.deepseek: DeepSeekBackend = deepseek or DeepSeekBackend(
            boundary=self.boundary, stats=self.stats
        )

    # ── Chat Elias → DeepSeek après anonymisation (politique 2026) ──

    async def chat(self, prompt: str, system: Optional[str] = None) -> str:
        """Traite un message sensible via DeepSeek après anonymisation PII.

        Plus aucun LLM local de raisonnement (MLX/Ollama) sur ce chemin —
        Ollama reste réservé au Screen Watcher.
        """
        return await self._deepseek_anonymized(
            text=prompt,
            instruction=system or _CHAT_SYSTEM_DEFAULT,
        )

    # ── Email → DeepSeek après anonymisation ─────────────────

    async def mail(self, email_payload: EmailPayload) -> str:
        """Anonymise l'email, le traite via DeepSeek, puis dé-anonymise."""
        if not isinstance(email_payload, EmailPayload):
            raise TypeError(
                f"mail() attend EmailPayload, reçu {type(email_payload)!r}"
            )
        source_text = f"Sujet : {email_payload.subject}\n\n{email_payload.body}"
        return await self._deepseek_anonymized(
            text=source_text,
            instruction=(
                "Voici un email reçu. Rédige une réponse appropriée et concise "
                "en français, en conservant les tokens masqués tels quels."
            ),
        )

    # ── RAG → DeepSeek après sanitize ────────────────────────

    async def rag(self, query: str, chunks: list[str]) -> str:
        """Répond à ``query`` à partir d'extraits de documents (jamais messages)."""
        if not isinstance(query, str) or not query.strip():
            raise ValueError("rag() : query vide.")
        clean_chunks = self.boundary.sanitize_chunks(chunks)
        context = "\n\n---\n\n".join(clean_chunks) if clean_chunks else "(aucun extrait)"
        prompt = (
            f"Contexte documentaire :\n{context}\n\n"
            f"Question : {query}\n\n"
            "Réponds uniquement à partir du contexte ci-dessus, en français."
        )
        result = await self.deepseek.generate(prompt=prompt)
        self.stats.deepseek_calls += 1
        return result

    # ── Tâche → DeepSeek ─────────────────────────────────────

    async def task(self, description: str) -> str:
        """Traite une tâche autonome (pas de données messages) via DeepSeek."""
        if not isinstance(description, str) or not description.strip():
            raise ValueError("task() : description vide.")
        result = await self.deepseek.generate(prompt=description)
        self.stats.deepseek_calls += 1
        return result

    # ── Résumé → routage selon la source ─────────────────────

    async def summarize(self, text: str, source: DataSource) -> str:
        """Résume ``text`` via DeepSeek après anonymisation (toutes sources)."""
        if not isinstance(source, DataSource):
            raise TypeError(f"summarize() : source invalide {source!r}")
        if not isinstance(text, str) or not text.strip():
            raise ValueError("summarize() : texte vide.")

        return await self._deepseek_anonymized(
            text=text,
            instruction="Résume ce contenu de façon concise, en français, en "
            "conservant les tokens masqués tels quels.",
        )

    # ── Pipeline commun DeepSeek + anonymisation ─────────────

    async def _deepseek_anonymized(self, text: str, instruction: str) -> str:
        """Anonymise → DeepSeek → dé-anonymise. Mapping détruit en fin de flux."""
        anonymized: AnonymizationResult = self.anonymizer.anonymize(text)
        self.stats.pii_entities_masked += anonymized.entities_masked
        prompt = f"{instruction}\n\n{anonymized.anonymized_text}"
        try:
            raw_response = await self.deepseek.generate(
                prompt=prompt, system=_PII_SYSTEM_PREAMBLE
            )
            self.stats.deepseek_calls += 1
            return self.anonymizer.deanonymize(raw_response, anonymized.mapping)
        finally:
            # Filet de sécurité : le mapping ne survit jamais, même en cas d'erreur.
            anonymized.mapping.clear()
