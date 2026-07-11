# 16 — Contrats API

**Date** : 11 juillet 2026
**Statut** : Proposé

---

## Politique de versionnement

### REST API

- **Version dans le path** : `/api/v1/tasks`, `/api/v2/tasks`
- **Version courante** : `v1` (implicite — les endpoints sans `/v1/` sont `v1`)
- **Rétrocompatibilité** : Une nouvelle version majeure (`v2`) est créée quand un breaking change est nécessaire. L'ancienne version (`v1`) est maintenue pendant 6 mois avec un header `Deprecation: sunset=YYYY-MM-DD`.
- **Ajout de champ** : Rétrocompatible — pas de nouvelle version.
- **Suppression de champ** : Breaking — nouvelle version majeure.
- **Changement de type** : Breaking — nouvelle version majeure.

### WebSocket

- **Version dans le handshake** : Le client envoie `{"type": "handshake", "version": 1}` au premier message.
- **Messages** : Chaque message a un champ `version`. Si le serveur émet une version que le client ne connaît pas, le client ignore le champ inconnu (forward compatibility).

## Format des réponses

### Succès

```json
{
  "ok": true,
  "data": { ... },
  "meta": {
    "version": 1,
    "timestamp": "2026-07-11T10:00:00Z"
  }
}
```

### Erreur

```json
{
  "ok": false,
  "error": {
    "code": "TASK_NOT_FOUND",
    "message": "Tâche 42 introuvable",
    "details": {
      "task_id": 42
    }
  },
  "meta": {
    "version": 1,
    "timestamp": "2026-07-11T10:00:00Z"
  }
}
```

## Codes d'erreur standard

| HTTP | Code interne | Signification |
|---|---|---|
| 400 | `VALIDATION_ERROR` | Requête mal formée |
| 401 | `UNAUTHORIZED` | Session invalide ou expirée |
| 403 | `FORBIDDEN` | Accès non autorisé à la ressource |
| 404 | `NOT_FOUND` | Ressource introuvable |
| 409 | `CONFLICT` | Conflit (ex: nom déjà utilisé) |
| 422 | `UNPROCESSABLE` | Données valides mais opération impossible |
| 428 | `SETUP_REQUIRED` | Aucun secret configuré |
| 429 | `RATE_LIMITED` | Trop de requêtes |
| 500 | `INTERNAL_ERROR` | Erreur serveur inattendue |
| 503 | `SERVICE_UNAVAILABLE` | Service temporairement indisponible |

## Pagination

Toutes les listes utilisent la pagination par curseur :

```json
// Requête
GET /api/conversations?limit=20&cursor=eyJpZCI6NDJ9

// Réponse
{
  "ok": true,
  "data": {
    "items": [...],
    "next_cursor": "eyJpZCI6NjJ9",
    "has_more": true,
    "total": 150
  }
}
```

## Rate Limiting

| Endpoint | Limite | Fenêtre |
|---|---|---|
| `/api/auth/unlock` | 5 | 15 minutes (anti-brute-force) |
| `/api/auth/*` | 20 | 1 minute |
| `/api/chat` (WebSocket) | 30 | 1 minute (messages) |
| `/api/location` (ingestion) | 60 | 1 minute |
| `/*` (tout le reste) | 300 | 1 minute |

## WebSocket — Contrat

### Messages client → serveur

| Type | Payload | Description |
|---|---|---|
| `handshake` | `{version: 1}` | Négociation de version |
| `text` | `{content: "...", conversation_id: 1}` | Message texte |
| `voice` | `Blob` (binaire) | Audio WebM |
| `action_confirm` | `{action: {...}}` | Confirmation d'action dangereuse |
| `switch_conversation` | `{conversation_id: 2}` | Changement de conversation |
| `new_conversation` | `{}` | Nouvelle conversation |

### Messages serveur → client

| Type | Payload | Description |
|---|---|---|
| `handshake_ok` | `{version: 1, session_id: "..."}` | Handshake accepté |
| `response` | `{content: "...", agent: "school", emotion: "warm"}` | Réponse JARVIS |
| `response_chunk` | `{content: "..."}` | Streaming progressif |
| `response_end` | `{}` | Fin du streaming |
| `action_request` | `{action: {...}}` | Demande de confirmation |
| `notification` | `{id: 1, title: "...", priority: "high"}` | Notification push |
| `conversation_updated` | `{id: 1, title: "..."}` | Mise à jour conversation |
| `error` | `{code: "...", message: "..."}` | Erreur |

## Évolution future

### Headers HTTP

```http
# Requête
Accept-Version: 1
X-Client-Version: 3.2.0
X-Client-Platform: ios|android|desktop|pwa

# Réponse
Content-Version: 1
Deprecation: sunset=2027-01-11
X-Server-Version: 3.2.0
```

### OpenAPI

À terme, la documentation API sera générée automatiquement via OpenAPI 3.1 :

```python
# FastAPI génère déjà /openapi.json
# À enrichir avec :
app.openapi_version = "3.1.0"
app.title = "JARVIS API"
app.description = "API de l'assistant personnel JARVIS"
app.version = "3.2.0"
```
