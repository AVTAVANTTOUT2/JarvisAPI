# Auto-réparation (Self-Repair)

Dernière mise à jour : 2026-07-16

## Rôle

Quand le self-healing détecte une boucle de crash ou une régression récurrente, et que la réparation locale report-only ne suffit pas, déléguer un correctif à Cursor via le template `self_repair` — **toujours en PR**, jamais de merge auto sur main.

## Fichiers clés

| Fichier | Rôle |
|---------|------|
| `scripts/self_healing.py` | Diagnostic + branche Cursor si flags OK |
| `prompts/cursor/self_repair.md` | Cahier des charges réparation |
| `integrations/cursor_delegation.py` | enqueue job |
| `config.py` | `SELF_REPAIR_*`, `SELF_HEALING_*`, capacités Cursor |

## Flux

```
crash / régression détectée (seuil SELF_HEALING_CRASH_THRESHOLD)
  → diagnostic self_healing (report-only par défaut)
  → si SELF_REPAIR_ENABLED && CURSOR_DELEGATION_ENABLED
       → cursor_delegation.enqueue(template_id="self_repair", ...)
       → job isolé + PR obligatoire ; échec explicite sinon
  → sinon : log + notification, pas de mutation code
```

Le module de self-healing ne contient plus de chemin d'application locale. Les projets DevAgent disposent de leurs propres worktrees et garde-fous ; ils ne passent pas par ce module.

## Config

```bash
SELF_HEALING_ENABLED=false          # diagnostic seul par défaut
SELF_HEALING_CRASH_THRESHOLD=3
SELF_REPAIR_ENABLED=true
CURSOR_DELEGATION_ENABLED=true
```

## Garde-fous

- Pas d'écriture, commit ou rollback dans le checkout actif.
- Pas de travail sur `main` : délégation Cursor uniquement.
- `CURSOR_ALLOW_COMMIT`, `CURSOR_ALLOW_PUSH` et `CURSOR_ALLOW_PR` doivent tous être actifs ; le job est refusé sinon.
- Un retour Cursor sans push et URL de PR finit en `failed`, jamais en `completed`.
- Secrets jamais injectés bruts dans le prompt (redaction délégation).

## Endpoints liés

- Historique self-healing : `GET /api/self-healing/status`, `POST /api/self-healing/diagnose`
- Jobs Cursor : `GET /api/cursor/jobs`
- Autonomie : `GET /api/autonomy/settings`

## Limites connues

- Self-repair Cursor n’est déclenché que si le CLI est disponible et authentifié.
- Le diagnostic seul reste le mode sûr quand `SELF_REPAIR_ENABLED=false` ou Cursor indisponible.
- Aucune garantie qu’un job Cursor « succeeded » corrige la cause racine — validation humaine via PR.
- Les projets DevAgent isolés utilisent leur propre pipeline ; le dépôt JARVIS reste `pr_only` sans option de repli.

## Voir aussi

- `Architecture/CURSOR_DELEGATION.md`
- `Architecture/SELF_IMPROVEMENT.md`
- `Architecture/LLM_POLICY.md`
