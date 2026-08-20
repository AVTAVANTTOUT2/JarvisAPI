"""Couche PII locale (logs, persistance). La sortie LLM n'anonymise plus.

- ``PIIAnonymizer`` : tokens opaques pour les journaux et les jobs persistés.
- ``DataBoundary``  : masque les secrets, laisse les PII intactes.
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
