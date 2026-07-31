<!--
source_agent: bc-019fb873-a157-7bab-8ad5-1fad11fd3c30
agent_name: Cognitif cursor devagent architecture
agent_url: https://cursor.com/agents/bc-019fb873-a157-7bab-8ad5-1fad11fd3c30
agent_status: IDLE
created_at: 2026-07-31T13:53:38.928000+00:00
extracted_msg_index: 232
extracted_at: 2026-07-31T14:37:19.333066+00:00
-->

# AUDIT — P12 — Cognitif, Cursor, DevAgent

## Métadonnées
- Agent / modèle : Cloud Agent (Composer) — auditeur architecture LLM routing
- Date : 2026-07-31
- Commit audité (`git rev-parse HEAD`) : `0c8f82296e884412fd1caceeadf04b291584f945`
- Branche : `elias/fitness-meal-ai-photo-8e4f`
- Fichiers dans le périmètre (count) : 53
- Fichiers lus (count) : 53
- Couverture estimée : 94% (prompts/cursor : invariants « jamais main / pr_only » vérifiés sur les 19 templates ; corps métier deep-read sur les chemins critiques)

## Synthèse exécutive
Le cœur de politique Flash/Main/Cursor/Ollama est **solide** : `CognitiveRouter.route()` est déterministe (regex, zéro LLM), Ollama est hors conversation et verrouillé par allowlist + contrat `offenders == []`, les jobs Cursor vivent en `jarvis/cursor/<job_id>` sous `.jarvis/worktrees/`, avec reprise au restart et refus explicite de travailler sur `main`/`master`. Les briefings respectent Main (écran) / Flash (voix) + dédup. Les écarts réels sont : (1) isolation DevAgent poreuse via `../` dans les écritures LLM, (2) « PR-only » affaibli par `CURSOR_ALLOW_PR=false` (défaut) et mutations quality opt-in hors Cursor, (3) redaction Cursor = secrets seulement (pas PII), (4) logs interview/spec DevAgent non rédigés. Verdict global : **PARTIEL — fondations conformes, trous d’isolation/redaction/contrat PR**.

## Findings

### F-P12-001
- Sévérité : HIGH
- Type : sécurité
- Titre : Path traversal DevAgent — écriture hors sandbox via chemins relatifs LLM
- Preuve : `agents/devagent/loop.py:37-41`
```python
def _write_generated_files(project_path: Path, files: dict[str, str]) -> None:
    for rel, content in files.items():
        full = project_path / "src" / rel
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content, encoding="utf-8")
```
  Même motif : `agents/devagent/refactor.py:105-108`. Aucun `resolve()` + `is_relative_to(project_path)`.
- Impact : un `rel` du type `../../../agents/foo.py` peut écrire dans le tree JARVIS hors `DEV_PROJECTS_ROOT/{slug}/`.
- Repro / condition : boucle DevAgent ou refactor avec payload `files` contrôlé par le LLM (ou réponse JSON malveillante/compromised).
- Correctif proposé (sans coder) : résoudre chaque cible et exiger `full.resolve().is_relative_to((project_path / "src").resolve())` ; rejeter `..` dans les parts ; tests d’injection.
- Confiance : haute

### F-P12-002
- Sévérité : MEDIUM
- Type : contrat-cassé
- Titre : Mode `pr_only` / self-mod : jobs peuvent terminer sans PR
- Preuve : enqueue lit `CURSOR_ALLOW_PR` / `CURSOR_ALLOW_PUSH` (défauts false côté config) — `integrations/cursor_delegation.py:222-225` ; ouverture PR conditionnelle `427-431` ; `_maybe_open_pr` early-return si `not allow_push` `495-496`. Autonomy expose le mode `pr_only` `api/router_cognitive.py:321-324`. Templates exigent PR (`prompts/cursor/self_repair.md:17,34,62`).
- Impact : self-repair / self-improvement / loop peuvent marquer `completed` sur branche `jarvis/cursor/*` locale sans draft PR — conforme « jamais main », non conforme à la promesse produit « s’arrête à la PR ».
- Repro / condition : `SELF_MODIFICATION_MODE=pr_only` + `CURSOR_ALLOW_PR=false` (défaut) + job self_repair réussi.
- Correctif proposé (sans coder) : pour templates autonomy (`self_repair`, `self_improvement`), forcer `allow_push`/`allow_pr=True` ou échouer le job si PR impossible ; documenter l’écart dans `/autonomy/settings`.
- Confiance : haute

### F-P12-003
- Sévérité : MEDIUM
- Type : sécurité
- Titre : Frontière PII Cursor = secrets only ; diagnostic expose `user_request`
- Preuve : redaction secrets `jarvis/security/redaction.py:10-37` (hors P12, consommé ici) ; usage systématique `integrations/cursor_delegation.py:182-218,315,378` ; vue publique omet le brut `130-157` ; vue diagnostic garde `user_request` tronqué `160-172` ; API `diagnostic=true` `api/router_cognitive.py:143,167`.
- Impact : emails/téléphones/noms dans une demande technique ne sont pas masqués avant envoi CLI / persistance ; un opérateur avec `diagnostic=true` lit le texte utilisateur (secrets masqués seulement).
- Repro / condition : enqueue avec PII dans `user_request` ; GET job `?diagnostic=true`.
- Correctif proposé (sans coder) : appliquer `PIIAnonymizer` (ou équivalent) avant composition prompt + insert ; restreindre diagnostic aux sessions admin et journaliser l’accès.
- Confiance : haute

### F-P12-004
- Sévérité : MEDIUM
- Type : sécurité
- Titre : Logs DevAgent — redaction seulement sur `dev_loop_log`
- Preuve : `database/devagent.py:269-277` (`redact_action_log_payload`) vs `save_interview_context` JSON brut `184-195` ; `save_spec` / deployments sans redact (record_deployment ~344+).
- Impact : réponses d’interview / specs / stdout tests de staging peuvent persister secrets ou PII en clair dans SQLite.
- Repro / condition : interview contenant un token ; ou staging avec stderr contenant un chemin/clé.
- Correctif proposé (sans coder) : même boundary `redact_action_log_payload` / `redact_sensitive_*` sur `context_json`, `spec_json`, `dev_deployments.log`.
- Confiance : haute

### F-P12-005
- Sévérité : MEDIUM
- Type : sécurité
- Titre : `run_isolated` DevAgent propage tout `os.environ`
- Preuve : `agents/devagent/executor.py:39-48` — `full_env = {**os.environ, **env}` puis `subprocess.run(...)`. Contraste Cursor : `build_cursor_safe_env` `integrations/cursor_env.py:42-70`.
- Impact : commandes projet (tests, git, LLM tools) héritent `DEEPSEEK_API_KEY`, tokens device, etc.
- Repro / condition : n’importe quel `run_isolated` pendant une boucle DevAgent sur machine avec `.env` chargé.
- Correctif proposé (sans coder) : réutiliser / adapter `build_cursor_safe_env` (ou allowlist) pour le sandbox DevAgent.
- Confiance : haute

### F-P12-006
- Sévérité : MEDIUM
- Type : robustesse / sécurité
- Titre : Redaction DB Cursor non défensive en profondeur
- Preuve : `_insert_cursor_job_row` écrit `user_request`/`prompt_sent` tels quels `database/cursor_jobs.py:104-126` ; redaction seulement dans `update_cursor_job` `173-183` avec `except Exception: pass`.
- Impact : appelant qui bypasse `enqueue` (ou échec silencieux de l’import redaction) persiste du secret en clair ; update peut aussi skipper la redaction.
- Repro / condition : `create_cursor_job({...prompt_sent: "sk-..."})` direct ; ou exception dans le bloc try d’update.
- Correctif proposé (sans coder) : redact dans `_insert_cursor_job_row` ; ne pas avaler l’échec de redaction (fail-closed ou log ERROR + redact fallback grossier).
- Confiance : moyenne

### F-P12-007
- Sévérité : LOW
- Type : dette
- Titre : `route_async` LLM fallback peut proposer Cursor sans recheck CLI
- Preuve : `jarvis/cognitive/router.py:293-324` — après `route()` déterministe, si `use_llm_fallback` et label `CURSOR`, force `execution_type="cursor"` sans rejouer le check `CURSOR_DELEGATION_ENABLED` / `_cli_info` (présent en sync `196-207`). Aucun caller prod de `route_async` (grep).
- Impact : surface morte aujourd’hui ; si branchée sans garde, fausse promesse Cursor.
- Repro / condition : appeler `route_async(..., use_llm_fallback=True)` avec Cursor off.
- Correctif proposé (sans coder) : supprimer l’API ou y réappliquer les mêmes gardes que `route()` ; ajouter un test de non-régression.
- Confiance : haute

### F-P12-008
- Sévérité : LOW
- Type : doc-drift
- Titre : Registry / message d’erreur Ollama omettent meal_analysis
- Preuve : allowlist `jarvis/cognitive/ollama_guard.py:18-23` inclut `app/fitness/meal_analysis.py` ; message d’erreur `145-147` « hors Screen Watcher / ollama_control » ; capability `screen_watcher.vision` dit « seul usage Ollama autorisé » `capability_registry.py:124-129`.
- Impact : opérateur / auditeur croit à 2 consommateurs alors qu’il y en a 3 ; faux positifs de diagnostic.
- Repro / condition : lecture registry / erreur policy.
- Correctif proposé (sans coder) : aligner libellés sur `OLLAMA_ALLOWED_MODULES`.
- Confiance : haute

### F-P12-009
- Sévérité : MEDIUM
- Type : contrat-cassé
- Titre : Quality router — mutations opt-in sur le codebase JARVIS (hors PR Cursor)
- Preuve : `api/router_quality.py:64-73` (`security/{id}/fix`), `76-81` (génération tests), `20-28` (install hook git). Doc CLAUDE : report-only sur JARVIS sauf opt-in.
- Impact : contournement du chemin self-mod worktree+PR si flags `.env` activés — écriture directe tracked files / hooks.
- Repro / condition : `SECURITY_AUTO_FIX_ENABLED=true` puis POST fix ; ou `AUTO_TEST_GEN_ENABLED`.
- Correctif proposé (sans coder) : router ces mutations via Cursor `pr_only`, ou exiger confirmation + audit log + refus si `SELF_MODIFICATION_MODE=pr_only`.
- Confiance : haute

### F-P12-010
- Sévérité : INFO
- Type : dead-code / smell
- Titre : `allow_merge` / `ExecutionType=workflow` non opérationnels
- Preuve : `allow_merge` persisté `cursor_delegation.py:225` — aucun `gh pr merge` dans le module ; `ExecutionType` inclut `"workflow"` `models.py:10` jamais émis par `route()`.
- Impact : configuration trompeuse (`auto_merge_low_risk` aspirational).
- Repro / condition : lecture config / modèles.
- Correctif proposé (sans coder) : retirer ou implémenter explicitement ; documenter « non branché ».
- Confiance : haute

### F-P12-011
- Sévérité : INFO
- Type : smell
- Titre : `/loop` auto-start Cursor sans confirmation humaine
- Preuve : `agents/autonomous_loop.py:234-242` — `auto_start=True`, `require_confirmation=False`. Commentaire : mode autonome explicite.
- Impact : risque accepté si l’utilisateur tape `/loop` ; pas un bypass chat/voix (ceux-ci passent par confirmation).
- Repro / condition : `/loop corrige le module X`.
- Correctif proposé (sans coder) : optionnel — confirmation pour `risk_level=high` même en loop ; journaliser dans autonomy settings.
- Confiance : haute

### F-P12-012
- Sévérité : LOW
- Type : dette
- Titre : Flag `allow_commit` stocké mais jamais lu dans le runner Cursor
- Preuve : écrit `cursor_delegation.py:222` ; grep runner — seule occurrence ; commits laissés au CLI dans le worktree. Garde réelle = `PROTECTED_BRANCHES` `411-421`.
- Impact : `CURSOR_ALLOW_COMMIT=false` n’a aucun effet.
- Repro / condition : désactiver le flag et lancer un job.
- Correctif proposé (sans coder) : honorer le flag (prompt + post-check clean tree) ou le supprimer de la config/API.
- Confiance : haute

## Contrats vérifiés
| Contrat / invariant | Statut | Preuve |
|---|---|---|
| 1. `router.py` déterministe avant LLM (chemin prod) | OK | `route()` L142-291 regex-only ; callers = `route_request` ; Ollama jamais dans `ExecutionType` |
| 1b. Aucun LLM dans le chemin sync | OK | pas d’import `llm` dans `route()` ; `route_async` optionnel/non branché (F-007) |
| 2. `ollama_guard` allowlist runtime | OK | `OLLAMA_ALLOWED_MODULES` L18-23 ; resolve path exact L116-148 |
| 2b. Scan statique `offenders == []` | OK | `tests/test_cognitive_routing.py:150-162` |
| 2c. Router ne sélectionne jamais Ollama conversation | OK | backends answer/tool/cursor seulement |
| 3. Branche `jarvis/cursor/<job_id>` | OK | `cursor_delegation.py:121-122` |
| 3b. Worktree `.jarvis/worktrees/` | OK | `_worktree_root` L99-105 + `worktree add` L133 |
| 3c. Jamais travail sur main/master (live HEAD worktree) | OK | `PROTECTED_BRANCHES` L48, refus L411-421 |
| 4. `SELF_MODIFICATION_MODE` défaut `pr_only` | OK | `config.py:488` ; prompts self_repair/self_improvement |
| 4b. Self-mod JARVIS uniquement via Cursor worktree (nominal) | OK / PARTIEL | nominal OK ; trous F-002, F-009 (+ frontière self_healing AUTO_APPLY → P11) |
| 5. Reprise jobs au restart | OK | `resume_pending_jobs` L592-625 ; câblé lifespan (frontière P01/P03) |
| 5b. Jobs persistants SQLite | OK | `database/cursor_jobs.py` table + `VALID_STATUSES` + capacity lock |
| 6. Redaction secrets Cursor (prompt/API/DB update) | OK | enqueue + update + public view |
| 6b. Redaction PII sur croisement Cursor/DevAgent | KO / PARTIEL | F-003, F-004 |
| Briefings Main écran + Flash voix + dédup | OK | `briefing_engine.py:55-64,365-401` |
| `/loop` route cognitive → Cursor worktree | OK | `autonomous_loop.py:222-257` |
| DevAgent isole sous `DEV_PROJECTS_ROOT` (intention) | PARTIEL | `spec_builder.py:18-24` ; cassé par F-001 |
| Quality « report-only » sur JARVIS | PARTIEL | doublons OK ; fix/tests/hooks = F-009 |
| Tests requis Cursor sans shell | OK | `cursor_required_tests.py` allowlist + anti-`..` |
| Env Cursor minimal (pas `os.environ` entier) | OK | `cursor_env.py` |
| Templates `prompts/cursor/*` « jamais main » | OK | 18/19 match explicite ; `regression_review.md` équivalent |

## Frontières / dépendances
- Signale vers **P01/P03** (`api/lifespan.py`) : appel `cursor_delegation.resume_pending_jobs()` au startup — hors lecture ligne-à-ligne ici, contrat repris OK.
- Signale vers **P02** (`jarvis/security/redaction.py`, `jarvis/log_privacy.py`, `jarvis/pii/`) : redaction secrets vs PII — findings F-003/F-004 consomment ces modules.
- Signale vers **P05** : agents historiques / orchestrateur / `agents/devops.py` conversationnel (Main) sans passer par le routeur cognitif pour chaque tour interne.
- Signale vers **P08** : sécurité subprocess Cursor CLI déjà partiellement couverte ici ; P08 OWN `cursor_*.py` pour subprocess.
- Signale vers **P10** : usage Ollama dans `screen_watcher` / meal_analysis — ici uniquement allowlist `ollama_guard`.
- Signale vers **P11** (`scripts/self_healing.py`, `self_improvement.py`, `scheduler.py`) : si `SELF_REPAIR_ENABLED=false` et `SELF_HEALING_AUTO_APPLY=true`, patch local possible même avec `SELF_MODIFICATION_MODE=pr_only` (`self_healing.py:219-271`) — hors fichiers INCLUS, mais casse le contrat P12.
- Signale vers **P13** : `app/fitness/meal_analysis.py` dans allowlist Ollama.
- Attendus de P12 consommés ailleurs : `route_request` (chat/voix/loop), `cursor_delegation` (API + autonomy), `briefing_engine` (scheduler/API), `ollama_http_request` (client Ollama).

## Fichiers non lus
| Fichier | Motif |
|---|---|
| *(aucun fichier du périmètre non ouvert)* | — |
| `prompts/cursor/*.md` (corps hors invariants) | Invariants « main / pr_only / hors-périmètre » vérifiés sur les 19 ; pas chaque paragraphe pédagogique relu ligne à ligne (couverture partielle assumée dans le 94%). |

## Couverture
- Liste exhaustive des fichiers lus (chemins relatifs), triée.

```
agents/autonomous_loop.py
agents/briefing_engine.py
agents/devagent/__init__.py
agents/devagent/autorun.py
agents/devagent/coder.py
agents/devagent/executor.py
agents/devagent/git_ops.py
agents/devagent/interview.py
agents/devagent/loop.py
agents/devagent/models.py
agents/devagent/planner.py
agents/devagent/pr.py
agents/devagent/refactor.py
agents/devagent/spec_builder.py
agents/devagent/staging.py
agents/devagent/utils.py
agents/devops.py
api/router_cognitive.py
api/router_devagent.py
api/router_quality.py
database/cursor_jobs.py
database/devagent.py
database/devops.py
integrations/cursor_cli.py
integrations/cursor_delegation.py
integrations/cursor_env.py
integrations/cursor_prompt_composer.py
integrations/cursor_required_tests.py
jarvis/cognitive/__init__.py
jarvis/cognitive/capability_registry.py
jarvis/cognitive/context_planner.py
jarvis/cognitive/models.py
jarvis/cognitive/ollama_guard.py
jarvis/cognitive/router.py
prompts/cursor/android_feature.md
prompts/cursor/backend_feature.md
prompts/cursor/bug_fix.md
prompts/cursor/ci_repair.md
prompts/cursor/database_migration.md
prompts/cursor/documentation_sync.md
prompts/cursor/feature_implementation.md
prompts/cursor/frontend_feature.md
prompts/cursor/integration_validation.md
prompts/cursor/performance_audit.md
prompts/cursor/refactor_safe.md
prompts/cursor/regression_review.md
prompts/cursor/release_build.md
prompts/cursor/runtime_diagnosis.md
prompts/cursor/security_audit.md
prompts/cursor/self_improvement.md
prompts/cursor/self_repair.md
prompts/cursor/test_creation.md
prompts/cursor/voice_pipeline.md
```