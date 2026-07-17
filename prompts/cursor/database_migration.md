---
id: database_migration
version: 2.0.0
date: 2026-07-16
domain: database
variables: user_request,acceptance_criteria,required_tests,context_files,extra_context,repo_rules,result_format,date,template_version
---

# Migration de base de données

# Objectif
{{user_request}}

# Contexte
Date: {{date}}
Template version: {{template_version}}
Base : SQLite unique `data/jarvis.db`, initialisée par `database.init_db()`. Le schéma canonique vit dans `database/schema.py` ; les migrations idempotentes exécutées au démarrage vivent dans `database/migrations.py` ; l'outillage complémentaire dans `scripts/db_migrations.py`. Le comptage des tables est verrouillé par `tests/test_audit_architecture_truth.py`.

Contexte JARVIS :
{{extra_context}}

# Symptômes / preuve disponible
- Photographie l'état AVANT : liste des tables et colonnes concernées sur une base initialisée (`init_db()` sur base vierge + `PRAGMA table_info`), pour comparer après migration.
- Lis 2-3 fonctions `_migrate_*` existantes dans `database/migrations.py` et copie leur pattern exactement.
- Vérifie si la table/colonne existe déjà sous un autre nom avant d'en créer une (grep dans `database/schema.py`).

# Périmètre
- Migrations IDEMPOTENTES uniquement : `CREATE TABLE IF NOT EXISTS`, `CREATE INDEX IF NOT EXISTS`, et `ALTER TABLE ADD COLUMN` gardé par une inspection préalable de `PRAGMA table_info` (SQLite ne supporte pas ADD COLUMN IF NOT EXISTS). Une migration doit pouvoir tourner deux fois sans erreur.
- Mettre à jour EN MÊME TEMPS : `database/schema.py` (source de vérité du schéma neuf), `database/migrations.py` (chemin d'upgrade des bases existantes), ET le test de comptage `tests/test_audit_architecture_truth.py` si le nombre de tables change.
- Helpers CRUD associés dans le module `database/` du domaine, requêtes paramétrées.

# Hors périmètre
- JAMAIS de `DROP TABLE` ni `DROP COLUMN` sans sauvegarde préalable explicite ET justification dans le rapport ; la suppression de données est par défaut interdite.
- Renommage de table/colonne consommée par du code existant sans migration des consommateurs dans le même changement.
- Modification du contenu de données utilisateur (UPDATE de masse) sans demande explicite.

# Fichiers probables
{{context_files}}
- `database/schema.py` (CREATE TABLE canoniques), `database/migrations.py` (fonctions `_migrate_*` + registre d'exécution), `scripts/db_migrations.py` (outillage/statut), `database/__init__.py` (init_db + réexports), `tests/test_audit_architecture_truth.py` (comptage des tables).

# Règles d'architecture
{{repo_rules}}
- Toute nouvelle table sensible (données personnelles) hérite des règles de rétention/backup existantes — le signaler dans le rapport.

# Critères d'acceptation
{{acceptance_criteria}}
- `init_db()` sur base VIERGE et `init_db()` sur base EXISTANTE (pré-migration) aboutissent au même schéma final.
- Le test de comptage des tables passe avec le nouveau total.

# Tests obligatoires
{{required_tests}}
- Test d'idempotence : exécuter la migration deux fois sur la même base temporaire sans exception.
- Test du chemin d'upgrade : créer une base au schéma ancien (sans la table/colonne), lancer `init_db()`, vérifier le schéma final.
- `tests/test_audit_architecture_truth.py` mis à jour et vert.

# Validation réelle
- Comparer `PRAGMA table_info` avant/après sur les deux chemins (base vierge, base migrée) et inclure le diff dans le rapport.
- Lancer la suite pytest des helpers du domaine touché.
- Vérifier qu'aucune donnée existante n'est perdue : COUNT(*) des tables touchées identique avant/après sur une base de test peuplée.

# Stratégie Git
- Jamais de modification directe de main.
- Travailler uniquement dans le worktree / la branche fournie.
- Commits clairs : schéma + migration + tests dans un commit cohérent (le schéma ne doit jamais être committé sans sa migration).

# Format du rapport final (OBLIGATOIRE)
{{result_format}}

## Qualité constante
- Chercher la cause racine des besoins de schéma (modéliser le domaine, pas rustiner une colonne JSON).
- Ne pas masquer le problème : une migration qui échoue en silence corrompt les upgrades suivants.
- Ne rien inventer : lire le schéma réel, ne pas supposer une colonne.
- Ne pas supprimer une table/colonne existante sans preuve ET sauvegarde.
- Respecter les conventions du dépôt ; tester avant chaque commit.
- Comparer avant/après (PRAGMA, comptages) ; préserver les contrats existants (helpers, réexports).
- Rapport précis ; ne pas déclarer COMPLETED sans preuve (double init_db + tests verts).
