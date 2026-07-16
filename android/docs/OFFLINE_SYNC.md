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
| `pending_location` | **Stub** prêt pour la file GPS (non branché sur le service en Vague 1) |

Migrations destructives interdites (`fallbackToDestructiveMigration` absent).

## SyncManager

Fichier : `core/sync/SyncManager.kt`

1. Vérifie serveur + token natif
2. GET Bearer : briefing, tasks, calendar (fenêtre jour), notifications
3. Upsert Room par domaine
4. Erreurs isolées → `partialErrors` (un widget peut échouer sans tout casser)
5. HTTP 401 → `unauthorized=true` + `ConnectivityObserver.Unauthorized`

## WorkManager

`SyncWorker` — travail périodique unique ~30 min, contrainte `NetworkType.CONNECTED`, déclenché aussi au démarrage (`JarvisApplication`).

## ConnectivityObserver

États : `Offline` | `NetworkAvailable` | `ServerReachable` | `Unauthorized`.

Ne confond pas « Wi‑Fi OK » et « Mac JARVIS joignable ».

## Hors Vague 1

- File GPS offline réelle + batch
- Pending chat / tasks / calendar mutations
- Idempotence `client_*_id`
- WebSocket

Voir plan global : `docs/superpowers/plans/2026-07-16-android-production-wave1.md`.
