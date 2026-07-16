# Offline sync — Vague 1

## Principe

L’UI lit **Room** en premier. Le réseau met à jour Room via `SyncManager.refreshHome()`.

## Entités Room (schema v1)

| Table | Rôle |
|-------|------|
| `cached_briefing` | Dernier briefing réussi (`kind`, contenu, date validité) |
| `cached_task` | Miroir des tâches serveur |
| `cached_event` | Fenêtre agenda synchronisée |
| `cached_notification` | Notifications JARVIS |
| `sync_metadata` | Horodatage / dernière erreur par clé |
| `pending_locations` | File GPS offline-first (Vague 2B) — états PENDING/SENDING/SYNCED/FAILED_*/INVALID |
| `location_sync_lock` | Verrou mono-worker pour sync batch |

Migrations destructives interdites (`fallbackToDestructiveMigration` absent). Migration `MIGRATION_1_2` recrée `pending_locations` sans perte de données v1.

## SyncManager

Fichier : `core/sync/SyncManager.kt`

1. Vérifie serveur + token natif
2. GET Bearer : briefing, tasks, calendar (fenêtre jour), notifications
3. Upsert Room par domaine
4. Erreurs isolées → `partialErrors` (un widget peut échouer sans tout casser)
5. HTTP 401 → `unauthorized=true` + `ConnectivityObserver.Unauthorized`

## WorkManager

- `SyncWorker` — travail périodique unique ~30 min (Accueil).
- `LocationSyncWorker` — GPS batch ~15 min + one-shot après insert (`jarvis-location-sync`).

## GPS offline (Vague 2B)

1. `JarvisLocationService` : capture → validation → Room → `LocationSyncWorker.enqueueNow()`
2. Jamais d'appel réseau dans le callback GPS.
3. `LocationSyncCoordinator` : lock → batch 50 → `POST /api/location/batch` Bearer.
4. Purge immédiate des points `accepted` / `duplicates`.
5. Voir `docs/LOCATION.md` pour seuils et états.

## ConnectivityObserver

États : `Offline` | `NetworkAvailable` | `ServerReachable` | `Unauthorized`.

Ne confond pas « Wi‑Fi OK » et « Mac JARVIS joignable ».

## Hors Vague 1 / 2B GPS

- Pending chat / tasks / calendar mutations
- WebSocket

GPS offline-first + batch : livré Vague 2B — voir `docs/LOCATION.md`.

Voir plan Vague 1 : `docs/superpowers/plans/2026-07-16-android-production-wave1.md`.
