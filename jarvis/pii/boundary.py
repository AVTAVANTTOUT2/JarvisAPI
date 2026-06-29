"""Data Boundary Enforcer — garde-fou anti-fuite vers DeepSeek.

Vérifie qu'aucun payload sortant ne contient de signature de données messages
brutes ou de métadonnées de base (ids, requêtes SQL sur la table messages…).
Appelé automatiquement par ``DeepSeekBackend.generate`` AVANT chaque requête
HTTP — c'est non-négociable et non-configurable côté appelant.
"""

from __future__ import annotations

import logging
import re

from jarvis.exceptions import DataLeakError

logger = logging.getLogger(__name__)

# Métadonnées DB à retirer des chunks RAG (texte conservé, identifiants ôtés).
_METADATA_LINE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?im)^\s*(?:message_id|conversation_id|chat_id|handle_id|rowid)\s*[=:].*$"),
    re.compile(r"(?im)^\s*(?:created_at|updated_at|timestamp|date_inserted)\s*[=:].*$"),
)
_INLINE_METADATA_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)\b(?:message_id|conversation_id|chat_id|handle_id|rowid)\s*[=:]\s*\d+"),
    re.compile(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+\-]\d{2}:?\d{2})?\b"),
)


class DataBoundary:
    """Inspecte les payloads sortants et bloque toute fuite de données messages."""

    # Signatures qui trahissent une fuite de données issues de la base messages.
    FORBIDDEN_PATTERNS: tuple[str, ...] = (
        r"message_id\s*[=:]\s*\d+",
        r"conversation_id\s*[=:]\s*\d+",
        r"SELECT\s+.*\s+FROM\s+messages",
        r"db\.messages\.",
    )

    def __init__(self) -> None:
        self._compiled: tuple[re.Pattern[str], ...] = tuple(
            re.compile(pattern, re.IGNORECASE | re.DOTALL)
            for pattern in self.FORBIDDEN_PATTERNS
        )

    def check(self, payload: str) -> None:
        """Lève ``DataLeakError`` si une signature interdite est détectée.

        Trace systématiquement un enregistrement d'audit (longueur du payload,
        jamais son contenu pour ne pas logger de PII) et un WARNING en cas de
        violation.
        """
        if not isinstance(payload, str):
            raise TypeError(f"check attend str, reçu {type(payload)!r}")

        logger.debug("DataBoundary audit : inspection payload (%d caractères).", len(payload))

        for pattern in self._compiled:
            match = pattern.search(payload)
            if match is not None:
                logger.warning(
                    "DataBoundary VIOLATION : signature interdite '%s' détectée — "
                    "requête DeepSeek bloquée.",
                    pattern.pattern,
                )
                raise DataLeakError(
                    f"Fuite de données messages bloquée : le payload correspond au "
                    f"motif interdit '{pattern.pattern}'. Aucune requête DeepSeek "
                    f"n'a été émise."
                )

    def sanitize_chunks(self, chunks: list[str]) -> list[str]:
        """Retire les métadonnées DB des chunks RAG, ne garde que le texte.

        Supprime les lignes d'identifiants/timestamps puis les occurrences
        inline, et écarte les chunks devenus vides.
        """
        if not isinstance(chunks, list):
            raise TypeError(f"sanitize_chunks attend list[str], reçu {type(chunks)!r}")

        cleaned: list[str] = []
        for index, chunk in enumerate(chunks):
            if not isinstance(chunk, str):
                raise TypeError(
                    f"chunk #{index} doit être str, reçu {type(chunk)!r}"
                )
            text = chunk
            for line_pattern in _METADATA_LINE_PATTERNS:
                text = line_pattern.sub("", text)
            for inline_pattern in _INLINE_METADATA_PATTERNS:
                text = inline_pattern.sub("", text)
            text = re.sub(r"\n{3,}", "\n\n", text).strip()
            if text:
                cleaned.append(text)
        return cleaned
