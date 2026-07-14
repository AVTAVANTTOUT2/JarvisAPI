# 05 — Plan de Migration

**Date** : 11 juillet 2026
**Durée totale** : 15 jours (6 phases)
**Principe** : Chaque phase est indépendante, testable, réversible, sans interruption de service.

## Vue d'ensemble

```
Phase 1 → Quick wins P0 (fait 14/07)                  Jour 1     [4 corrections]
Phase 2 → Database modulaire (fait 14/07)              Jour 2     [ADR-009]
Phase 3 → Pipeline + Event bus (fait 14/07)            Jour 3-4   [ADR-010, ADR-005]
Phase 4 → main.py découpé en routeurs (fait 14/07)     Jour 5-7   [ADR-008]
Phase 5 → Apple Data Service (fait 14/07)               Jour 8-10  [ADR-006]
Phase 6 → Frontend unifié + SDK auth                   Jour 11-15 [ADR-001, ADR-007]
```

## Phase 1 — Quick Wins P0 (Jour 1)

### 1.1 SQLite busy_timeout (ADR-004)

**État** : ✅ Implémenté le 11 juillet 2026 (`PRAGMA busy_timeout = 5000`) — test ciblé ajouté.

| | |
|---|---|
| **Fichier** | `database/core.py`, fonction `get_connection()` |
| **Changement** | Ajouter `conn.execute("PRAGMA busy_timeout = 5000")` |
| **Impact** | Aucun. Le mode WAL est déjà actif. |
| **Test** | `python -m pytest tests/ -q -k "database"` |
| **Critère de validation** | Aucun `SQLITE_BUSY` dans les logs après 24h |
| **Réversibilité** | Supprimer la ligne |

### 1.2 Race condition WebSocket (ADR-003)

**État** : ✅ Implémenté le 11 juillet 2026 (verrou sur les mutations + snapshot défensif, sans verrou pendant les I/O) — test ciblé ajouté.

| | |
|---|---|
| **Fichiers** | `websocket_registry.py`, `api/ws_handler.py` (`websocket_endpoint()`) |
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

**État** : ✅ Découplage implémenté le 11 juillet 2026 — contrat indépendant configuré par `main.py`, utilisé par les daemons.

| | |
|---|---|
| **Fichiers créés** | `pipeline.py` (nouveau) |
| **Fichiers modifiés** | `main.py` configure le contrat ; `jarvis_daemon.py` et `audio_daemon.py` importent uniquement `pipeline.py`. |
| **Impact** | Les signatures restent compatibles. Les implémentations ont été réparties dans les modules spécialisés de `api/` pendant la Phase 4. |
| **Test** | Tests de contrat et vérification statique de l'absence d'import de `main` dans les daemons. |
| **Critère de validation** | Aucun import inverse daemon → main ; délégation explicite des trois points d'entrée. |
| **Réversibilité** | `git revert` |

## Phase 2 — Database modulaire (Jour 2)

**État** : ✅ Terminée le 14 juillet 2026 — 25 modules d'implémentation après l'ajout normal de `event_log.py` en Phase 3. `database/__init__.py` est passé de 4 185 à 236 lignes et reste une façade de réexports rétrocompatibles.

### Extraction des modules

25 modules spécialisés dans `database/`. `__init__.py` ré-exporte l'API historique (backward compatible).

| Fichier | Contenu | Lignes estimées |
|---|---|---|
| `database/core.py` | `get_db`, `init_db`, `build_full_context` | 121 |
| `database/schema.py` | Schéma SQLite déclaratif | 666 |
| `database/migrations.py` | Migrations idempotentes et orchestration | 621 |
| `database/settings.py` | `get_setting`, `set_setting` | ~25 |
| `database/conversations.py` | `save_message`, `create_conversation`, `delete_conversation` | ~200 |
| `database/people.py` | `upsert_person`, `get_all_people`, `get_people_sorted_by_recent` | ~300 |
| `database/relationships.py` | `upsert_relationship_profile`, events | ~150 |
| `database/episodes.py` | `save_episode`, `get_recent_episodes` | ~100 |
| `database/tasks.py` | `get_tasks`, `create_task`, `update_task_status`, `delete_task` | ~150 |
| `database/email.py` | `upsert_email_summary`, `get_recent_email_summaries` | ~150 |
| `database/notifications.py` | `create_notification`, `get_unread_notifications`, `mark_read` | ~150 |
| `database/event_log.py` | Journal idempotent et lecture des événements non traités | 86 |
| `database/facts.py` | `add_fact`, `get_facts`, `get_all_facts_summary` | ~200 |
| `database/patterns.py` | `save_mood`, `create_pattern`, `find_or_create_pattern` | ~150 |
| `database/screen_daemon.py` | `save_screen_activity`, `upsert_app_usage`, devices | ~250 |
| `database/sessions.py` | `create_session_row`, `verify_session`, `revoke_all_sessions` | ~150 |
| `database/push.py` | `upsert_push_subscription`, `delete_push_subscription` | ~50 |
| `database/embeddings.py` | `upsert_memory_embedding`, `get_all_memory_embeddings` | ~50 |
| `database/conversation_turns.py` | Tours de parole, labels et association aux personnes | ~60 |
| `database/rituals.py` | daily_rituals, commitments, dnd | ~150 |
| `database/devops.py` | security_findings, perf_benchmarks | ~150 |
| `database/stats.py` | `get_cost_summary`, `get_daily_activity_stats` | ~250 |
| `database/school.py` | Documents scolaires | 32 |
| `database/location_helpers.py` | Localisation, lieux, visites et trajets | 420 |
| `database/devagent.py` | Persistance du DevAgent | 377 |

### Validation

| | |
|---|---|
| **Test** | `python -m pytest tests/ jarvis/tests agents/devagent -q` — suite backend complète |
| **Critère** | Même nombre de tests passants qu'avant. Aucun import cassé. |
| **Réversibilité** | `git revert` |

## Phase 3 — Event bus actif (Jour 3-4)

**État** : ✅ Terminée et validée le 14 juillet 2026. Le contrat historique `JarvisEvent(type, agent, data, timestamp)` reste compatible et porte désormais les champs canoniques `event_id`, `event_type`, `version`, `source`, `payload` et `checksum`.

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

Les fonctions d'écriture de `tasks`, `notifications`, `conversations`, `episodes`, `facts`, `patterns` et `people` émettent après commit. Les 10 types sont exercés par un test d'intégration commun.

### 3.3 Consommateurs

| Événement | Consommateur | Action |
|---|---|---|
| Tous les événements de domaine | `database/event_log.py` | Journal SQLite idempotent par `event_id` |
| Tous les événements de domaine | `websocket_registry.py` | Push WebSocket sur snapshot défensif |
| `NotificationCreated` (urgent/high) | `scripts/audio_daemon.py` | Notification vocale si le daemon est actif |
| Notifications et tâches | `pwa/EventSync.tsx` | Invalidation React Query via SSE, sans polling périodique |

### Validation

| | |
|---|---|
| **Fichiers** | `tests/test_event_bus_contract.py`, `tests/test_event_bus_integration.py` |
| **Test** | 4 tests : immutabilité/checksum/version, concurrence et isolation des handlers, drainage de `emit_nowait()`, puis les 10 mutations réelles avec journal + WebSocket |
| **Critère** | ✅ Polling notifications/tâches supprimé de la PWA ; suite backend complète à 542 passants, 1 ignoré ; build PWA réussi |
| **Réversibilité** | Ne pas enregistrer les handlers |

Le journal conserve `processed_by = NULL` tant qu'aucun moteur de rejeu n'existe. Les données sont donc disponibles pour un futur rejeu, mais aucun rejeu automatique au redémarrage n'est revendiqué dans cette phase. `Queue Engine`, `AI Service`, `/health` et `/metrics` restent des travaux Q4/futurs hors Phase 3.

## Phase 4 — Routeurs FastAPI (Jour 5-7)

**État** : ✅ Implémentée et validée le 14/07/2026. La bascule a conservé les 174 opérations HTTP, le WebSocket `/ws` et les 157 chemins OpenAPI. `main.py` contient 175 lignes d'assemblage.

### Routeurs créés (12)

| Fichier | Domaine principal | Lignes réelles | Risque initial |
|---|---|---|---|
| `api/router_auth.py` | `/api/auth/*` | 146 | Bas |
| `api/router_people.py` | `/api/people/*` | 325 | Moyen |
| `api/router_conversations.py` | `/api/conversations/*` | 170 | Bas |
| `api/router_tasks.py` | `/api/tasks/*` | 83 | Bas |
| `api/router_location.py` | `/api/location/*`, `/api/places/*` | 238 | Bas |
| `api/router_devices.py` | `/api/devices/*` | 216 | Bas |
| `api/router_daemon.py` | `/api/audio-daemon/*`, `/api/control/*` | 171 | Moyen |
| `api/router_devagent.py` | `/api/devagent/*` | 187 | Bas |
| `api/router_quality.py` | `/api/quality/*`, `/api/migrations/*` | 100 | Bas |
| `api/router_rituals.py` | `/api/rituals/*`, `/api/dnd/*` | 79 | Bas |
| `api/router_recordings.py` | `/api/recordings/*`, recherche sémantique | 97 | Bas |
| `api/router_misc.py` | Status, stats, costs, export, search, etc. | 447 | Bas |
| `api/ws_handler.py` | WebSocket (1) | 494 | Élevé |
| `api/frontend.py` | Montage desktop/PWA et détection mobile | 316 | Bas |
| `api/middleware.py` | `security_middleware` | 99 | Bas |

### main.py après (175 lignes)

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
| **Méthode** | Contrat routes/OpenAPI avant-après, tests d'architecture, suite backend complète, `compileall`, Ruff et `git diff --check` |
| **Critère** | ✅ 6 tests Phase 4 et suite complète : 548 passants, 1 ignoré. Hash des signatures routes et OpenAPI inchangés. Aucun serveur réel ni campagne `curl` n'a été lancé ; les routes représentatives sont exercées par `TestClient`. |
| **Réversibilité** | Deux commits de code séparés (contrat puis extraction), réversibles par `git revert` |

## Phase 5 — Apple Data Service (Jour 8-10)

**État** : ✅ Implémentée et validée le 14/07/2026. La portée livrée est l'accès iMessage à `chat.db`; les intégrations Calendar, Mail et Contacts restent des travaux distincts et ne sont pas présentées comme migrées.

### Nouveau module

```
integrations/apple_data.py
├── class AppleDataService
│   ├── get_new_messages(since_rowid) → list[Message]
│   ├── get_recent_messages(), count_messages(), health()
│   ├── get_conversation(handle, limit, since_rowid) → list[Message]
│   ├── get_all_conversation_stats() → list[ConversationStats]
│   ├── search_messages(query) → list[Message]
│   ├── resolve_handle(handle) → str
│   └── get_contacts() → list[Contact]
├── apple_epoch_to_datetime(ts) → datetime  ← UNE SEULE conversion
└── singleton apple_data
```

### Migrations réalisées

| Consommateur | Chemin migré |
|---|---|
| `IMessageBridge`, `JarvisDaemon` | `get_new_messages()` et `get_max_rowid()` |
| `IMessageReader` | conversations, recherche, statistiques et contacts ; les analyseurs relationnels l'utilisent déjà |
| `IMessageImporter`, backfill, diagnostics | unique ouverture `connect_readonly()` du service |
| TV et lifespan API | messages récents et diagnostic `health()` |

Les adaptations conservent les contrats publics historiques et les chemins `jarvis.db` restent hors périmètre.

### Validation

| | |
|---|---|
| **Fichier** | `tests/test_apple_data.py` |
| **Test** | Base SQLite temporaire : lecture seule, conversions secondes/nanosecondes, conversations, recherche, statistiques et provider Contacts injecté |
| **Critère** | ✅ Garde-fou AST : aucune construction du chemin Messages ni `sqlite3.connect` visant `chat.db` hors `apple_data.py`; conversion canonique définie une seule fois |
| **Résultat** | ✅ 67 tests ciblés ; suite backend : 555 passants, 1 ignoré ; `compileall` et `git diff --check` réussis |
| **Réversibilité** | `git revert` par commit de Phase 5 |

## Phase 6 — Frontend unifié + SDK Auth (Jour 11-15)

**État** : ✅ Implémentée et validée localement le 14/07/2026. La validation CI de la branche reste à obtenir avant fusion ; la suite backend complète n'a pas pu être relancée localement car la compilation de PyAudio exige l'en-tête système `portaudio.h`.

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
│   │   ├── layout.tsx         ← Shell et LockGate importé de jarvis_auth
│   │   ├── page.tsx           ← Racine statique
│   │   └── [segment]/page.tsx ← 21 segments pré-générés
│   ├── components/
│   │   ├── UnifiedApp.tsx     ← Détection device → layout desktop ou mobile
│   │   └── MobileApp.tsx      ← Adaptateur des 5 vues pwa/src
│   ├── lib/
│   │   ├── api.ts             ← UN SEUL wrapper
│   │   └── device.ts          ← Sélection responsive testée
│   └── types/                 ← Contrats frontend partagés
├── e2e/                       ← Playwright desktop + mobile
└── public/                    ← manifest, icônes, SW sans données privées
```

### 6.3 Coexistence

FastAPI sert le frontend unifié en priorité. Le build Vite est conservé comme fallback racine et la PWA historique reste disponible sous `/m/`.

### Validation

| | |
|---|---|
| **Tests unitaires** | ✅ `cd frontend && pnpm test` — 9 passants |
| **Tests E2E** | ✅ Playwright — 3 passants (desktop, mobile authentifié, mobile fail-closed) |
| **Contrats serveur** | ✅ 4 tests FastAPI : priorité du build unifié, routes statiques, coexistence `/m/`, fallback Vite et wrapper API unique |
| **Builds** | ✅ Next.js 15 unifié (25 pages), Vite historique et Next.js 14 historique |
| **Critère** | ✅ Vues desktop et mobile réutilisées, client API/auth partagé et contenu privé masqué avant authentification |
| **Réversibilité** | Supprimer le nouveau frontend, FastAPI resert l'ancien |

Le Service Worker unifié ne met en cache que les assets publics du shell (`/_next/static` et `/icons`) : aucune réponse `/api/*`, page HTML ou donnée utilisateur n'est persistée par ce cache. Les builds `web/` et `pwa/` restent volontairement présents pour le rollback ; leurs manifestes ne sont pas la source de dépendances du frontend unifié.
