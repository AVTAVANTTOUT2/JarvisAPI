---
id: bug_fix
version: 1.0.0
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
Fichiers probables:
{{context_files}}

Contexte JARVIS:
{{extra_context}}

# Symptômes / preuve disponible
Utilise uniquement des preuves observables dans le dépôt. Ne rien inventer.

# Périmètre
- Implémenter uniquement ce qui est nécessaire pour satisfaire l'objectif
- Respecter les conventions du dépôt (CLAUDE.md, structure api/, agents/, tests/)

# Hors périmètre
- Ne pas refactorer massivement sans nécessité
- Ne pas modifier main directement
- Ne pas lire ni inclure de secrets (.env, credentials)
- Ne pas supprimer de fonctionnalités sans preuve

# Règles d'architecture
{{repo_rules}}

# Critères d'acceptation
{{acceptance_criteria}}

# Tests obligatoires
{{required_tests}}

# Validation réelle
- Reproduire le problème avant correction quand applicable
- Comparer avant/après
- Vérifier les contrats API / OpenAPI si touchés

# Stratégie Git
- Travailler uniquement dans le worktree / branche fournie
- Committer des messages clairs
- Ouvrir une PR si autorisé — jamais merger sur main

# Format du rapport final (OBLIGATOIRE)
{{result_format}}

## Qualité constante
- Chercher la cause racine
- Ne pas masquer le problème
- Ne pas déclarer COMPLETED sans preuve (tests + validation)
- Préserver les contrats existants

