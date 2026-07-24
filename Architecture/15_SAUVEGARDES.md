# 15 — Sauvegardes et restauration

**État** : implémenté et testé
**Code** : `scripts/db_maintenance.py`, `core/file_security.py`

## Politique actuelle

- Snapshot SQLite cohérent par `VACUUM INTO`, chaque jour à 04:15 et avant
  toute migration.
- Rotation des `BACKUP_KEEP` sauvegardes les plus récentes (7 par défaut).
- Chiffrement activé par défaut et **fail-closed** : une erreur de clé ou de
  chiffrement produit un rapport d’échec et supprime le snapshot plaintext.
- Enveloppe `JARVIS-BACKUP-V2` : sel aléatoire, PBKDF2-SHA256 (600 000
  itérations), puis Fernet authentifié.
- Compatibilité de lecture avec les anciens fichiers Fernet sans en-tête.
- Dossier de backup en `0700`, fichiers chiffrés/plaintext explicite et clé
  locale en `0600`.

## Gestion de la clé

Deux modes sont disponibles :

1. `BACKUP_ENCRYPTION_PASSPHRASE` renseignée : elle est prioritaire. Elle doit
   vivre dans le fichier de secrets `.env` ou un gestionnaire de secrets.
2. Passphrase vide : JARVIS crée automatiquement
   `BACKUP_ENCRYPTION_KEY_FILE` (`data/.backup_encryption.key` par défaut) en
   `0600`.

La clé doit être copiée dans un emplacement distinct des `.db.enc`. Copier
uniquement les sauvegardes sans leur clé rend leur restauration impossible.

## Procédure de restauration

`POST /api/backups/{name}/restore` et `restore_backup(name)` appliquent le même
flux :

1. refus des chemins absolus et traversées hors de `BACKUP_DIR` ;
2. déchiffrement en mémoire (V2 ou format legacy) ;
3. écriture d’une image SQLite temporaire `0600` ;
4. `PRAGMA integrity_check` avant toute mutation ;
5. snapshot chiffré de sécurité de la base courante ; un échec arrête la
   restauration ;
6. copie SQLite contrôlée vers la base active ;
7. réapplication des permissions `0600` et suppression du plaintext
   temporaire.

Les tests couvrent le round-trip chiffré, le format legacy, la mauvaise
passphrase, l’intégrité SQLite, le path traversal et la présence du snapshot
de sécurité.

## Configuration

```bash
BACKUP_ENABLED=true
BACKUP_DIR=./data/backups
BACKUP_KEEP=7
BACKUP_ENCRYPTION_ENABLED=true
BACKUP_ENCRYPTION_KEY_FILE=./data/.backup_encryption.key
# Facultatif ; si vide, la clé locale ci-dessus est créée.
BACKUP_ENCRYPTION_PASSPHRASE=
```

## Limite assumée

La base active SQLite n’est pas chiffrée page par page par JARVIS. Elle, ses
sidecars WAL/SHM et les uploads sont limités au compte Unix courant (`0600`,
dossiers `0700`). FileVault reste nécessaire pour le chiffrement complet du
volume. L’évaluation SQLCipher et chiffrement applicatif est documentée dans
`Architecture/adr/ADR-022-DATA-AT-REST.md`.
