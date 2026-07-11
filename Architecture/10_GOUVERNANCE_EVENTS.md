# 10 — Gouvernance des Événements (ADR-005-bis)

**Date** : 11 juillet 2026
**ADR** : ADR-005 (complément)
**Statut** : Proposé

---

## Complément à l'ADR-005

L'ADR-005 définit l'activation de l'Event Bus. Ce document définit le **contrat d'événements** : comment les événements sont structurés, versionnés, et gouvernés.

## Structure d'un événement

Chaque événement doit implémenter l'interface `JarvisEvent` :

```python
@dataclass(frozen=True)  # immuable
class JarvisEvent:
    event_id: str          # UUID v4
    event_type: str        # Nom qualifié : "notification.created"
    version: int           # Version du schéma (commence à 1)
    timestamp: float       # time.time() UTC
    source: str            # Module émetteur : "email_watcher"
    payload: dict          # Données spécifiques à l'événement
    checksum: str          # SHA256 du payload sérialisé
```

### Règles

1. **Immuabilité** : Un événement émis n'est jamais modifié. Pour corriger une erreur, émettre un nouvel événement (ex: `TaskUpdated` avec `previous_version`).
2. **Versionnement** : Le champ `version` est incrémenté quand le schéma du `payload` change. Les consommateurs doivent gérer les anciennes versions ou les ignorer.
3. **Idempotence** : Un consommateur peut recevoir le même événement plusieurs fois (at-least-once delivery). Il doit être capable de le détecter via `event_id` et de l'ignorer.
4. **Traçabilité** : Chaque événement est loggé dans une table `event_log` (SQLite) pour le debugging et le replay.

## Catalogue des événements principaux

### Domaine : Messages / Conversations

| Événement | Émetteur | Payload |
|---|---|---|
| `message.sent` | Pipeline | `{conversation_id, message_id, role, content_preview}` |
| `message.received` | iMessage Bridge | `{handle, text, timestamp}` |
| `conversation.created` | Conversation Service | `{conversation_id, title}` |
| `conversation.updated` | Conversation Service | `{conversation_id, changes}` |
| `conversation.deleted` | Conversation Service | `{conversation_id}` |

### Domaine : People / Relations

| Événement | Émetteur | Payload |
|---|---|---|
| `person.upserted` | Memory Service | `{person_id, name, changes}` |
| `person.renamed` | Memory Service | `{person_id, old_name, new_name}` |
| `relationship.updated` | Memory Service | `{person_id, profile_changes}` |
| `relationship.event_added` | Timeline Service | `{person_id, event_type, summary}` |
| `running_gag.detected` | Memory Service | `{person_id, pattern, occurrences}` |

### Domaine : Tâches

| Événement | Émetteur | Payload |
|---|---|---|
| `task.created` | Task Service | `{task_id, title, priority, due_date}` |
| `task.updated` | Task Service | `{task_id, changes}` |
| `task.completed` | Task Service | `{task_id, completed_at}` |
| `task.deleted` | Task Service | `{task_id}` |

### Domaine : Notifications

| Événement | Émetteur | Payload |
|---|---|---|
| `notification.created` | Notification Service | `{notification_id, source, priority, title}` |
| `notification.read` | Notification Service | `{notification_id}` |

### Domaine : Mémoire

| Événement | Émetteur | Payload |
|---|---|---|
| `memory.fact_added` | Memory Service | `{fact_id, category, content, confidence}` |
| `memory.pattern_detected` | Memory Service | `{pattern_id, type, description}` |
| `memory.episode_saved` | Memory Service | `{episode_id, summary, importance}` |
| `memory.context_changed` | Memory Service | `{context_id, type, description}` |

### Domaine : Apple / iMessage

| Événement | Émetteur | Payload |
|---|---|---|
| `imessage.imported` | Apple Data Service | `{batch_size, new_messages, total_messages}` |
| `imessage.import_progress` | Apple Data Service | `{progress_pct, messages_processed}` |
| `imessage.sync_completed` | Apple Data Service | `{total_imported, duration_seconds}` |

### Domaine : Système

| Événement | Émetteur | Payload |
|---|---|---|
| `system.startup` | main.py | `{version, services_started}` |
| `system.shutdown` | main.py | `{uptime_seconds, reason}` |
| `system.error` | Tout module | `{module, error_type, traceback_preview}` |
| `system.health_check` | Health Service | `{service, status, response_time_ms}` |

## Cycle de vie d'un événement

```
┌─────────┐    emit()    ┌───────────┐   dispatch()   ┌─────────────┐
│ Émetteur│ ───────────▶ │ Event Bus │ ─────────────▶ │ Consommateur │
└─────────┘              └─────┬─────┘                └──────┬──────┘
                               │                             │
                               │ log()                       │ process()
                               ▼                             ▼
                        ┌─────────────┐            ┌────────────────┐
                        │ event_log   │            │ Action métier   │
                        │ (SQLite)    │            │ (DB write,      │
                        │             │            │  WS broadcast,  │
                        │ pour replay │            │  TTS, etc.)     │
                        └─────────────┘            └────────────────┘
```

## Compatibilité

- **Ajout de champ** : Compatible (les anciens consommateurs ignorent les nouveaux champs). Ne pas incrémenter `version`.
- **Suppression de champ** : Incompatible. Créer un nouvel événement avec `version += 1`.
- **Renommage de champ** : Incompatible. Créer un nouvel événement.
- **Changement de type** : Incompatible. Créer un nouvel événement.

## Log d'événements (table SQLite)

```sql
CREATE TABLE IF NOT EXISTS event_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT UNIQUE NOT NULL,
    event_type TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    timestamp REAL NOT NULL,
    source TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    checksum TEXT NOT NULL,
    processed_by TEXT,  -- NULL = pas encore traité
    processed_at REAL,
    error TEXT
);
CREATE INDEX idx_event_log_type ON event_log(event_type);
CREATE INDEX idx_event_log_timestamp ON event_log(timestamp);
```

Cette table permet le **replay** : en cas de crash, les événements non traités (`processed_by IS NULL`) sont rejoués au redémarrage.
