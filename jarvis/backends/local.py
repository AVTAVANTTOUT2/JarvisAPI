"""Backend local MLX-LM (Qwen3-30B) — traite UNIQUEMENT les données d'Elias.

Le modèle tourne en local sur le Mac via ``mlx_lm.generate`` lancé en
subprocess asynchrone. Aucune donnée ne quitte la machine. Cette classe n'a
volontairement aucun lien d'héritage avec ``DeepSeekBackend``.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Optional

from jarvis import settings
from jarvis.exceptions import LocalBackendError

logger = logging.getLogger(__name__)

# Token de contrôle Qwen3 activant le mode raisonnement.
_THINK_PREFIX = "/think\n"
# Délimiteur encadrant le texte généré dans la sortie de mlx_lm.generate.
_OUTPUT_DELIMITER = "=========="
_HEALTHCHECK_PROMPT = "ping"


class LocalBackend:
    """Génération locale via MLX-LM en subprocess."""

    def __init__(
        self,
        model: Optional[str] = None,
        venv_path: Optional[str] = None,
    ) -> None:
        self.model: str = model or settings.LOCAL_MODEL
        self._venv_path: str = venv_path or settings.LOCAL_VENV
        self._health_cached: Optional[bool] = None
        self._health_checked_at: float = 0.0

    # ── Génération ───────────────────────────────────────────

    async def generate(
        self,
        prompt: str,
        system: Optional[str] = None,
        max_tokens: int = 2048,
        temp: float = 0.6,
        top_p: float = 0.95,
        timeout: Optional[float] = None,
    ) -> str:
        """Génère du texte localement et retourne uniquement la sortie du modèle.

        Lève ``LocalBackendError`` si le subprocess échoue, expire ou produit un
        code de retour non nul.
        """
        if not isinstance(prompt, str) or not prompt.strip():
            raise LocalBackendError("prompt local vide ou non-textuel.")

        full_prompt = self._build_prompt(prompt, system)
        cmd = self._build_command(full_prompt, max_tokens, temp, top_p)
        effective_timeout = (
            timeout if timeout is not None else settings.LOCAL_GENERATION_TIMEOUT_SEC
        )

        stdout, stderr, returncode = await self._run_subprocess(cmd, effective_timeout)

        if returncode != 0:
            err = stderr.strip() or "(stderr vide)"
            logger.error("mlx_lm.generate exit=%s : %s", returncode, err)
            raise LocalBackendError(
                f"mlx_lm.generate a échoué (exit={returncode}) pour le modèle "
                f"'{self.model}' : {err}"
            )

        text = self._extract_generated_text(stdout)
        if not text:
            raise LocalBackendError(
                f"mlx_lm.generate n'a produit aucun texte exploitable pour le "
                f"modèle '{self.model}' (sortie de {len(stdout)} caractères)."
            )
        return text

    async def is_healthy(self) -> bool:
        """Vérifie la disponibilité du modèle (1 token, timeout court, cache 30s)."""
        now = time.monotonic()
        if (
            self._health_cached is not None
            and (now - self._health_checked_at) < settings.LOCAL_HEALTH_CACHE_TTL_SEC
        ):
            return self._health_cached

        healthy = False
        try:
            await self.generate(
                prompt=_HEALTHCHECK_PROMPT,
                max_tokens=1,
                timeout=settings.LOCAL_HEALTHCHECK_TIMEOUT_SEC,
            )
            healthy = True
        except LocalBackendError as exc:
            logger.warning("Healthcheck local KO : %s", exc)
            healthy = False

        self._health_cached = healthy
        self._health_checked_at = now
        return healthy

    # ── Helpers internes ─────────────────────────────────────

    @staticmethod
    def _build_prompt(prompt: str, system: Optional[str]) -> str:
        """Préfixe ``/think`` et injecte le system prompt s'il existe."""
        if system:
            return f"{_THINK_PREFIX}{system}\n\n{prompt}"
        return f"{_THINK_PREFIX}{prompt}"

    def _python_executable(self) -> str:
        """Python du venv MLX si présent, sinon l'interpréteur courant."""
        candidate = os.path.join(self._venv_path, "bin", "python")
        if os.path.exists(candidate):
            return candidate
        logger.debug(
            "Python venv MLX introuvable (%s) — fallback sys.executable.", candidate
        )
        import sys

        return sys.executable

    def _build_command(
        self, full_prompt: str, max_tokens: int, temp: float, top_p: float
    ) -> list[str]:
        return [
            self._python_executable(),
            "-m",
            "mlx_lm.generate",
            "--model",
            self.model,
            "--prompt",
            full_prompt,
            "--max-tokens",
            str(int(max_tokens)),
            "--temp",
            str(float(temp)),
            "--top-p",
            str(float(top_p)),
        ]

    async def _run_subprocess(
        self, cmd: list[str], timeout: float
    ) -> tuple[str, str, int]:
        """Exécute le subprocess et retourne (stdout, stderr, returncode)."""
        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise LocalBackendError(
                f"Exécutable Python introuvable pour MLX ('{cmd[0]}'). "
                f"Vérifie JARVIS_VENV. Détail : {exc}"
            ) from exc

        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                process.communicate(), timeout=timeout
            )
        except asyncio.TimeoutError as exc:
            logger.error(
                "[LocalBackend] MLX timeout après %ds — process tué, "
                "modèle probablement déchargé ou mémoire saturée.",
                timeout,
            )
            await self._kill(process)
            raise LocalBackendError(
                f"mlx_lm.generate : timeout après {timeout}s (modèle '{self.model}'). "
                f"Cause probable : modèle déchargé en RAM, rechargement nécessaire."
            ) from exc

        stderr_text = stderr_b.decode("utf-8", errors="replace")
        if process.returncode is not None and process.returncode != 0:
            truncated = stderr_text[:500] if stderr_text else "(stderr vide)"
            logger.error(
                "[LocalBackend] MLX exit=%s, stderr=%r",
                process.returncode,
                truncated,
            )

        return (
            stdout_b.decode("utf-8", errors="replace"),
            stderr_text,
            process.returncode if process.returncode is not None else -1,
        )

    @staticmethod
    async def _kill(process: asyncio.subprocess.Process) -> None:
        try:
            process.kill()
            await process.wait()
        except ProcessLookupError:
            pass

    @staticmethod
    def _extract_generated_text(stdout: str) -> str:
        """Extrait le texte entre les deux délimiteurs ``==========``.

        mlx_lm.generate encadre la génération de deux lignes de délimiteurs,
        suivies de statistiques (Prompt:/Generation:). On isole le bloc central ;
        à défaut on retombe sur la sortie nettoyée.
        """
        parts = stdout.split(_OUTPUT_DELIMITER)
        if len(parts) >= 3:
            return parts[1].strip()
        # Fallback : retirer les lignes de stats connues.
        lines = [
            line
            for line in stdout.splitlines()
            if not line.startswith(("Prompt:", "Generation:", "Peak memory:"))
        ]
        return "\n".join(lines).strip()
