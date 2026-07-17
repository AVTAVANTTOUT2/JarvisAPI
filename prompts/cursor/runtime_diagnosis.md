---
id: runtime_diagnosis
version: 2.0.0
date: 2026-07-16
domain: dev
variables: user_request,acceptance_criteria,required_tests,context_files,extra_context,repo_rules,result_format,date,template_version
---

# Diagnostic runtime

# Objectif
{{user_request}}

# Contexte
Date: {{date}}
Template version: {{template_version}}
Dépôt : JARVIS — backend FastAPI lancé par `supervisor.py`, workers async démarrés dans le lifespan (`api/lifespan.py`), daemons (`scripts/jarvis_daemon.py`, `scripts/email_watcher.py`), SQLite `data/jarvis.db`.

Contexte JARVIS :
{{extra_context}}

# Symptômes / preuve disponible
- Commence par les logs : `data/.jarvis_restart/backend.log` (backend + workers). Extrais les lignes ERROR/Traceback horodatées autour du symptôme, cite-les dans le rapport.
- Reconstitue la chronologie : dernier démarrage, premier symptôme, actions utilisateur, jobs scheduler concernés.
- Tente une reproduction contrôlée : redéclenche le chemin fautif de la façon la plus isolée possible (TestClient, appel direct de la fonction, script minimal) plutôt qu'en relançant tout le serveur.
- Classe tes hypothèses par probabilité décroissante et instruis-les dans cet ordre ; note pour chacune la preuve qui la confirme ou l'élimine.

# Périmètre
- Un diagnostic précis : cause racine, chaîne causale, preuves.
- Un correctif UNIQUEMENT s'il est de faible risque et validé par un test. En cas de risque élevé (auth, données, pipeline vocal, daemons), livrer un rapport de diagnostic SANS correctif, avec le correctif proposé décrit dans le rapport.

# Hors périmètre
- Redémarrages ou nettoyages « pour voir » qui détruisent l'état à diagnostiquer.
- Correctif spéculatif non relié à une preuve.
- Suppression de logs ou de données ; modification de `.env`.

# Fichiers probables
{{context_files}}
- Boucles de fond : `api/lifespan.py`, `scripts/scheduler.py`, `scripts/jarvis_daemon.py`, `scripts/email_watcher.py`, `supervisor.py`.
- Erreurs DB : `database/__init__.py` (init/migrations) ; erreurs LLM : `llm.py` ; erreurs WS : `api/ws_handler.py`, `api/ws_messages.py`.

# Règles d'architecture
{{repo_rules}}
- Le diagnostic ne doit pas laisser d'instrumentation résiduelle (prints, logs debug) dans les commits finaux.

# Critères d'acceptation
{{acceptance_criteria}}
- La cause racine est démontrée par au moins une preuve reproductible (log cité, test, commande).
- Chaque hypothèse écartée l'est avec sa preuve d'élimination.

# Tests obligatoires
{{required_tests}}
- Si un correctif est appliqué : test de non-régression reproduisant les conditions du dysfonctionnement.

# Validation réelle
- Rejouer le scénario fautif après diagnostic : soit le symptôme est corrigé (si correctif appliqué), soit il est reproduit à la demande (diagnostic seul).
- Vérifier dans les logs que l'erreur ne réapparaît plus après correctif, sur une fenêtre d'exécution significative.
- Confirmer que les workers de fond redémarrent proprement si le lifespan a été touché.

# Stratégie Git
- Jamais de modification directe de main.
- Travailler uniquement dans le worktree / la branche fournie.
- Commits clairs ; le diagnostic pur (sans correctif) ne produit aucun commit de code.

# Format du rapport final (OBLIGATOIRE)
{{result_format}}

## Qualité constante
- Chercher la cause racine ; un redémarrage qui « répare » n'est pas un diagnostic.
- Ne pas masquer le problème ; ne pas réduire la verbosité d'un log pour faire disparaître l'erreur.
- Ne rien inventer : toute affirmation s'appuie sur un log, un test ou une lecture de code citée.
- Ne pas supprimer une fonction existante sans preuve.
- Respecter les conventions du dépôt ; tester avant chaque commit.
- Comparer l'état avant/après ; préserver les contrats existants.
- Rapport précis ; ne pas déclarer COMPLETED sans preuve.
