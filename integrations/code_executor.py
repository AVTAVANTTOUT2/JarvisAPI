"""Exécution de code et shell avancée — moteur interne JARVIS."""

import logging
import re
import asyncio

import config

logger = logging.getLogger(__name__)

BLOCKED_INSTRUCTIONS = [
    re.compile(r"supprim\w+ (?:tout|le système|le disque)", re.IGNORECASE),
    re.compile(r"format\w+ (?:le disque|la partition)", re.IGNORECASE),
    re.compile(r"rm\s+-rf\s+/(?!\S)", re.IGNORECASE),
    re.compile(r"efface\w* (?:tout|mes données)", re.IGNORECASE),
    re.compile(r"\b(?:shutdown|reboot|éteins)\b", re.IGNORECASE),
    re.compile(r":(){ :\|:& };:", re.IGNORECASE),  # fork bomb
    re.compile(r"mkfs\.", re.IGNORECASE),
    re.compile(r"dd\s+if=.*of=/dev/", re.IGNORECASE),
]


class CodeExecutor:
    """Moteur d'exécution de code intelligent.

    Peut enchaîner des commandes, écrire du code, debugger,
    et réaliser des workflows multi-étapes.
    """

    def __init__(self):
        self.available = False
        if not getattr(config, "CODE_EXECUTOR_ENABLED", True):
            logger.info("[code_executor] Désactivé par configuration")
            return
        try:
            from interpreter import interpreter

            self.interpreter = interpreter
            self.interpreter.auto_run = True
            self.interpreter.llm.model = f"openai/{getattr(config, 'CODE_EXECUTOR_MODEL', config.DEEPSEEK_MAIN_MODEL)}"
            self.interpreter.llm.api_key = config.DEEPSEEK_API_KEY
            self.interpreter.llm.api_base = config.DEEPSEEK_BASE_URL + "/v1"
            self.interpreter.verbose = False
            self.interpreter.offline = True
            self.interpreter.safe_mode = "auto"
            self.interpreter.max_output = 5000
            self.available = True
            logger.info("[code_executor] Moteur d'exécution avancé initialisé")
        except ImportError:
            logger.warning("[code_executor] open-interpreter non installé — fallback sur subprocess basique")
        except Exception as e:
            logger.warning("[code_executor] Erreur init : %s", e)

    def _is_safe(self, instruction: str) -> tuple[bool, str]:
        """Vérifie qu'une instruction ne contient pas de pattern dangereux."""
        for pattern in BLOCKED_INSTRUCTIONS:
            if pattern.search(instruction):
                return False, f"Instruction bloquée par sécurité (pattern : {pattern.pattern})"
        return True, ""

    async def execute(self, instruction: str, timeout: int | None = None) -> dict:
        """Exécute une instruction en langage naturel.

        Open Interpreter traduit en code/shell, exécute, gère les erreurs.
        Retourne le résultat formaté.
        """
        if not self.available:
            return {"ok": False, "error": "Moteur d'exécution non disponible"}

        safe, reason = self._is_safe(instruction)
        if not safe:
            logger.warning("[code_executor] Bloqué : %s", reason)
            return {"ok": False, "error": reason}

        if timeout is None:
            timeout = int(getattr(config, "CODE_EXECUTOR_TIMEOUT", 120))

        try:
            logger.info("[code_executor] Instruction : %s", instruction[:100])

            loop = asyncio.get_event_loop()
            result = await asyncio.wait_for(
                loop.run_in_executor(None, self._run, instruction),
                timeout=timeout,
            )
            return result

        except asyncio.TimeoutError:
            logger.error("[code_executor] Timeout après %ds", timeout)
            try:
                self.interpreter.computer.terminate()
            except Exception:
                pass
            return {"ok": False, "error": f"Timeout après {timeout}s"}
        except Exception as e:
            logger.exception("[code_executor] Erreur : %s", e)
            return {"ok": False, "error": str(e)}

    def _run(self, instruction: str) -> dict:
        """Exécution synchrone dans un thread."""
        try:
            messages = self.interpreter.chat(instruction, display=False)

            output_parts: list[str] = []
            code_blocks: list[dict] = []
            errors: list[str] = []

            for msg in messages:
                msg_type = msg.get("type", "")
                content = msg.get("content", "")
                if msg_type == "message":
                    output_parts.append(content)
                elif msg_type == "code":
                    code_blocks.append({
                        "language": msg.get("format", "python"),
                        "code": content,
                    })
                elif msg_type == "console":
                    if msg.get("format") == "error":
                        errors.append(content)
                    else:
                        output_parts.append(content)

            self.interpreter.messages = []

            return {
                "ok": len(errors) == 0,
                "output": "\n".join(output_parts)[:5000],
                "code": code_blocks,
                "errors": errors[:3],
                "summary": output_parts[-1][:500] if output_parts else "Exécution terminée.",
            }

        except Exception as e:
            return {"ok": False, "error": str(e)}

    def reset(self):
        """Reset la conversation de l'interpréteur."""
        if self.available:
            self.interpreter.messages = []


code_executor = CodeExecutor()
