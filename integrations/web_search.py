"""Recherche web legere pour le pipeline vocal.

DuckDuckGo Instant Answer API — zero cle API, zero rate limit strict.
Fallback : reponse textuelle du LLM sans recherche.
"""

import logging

import httpx

logger = logging.getLogger("integrations.web_search")

DDGS_URL = "https://api.duckduckgo.com/"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) JARVIS/1.0"
REQUEST_TIMEOUT = 8.0


async def web_search(query: str, max_results: int = 3) -> str:
    """Recherche DuckDuckGo Instant Answer — retourne un resume textuel.

    Args:
        query: termes de recherche
        max_results: nombre maximum de topics relies a inclure

    Returns:
        resume textuel, ou message d'erreur.
    """
    if not query or not query.strip():
        return "Aucun terme de recherche fourni."

    try:
        async with httpx.AsyncClient(
            timeout=REQUEST_TIMEOUT,
            headers={"User-Agent": USER_AGENT},
        ) as client:
            resp = await client.get(DDGS_URL, params={
                "q": query,
                "format": "json",
                "no_html": 1,
                "skip_disambig": 1,
            })
            resp.raise_for_status()
            data = resp.json()
    except httpx.TimeoutException:
        logger.warning("[web_search] Timeout pour %r", query[:100])
        return f"Recherche trop lente pour '{query[:80]}'. Reessaie de formuler autrement."
    except httpx.HTTPStatusError as e:
        logger.warning("[web_search] HTTP %s pour %r", e.response.status_code, query[:100])
        return f"Service de recherche indisponible (HTTP {e.response.status_code})."
    except Exception as e:
        logger.warning("[web_search] Erreur : %s", e)
        return f"Recherche web echouee : {e}"

    # AbstractText = meilleur resultat instantane
    abstract = data.get("AbstractText", "").strip()
    if abstract:
        src = data.get("AbstractSource", "DuckDuckGo")
        abstract_url = data.get("AbstractURL", "")
        if abstract_url:
            return f"{abstract} (source: {src})"
        return f"{abstract} (source: {src})"

    # RelatedTopics fallback
    topics: list[dict] = data.get("RelatedTopics", [])
    results: list[str] = []
    for topic in topics[:max_results]:
        if isinstance(topic, dict) and topic.get("Text"):
            results.append(topic["Text"].strip())

    if results:
        return " | ".join(results)

    heading = data.get("Heading", "").strip()
    if heading:
        return f"Pas de resultat detaille pour '{heading}'. Essaie de reformuler."

    return f"Pas de resultat instantane pour '{query[:80]}'. Essaie de reformuler."
