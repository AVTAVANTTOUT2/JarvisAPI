---
id: self_repair
version: 2.0.0
date: 2026-07-16
domain: autonomy
variables: user_request,acceptance_criteria,required_tests,context_files,extra_context,repo_rules,result_format,date,template_version
---

# Auto-réparation JARVIS

# Objectif
{{user_request}}

# Contexte
Date: {{date}}
Template version: {{template_version}}
Job d'auto-réparation : JARVIS répare SON PROPRE code après détection d'un crash ou d'une boucle d'échec. Mode `pr_only` OBLIGATOIRE (`SELF_MODIFICATION_MODE`) : le correctif part en branche + PR, il n'est JAMAIS mergé ni appliqué automatiquement sur l'instance qui tourne. L'utilisateur relit et merge.

Contexte JARVIS :
{{extra_context}}

# Symptômes / preuve disponible
- Le tail de log du crash est fourni dans le contexte ci-dessus (extra_context) : c'est la preuve primaire. Extrais la traceback complète, l'horodatage, la fréquence de récurrence.
- Reproduis le crash en isolation (test unitaire ou script minimal rejouant les conditions) AVANT tout correctif ; si la reproduction est impossible, le rapport le dit et propose une instrumentation au lieu d'un correctif aveugle.
- Vérifie si ce crash est déjà connu : historique des jobs de self-healing, notifications récentes, commits récents touchant la zone (un correctif précédent qui a régressé est une information capitale).

# Périmètre
- Le correctif MINIMAL qui élimine la cause du crash : pas de refactoring, pas d'amélioration opportuniste, le plus petit diff possible.
- Un test de non-régression reproduisant les conditions du crash.
- Une évaluation honnête du risque résiduel dans le rapport : ce que le correctif ne couvre pas, ce qui pourrait recasser, les zones adjacentes fragiles — cette notification de risque est OBLIGATOIRE.

# Hors périmètre
- INTERDIT ABSOLU, sans exception : suppression de données (`data/`, tables SQLite), modification de l'authentification (`auth.py`, sessions, middleware de sécurité), rotation ou modification de secrets (`.env`, `credentials/`), création/modification de LaunchAgents ou de tout mécanisme de démarrage système.
- Merge ou déploiement du correctif : le job s'arrête à la PR (pr_only).
- Modification du superviseur (`supervisor.py`) ou du mécanisme de self-healing lui-même, sauf si c'est précisément lui qui crashe (à signaler comme haut risque).

# Fichiers probables
{{context_files}}
- La traceback du log désigne le module fautif ; zones fréquentes : workers du lifespan (`api/lifespan.py`), daemons (`scripts/jarvis_daemon.py`, `scripts/email_watcher.py`, `scripts/scheduler.py`), intégrations macOS (timeouts osascript), DB (`database/`).
- Logs : `data/.jarvis_restart/backend.log` ; historique self-healing : `scripts/self_healing.py` et sa table de jobs.

# Règles d'architecture
{{repo_rules}}
- Le correctif ne doit jamais réduire la visibilité des erreurs (pas de except élargi, pas de log supprimé).

# Critères d'acceptation
{{acceptance_criteria}}
- Le crash reproduit ne se reproduit plus avec le correctif ; la reproduction et sa disparition sont documentées.
- Le diff est minimal et n'a touché aucune zone interdite.

# Tests obligatoires
{{required_tests}}
- Test de non-régression rejouant les conditions du crash (rouge sans correctif, vert avec).
- Suite complète du module touché, puis suite globale rapide.

# Validation réelle
- Rejouer le scénario de crash (mêmes entrées/conditions que la traceback) et constater l'absence de crash.
- Vérifier que le service concerné redémarre et tient (pas de nouvelle boucle de crash dans le log sur la fenêtre d'observation possible).
- Confirmer par diff que le changement ne touche ni auth, ni secrets, ni données, ni démarrage système.

# Stratégie Git
- Jamais de modification directe de main — mode pr_only strict.
- Travailler uniquement dans le worktree / la branche fournie ; ouvrir une PR, ne jamais merger.
- Commits clairs mentionnant le crash corrigé (référence au log).

# Format du rapport final (OBLIGATOIRE)
{{result_format}}

## Qualité constante
- Chercher la cause racine du crash, jamais supprimer le symptôme (un except qui avale l'erreur = échec du job).
- Ne pas masquer le problème ; ne rien inventer : la chaîne causale s'appuie sur la traceback fournie.
- Ne pas supprimer une fonction existante sans preuve.
- Respecter les conventions du dépôt ; tester avant chaque commit.
- Comparer avant/après (reproduction du crash) ; préserver les contrats existants.
- Rapport précis incluant le risque résiduel ; ne pas déclarer COMPLETED sans preuve (reproduction éliminée + tests verts + PR ouverte).
