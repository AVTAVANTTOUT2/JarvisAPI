"""Politique d'adresse vocale — quand JARVIS a le droit de dire « Monsieur ».

L'honorifique fait partie de la personnalité de JARVIS ; ce module ne le
supprime pas, il le **rationne**. Le défaut vocal est de ne pas l'employer :
répété à chaque réponse il cesse d'être une marque de déférence pour devenir un
tic, et il s'empilait avec les accusés fixes du daemon (« Bien, Monsieur. »
suivi de « Il fait 18 degrés, Monsieur. »).

Trois propriétés motivent un module dédié plutôt qu'une réécriture du prompt :

- **Un prompt n'est pas une garantie.** Le modèle suit la consigne la plupart du
  temps ; « la plupart du temps » ne suffit pas pour un comportement que
  l'utilisateur entend à chaque tour.
- **Tous les producteurs ne sont pas des modèles.** Les fast-paths, les replis
  d'action et le cache TTS écrivent du texte en dur : aucun prompt ne les
  touche.
- **La règle dépend du type d'énoncé, pas du texte.** « Bonjour Monsieur. » est
  correct à l'ouverture d'une session et incorrect au milieu. Seul l'appelant
  connaît cette différence, d'où ``VoiceUtteranceKind``.

Le filtrage déterministe ne fait jamais un remplacement global : il protège les
citations, les titres et les emplois où le mot est un nom commun ou introduit un
tiers (« Monsieur Dupont »).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from enum import Enum

# ── Types d'énoncés ─────────────────────────────────────────────────────────


class VoiceUtteranceKind(str, Enum):
    """Nature d'un énoncé vocal, du point de vue de l'utilisateur.

    Hérite de ``str`` pour rester sérialisable tel quel dans les traces et les
    événements WebSocket sans conversion explicite.
    """

    ANSWER = "answer"
    """Réponse conversationnelle ou résultat d'outil — le cas courant."""

    ACTION_CONFIRMATION = "action_confirmation"
    """« C'est fait. » — une action a réellement abouti."""

    PROGRESS = "progress"
    """« Je lance l'analyse. » — un travail long a réellement été accepté."""

    GREETING = "greeting"
    """Ouverture réelle d'une session."""

    FAREWELL = "farewell"
    """Fermeture réelle d'une session (mise en veille, fin de conversation)."""

    RITUAL = "ritual"
    """Prise de parole proactive prévue : briefing, debrief, roast."""

    ERROR = "error"
    """Panne, repli, réponse vide."""

    SYSTEM_SIGNAL = "system_signal"
    """Notification technique poussée par le daemon."""


HONORIFIC_ALLOWED_KINDS: frozenset[VoiceUtteranceKind] = frozenset({
    VoiceUtteranceKind.GREETING,
    VoiceUtteranceKind.FAREWELL,
    VoiceUtteranceKind.RITUAL,
})
"""Seuls ces trois types peuvent porter l'honorifique, et une fois par session.

Une erreur en est volontairement exclue : s'excuser avec déférence d'une panne
en accentue la lourdeur au lieu de l'excuser.
"""


# ── Session vocale ──────────────────────────────────────────────────────────


@dataclass
class VoiceSession:
    """Frontière de session vocale — porte le budget d'honorifique.

    Une session n'est **pas** une détection de wake word : réveiller JARVIS
    trois fois pour trois questions successives reste la même conversation. La
    frontière suit celle du modèle de conversation existant — un identifiant de
    conversation, ouvert au premier tour et fermé par une mise en veille ou une
    expiration d'inactivité.
    """

    session_id: str | int | None = None
    honorific_spent: bool = False

    def spend_honorific(self) -> bool:
        """Consomme le budget. ``True`` si l'honorifique est accordé."""
        if self.honorific_spent:
            return False
        self.honorific_spent = True
        return True

    def reset(self) -> None:
        """Rouvre le budget — appelé à l'ouverture d'une nouvelle session."""
        self.honorific_spent = False


# ── Énoncé ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class VoiceUtterance:
    """Un énoncé vocal et son contexte, avant passage à la synthèse.

    ``turn_id``, ``speech_id`` et ``job_id`` sont distincts à dessein : une
    interruption de parole annule une lecture et une génération, jamais un
    travail de fond déjà accepté.
    """

    text: str
    kind: VoiceUtteranceKind = VoiceUtteranceKind.ANSWER
    turn_id: str | None = None
    speech_id: str | None = None
    job_id: str | None = None
    emotion: str = "neutral"
    interruptible: bool = True
    session_boundary: bool = False
    allow_honorific: bool = False
    metadata: dict = field(default_factory=dict)

    def with_text(self, text: str) -> VoiceUtterance:
        return replace(self, text=text)


# ── Filtrage déterministe de l'honorifique ──────────────────────────────────

_PLACEHOLDER = "\x00{}\x00"

# Citations et contenus lus : ce qui est entre guillemets appartient à
# quelqu'un d'autre (utilisateur, document, titre d'œuvre) et n'est jamais
# réécrit.
_PROTECTED_SPANS = re.compile(
    r"«[^»]*»"
    r"|“[^”]*”"
    r"|\"[^\"]*\""
    r"|`[^`]*`",
)

# Déterminants qui font de « monsieur » un nom commun (« ce monsieur »), ou
# une civilité portant sur un tiers.
_DETERMINERS = frozenset({
    "le", "un", "ce", "cet", "du", "au", "les", "des", "ces", "aux",
    "mon", "ton", "son", "notre", "votre", "leur", "quel", "quelle",
    "vieux", "jeune", "petit", "grand",
})

_WORD_BEFORE = re.compile(r"([\wÀ-ÿ'’-]+)\s*$", re.UNICODE)
_WORD_AFTER = re.compile(r"^\s*([\wÀ-ÿ'’-]+)", re.UNICODE)
_SENTENCE_HEAD = re.compile(r"(?:^|[.!?…])\s*$")


def _is_protected(text: str, start: int, end: int) -> bool:
    """L'occurrence de « monsieur » doit-elle rester intacte ?"""
    after = _WORD_AFTER.match(text[end:])
    if after and after.group(1)[:1].isupper():
        # « Monsieur Dupont » — civilité d'un tiers, pas une adresse.
        return True
    before = _WORD_BEFORE.search(text[:start])
    if before and before.group(1).lower() in _DETERMINERS:
        # « ce monsieur », « le monsieur du deuxième » — nom commun.
        return True
    return False


def _mask_protected(text: str) -> tuple[str, list[str]]:
    """Remplace les citations par des jetons opaques avant réécriture."""
    stored: list[str] = []

    def _store(match: re.Match[str]) -> str:
        stored.append(match.group(0))
        return _PLACEHOLDER.format(len(stored) - 1)

    return _PROTECTED_SPANS.sub(_store, text), stored


def _unmask(text: str, stored: list[str]) -> str:
    for index, original in enumerate(stored):
        text = text.replace(_PLACEHOLDER.format(index), original)
    return text


_FILLER_HEAD = re.compile(
    r"^\s*(?:bien|très bien|tres bien|entendu|parfait|d'accord|certainement)"
    r"\s*,?\s+monsieur\s*[.!?…]*\s*",
    re.IGNORECASE,
)
_PAIRED_WORD = re.compile(
    r"\b(bonjour|bonsoir|bonne nuit|au revoir|salut|merci|oui|non|pardon|désolé|desole)"
    r"\s*,?\s+monsieur\b",
    re.IGNORECASE,
)
_TRAILING_VOCATIVE = re.compile(
    r"\s*,\s*monsieur\b(?=\s*(?:[.!?…]|$))",
    re.IGNORECASE,
)
_LEADING_VOCATIVE = re.compile(r"^\s*monsieur\s*,\s*", re.IGNORECASE)
_BARE_VOCATIVE = re.compile(r"\s*,?\s*\bmonsieur\b", re.IGNORECASE)

# Le français fait précéder « ? ! ; : » d'une espace : la resserrer produirait
# une ponctuation fautive, et le TTS s'appuie sur cette espace pour la prosodie.
# Seuls le point et la virgule sont recollés.
_SPACE_BEFORE_PUNCT = re.compile(r"\s+([.,])")
_MULTI_SPACE = re.compile(r"[ \t]{2,}")
_DANGLING_COMMA = re.compile(r",\s*([.!?…])")


def _cleanup(text: str) -> str:
    text = _DANGLING_COMMA.sub(r"\1", text)
    text = _SPACE_BEFORE_PUNCT.sub(r"\1", text)
    text = _MULTI_SPACE.sub(" ", text)
    return text.strip(" ,;")


def _restore_initial_case(original: str, rewritten: str) -> str:
    """Rend la majuscule initiale que le retrait de l'adresse a emportée.

    « Monsieur, votre agenda est vide. » perd son premier mot ; sans cette
    reprise la phrase commencerait par une minuscule, ce qui se voit dans les
    transcriptions affichées et dans l'historique persisté.
    """
    if not rewritten or not original:
        return rewritten
    if original[:1].isupper() and rewritten[:1].islower():
        return rewritten[:1].upper() + rewritten[1:]
    return rewritten


def strip_honorific(text: str) -> str:
    """Retire l'adresse « Monsieur » sans toucher au reste du sens.

    Idempotente. Ne modifie ni les citations, ni « Monsieur <Nom> », ni les
    emplois comme nom commun. Si le retrait viderait entièrement l'énoncé, seul
    le mot est retiré et la phrase porteuse est conservée : mieux vaut « Bien. »
    qu'un silence non demandé.
    """
    if not text or "monsieur" not in text.lower():
        return text

    masked, stored = _mask_protected(text)

    def _honorific_span(match: re.Match[str]) -> tuple[int, int]:
        """Position du mot « monsieur » **dans** la correspondance.

        La protection se juge sur le voisinage de l'honorifique, pas sur celui
        de la formule entière : « Bien, Monsieur. Je lance… » a un mot
        capitalisé après le point qui n'a rien à voir avec une civilité.
        """
        inner = re.search(r"monsieur", match.group(0), re.IGNORECASE)
        if inner is None:  # pragma: no cover — les motifs contiennent le mot
            return match.span()
        return match.start() + inner.start(), match.start() + inner.end()

    def _drop_if_free(match: re.Match[str], replacement: str = "") -> str:
        if _is_protected(masked, *_honorific_span(match)):
            return match.group(0)
        return replacement

    # 1. Accusé de réception creux en tête : la formule entière disparaît.
    reduced = _FILLER_HEAD.sub(lambda m: _drop_if_free(m), masked)

    # 2. Formules appariées : le mot d'usage reste, l'honorifique part.
    reduced = _PAIRED_WORD.sub(
        lambda m: m.group(0) if _is_protected(masked, *_honorific_span(m)) else m.group(1),
        reduced,
    )

    # 3. Vocatif en fin de proposition : « …, Monsieur. » → « …. »
    reduced = _TRAILING_VOCATIVE.sub(lambda m: _drop_if_free(m), reduced)

    # 4. Vocatif en tête suivi d'une virgule : « Monsieur, … » → « … »
    reduced = _LEADING_VOCATIVE.sub(lambda m: _drop_if_free(m), reduced)

    # 5. Reliquat. Exclu en tête de phrase sans virgule : « Monsieur a couru »
    #    a « Monsieur » pour sujet, pas pour destinataire.
    def _bare(match: re.Match[str]) -> str:
        start, end = match.span()
        if _is_protected(reduced, start, end):
            return match.group(0)
        if _SENTENCE_HEAD.search(reduced[:start]):
            return match.group(0)
        return ""

    reduced = _BARE_VOCATIVE.sub(_bare, reduced)

    cleaned = _cleanup(_unmask(reduced, stored))
    if not cleaned:
        # Le texte n'était que l'adresse : garder la phrase porteuse plutôt que
        # de rendre une chaîne vide, qu'un appelant lirait comme « ne rien dire ».
        cleaned = _cleanup(_unmask(_BARE_VOCATIVE.sub("", masked), stored))
    return _restore_initial_case(text, cleaned) or text


# ── Politique ───────────────────────────────────────────────────────────────

MODE_RARE = "rare"
MODE_NEVER = "never"
MODE_FREE = "free"
_VALID_MODES = (MODE_RARE, MODE_NEVER, MODE_FREE)


def _configured_mode() -> str:
    """Mode courant, relu à chaque appel (les tests le surchargent)."""
    import config

    mode = str(getattr(config, "VOICE_ADDRESS_POLICY", MODE_RARE) or MODE_RARE).lower()
    return mode if mode in _VALID_MODES else MODE_RARE


class VoiceAddressPolicy:
    """Décide, puis applique. Sans état propre : l'état vit dans la session."""

    def __init__(self, mode: str | None = None) -> None:
        self._mode = mode

    @property
    def mode(self) -> str:
        return self._mode or _configured_mode()

    def allows_honorific(
        self, utterance: VoiceUtterance, session: VoiceSession | None = None,
    ) -> bool:
        """L'honorifique est-il accordé à cet énoncé ?

        Ne consomme rien : ``apply`` est le seul chemin qui débite le budget.
        """
        mode = self.mode
        if mode == MODE_FREE:
            return True
        if mode == MODE_NEVER:
            return False
        if not (utterance.allow_honorific or utterance.session_boundary):
            return False
        if utterance.kind not in HONORIFIC_ALLOWED_KINDS:
            return False
        if session is None:
            return True
        return not session.honorific_spent

    def apply(
        self, utterance: VoiceUtterance, session: VoiceSession | None = None,
    ) -> VoiceUtterance:
        """Rend l'énoncé conforme à la politique."""
        if not utterance.text:
            return utterance
        if not self.allows_honorific(utterance, session):
            stripped = strip_honorific(utterance.text)
            return utterance if stripped == utterance.text else utterance.with_text(stripped)
        if session is not None and "monsieur" in utterance.text.lower():
            session.spend_honorific()
        return utterance


default_policy = VoiceAddressPolicy()


# ── Registre de sessions ────────────────────────────────────────────────────
#
# Le budget d'honorifique doit survivre entre deux tours de la même
# conversation, sans quoi chaque réveil rouvrirait le droit à « Bonjour
# Monsieur ». Le registre est borné : une conversation abandonnée ne doit pas
# retenir de mémoire indéfiniment dans un processus qui tourne des semaines.
_MAX_TRACKED_SESSIONS = 64
_sessions: dict[str | int, VoiceSession] = {}


def get_voice_session(session_id: str | int | None) -> VoiceSession | None:
    """Session vocale d'une conversation, créée à la demande.

    ``None`` en entrée rend ``None`` : un producteur qui ne sait pas à quelle
    session il appartient ne doit pas en inventer une, il n'a simplement pas
    droit à l'honorifique.
    """
    if session_id is None:
        return None
    existing = _sessions.get(session_id)
    if existing is not None:
        return existing
    if len(_sessions) >= _MAX_TRACKED_SESSIONS:
        _sessions.pop(next(iter(_sessions)))
    session = VoiceSession(session_id=session_id)
    _sessions[session_id] = session
    return session


def close_voice_session(session_id: str | int | None) -> None:
    """Ferme une session : le prochain tour rouvrira un budget neuf."""
    if session_id is not None:
        _sessions.pop(session_id, None)


def reset_voice_sessions() -> None:
    """Vide le registre — isolation des tests et redémarrage du daemon."""
    _sessions.clear()


def apply_address_policy(
    text: str,
    *,
    kind: VoiceUtteranceKind = VoiceUtteranceKind.ANSWER,
    session: VoiceSession | None = None,
    allow_honorific: bool = False,
    session_boundary: bool = False,
    policy: VoiceAddressPolicy | None = None,
) -> str:
    """Raccourci textuel des points d'entrée qui ne manipulent pas d'énoncé.

    Les deux goulets vocaux (adaptateur de tour et file du daemon) l'appellent
    sur toute chaîne destinée à être prononcée.
    """
    utterance = VoiceUtterance(
        text=text,
        kind=kind,
        allow_honorific=allow_honorific,
        session_boundary=session_boundary,
    )
    return (policy or default_policy).apply(utterance, session).text


__all__ = [
    "HONORIFIC_ALLOWED_KINDS",
    "MODE_FREE",
    "MODE_NEVER",
    "MODE_RARE",
    "VoiceAddressPolicy",
    "VoiceSession",
    "VoiceUtterance",
    "VoiceUtteranceKind",
    "apply_address_policy",
    "close_voice_session",
    "default_policy",
    "get_voice_session",
    "reset_voice_sessions",
    "strip_honorific",
]
