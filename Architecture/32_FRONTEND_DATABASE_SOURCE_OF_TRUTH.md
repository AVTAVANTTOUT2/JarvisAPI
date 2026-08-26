# 32 — Source de vérité : frontends, API et base SQLite

**Revue** : 26 août 2026
**Méthode** : inventaire statique du code et initialisation SQLite en mémoire
**Registre de statut** : `Architecture/project_truth_registry.json`

Runtime SQLite canonique : **119 tables persistantes**, **124 tables physiques avec FTS5**, schéma généré : **126 déclarations de tables**.

Surface API canonique : **318 opérations**, **283 chemins**, **144 consommées et testées**, **68 consommées sans référence de test**, **53 non-frontend documentées et testées**, **53 non-frontend documentées sans référence de test**, **0 non attribuées**.

Structure API canonique : **316 opérations HTTP + 2 WebSockets**, **281 chemins OpenAPI**, **22 routeurs api/router_*.py + Fitness = 23 montés**, main.py **269 lignes**.

Ces valeurs sont calculées par `tools/audit_architecture_truth.py`. Elles ne
doivent pas être recalculées ou recopiées manuellement depuis un rapport daté.
L'artefact détaillé est `artifacts/architecture_truth.json`.

## Frontends courants

- `frontend/` est l'unique bureau canonique : Next.js 15 exporte vers
  `frontend/out`, servi par FastAPI et le supervisor.
- Si `frontend/out` n'est pas utilisable, le bureau répond explicitement 503.
- `web/src` est la bibliothèque de vues compilée par le bureau ; ce n'est pas
  une application exécutable indépendante.
- `web_mobile/` est l'interface mobile autonome servie sous `/mobile/` sans
  étape de build.
- `tv/` est le dashboard dédié, exécuté comme processus séparé.

La résolution partagée est définie dans `core/frontend_resolution.py`. Les
contrats sont exercés par `tests/test_frontend_resolution.py` et
`tests/test_frontend_runtime_uniqueness.py`.

## Base SQLite courante

`database/core.py` initialise une base fraîche à partir de
`database/schema.py`, puis applique `database/migrations.py` et
`database/devagent.py`. `database/schema.sql` est un miroir déterministe généré
pour la revue et la CI ; il n'est pas exécuté par `init_db()`.

Les objets internes `sqlite_*` sont exclus des comptages. Les objets auxiliaires
créés automatiquement par FTS5 sont inclus uniquement dans le comptage physique.

## Surface API courante

L'audit parcourt statiquement `main.py`, `api/` et `app/`, puis relie chaque
route à ses consommateurs et aux références de test. Les opérations sans client
direct doivent être attribuées exactement une fois dans
`Architecture/api_route_ownership.json`; une règle orpheline ou masquant une
route cliente est une erreur.

Une référence de chemin dans un test ne prouve pas chaque comportement métier.
Elle établit seulement que le contrat de chemin est exercé ; les tests de domaine
restent responsables des verbes, permissions, erreurs et effets de bord.

## Régénération et contrôle

```bash
python tools/audit_architecture_truth.py \
  --output artifacts/architecture_truth.json \
  --schema-output database/schema.sql \
  --status-output Architecture/28_VALIDATION_COHERENCE.md

python tools/audit_architecture_truth.py \
  --check \
  --output artifacts/architecture_truth.json \
  --schema-output database/schema.sql \
  --status-output Architecture/28_VALIDATION_COHERENCE.md
```

Le mode `--check` valide aussi les statuts du registre, les preuves référencées,
la classification documentaire et toute assertion numérique SQLite/API placée
dans un document classé `current`.
