"""Self-healing — diagnostic de crash, patch réversible, rollback automatique.

Désactivé par défaut (``SELF_HEALING_ENABLED=false``). Deux niveaux d'opt-in
distincts, volontairement séparés :

- ``SELF_HEALING_ENABLED`` : active le **diagnostic seul** — quand le
  supervisor détecte une boucle de crash (``SELF_HEALING_CRASH_THRESHOLD``
  redémarrages consécutifs), DeepSeek analyse les dernières lignes du log et
  une notification résume la cause probable. Aucune modification de code.
- ``SELF_HEALING_AUTO_APPLY`` : active en plus l'**application du patch**
  suggéré, si et seulement si le diagnostic identifie un fichier ``.py``
  suivi par git avec un contenu de correction complet. Garde-fous :
    - le fichier doit être sous ``config.BASE_DIR`` et suivi par git
      (jamais de mutation d'un fichier arbitraire ou non versionné) ;
    - le nouveau contenu doit au moins compiler (``py_compile``) avant
      d'être retenu ;
    - le patch est un commit git normal — jamais une écriture directe sans
      trace, toujours réversible par ``git revert`` ;
    - si la MÊME boucle de crash se reproduit dans les
      ``SELF_HEALING_REGRESSION_WINDOW_MIN`` minutes suivant un patch, celui-ci
      est automatiquement annulé (``git revert``) et une alerte **urgente**
      demande une intervention manuelle — avec une période de recul
      (``SELF_HEALING_COOLDOWN_MIN``) avant tout nouveau patch automatique.

Ce fichier ne redémarre jamais le processus lui-même — c'est le rôle du
supervisor, qui appelle ``handle_crash_loop`` puis reprend son cycle normal
de redémarrage.
"""

from __future__ import annotations

import json
import logging
import subprocess
from datetime import datetime
from pathlib import Path

import config
from jarvis.notification_service import notification_service

logger = logging.getLogger(__name__)

STATE_PATH = config.BASE_DIR / "data" / ".self_healing_state.json"

DIAGNOSTIC_PROMPT = """Role: ingénieur SRE senior qui diagnostique un crash en production.
Voici les dernières lignes du log au moment du crash :
```
{log_tail}
```

Identifie la cause racine la plus probable. Si tu peux localiser précisément
le fichier et la correction, propose un patch COMPLET du fichier concerné
(pas un diff). Sois conservateur : si tu n'es pas raisonnablement confiant,
ne propose PAS de fichier/correctif — un diagnostic honnête sans patch vaut
mieux qu'un patch hasardeux.

Retourne UNIQUEMENT ce JSON :
{{
  "root_cause": "explication en 2-3 phrases",
  "confidence": "high|medium|low",
  "file": "chemin/relatif/fichier.py ou null",
  "fix_content": "contenu complet corrigé du fichier, ou null"
}}
"""


def _load_state() -> dict:
    if not STATE_PATH.is_file():
        return {}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _minutes_since(iso_ts: str | None) -> float:
    if not iso_ts:
        return float("inf")
    try:
        return (datetime.now() - datetime.fromisoformat(iso_ts)).total_seconds() / 60
    except ValueError:
        return float("inf")


def _in_cooldown(state: dict) -> bool:
    return _minutes_since(state.get("cooldown_started_at")) < config.SELF_HEALING_COOLDOWN_MIN


def _git(args: list[str], cwd: Path, timeout: int = 15) -> dict:
    try:
        result = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, timeout=timeout)
        return {"returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr}
    except (OSError, subprocess.SubprocessError) as e:
        return {"returncode": -1, "stdout": "", "stderr": str(e)}


def _is_git_tracked(path: Path, root: Path) -> bool:
    result = _git(["ls-files", "--error-unmatch", str(path.relative_to(root))], root)
    return result["returncode"] == 0


async def diagnose_crash(log_tail: str) -> dict:
    """Analyse LLM des dernières lignes de log. Ne lève jamais — dégrade proprement."""
    import llm

    try:
        result = await llm.chat(
            messages=[{"role": "user", "content": log_tail[-4000:]}],
            model=config.DEEPSEEK_MAIN_MODEL,
            system=DIAGNOSTIC_PROMPT.format(log_tail=log_tail[-4000:]),
            max_tokens=1500,
            temperature=0.1,
        )
        from agents.devagent.utils import parse_json_response

        return parse_json_response(result["content"])
    except Exception as e:
        logger.warning("[self-healing] diagnostic LLM indisponible : %s", e)
        return {"root_cause": f"Diagnostic indisponible : {e}", "confidence": "low",
                "file": None, "fix_content": None}


def _apply_patch(root: Path, relative_file: str, fix_content: str) -> dict:
    """Applique le patch avec garde-fous. Retourne {ok, commit_sha?, reason?}."""
    if ".." in Path(relative_file).parts:
        return {"ok": False, "reason": "chemin suspect (traversal) refusé"}
    if not relative_file.endswith(".py"):
        return {"ok": False, "reason": "seuls les fichiers .py peuvent être patchés"}

    target = (root / relative_file).resolve()
    try:
        target.relative_to(root.resolve())
    except ValueError:
        return {"ok": False, "reason": "le fichier cible sort de BASE_DIR — refusé"}
    if not target.is_file():
        return {"ok": False, "reason": f"fichier introuvable : {relative_file}"}
    if not _is_git_tracked(target, root):
        return {"ok": False, "reason": "fichier non suivi par git — correctif refusé par sécurité"}

    original = target.read_text(encoding="utf-8")
    target.write_text(fix_content, encoding="utf-8")

    compile_check = subprocess.run(
        ["python3", "-m", "py_compile", str(target)], capture_output=True, text=True, timeout=20,
    )
    if compile_check.returncode != 0:
        target.write_text(original, encoding="utf-8")  # rollback immédiat
        return {"ok": False, "reason": f"le correctif ne compile pas : {compile_check.stderr[:300]}"}

    add = _git(["add", relative_file], root)
    if add["returncode"] != 0:
        target.write_text(original, encoding="utf-8")
        return {"ok": False, "reason": f"git add échoué : {add['stderr'][:300]}"}

    commit = _git(["commit", "-m", f"self-healing: correctif automatique ({relative_file})"], root)
    if commit["returncode"] != 0:
        _git(["checkout", "--", relative_file], root)  # annule le staging + le fichier
        return {"ok": False, "reason": f"git commit échoué : {commit['stderr'][:300]}"}

    sha = _git(["rev-parse", "HEAD"], root)["stdout"].strip()
    return {"ok": True, "commit_sha": sha}


async def handle_crash_loop(crash_tail: str, root: Path | None = None) -> dict:
    """Point d'entrée appelé par le supervisor à chaque seuil de crash-loop.

    Ne lève jamais — toute exception est capturée et journalisée, le
    supervisor ne doit jamais être mis en péril par cette fonction.
    """
    if not config.SELF_HEALING_ENABLED:
        return {"ok": False, "reason": "SELF_HEALING_ENABLED désactivé"}

    root = root or config.BASE_DIR
    try:
        state = _load_state()

        # La boucle de crash s'est reproduite juste après un patch → il n'a
        # pas résolu le problème. Annulation automatique + alerte urgente.
        last_commit = state.get("last_patch_commit")
        if last_commit and _minutes_since(state.get("last_patch_at")) < config.SELF_HEALING_REGRESSION_WINDOW_MIN:
            revert = _git(["revert", "--no-edit", last_commit], root)
            notification_service.create(
                source="system", title="Self-healing annulé — patch inefficace",
                content=(
                    f"Le correctif automatique {last_commit[:8]} n'a pas résolu la boucle de crash "
                    "(récidive sous le délai de surveillance). Annulé automatiquement. "
                    "Intervention manuelle requise."
                ),
                priority="urgent",
            )
            state["last_patch_commit"] = None
            state["last_patch_at"] = None
            state["cooldown_started_at"] = _now_iso()
            _save_state(state)
            logger.critical("[self-healing] patch %s annulé — récidive du crash", last_commit[:8])
            return {"ok": True, "action": "rolled_back", "reverted_commit": last_commit,
                    "revert_ok": revert["returncode"] == 0}

        if _in_cooldown(state):
            return {"ok": False, "reason": "cooldown actif après un échec récent — self-healing en pause"}

        diagnosis = await diagnose_crash(crash_tail)
        notification_service.create(
            source="system", title="Diagnostic self-healing",
            content=f"{diagnosis.get('root_cause', 'cause inconnue')} (confiance : {diagnosis.get('confidence', '?')})",
            priority="high",
        )
        logger.warning("[self-healing] diagnostic : %s", diagnosis.get("root_cause"))

        # Mode préféré 2026 : délégation Cursor (PR only) — jamais de mutation directe de main
        if (
            getattr(config, "SELF_REPAIR_ENABLED", True)
            and getattr(config, "CURSOR_DELEGATION_ENABLED", True)
            and getattr(config, "SELF_MODIFICATION_MODE", "pr_only") == "pr_only"
        ):
            try:
                from integrations.cursor_delegation import cursor_delegation

                from jarvis.security.redaction import redact_sensitive_text

                job = await cursor_delegation.enqueue(
                    title="Self-repair: crash loop",
                    user_request=redact_sensitive_text(
                        "Auto-réparation JARVIS après crash loop.\n"
                        f"Diagnostic: {diagnosis.get('root_cause')}\n"
                        f"Fichier suspect: {diagnosis.get('file')}\n"
                        f"Log tail:\n{crash_tail[-3000:]}\n"
                        "Reproduire, corriger, tester, ouvrir une PR. "
                        "Ne jamais modifier main directement."
                    ),
                    template_id="self_repair",
                    risk_level="high",
                    interaction_mode="scheduled",
                    auto_start=True,
                    require_confirmation=False,  # job scheduler autorisé
                )
                notification_service.create(
                    source="system",
                    title="Self-repair délégué à Cursor",
                    content=f"Job {job.get('job_id')} — mode pr_only",
                    priority="high",
                )
                return {
                    "ok": True,
                    "action": "cursor_delegated",
                    "job_id": job.get("job_id"),
                    "diagnosis": diagnosis,
                }
            except Exception as exc:
                logger.warning("[self-healing] délégation Cursor échouée : %s", exc)
                # pr_only : jamais de patch direct en fallback
                return {
                    "ok": False,
                    "action": "cursor_failed_pr_only",
                    "error": str(exc)[:300],
                    "diagnosis": diagnosis,
                }

        if not config.SELF_HEALING_AUTO_APPLY or not diagnosis.get("file") or not diagnosis.get("fix_content"):
            return {"ok": True, "action": "diagnosed_only", "diagnosis": diagnosis}

        patch_result = _apply_patch(root, diagnosis["file"], diagnosis["fix_content"])
        if not patch_result["ok"]:
            notification_service.create(
                source="system", title="Self-healing — correctif refusé",
                content=patch_result["reason"], priority="medium",
            )
            return {"ok": True, "action": "diagnosed_only", "diagnosis": diagnosis, "patch_reason": patch_result["reason"]}

        state["last_patch_commit"] = patch_result["commit_sha"]
        state["last_patch_at"] = _now_iso()
        _save_state(state)
        notification_service.create(
            source="system", title="Self-healing — correctif appliqué",
            content=f"Correctif appliqué sur {diagnosis['file']} (commit {patch_result['commit_sha'][:8]}). "
                    "Redémarrage en cours ; surveillance de récidive activée.",
            priority="high",
        )
        logger.warning("[self-healing] correctif appliqué : %s", patch_result["commit_sha"])
        return {"ok": True, "action": "patched", "commit_sha": patch_result["commit_sha"], "diagnosis": diagnosis}

    except Exception as e:
        logger.exception("[self-healing] erreur inattendue (ignorée, jamais bloquante) : %s", e)
        return {"ok": False, "reason": f"erreur interne : {e}"}
