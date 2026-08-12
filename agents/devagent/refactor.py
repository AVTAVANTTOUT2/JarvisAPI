"""Auto-refactor du code dupliqué détecté — réservé aux projets DevAgent.

Contrairement au scan de la codebase JARVIS (``scripts/duplicate_scanner``,
rapport seul), un projet DevAgent est isolé, versionné par git et couvert par
sa propre suite de tests : un refactor assisté par LLM peut donc être
**appliqué puis validé automatiquement**. Le principe :

1. Scanner ``src/`` du projet pour le plus gros bloc dupliqué.
2. Confier l'édition au runtime agentique générique.
3. Écrire les fichiers, lancer la suite de tests du projet.
4. Tests verts → commit. Tests rouges → ``git checkout`` (annulation totale,
   aucune trace du refactor raté).

Un seul refactor par appel (pas de boucle) pour borner le coût et le risque.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

import config
from agents.devagent.agentic_runtime import (
    AgenticRuntimeUnavailable,
    delegate_agentic_task,
    legacy_fallback_enabled,
    resolve_runtime,
)
from agents.devagent.executor import git_commit, resolve_generated_path, run_isolated
from agents.devagent.utils import parse_json_response
from integrations.deepseek_client import call_deepseek
from scripts.duplicate_scanner import find_duplicates

logger = logging.getLogger(__name__)

REFACTOR_PROMPT = """Role: développeur senior Python.
Deux blocs de code quasi-identiques ont été détectés dans le même projet :

Fichier A ({file_a}, lignes {start_a}-{end_a}) :
```python
{code_a}
```

Fichier B ({file_b}, lignes {start_b}-{end_b}) :
```python
{code_b}
```

Contenu complet actuel des fichiers concernés (JSON {{chemin: contenu}}) :
{existing_content}

Refactore pour éliminer la duplication : extrais le code commun dans une
fonction partagée (nouveau fichier si pertinent, ou un fichier existant
approprié), puis fais appeler cette fonction depuis les deux emplacements.
Ne change AUCUN autre comportement.

Retourne UNIQUEMENT ce JSON :
{{"files": {{"chemin/fichier.py": "contenu complet du fichier"}}, "summary": "..."}}
"""


def _extract_block(path: Path, start: int, end: int) -> str:
    lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    return "\n".join(lines[start - 1:end])


async def refactor_top_duplicate(
    project_path: Path,
    test_command: str = "python3 -m pytest -q",
) -> dict:
    """Refactore via le runtime générique; l'ancien moteur exige un opt-in."""

    try:
        service, _runtime_id = resolve_runtime()
    except AgenticRuntimeUnavailable as exc:
        if legacy_fallback_enabled():
            return await _refactor_top_duplicate_legacy(project_path, test_command)
        return {"ok": False, "applied": False, "reason": str(exc)}

    src = project_path / "src"
    if not src.is_dir():
        return {"ok": False, "applied": False, "reason": "pas de dossier src/"}
    blocks = find_duplicates(project_path, ["src"], min_lines=6)
    if not blocks:
        return {"ok": True, "applied": False, "reason": "aucune duplication détectée"}
    top = blocks[0]
    code_a = _extract_block(project_path / top.file_a, top.start_a, top.end_a)
    code_b = _extract_block(project_path / top.file_b, top.start_b, top.end_b)
    instruction = REFACTOR_PROMPT.format(
        file_a=top.file_a,
        start_a=top.start_a,
        end_a=top.end_a,
        code_a=code_a,
        file_b=top.file_b,
        start_b=top.start_b,
        end_b=top.end_b,
        code_b=code_b,
        existing_content="Les fichiers complets sont disponibles dans le worktree.",
    )
    digest = hashlib.sha256(instruction.encode("utf-8")).hexdigest()[:24]
    outcome = await delegate_agentic_task(
        title=f"Refactor duplication {top.file_a} / {top.file_b}",
        instruction=(
            instruction
            + "\nJARVIS exécute seul les tests et Git. Ne lance ni test ni commande Git."
        ),
        channel="devagent-refactor",
        origin="devagent",
        workspace=project_path.resolve(strict=True),
        task_id=f"refactor:{digest}",
        idempotency_key=f"devagent-refactor:{digest}",
        permissions=("workspace:read", "workspace:write"),
        selected_context={"delivery_owner": "jarvis"},
        wait=True,
        timeout=float(getattr(config, "AGENTIC_MAX_RUN_SECONDS", 1800)),
        service=service,
    )
    if not outcome.succeeded:
        return {
            "ok": False,
            "applied": False,
            "reason": outcome.summary or f"run agentique {outcome.status}",
            "run_id": outcome.run_id,
        }
    test_result = run_isolated(test_command, cwd=project_path, timeout=120)
    if test_result["returncode"] != 0:
        return {
            "ok": False,
            "applied": False,
            "reason": "tests rouges après refactor; aucun commit créé",
            "test_output": test_result.get("stderr") or test_result.get("stdout", ""),
            "run_id": outcome.run_id,
        }
    commit = git_commit(
        project_path,
        f"refactor: élimine la duplication {top.file_a}/{top.file_b}",
    )
    return {
        "ok": commit.get("returncode") == 0,
        "applied": True,
        "summary": outcome.summary,
        "files": list(outcome.changed_files),
        "commit_ok": commit.get("returncode") == 0,
        "run_id": outcome.run_id,
    }


async def _refactor_top_duplicate_legacy(
    project_path: Path,
    test_command: str = "python3 -m pytest -q",
) -> dict:
    """Refactore le plus gros bloc dupliqué du projet. Ne fait rien si aucun trouvé.

    Retourne ``{ok, applied, summary, reason}``.
    """
    src = project_path / "src"
    if not src.is_dir():
        return {"ok": False, "applied": False, "reason": "pas de dossier src/"}

    blocks = find_duplicates(project_path, ["src"], min_lines=6)
    if not blocks:
        return {"ok": True, "applied": False, "reason": "aucune duplication détectée"}

    top = blocks[0]
    file_a = project_path / top.file_a
    file_b = project_path / top.file_b
    code_a = _extract_block(file_a, top.start_a, top.end_a)
    code_b = _extract_block(file_b, top.start_b, top.end_b)

    existing_content = {}
    for rel in {top.file_a, top.file_b}:
        p = project_path / rel
        existing_content[rel] = p.read_text(encoding="utf-8") if p.exists() else ""

    response = await call_deepseek(
        system=REFACTOR_PROMPT.format(
            file_a=top.file_a, start_a=top.start_a, end_a=top.end_a, code_a=code_a,
            file_b=top.file_b, start_b=top.start_b, end_b=top.end_b, code_b=code_b,
            existing_content=json.dumps(existing_content, ensure_ascii=False),
        ),
        user="Refactore ce code dupliqué.",
        json_mode=True,
    )
    payload = parse_json_response(response["content"])
    generated = payload.get("files") or {}
    if not isinstance(generated, dict) or not generated:
        return {"ok": False, "applied": False, "reason": "réponse LLM sans fichiers générés"}

    # Sauvegarde du contenu original pour rollback si les tests échouent.
    backup: dict[str, str | None] = {}
    resolved_files = {
        rel: resolve_generated_path(project_path, rel)
        for rel in generated
    }
    for rel in generated:
        p = resolved_files[rel]
        backup[rel] = p.read_text(encoding="utf-8") if p.exists() else None

    for rel, content in generated.items():
        full = resolved_files[rel]
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content, encoding="utf-8")

    test_result = run_isolated(test_command, cwd=project_path, timeout=120)
    if test_result["returncode"] != 0:
        # Rollback : restaure le contenu original (ou supprime le fichier créé).
        for rel, original in backup.items():
            full = resolved_files[rel]
            if original is None:
                full.unlink(missing_ok=True)
            else:
                full.write_text(original, encoding="utf-8")
        logger.warning(
            "[devagent-refactor] tests rouges après refactor — annulé (%s)", top.file_a,
        )
        return {
            "ok": False, "applied": False,
            "reason": "tests rouges après refactor, changements annulés",
            "test_output": test_result.get("stderr") or test_result.get("stdout", ""),
        }

    commit = git_commit(project_path, f"refactor: élimine la duplication {top.file_a}/{top.file_b}")
    logger.info("[devagent-refactor] refactor appliqué et commité : %s", payload.get("summary", ""))
    return {
        "ok": True, "applied": True,
        "summary": payload.get("summary", ""),
        "files": list(generated.keys()),
        "commit_ok": commit.get("returncode") == 0,
    }
