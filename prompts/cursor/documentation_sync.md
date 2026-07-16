---
id: documentation_sync
version: 2.0.0
date: 2026-07-16
domain: docs
variables: user_request,acceptance_criteria,required_tests,context_files,extra_context,repo_rules,result_format,date,template_version
---

# Synchronisation de documentation

# Objectif
{{user_request}}

# Contexte
Date: {{date}}
Template version: {{template_version}}
Hiérarchie documentaire JARVIS : `Architecture/*.md` = source de vérité technique (43 documents numérotés + thématiques, index dans `Architecture/INDEX.md`) ; `CLAUDE.md` = manuel opérationnel de l'agent (conventions, architecture, contrats) ; `README.md` = onboarding humain. En cas de divergence entre doc et code, LE CODE FAIT FOI et la doc s'aligne.

Contexte JARVIS :
{{extra_context}}

# Symptômes / preuve disponible
- Avant d'écrire un chiffre, VÉRIFIE-le dans le code réel : nombre de tables (compter les CREATE TABLE de `database/schema.py` + migrations, ou exécuter `init_db()` sur base vierge et compter), nombre de routeurs (`ls api/router_*.py`), nombre d'endpoints (générer l'OpenAPI et compter les chemins), nombre de tests (sortie `pytest --collect-only -q`).
- Chaque affirmation corrigée dans la doc doit citer sa source de vérification (commande exécutée ou fichier lu) dans le rapport.
- Repère les sections périmées en croisant la doc avec l'état du dépôt (fonctionnalités retirées, fichiers déplacés, variables d'env renommées dans `config.py` / `.env.example`).

# Périmètre
- Aligner la documentation sur le code existant : chiffres, chemins de fichiers, noms de tables/routes/variables, commandes.
- Respecter le rôle de chaque document : détail technique dans `Architecture/`, contrat opérationnel dans `CLAUDE.md`, prise en main dans `README.md` — ne pas dupliquer le même contenu aux trois endroits.
- Mettre à jour `Architecture/INDEX.md` si des documents sont ajoutés ou renommés.

# Hors périmètre
- PAS DE DOC SPÉCULATIVE : ne jamais documenter une fonctionnalité prévue, souhaitée ou à moitié implémentée comme si elle existait.
- Modifier du code pour le faire correspondre à la doc (c'est l'inverse : la doc suit le code) — une divergence qui révèle un bug se signale dans le rapport.
- Réécriture stylistique massive de sections déjà exactes.

# Fichiers probables
{{context_files}}
- `Architecture/*.md` (dont `INDEX.md`, `32_FRONTEND_DATABASE_SOURCE_OF_TRUTH.md` pour les comptages de tables), `CLAUDE.md`, `README.md`, `android/README.md` ; sources de vérification : `database/schema.py`, `api/router_*.py`, `config.py`, `.env.example`.

# Règles d'architecture
{{repo_rules}}
- La doc est en français, sans emoji, cohérente avec la terminologie existante du dépôt.

# Critères d'acceptation
{{acceptance_criteria}}
- Zéro chiffre écrit sans vérification exécutée ; le rapport liste chaque chiffre corrigé avec sa preuve.
- Aucune fonctionnalité documentée qui n'existe pas dans le code au commit courant.

# Tests obligatoires
{{required_tests}}
- Exécuter les commandes de vérification des chiffres (comptages) et coller leurs sorties dans le rapport.
- Si le dépôt a des tests de cohérence doc/code (ex. comptage de tables dans `tests/test_audit_architecture_truth.py`), les lancer.

# Validation réelle
- Relire chaque section modifiée en la confrontant ligne par ligne au code cité.
- Vérifier que les commandes documentées (setup, build, scripts) s'exécutent réellement telles qu'écrites, ou les corriger.
- Vérifier les liens et chemins internes mentionnés (fichiers existants, ancres valides).

# Stratégie Git
- Jamais de modification directe de main.
- Travailler uniquement dans le worktree / la branche fournie.
- Commits clairs, un par document ou par thème de synchronisation.

# Format du rapport final (OBLIGATOIRE)
{{result_format}}

## Qualité constante
- Chercher la cause racine des divergences doc/code (process ou oubli) et la mentionner.
- Ne pas masquer le problème : une incohérence non résolue se signale, ne se contourne pas par du flou.
- Ne rien inventer — c'est LA règle centrale de ce template : tout chiffre, chemin ou comportement écrit a été vérifié.
- Ne pas supprimer une section existante sans preuve qu'elle est obsolète.
- Respecter les conventions du dépôt ; vérifier avant chaque commit.
- Comparer avant/après (diff de doc lisible) ; préserver les contrats existants.
- Rapport précis ; ne pas déclarer COMPLETED sans preuve (sorties des commandes de vérification).
