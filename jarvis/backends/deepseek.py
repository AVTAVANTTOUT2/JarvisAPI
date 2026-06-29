"""Backend DeepSeek (API HTTP OpenAI-compatible) — données anonymisées seulement.

Ce backend ne doit JAMAIS recevoir de données messages brutes. Pour le garantir
structurellement, ``generate()`` appelle systématiquement ``DataBoundary.check``
sur le system prompt ET le prompt avant toute requête réseau. Il n'existe aucune
méthode publique permettant d'injecter des données messages.
"""

from __future__ import annotations

import logging
from typing import Optional

import httpx

from jarvis import settings
from jarvis.exceptions import DeepSeekBackendError
from jarvis.models import RouterStats
from jarvis.pii.boundary import DataBoundary

logger = logging.getLogger(__name__)

_CHAT_COMPLETIONS_PATH = "/chat/completions"


class DeepSeekBackend:
    """Client DeepSeek async avec garde-fou anti-fuite intégré et non-contournable."""

    def __init__(
        self,
        boundary: DataBoundary,
        stats: Optional[RouterStats] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        timeout: Optional[float] = None,
        client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        if boundary is None:
            raise ValueError("DeepSeekBackend exige un DataBoundary (garde-fou).")
        self._boundary = boundary
        self._stats = stats
        self.base_url: str = (base_url or settings.DEEPSEEK_BASE_URL).rstrip("/")
        self.model: str = model or settings.DEEPSEEK_MODEL
        self._timeout: float = (
            timeout if timeout is not None else settings.DEEPSEEK_TIMEOUT_SEC
        )
        self._client: Optional[httpx.AsyncClient] = client
        self._owns_client: bool = client is None

    async def generate(
        self,
        prompt: str,
        system: Optional[str] = None,
        max_tokens: int = 8192,
        temperature: float = 0.7,
    ) -> str:
        """Appelle DeepSeek après vérification du garde-fou. Données anonymisées.

        Lève ``DeepSeekBackendError`` sur toute erreur HTTP ou réponse invalide,
        ``DataLeakError`` (via la boundary) si une fuite est détectée.
        """
        if not isinstance(prompt, str) or not prompt.strip():
            raise DeepSeekBackendError("prompt DeepSeek vide ou non-textuel.")

        # GARDE-FOU NON-NÉGOCIABLE : vérifié avant tout accès réseau.
        self._enforce_boundary(prompt)
        if system:
            self._enforce_boundary(system)

        payload = self._build_payload(prompt, system, max_tokens, temperature)
        data = await self._post(payload)
        return self._extract_content(data)

    async def aclose(self) -> None:
        """Ferme le client HTTP si ce backend en est propriétaire."""
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None

    # ── Helpers internes ─────────────────────────────────────

    def _enforce_boundary(self, text: str) -> None:
        from jarvis.exceptions import DataLeakError

        try:
            self._boundary.check(text)
        except DataLeakError:
            if self._stats is not None:
                self._stats.boundary_violations += 1
            raise

    def _build_payload(
        self, prompt: str, system: Optional[str], max_tokens: int, temperature: float
    ) -> dict:
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        return {
            "model": self.model,
            "messages": messages,
            "max_tokens": int(max_tokens),
            "temperature": float(temperature),
            "stream": False,
        }

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.base_url, timeout=self._timeout
            )
        return self._client

    async def _post(self, payload: dict) -> dict:
        api_key = settings.require_deepseek_api_key()
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        client = self._ensure_client()
        try:
            response = await client.post(
                _CHAT_COMPLETIONS_PATH, json=payload, headers=headers
            )
        except httpx.TimeoutException as exc:
            raise DeepSeekBackendError(
                f"DeepSeek : timeout après {self._timeout}s sur {self.base_url}"
                f"{_CHAT_COMPLETIONS_PATH}."
            ) from exc
        except httpx.HTTPError as exc:
            raise DeepSeekBackendError(
                f"DeepSeek : erreur réseau vers {self.base_url}"
                f"{_CHAT_COMPLETIONS_PATH} : {exc}"
            ) from exc

        if response.status_code != 200:
            body = response.text[:500]
            logger.error("DeepSeek HTTP %s : %s", response.status_code, body)
            raise DeepSeekBackendError(
                f"DeepSeek a répondu HTTP {response.status_code} "
                f"(modèle '{self.model}') : {body}"
            )

        try:
            return response.json()
        except ValueError as exc:
            raise DeepSeekBackendError(
                f"DeepSeek : réponse JSON invalide (modèle '{self.model}')."
            ) from exc

    @staticmethod
    def _extract_content(data: dict) -> str:
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise DeepSeekBackendError(
                f"DeepSeek : structure de réponse inattendue : {data!r}"
            ) from exc
        if not isinstance(content, str) or not content.strip():
            raise DeepSeekBackendError("DeepSeek : contenu de réponse vide.")
        return content.strip()
