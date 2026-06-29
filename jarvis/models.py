"""Types partagés du package dual-LLM.

Aucune dépendance vers les backends ou la base de données : ces types décrivent
les contrats d'entrée/sortie du routeur et restent volontairement minimaux.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class DataSource(Enum):
    """Origine d'une donnée — détermine le backend autorisé.

    - ``MESSAGES`` : conversations privées d'Elias → LOCAL uniquement.
    - ``EMAIL``    : email → DeepSeek après anonymisation PII.
    - ``DOCUMENT`` : extrait de document → DeepSeek après ``sanitize_chunks``.
    - ``WEB``      : contenu web public → DeepSeek libre.
    """

    MESSAGES = "messages"
    EMAIL = "email"
    DOCUMENT = "document"
    WEB = "web"


@dataclass(frozen=True)
class EmailPayload:
    """Charge utile d'un email à traiter par DeepSeek (après anonymisation).

    Volontairement dépourvu de tout champ lié à la base messages
    (``messages``, ``conversation`` interdits) pour rendre une fuite
    structurellement impossible depuis ce type.
    """

    subject: str
    body: str
    sender: str
    recipients: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not isinstance(self.subject, str):
            raise TypeError(f"subject doit être str, reçu {type(self.subject)!r}")
        if not isinstance(self.body, str):
            raise TypeError(f"body doit être str, reçu {type(self.body)!r}")
        if not isinstance(self.sender, str):
            raise TypeError(f"sender doit être str, reçu {type(self.sender)!r}")
        if not isinstance(self.recipients, list):
            raise TypeError(
                f"recipients doit être list[str], reçu {type(self.recipients)!r}"
            )


@dataclass
class RouterStats:
    """Compteurs d'observabilité du routeur (jamais de PII ici).

    ``boundary_violations`` doit rester à 0 en fonctionnement nominal : toute
    valeur > 0 signale qu'une tentative de fuite a été interceptée.
    """

    local_calls: int = 0
    deepseek_calls: int = 0
    pii_entities_masked: int = 0
    boundary_violations: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "local_calls": self.local_calls,
            "deepseek_calls": self.deepseek_calls,
            "pii_entities_masked": self.pii_entities_masked,
            "boundary_violations": self.boundary_violations,
        }
