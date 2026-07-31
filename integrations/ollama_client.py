"""Client HTTP Ollama — porte d'entrée applicative (allowlist).

Consommateurs autorisés : Screen Watcher, contrôle process Ollama, et
analyse photo des repas fitness (``app.fitness.meal_analysis``).
"""

from __future__ import annotations

from typing import Any

from jarvis.cognitive.ollama_guard import ollama_http_request


async def ollama_generate(
    base_url: str,
    *,
    model: str,
    prompt: str,
    images: list[str] | None = None,
    options: dict[str, Any] | None = None,
    keep_alive: str = "30s",
    timeout: float = 60.0,
) -> dict[str, Any]:
    """POST /api/generate via le garde-fou d'allowlist."""
    payload: dict[str, Any] = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "keep_alive": keep_alive,
        "options": options or {"temperature": 0.1, "num_predict": 100},
    }
    if images:
        payload["images"] = images
    url = f"{base_url.rstrip('/')}/api/generate"
    return await ollama_http_request("POST", url, json=payload, timeout=timeout)
