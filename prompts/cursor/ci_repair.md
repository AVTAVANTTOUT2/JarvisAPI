---
id: ci_repair
version: 2.0.0
date: 2026-07-16
domain: dev
variables: user_request,acceptance_criteria,required_tests,context_files,extra_context,repo_rules,result_format,date,template_version
---

# Réparation de CI

# Objectif
{{user_request}}

# Contexte
Date: {{date}}
Template version: {{template_version}}
Dépôt : JARVIS — la CI GitHub Actions est définie dans `.github/workflows/ci.yml` (backend pytest + jobs frontend). La CI tourne sous Linux : les intégrations macOS (osascript, chat.db) y sont absentes par construction.

Contexte JARVIS :
{{extra_context}}

# Symptômes / preuve disponible
- Lis le job en échec : identifie le step exact, la commande exécutée et le message d'erreur complet (pas seulement la dernière ligne).
- Reproduis LOCALEMENT la commande exacte de la CI, telle qu'écrite dans `.github/workflows/ci.yml` (mêmes options, même répertoire), avant de modifier quoi que ce soit.
- Distingue trois cas et prouve lequel s'applique : (1) test cassé par un changement de code, (2) test flaky (échec non déterministe — relance-le plusieurs fois pour le démontrer), (3) environnement CI divergent (dépendance manquante, différence Linux/macOS).
- Compare avec le dernier run vert : quel commit a introduit l'échec ?

# Périmètre
- Le correctif minimal qui rend le job vert pour la bonne raison : corriger le code si le code est fautif, corriger le test si le test est fautif, corriger le workflow si l'environnement est fautif.
- Pour un flaky prouvé : corriger la source du non-déterminisme (ordre des tests, temps, état partagé), pas le symptôme.

# Hors périmètre
- INTERDIT : désactiver, skiper ou marquer xfail un test pour « faire passer » la CI sans justification prouvée et documentée dans le rapport.
- Élargir les timeouts ou ajouter des retries sans avoir démontré que c'est la cause.
- Refactorer le workflow au-delà du correctif nécessaire.

# Fichiers probables
{{context_files}}
- `.github/workflows/ci.yml` (définition des jobs), `tests/conftest.py` (fixtures partagées, isolation DB), `requirements.txt` / `requirements-dev.txt` (dépendances CI).
- Si le job frontend échoue : `frontend/package.json`, `web/package.json` et leurs lockfiles.

# Règles d'architecture
{{repo_rules}}
- Les tests doivent passer dans n'importe quel ordre et sans état partagé (voir isolation dans `tests/conftest.py`).

# Critères d'acceptation
{{acceptance_criteria}}
- La commande exacte de la CI passe localement après correctif.
- Le diagnostic (cassé / flaky / environnement) est explicite et prouvé dans le rapport.

# Tests obligatoires
{{required_tests}}
- Exécuter la commande CI reproduite avant ET après le correctif ; inclure les deux sorties dans le rapport.

# Validation réelle
- Relancer la suite complète concernée en local, pas seulement le test réparé.
- Pour un flaky corrigé : relancer le test en boucle (au moins 5 exécutions) et constater la stabilité.
- Vérifier qu'aucun autre job du workflow n'est cassé par le correctif (lint, build frontend).

# Stratégie Git
- Jamais de modification directe de main.
- Travailler uniquement dans le worktree / la branche fournie.
- Commits clairs, mentionnant le job et le step réparés.

# Format du rapport final (OBLIGATOIRE)
{{result_format}}

## Qualité constante
- Chercher la cause racine de l'échec CI, jamais neutraliser le signal.
- Ne pas masquer le problème : un test désactivé est un problème caché, pas résolu.
- Ne rien inventer : citer les sorties de commandes réelles.
- Ne pas supprimer un test existant sans preuve qu'il est invalide.
- Respecter les conventions du dépôt ; tester avant chaque commit.
- Comparer avant/après (sortie CI locale) ; préserver les contrats existants.
- Rapport précis ; ne pas déclarer COMPLETED sans preuve (commande CI verte en local).
