"""Client DeepSeek API (format OpenAI) avec routing de modèles fast/main."""

import asyncio
import json
import logging
from typing import AsyncGenerator

import httpx

import config

logger = logging.getLogger(__name__)

# Coûts par million de tokens (input, output, cache_hit)
# DeepSeek v4-flash : input ~$0.27/M, output ~$1.10/M, cache ~$0.07/M (prix indicatifs)
# DeepSeek v4-pro   : input ~$2.00/M, output ~$8.00/M, cache ~$0.50/M (prix indicatifs)
MODEL_COSTS = {
    config.DEEPSEEK_FAST_MODEL: (0.27, 1.10, 0.07),
    config.DEEPSEEK_MAIN_MODEL: (2.00, 8.00, 0.50),
}

# Client httpx partagé : évite un handshake TCP+TLS par appel LLM
# (2 appels minimum par message utilisateur : classification + agent).
_http_client: httpx.AsyncClient | None = None

# Erreurs transitoires DeepSeek qui méritent un retry (surcharge / rate limit).
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}
_MAX_RETRIES = 3


def _get_http_client() -> httpx.AsyncClient:
    """Retourne le client httpx partagé (créé paresseusement)."""
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(120.0, connect=10.0),
            limits=httpx.Limits(max_keepalive_connections=5, max_connections=10),
        )
    return _http_client


def _check_api_key() -> None:
    """Échoue immédiatement avec un message clair si la clé API manque."""
    if not config.DEEPSEEK_API_KEY:
        raise RuntimeError(
            "DEEPSEEK_API_KEY manquante — ajoute-la dans .env pour activer le LLM."
        )


def estimate_cost(model: str, tokens_in: int, tokens_out: int, cache_hit: int = 0) -> float:
    """Calcule le coût estimé en dollars pour un appel LLM.

    Args:
        model: Identifiant du modèle (ex: config.DEEPSEEK_MAIN_MODEL).
        tokens_in: Nombre de tokens en entrée (prompt).
        tokens_out: Nombre de tokens en sortie (completion).
        cache_hit: Nombre de tokens servis depuis le cache automatique DeepSeek.

    Returns:
        Coût total estimé en dollars (float).
    """
    costs = MODEL_COSTS.get(model, (3.0, 15.0, 0.3))
    input_cost = (tokens_in - cache_hit) * costs[0] / 1_000_000
    cache_cost = cache_hit * costs[2] / 1_000_000
    output_cost = tokens_out * costs[1] / 1_000_000
    return input_cost + cache_cost + output_cost


async def chat(
    messages: list[dict],
    model: str = None,
    system: str = "",
    max_tokens: int = 4096,
    temperature: float = 0.7,
    use_cache: bool = True,
) -> dict:
    """Appel DeepSeek API non-streaming via httpx.

    Le paramètre ``use_cache`` est conservé pour compatibilité mais ignoré —
    DeepSeek gère automatiquement le cache serveur (pas d'API explicite).

    Args:
        messages: Liste de messages au format [{"role": "user", "content": "..."}, ...].
        model: Modèle DeepSeek (défaut: config.DEEPSEEK_MAIN_MODEL).
        system: System prompt optionnel (injecté en premier message "system").
        max_tokens: Nombre maximum de tokens à générer.
        temperature: Température d'échantillonnage (0.0 = déterministe).
        use_cache: Ignoré — conservé pour compatibilité ascendante.

    Returns:
        dict avec clés: content, tokens_in, tokens_out, cache_hit, cost, model, stop_reason.
    """
    model = model or config.DEEPSEEK_MAIN_MODEL

    api_messages: list[dict] = []
    if system:
        api_messages.append({"role": "system", "content": system})
    api_messages.extend(messages)

    url = f"{config.DEEPSEEK_BASE_URL}/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {config.DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": api_messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": False,
    }

    _check_api_key()
    client = _get_http_client()
    data = None
    for attempt in range(_MAX_RETRIES):
        try:
            response = await client.post(url, json=payload, headers=headers)
            if response.status_code in _RETRYABLE_STATUS and attempt < _MAX_RETRIES - 1:
                delay = 2 ** attempt
                logger.warning(
                    "DeepSeek HTTP %s — retry %d/%d dans %ds",
                    response.status_code, attempt + 1, _MAX_RETRIES - 1, delay,
                )
                await asyncio.sleep(delay)
                continue
            response.raise_for_status()
            data = response.json()
            break
        except httpx.TransportError as e:
            if attempt >= _MAX_RETRIES - 1:
                raise
            delay = 2 ** attempt
            logger.warning(
                "DeepSeek erreur réseau (%s) — retry %d/%d dans %ds",
                type(e).__name__, attempt + 1, _MAX_RETRIES - 1, delay,
            )
            await asyncio.sleep(delay)
    if data is None:
        raise RuntimeError("DeepSeek : aucune réponse après retries")

    choice = data["choices"][0]
    content = choice["message"]["content"]
    usage = data.get("usage", {})
    tokens_in = usage.get("prompt_tokens", 0)
    tokens_out = usage.get("completion_tokens", 0)
    # DeepSeek expose le cache hit dans prompt_cache_hit_tokens (cache automatique)
    cache_hit = usage.get("prompt_cache_hit_tokens", 0)

    cost = estimate_cost(model, tokens_in, tokens_out, cache_hit)

    return {
        "content": content,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "cache_hit": cache_hit,
        "cost": cost,
        "model": model,
        "stop_reason": choice.get("finish_reason", "stop"),
    }


async def chat_stream(
    messages: list[dict],
    model: str = None,
    system: str = "",
    max_tokens: int = 4096,
    temperature: float = 0.7,
) -> AsyncGenerator[str, None]:
    """Appel DeepSeek API en streaming SSE via httpx. Yield chaque chunk de texte.

    Args:
        messages: Liste de messages au format [{"role": "user", "content": "..."}, ...].
        model: Modèle DeepSeek (défaut: config.DEEPSEEK_MAIN_MODEL).
        system: System prompt optionnel.
        max_tokens: Nombre maximum de tokens à générer.
        temperature: Température d'échantillonnage.

    Yields:
        str: Chunk de texte généré par le modèle.
    """
    model = model or config.DEEPSEEK_MAIN_MODEL

    api_messages: list[dict] = []
    if system:
        api_messages.append({"role": "system", "content": system})
    api_messages.extend(messages)

    url = f"{config.DEEPSEEK_BASE_URL}/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {config.DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": api_messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": True,
    }

    _check_api_key()
    client = _get_http_client()
    async with client.stream("POST", url, json=payload, headers=headers) as response:
        response.raise_for_status()
        async for line in response.aiter_lines():
            if not line.startswith("data: "):
                continue
            data_str = line[6:]  # skip "data: "
            if data_str == "[DONE]":
                break
            try:
                data = json.loads(data_str)
                delta = data.get("choices", [{}])[0].get("delta", {})
                chunk_content = delta.get("content", "")
                if chunk_content:
                    yield chunk_content
            except json.JSONDecodeError:
                continue


async def quick_classify(text: str, categories: list[str], model: str = None) -> str:
    """Classification rapide via DeepSeek Fast. Retourne le nom de la catégorie.

    Args:
        text: Le texte à classifier.
        categories: Liste des catégories possibles (ex: ["SCHOOL", "INFO", "COACH"]).
        model: Modèle à utiliser (défaut: config.DEEPSEEK_FAST_MODEL).

    Returns:
        str: Nom de la catégorie en MAJUSCULES (ex: "SCHOOL").
    """
    model = model or config.DEEPSEEK_FAST_MODEL
    cats = ", ".join(categories)

    response = await chat(
        messages=[{"role": "user", "content": text}],
        model=model,
        system=f"Classifie ce message dans UNE seule catégorie parmi : {cats}. Réponds UNIQUEMENT avec le nom de la catégorie en majuscules, rien d'autre.",
        max_tokens=20,
        temperature=0.0,
    )

    result = response["content"].strip().upper()
    for cat in categories:
        if cat.upper() in result:
            return cat.upper()
    return categories[0].upper()


async def classify_task_type(user_message: str) -> str:
    """Décide via DeepSeek Fast si la demande est une production lourde.

    Retourne :
        "heavy"    → contenu long autonome (exo, dissertation, code, rapport,
                     fichier, flashcards en masse) → DEEPSEEK_MAIN_MODEL avec
                     plafond de tokens élevé (config.HEAVY_TASK_MAX_TOKENS)
        "standard" → conversation, analyse contextuelle, décision, mémoire
    """
    response = await chat(
        messages=[{"role": "user", "content": user_message}],
        model=config.DEEPSEEK_FAST_MODEL,
        system=(
            "Cette demande implique-t-elle de produire un contenu long "
            "(exercice complet, dissertation, code, rapport, résumé de document, "
            "fichier, série de flashcards) ? "
            "Réponds UN SEUL MOT : LOURDE si oui, STANDARD si non."
        ),
        max_tokens=10,
        temperature=0.0,
    )

    raw = response["content"].strip().upper()
    return "heavy" if "LOURDE" in raw else "standard"
