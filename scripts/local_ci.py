"""Pipeline CI locale — build/test/lint, sans dépendance à GitHub Actions.

Trois étapes, chacune best-effort (une étape indisponible est *skip*, jamais
un échec de pipeline) :

1. **Lint** : ``ruff`` si installé, sinon ``py_compile`` sur chaque fichier
   Python modifié (vérifie au moins la syntaxe — aucune nouvelle dépendance
   requise pour que la CI locale fonctionne).
2. **Tests** : la suite pytest complète du projet. Le temps d'exécution est
   enregistré (``scripts.perf_regression``, scope ``"jarvis"``) — une
   régression de perf déclenche une alerte (jamais de modification
   automatique du code de l'assistant, voir ``scripts/self_healing.py``
   pour la version supervisée).
3. **Build frontend** : ``pnpm run build`` (typecheck + Vite) si
   ``LOCAL_CI_RUN_FRONTEND_BUILD=true`` — désactivé par défaut car trop
   lent pour tourner à *chaque* commit ; à réserver au hook ``pre-push`` ou
   à un lancement manuel.

Utilisable en CLI (``python scripts/local_ci.py``, code de sortie non-nul si
un échec bloquant) ou importé par le hook ``pre-commit`` installé via
``scripts/install_git_hooks.py``.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import time
from pathlib import Path

import config


def _run(cmd: list[str], cwd: Path, timeout: int = 300) -> dict:
    try:
        t0 = time.monotonic()
        result = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, timeout=timeout)
        duration_ms = (time.monotonic() - t0) * 1000
        return {
            "returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr,
            "duration_ms": duration_ms,
        }
    except subprocess.TimeoutExpired:
        return {"returncode": -1, "stdout": "", "stderr": f"timeout après {timeout}s", "duration_ms": timeout * 1000}
    except (OSError, FileNotFoundError) as e:
        return {"returncode": -1, "stdout": "", "stderr": str(e), "duration_ms": 0}


def step_lint(root: Path, files: list[Path] | None = None) -> dict:
    """Ruff si présent, sinon vérification syntaxique via py_compile."""
    if shutil.which("ruff"):
        result = _run(["ruff", "check", "."], root, timeout=60)
        return {"name": "lint", "tool": "ruff", "ok": result["returncode"] == 0,
                "output": (result["stdout"] + result["stderr"])[-2000:], "duration_ms": result["duration_ms"]}

    targets = files or list(root.glob("*.py"))
    py_files = [f for f in targets if f.suffix == ".py" and f.is_file()]
    errors = []
    t0 = time.monotonic()
    for f in py_files:
        r = _run([sys.executable, "-m", "py_compile", str(f)], root, timeout=20)
        if r["returncode"] != 0:
            errors.append(f"{f}: {r['stderr'][:200]}")
    duration_ms = (time.monotonic() - t0) * 1000
    return {
        "name": "lint", "tool": "py_compile (ruff absent)", "ok": not errors,
        "output": "\n".join(errors)[-2000:], "duration_ms": duration_ms,
        "files_checked": len(py_files),
    }


def step_tests(root: Path) -> dict:
    """Suite pytest complète — durée enregistrée pour la détection de régression."""
    result = _run(
        [sys.executable, "-m", "pytest", "tests/", "jarvis/tests", "agents/devagent", "-q"],
        root, timeout=300,
    )
    ok = result["returncode"] == 0

    try:
        from scripts.perf_regression import alert_regression, record_and_check
        from agents.devagent.executor import git_current_sha

        sha = git_current_sha(root)
        regression = record_and_check("jarvis", sha, result["duration_ms"])
        if regression:
            alert_regression(regression, extra="(suite de tests JARVIS, CI locale)")
    except Exception:
        pass  # la CI locale ne doit jamais échouer à cause du monitoring perf lui-même

    return {
        "name": "tests", "ok": ok,
        "output": (result["stdout"] + result["stderr"])[-3000:], "duration_ms": result["duration_ms"],
    }


def step_frontend_build(root: Path) -> dict | None:
    """pnpm run build (typecheck + Vite) — seulement si activé (lent)."""
    if not config.LOCAL_CI_RUN_FRONTEND_BUILD:
        return None
    web_dir = root / "web"
    if not (web_dir / "package.json").is_file() or not shutil.which("pnpm"):
        return {"name": "frontend_build", "ok": True, "output": "skip (pnpm ou web/ absent)", "duration_ms": 0}
    result = _run(["pnpm", "run", "build"], web_dir, timeout=180)
    return {
        "name": "frontend_build", "ok": result["returncode"] == 0,
        "output": (result["stdout"] + result["stderr"])[-2000:], "duration_ms": result["duration_ms"],
    }


def run_local_ci(root: Path | None = None) -> dict:
    """Exécute lint → tests → (build frontend optionnel). Retourne le rapport complet."""
    root = root or config.BASE_DIR
    steps = [step_lint(root), step_tests(root)]
    frontend = step_frontend_build(root)
    if frontend is not None:
        steps.append(frontend)

    all_ok = all(s["ok"] for s in steps)
    return {"all_ok": all_ok, "steps": steps}


def _print_report(report: dict) -> None:
    for step in report["steps"]:
        status = "OK" if step["ok"] else "ÉCHEC"
        print(f"[{status}] {step['name']} ({step['duration_ms']:.0f} ms)")
        if not step["ok"] and step.get("output"):
            print(step["output"])
    print("─" * 40)
    print("RÉSULTAT :", "VERT" if report["all_ok"] else "ROUGE")


if __name__ == "__main__":
    report = run_local_ci()
    _print_report(report)
    sys.exit(0 if report["all_ok"] else 1)
