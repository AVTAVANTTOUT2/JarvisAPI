# 10 — Gouvernance des Événements (ADR-005-bis)

**Date** : 11 juillet 2026
**ADR** : ADR-005 (complément)
**Statut** : Implémenté pour les 10 événements de domaine de la Phase 3 — 14 juillet 2026

---

## Complément à l'ADR-005

L'ADR-005 définit l'activation de l'Event Bus. Ce document définit le **contrat d'événements** : comment les événements sont structurés, versionnés, et gouvernés.

## Structure d'un événement

Chaque événement doit implémenter l'interface `JarvisEvent` :

```python
@dataclass(frozen=True)
class JarvisEvent:
    type: str                        # alias historique conservé
    agent: str | None = None         # alias historique conservé
    data: Mapping | None = None      # payload historique, protégé en lecture seule
    timestamp: float = time.time()
    event_id: str = uuid4()
    version: int = 1
    source: str | None = None
    checksum: str = field(init=False)

    @property
    def event_type(self) -> str: ... # nom canonique

    @property
    def payload(self) -> dict: ...   # copie sérialisable du payload
```

`to_dict()` expose à la fois les champs canoniques (`event_type`, `payload`) et les alias (`type`, `data`, `agent`) afin de ne pas casser les événements techniques et le flux SSE existants.

### Règles

1. **Immuabilité** : Un événement émis n'est jamais modifié. Pour corriger une erreur, émettre un nouvel événement (ex: `TaskUpdated` avec `previous_version`).
2. **Versionnement** : Le champ `version` est incrémenté quand le schéma du `payload` change. Les consommateurs doivent gérer les anciennes versions ou les ignorer.
3. **Idempotence** : Un consommateur peut recevoir le même événement plusieurs fois (at-least-once delivery). Il doit être capable de le détecter via `event_id` et de l'ignorer.
4. **Traçabilité** : Chaque événement est loggé dans une table `event_log` (SQLite) pour le debugging et le replay.

## Catalogue activé en Phase 3

### Domaine : Messages / Conversations

| Événement | Émetteur | Payload |
|---|---|---|
| `message.sent` | `database.conversations` | `{conversation_id, message_id, role, content_preview}` |
| `conversation.updated` | `database.conversations` | `{conversation_id, changes}` |

### Domaine : People / Relations

| Événement | Émetteur | Payload |
|---|---|---|
| `person.upserted` | `database.people` | `{person_id, name, changes}` |
| `memory.updated` | `database.people` | `{context_id, type, description}` |

### Domaine : Tâches

| Événement | Émetteur | Payload |
|---|---|---|
| `task.created` | `database.tasks` | `{task_id, title, priority, due_date}` |
| `task.updated` | `database.tasks` | `{task_id, changes}` |

### Domaine : Notifications

| Événement | Émetteur | Payload |
|---|---|---|
| `notification.created` | `database.notifications` | `{notification_id, source, priority, title, content}` |

### Domaine : Mémoire

| Événement | Émetteur | Payload |
|---|---|---|
| `fact.added` | `database.facts` | `{fact_id, category, content, confidence}` |
| `pattern.detected` | `database.patterns` | `{pattern_id, type, description}` |
| `episode.saved` | `database.episodes` | `{episode_id, summary, importance}` |

Le catalogue technique historique (`voice.*`, `agent.*`, `tts.*`, `system.*`, etc.) reste accepté pour compatibilité. Les événements futurs liés à AppleDataService, Queue Engine, SearchService ou aux opérations de suppression ne sont pas déclarés comme implémentés tant que leurs producteurs et consommateurs n'existent pas.

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

La Phase 3 écrit chaque événement dans cette table avec `INSERT OR IGNORE`, ce qui rend le journal idempotent par `event_id`. Les lignes `processed_by IS NULL` sont consultables via `get_unprocessed_events()` et fournissent les données nécessaires à un futur rejeu. **Aucun rejeu automatique au redémarrage n'est encore implémenté** ; il relève du futur Queue Engine.
