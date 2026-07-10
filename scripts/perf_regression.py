"""Détection de régression de performance — benchmark, rollback, alerte.

Deux usages :

- **Projets DevAgent** (``scope`` = slug du projet) : chronométrage de la
  suite de tests à chaque itération de la boucle (``agents/devagent/loop.py``).
  Une régression déclenche un **rollback automatique** (``git revert`` du
  dernier commit) — le projet est isolé, versionné et jetable, l'opération
  est donc sans risque.
- **JARVIS lui-même** (``scope="jarvis"``) : chronométrage de la suite de
  tests à chaque exécution de la CI locale (``scripts/local_ci.py``). Une
  régression ne déclenche qu'une **alerte** — on ne touche jamais au code de
  l'assistant en production tout seul sans garde-fou explicite (voir
  ``scripts/self_healing.py`` pour la version supervisée et réversible).

Le seuil et la fenêtre de référence sont configurables
(``PERF_REGRESSION_THRESHOLD_PCT``, ``PERF_BASELINE_WINDOW``).
"""

from __future__ import annotations

import logging
from pathlib import Path

import config
from database import create_notification, get_perf_baseline, record_perf_benchmark

logger = logging.getLogger(__name__)


def check_regression(scope: str, duration_ms: float, threshold_pct: int | None = None) -> dict | None:
    """Compare ``duration_ms`` à la médiane des derniers benchmarks du scope.

    Retourne un rapport de régression si le seuil est dépassé, sinon None
    (y compris quand l'historique est encore insuffisant pour juger).
    """
    threshold = threshold_pct if threshold_pct is not None else config.PERF_REGRESSION_THRESHOLD_PCT
    baseline = get_perf_baseline(scope, window=config.PERF_BASELINE_WINDOW)
    if baseline is None or baseline <= 0:
        return None
    pct = (duration_ms - baseline) / baseline * 100
    if pct < threshold:
        return None
    return {
        "regression": True,
        "scope": scope,
        "baseline_ms": round(baseline, 1),
        "duration_ms": round(duration_ms, 1),
        "pct": round(pct, 1),
        "threshold_pct": threshold,
    }


def record_and_check(scope: str, commit_sha: str | None, duration_ms: float) -> dict | None:
    """Vérifie la régression PUIS enregistre le nouveau point (ordre important :
    le nouveau point ne doit pas polluer sa propre comparaison de référence)."""
    report = check_regression(scope, duration_ms)
    record_perf_benchmark(scope, commit_sha, duration_ms)
    return report


def alert_regression(report: dict, extra: str = "") -> None:
    """Notification (priorité haute) — utilisé par le scope 'jarvis' (pas de rollback)."""
    content = (
        f"Suite de tests {report['pct']:+.0f}% plus lente que la référence "
        f"({report['duration_ms']:.0f} ms vs {report['baseline_ms']:.0f} ms). {extra}".strip()
    )
    create_notification(
        source="system", title=f"Régression de performance — {report['scope']}",
        content=content, priority="high",
    )
    logger.warning("[perf] régression détectée (%s) : %s", report["scope"], content)


async def guard_devagent_iteration(project_path: Path, slug: str, commit_sha: str | None,
                                   duration_ms: float) -> dict:
    """Enregistre le benchmark d'une itération DevAgent ; rollback auto si régression.

    À appeler juste après un commit réussi de la boucle autonome, avec la
    durée d'exécution de la suite de tests de CETTE itération. Retourne
    ``{rolled_back: bool, ...}``.
    """
    from agents.devagent.executor import run_isolated

    report = record_and_check(slug, commit_sha, duration_ms)
    if not report:
        return {"rolled_back": False}

    revert = run_isolated(["git", "revert", "--no-edit", "HEAD"], cwd=project_path, timeout=30)
    rolled_back = revert["returncode"] == 0
    content = (
        f"Régression de {report['pct']:.0f}% détectée sur ce commit "
        f"({report['duration_ms']:.0f} ms vs {report['baseline_ms']:.0f} ms de référence). "
        + ("Commit annulé automatiquement (git revert)." if rolled_back
           else "Échec du git revert — intervention manuelle requise.")
    )
    create_notification(
        source="devagent", title=f"Rollback perf — {slug}", content=content,
        priority="high" if rolled_back else "urgent",
    )
    logger.warning("[perf][devagent:%s] %s", slug, content)
    return {"rolled_back": rolled_back, **report}
