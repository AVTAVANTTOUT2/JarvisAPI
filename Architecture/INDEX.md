# Architecture — index canonique JARVIS API

**Revue :** 27 août 2026
**Référence code :** `origin/main` @ `6becf26cb3ea4ab47acb1996a2a9125500446ab7`

La source structurée des statuts et de la classification documentaire est
[`project_truth_registry.json`](./project_truth_registry.json). La vue humaine
[`28_VALIDATION_COHERENCE.md`](./28_VALIDATION_COHERENCE.md) et
`artifacts/architecture_truth.json` sont générés par
`tools/audit_architecture_truth.py` et contrôlés en CI.

Runtime SQLite canonique : **119 tables persistantes**, **124 tables physiques avec FTS5**, schéma généré : **126 déclarations de tables**.

Surface API canonique : **324 opérations**, **288 chemins**, **150 consommées et testées**, **68 consommées sans référence de test**, **53 non-frontend documentées et testées**, **53 non-frontend documentées sans référence de test**, **0 non attribuées**.

Structure API canonique : **322 opérations HTTP + 2 WebSockets**, **286 chemins OpenAPI**, **22 routeurs api/router_*.py + Fitness = 23 montés**, main.py **269 lignes**.

## Points d’entrée actuels

| Besoin | Document ou source |
|---|---|
| État démontré par domaine | [28 — état généré](./28_VALIDATION_COHERENCE.md) |
| Ordre, priorités et critères de sortie | [07 — roadmap active](./07_FEUILLE_DE_ROUTE.md) |
| Frontends, API et SQLite | [32 — source de vérité runtime](./32_FRONTEND_DATABASE_SOURCE_OF_TRUTH.md) |
| Definition of Done | [17 — règles actives](./17_DEFINITION_OF_DONE.md) |
| Dette technique | [23 — registre généré](./23_TECHNICAL_DEBT.md) |
| Audio et validations longues | [30 — stabilisation audio](./30_PLAN_STABILISATION_AUDIO.md) |
| Contrat public | [OpenAPI](./33_API_PUBLIQUE_OPENAPI.md) et [SDK](./34_SDK_DEVELOPPEURS.md) |
| Android livré | [README Android](../android/README.md) |
| Android volontairement futur | [FUTURE_FEATURES](../android/docs/FUTURE_FEATURES.md) |
| Décisions | [ADR individuels](./adr/) |
| Gouvernance des documents | [registre JSON](./project_truth_registry.json) |

## Classification obligatoire

Chaque fichier Markdown sous `Architecture/` doit apparaître exactement une
fois dans l’une des catégories du registre :

- `current` : règle, contrat ou état encore opposable ;
- `historical` : snapshot daté, jamais une description implicite du runtime ;
- `superseded` : ancienne vue avec remplacement explicite.

La CI échoue dès qu’un nouveau Markdown gouverné n’est pas classé. La liste
exhaustive est générée dans le document 28 ; elle n’est pas dupliquée ici.

## Règles de modification

1. Modifier le code ou la source structurée, jamais un artefact généré seul.
2. Ajouter ou mettre à jour les tests qui démontrent l’affirmation.
3. Classer tout nouveau document et ajouter un bandeau explicite aux archives
   ou vues superseded.
4. Régénérer vérité d’architecture, schéma et, si concernés, OpenAPI/SDK et
   registre de dette.
5. Vérifier liens Markdown, PII/secrets, chemins locaux et
   `git diff --check`.
6. Conserver séparément preuve automatisée, validation matérielle et campagne
   prolongée ; aucune ne remplace les autres.

Les audits de juillet/août et leurs rapports bruts restent consultables sous
[`audit/`](./audit/), avec leur statut historique. Les plans migratoires
achevés sont eux aussi des archives et ne doivent pas être relancés comme
roadmap.
