# ADR-022 — Protection des données au repos

**Date** : 24 juillet 2026
**Statut** : accepté

## Contexte

La base JARVIS contient conversations, emails, localisation, mémoire
personnelle et analyses d’écran. Les uploads et sauvegardes transportent les
mêmes catégories de données. Le chiffrement optionnel des seuls backups ne
suffisait pas : il était désactivé par défaut et échouait en laissant un
fichier SQLite en clair.

## Décision immédiate

- Activer le chiffrement authentifié des backups par défaut et échouer fermé.
- Générer une clé locale `0600` lorsqu’aucune passphrase n’est fournie.
- Utiliser une enveloppe versionnée avec KDF salé, tout en conservant la
  restauration des anciens backups.
- Forcer `0600` sur DB, WAL/SHM/journal, backups, uploads et clés ; `0700` sur
  leurs dossiers.
- Vérifier l’intégrité et prendre un snapshot de sécurité avant restauration.
- Exiger FileVault sur le Mac de production pour couvrir la base active, les
  fichiers temporaires, le swap et les autres données du compte.

## Évaluation SQLCipher

SQLCipher apporterait le chiffrement transparent page par page de SQLite, mais
son adoption n’est pas retenue dans ce checkpoint :

- dépendance native supplémentaire à compiler et maintenir sur macOS et CI ;
- migration atomique obligatoire de la base existante et procédure de rollback ;
- validation à refaire pour WAL, FTS5, migrations et lecteurs secondaires TV ;
- gestion de clé au démarrage à intégrer au Trousseau macOS, sans clé dans
  `.env` ni dans le même répertoire que la base ;
- impossibilité de lire la base avec le module `sqlite3` standard actuel.

SQLCipher sera adopté seulement avec un prototype validant ces cinq points,
une copie chiffrée de migration, un rollback testé et un job CI macOS.

## Évaluation du chiffrement applicatif par colonne

Le chiffrement de colonnes isolées réduit la portée d’une fuite SQL, mais casse
la recherche FTS, le tri et les agrégations sur les contenus concernés. Il
multiplie aussi les chemins de migration et les risques de plaintext
intermédiaire. Il reste pertinent à étudier pour quelques secrets non
recherchables, après mise en place d’une clé dans le Trousseau.

## Conséquences

Les copies de sauvegarde sont protégées dès une installation neuve. Les
permissions limitent les lectures inter-comptes, mais ne remplacent ni
FileVault contre le vol du disque ni SQLCipher contre une compromission du
compte utilisateur pendant que JARVIS tourne.
