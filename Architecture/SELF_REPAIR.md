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
| `config.py` | `SELF_REPAIR_*`, `SELF_HEALING_*`, `SELF_MODIFICATION_MODE` |

## Flux

```
crash / régression détectée (seuil SELF_HEALING_CRASH_THRESHOLD)
  → diagnostic self_healing (report-only par défaut)
  → si SELF_REPAIR_ENABLED && CURSOR_DELEGATION_ENABLED
       → cursor_delegation.enqueue(template_id="self_repair", ...)
       → job isolé + PR éventuelle
  → sinon : log + notification, pas de mutation code
```

`SELF_HEALING_AUTO_APPLY` reste le flag historique pour patches locaux hors dépôt JARVIS / projets DevAgent. Pour le code JARVIS lui-même, la voie canonique est Cursor + `pr_only`.

## Config

```bash
SELF_HEALING_ENABLED=false          # diagnostic seul par défaut
SELF_HEALING_AUTO_APPLY=false
SELF_HEALING_CRASH_THRESHOLD=3
SELF_HEALING_REGRESSION_WINDOW_MIN=15
SELF_HEALING_COOLDOWN_MIN=60
SELF_REPAIR_ENABLED=true
CURSOR_DELEGATION_ENABLED=true
SELF_MODIFICATION_MODE=pr_only
```

## Garde-fous

- Cooldown entre tentatives (`SELF_HEALING_COOLDOWN_MIN`).
- Fenêtre de régression : si le même crash revient après patch, rollback / alerte.
- Pas de travail sur `main` (délégation Cursor).
- Secrets jamais injectés bruts dans le prompt (redaction délégation).

## Endpoints liés

- Historique self-healing : `GET /api/self-healing/status`, `POST /api/self-healing/diagnose`
- Jobs Cursor : `GET /api/cursor/jobs`
- Autonomie : `GET /api/autonomy/settings`

## Limites connues

- Self-repair Cursor n’est déclenché que si le CLI est disponible et authentifié.
- Le diagnostic sans Cursor reste le mode sûr quand `SELF_HEALING_ENABLED=false`.
- Aucune garantie qu’un job Cursor « succeeded » corrige la cause racine — validation humaine via PR.
- Les projets DevAgent isolés peuvent encore utiliser des chemins historiques de staging ; le dépôt JARVIS lui-même reste `pr_only`.

## Voir aussi

- `Architecture/CURSOR_DELEGATION.md`
- `Architecture/SELF_IMPROVEMENT.md`
- `Architecture/LLM_POLICY.md`
