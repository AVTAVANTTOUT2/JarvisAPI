# Migrations SQLite versionnées

Ce dossier contient des migrations **incrémentales** appliquées une seule fois,
dans l'ordre alphabétique de leur nom de fichier. Elles complètent — sans le
remplacer — le système de migrations idempotentes `_migrate_xxx(conn)` de
`database/__init__.py` (qui gère l'ajout de colonnes/tables sur une base déjà
existante à chaque démarrage).

**Utiliser ce dossier pour** : des changements qui n'ont de sens qu'une seule
fois (backfill de données, transformation qui ne peut pas s'exprimer comme un
`CREATE TABLE IF NOT EXISTS`/`ALTER TABLE` idempotent, renommage de colonne
via une table temporaire, etc.).

**Ne pas utiliser ce dossier pour** : l'ajout d'une table ou d'une colonne
simple — utilisez plutôt une fonction `_migrate_xxx(conn)` idempotente dans
`database/__init__.py`, appelée depuis `init_db()`.

## Format

```
0001_description_courte.sql
0002_autre_migration.sql
```

Numérotées sur 4 chiffres, ordre = ordre d'application. Chaque fichier est du
SQL brut, exécuté dans une transaction unique via `sqlite3.Connection.executescript`.

## Fonctionnement

`scripts/db_migrations.py::apply_pending_migrations()` :

1. Liste les fichiers `.sql` de ce dossier.
2. Compare avec `schema_migrations` (table qui trace `filename` + `checksum`
   SHA-256 du contenu). Si un fichier déjà appliqué a été modifié depuis
   (checksum différent), une `MigrationIntegrityError` est levée — jamais de
   ré-application silencieuse d'un contenu différent.
3. S'il y a des migrations en attente, **sauvegarde automatique** de la base
   (`scripts.db_maintenance.run_backup()`) avant la première d'entre elles.
4. Applique chaque migration en attente, dans l'ordre, une transaction par
   fichier. Arrêt immédiat à la première erreur (les migrations suivantes ne
   sont pas appliquées) — la sauvegarde permet une restauration immédiate.

Appelée automatiquement au démarrage de JARVIS (après `init_db()`), et
disponible manuellement via `POST /api/migrations/run` /
`GET /api/migrations/status`.
