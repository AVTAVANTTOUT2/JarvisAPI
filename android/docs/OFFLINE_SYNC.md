# Offline sync — Vague 1 + Chat (Vague 2) + GPS (Vague 2B)

## Principe

L'UI lit **Room** en premier. Le réseau met à jour Room via `SyncManager.refreshHome()` (accueil) et les repositories chat (conversations/messages).

## Entités Room

### Schema v1 (accueil)

| Table | Rôle |
|-------|------|
| `cached_briefing` | Dernier briefing réussi (`kind`, contenu, date validité) |
| `cached_task` | Miroir des tâches serveur |
| `cached_event` | Fenêtre agenda synchronisée |
| `cached_notification` | Notifications JARVIS |
| `sync_metadata` | Horodatage / dernière erreur par clé |
| `pending_locations` | File GPS offline-first (Vague 2B) — états PENDING/SENDING/SYNCED/FAILED_*/INVALID |
| `location_sync_lock` | Verrou mono-worker pour sync batch |

### Schema v2 (chat + GPS) — `MIGRATION_1_2`

| Table | Rôle |
|-------|------|
| `chat_conversations` | Conversations locales + `serverId`, pin/archive |
| `chat_messages` | Messages avec `deliveryState`, `clientRequestId` unique |
| `pending_chat_operations` | File offline ordonnée par conversation |
| `chat_drafts` | Brouillons composer |

Migrations destructives interdites (`fallbackToDestructiveMigration` absent). Migration `MIGRATION_1_2` recrée `pending_locations` sans perte de données v1 et ajoute les tables chat.

## SyncManager (accueil)

Fichier : `core/sync/SyncManager.kt`

1. Vérifie serveur + token natif
2. GET Bearer : briefing, tasks, calendar (fenêtre jour), notifications, conversations (métadonnées)
3. Upsert Room par domaine
4. Erreurs isolées → `partialErrors`
5. HTTP 401 → `unauthorized=true`

## ChatSyncWorker (chat)

Fichier : `core/sync/ChatSyncWorker.kt`

Types d'opérations (`PendingChatOpType`) :

| Type | Endpoint |
|------|----------|
| `CREATE_CONVERSATION` | `POST /api/mobile/conversations` |
| `SEND_MESSAGE` | `POST /api/mobile/chat` (+ `client_message_id`) |
| `RENAME` | `PATCH /api/conversations/{id}` |
| `PIN` / `UNPIN` | `POST /api/conversations/{id}/pin` |
| `ARCHIVE` | `POST /api/conversations/{id}/archive` |
| `DELETE` | `DELETE /api/conversations/{id}` |

Idempotence : `client_message_id` 8–64 caractères alphanum/`_`/`-`, unique par message Room.

Streaming temps réel : `JarvisChatWebSocket` (prioritaire si connecté).

## WorkManager

| Worker | Intervalle | Rôle |
|--------|------------|------|
| `SyncWorker` | ~30 min | Accueil |
| `LocationSyncWorker` | ~15 min | GPS batch + one-shot après insert (`jarvis-location-sync`) |
| `ChatSyncWorker` | ~15 min | File chat + refresh conversations |

## GPS offline (Vague 2B)

1. `JarvisLocationService` : capture → validation → Room → `LocationSyncWorker.enqueueNow()`
2. Jamais d'appel réseau dans le callback GPS.
3. `LocationSyncCoordinator` : lock → batch 50 → `POST /api/location/batch` Bearer.
4. Purge immédiate des points `accepted` / `duplicates`.
5. Voir `docs/LOCATION.md` pour seuils et états.

## ConnectivityObserver

États : `Offline` | `NetworkAvailable` | `ServerReachable` | `Unauthorized`.

Ne confond pas « Wi‑Fi OK » et « Mac JARVIS joignable ».

Voir aussi : `docs/CHAT.md`, `docs/LOCATION.md`.
