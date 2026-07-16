---
id: feature_implementation
version: 2.0.0
date: 2026-07-16
domain: dev
variables: user_request,acceptance_criteria,required_tests,context_files,extra_context,repo_rules,result_format,date,template_version
---

# Implémentation de fonctionnalité

# Objectif
{{user_request}}

# Contexte
Date: {{date}}
Template version: {{template_version}}
Dépôt : assistant personnel JARVIS — backend FastAPI Python 3.12, agents DeepSeek (`agents/`), SQLite, frontend Next.js 15 + fallback Vite, app Android. Les conventions vivent dans `CLAUDE.md` et `Architecture/`.

Contexte JARVIS :
{{extra_context}}

# Symptômes / preuve disponible
- Il ne s'agit pas d'un bug : la preuve attendue est une mini-spec, pas une reproduction.
- Avant d'écrire du code, rédige une mini-spec (5-10 lignes) : entrées, sorties, cas limites, points d'intégration. Inclus-la dans le rapport final.
- Vérifie qu'une fonctionnalité équivalente n'existe pas déjà (recherche dans `api/`, `agents/`, `scripts/`, `integrations/`) — étendre vaut mieux que dupliquer.

# Périmètre
- Exactement ce que demande l'objectif, découpé en incréments compilables/testables : d'abord le cœur métier, puis l'exposition (route, WS, UI), puis les tests.
- Réutiliser les patterns existants du dépôt : `BaseAgent` pour un agent, `APIRouter` par domaine pour une route, helpers `database/` avec `get_db()` pour la persistance.
- Documentation courte si un contrat public est ajouté (route HTTP, message WS, table) : docstring + mention dans le doc d'architecture concerné.

# Hors périmètre
- Over-engineering : pas d'abstraction pour un seul cas d'usage, pas de feature flag non demandé, pas de configuration spéculative.
- Refactoring des modules voisins non requis par la fonctionnalité.
- Changement de dépendances (`requirements.txt`, `package.json`) sans nécessité démontrée.

# Fichiers probables
{{context_files}}
- Nouvelle route → `api/router_<domaine>.py` (rester sous 500 lignes) ; nouveau helper DB → module `database/` du domaine ; nouvel agent → `agents/` + prompt `prompts/<agent>.txt`.
- Cherche le module existant le plus proche et imite sa structure.

# Règles d'architecture
{{repo_rules}}
- `main.py` reste un point d'assemblage : aucun module `api/*.py` ne l'importe.
- Événements de domaine émis après commit via `event_bus`.

# Critères d'acceptation
{{acceptance_criteria}}
- La mini-spec est respectée point par point, cas limites compris.
- Le code suit les patterns du dépôt (pas de style étranger introduit).

# Tests obligatoires
{{required_tests}}
- Chaque comportement de la mini-spec a un test ; les cas limites identifiés sont testés.

# Validation réelle
- Exécuter la fonctionnalité de bout en bout au moins une fois (appel réel de la route via TestClient, exécution du script, rendu du composant) — pas seulement les tests unitaires.
- Si une route est ajoutée : mettre à jour et exécuter `tests/test_phase4_route_contract.py`.
- Vérifier que les fonctionnalités adjacentes (mêmes fichiers touchés) fonctionnent toujours.

# Stratégie Git
- Jamais de modification directe de main.
- Travailler uniquement dans le worktree / la branche fournie.
- Commits clairs, un par incrément fonctionnel (cœur, exposition, tests).

# Format du rapport final (OBLIGATOIRE)
{{result_format}}

## Qualité constante
- Chercher la cause racine des difficultés rencontrées, pas de contournement masquant.
- Ne rien inventer : ne pas affirmer qu'un pattern existe sans l'avoir lu dans le dépôt.
- Ne pas supprimer une fonction existante sans preuve qu'elle est inutilisée.
- Respecter les conventions du dépôt ; tester avant chaque commit.
- Comparer le comportement des modules touchés avant/après.
- Préserver les contrats existants ; rapport précis.
- Ne pas déclarer COMPLETED sans preuve (tests verts + exécution de bout en bout).
