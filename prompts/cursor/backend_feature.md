---
id: backend_feature
version: 2.0.0
date: 2026-07-16
domain: backend
variables: user_request,acceptance_criteria,required_tests,context_files,extra_context,repo_rules,result_format,date,template_version
---

# Fonctionnalité backend

# Objectif
{{user_request}}

# Contexte
Date: {{date}}
Template version: {{template_version}}
Backend : FastAPI async Python 3.12. `main.py` est un point d'assemblage (~175 lignes) qui monte 12 routeurs `api/router_*.py` ; le cycle de vie est dans `api/lifespan.py`, la sécurité HTTP dans `api/middleware.py`. Persistance SQLite via les helpers `database/` et le context manager `get_db()`. Bus d'événements typés dans `jarvis/events.py`.

Contexte JARVIS :
{{extra_context}}

# Symptômes / preuve disponible
- Localise le routeur de domaine existant qui doit accueillir la route (les 12 `api/router_*.py`) ; n'en crée pas de 13e sans nécessité démontrée.
- Lis un helper voisin dans `database/` pour copier le pattern exact : fonction synchrone, `with get_db() as db:`, requêtes paramétrées, retour de dicts.
- Vérifie si un événement typé existe déjà dans `jarvis/events.py` pour la mutation envisagée avant d'en créer un.

# Périmètre
- Routes async dans le routeur `api/router_*.py` du domaine, en gardant le fichier SOUS 500 lignes (extraire un module d'aide dans `api/` si besoin).
- Accès DB exclusivement via des helpers dans `database/` utilisant `get_db()` — jamais de `sqlite3.connect()` inline dans une route.
- Mutations : émettre l'événement APRÈS commit via `event_bus` (`await event_bus.emit(...)` en async, `emit_nowait(...)` depuis un chemin synchrone) ; jamais d'émission avant persistance.
- Si des routes sont ajoutées ou modifiées : mettre à jour `tests/test_phase4_route_contract.py` (signatures + empreinte OpenAPI).

# Hors périmètre
- Import de `main.py` depuis un module `api/*` : INTERDIT (verrouillé par `tests/test_phase4_architecture.py`).
- Contournement du verrou de session de `api/middleware.py` : toute nouvelle route est protégée par défaut ; une exception d'allowlist doit être justifiée dans le rapport.
- Nouvelle dépendance dans `requirements.txt` sans justification.

# Fichiers probables
{{context_files}}
- Routeur du domaine : `api/router_<domaine>.py` ; helpers : `database/<domaine>.py` + réexport éventuel dans `database/__init__.py` ; événements : `jarvis/events.py` ; schéma : `database/schema.py` si nouvelle table (voir template database_migration pour les migrations).

# Règles d'architecture
{{repo_rules}}
- Type hints partout, logging par module (`logger = logging.getLogger(__name__)`), jamais de crash silencieux.

# Critères d'acceptation
{{acceptance_criteria}}
- Les routes répondent conformément au contrat annoncé (codes, corps JSON) via TestClient.
- `tests/test_phase4_route_contract.py` et `tests/test_phase4_architecture.py` passent après mise à jour.

# Tests obligatoires
{{required_tests}}
- Tests d'endpoint via TestClient (cas nominal + cas d'erreur) ; tests unitaires des helpers DB sur base temporaire (pattern conftest tmp_path).

# Validation réelle
- Appeler chaque nouvelle route via TestClient et inclure code + corps de réponse dans le rapport.
- Vérifier `wc -l` du routeur touché (< 500 lignes).
- Si un événement est émis : vérifier sa présence dans `event_log` après la mutation (ou via un handler de test), et son absence en cas de rollback.

# Stratégie Git
- Jamais de modification directe de main.
- Travailler uniquement dans le worktree / la branche fournie.
- Commits clairs : helpers DB, puis routes, puis tests de contrat.

# Format du rapport final (OBLIGATOIRE)
{{result_format}}

## Qualité constante
- Chercher la cause racine des échecs de tests de contrat (signature, OpenAPI) au lieu d'assouplir le test.
- Ne pas masquer le problème ; ne rien inventer (pas de table ni de colonne supposée — lire `database/schema.py`).
- Ne pas supprimer une fonction existante sans preuve (les helpers `database/` sont réexportés et consommés largement).
- Respecter les conventions du dépôt ; tester avant chaque commit.
- Comparer avant/après ; préserver les contrats existants (HTTP, WS, événements).
- Rapport précis ; ne pas déclarer COMPLETED sans preuve (sorties pytest + réponses TestClient).
