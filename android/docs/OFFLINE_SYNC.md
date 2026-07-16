# Offline sync — Vague 1 + Chat (Vague 2)

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
| `pending_location` | **Stub** prêt pour la file GPS (non branché sur le service en Vague 1) |

### Schema v2 (chat) — `MIGRATION_1_2`

| Table | Rôle |
|-------|------|
| `chat_conversations` | Conversations locales + `serverId`, pin/archive |
| `chat_messages` | Messages avec `deliveryState`, `clientRequestId` unique |
| `pending_chat_operations` | File offline ordonnée par conversation |
| `chat_drafts` | Brouillons composer |

Migrations destructives interdites (`fallbackToDestructiveMigration` absent).

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
| `ChatSyncWorker` | ~15 min | File chat + refresh conversations |

## ConnectivityObserver

États : `Offline` | `NetworkAvailable` | `ServerReachable` | `Unauthorized`.

Voir aussi : `docs/CHAT.md`.
