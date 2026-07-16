---
id: performance_audit
version: 2.0.0
date: 2026-07-16
domain: dev
variables: user_request,acceptance_criteria,required_tests,context_files,extra_context,repo_rules,result_format,date,template_version
---

# Audit de performance

# Objectif
{{user_request}}

# Contexte
Date: {{date}}
Template version: {{template_version}}
Dépôt : JARVIS — backend FastAPI async, SQLite locale, appels LLM DeepSeek (latence réseau dominante sur beaucoup de chemins), pipeline vocal sensible à la latence (traces dans la table `voice_debug_log`).

Contexte JARVIS :
{{extra_context}}

# Symptômes / preuve disponible
- Mesurer AVANT tout : établis une baseline chiffrée et reproductible (commande exacte, N répétitions, médiane + p95) du chemin incriminé. Sans baseline, pas d'optimisation.
- Profiler pour localiser : `cProfile`/`pstats` ou instrumentation `time.perf_counter()` ciblée pour identifier les points chauds réels. Pour le vocal, exploiter les latences déjà tracées dans `voice_debug_log` (export : `scripts/export_voice_debug.py`).
- Distinguer les natures de coût : CPU Python, requête SQLite (N+1, index manquant), appel LLM réseau, I/O subprocess (osascript). Chaque nature a un remède différent.

# Périmètre
- Optimiser UNIQUEMENT les points chauds prouvés par le profil — le hot path démontré, pas le code « qui a l'air lent ».
- Re-mesurer APRÈS chaque optimisation avec exactement le même protocole que la baseline ; ne conserver que ce qui améliore de façon mesurable.
- Documenter dans le rapport : baseline, profil, changement, mesure après, gain en % .

# Hors périmètre
- Micro-optimisations illisibles : ne pas sacrifier la lisibilité pour des microsecondes ; un gain < 5 % sur un chemin non critique ne justifie pas de complexité.
- Caches spéculatifs, pools, threads ajoutés sans mesure préalable démontrant le besoin.
- Changement de comportement fonctionnel ou de contrats sous couvert d'optimisation.

# Fichiers probables
{{context_files}}
- Chemins chauds connus du dépôt : pipeline message (`api/ws_messages.py`, `api/chat_context.py` — construction de contexte), pipeline vocal (`api/voice_processing.py`, `audio/`), helpers DB fréquents (`database/core.py`).
- Requêtes SQLite : chercher les boucles d'appels `get_db()` (pattern N+1) et les SELECT sans index.

# Règles d'architecture
{{repo_rules}}
- Les optimisations ne doivent pas contourner les façades existantes (ex. accès direct à `chat.db` interdit hors `integrations/apple_data.py`).

# Critères d'acceptation
{{acceptance_criteria}}
- Gain chiffré démontré entre baseline et mesure finale, même protocole, même machine.
- Aucune régression fonctionnelle : la suite de tests du domaine touché reste verte.

# Tests obligatoires
{{required_tests}}
- Les tests existants des modules optimisés passent inchangés (le comportement fonctionnel est un invariant).

# Validation réelle
- Tableau avant/après dans le rapport : médiane et p95 baseline vs final, N identique.
- Vérifier l'absence d'effet de bord mémoire (pas de cache non borné introduit).
- Sur le chemin vocal : confirmer via `voice_debug_log` que la latence de bout en bout s'améliore, pas seulement le segment optimisé.

# Stratégie Git
- Jamais de modification directe de main.
- Travailler uniquement dans le worktree / la branche fournie.
- Un commit par optimisation prouvée, message incluant le gain mesuré.

# Format du rapport final (OBLIGATOIRE)
{{result_format}}

## Qualité constante
- Chercher la cause racine de la lenteur (profil), jamais optimiser à l'intuition.
- Ne pas masquer le problème : un timeout élargi n'est pas une optimisation.
- Ne rien inventer : chaque chiffre du rapport provient d'une mesure réellement exécutée.
- Ne pas supprimer une fonction existante sans preuve.
- Respecter les conventions du dépôt ; tester avant chaque commit.
- Comparer avant/après avec le même protocole ; préserver les contrats existants.
- Rapport précis ; ne pas déclarer COMPLETED sans preuve (baseline + mesure finale).
