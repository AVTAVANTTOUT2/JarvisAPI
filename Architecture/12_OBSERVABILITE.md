# 12 — Observabilité

**Date** : 11 juillet 2026
**Statut** : Proposé

---

## Endpoints de santé

### `GET /health`

État général de tous les services. Retourne un JSON avec le statut de chaque composant.

```json
{
  "status": "healthy",
  "uptime_seconds": 123456,
  "version": "3.2.0",
  "services": {
    "backend": {"status": "healthy", "response_time_ms": 12},
    "database": {"status": "healthy", "size_mb": 87, "wal_size_mb": 2},
    "apple_data": {"status": "healthy", "chat_db_accessible": true, "last_sync_ago_s": 45},
    "ai_service": {"status": "healthy", "last_call_ago_s": 120, "calls_today": 47},
    "event_bus": {"status": "healthy", "events_today": 1234, "queue_depth": 0},
    "queue_engine": {"status": "healthy", "pending": 3, "running": 1, "failed": 0},
    "search": {"status": "healthy", "embeddings_count": 5678},
    "notifications": {"status": "healthy", "sent_today": 23},
    "scheduler": {"status": "healthy", "jobs": 29, "last_run_ago_s": 12},
    "websocket": {"status": "healthy", "connections": 2}
  }
}
```

### `GET /ready`

Prêt à recevoir du trafic. Vérifie que les dépendances critiques sont disponibles.

```json
{
  "ready": true,
  "checks": {
    "database": true,
    "apple_data": true,
    "ai_service": true,
    "event_bus": true
  }
}
```

### `GET /live`

Le processus est vivant (pas de vérification de dépendances). Utilisé par Kubernetes/liveness probe.

```json
{"alive": true}
```

### `GET /metrics`

Métriques Prometheus-compatible (format texte).

```
# HELP jarvis_messages_total Total messages processed
# TYPE jarvis_messages_total counter
jarvis_messages_total{source="chat"} 1234
jarvis_messages_total{source="imessage"} 567
jarvis_messages_total{source="voice"} 89

# HELP jarvis_llm_calls_total Total LLM API calls
# TYPE jarvis_llm_calls_total counter
jarvis_llm_calls_total{model="deepseek-v4-flash"} 890
jarvis_llm_calls_total{model="deepseek-v4-pro"} 234

# HELP jarvis_queue_depth Current queue depth
# TYPE jarvis_queue_depth gauge
jarvis_queue_depth{queue="main"} 3
jarvis_queue_depth{queue="embeddings"} 1

# HELP jarvis_db_size_bytes SQLite database size
# TYPE jarvis_db_size_bytes gauge
jarvis_db_size_bytes 91234567

# HELP jarvis_ws_connections Current WebSocket connections
# TYPE jarvis_ws_connections gauge
jarvis_ws_connections 2
```

## Métriques à exposer

| Catégorie | Métrique | Type | Description |
|---|---|---|---|
| **Système** | `cpu_percent` | gauge | Utilisation CPU |
| | `memory_rss_mb` | gauge | Mémoire RSS du processus |
| | `uptime_seconds` | gauge | Temps depuis le démarrage |
| **API** | `http_requests_total` | counter | Requêtes HTTP par endpoint |
| | `http_request_duration_ms` | histogram | Latence par endpoint |
| | `http_errors_total` | counter | Erreurs 4xx/5xx |
| **WebSocket** | `ws_connections` | gauge | Connexions actives |
| | `ws_messages_total` | counter | Messages échangés |
| **Base de données** | `db_size_bytes` | gauge | Taille du fichier SQLite |
| | `db_wal_size_bytes` | gauge | Taille du WAL |
| | `db_queries_total` | counter | Requêtes SQL |
| | `db_slow_queries_total` | counter | Requêtes > 100ms |
| **Event Bus** | `events_total` | counter | Événements émis |
| | `events_dropped` | counter | Événements perdus (buffer plein) |
| | `event_handlers_duration_ms` | histogram | Temps de traitement par handler |
| **Queue Engine** | `queue_depth` | gauge | Tâches en attente |
| | `tasks_completed_total` | counter | Tâches terminées |
| | `tasks_failed_total` | counter | Tâches échouées |
| | `task_duration_ms` | histogram | Durée d'exécution par type |
| **AI Service** | `llm_calls_total` | counter | Appels LLM par modèle |
| | `llm_tokens_total` | counter | Tokens consommés |
| | `llm_cost_total` | counter | Coût cumulé |
| | `llm_cache_hits_total` | counter | Cache hits prompt |
| | `embeddings_generated_total` | counter | Embeddings générés |
| **Apple Data** | `imessage_sync_lag_s` | gauge | Délai depuis dernière sync |
| | `imessage_messages_imported_total` | counter | Messages importés |
| | `contacts_count` | gauge | Contacts dans SQLite |
| **Notifications** | `notifications_sent_total` | counter | Notifications envoyées par canal |
| **PWA** | `sw_installs_total` | counter | Installations PWA |
| | `sw_activated_total` | counter | Activations Service Worker |
| | `push_subscriptions` | gauge | Abonnements push actifs |
| **IndexedDB** | `idb_queue_depth` | gauge | Écritures en attente |
| | `idb_sync_success_total` | counter | Synchronisations réussies |
| | `idb_sync_failed_total` | counter | Synchronisations échouées |

## Alertes critiques

| Alerte | Condition | Sévérité | Action |
|---|---|---|---|
| `backend_down` | `/live` ne répond pas depuis 30s | CRITIQUE | Redémarrage auto (supervisor) + notification |
| `db_corruption` | SQLite retourne `SQLITE_CORRUPT` | CRITIQUE | Restauration depuis backup + notification |
| `chat_db_inaccessible` | `apple_data.chat_db_accessible = false` | CRITIQUE | Notification admin |
| `ai_service_unavailable` | 3 échecs consécutifs LLM | HAUTE | Fallback modèle + notification |
| `queue_stuck` | `queue_depth > 100` depuis 5 min | HAUTE | Scale workers + notification |
| `disk_full` | `disk_free_percent < 5%` | HAUTE | Purge automatique + notification |
| `llm_budget_exceeded` | `llm_cost_total > budget * 0.9` | MOYENNE | Notification + ralentissement fast model |
| `backup_failed` | Dernière backup > 25h | MOYENNE | Retry + notification |
| `sync_lag` | `imessage_sync_lag > 300s` | BASSE | Log warning |
| `ws_disconnected` | `ws_connections = 0` depuis 60s | BASSE | Log info |

## Dashboard `/health`

Page HTML accessible depuis l'interface JARVIS affichant :

- Statut de chaque service (vert/jaune/rouge)
- Graphiques de métriques (dernière heure, 24h, 7j)
- Logs d'erreurs récents
- File d'attente Queue Engine
- Dernières sauvegardes
