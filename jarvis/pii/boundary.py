"""Data Boundary — filtre de contenu avant un appel DeepSeek.

Les contacts, messages et e-mails partent tels quels. Les secrets (clés,
jetons) sont masqués. ``check`` ne lève plus ``DataLeakError`` : il retourne
le texte filtré. ``sanitize_chunks`` conserve le texte intégral ; le masquage
des secrets se fait à ``check``, dernière ligne avant l'HTTP.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class DataBoundary:
    """Valide le type, masque les secrets, laisse les PII intactes."""

    def check(self, payload: str) -> str:
        """Refuse un payload non textuel. Retourne le texte prêt pour le LLM."""
        if not isinstance(payload, str):
            raise TypeError(f"check attend str, reçu {type(payload)!r}")
        # Import local : redaction → jarvis.pii → ce module.
        from jarvis.security.llm_data_boundary import redact_for_external_llm

        filtered = redact_for_external_llm(payload, max_chars=None)
        logger.debug("DataBoundary : payload accepté (%d caractères).", len(filtered))
        return filtered

    def sanitize_chunks(self, chunks: list[str]) -> list[str]:
        """Retourne les extraits tels quels, sans retirer ids ni horodatages."""
        if not isinstance(chunks, list):
            raise TypeError(f"sanitize_chunks attend list[str], reçu {type(chunks)!r}")

        cleaned: list[str] = []
        for index, chunk in enumerate(chunks):
            if not isinstance(chunk, str):
                raise TypeError(
                    f"chunk #{index} doit être str, reçu {type(chunk)!r}"
                )
            cleaned.append(chunk)
        return cleaned
