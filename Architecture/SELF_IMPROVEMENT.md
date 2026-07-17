# Auto-amélioration (Self-Improvement)

Dernière mise à jour : 2026-07-16

## Rôle

Proposer des améliorations **basées sur des preuves** mesurées en base (latences vocales, échecs Cursor, logs LLM, tours vocaux vides) — zéro invention. Les propositions peuvent être transformées en jobs Cursor (`voice_pipeline`, `self_improvement`, etc.).

## Fichiers clés

| Fichier | Rôle |
|---------|------|
| `scripts/self_improvement.py` | `collect_evidence`, propositions, enqueue optionnel |
| `scripts/scheduler.py` | job périodique `_self_improvement_job` |
| `data/.self_improvement_state.json` | état local des propositions |
| `api/router_cognitive.py` | `GET/POST /api/improvements/*` |
| `prompts/cursor/self_improvement.md` | template Cursor |

## Preuves collectées (`collect_evidence`)

Exemples implémentés :

1. **Latence vocale** — moyenne `voice_debug_log.latency_total_ms` > 4 s sur 7 jours → template `voice_pipeline`
2. **Échecs Cursor** — jobs `failed` récurrents → template `self_repair` / diagnostic
3. **Logs LLM** — patterns d’échec dans `llm_action_logs` (si table présente)
4. **Voix vide** — tours sans transcript utile

Chaque preuve inclut `type`, métriques, `impact`, `risk`, `template_id`.

## Flux scheduler

```
SELF_IMPROVEMENT_SCHEDULE (weekly par défaut)
  → collect_evidence()
  → stocke propositions dans STATE_PATH
  → si SELF_IMPROVEMENT_ENABLED && Cursor OK
       → enqueue low-risk (selon mode)
  → sinon : propositions visibles via API uniquement
```

## Config

```bash
SELF_IMPROVEMENT_ENABLED=true
SELF_IMPROVEMENT_SCHEDULE=weekly
SELF_MODIFICATION_MODE=pr_only
CURSOR_DELEGATION_ENABLED=true
```

## Endpoints

| Route | Méthode | Rôle |
|-------|---------|------|
| `/api/improvements/proposals` | GET | Liste des propositions / preuves |
| `/api/improvements/run` | POST | Force un cycle de collecte (+ enqueue si autorisé) |
| `/api/autonomy/settings` | GET | Flags autonomie effectifs |

## Limites connues

- Sans données dans `voice_debug_log` / `cursor_jobs`, la liste de preuves peut être vide (comportement voulu).
- Les propositions ne mutent pas le code sans passage Cursor + revue PR.
- Le fichier d’état JSON n’est pas une table SQLite versionnée — acceptable pour un solo-user local.
- Le job scheduler est best-effort : un échec de collecte est journalisé sans faire planter le processus principal.

## Voir aussi

- `Architecture/VOICE_PIPELINE.md` (métriques latence)
- `Architecture/CURSOR_DELEGATION.md`
- `Architecture/SELF_REPAIR.md`
