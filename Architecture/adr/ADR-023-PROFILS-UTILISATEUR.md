# ADR-023 — Isolation multi-utilisateur par base de profil

**Statut** : accepté et implémenté  
**Date** : 11 août 2026

## Contexte

JARVIS a été construit autour d’une base SQLite locale unique. Ajouter un
`user_id` uniquement aux conversations et aux tâches laisserait accessibles la
mémoire, les documents, les appareils, les sessions, les journaux et les
futures tables oubliées. Une isolation partielle est plus dangereuse qu’un mode
mono-utilisateur explicite.

## Décision

Le profil historique `default` conserve `DB_PATH`. Chaque profil additionnel
reçoit une base complète sous `profiles/<profile_id>/`, initialisée par le même
schéma et les mêmes migrations. Un `ContextVar` lie la requête HTTP, la socket
WebSocket, les tâches asyncio héritées et les handlers de l’EventBus au profil
courant.

Le registre `user_profiles` vit dans la base principale. Le header
`X-Jarvis-Profile` sélectionne un profil pour HTTP ; le cookie non secret
`jarvis_profile` assure la même sélection pour WebSocket et SSE, dont les API
navigateur ne permettent pas toujours un header arbitraire. Le cookie de
session reste `HttpOnly` et est vérifié dans la base sélectionnée : un jeton
valide dans un profil est invalide dans tous les autres.

Les uploads suivent la même partition de stockage. Les abonnements EventBus et
le registre WebSocket filtrent leurs destinataires par profil. Le changement de
profil purge IndexedDB avant rechargement afin de ne pas réafficher un cache du
profil précédent.

## Conséquences

- Les modules métier existants obtiennent l’isolation sans réécriture de leurs
  requêtes SQL et les futures tables sont isolées par défaut.
- Les profils disposent chacun de leur secret, de leurs sessions, appareils,
  paramètres et données.
- Le profil principal administre le registre. Une désactivation conserve la
  base sur disque et reste récupérable ; aucune suppression irréversible n’est
  exposée.
- Les workers système démarrés sans contexte opèrent sur `default`. Un worker
  déclenché depuis une requête ou un événement hérite du profil concerné.
- Les clients natifs qui ne choisissent pas de profil restent compatibles et
  utilisent `default`.

## Options rejetées

- **Ajouter `user_id` table par table** : migration massive, oublis probables,
  requêtes historiques non filtrées et régressions de confidentialité.
- **Une instance système par utilisateur** : isolation forte mais exploitation,
  ports, launchd et mises à jour dupliqués.
- **Nom de profil dans les chemins fournis par le client** : risque de traversée
  de répertoire. Les identifiants sont générés côté serveur et validés par une
  grammaire stricte avant toute résolution de chemin.
