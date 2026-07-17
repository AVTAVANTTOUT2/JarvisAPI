---
id: bug_fix
version: 2.0.0
date: 2026-07-16
domain: dev
variables: user_request,acceptance_criteria,required_tests,context_files,extra_context,repo_rules,result_format,date,template_version
---

# Correction de bug avec cause racine

# Objectif
{{user_request}}

# Contexte
Date: {{date}}
Template version: {{template_version}}
Dépôt : assistant personnel JARVIS — backend FastAPI Python 3.12 (`api/`, `agents/`, `database/`), frontend Next.js 15 (`frontend/`) + fallback Vite (`web/`), app Android (`android/`), SQLite (`data/jarvis.db`).

Contexte JARVIS :
{{extra_context}}

# Symptômes / preuve disponible
- Reproduis le bug AVANT toute correction : écris d'abord un test qui échoue (test rouge) ou une commande qui montre le comportement fautif. Si tu ne peux pas le reproduire, dis-le dans le rapport — ne corrige pas à l'aveugle.
- Note la trace exacte : exception, ligne, valeurs d'entrée. Les logs runtime vivent dans `data/.jarvis_restart/backend.log`.
- Fais un bisect mental des causes : liste les 2-3 causes candidates, élimine-les une par une avec des preuves (lecture de code, print/log ciblé, test unitaire), pas avec des intuitions.
- Distingue le symptôme (ce que voit l'utilisateur) de la cause racine (le code fautif). Le correctif vise la cause racine, jamais le symptôme.

# Périmètre
- Le correctif minimal qui élimine la cause racine identifiée.
- Un test de non-régression qui échoue sans le correctif et passe avec.
- La mise à jour des tests existants uniquement s'ils encodaient le comportement bogué.

# Hors périmètre
- Refactoring opportuniste des fichiers traversés.
- « Correctifs » défensifs (try/except large, valeur par défaut) qui masquent le problème au lieu de le résoudre.
- Modification de contrats API, de schéma DB ou de messages WebSocket, sauf si le bug y réside.
- Lecture ou inclusion de secrets (`.env`, `credentials/`).

# Fichiers probables
{{context_files}}
- Remonte la pile d'appel depuis le point de crash ; le fichier qui lève n'est pas forcément le fautif.
- Les helpers DB sont dans `database/`, les routeurs dans `api/router_*.py`, le pipeline message dans `api/ws_messages.py`.

# Règles d'architecture
{{repo_rules}}
- Conventions : async partout côté FastAPI, type hints, pas de crash silencieux (log + message d'erreur).

# Critères d'acceptation
{{acceptance_criteria}}
- Le test rouge écrit en phase de reproduction passe désormais.
- Aucun autre test ne régresse.

# Tests obligatoires
{{required_tests}}
- Ajouter au minimum un test de non-régression nommé d'après le comportement corrigé (`test_<comportement>`).

# Validation réelle
- Rejouer la reproduction initiale (même commande, mêmes entrées) et constater la disparition du symptôme.
- Comparer la sortie avant/après sur le cas nominal ET sur le cas limite qui déclenchait le bug.
- Lancer la suite pytest complète du domaine touché, pas seulement le nouveau test.
- Si le bug touchait une route HTTP : vérifier `tests/test_phase4_route_contract.py`.

# Stratégie Git
- Jamais de modification directe de main.
- Travailler uniquement dans le worktree / la branche fournie.
- Commits clairs : un commit pour le test de reproduction si pertinent, un pour le correctif.

# Format du rapport final (OBLIGATOIRE)
{{result_format}}

## Qualité constante
- Chercher la cause racine, ne jamais patcher le symptôme.
- Ne pas masquer le problème (pas de except silencieux, pas de test désactivé).
- Ne rien inventer : chaque affirmation du rapport s'appuie sur une preuve observable.
- Ne pas supprimer une fonction existante sans preuve qu'elle est morte.
- Respecter les conventions du dépôt ; tester avant chaque commit.
- Comparer avant/après ; préserver les contrats existants (API, WS, DB).
- Rapport précis ; ne pas déclarer COMPLETED sans preuve (test rouge devenu vert + suite passante).
