# ADR-022 — Protection des données au repos

**Date** : 24 juillet 2026 — révisé le 11 août 2026
**Statut** : accepté

## Contexte

La base JARVIS contient conversations, emails, localisation, mémoire
personnelle et analyses d’écran. Les uploads et sauvegardes transportent les
mêmes catégories de données. Le chiffrement optionnel des seuls backups ne
suffisait pas : il était désactivé par défaut et échouait en laissant un
fichier SQLite en clair.

## Décision

- Activer le chiffrement authentifié des backups par défaut et échouer fermé.
- Générer une clé locale `0600` lorsqu’aucune passphrase n’est fournie.
- Utiliser une enveloppe versionnée avec KDF salé, tout en conservant la
  restauration des anciens backups.
- Proposer le chiffrement page par page de chaque base active avec SQLCipher,
  activé explicitement après une migration atomique contrôlée.
- Stocker une clé distincte par profil dans le Trousseau macOS ; une passphrase
  injectée par le gestionnaire de secrets reste possible pour CI et déploiement.
- Refuser d’ouvrir une base plaintext, une mauvaise clé ou un pilote SQLCipher
  dépourvu de FTS5 lorsque le chiffrement actif est demandé.
- Forcer `0600` sur DB, WAL/SHM/journal, backups, uploads et clés ; `0700` sur
  leurs dossiers.
- Vérifier l’intégrité et prendre un snapshot de sécurité avant restauration.
- Conserver FileVault sur le Mac de production pour couvrir les fichiers hors
  base, les temporaires, le swap et les autres données du compte.

## Validation SQLCipher

Le prototype demandé par la version initiale de cet ADR est validé :

- `sqlcipher3` fournit des roues reproductibles macOS arm64 et Linux x86_64 ;
- la migration utilise `sqlcipher_export` dans un fichier temporaire, vérifie
  l’intégrité, conserve `user_version`, garde une copie de rollback et remplace
  la base atomiquement ;
- les schémas JARVIS, FTS5, migrations, WAL, sauvegardes et lecteurs secondaires
  passent par une façade DB-API commune et sont couverts par les tests ;
- le Trousseau est interrogé par service et profil, sans exposer la clé dans les
  logs ni la stocker avec la base ;
- sauvegarde et restauration exportent un snapshot SQLite plaintext temporaire
  privé, immédiatement enveloppé en Fernet ou réimporté dans SQLCipher.

L’activation n’est pas automatique sur une installation existante : elle exige
`python tools/database_encryption.py enable --all-profiles`, puis
`DATABASE_ENCRYPTION_ENABLED=true`. Ce séquencement évite toute migration
silencieuse au démarrage et laisse à l’administrateur la maîtrise du rollback.

## Évaluation du chiffrement applicatif par colonne

Le chiffrement de colonnes isolées réduit la portée d’une fuite SQL, mais casse
la recherche FTS, le tri et les agrégations sur les contenus concernés. Il
multiplie aussi les chemins de migration et les risques de plaintext
intermédiaire. Il reste pertinent à étudier pour quelques secrets non
recherchables, après mise en place d’une clé dans le Trousseau.

## Conséquences

Les sauvegardes restent chiffrées indépendamment de la base active. SQLCipher
protège les pages SQLite et leurs sidecars lorsqu’il est activé, mais une clé
chargée en mémoire ne protège pas d’une compromission du compte pendant que
JARVIS tourne. FileVault reste requis pour la couverture du volume complet.
