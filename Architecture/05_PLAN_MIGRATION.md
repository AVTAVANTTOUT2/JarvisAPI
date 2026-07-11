# 05 — Plan de Migration

**Date** : 11 juillet 2026
**Durée totale** : 15 jours (6 phases)
**Principe** : Chaque phase est indépendante, testable, réversible, sans interruption de service.

## Vue d'ensemble

```
Phase 1 → Quick wins P0 (sécurité + stabilité)        Jour 1     [4 corrections]
Phase 2 → Database modulaire                           Jour 2     [ADR-009]
Phase 3 → Pipeline + Event bus                         Jour 3-4   [ADR-010, ADR-005]
Phase 4 → main.py découpé en routeurs                  Jour 5-7   [ADR-008]
Phase 5 → Apple Data Service                            Jour 8-10  [ADR-006]
Phase 6 → Frontend unifié + SDK auth                   Jour 11-15 [ADR-001, ADR-007]
```

## Phase 1 — Quick Wins P0 (Jour 1)

### 1.1 SQLite busy_timeout (ADR-004)

**État** : ✅ Implémenté le 11 juillet 2026 (`PRAGMA busy_timeout = 5000`) — test ciblé ajouté.

| | |
|---|---|
| **Fichier** | `database/__init__.py`, fonction `get_connection()` |
| **Changement** | Ajouter `conn.execute("PRAGMA busy_timeout = 5000")` |
| **Impact** | Aucun. Le mode WAL est déjà actif. |
| **Test** | `python -m pytest tests/ -q -k "database"` |
| **Critère de validation** | Aucun `SQLITE_BUSY` dans les logs après 24h |
| **Réversibilité** | Supprimer la ligne |

### 1.2 Race condition WebSocket (ADR-003)

**État** : ✅ Implémenté le 11 juillet 2026 (verrou sur les mutations + snapshot défensif, sans verrou pendant les I/O) — test ciblé ajouté.

| | |
|---|---|
| **Fichiers** | `websocket_registry.py`, `main.py` (`websocket_endpoint()`) |
| **Changement** | Isoler le registre, ajouter `asyncio.Lock` + copie défensive du set avant itération |
| **Impact** | Aucun changement d'API. WebSocket continue normalement. |
| **Test** | `python -m pytest tests/ -q -k "websocket"` |
| **Critère de validation** | Aucun `RuntimeError: Set changed size during iteration` |
| **Réversibilité** | `git revert` |

### 1.3 Curseur ROWID unique (ADR-002)

**État** : ✅ Implémenté le 11 juillet 2026 — registre SQLite central, offsets persistants et monotones par consommateur.

| | |
|---|---|
| **Fichiers** | `integrations/imessage_cursor.py`, `imessage_reader.py`, `imessage.py`, `scripts/jarvis_daemon.py` |
| **Changement** | Centraliser les offsets dans `imessage_consumer_cursors`, avec progression atomique monotone et persistance après redémarrage. |
| **Impact** | Reader, bridge et daemon ne dépendent plus de curseurs uniquement en mémoire et ne se masquent pas mutuellement leurs messages. |
| **Test** | Tests unitaires de monotonie, isolation des consommateurs et reprise après redémarrage ; validation iMessage réelle à effectuer. |
| **Critère de validation** | Aucun ancien attribut de curseur mémoire ; pas de double traitement par consommateur et aucun masquage entre consommateurs. |
| **Réversibilité** | `git revert` |

### 1.4 Cycle main↔daemon (ADR-010)

| | |
|---|---|
| **Fichiers créés** | `pipeline.py` (nouveau) |
| **Fichiers modifiés** | `main.py` (déplacer `_process_message_internal`, `_process_voice_fast`, `_build_enriched_context`), `scripts/jarvis_daemon.py` (importer de `pipeline` au lieu de `main`) |
| **Impact** | Les fonctions déplacées gardent la même signature. `main.py` importe depuis `pipeline` et ré-exporte. |
| **Test** | `python -m pytest tests/ -q` — suite complète |
| **Critère de validation** | Aucune régression. Le daemon peut parler à JARVIS. |
| **Réversibilité** | `git revert` |

## Phase 2 — Database modulaire (Jour 2)

### Extraction des modules

17 nouveaux fichiers dans `database/`. `__init__.py` ré-exporte tout (backward compatible).

| Fichier | Contenu | Lignes estimées |
|---|---|---|
| `database/core.py` | `get_db`, `init_db`, `build_full_context`, migrations | ~600 |
| `database/conversations.py` | `save_message`, `create_conversation`, `delete_conversation` | ~200 |
| `database/people.py` | `upsert_person`, `get_all_people`, `get_people_sorted_by_recent` | ~300 |
| `database/relationships.py` | `upsert_relationship_profile`, events | ~150 |
| `database/episodes.py` | `save_episode`, `get_recent_episodes` | ~100 |
| `database/tasks.py` | `get_tasks`, `create_task`, `update_task_status`, `delete_task` | ~150 |
| `database/email.py` | `upsert_email_summary`, `get_recent_email_summaries` | ~150 |
| `database/notifications.py` | `create_notification`, `get_unread_notifications`, `mark_read` | ~150 |
| `database/facts.py` | `add_fact`, `get_facts`, `get_all_facts_summary` | ~200 |
| `database/patterns.py` | `save_mood`, `create_pattern`, `find_or_create_pattern` | ~150 |
| `database/screen_daemon.py` | `save_screen_activity`, `upsert_app_usage`, devices | ~250 |
| `database/sessions.py` | `create_session_row`, `verify_session`, `revoke_all_sessions` | ~150 |
| `database/push.py` | `upsert_push_subscription`, `delete_push_subscription` | ~50 |
| `database/embeddings.py` | `upsert_memory_embedding`, `get_all_memory_embeddings` | ~50 |
| `database/rituals.py` | daily_rituals, commitments, dnd | ~150 |
| `database/devops.py` | security_findings, perf_benchmarks | ~150 |
| `database/stats.py` | `get_cost_summary`, `get_daily_activity_stats` | ~250 |

### Validation

| | |
|---|---|
| **Test** | `python -m pytest tests/ -q` — suite complète |
| **Critère** | Même nombre de tests passants qu'avant. Aucun import cassé. |
| **Réversibilité** | `git revert` |

## Phase 3 — Event bus actif (Jour 3-4)

### 3.1 Événements Phase 1 (10 événements)

```python
class NotificationCreated(JarvisEvent): ...
class TaskCreated(JarvisEvent): ...
class TaskUpdated(JarvisEvent): ...
class ConversationUpdated(JarvisEvent): ...
class MessageSent(JarvisEvent): ...
class MemoryUpdated(JarvisEvent): ...
class PersonUpserted(JarvisEvent): ...
class EpisodeSaved(JarvisEvent): ...
class PatternDetected(JarvisEvent): ...
class FactAdded(JarvisEvent): ...
```

### 3.2 Émetteurs (dans database/)

Modifier les fonctions d'écriture pour émettre après chaque mutation importante.

### 3.3 Consommateurs

| Événement | Consommateur | Action |
|---|---|---|
| `NotificationCreated` | `broadcast_ws` | Push WebSocket (remplace le polling 30s) |
| `NotificationCreated` (urgent/high) | Daemon TTS | Notification vocale |
| `TaskCreated`, `TaskUpdated` | `broadcast_ws` | Mise à jour temps réel |
| `ConversationUpdated` | `broadcast_ws` | Sidebar conversations |
| `MemoryUpdated`, `PersonUpserted`, `EpisodeSaved`, `FactAdded` | `broadcast_ws` | Contexte mémoire |

### Validation

| | |
|---|---|
| **Fichier** | `tests/test_event_bus_integration.py` (nouveau) |
| **Test** | Vérifier que `create_notification()` émet bien un événement et que le handler WS le reçoit |
| **Critère** | Le polling 30s de l'UI peut être désactivé |
| **Réversibilité** | Ne pas enregistrer les handlers |

## Phase 4 — Routeurs FastAPI (Jour 5-7)

### Routeurs créés (12)

| Fichier | Routes | Lignes estimées | Risque |
|---|---|---|---|
| `api/router_auth.py` | `/api/auth/*` (8) | 200 | Bas |
| `api/router_people.py` | `/api/people/*` (15) | 500 | Moyen |
| `api/router_conversations.py` | `/api/conversations/*` (12) | 300 | Bas |
| `api/router_tasks.py` | `/api/tasks/*` (5) | 100 | Bas |
| `api/router_location.py` | `/api/location/*` (17) | 300 | Bas |
| `api/router_devices.py` | `/api/devices/*` (7) | 150 | Bas |
| `api/router_daemon.py` | `/api/audio-daemon/*`, `/api/control/*` (14) | 300 | Moyen |
| `api/router_devagent.py` | `/api/devagent/*` (8) | 200 | Bas |
| `api/router_quality.py` | `/api/quality/*`, `/api/migrations/*` (8) | 100 | Bas |
| `api/router_rituals.py` | `/api/rituals/*`, `/api/dnd/*` (7) | 150 | Bas |
| `api/router_recordings.py` | `/api/recordings/*`, `/api/memory/search-semantic` (6) | 130 | Bas |
| `api/router_misc.py` | Status, stats, costs, export, search, etc. (~15) | 300 | Bas |
| `api/ws_handler.py` | WebSocket (1) | 400 | Élevé |
| `api/frontend.py` | `_setup_frontend`, `_setup_pwa_frontend`, `_is_mobile_device` | 150 | Bas |
| `api/middleware.py` | `security_middleware` | 100 | Bas |

### main.py après (~200 lignes)

```python
from api.router_auth import router as auth_router
from api.router_people import router as people_router
# ...

app = FastAPI(lifespan=lifespan)
app.middleware("http")(security_middleware)
app.include_router(auth_router)
app.include_router(people_router, prefix="/api")
# ...
app.websocket("/ws")(websocket_endpoint)
_setup_frontend(app)
```

### Validation

| | |
|---|---|
| **Méthode** | `python -m pytest tests/ -q` après chaque routeur extrait |
| **Critère** | Tous les tests passent. `curl` sur chaque endpoint retourne 200. |
| **Réversibilité** | `git revert` par routeur |

## Phase 5 — Apple Data Service (Jour 8-10)

### Nouveau module

```
integrations/apple_data.py
├── class AppleDataService
│   ├── get_new_messages(since_rowid) → list[Message]
│   ├── get_conversation(handle, limit, since_rowid) → list[Message]
│   ├── get_all_conversation_stats() → list[ConversationStats]
│   ├── search_messages(query) → list[Message]
│   ├── resolve_handle(handle) → str
│   └── get_contacts() → list[Contact]
├── apple_epoch_to_datetime(ts) → datetime  ← UNE SEULE conversion
└── singleton apple_data
```

### Migration progressive des consommateurs

```
Jour 8 : IMessageBridge    → apple_data.get_new_messages()
Jour 8 : JarvisDaemon      → apple_data.get_new_messages()
Jour 9 : RelationshipAnalyzer → apple_data.get_conversation()
Jour 9 : ContactAnalytics  → apple_data.get_conversation()
Jour 10: TimelineGenerator  → apple_data.get_conversation()
Jour 10: message_predictor  → apple_data.get_conversation()
```

Chaque migration est un commit séparé, testable indépendamment.

### Validation

| | |
|---|---|
| **Fichier** | `tests/test_apple_data.py` (nouveau) |
| **Test** | Mock de `chat.db` — vérifier que `get_new_messages()` retourne les bons messages |
| **Critère** | `grep -r "chat.db" --include="*.py" | grep -v apple_data | grep -v test` → 0 résultat |
| **Réversibilité** | `git revert` par consommateur |

## Phase 6 — Frontend unifié + SDK Auth (Jour 11-15)

### 6.1 SDK d'auth partagé (Jour 11-12)

```
jarvis_auth/
├── package.json
├── src/
│   ├── client.ts              ← AuthClient (setup, unlock, verify, status)
│   ├── useLockGate.ts         ← Hook React
│   ├── LockGate.tsx           ← Composant wrapper
│   └── index.ts               ← exports
```

### 6.2 Nouvelle structure frontend (Jour 12-14)

```
frontend/
├── package.json               ← Next.js 15, React 19, Tailwind v4
├── src/
│   ├── app/
│   │   ├── layout.tsx         ← Détection device → layout desktop ou mobile
│   │   ├── auth/LockGate.tsx  ← Importé de jarvis_auth
│   │   ├── (desktop)/
│   │   │   └── ... (15 vues)
│   │   └── (mobile)/
│   │       └── ... (5 vues)
│   ├── components/            ← Composants partagés
│   ├── lib/
│   │   ├── api.ts             ← UN SEUL wrapper
│   │   └── offline/           ← IndexedDB + sync queue
│   └── hooks/
```

### 6.3 Coexistence

FastAPI sert les deux frontends. Le nouveau est prioritaire si présent. L'ancien est conservé comme fallback.

### Validation

| | |
|---|---|
| **Tests unitaires** | `cd frontend && pnpm test` |
| **Tests E2E** | Playwright desktop + mobile |
| **Critère** | Même comportement que l'ancien frontend sur toutes les pages |
| **Réversibilité** | Supprimer le nouveau frontend, FastAPI resert l'ancien |
