"""Nettoyage du texte assistant pour l'affichage utilisateur (hors blocs techniques).

Source unique pour :
  - Extraction / strip du tag `[emotion]` en début de réponse Claude.
  - Suppression des blocs techniques (```action```, ```json```, ```save```).
"""

from __future__ import annotations

import re

# Même ensemble que BaseAgent._VALID_EMOTIONS
VALID_EMOTIONS = frozenset(
    {"neutral", "warm", "serious", "concerned", "amused", "urgent", "encouraging"}
)
_LEADING_EMOTION_RE = re.compile(r"^\s*\[(\w+)\]\s*\n?", re.MULTILINE)

# Blocs à masquer à l'utilisateur (save : affichage seulement — l'agent école parse avant strip)
# \n? : DeepSeek produit parfois ```action {JSON}``` sans saut de ligne (aligné sur main.py)
_RE_ACTION = re.compile(r"```action\s*\n?.*?```", re.DOTALL | re.IGNORECASE)
_RE_JSON = re.compile(r"```json\s*\n.*?\n```", re.DOTALL | re.IGNORECASE)
_RE_SAVE = re.compile(r"```save\s*\n.*?\n```", re.DOTALL | re.IGNORECASE)
# Bloc ```action / ```json / ```save incomplet en fin de flux streaming
_PARTIAL_FENCE_RE = re.compile(
    r"```(?:action|json|save)\b[\s\S]*$",
    re.IGNORECASE,
)


def extract_leading_emotion(text: str) -> tuple[str, str]:
    """Extrait le tag `[emotion]` en début de texte.

    Retourne (emotion, texte_sans_tag). Si pas de tag valide, retourne
    ("neutral", texte_original_trimmed).
    """
    if not text:
        return "neutral", ""
    m = _LEADING_EMOTION_RE.match(text)
    if m and m.group(1).lower() in VALID_EMOTIONS:
        return m.group(1).lower(), text[m.end():].strip()
    # Tag présent mais émotion non reconnue → on garde le tag dans le texte.
    if m and m.group(1).lower() not in VALID_EMOTIONS:
        return "neutral", text
    return "neutral", text


def strip_leading_emotion(text: str) -> str:
    """Retourne le texte sans le tag `[emotion]` en début (s'il est valide)."""
    _, clean = extract_leading_emotion(text)
    return clean


def strip_assistant_code_fences(
    text: str,
    *,
    include_save: bool = False,
) -> str:
    """Retire les blocs ```action``` / ```json``` et optionnellement ```save```."""
    if not text:
        return text
    t = text
    t = _RE_ACTION.sub("", t)
    t = _RE_JSON.sub("", t)
    if include_save:
        t = _RE_SAVE.sub("", t)
    return t.strip()


def strip_non_action_fences(text: str) -> str:
    """Retire ```json``` / ```save``` mais conserve ```action``` pour le pipeline."""
    if not text:
        return text
    t = _RE_JSON.sub("", text)
    t = _RE_SAVE.sub("", t)
    return t.strip()


def sanitize_streaming_display(text: str) -> str:
    """Texte affichable pendant le streaming (masque blocs complets et partiels)."""
    if not text:
        return ""
    cleaned = strip_assistant_code_fences(strip_leading_emotion(text), include_save=True)
    cleaned = _PARTIAL_FENCE_RE.sub("", cleaned)
    return cleaned.rstrip()


def finalize_assistant_display_text(text: str) -> str:
    """Tag [emotion] en tête + tous les blocs techniques (y compris save) pour le chat."""
    if not text:
        return text
    return strip_assistant_code_fences(strip_leading_emotion(text), include_save=True)
