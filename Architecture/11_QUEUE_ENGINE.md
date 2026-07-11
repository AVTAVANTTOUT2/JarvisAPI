# 11 — Queue Engine (ADR-012)

**Date** : 11 juillet 2026
**ADR** : ADR-012
**Statut** : Proposé

---

## Problème

Actuellement, les traitements lourds (résumé IA, embeddings, analyse relationnelle, timeline) sont exécutés de manière synchrone ou via des `asyncio.create_task` sans file d'attente structurée. En cas d'erreur, il n'y a pas de retry ni de persistance.

## Solution

Créer un **Queue Engine** — une file de traitement persistante qui garantit l'exécution ordonnée, avec retry, priorités, et journalisation.

## Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                         QUEUE ENGINE                              │
├──────────────────────────────────────────────────────────────────┤
│                                                                    │
│  ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐   │
│  │ Message  │    │ Résumé   │    │Embeddings│    │ Timeline │   │
│  │ Importé  │───▶│   IA     │───▶│          │───▶│          │   │
│  └──────────┘    └──────────┘    └──────────┘    └──────────┘   │
│       │                                               │          │
│       │          ┌──────────┐    ┌──────────┐         │          │
│       │          │  Mémoire │◀───│  Notif.  │◀────────┘          │
│       │          └──────────┘    └──────────┘                    │
│       │                                               │          │
│       └───────────────────────────────────────────────┘          │
│                          │                                        │
│                     ┌────▼─────┐                                  │
│                     │ Recherche │                                  │
│                     └──────────┘                                  │
└──────────────────────────────────────────────────────────────────┘
```

## Structure d'une tâche

```python
@dataclass
class QueueTask:
    task_id: str           # UUID v4
    task_type: str         # "imessage.summarize", "embedding.generate"
    priority: int          # 0 (haute) à 100 (basse)
    payload: dict          # Données nécessaires au traitement
    status: str            # "pending" | "running" | "completed" | "failed"
    attempts: int          # Nombre de tentatives
    max_attempts: int      # Maximum avant abandon
    created_at: float
    started_at: float | None
    completed_at: float | None
    error: str | None
    result: dict | None
```

## Persistance

Table SQLite `queue_tasks` :

```sql
CREATE TABLE IF NOT EXISTS queue_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT UNIQUE NOT NULL,
    task_type TEXT NOT NULL,
    priority INTEGER NOT NULL DEFAULT 50,
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    created_at REAL NOT NULL,
    started_at REAL,
    completed_at REAL,
    error TEXT,
    result_json TEXT
);
CREATE INDEX idx_queue_status ON queue_tasks(status, priority);
```

## Cycle de vie

```
┌─────────┐   enqueue()   ┌──────────┐   dequeue()   ┌──────────┐
│ Émetteur│ ────────────▶ │  Queue   │ ────────────▶ │  Worker  │
└─────────┘               │  Engine  │               └────┬─────┘
                          └──────────┘                    │
                               ▲                          │
                               │     update_status()      │
                               └──────────────────────────┘
```

## Garanties

| Garantie | Implémentation |
|---|---|
| **Reprise après crash** | Les tâches `pending` ou `running` sont reprises au redémarrage (`started_at` > 5 min = stuck → retry) |
| **Retries** | Backoff exponentiel : 1s, 4s, 16s. Max `max_attempts` (défaut 3). Après échec final : `status = 'failed'` + notification admin |
| **Priorités** | Traitées dans l'ordre `priority ASC, created_at ASC` |
| **Idempotence** | Le worker vérifie `task_id` avant d'exécuter. Si déjà `completed`, skip. |
| **Journalisation** | Chaque transition de statut est loggée |
| **Métriques** | `queue_depth`, `tasks_completed`, `tasks_failed`, `avg_processing_time` exposées via `/health` |

## Tâches définies

| Type | Priorité | max_attempts | Description |
|---|---|---|---|
| `imessage.summarize` | 50 | 3 | Résumé IA d'un batch de messages importés |
| `embedding.generate` | 60 | 2 | Génération d'embedding pour un message/épisode |
| `timeline.generate` | 70 | 2 | Génération de timeline pour un contact |
| `relationship.analyze` | 70 | 3 | Analyse relationnelle DeepSeek |
| `memory.extract` | 50 | 3 | Extraction de faits/patterns depuis un message |
| `notification.send` | 0 | 2 | Envoi de notification (push, TTS) |
| `backup.run` | 90 | 1 | Sauvegarde SQLite |
| `maintenance.purge` | 95 | 1 | Purge de rétention |
| `search.reindex` | 80 | 2 | Réindexation FTS5 + embeddings |
