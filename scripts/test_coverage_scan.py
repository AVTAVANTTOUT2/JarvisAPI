"""Génération automatique de tests pour les fonctions non couvertes.

Heuristique sans dépendance supplémentaire (pas de `coverage.py` au runtime) :
une fonction top-level publique d'un module cible est considérée "non
couverte" si son nom n'apparaît dans AUCUN fichier sous ``tests/``. C'est un
proxy imparfait mais dépourvu de dépendance et rapide — un faux "couvert" est
possible (le nom apparaît sans que la fonction soit vraiment testée), jamais
l'inverse dans le sens dangereux (on ne génère pas de test pour une fonction
réellement testée, ce qui limite le gaspillage de tokens).

Pour chaque fonction non couverte, un test pytest est généré par DeepSeek et
**exécuté isolément** ; il n'est conservé que s'il passe — sinon il est jeté
et l'échec est notifié (jamais de test cassé qui entre dans la suite).

Désactivé par défaut (``AUTO_TEST_GEN_ENABLED=false``) et sans cible par
défaut (``AUTO_TEST_GEN_TARGET_DIRS`` vide) : opt-in explicite à deux
niveaux, aucune surprise.
"""

from __future__ import annotations

import ast
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

import config
from jarvis.notification_service import notification_service

logger = logging.getLogger(__name__)

GENERATED_TESTS_DIR = config.BASE_DIR / "tests" / "generated"


@dataclass
class FunctionInfo:
    name: str
    lineno: int
    end_lineno: int
    is_async: bool
    module_path: Path


def list_public_functions(module_path: Path) -> list[FunctionInfo]:
    """Fonctions top-level publiques (pas de préfixe `_`) d'un module."""
    try:
        tree = ast.parse(module_path.read_text(encoding="utf-8"))
    except (SyntaxError, OSError):
        return []
    functions = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("_"):
            functions.append(FunctionInfo(
                name=node.name, lineno=node.lineno,
                end_lineno=getattr(node, "end_lineno", node.lineno),
                is_async=isinstance(node, ast.AsyncFunctionDef),
                module_path=module_path,
            ))
    return functions


def _function_source(fn: FunctionInfo) -> str:
    lines = fn.module_path.read_text(encoding="utf-8").splitlines()
    return "\n".join(lines[fn.lineno - 1:fn.end_lineno])


def _referenced_in_tests(name: str, tests_dir: Path) -> bool:
    if not tests_dir.is_dir():
        return False
    for f in tests_dir.rglob("test_*.py"):
        try:
            if name in f.read_text(encoding="utf-8", errors="ignore"):
                return True
        except OSError:
            continue
    return False


def find_uncovered_functions(target_dirs: list[Path], tests_dir: Path) -> list[FunctionInfo]:
    """Fonctions publiques jamais référencées par nom dans tests_dir."""
    uncovered: list[FunctionInfo] = []
    for d in target_dirs:
        if not d.is_dir():
            continue
        for py in sorted(d.rglob("*.py")):
            if py.name.startswith("test_") or "__pycache__" in py.parts:
                continue
            for fn in list_public_functions(py):
                if not _referenced_in_tests(fn.name, tests_dir):
                    uncovered.append(fn)
    return uncovered


def _extract_code_block(raw: str) -> str:
    """Retire les fences ```python … ``` éventuelles d'une réponse LLM."""
    text = (raw or "").strip()
    if "```" in text:
        parts = text.split("```")
        for part in parts:
            candidate = part
            if candidate.lstrip().startswith("python"):
                candidate = candidate.split("\n", 1)[1] if "\n" in candidate else ""
            if "def test_" in candidate or "import" in candidate:
                return candidate.strip()
    return text


async def generate_test_for_function(fn: FunctionInfo, base_dir: Path = config.BASE_DIR) -> dict:
    """Génère, écrit et valide un test pour `fn`. Jette le fichier si les tests échouent.

    Retourne ``{ok, path, reason}``.
    """
    import llm

    module_rel = fn.module_path.relative_to(base_dir)
    module_import = str(module_rel.with_suffix("")).replace("/", ".")
    source = _function_source(fn)

    try:
        result = await llm.chat(
            messages=[{"role": "user", "content": (
                f"Module : {module_import}\nFonction ({'async ' if fn.is_async else ''}"
                f"{fn.name}) :\n```python\n{source}\n```"
            )}],
            model=config.DEEPSEEK_MAIN_MODEL,
            system=(
                "Tu es un développeur senior Python. Écris un fichier de test pytest "
                "COMPLET (imports inclus) pour la fonction donnée. Le module s'importe "
                f"avec `from {module_import} import {fn.name}`. "
                + ("Utilise `@pytest.mark.asyncio` et `await` si la fonction est async. "
                   if fn.is_async else "")
                + "Teste le comportement réel visible depuis la signature et le corps "
                "(cas simple + un cas limite). N'invente pas de dépendances externes non "
                "visibles dans le code. Réponds UNIQUEMENT avec le code Python du fichier "
                "de test, sans explication ni fence markdown superflue."
            ),
            max_tokens=1200,
            temperature=0.2,
        )
    except Exception as e:
        return {"ok": False, "path": None, "reason": f"LLM indisponible : {e}"}

    code = _extract_code_block(result["content"])
    if "def test_" not in code:
        return {"ok": False, "path": None, "reason": "réponse LLM sans fonction de test"}

    GENERATED_TESTS_DIR.mkdir(parents=True, exist_ok=True)
    safe_module = module_import.replace(".", "_")
    test_path = GENERATED_TESTS_DIR / f"test_gen_{safe_module}_{fn.name}.py"
    test_path.write_text(code, encoding="utf-8")

    try:
        proc = subprocess.run(
            ["python3", "-m", "pytest", str(test_path), "-q"],
            cwd=base_dir, capture_output=True, text=True, timeout=60,
        )
    except subprocess.TimeoutExpired:
        test_path.unlink(missing_ok=True)
        return {"ok": False, "path": None, "reason": "timeout à l'exécution du test généré"}

    if proc.returncode != 0:
        test_path.unlink(missing_ok=True)
        return {"ok": False, "path": None, "reason": proc.stdout[-500:] or proc.stderr[-500:]}

    return {"ok": True, "path": str(test_path), "reason": None}


async def run_test_generation() -> dict:
    """Point d'entrée (scheduler + endpoint). No-op si non configuré."""
    if not config.AUTO_TEST_GEN_ENABLED:
        return {"ok": False, "reason": "AUTO_TEST_GEN_ENABLED désactivé"}
    target_dirs = [d.strip() for d in config.AUTO_TEST_GEN_TARGET_DIRS.split(",") if d.strip()]
    if not target_dirs:
        return {"ok": False, "reason": "AUTO_TEST_GEN_TARGET_DIRS vide — aucune cible configurée"}

    dirs = [config.BASE_DIR / d for d in target_dirs]
    tests_dir = config.BASE_DIR / "tests"
    uncovered = find_uncovered_functions(dirs, tests_dir)

    generated: list[str] = []
    failed: list[dict] = []
    for fn in uncovered[:config.AUTO_TEST_GEN_MAX_PER_RUN]:
        outcome = await generate_test_for_function(fn)
        if outcome["ok"]:
            generated.append(f"{fn.module_path.name}::{fn.name}")
        else:
            failed.append({"function": f"{fn.module_path.name}::{fn.name}", "reason": outcome["reason"]})

    if generated or failed:
        content = f"{len(generated)} test(s) généré(s) et validé(s)"
        if failed:
            content += f", {len(failed)} échec(s) de génération"
        notification_service.create(
            source="system", title="Génération de tests manquants", content=content, priority="low",
        )
    logger.info("[test-gen] %d généré(s), %d échoué(s), %d fonction(s) non couverte(s) au total",
               len(generated), len(failed), len(uncovered))
    return {
        "ok": True, "generated": generated, "failed": failed,
        "uncovered_total": len(uncovered),
    }
