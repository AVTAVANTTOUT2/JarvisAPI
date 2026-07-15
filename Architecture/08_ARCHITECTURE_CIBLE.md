# 08 — Architecture Cible (Target Architecture)

**Date** : 11 juillet 2026
**Statut** : Cible progressive — couches database/API/Apple/frontend atteintes après les Phases 2/4/5/6 ; Queue Engine, AI Service et observabilité restent futurs

---

L'architecture cible représente l'organisation idéale de JARVIS après tous les refactorings planifiés. Elle est organisée en couches strictement séparées, communiquant via des interfaces explicites et un Event Bus central.

## Vue d'ensemble

```mermaid
graph TB
    subgraph "Clients"
        FRONT["Frontend Unique<br/>Next.js 15 responsive<br/>Desktop / Mobile / PWA"]
        IMESSAGE_BRIDGE["iMessage Bridge<br/>entrée/sortie messages"]
        TV["TV Dashboard<br/>War Room"]
        AGENT["Agent distant<br/>MacBook"]
    end

    subgraph "API Layer"
        API["FastAPI<br/>12 routeurs par domaine<br/>174 opérations HTTP"]
        WS["WebSocket /ws<br/>Streaming + Broadcast"]
        AUTH["Auth Middleware<br/>SDK partagé"]
    end

    subgraph "Core Services"
        ORCH["Pipeline<br/>_process_message"]
        EVENT_BUS["Event Bus<br/>pub/sub, historique"]
        QUEUE["Queue Engine<br/>traitements lourds"]
        SEARCH["Search Service<br/>FTS5 + embeddings"]
    end

    subgraph "Domain Services"
        MEMORY["Memory Service<br/>faits, patterns, contexte"]
        TASK["Task Service<br/>CRUD + notifications"]
        JOURNAL["Journal Service<br/>entrées + insights"]
        TIMELINE["Timeline Service<br/>événements par contact"]
        NOTIF["Notification Service<br/>création + broadcast"]
    end

    subgraph "External Connectors"
        APPLE["Apple Data Service<br/>chat.db ⋅ Contacts<br/>Calendar ⋅ Mail"]
        LLM["AI Service<br/>DeepSeek ⋅ Ollama<br/>embeddings ⋅ cache"]
        WEATHER["Weather Service<br/>OpenWeatherMap"]
        WEB_SEARCH["Web Search<br/>Tavily"]
    end

    subgraph "Storage"
        SQLITE[(SQLite WAL<br/>jarvis.db<br/>70 + FTS→75)]
        IDB[(IndexedDB<br/>offline queue<br/>read cache)]
        SW["Service Worker<br/>precache + push<br/>background sync"]
    end

    FRONT --> API
    FRONT --> WS
    IMESSAGE_BRIDGE --> API
    TV --> API
    AGENT --> API

    API --> AUTH
    API --> ORCH
    API --> EVENT_BUS

    ORCH --> MEMORY
    ORCH --> TASK
    ORCH --> JOURNAL
    ORCH --> LLM

    EVENT_BUS --> NOTIF
    EVENT_BUS --> QUEUE
    EVENT_BUS --> TIMELINE
    EVENT_BUS --> SEARCH

    QUEUE --> LLM
    QUEUE --> SEARCH
    QUEUE --> TIMELINE

    MEMORY --> SQLITE
    TASK --> SQLITE
    JOURNAL --> SQLITE
    NOTIF --> SQLITE
    SEARCH --> SQLITE

    APPLE --> SQLITE
    LLM --> APPLE

    FRONT --> IDB
    FRONT --> SW
```

## Couche 1 — Clients

| Client | Technologie | Responsabilité |
|---|---|---|
| Frontend Unique | Next.js 15, React 19, Tailwind v4 | Interface responsive Desktop/Mobile/PWA |
| iMessage Bridge | AppleScript (lecture chat.db + envoi) | Entrée/sortie iMessage |
| TV Dashboard | FastAPI + JS vanilla | Affichage War Room sur TV |
| Agent distant | Python + requests + Pillow | Capture écran MacBook → TTS |

## Couche 2 — API Layer

**Routeurs FastAPI par domaine** (implémentés : 12 routeurs, 175 lignes dans `main.py`) :

```
api/
├── router_auth.py          ← /api/auth/*
├── router_people.py        ← /api/people/*
├── router_conversations.py ← /api/conversations/*
├── router_tasks.py         ← /api/tasks/*
├── router_location.py      ← /api/location/*, /api/places/*
├── router_devices.py       ← /api/devices/*
├── router_daemon.py        ← /api/audio-daemon/*, /api/control/*
├── router_devagent.py      ← /api/devagent/*
├── router_quality.py       ← /api/quality/*, /api/migrations/*
├── router_rituals.py       ← /api/rituals/*, /api/dnd/*
├── router_recordings.py    ← /api/recordings/*
├── router_misc.py          ← Status, stats, costs, export, search
├── ws_handler.py           ← WebSocket /ws
├── frontend.py             ← _setup_frontend, _is_mobile_device
└── middleware.py            ← security_middleware
```

## Couche 3 — Core Services

### Pipeline (`pipeline.py`)

Point d'entrée unique pour le traitement de message :
```
Input (WS texte, WS audio, REST) → _build_enriched_context → orchestrator.handle
→ agent.handle → _extract_action → execute_action → response + TTS
```

### Event Bus (`jarvis/event_bus.py`)

**Implémenté en Phase 3 pour les 10 mutations de domaine centrales.** Consommateurs actuels :
- `database/event_log.py` (journal SQLite idempotent)
- `websocket_registry.py` (push WebSocket)
- `scripts/audio_daemon.py` (TTS urgent/high)
- flux SSE existant consommé par `pwa/EventSync.tsx`

Les consommateurs cibles `Queue Engine` et `SearchService` restent à ajouter lorsque ces services seront implémentés. `NotificationService` est implémenté le 14/07/2026 : il centralise les producteurs, la politique de priorité, la déduplication et l'émission post-commit de `notification.created`. Les événements techniques historiques du pipeline restent compatibles avec le même bus.

### Queue Engine (`queue_engine.py`)

File de traitement pour les opérations lourdes :
```
MessageImported → Résumé IA → Embeddings → Timeline → Mémoire → Notifications → Recherche
```

### Search Service (`search_service.py`)

Moteur de recherche unifié : FTS5 (SQLite) + embeddings (sentence-transformers).

## Couche 4 — Domain Services

Chaque service est propriétaire de ses données (Data Ownership — ADR-011).

| Service | Données possédées | Écriture | Lecture |
|---|---|---|---|
| Memory Service | episodes, facts, patterns, life_profile | ✅ | Tous |
| Task Service | tasks | ✅ | Tous |
| Journal Service | journal entries, insights | ✅ | Tous |
| Timeline Service | relationship_events, timeline | ✅ | Tous |
| Notification Service | notifications | ✅ | Tous |

## Couche 5 — External Connectors (Plugins)

Chaque connecteur externe implémente l'interface `Plugin` (ADR-015).

### Apple Data Service

**SEUL** point d'accès à `chat.db`, Contacts, Calendar, Mail. Tous les autres services passent par lui.

### AI Service

**SEUL** point d'accès aux LLM (DeepSeek, Ollama). Gère le cache, les embeddings, le routage entre modèles.

## Couche 6 — Storage

| Stockage | Technologie | Usage |
|---|---|---|
| SQLite (jarvis.db) | WAL mode | Données persistantes (70 tables persistantes, 75 avec FTS5 — voir doc 32) |
| IndexedDB | idb v8 | File d'écriture offline + cache lecture |
| Service Worker | Workbox | Precache app shell, push, background sync |

## Flux de données principal

```mermaid
sequenceDiagram
    participant Client as Frontend
    participant API as FastAPI
    participant Pipeline as Pipeline
    participant Orch as Orchestrator
    participant Agent as Agent LLM
    participant EventBus as Event Bus
    participant Queue as Queue Engine
    participant DB as SQLite

    Client->>API: POST /api/chat (WebSocket)
    API->>Pipeline: _process_message(text, conv_id)
    Pipeline->>Orch: orchestrator.handle()
    Orch->>Agent: agent.handle()
    Agent->>Agent: LLM call (AI Service)
    Agent-->>Orch: response + actions
    Orch-->>Pipeline: result
    Pipeline->>EventBus: emit(MessageSent)
    Pipeline->>DB: save_message(user, assistant)
    EventBus->>Queue: enqueue(IndexMessage)
    Queue->>DB: upsert_embedding()
    Pipeline-->>Client: streaming response
```

## Dépendances (post-refactoring)

```mermaid
graph TB
    CONFIG["config.py<br/>feuille, aucun import"]
    EVENT["event_bus.py<br/>stdlib"]
    DB["database/<br/>25 modules + façade<br/>accès via core.py"]
    LLM_CLIENT["llm.py<br/>client DeepSeek"]

    SERVICES["domain/*.py<br/>importent db, event"]
    APPLE["apple_data.py<br/>importe config"]
    AI["ai_service.py<br/>importe llm, apple"]

    PIPELINE["pipeline.py<br/>importe orchestrator, actions"]
    ROUTERS["api/router_*.py<br/>importent services, pipeline"]

    MAIN["main.py<br/>175 lignes<br/>monte les routeurs"]

    CONFIG --> DB
    CONFIG --> LLM_CLIENT
    CONFIG --> APPLE
    DB --> SERVICES
    EVENT --> SERVICES
    LLM_CLIENT --> AI
    APPLE --> AI
    SERVICES --> PIPELINE
    AI --> PIPELINE
    PIPELINE --> ROUTERS
    SERVICES --> ROUTERS
    ROUTERS --> MAIN

    style MAIN fill:#4ecdc4,color:#fff
    style PIPELINE fill:#4ecdc4,color:#fff
    style EVENT fill:#ffe66d,color:#000
```

**Zéro dépendance circulaire. Zéro lazy import. Zéro god object.**
