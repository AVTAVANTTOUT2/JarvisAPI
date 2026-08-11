# 15 — Sauvegardes et restauration

**État** : implémenté et testé
**Code** : `scripts/db_maintenance.py`, `database/encryption.py`,
`core/file_security.py`

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

## Base active SQLCipher

La protection de la base active est indépendante de l’enveloppe Fernet des
sauvegardes. Une migration explicite chiffre chaque base de profil page par
page, conserve le schéma, FTS5 et `user_version`, puis garde une copie de
rollback privée :

```bash
python tools/database_encryption.py status --all-profiles
python tools/database_encryption.py enable --all-profiles
# Après succès seulement :
DATABASE_ENCRYPTION_ENABLED=true
```

Par défaut, chaque profil reçoit une clé distincte dans le Trousseau macOS sous
le service `com.jarvis.database.sqlcipher`. En CI ou avec un gestionnaire de
secrets, `DATABASE_ENCRYPTION_PASSPHRASE` peut fournir une passphrase d’au moins
20 caractères. Une base plaintext, une mauvaise clé ou un pilote sans FTS5
font échouer le démarrage fermé.

La restauration d’une sauvegarde Fernet détecte la cible : elle restaure une
image SQLite standard lorsque SQLCipher est désactivé et la réimporte chiffrée
lorsqu’il est actif. Aucun backup ne dépend donc du format de la base courante.

## Configuration

```bash
BACKUP_ENABLED=true
BACKUP_DIR=./data/backups
BACKUP_KEEP=7
BACKUP_ENCRYPTION_ENABLED=true
BACKUP_ENCRYPTION_KEY_FILE=./data/.backup_encryption.key
# Facultatif ; si vide, la clé locale ci-dessus est créée.
BACKUP_ENCRYPTION_PASSPHRASE=
DATABASE_ENCRYPTION_ENABLED=false
DATABASE_ENCRYPTION_PASSPHRASE=
DATABASE_ENCRYPTION_KEYCHAIN_SERVICE=com.jarvis.database.sqlcipher
```

## Limite assumée

SQLCipher couvre la base active et ses sidecars après activation, pas les
uploads ni les autres fichiers applicatifs. Ceux-ci restent limités au compte
Unix courant (`0600`, dossiers `0700`) et FileVault demeure nécessaire pour le
chiffrement complet du volume. La décision est détaillée dans
`Architecture/adr/ADR-022-DATA-AT-REST.md`.
