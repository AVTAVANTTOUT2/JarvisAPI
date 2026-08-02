"""Découpage du flux de texte du LLM en segments prononçables.

Le modèle produit des jetons ; la synthèse a besoin d'unités de sens. Entre les
deux, ce module décide **quand** un morceau de texte est prêt à être parlé.
C'est ce qui permet de commencer à parler pendant que la suite se génère.

Deux erreurs symétriques sont possibles, et le réglage consiste à les éviter
toutes les deux :

- couper trop tôt — un fragment d'une syllabe coûte une génération complète
  pour un souffle, et la prosodie se hache ;
- couper trop tard — l'utilisateur attend la fin d'une phrase interminable
  avant d'entendre le premier son.

D'où trois seuils (minimum, cible, maximum) et un délai de vidage : quand le
flux se tait un instant, ce qui est déjà là part, sans attendre une ponctuation
qui ne viendra peut-être jamais.

**Ce module ne coupe jamais au milieu d'une chose qui se lit d'un bloc** : une
URL, une adresse e-mail, un nombre décimal, un nombre et son unité, une
abréviation, un groupe entre parenthèses court, un bloc de code. Un point dans
« 12.5 » ou dans « M. Dupont » n'est pas une fin de phrase, et le prononcer
comme telle s'entend immédiatement.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator, Iterable

from jarvis.audio.tts.config import TTSSettings, load_tts_settings

# ── Ponctuation de coupure, par ordre de préférence ─────────────────────────
# Le rang exprime la qualité prosodique de la coupure, pas l'urgence : tous les
# candidats considérés sont déjà présents dans le tampon, donc préférer un
# point à un deux-points ne retarde rien — cela donne juste une meilleure fin.
_PUNCTUATION_RANK: dict[str, int] = {
    ".": 0,
    "?": 1,
    "!": 2,
    ";": 3,
    ":": 4,
    ",": 5,
}

# La virgule ne devient une coupure acceptable qu'au-delà de la taille cible :
# c'est une respiration, pas une fin.
_WEAK_PUNCTUATION = frozenset({","})

_TERMINATORS = "".join(_PUNCTUATION_RANK) + "…"

# ── Régions insécables ──────────────────────────────────────────────────────
# Chaque motif décrit une chose qui se lit d'un bloc. Une coupure dont l'index
# tombe strictement à l'intérieur d'une de ces régions est refusée.
_PROTECTED_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"```.*?```", re.DOTALL),                      # bloc de code
    re.compile(r"`[^`\n]+`"),                                 # code en ligne
    re.compile(r"<[^<>\n]{1,60}>"),                           # balise / marqueur
    re.compile(r"\b(?:https?://|www\.)\S+", re.IGNORECASE),   # URL
    re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b"),               # e-mail
    re.compile(r"\d+[.,]\d+"),                                # décimal
    re.compile(                                               # nombre + unité
        r"\d+\s?(?:°[CF]?|%|€|\$|£|km/h|km|cm|mm|kg|mg|ml|cl|"
        r"Go|Mo|Ko|To|h|min|ms|s)\b",
        re.IGNORECASE,
    ),
    re.compile(                                               # abréviations FR
        r"\b(?:M|MM|Mme|Mlle|Dr|Pr|St|Ste|cf|etc|env|art|éd|vol|chap|fig|"
        r"p|ex|resp|av|apr|J\.-C|réf|tél|bd|av)\.",
        re.IGNORECASE,
    ),
    re.compile(r"\bn°\s?\d+"),                                # numéro
    re.compile(r"\.{2,}"),                                    # points de suspension
    re.compile(r"\([^()\n]{0,60}\)"),                         # parenthèse courte
)


def _protected_spans(text: str) -> list[tuple[int, int]]:
    """Intervalles ``[début, fin)`` où toute coupure est interdite."""
    spans: list[tuple[int, int]] = []
    for pattern in _PROTECTED_PATTERNS:
        for match in pattern.finditer(text):
            spans.append(match.span())
    return spans


def _is_protected(index: int, spans: Iterable[tuple[int, int]]) -> bool:
    """La coupure **après** ``index`` tombe-t-elle dans une région insécable ?

    Une région qui se termine exactement après ``index`` (``end == index + 1``)
    ne protège pas : couper juste après « etc. » est légitime, couper au milieu
    ne l'est pas. C'est la nuance qui distingue « fin d'abréviation » de « point
    interne ».
    """
    for start, end in spans:
        if start <= index < end - 1:
            return True
    return False


def _has_unclosed_group(text: str) -> bool:
    """Un groupe ouvert non refermé : la phrase n'est pas finie."""
    return text.count("(") > text.count(")") or text.count("```") % 2 == 1


class TextStreamSegmenter:
    """Accumule des fragments de texte et rend des segments prononçables.

    Usage synchrone :

        segmenter = TextStreamSegmenter()
        for delta in llm_deltas:
            for segment in segmenter.feed(delta):
                speak(segment)
        for segment in segmenter.flush():
            speak(segment)
    """

    def __init__(self, settings: TTSSettings | None = None) -> None:
        resolved = settings or load_tts_settings()
        self._min_chars = resolved.min_chunk_chars
        self._target_chars = resolved.target_chunk_chars
        self._max_chars = resolved.max_chunk_chars
        self._buffer = ""
        self._index = 0

    # ── État ────────────────────────────────────────────────────────────────

    @property
    def pending(self) -> str:
        """Texte accumulé pas encore émis (diagnostic et tests)."""
        return self._buffer

    @property
    def segment_index(self) -> int:
        """Nombre de segments déjà émis — sert d'index d'instrumentation."""
        return self._index

    # ── Alimentation ────────────────────────────────────────────────────────

    def feed(self, delta: str) -> list[str]:
        """Ajoute un fragment du flux et retourne les segments prêts."""
        if delta:
            self._buffer += delta
        return self._drain(final=False)

    def flush(self) -> list[str]:
        """Fin de flux : rend tout ce qui reste, ponctuation ou non."""
        segments = self._drain(final=True)
        remainder = self._buffer.strip()
        self._buffer = ""
        if remainder:
            self._index += 1
            segments.append(remainder)
        return segments

    def flush_timeout(self) -> list[str]:
        """Silence du flux : rend ce qui est là si c'est assez long.

        Différent de ``flush`` : le flux n'est pas terminé, on ne vide donc pas
        un demi-mot. En dessous du minimum, mieux vaut attendre la suite que
        prononcer une syllabe isolée.
        """
        candidate = self._buffer.strip()
        if len(candidate) < self._min_chars or _has_unclosed_group(candidate):
            return []
        segments = self._drain(final=True)
        remainder = self._buffer.strip()
        self._buffer = ""
        if remainder:
            self._index += 1
            segments.append(remainder)
        return segments

    # ── Découpage ───────────────────────────────────────────────────────────

    def _drain(self, *, final: bool) -> list[str]:
        segments: list[str] = []
        while True:
            cut = self._find_cut(final=final)
            if cut is None:
                return segments
            segment = self._buffer[:cut].strip()
            self._buffer = self._buffer[cut:].lstrip()
            if segment:
                self._index += 1
                segments.append(segment)

    def _find_cut(self, *, final: bool) -> int | None:
        """Index de fin du prochain segment, ou ``None`` s'il faut attendre."""
        buffer = self._buffer
        if not buffer.strip():
            return None

        spans = _protected_spans(buffer)
        best: tuple[int, int] | None = None  # (rang, index)

        for index, char in enumerate(buffer):
            if char not in _TERMINATORS:
                continue
            # Une ponctuation en fin de tampon peut être incomplète (« … »
            # arrivant point par point) : sans caractère suivant, on attend.
            if index == len(buffer) - 1 and not final:
                continue
            following = buffer[index + 1 : index + 2]
            if following and not following.isspace() and following not in _TERMINATORS:
                continue
            if _is_protected(index, spans):
                continue

            length = len(buffer[: index + 1].strip())
            if length < self._min_chars or length > self._max_chars:
                continue
            if char in _WEAK_PUNCTUATION and length < self._target_chars:
                continue
            if _has_unclosed_group(buffer[: index + 1]):
                continue

            rank = _PUNCTUATION_RANK.get(char, 0)
            if best is None or (rank, index) < best:
                best = (rank, index)

        if best is not None:
            return best[1] + 1

        # Aucune ponctuation exploitable et le tampon déborde : on coupe au
        # dernier espace avant le plafond plutôt qu'au milieu d'un mot.
        if len(buffer.strip()) > self._max_chars:
            return self._hard_cut(buffer, spans)
        return None

    def _hard_cut(self, buffer: str, spans: list[tuple[int, int]]) -> int | None:
        limit = min(self._max_chars, len(buffer))
        for index in range(limit - 1, 0, -1):
            if not buffer[index].isspace():
                continue
            if _is_protected(index - 1, spans):
                continue
            if len(buffer[:index].strip()) >= self._min_chars:
                return index
        # Un seul « mot » plus long que le plafond (URL géante, jeton anormal) :
        # le couper produirait une prononciation absurde, on le laisse entier.
        return None


async def segment_stream(
    deltas: AsyncIterator[str],
    *,
    settings: TTSSettings | None = None,
) -> AsyncIterator[str]:
    """Transforme un flux de fragments LLM en flux de segments prononçables.

    Le délai de vidage (``TTS_FLUSH_TIMEOUT_MS``) borne l'attente : un modèle
    qui marque une pause ne doit pas retarder la parole de ce qui est déjà
    écrit. C'est le seul endroit où le temps intervient dans la segmentation.
    """
    resolved = settings or load_tts_settings()
    segmenter = TextStreamSegmenter(resolved)
    timeout = max(0.01, resolved.flush_timeout_ms / 1000.0)

    iterator = deltas.__aiter__()
    pending: asyncio.Task[str] | None = None
    try:
        while True:
            if pending is None:
                pending = asyncio.ensure_future(iterator.__anext__())
            try:
                delta = await asyncio.wait_for(asyncio.shield(pending), timeout)
            except asyncio.TimeoutError:
                for segment in segmenter.flush_timeout():
                    yield segment
                continue
            except StopAsyncIteration:
                pending = None
                break
            pending = None
            for segment in segmenter.feed(delta):
                yield segment
    finally:
        if pending is not None:
            pending.cancel()

    for segment in segmenter.flush():
        yield segment


__all__ = ["TextStreamSegmenter", "segment_stream"]
