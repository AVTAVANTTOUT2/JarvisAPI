"""Couche de protection des données personnelles (PII).

- ``PIIAnonymizer`` : pseudonymisation réversible par tokens opaques.
- ``DataBoundary``  : garde-fou interdisant toute fuite de données messages.
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
