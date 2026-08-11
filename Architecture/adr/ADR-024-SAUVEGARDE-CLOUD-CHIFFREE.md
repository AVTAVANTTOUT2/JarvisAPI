# ADR-024 — Sauvegarde cloud chiffrée et optionnelle

**Date** : 11 août 2026
**Statut** : accepté

## Contexte

Les sauvegardes Fernet V2 protègent déjà la confidentialité et l’intégrité,
mais restent sur le même site physique que le Mac. Une panne, un vol ou un
sinistre peut donc supprimer simultanément la base et toutes ses copies. La
vision JARVIS interdit toutefois une dépendance cloud obligatoire et tout envoi
de données personnelles non chiffrées.

## Décision

- Ajouter une réplication WebDAV standard, désactivée par défaut.
- N’envoyer que les enveloppes `JARVIS-BACKUP-V2` déjà chiffrées localement ; le
  serveur distant ne reçoit jamais SQLite plaintext ni la clé Fernet.
- Exiger HTTPS, refuser credentials dans l’URL, n’accepter qu’un mode
  d’authentification Basic ou Bearer et désactiver les redirections.
- Confirmer chaque `PUT` par un `HEAD` de même taille ; une erreur laisse la
  copie locale intacte mais rend le job en échec de façon visible.
- Lister, retenir et restaurer uniquement les objets du profil actif.
- Borner les téléchargements et repasser toute restauration distante par
  l’authentification Fernet, l’intégrité SQLite, le snapshot de sécurité et le
  format actif SQLite/SQLCipher.

## Alternatives

- **SDK S3 propriétaire** : large compatibilité mais nouvelle dépendance,
  signature complexe et configuration spécifique au fournisseur.
- **Dossier iCloud Drive** : très simple sur macOS mais ne fournit pas de reçu
  distant fiable ; une copie locale ne prouve pas que la synchronisation est
  terminée.
- **WebDAV** : protocole ouvert déjà disponible chez plusieurs fournisseurs,
  testable sans SDK additionnel et compatible avec HTTPX présent dans JARVIS.

## Conséquences

Le cloud reste facultatif et ne bloque pas les usages locaux lorsqu’il est
désactivé. Lorsqu’il est activé, une indisponibilité distante fait échouer le
rapport de sauvegarde afin d’être observable, sans supprimer la copie locale.
La disponibilité et la politique de rétention du fournisseur restent hors du
contrôle de JARVIS ; l’authenticité des données restaurées reste garantie par
Fernet V2.
