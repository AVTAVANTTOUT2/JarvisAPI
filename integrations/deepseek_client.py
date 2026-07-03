"""Client HTTP DeepSeek dedie au module DevAgent (DeepSeek uniquement)."""

from __future__ import annotations

import logging
from typing import Any, Optional

import httpx

import config

logger = logging.getLogger(__name__)

_CHAT_COMPLETIONS_URL = f"{config.DEEPSEEK_BASE_URL.rstrip('/')}/v1/chat/completions"


class DeepSeekClientError(RuntimeError):
    """Erreur d'appel API DeepSeek pour DevAgent."""


async def call_deepseek(
    system: str,
    user: str,
    json_mode: bool = False,
    model: Optional[str] = None,
    max_tokens: int = 8192,
    temperature: float = 0.3,
) -> dict[str, Any]:
    """Appelle DeepSeek et retourne contenu + usage tokens.

    Returns:
        dict avec cles ``content``, ``tokens_in``, ``tokens_out``, ``tokens_total``.
    """
    api_key = config.DEEPSEEK_API_KEY
    if not api_key:
        raise DeepSeekClientError(
            "DEEPSEEK_API_KEY manquante — configurez-la dans .env pour DevAgent."
        )

    payload: dict[str, Any] = {
        "model": model or config.DEEPSEEK_MAIN_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_tokens": int(max_tokens),
        "temperature": float(temperature),
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    timeout = httpx.Timeout(120.0, connect=15.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            resp = await client.post(_CHAT_COMPLETIONS_URL, headers=headers, json=payload)
        except httpx.TimeoutException as exc:
            raise DeepSeekClientError(
                f"DeepSeek timeout apres 120s (modele {payload['model']})."
            ) from exc
        except httpx.HTTPError as exc:
            raise DeepSeekClientError(f"DeepSeek erreur reseau : {exc}") from exc

    if resp.status_code != 200:
        body = resp.text[:500]
        logger.error("DeepSeek HTTP %s : %s", resp.status_code, body)
        raise DeepSeekClientError(
            f"DeepSeek HTTP {resp.status_code} (modele {payload['model']}) : {body}"
        )

    try:
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise DeepSeekClientError(f"DeepSeek reponse invalide : {data!r}") from exc

    usage = data.get("usage") or {}
    return {
        "content": (content or "").strip(),
        "tokens_in": int(usage.get("prompt_tokens", 0)),
        "tokens_out": int(usage.get("completion_tokens", 0)),
        "tokens_total": int(usage.get("total_tokens", 0)),
    }
