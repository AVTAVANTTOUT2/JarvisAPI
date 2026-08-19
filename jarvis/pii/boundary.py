"""Data Boundary — validation de type avant un appel DeepSeek.

Le filtre anti-PII (téléphones, e-mails, signatures de la base messages) a
été retiré : contacts et messages partent tels quels. ``check`` ne bloque plus
le contenu. ``sanitize_chunks`` conserve le texte intégral.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class DataBoundary:
    """Valide le type du payload. Ne filtre plus le contenu personnel."""

    FORBIDDEN_PATTERNS: tuple[str, ...] = ()

    def check(self, payload: str) -> None:
        """Refuse uniquement un payload non textuel."""
        if not isinstance(payload, str):
            raise TypeError(f"check attend str, reçu {type(payload)!r}")
        logger.debug("DataBoundary : payload accepté (%d caractères).", len(payload))

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
