"""Routeur de compatibilité JARVIS vers DeepSeek.

Les chemins publics ``chat`` et ``summarize`` envoient le texte tel quel.
Les secrets restent masqués plus bas, dans ``redact_for_external_llm``.
"""

from __future__ import annotations

from typing import Optional

from jarvis.backends.deepseek import DeepSeekBackend
from jarvis.backends.local import LocalBackend
from jarvis.models import DataSource, EmailPayload, RouterStats
from jarvis.pii.anonymizer import PIIAnonymizer
from jarvis.pii.boundary import DataBoundary

_CHAT_SYSTEM_DEFAULT = (
    "Tu es JARVIS, l'assistant personnel d'Elias. Concis, précis, en français. "
    "N'affirme pas que le traitement est local ou strictement privé."
)


class JARVISRouter:
    """Point d'entrée de compatibilité : DeepSeek, contenu personnel intact."""

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
        # Conservé pour compatibilité des appelants ; plus utilisé à l'envoi.
        self.anonymizer: PIIAnonymizer = anonymizer or PIIAnonymizer()
        self.local: LocalBackend = local or LocalBackend()
        self.deepseek: DeepSeekBackend = deepseek or DeepSeekBackend(
            boundary=self.boundary, stats=self.stats
        )

    async def chat(self, prompt: str, system: Optional[str] = None) -> str:
        """Envoie le message à DeepSeek sans pseudonymiser les PII."""
        return await self._deepseek_passthrough(
            text=prompt,
            instruction=system or _CHAT_SYSTEM_DEFAULT,
        )

    async def mail(self, email_payload: EmailPayload) -> str:
        """Envoie l'email tel quel à DeepSeek."""
        if not isinstance(email_payload, EmailPayload):
            raise TypeError(
                f"mail() attend EmailPayload, reçu {type(email_payload)!r}"
            )
        source_text = f"Sujet : {email_payload.subject}\n\n{email_payload.body}"
        return await self._deepseek_passthrough(
            text=source_text,
            instruction=(
                "Voici un email reçu. Rédige une réponse appropriée et concise "
                "en français."
            ),
        )

    async def rag(self, query: str, chunks: list[str]) -> str:
        """Répond à ``query`` à partir des extraits, sans les altérer."""
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

    async def task(self, description: str) -> str:
        """Traite une tâche autonome via DeepSeek."""
        if not isinstance(description, str) or not description.strip():
            raise ValueError("task() : description vide.")
        result = await self.deepseek.generate(prompt=description)
        self.stats.deepseek_calls += 1
        return result

    async def summarize(self, text: str, source: DataSource) -> str:
        """Résume ``text`` via DeepSeek, contenu intact."""
        if not isinstance(source, DataSource):
            raise TypeError(f"summarize() : source invalide {source!r}")
        if not isinstance(text, str) or not text.strip():
            raise ValueError("summarize() : texte vide.")

        return await self._deepseek_passthrough(
            text=text,
            instruction="Résume ce contenu de façon concise, en français.",
        )

    async def _deepseek_passthrough(self, text: str, instruction: str) -> str:
        """Envoie le texte tel quel. Plus d'anonymisation aller-retour."""
        prompt = f"{instruction}\n\n{text}"
        raw_response = await self.deepseek.generate(
            prompt=prompt, system=_CHAT_SYSTEM_DEFAULT
        )
        self.stats.deepseek_calls += 1
        return raw_response
