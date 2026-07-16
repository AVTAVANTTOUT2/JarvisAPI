# ADR-021 — Localisation Android offline-first et batch idempotent

**Date :** 2026-07-16  
**Statut :** Accepté  
**Lié :** ADR-020 (fondation offline-first Companion), Vague 2B  
**Spec :** `docs/superpowers/specs/2026-07-16-android-offline-location-design.md`

## Contexte

Le Companion Android envoie chaque point GPS immédiatement via `POST /api/location`. La table Room `pending_locations` existe en stub mais n’est pas branchée. Hors réseau ou erreur HTTP, les points sont perdus. Le backend expose déjà `/api/location/batch` sans idempotence ni limite de lot.

## Décision

1. Persister chaque point valide dans Room **avant** toute tentative réseau.
2. Synchroniser via `LocationSyncCoordinator` + `LocationSyncWorker` (unique work `jarvis-location-sync`), hors du `SyncManager` Accueil.
3. Enrichir `POST /api/location/batch` : Bearer obligatoire, `client_point_id`, limite **50**, réponse `accepted` / `duplicates` / `rejected`, table `location_point_dedup` `(device_id, client_point_id)`.
4. Encapsuler le GPS derrière `LocationEngine` ; implémenter uniquement `LocationManagerEngine` ; documenter `FusedLocationEngine` pour Vague 2B+.
5. Réservation de lot atomique avec `batchId` + verrou Room `location_sync_lock`.
6. État local `INVALID` pour rejets de validation ; purge immédiate des `SYNCED` après confirmation.

## Conséquences

- Plus de perte silencieuse hors ligne ; reprise après reboot via WorkManager + boot receiver.
- Payloads plus petits (50) et retries moins coûteux.
- Compatibilité Shortcuts iOS conservée sur l’endpoint unitaire.
- Dépendance Play Services évitée pour cette vague.
- Spec complète et critères de done : voir le document de design cité ci-dessus.
