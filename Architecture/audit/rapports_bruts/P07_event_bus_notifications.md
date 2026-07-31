<!--
source_agent: bc-019fb867-1d2f-7ae3-86d5-badae2c975fb
agent_name: Event bus et notifications
agent_url: https://cursor.com/agents/bc-019fb867-1d2f-7ae3-86d5-badae2c975fb
agent_status: IDLE
created_at: 2026-07-31T13:39:53.598000+00:00
extracted_msg_index: 97
extracted_at: 2026-07-31T14:37:19.332762+00:00
-->

```
════════════════════════════════════════════════════════════════════════
AUDIT P07 — Event bus et notifications
MODE          : lecture seule (aucune mutation)
OWNER         : P07 (incl. websocket_registry.py runtime)
DATE          : 2026-07-31
VERDICT       : PASS_WITH_FINDINGS
════════════════════════════════════════════════════════════════════════
```

## 1. Périmètre lu

| Fichier | Lignes | Rôle |
|---|---|---|
| `jarvis/event_bus.py` | 1–394 | Contrat `JarvisEvent`, bus, handlers, history, SSE helpers |
| `jarvis/events.py` | 1–221 | 10 classes domaine + assert catalogue |
| `jarvis/notification_service.py` | 1–98 | Priorité, dédup, push hook, emit |
| `jarvis/__init__.py` | 1–48 | Lazy `JARVISRouter` — pas de couplage bus/LLM |
| `database/event_log.py` | 1–86 | Persist `*`, lecture, unprocessed |
| `database/notifications.py` | 1–291 | Insert atomique + façade + push thread |
| `websocket_registry.py` | 1–47 | WS set + broadcast domaine |

Hors profondeur (preuves croisées uniquement) : émetteurs DB (`tasks/conversations/people/episodes/patterns/facts`), SSE `api/misc_integrations.py`, `api/lifespan.py` (`bind_loop`), handler TTS `scripts/audio_daemon.py` (Frontière P09/P10).

---

## 2. Checklist

| # | Item | Statut | Preuve |
|---|---|---|---|
| 1 | Classes `events.py` vs émetteurs | **PASS** (écarts mineurs) | 10/10 classes émises après commit ; trous sur delete/end non catalogués |
| 2 | `emit` / `emit_nowait` / `bind_loop` | **PASS_WITH_FINDINGS** | `lifespan` bind/unbind OK ; fallback `asyncio.run` dangereux ; `create_task(emit)` hors `_pending` |
| 3 | Handler sync SQLite sur loop asyncio | **FAIL** | `_persist_event` sync dans `gather` → I/O SQLite sur le thread de la loop |
| 4 | Échec handler n’arrête pas les autres | **PASS** | `_invoke_handler` catch `Exception` + `gather` |
| 5 | urgent/high → push/TTS | **PASS** (Frontière TTS) | push dans service ; TTS via `@event_bus.on("notification.created")` hors P07 |
| 6 | `get_unprocessed_events` sans replay auto | **PASS** (contrat tenu) | lecture seule ; **aucune** écriture `processed_by` dans le repo |

---

## 3. Cartographie classes ↔ émetteurs

| Classe | `EVENT_TYPE` | Émetteur | Après commit ? |
|---|---|---|---|
| `NotificationCreated` | `notification.created` | `notification_service.create` | Oui (`_insert` puis emit si `created`) |
| `TaskCreated` | `task.created` | `database/tasks.create_task` | Oui |
| `TaskUpdated` | `task.updated` | `update_task_status` | Oui (si rowcount>0) |
| `ConversationUpdated` | `conversation.updated` | `create_conversation` / `update_conversation` | Oui |
| `MessageSent` | `message.sent` | `save_message` | Oui |
| `MemoryUpdated` | `memory.updated` | `database/people` (`add_life_context`) | Oui |
| `PersonUpserted` | `person.upserted` | `upsert_person` | Oui |
| `EpisodeSaved` | `episode.saved` | `save_episode` | Oui |
| `PatternDetected` | `pattern.detected` | `create_pattern` / `find_or_create_pattern` | Oui |
| `FactAdded` | `fact.added` | `add_fact` | Oui |

`assert DOMAIN_EVENT_CLASSES == DOMAIN_EVENT_TYPES` (`events.py:221`) verrouille le catalogue.  
`DOMAIN_EVENT_TYPES = EVENT_TYPES[-10:]` (`event_bus.py:85`) : couplage positionnel fragile.

Trous d’émission (hors catalogue Phase 3, à noter) : `delete_task`, `end_conversation`, `update_pattern` seul — pas d’événement.

Écart sémantique : `find_or_create_pattern` ré-émet `PatternDetected` sur pattern **existant** (`patterns.py:45–47`).

---

## 4. Findings

### F-P07-01 — Handler sync SQLite sur la boucle asyncio
- **Sévérité** : HAUTE  
- **Checklist** : 3  
- **Où** : `database/event_log.py:12–35` + `jarvis/event_bus.py:320–335`  
- **Fait** : `@event_bus.on("*") def _persist_event` est synchrone (`get_db()` + INSERT). `_invoke_handler` l’exécute inline dans la coroutine ; `emit()` l’attend via `asyncio.gather`. Toute émission bloque le thread de la loop le temps du round-trip SQLite.  
- **Impact** : latence cross-cutting sur chat/voix/WS ; contention avec autres handlers async du même `emit`.  
- **Attendu** : `async` + `asyncio.to_thread` / executor, ou queue de journalisation découplée.

### F-P07-02 — Handler TTS attendu dans le même `gather` que le bus
- **Sévérité** : HAUTE (Frontière P09/P10 pour le corps TTS ; contrat bus = P07)  
- **Checklist** : 2, 5  
- **Où** : `scripts/audio_daemon.py:1973–1988` + `event_bus.emit` gather  
- **Fait** : `_speak_priority_notification` est async et `await audio_daemon._play_tts(...)`. `emit()` ne termine qu’après TTS. `emit_nowait` → task tracked → `wait_until_idle` / shutdown attendent la lecture audio.  
- **Impact** : couplage domaine→audio ; file d’émissions `notification.created` sérialisée derrière le TTS.  
- **Attendu** : handler fire-and-forget (`create_task`) ou file TTS dédiée, hors criticité du bus.

### F-P07-03 — Checksum ≠ sérialisation `event_log`
- **Sévérité** : MOYENNE  
- **Checklist** : 1 (intégrité contrat)  
- **Où** : `event_bus.py:130–141` vs `event_log.py:32`  
- **Fait** : checksum = `json.dumps(..., separators=(",", ":"))` ; persist = `json.dumps(...)` séparateurs défaut `(", ", ": ")`. Re-hash de `payload_json` ≠ `checksum`. Checksum ne couvre que le payload, pas `event_type`/`event_id`/`version`/`source`/`timestamp`.  
- **Impact** : vérification d’intégrité future cassée ; métadonnées non protégées.

### F-P07-04 — `emit_nowait` fallback `asyncio.run`
- **Sévérité** : MOYENNE  
- **Checklist** : 2  
- **Où** : `event_bus.py:345–361`  
- **Fait** : sans loop courante et sans `_loop` bound → `asyncio.run(self.emit(event))`. Nouveau loop vs `connected_ws_lock` / handlers async → risque « Lock bound to a different event loop ». OK en prod après `bind_loop` (`api/lifespan.py:56`) ; fragile avant bind / hors process app.  
- **Parallèle** : `asyncio.create_task(event_bus.emit(...))` (agents/tts/audio) **contourne** `_pending` → `wait_until_idle` incomplet.

### F-P07-05 — Dédup notifications non unique sous concurrence
- **Sévérité** : MOYENNE  
- **Checklist** : 5 (politique notifs)  
- **Où** : `notifications.py:45–71` ; index `idx_notif_dedup` non-UNIQUE (`schema.py:241`, `migrations.py:744–746`)  
- **Fait** : `INSERT…WHERE NOT EXISTS` dans une transaction ; deux connexions concurrentes peuvent toutes deux insérer. Index = lookup, pas contrainte.  
- **Atténuation** : usage solo local ; fenêtre 300s ; tests mono-thread OK (`test_notification_service.py`).

### F-P07-06 — Replay : API lecture sans moteur ni marquage
- **Sévérité** : BASSE (attendu documenté)  
- **Checklist** : 6  
- **Où** : `event_log.py:73–86` ; colonnes `processed_by`/`processed_at`/`error` (`schema.py:186–188`)  
- **Fait** : `get_unprocessed_events` liste `processed_by IS NULL`. **Aucune** fonction `mark_processed` dans le dépôt (`rg processed_by\s*=` → 0). Pas de replay au startup (`lifespan` bind seulement). Conforme ADR / `Architecture/10_GOUVERNANCE_EVENTS.md`.  
- **Écart** : `get_unprocessed_events` ne parse pas `payload_json` (contrairement à `get_event_log`).

### F-P07-07 — SSE : drop abonnés lents ; history RAM only
- **Sévérité** : BASSE  
- **Checklist** : 2 (diffusion)  
- **Où** : `event_bus.py:294–311` ; `api/misc_integrations.py:98–117`  
- **Fait** : `QueueFull` → unsubscribe (perte d’événements). History 200 en RAM, rejouée 30 au connect SSE — **pas** depuis `event_log`. Redémarrage = trou SSE ; journal DB orphelin côté UI temps réel.

### F-P07-08 — Types inconnus : warn puis emit quand même
- **Sévérité** : BASSE  
- **Où** : `event_bus.py:115–119` vs `on()` qui `raise` (`227–228`)  
- **Fait** : émission permissive ; abonnement strict. Clients peuvent recevoir des types hors catalogue.

---

## 5. Points validés (PASS)

**Concurrence / isolation handlers** — `test_event_bus_contract.py:47–77` : fast handler avance pendant que slow attend ; failing handler isolé ; wildcard sync OK.

**Immuabilité / checksum payload / aliases** — frozen dataclass ; `to_dict()` expose `event_type`+`type`, `payload`+`data` ; test unitaire OK.

**`bind_loop` cycle de vie** — `lifespan` : `bind_loop` au start, `wait_until_idle` + `unbind_loop` au stop.

**Push urgent/high** — uniquement si `created` ; dédup skip push+event (`notification_service.py:58–74`) ; thread daemon best-effort (`notifications.py:115–144`) — chiffrement push hors scope (→ P02).

**WS broadcast domaine** — `websocket_registry.py:44–47` sur `DOMAIN_EVENT_TYPES` ; snapshot hors lock ; morts retirés. P07 OWN runtime ; P01 = assemblage imports uniquement.

**`jarvis/__init__.py`** — pas d’import bus/LLM au load ; `__getattr__` lazy `JARVISRouter` — conforme CLAUDE.md.

**Idempotence journal** — `INSERT OR IGNORE` sur `event_id` ; re-emit même UUID ne duplique pas (`test_event_bus_integration.py:113–120`).

---

## 6. Flux runtime (synthèse)

```
DB mutation (commit)
  └─ emit_nowait(DomainEvent)
        ├─ [thread] run_coroutine_threadsafe → loop liée
        └─ [async]  create_task(emit)
              emit():
                history RAM
                queues SSE (drop si full)
                gather handlers:
                  ├─ _persist_event (SYNC SQLite)     ← F-P07-01
                  ├─ broadcast_domain_event (WS)
                  └─ _speak_priority_notification    ← F-P07-02 / P09
```

SSE `/api/events/stream` : history 30 + queue `*` (tous types, pas seulement domaine).

---

## 7. Frontières

| Sujet | Owner | Note |
|---|---|---|
| `websocket_registry.py` runtime | **P07** | P01 : montage/assemblage seulement |
| `push.py` crypto / VAPID | **P02** | P07 : appel `_dispatch_push_notification` seul |
| TTS / `audio_daemon` conso notifs | **P09/P10** | Handler enregistré sur le bus : contrat d’attente = finding P07 |
| SSE route `events_stream` | API (voisin) | Consommateur du bus ; comportement Queue/history audité ici |
| Émetteurs `database/*.py` | DB / P03 | Contrats d’émission vérifiés ; SQL métier non re-audité |

---

## 8. Matrice checklist → verdict local

| Checklist | Verdict |
|---|---|
| 1 Classes vs émetteurs | PASS (+ F-P07-03 intégrité, PatternDetected re-emit) |
| 2 emit / nowait / bind | PASS_WITH_FINDINGS (F-P07-02, F-P07-04, F-P07-07) |
| 3 Sync SQLite sur loop | **FAIL** (F-P07-01) |
| 4 Isolation handlers | PASS |
| 5 urgent/high push/TTS | PASS (push P07 ; TTS délégué, couplage gather = finding) |
| 6 Unprocessed sans replay | PASS (F-P07-06 = dette assumée) |

---

## 9. Verdict global

**PASS_WITH_FINDINGS** — architecture Phase 3 globalement respectée (10 événements typés, emit après commit, isolation handlers, dédup+push, journal idempotent, replay non implémenté comme documenté).

Bloquants de production à traiter en priorité :
1. **F-P07-01** — persist sync sur loop  
2. **F-P07-02** — TTS dans le criticité path de `emit`  
3. **F-P07-03** — aligner sérialisation checksum / `payload_json`

Pas de correctif dans ce passage (audit lecture seule).