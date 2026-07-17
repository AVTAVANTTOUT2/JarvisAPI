"""Résolution de contacts tolérante — accents, surnoms, partial match."""

from __future__ import annotations

import logging
import re
import unicodedata
from typing import Any

logger = logging.getLogger(__name__)

# Relations / surnoms courants → clés de recherche
_RELATION_ALIASES: dict[str, tuple[str, ...]] = {
    "maman": ("mère", "mere", "mom", "mummy", "maman"),
    "papa": ("père", "pere", "dad", "daddy", "papa"),
    "frere": ("frère", "frere", "brother"),
    "soeur": ("sœur", "soeur", "sister"),
}


def _fold(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower().strip()


def resolve_contact_query(query: str) -> dict[str, Any]:
    """Résout un nom / surnom / partial vers people + Contacts.app.

    Retourne:
        {
          "status": "resolved" | "ambiguous" | "not_found",
          "matches": [...],
          "preferred": {...} | None,
          "query": str,
        }
    """
    q = (query or "").strip()
    if not q:
        return {"status": "not_found", "matches": [], "preferred": None, "query": q}

    folded = _fold(q)
    # Extraire le nom d'une phrase ("numéro de Thomas", "message à maman")
    m = re.search(
        r"(?:num[eé]ro|telephone|téléphone|email|message|appelle|contact)\s+"
        r"(?:de\s+|d['’]|à\s+|a\s+)?(.+)$",
        folded,
        re.I,
    )
    needle = _fold(m.group(1) if m else q)
    needle = re.sub(r"[^\w\s+]", " ", needle).strip()

    search_terms = {needle}
    for canon, aliases in _RELATION_ALIASES.items():
        if needle == canon or needle in aliases:
            search_terms.add(canon)
            search_terms.update(aliases)

    matches: list[dict[str, Any]] = []
    try:
        from database import get_all_people, get_people_sorted_by_recent

        people = get_people_sorted_by_recent() or get_all_people() or []
    except Exception as exc:
        logger.debug("[contact_resolver] people: %s", exc)
        people = []

    for person in people:
        name = str(person.get("name") or "")
        rel = str(person.get("relationship") or "")
        name_f = _fold(name)
        rel_f = _fold(rel)
        score = 0.0
        for term in search_terms:
            if not term:
                continue
            if name_f == term or rel_f == term:
                score = max(score, 1.0)
            elif term in name_f or name_f.startswith(term):
                score = max(score, 0.85)
            elif term in rel_f:
                score = max(score, 0.8)
            elif any(part.startswith(term) for part in name_f.split() if len(term) >= 2):
                score = max(score, 0.7)
        if score >= 0.7:
            matches.append({**person, "score": score, "channels": _channels_for(person)})

    # Enrichissement Contacts.app (cache)
    try:
        from integrations.contacts import contacts_reader

        cache = getattr(contacts_reader, "_cache", {}) or {}
        for handle, display in list(cache.items())[:500]:
            disp_f = _fold(str(display))
            for term in search_terms:
                if term and (disp_f == term or term in disp_f):
                    matches.append(
                        {
                            "name": display,
                            "handle": handle,
                            "score": 0.75 if disp_f != term else 0.95,
                            "channels": {"phone": [handle]} if str(handle).startswith("+") or str(handle)[0].isdigit() else {"email": [handle]},
                            "source": "contacts_app",
                        }
                    )
    except Exception as exc:
        logger.debug("[contact_resolver] contacts cache: %s", exc)

    # Dédupliquer par nom
    by_name: dict[str, dict[str, Any]] = {}
    for mrow in matches:
        key = _fold(str(mrow.get("name") or ""))
        if not key:
            continue
        prev = by_name.get(key)
        if not prev or float(mrow.get("score", 0)) > float(prev.get("score", 0)):
            by_name[key] = mrow
    matches = sorted(by_name.values(), key=lambda x: -float(x.get("score", 0)))

    if not matches:
        return {"status": "not_found", "matches": [], "preferred": None, "query": q}

    top = matches[0]
    if len(matches) > 1 and float(matches[1].get("score", 0)) >= float(top.get("score", 0)) - 0.05:
        return {"status": "ambiguous", "matches": matches[:5], "preferred": None, "query": q}

    return {"status": "resolved", "matches": matches[:5], "preferred": top, "query": q}


def _channels_for(person: dict[str, Any]) -> dict[str, list[str]]:
    channels: dict[str, list[str]] = {"phone": [], "email": []}
    handle = person.get("handle")
    if handle:
        h = str(handle)
        if "@" in h:
            channels["email"].append(h)
        else:
            channels["phone"].append(h)
    return channels
