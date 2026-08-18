"""Couche PII locale (logs, persistance). La sortie LLM n'anonymise plus.

- ``PIIAnonymizer`` : tokens opaques pour les journaux et les jobs persistés.
- ``DataBoundary``  : validation de type, plus de blocage de contenu personnel.
"""

from jarvis.pii.anonymizer import (
    AnonymizationResult,
    PIIAnonymizer,
    PIIMatch,
)
from jarvis.pii.boundary import DataBoundary

__all__ = [
    "PIIAnonymizer",
    "AnonymizationResult",
    "PIIMatch",
    "DataBoundary",
]
