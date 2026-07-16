# Délégation Cursor CLI

Dernière mise à jour : 2026-07-16

## Rôle

Exécuter le travail technique (bugs, features, CI, migrations, audits) via Cursor Agent en mode headless, dans un worktree git isolé, jamais sur `main`/`master`.

## Fichiers clés

| Fichier | Rôle |
|---------|------|
| `integrations/cursor_delegation.py` | Orchestration jobs, sémaphore, cancel, reprise |
| `integrations/cursor_cli.py` | Détection binaire, construction commande `--print` |
| `integrations/cursor_prompt_composer.py` | Remplissage templates `prompts/cursor/*.md` |
| `database/cursor_jobs.py` | Persistance SQLite des jobs |
| `api/lifespan.py` | `resume_pending_jobs()` au boot |
| `api/router_cognitive.py` | CRUD / cancel / retry / rollback |
| `prompts/cursor/` | 19 templates différenciés |

## Garanties

1. **Isolation** — branche + worktree sous `CURSOR_WORKTREE_ROOT` ; branches protégées `main`/`master` refusées.
2. **CLI** — commande avec `--print` et `--output-format text` ; `returncode` vérifié.
3. **Concurrence** — sémaphore `CURSOR_MAX_CONCURRENT_JOBS` (défaut 2).
4. **Cancel** — kill du process group ; statut mis à jour avant kill.
5. **Reprise** — jobs `queued`/`running` relancés au startup via lifespan.
6. **Secrets** — redaction API keys / Bearer / sk- avant envoi au prompt.
7. **Mode** — `SELF_MODIFICATION_MODE=pr_only` : commit/push/PR autorisés selon flags ; merge sur main désactivé par défaut (`CURSOR_ALLOW_MERGE=false`).

## Flux

```
enqueue(user_request, template_id, ...)
  → UUID job_id
  → create_cursor_job (SQLite)
  → worktree + branche feat/jarvis-cursor-<id>
  → compose_cursor_prompt(template)
  → agent --print ...
  → parse résultat + tests
  → update statut (succeeded/failed)
  → optionnel : commit / push / gh pr create
```

## Templates

19 fichiers dans `prompts/cursor/` (bug_fix, frontend_feature, self_repair, voice_pipeline, …). Chaque template a un corps unique (pas de copie générique).

## Config

```bash
CURSOR_DELEGATION_ENABLED=true
CURSOR_CLI_PATH=                 # vide = auto-detect agent / cursor
CURSOR_DEFAULT_TIMEOUT_SEC=1800
CURSOR_MAX_CONCURRENT_JOBS=2
CURSOR_WORKTREE_ROOT=.jarvis/worktrees
CURSOR_ALLOW_COMMIT=true
CURSOR_ALLOW_PUSH=true
CURSOR_ALLOW_PR=true
CURSOR_ALLOW_MERGE=false
SELF_MODIFICATION_MODE=pr_only
```

## Endpoints

| Route | Méthode |
|-------|---------|
| `/api/cursor/status` | GET |
| `/api/cursor/jobs` | GET |
| `/api/cursor/jobs/{id}` | GET |
| `/api/cursor/jobs` | POST |
| `/api/cursor/jobs/{id}/cancel` | POST |
| `/api/cursor/jobs/{id}/rollback` | POST |
| `/api/cursor/jobs/{id}/retry` | POST |

## Limites connues

- Nécessite Cursor CLI installé et authentifié sur la machine hôte.
- Les tests post-job sont best-effort (commande configurable) ; un échec CLI marque le job `failed`.
- Rollback = suppression worktree / reset branche job — ne force pas de revert sur main.
