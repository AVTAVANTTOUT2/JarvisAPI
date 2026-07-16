# Chat texte natif — Vague 2

## Vue d'ensemble

Le chat Android 2.0.0-alpha02 remplace le placeholder par une expérience native complète :

- liste des conversations (Room + sync GET Bearer)
- écran de chat avec streaming WebSocket
- fallback HTTP `POST /api/mobile/chat` (idempotent via `client_message_id`)
- file d'opérations offline (`pending_chat_operations`)
- intégration voix via `VoiceActivity` + `conversation_id`

## Architecture

```
UI (Compose)
  ConversationListScreen / ChatScreen
       ↓
  ViewModels
       ↓
  ConversationRepository / ChatRepository
       ↓
  Room v2 + JarvisChatWebSocket + JarvisRepository (Retrofit)
       ↓
  FastAPI : GET/POST/PATCH conversations, POST /api/mobile/chat, WS /ws
```

## Room v2

| Table | Rôle |
|-------|------|
| `chat_conversations` | Miroir local + état sync |
| `chat_messages` | Messages user/assistant, `deliveryState`, `clientRequestId` unique |
| `pending_chat_operations` | File CREATE/SEND/RENAME/PIN/ARCHIVE/DELETE |
| `chat_drafts` | Brouillon composer par conversation |

Migration non destructive : `MIGRATION_1_2` via `Migrations.kt` (partagée avec la migration GPS Vague 2B).

## WebSocket (`JarvisChatWebSocket`)

- Handshake `Authorization: Bearer` (jamais en query)
- Messages sortants : `text`, `new_conversation`, `switch_conversation`, `action_confirm`
- Messages entrants : `connected`, `chunk`, `done`, `response`, `error`, `conversation_switched`, `action_pending`
- Reconnexion exponentielle + jitter, arrêt sur 401

## Envoi message

1. Insert Room (`LOCAL_PENDING` → `SENDING` si WS connecté)
2. Si WS OK : streaming `chunk`/`done` avec flush Room ~150 ms
3. Sinon : `QUEUED` + `PendingChatOperationEntity` (`SEND_MESSAGE`)
4. `ChatSyncWorker` rejoue via `POST /api/mobile/chat`

## Sync

- `ChatSyncWorker` : périodique 15 min + `runOnce` après mutations
- `ConversationRepository.refreshFromServer()` : GET `/api/conversations`
- Ordre des ops respecté par `conversationLocalId` + `createdAtMillis`

## UI

- **Liste** : groupes Épinglées / Aujourd'hui / Hier / 7j / Plus anciennes, recherche, FAB, menu contextuel
- **Chat** : LazyColumn, indicateur streaming, composer multiline, micro → voix, bannière offline, dialog `action_pending`

## Version

`versionName 2.0.0-alpha02` / `versionCode 8`
