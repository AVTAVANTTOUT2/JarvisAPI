---
id: self_improvement
version: 2.0.0
date: 2026-07-16
domain: autonomy
variables: user_request,acceptance_criteria,required_tests,context_files,extra_context,repo_rules,result_format,date,template_version
---

# Auto-amélioration JARVIS

# Objectif
{{user_request}}

# Contexte
Date: {{date}}
Template version: {{template_version}}
Job d'auto-amélioration : JARVIS propose une amélioration de SON PROPRE fonctionnement à partir de données mesurées sur lui-même (latences `voice_debug_log`, échecs répétés d'actions, coûts LLM, patterns en base). Mode PR only : la proposition part en branche + PR, jamais de merge automatique.

Contexte JARVIS :
{{extra_context}}

# Symptômes / preuve disponible
- Le point de départ est une PREUVE MESURÉE fournie dans le contexte ci-dessus (extra_context) : latence chiffrée, taux d'échec, requêtes répétées en base. Pas de preuve chiffrée = pas d'amélioration ; le job s'arrête et le rapport explique ce qui manque.
- Requalifie la preuve toi-même : re-exécute la mesure (requête SQL sur `voice_debug_log`, comptage d'échecs, relevé de coût) pour confirmer que le problème existe toujours au commit courant, et fige cette valeur comme baseline.
- Cherche la cause du chiffre avant de proposer : une latence élevée peut venir du réseau LLM, pas du code — l'amélioration doit viser la composante réellement responsable.

# Périmètre
- LE PLUS PETIT changement à impact mesurable : une seule amélioration ciblée par job, pas un lot.
- Le rapport DOIT contenir la métrique avant (mesurée) et la métrique après attendue (estimée puis, si mesurable dans le job, re-mesurée) : nom de la métrique, valeur baseline, valeur cible, comment la re-mesurer après merge.
- Tests couvrant le comportement modifié.

# Hors périmètre
- Améliorations non mesurables (« code plus propre », « plus robuste ») sans métrique associée : rejetées.
- Changements touchant l'auth, les secrets, la suppression de données, les mécanismes de démarrage — mêmes interdits que l'auto-réparation.
- Grosses refontes : si la preuve pointe vers un problème structurel, le rapport le documente et recommande un job humain dédié.

# Fichiers probables
{{context_files}}
- Selon la métrique : pipeline vocal (`api/voice_processing.py`, `api/voice_cognitive.py`) pour les latences ; `scripts/self_improvement.py` (collecte de preuves existante) ; `llm.py` (coûts/retries) ; helpers `database/` (requêtes répétées) ; `scripts/scheduler.py` (jobs récurrents inefficaces).

# Règles d'architecture
{{repo_rules}}
- L'amélioration ne doit dégrader aucune autre métrique connue (latence contre coût, coût contre qualité) sans le documenter explicitement.

# Critères d'acceptation
{{acceptance_criteria}}
- Baseline re-mesurée et consignée avant changement.
- Changement unique, minimal, avec métrique cible explicite et procédure de re-mesure.

# Tests obligatoires
{{required_tests}}
- Tests du comportement modifié ; la suite du module touché reste verte.
- Si la métrique est mesurable hors production (benchmark local, requête sur données de test) : inclure la mesure après dans le job.

# Validation réelle
- Tableau métrique avant / métrique après (ou après attendue + procédure de vérification post-merge) dans le rapport.
- Vérifier l'absence de régression fonctionnelle sur le chemin modifié (exécution de bout en bout).
- Confirmer par diff que le changement reste dans le périmètre autorisé.

# Stratégie Git
- Jamais de modification directe de main — PR only strict.
- Travailler uniquement dans le worktree / la branche fournie ; ouvrir une PR, ne jamais merger.
- Un commit unique et clair décrivant l'amélioration et sa métrique.

# Format du rapport final (OBLIGATOIRE)
{{result_format}}

## Qualité constante
- Chercher la cause racine du chiffre mesuré avant d'optimiser quoi que ce soit.
- Ne pas masquer le problème ; ne rien inventer : jamais de métrique estimée présentée comme mesurée.
- Ne pas supprimer une fonction existante sans preuve.
- Respecter les conventions du dépôt ; tester avant chaque commit.
- Comparer avant/après avec la même mesure ; préserver les contrats existants.
- Rapport précis ; ne pas déclarer COMPLETED sans preuve (baseline + changement + tests verts + PR ouverte).
