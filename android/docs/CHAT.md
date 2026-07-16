# Chat texte natif Android (Vague 2)

## Objectif

Remplacer le placeholder Chat par une interface Compose production : conversations, historique Room, streaming WebSocket, file offline, mutations.

## Architecture

```
UI (ConversationList / ChatScreen)
  → ViewModels
    → ConversationRepository / ChatRepository (Flow Room)
      → JarvisChatWebSocket (streaming, Bearer handshake)
      → POST /api/mobile/chat (fallback non-stream + idempotence)
      → ChatSyncWorker (pending ops)
```

## Transports

| Cas | Transport |
|-----|-----------|
| En ligne + streaming | `WS /ws` + header `Authorization: Bearer` |
| Offline / WS down | File Room → `POST /api/mobile/chat` avec `client_message_id` |
| Mutations rename/pin/archive/delete | REST Bearer sur `/api/conversations/...` |

**Jamais** de token en query string.

## Idempotence

UUID `client_message_id` côté Android. Table serveur `mobile_chat_dedup(device_id, client_message_id)`.

## Room (schema v2)

- `chat_conversations`
- `chat_messages` (`deliveryState`, `clientRequestId`, `isStreaming`)
- `pending_chat_operations`
- `chat_drafts`

Migration 1→2 non destructive.

## Voix PTT

Le micro du composer ouvre `VoiceActivity` avec `conversation_id` serveur. L’historique texte est rafraîchi après le tour.

## Hors scope Vague 2

Voix continue, agenda/tâches UI, GPS offline, release 2.0.0 stable.
