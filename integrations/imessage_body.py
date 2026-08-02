"""Extraction du texte iMessage depuis ``text`` ou ``attributedBody``."""

from __future__ import annotations

import re

_ATTRIBUTED_BODY_NOISE = frozenset(
    {
        "streamtyped",
        "NSMutableAttributedString",
        "NSAttributedString",
        "NSString",
        "NSDictionary",
        "NSNumber",
        "NSValue",
        "NSObject",
        "NSRange",
        "__kIMMessagePartAttributeName",
        "kIMMessagePartAttributeName",
        "NSMutableString",
        "NSParagraphStyle",
        "NSFont",
    }
)

_READABLE_FRAGMENT = re.compile(
    r"[\u0020-\u007E\u00A0-\u024F\u1E00-\u1EFF"
    r"\u2019\u2018\u201C\u201D\u2026"
    r"\u00E9\u00E8\u00EA\u00EB\u00E0\u00E2\u00EE\u00EF\u00F4\u00F9\u00FC\u00E7"
    r"\u0153\u0152]{3,}"
)


def decode_attributed_body(blob: bytes | memoryview | None) -> str | None:
    """Decode le corps NSAttributedString stocke dans chat.db."""
    if not blob:
        return None
    try:
        raw = bytes(blob)
    except TypeError:
        return None
    if not raw:
        return None

    text = raw.decode("utf-8", errors="ignore")
    candidates = _READABLE_FRAGMENT.findall(text)
    filtered = [
        candidate
        for candidate in candidates
        if candidate not in _ATTRIBUTED_BODY_NOISE
        and not candidate.startswith("NS")
        and not candidate.startswith("__k")
        and not candidate.startswith("+*")
    ]
    if not filtered:
        return None
    best = max(filtered, key=len).strip()
    return best or None


def message_text_from_row(
    text: str | None,
    attributed_body: bytes | memoryview | None = None,
) -> str | None:
    """Retourne le texte exploitable d'un message Apple."""
    normalized = (text or "").strip()
    if normalized:
        return normalized
    decoded = decode_attributed_body(attributed_body)
    if decoded:
        return decoded.strip() or None
    return None
