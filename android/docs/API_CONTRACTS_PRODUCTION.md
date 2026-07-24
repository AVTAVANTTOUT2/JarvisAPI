# API_CONTRACTS_PRODUCTION — Contrats backend pour Companion Android

**Date :** 2026-07-16  
**Sources :** `api/router_*.py`, `api/middleware.py`, `api/ws_handler.py`, `auth.py`, `tests/test_mobile_*.py`, client Kotlin `JarvisApiService` / `VoiceRepository`.  
**Règle :** ne pas modifier ces contrats silencieusement. Tout ajout Bearer / endpoint doit être testé (pytest) et documenté ici.

---

## 1. Authentification

### Mécanismes

| Mécanisme | Transport | Usage |
|-----------|-----------|--------|
| Session web | Cookie `jarvis_session` (nom = `config.SESSION_COOKIE_NAME`) | UI web / PWA |
| Jeton mobile natif | `Authorization: Bearer {token}` | Companion Android |
| Localisation legacy | `X-Location-Token` ou `?token=` si `LOCATION_API_TOKEN` non vide | Shortcuts iOS |
| Device daemon | `X-Device-Token` | `jarvis_agent.py` — **distinct** du Companion |

**Il n’existe pas** de header `X-Mobile-Token` : le contrat mobile est uniquement `Authorization: Bearer`.

### Gate session (`api/middleware.py`)

Fail-closed :

- `428` + `{ "error": "setup_required" }` si PIN non configuré ;
- `401` + `{ "error": "unauthorized" }` si session absente/invalide.

Bypass actuels (Bearer géré dans le handler, pas le cookie) :

- `/api/auth/*`
- `POST /api/location`, `POST /api/location/batch`
- `POST /api/devices/register`
- `POST /api/mobile/pairing/complete|session|push-token|capabilities|voice/turn`
- `POST /api/devices/{id}/heartbeat|screen`

### Écart Companion (1.2.0)

`POST /api/mobile/session` pose un cookie de session, mais OkHttp Android **n’a pas de CookieJar** → les routes métier session-only sont **inaccessibles**.

**Décision Vague 1 :** étendre le middleware pour accepter un Bearer mobile valide **en plus** du cookie sur un ensemble whitelist de routes métier (lecture d’abord). Voir section 10.

Mutations web par cookie : `X-CSRF-Token` lié à la session obligatoire et contrôle exact de l’Origin/Referer (schéma+hôte+port, ou `CSRF_ALLOWED_ORIGINS`). Les mutations Bearer mobiles n’utilisent pas le cookie et ne passent donc pas par ce contrôle CSRF.

---

## 2. Domaine Mobile

### `POST /api/mobile/pairing/start`

| Champ | Valeur |
|-------|--------|
| Auth | Cookie session (admin web) |
| Body | `{}` |
| Réponse 200 | `{ "code": "123456", "expires_at": "<ISO>" }` |
| Offline | Non applicable (génération sur Mac) |
| Android | Non appelé |

### `POST /api/mobile/pairing/complete`

| Champ | Valeur |
|-------|--------|
| Auth | Aucune (bypass) |
| Body | `{ "code", "device_id", "name?", "model?", "app_version?" }` |
| Validation | code 6 chiffres ; `device_id` requis ≤ 128 |
| Réponse 200 | `{ "token", "device": { device_id, name, model } }` |
| Erreurs | `400` invalide ; `401` code expiré/utilisé |
| Idempotence | Un code ne consomme qu’une fois |
| Android | **Implémenté** |

### `POST /api/mobile/session`

| Champ | Valeur |
|-------|--------|
| Auth | Bearer mobile |
| Body | `{}` |
| Réponse 200 | `{ "ok": true, "device_id" }` + `Set-Cookie` session |
| Erreurs | `401` token révoqué/invalide |
| Side effect | `touch_mobile_device` |
| Android | **Implémenté** (cookie ignoré) |

### `POST /api/mobile/push-token`

| Champ | Valeur |
|-------|--------|
| Auth | Bearer |
| Body | `{ "token": "<FCM>" }` |
| Réponse | `{ "ok": true }` |
| Erreurs | `400` vide ; `401` |
| Feature flag | Push serveur si `FCM_SERVICE_ACCOUNT_FILE` + `FCM_PROJECT_ID` |
| Android | **Implémenté** si Firebase |

### `POST /api/mobile/capabilities`

| Champ | Valeur |
|-------|--------|
| Auth | Bearer |
| Body | bools filtrés : `push`, `background_location`, `wake_word` |
| Réponse | `{ "ok": true, "capabilities": {...} }` |
| Android | **Implémenté** |

### `GET /api/mobile/devices`

| Champ | Valeur |
|-------|--------|
| Auth | Cookie session |
| Réponse | `{ "devices": [ ... revoked, capabilities, last_seen_at ... ] }` |
| Android | Admin web uniquement |

### `POST /api/mobile/devices/{device_id}/revoke`

| Champ | Valeur |
|-------|--------|
| Auth | Cookie session |
| Réponse | `{ "ok": true }` ou `404` |
| Side effect | Révoque token + FCM + sessions liées |
| Android | Non (révocation locale = clear Keystore seulement) |

### Heartbeat explicite

| Statut | **Absent** (`/api/mobile/heartbeat`) |
|--------|--------------------------------------|
| Remplacement | `verify_mobile_token` / `touch_mobile_device` sur chaque Bearer |
| Option Vague N | Ajouter heartbeat enrichi si besoin diagnostics |

---

## 3. Localisation

### `POST /api/location`

| Champ | Valeur |
|-------|--------|
| Auth | Bypass session ; Bearer mobile **ou** `X-Location-Token` **ou** ouvert si `LOCATION_API_TOKEN` vide |
| Body | `{ latitude, longitude, altitude?, accuracy?, speed?, heading?, source?, timestamp? }` |
| Réponse 200 | `{ place, place_id, arrived, departed, visit_id }` ou `{ skipped: true }` si tracking off |
| Erreurs | `400` coords ; `401` |
| Offline attendu | Client stocke en Room puis batch (Vague 2B) |
| Android | **Implémenté** (chemin unitaire conservé ; file offline prioritaire) |

### `POST /api/location/batch`

| Champ | Valeur |
|-------|--------|
| Auth | **Bearer mobile obligatoire** (device actif, non révoqué) |
| Body | `{ "points": [ { client_point_id, latitude, longitude, altitude?, accuracy?, speed?, bearing?, provider?, captured_at?, source? } ] }` |
| Limite | `LOCATION_BATCH_MAX_POINTS` (défaut **50**) → `400` si dépassé |
| Réponse 200 | `{ "accepted": [...], "duplicates": [...], "rejected": [{ client_point_id, reason }] }` |
| Idempotence | UNIQUE `(device_id, client_point_id)` via table `location_point_dedup` |
| Erreurs | `401` sans Bearer ; `400` lot trop grand / body invalide |
| Offline attendu | Android stocke en Room puis drain via `LocationSyncWorker` |
| Android | **Implémenté** (Vague 2B) |

### Lectures (`GET /api/location/*`, places, visits, trips)

Auth cookie/session (ou Bearer après extension Vague 1). Feature `LOCATION_TRACKING`.

---

## 4. Audio mobile — `POST /api/mobile/voice/turn`

| Champ | Valeur |
|-------|--------|
| Auth | Bearer |
| Content-Type | `multipart/form-data` |
| Fields | `audio` (fichier) ; `conversation_id` (form int, optionnel) |
| Formats | M4A, WAV, WebM, MP3, OGG (conteneurs encodés) |
| Limites | `MOBILE_VOICE_MAX_BYTES` (défaut 5 Mo) ; `MOBILE_VOICE_MIN_BYTES` (1000) |
| Réponse 200 | `conversation_id`, `transcript`, `response_text`, `audio_mime_type`, `audio_base64`, `audio_url`, moteurs STT/TTS, `emotion`, `agent`, `tts_error?` |
| Erreurs | `400` audio/echo ; `401` ; `413` ; `415` ; `429` tour concurrent ; `503` STT/TTS ; `504` timeout |
| Offline | Non supporté serveur — file client chiffrée (cible voix offline) |
| Transport | Un tour = une requête HTTP (pas de stream WS) |
| Android | **Implémenté** (`VoiceRepository`) |

---

## 5. Chat / Conversations

### Chat Android natif (Vague 2)

| Transport | Endpoint / type | Auth | Payload | Réponse | Idempotence | Preuve |
|-----------|-----------------|------|---------|---------|-------------|--------|
| HTTP | `POST /api/mobile/conversations` | Bearer | `{title?}` | `{conversation_id, title, agent}` | — | `api/router_mobile_chat.py` |
| HTTP | `POST /api/mobile/chat` | Bearer | `{content, conversation_id?, client_message_id?}` | `{response_text, emotion, action?, needs_confirmation, idempotent_replay}` | `client_message_id` + device UNIQUE | `mobile_chat_dedup` |
| HTTP | `POST /api/mobile/chat/confirm` | Bearer | `{conversation_id, confirmed}` | résultat action | — | même fichier |
| HTTP | `GET /api/conversations` | Bearer GET | query archived/limit | liste | — | Vague 1 |
| HTTP | `GET /api/conversations/{id}` | Bearer GET | — | messages | — | Vague 1 |
| HTTP | `PATCH /api/conversations/{id}` | Bearer Vague 2 | title/pinned/archived | `{ok}` | — | middleware whitelist |
| HTTP | `DELETE /api/conversations/{id}` | Bearer Vague 2 | — | `{ok}` | — | middleware |
| HTTP | `POST .../pin`, `.../archive` | Bearer Vague 2 | — | `{ok}` | — | middleware |
| WS | `/ws` | Cookie **ou** `Authorization: Bearer` handshake | `text`, `chunk`/`done` | streaming | — | `api/ws_handler.py` |

**Règles :**

- Token **jamais** en query string.
- Fallback HTTP `/api/mobile/chat` = réponse complète non streamée (offline / WS down).
- Streaming = WebSocket uniquement.
- Mutations admin (`/api/mobile/devices`, quality, etc.) **non** ouvertes au Bearer.

### Routes REST conversations (historique)

Toutes les routes actuelles : **auth session cookie** (Bearer lecture Vague 1 ; mutations conversation Vague 2).

| Method | Path | Body / Query | Réponse |
|--------|------|--------------|---------|
| GET | `/api/conversations` | `archived=false`, `limit=50` | `{ conversations: [...] }` |
| GET | `/api/conversations/search` | `q`, `limit=20` | `{ results, count }` |
| GET | `/api/conversations/{id}` | — | conv + `messages[]` + `documents[]` |
| PATCH | `/api/conversations/{id}` | `title?`, `pinned?`, `archived?`, `tags?` | `{ ok: true }` |
| DELETE | `/api/conversations/{id}` | — | `{ ok: true }` |
| POST | `.../archive` | — | `{ ok: true }` |
| POST | `.../pin` | — | `{ ok: true, pinned }` |
| POST | `.../upload` | multipart file | meta document |

**Message object :** `id`, `conversation_id`, `role`, `content`, `agent`, `model`, tokens, `cost`, `created_at`.

### Envoi texte

| Statut | **Pas de REST** pour poster un message utilisateur |
|--------|-----------------------------------------------------|
| Canal | `WS /ws` type `text` |
| Mobile actuel | voice turn seulement |
| À créer (Vague chat) | `POST /api/mobile/chat` ou message REST + auth Bearer, idéalement avec `client_message_id` idempotent |

### Offline attendu (cible)

- File Room `pending_chat_messages`
- Retry + dédup par `client_message_id`
- UI « en attente »

---

## 6. Briefing & notifications

### `GET /api/briefing`

| Champ | Valeur |
|-------|--------|
| Auth | Session (→ Bearer Vague 1) |
| Query | `kind=morning|evening` |
| Réponse | `{ "kind", "content" }` — **génération LLM à la demande** |
| Erreurs | `500` |
| Offline | Pas de cache serveur dédié — cache Room côté Android recommandé |
| Feature | Dépend Mail/Calendar/Mac |

### Briefing persisté table `daily_briefings`

Pas d’endpoint GET public aujourd’hui. Option future : `GET /api/briefings/today`.

### Notifications

| Method | Path | Auth | Notes |
|--------|------|------|-------|
| GET | `/api/notifications` | Session → Bearer | Non lues, tri priorité |
| GET | `/api/notifications/all` | Session → Bearer | `limit=50` |
| POST | `/api/notifications/{id}/read` | Session → Bearer | |
| POST | `/api/notifications/read-all` | Session → Bearer | `{ marked }` |

**Object :** `id`, `source`, `title`, `content`, `priority`, `read`, `email_id`, `created_at`.

Push FCM natif : enregistrement via `/api/mobile/push-token` ; envoi serveur pour `urgent`/`high` si FCM configuré.

---

## 7. Agenda

| Method | Path | Auth | Request | Réponse |
|--------|------|------|---------|---------|
| GET | `/api/calendar` | Session → Bearer | `start`, `end` ISO **requis** | `{ events, count }` |
| POST | `/api/calendar` | Session → Bearer mutations | `title/summary`, `start`, `end?`, `location?`, `notes?` | `{ ok, ... }` ou `500` |
| POST | `/api/calendar/test` | Session | — | Event test |

**Event :** `{ id, title, start, end, location, notes, calendar }`.

| Limitation | Pas de PATCH/DELETE événement documentés → Android ne doit pas promettre édition/suppression |
| Erreurs | `400` plage ; `503` Calendar.app |
| Offline | Cache fenêtre ; file create pending (Vague agenda) |

---

## 8. Tâches

| Method | Path | Auth | Body | Réponse |
|--------|------|------|------|---------|
| GET | `/api/tasks` | Session → Bearer | `status?=all\|todo\|doing\|done` | `{ tasks }` |
| POST | `/api/tasks` | Mutations | `title`, `description?`, `priority?`, `due_date?`, `category?` | `{ task }` |
| PATCH | `/api/tasks/{id}` | Mutations | `{ status }` **uniquement** | `{ task }` |
| DELETE | `/api/tasks/{id}` | Mutations | — | `{ ok, deleted_id }` |
| DELETE | `/api/tasks` | Mutations | — | purge totale |

**Task :** `id`, `title`, `description`, `priority`, `status`, `due_date`, `category`, `created_at`, `completed_at`.

| Limitation | Pas d’édition title/due via PATCH — documenter `server wins` pour sync |
| Offline | `pending_task_mutations` + retry |

---

## 9. Temps réel

### WebSocket `WS /ws`

| Champ | Valeur |
|-------|--------|
| Auth | Cookie session **uniquement** (fermeture `4401` / `4428`) |
| Streaming chat | client `text` → serveur `chunk` / `done` |
| Audio | binaire WebM + `speaking` / `speech_done` |
| Reconnexion | Client doit backoff — pas de protocol heartbeat documenté côté serveur WS |
| Android 1.2.0 | Non branché |
| Vague chat | Nécessite auth WS Bearer **ou** cookie après session mobile |

Messages client notables : `text`, `action_confirm`, `new_conversation`, `switch_conversation`, `conversation_start/stop`, `done_playing`, `recording_*`.

Messages serveur notables : `connected`, `chunk`, `done`, `response*`, `error`, `transcript`, `action_pending`, `conversation_*`, binaire TTS.

### SSE `GET /api/events/stream`

Auth session ; bootstrap 30 derniers événements ; types domaine (`notification.created`, `task.*`, …). Substitut Android : FCM + polling sync raisonnable.

---

## 10. Extension Bearer — **implémentée (Vague 1)**

**Comportement (`api/middleware.py`) :**

1. Cookie session valide → OK (inchangé).
2. Sinon `Authorization: Bearer` validé via `auth.verify_mobile_token` **et** route dans la whitelist GET → OK (`request.state.mobile_device`).
3. Sinon `401` / `428`.

**Whitelist GET :**

- `/api/briefing`
- `/api/notifications`, `/api/notifications/all`
- `/api/tasks`
- `/api/calendar`
- `/api/conversations`, `/api/conversations/search`, `/api/conversations/{id}`
- `/api/visits/today`, `/api/location/status`

**Mutations** (`POST`/`PATCH`/`DELETE` métier) : **non** ouvertes au Bearer en Vague 1.

**Tests :** `tests/test_mobile_bearer_routes.py` (6 cas).

---

## 11. Codes d’erreur communs

| Code | Signification |
|------|---------------|
| 401 | Session/token invalide |
| 403 | CSRF |
| 404 | Introuvable |
| 413 / 415 | Audio |
| 428 | Setup PIN requis |
| 429 | Rate / voice concurrent / lockout |
| 503 | Intégration Mac / STT / TTS / Calendar |
| 504 | Timeout voice pipeline |

Formats : FastAPI `{ "detail": "..." }` ou middleware `{ "error": "..." }`.

---

## 12. Variables d’environnement pertinentes

```bash
SESSION_COOKIE_NAME=jarvis_session
LOCATION_API_TOKEN=
LOCATION_TRACKING=true
FCM_SERVICE_ACCOUNT_FILE=
FCM_PROJECT_ID=
MOBILE_VOICE_MAX_BYTES=5242880
MOBILE_VOICE_MIN_BYTES=1000
MOBILE_VOICE_STT_TIMEOUT_SEC=120
MOBILE_VOICE_LLM_TIMEOUT_SEC=90
MOBILE_VOICE_TTS_TIMEOUT_SEC=60
WEB_HTTPS=true
CSRF_ALLOWED_ORIGINS=
```

---

## 13. Matrice Android ↔ Backend

| Feature | Backend | Android 1.2.0 | Action Vague 1+ |
|---------|---------|---------------|-----------------|
| Pairing / session / capabilities / push | OK | OK | — |
| Voice turn | OK | OK | Améliorer offline plus tard |
| Location single | OK | OK | File Room + batch |
| Location batch | OK | Manquant | Câbler + worker |
| Briefing GET | OK | Manquant | Bearer + UI Accueil |
| Tasks / calendar / notifs | OK | Manquant | Bearer + cache Room |
| Conversations list/detail | OK | Manquant | Bearer lecture |
| Chat texte send | WS only | Manquant | Vague chat |
| Continuous voice | WS recording / pas mobile turn loop | Manquant | Vague voix continue |
| Heartbeat | Touch implicite | — | Optionnel |

---

## 14. Références fichiers

| Domaine | Fichiers |
|---------|----------|
| Middleware | `api/middleware.py` |
| Mobile auth | `api/router_auth.py`, `auth.py` |
| Voice | `api/router_mobile_voice.py`, `api/mobile_voice_service.py` |
| Location | `api/router_location.py` |
| Conversations | `api/router_conversations.py` |
| Tasks | `api/router_tasks.py` |
| Briefing / notifs | `api/misc_integrations.py`, `api/router_misc.py` |
| Calendar | `api/misc_relationships.py` (routes calendar) |
| WebSocket | `api/ws_handler.py`, `api/ws_messages.py` |
| Tests | `tests/test_mobile_pairing.py`, `tests/test_mobile_voice.py` |
| Client | `android/.../network/JarvisApiService.kt`, `voice/VoiceRepository.kt` |
