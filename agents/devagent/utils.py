"""Utilitaires partages DevAgent."""

from __future__ import annotations

import json
import re
import unicodedata
from typing import Any


def slugify(name: str) -> str:
    """Convertit un nom humain en slug URL-safe."""
    normalized = unicodedata.normalize("NFKD", name)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")
    return slug or "project"


def parse_json_response(raw: str) -> dict[str, Any]:
    """Parse une reponse LLM JSON, avec retrait eventuel de fences markdown."""
    text = (raw or "").strip()
    if not text:
        raise ValueError("Reponse DeepSeek vide.")

    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fence:
        text = fence.group(1).strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            data = json.loads(text[start : end + 1])
        else:
            raise

    if not isinstance(data, dict):
        raise ValueError(f"JSON attendu (objet), recu : {type(data).__name__}")
    return data
