"""Pseudonymisation réversible des PII par tokens opaques.

Principe : DeepSeek doit pouvoir *lire* un texte cohérent — on ne chiffre donc
pas, on remplace chaque entité sensible par un token stable de la forme
``[PERSON_1]``, ``[EMAIL_2]``… Le mapping token→valeur reste strictement en
mémoire (jamais loggué, sérialisé ni écrit sur disque) et est détruit dès la
dé-anonymisation effectuée.

Détection en deux temps :

1. Regex déterministes pour les entités structurées (email, téléphone, IBAN,
   carte bancaire, dates) — fiables et indépendantes de tout modèle.
2. NER spaCy (``fr_core_news_sm``) pour les entités linguistiques (personnes,
   organisations, adresses). Si spaCy est indisponible, un fallback regex
   heuristique prend le relais (noms précédés d'une civilité, etc.).

Les entités structurées priment toujours sur le NER en cas de chevauchement.
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass, field
from typing import Optional

from jarvis import settings

logger = logging.getLogger(__name__)

# ── Types d'entités et préfixes de token ─────────────────────
ENTITY_EMAIL = "EMAIL"
ENTITY_PHONE = "PHONE"
ENTITY_ADDRESS = "ADDRESS"
ENTITY_PERSON = "PERSON"
ENTITY_ORG = "ORG"
ENTITY_FINANCIAL = "FINANCIAL"
ENTITY_DOB = "DOB"

# Priorité de résolution des chevauchements (plus haut = gagne).
# Les entités structurées (regex) priment sur le NER linguistique.
_ENTITY_PRIORITY: dict[str, int] = {
    ENTITY_FINANCIAL: 100,
    ENTITY_EMAIL: 90,
    ENTITY_PHONE: 80,
    ENTITY_DOB: 70,
    ENTITY_ADDRESS: 40,
    ENTITY_ORG: 30,
    ENTITY_PERSON: 20,
}

# ── Regex déterministes ──────────────────────────────────────
# Email : sous-ensemble pragmatique de RFC 5322 (suffisant pour la détection).
_EMAIL_RE = re.compile(
    r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"
)

# IBAN : 2 lettres pays + 2 chiffres clé + 11 à 30 caractères alphanumériques.
_IBAN_RE = re.compile(
    r"\b[A-Z]{2}\d{2}(?:[ ]?[A-Za-z0-9]){11,30}\b"
)

# Carte bancaire : 13 à 19 chiffres, séparateurs espace/tiret tolérés.
_CARD_RE = re.compile(
    r"\b(?:\d[ \-]?){12}\d(?:[ \-]?\d){0,6}\b"
)

# Téléphone FR / international : +33, 0033, ou 0 suivi de 9 chiffres groupés.
_PHONE_RE = re.compile(
    r"(?<![\w.])(?:(?:\+|00)\d{1,3}[ .\-]?)?(?:\(0\)[ .\-]?)?"
    r"(?:\d[ .\-]?){8,12}\d(?![\w])"
)

# Dates (potentielles dates de naissance) : 12/05/1998, 12-05-98, 12 mai 1998.
_DATE_NUMERIC_RE = re.compile(
    r"\b(?:0?[1-9]|[12]\d|3[01])[/.\-](?:0?[1-9]|1[0-2])[/.\-](?:\d{4}|\d{2})\b"
)
_DATE_TEXT_RE = re.compile(
    r"\b(?:0?[1-9]|[12]\d|3[01])\s+"
    r"(?:janvier|février|fevrier|mars|avril|mai|juin|juillet|août|aout|"
    r"septembre|octobre|novembre|décembre|decembre)\s+\d{4}\b",
    re.IGNORECASE,
)

# Civilité + nom propre (fallback sans spaCy).
_CIVILITY_NAME_RE = re.compile(
    r"\b(?:M\.|MM\.|Mme|Mmes|Mlle|Dr|Pr|Me|Monsieur|Madame|Mademoiselle|Maître)"
    r"\s+([A-ZÀ-Ÿ][\wÀ-ÿ'\-]+(?:\s+[A-ZÀ-Ÿ][\wÀ-ÿ'\-]+){0,2})"
)
# Séquence de 2+ mots capitalisés consécutifs (heuristique nom complet).
_CAP_SEQUENCE_RE = re.compile(
    r"\b[A-ZÀ-Ÿ][a-zà-ÿ'\-]+(?:\s+[A-ZÀ-Ÿ][a-zà-ÿ'\-]+)+\b"
)

# Token opaque tel qu'inséré dans le texte : [TYPE_N].
_TOKEN_RE = re.compile(r"\[[^\[\]]{1,64}\]")

# Cache module-level du pipeline spaCy (chargement coûteux, fait une seule fois).
_SPACY_PIPELINE = None
_SPACY_LOAD_ATTEMPTED = False


@dataclass(frozen=True)
class PIIMatch:
    """Une occurrence de PII détectée dans le texte source."""

    start: int
    end: int
    text: str
    entity_type: str

    @property
    def priority(self) -> int:
        return _ENTITY_PRIORITY.get(self.entity_type, 0)


@dataclass
class AnonymizationResult:
    """Résultat d'une anonymisation.

    ``mapping`` ne doit jamais être loggué ni persisté. ``session_id`` est un
    UUID v4 unique, non réutilisé d'un appel à l'autre.
    """

    anonymized_text: str
    mapping: dict[str, str]
    session_id: str

    @property
    def entities_masked(self) -> int:
        return len(self.mapping)


def _load_spacy():
    """Charge le pipeline spaCy une seule fois ; None si indisponible."""
    global _SPACY_PIPELINE, _SPACY_LOAD_ATTEMPTED
    if _SPACY_LOAD_ATTEMPTED:
        return _SPACY_PIPELINE
    _SPACY_LOAD_ATTEMPTED = True
    if not settings.PII_USE_SPACY:
        logger.info("spaCy désactivé (JARVIS_PII_USE_SPACY=false) — fallback regex.")
        return None
    try:
        import spacy  # type: ignore

        _SPACY_PIPELINE = spacy.load(
            settings.SPACY_MODEL, disable=["lemmatizer", "tagger", "parser"]
        )
        logger.info("Pipeline spaCy '%s' chargé pour le NER PII.", settings.SPACY_MODEL)
    except Exception as exc:  # ImportError ou modèle absent — fallback regex.
        logger.warning(
            "spaCy indisponible (%s) — fallback regex heuristique pour les noms. "
            "Installe : python -m spacy download %s",
            exc,
            settings.SPACY_MODEL,
        )
        _SPACY_PIPELINE = None
    return _SPACY_PIPELINE


def _normalize_token_key(raw_token: str) -> str:
    """Normalise un token pour une comparaison tolérante.

    ``[Person_1]``, ``[PERSON 1]`` et ``[ person_1 ]`` donnent tous ``PERSON1``.
    Permet de retrouver le mapping même si DeepSeek a reformaté la casse ou les
    séparateurs du token.
    """
    inner = raw_token.strip().strip("[]")
    return re.sub(r"[^A-Za-z0-9]", "", inner).upper()


class PIIAnonymizer:
    """Pseudonymise et restaure les PII via des tokens opaques en mémoire."""

    def anonymize(self, text: str) -> AnonymizationResult:
        """Remplace les PII de ``text`` par des tokens, retourne le mapping.

        Une même entité (même valeur normalisée) reçoit toujours le même token.
        Le mapping vit uniquement en mémoire dans l'objet retourné.
        """
        if not isinstance(text, str):
            raise TypeError(f"anonymize attend str, reçu {type(text)!r}")

        session_id = str(uuid.uuid4())
        if not text:
            return AnonymizationResult(
                anonymized_text="", mapping={}, session_id=session_id
            )

        matches = self._detect_all(text)
        resolved = self._resolve_overlaps(matches)

        # Attribution déterministe des tokens (ordre d'apparition dans le texte).
        resolved.sort(key=lambda m: m.start)
        type_counters: dict[str, int] = {}
        value_to_token: dict[tuple[str, str], str] = {}
        mapping: dict[str, str] = {}
        replacements: list[tuple[int, int, str]] = []

        for match in resolved:
            value_key = (match.entity_type, self._canonical_value(match.text))
            token = value_to_token.get(value_key)
            if token is None:
                type_counters[match.entity_type] = (
                    type_counters.get(match.entity_type, 0) + 1
                )
                token = f"[{match.entity_type}_{type_counters[match.entity_type]}]"
                value_to_token[value_key] = token
                mapping[token] = match.text
            replacements.append((match.start, match.end, token))

        anonymized = self._apply_replacements(text, replacements)
        logger.debug(
            "Anonymisation session=%s : %d entité(s) masquée(s).",
            session_id,
            len(mapping),
        )
        return AnonymizationResult(
            anonymized_text=anonymized, mapping=mapping, session_id=session_id
        )

    def deanonymize(self, text: str, mapping: dict[str, str]) -> str:
        """Restaure les valeurs originales puis détruit le mapping.

        Tolère un reformatage des tokens par le LLM (casse, séparateurs).
        Après l'appel, ``mapping`` est vidé (les secrets ne survivent pas).
        """
        if not isinstance(text, str):
            raise TypeError(f"deanonymize attend str, reçu {type(text)!r}")
        if mapping is None:
            raise ValueError("mapping ne peut pas être None")

        if not mapping:
            return text

        lookup = {_normalize_token_key(token): value for token, value in mapping.items()}

        def _restore(match: re.Match[str]) -> str:
            key = _normalize_token_key(match.group(0))
            return lookup.get(key, match.group(0))

        restored = _TOKEN_RE.sub(_restore, text)

        # Destruction du mapping : les valeurs sensibles ne doivent pas survivre.
        mapping.clear()
        return restored

    # ── Détection ────────────────────────────────────────────

    def _detect_all(self, text: str) -> list[PIIMatch]:
        matches: list[PIIMatch] = []
        matches.extend(self._detect_pii_regex(text))
        matches.extend(self._detect_pii_spacy(text))
        return matches

    def _detect_pii_regex(self, text: str) -> list[PIIMatch]:
        """Détecte les entités structurées par regex (fallback inclus)."""
        found: list[PIIMatch] = []

        def _collect(pattern: re.Pattern[str], entity_type: str, group: int = 0) -> None:
            for m in pattern.finditer(text):
                start, end = m.span(group)
                value = m.group(group)
                if value and value.strip():
                    found.append(PIIMatch(start, end, value, entity_type))

        _collect(_EMAIL_RE, ENTITY_EMAIL)
        _collect(_IBAN_RE, ENTITY_FINANCIAL)
        _collect(_CARD_RE, ENTITY_FINANCIAL)
        _collect(_DATE_NUMERIC_RE, ENTITY_DOB)
        _collect(_DATE_TEXT_RE, ENTITY_DOB)
        _collect(_PHONE_RE, ENTITY_PHONE)
        _collect(_CIVILITY_NAME_RE, ENTITY_PERSON, group=1)

        # Fallback noms uniquement si spaCy indisponible (sinon doublons bruités).
        if _load_spacy() is None:
            _collect(_CAP_SEQUENCE_RE, ENTITY_PERSON)

        return found

    def _detect_pii_spacy(self, text: str) -> list[PIIMatch]:
        """Détecte personnes / organisations / lieux via NER spaCy."""
        nlp = _load_spacy()
        if nlp is None:
            return []
        label_map = {
            "PER": ENTITY_PERSON,
            "PERSON": ENTITY_PERSON,
            "ORG": ENTITY_ORG,
            "LOC": ENTITY_ADDRESS,
            "GPE": ENTITY_ADDRESS,
        }
        found: list[PIIMatch] = []
        try:
            doc = nlp(text)
        except Exception as exc:
            logger.warning("Échec NER spaCy (%s) — entités linguistiques ignorées.", exc)
            return []
        for ent in doc.ents:
            entity_type = label_map.get(ent.label_)
            if entity_type and ent.text.strip():
                found.append(
                    PIIMatch(ent.start_char, ent.end_char, ent.text, entity_type)
                )
        return found

    # ── Résolution des chevauchements ────────────────────────

    @staticmethod
    def _resolve_overlaps(matches: list[PIIMatch]) -> list[PIIMatch]:
        """Conserve, sur chaque zone, le match de plus haute priorité/longueur."""
        if not matches:
            return []
        # Tri : priorité décroissante, puis longueur décroissante.
        ordered = sorted(
            matches,
            key=lambda m: (m.priority, m.end - m.start),
            reverse=True,
        )
        kept: list[PIIMatch] = []
        occupied: list[tuple[int, int]] = []
        for match in ordered:
            if any(match.start < e and match.end > s for s, e in occupied):
                continue
            kept.append(match)
            occupied.append((match.start, match.end))
        return kept

    @staticmethod
    def _apply_replacements(text: str, replacements: list[tuple[int, int, str]]) -> str:
        """Applique les remplacements de la fin vers le début (offsets stables)."""
        result = text
        for start, end, token in sorted(replacements, key=lambda r: r[0], reverse=True):
            result = result[:start] + token + result[end:]
        return result

    @staticmethod
    def _canonical_value(value: str) -> str:
        """Clé de déduplication : espaces normalisés, insensible à la casse."""
        return re.sub(r"\s+", " ", value).strip().lower()
