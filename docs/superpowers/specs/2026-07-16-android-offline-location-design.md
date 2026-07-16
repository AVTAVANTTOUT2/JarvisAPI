# Design — Vague 2B Localisation Android offline-first

**Date :** 2026-07-16  
**Branche :** `feature/android-offline-location` (empilée sur `feature/android-production-app` / PR Vague 1 #34)  
**Statut :** validé avec ajustements (feu vert utilisateur)  
**ADR :** `Architecture/adr/ADR-021-android-offline-location-batch.md`

---

## 1. Contexte et problème

### État avant Vague 2B

- Capture : `JarvisLocationService` + `LocationManager` (5 min / 50 m).
- Envoi : immédiat via `JarvisRepository.postLocation()` → `POST /api/location`.
- Room : table `pending_locations` présente (v1) mais **jamais alimentée**.
- Hors ligne / timeout / 4xx / 5xx : le résultat HTTP est ignoré → **point perdu**.
- Reboot : `JarvisBootReceiver` peut relancer le service ; la file GPS n’existe pas.
- Backend : `POST /api/location/batch` existe sans `client_point_id`, sans limite de lot, sans réponse par point.

### Objectif

```text
Position capturée → validée → Room (PENDING|INVALID)
  → réservation lot (SENDING + batch_id)
  → POST /api/location/batch (Bearer)
  → accepted|duplicates → SYNCED puis purge
  → rejected → FAILED_PERMANENT
  → erreur temporaire → FAILED_RETRYABLE + backoff
```

Une coupure réseau ne doit jamais provoquer la perte silencieuse d’un point valide.  
Fused Location Provider : **hors scope** (Vague 2B+), interface préparée.

---

## 2. Décisions d’architecture

| Décision | Choix |
|----------|--------|
| Moteur GPS | `LocationManager` derrière `LocationEngine` |
| Future Fused | `FusedLocationEngine` (non implémenté) |
| Sync GPS | `LocationSyncCoordinator` + `LocationSyncWorker` (hors `SyncManager` Accueil) |
| Batch size | **50** (`MAX_BATCH_SIZE`, configurable) |
| Idempotence | `(device_id, client_point_id)` unique serveur |
| Auth batch | Bearer mobile obligatoire |
| Auth unitaire | Inchangé (Shortcuts / `LOCATION_API_TOKEN`) |
| Rétention SYNCED | **Purge immédiate** après confirmation |
| Concurrence | Unique WorkManager + `sync_lock` Room |

---

## 3. Schéma Room et migration

### Version

- Room actuelle : version **1** (`pending_locations` stub Vague 1).
- Cible : version **2**, migration **non destructive** `MIGRATION_1_2`.
- Interdiction : `fallbackToDestructiveMigration()` en build production.

### Entité `PendingLocationEntity` (table `pending_locations`)

| Champ | Type | Notes |
|-------|------|--------|
| `id` | Long PK auto | Local |
| `clientPointId` | String | UUID v4, **UNIQUE** |
| `latitude` / `longitude` | Double | |
| `altitude` | Double? | |
| `accuracy` | Float | |
| `speed` / `bearing` | Float? | |
| `provider` | String? | |
| `capturedAt` | Long | epoch ms |
| `createdAt` | Long | insert local |
| `syncState` | String | voir §3.1 |
| `batchId` | String? | UUID du lot en cours d’envoi |
| `retryCount` | Int | défaut 0 |
| `nextRetryAt` | Long? | |
| `lastAttemptAt` | Long? | |
| `lastErrorCode` | String? | `NETWORK`, `HTTP_429`, `HTTP_5xx`, `AUTH`, `REJECTED`, `INVALID`, … |
| `lastErrorMessage` | String? | jamais de coords en logs release |

**Index :** `syncState`, `capturedAt`, `nextRetryAt`, `batchId`, UNIQUE(`clientPointId`).

Migration depuis v1 : renommer/mapper `capturedAtMillis`→`capturedAt`, `createdAtMillis`→`createdAt`, `lastError`→`lastErrorMessage` ; ajouter colonnes manquantes ; normaliser anciens états `pending`/`synced`/`failed` vers le nouvel enum.

### 3.1 États de synchronisation

| État | Rôle |
|------|------|
| `PENDING` | En file, éligible à l’envoi |
| `SENDING` | Réservé dans un lot (`batchId` non null) |
| `SYNCED` | Confirmé (accepted ou duplicate serveur) — **puis purgé** |
| `FAILED_RETRYABLE` | Erreur temporaire, backoff |
| `FAILED_PERMANENT` | Rejet serveur ou payload irrécupérable |
| `CANCELLED` | Annulé (ex. purge manuelle pending) |
| `INVALID` | Rejeté **localement** avant envoi (validation) — visible diagnostics, hors file d’envoi |

### 3.2 Table `location_sync_lock`

Verrou simple mono-processus / multi-worker :

| Champ | Type |
|-------|------|
| `id` | Int PK = 1 (singleton) |
| `lockedBy` | String? (worker/run id) |
| `lockedAt` | Long? |
| `expiresAt` | Long? |

Règles :

- Acquisition : UPDATE si `lockedBy` IS NULL OR `expiresAt` &lt; now.
- TTL : **5 minutes** (reclaim après crash).
- Libération : clear `lockedBy` en `finally` du worker.
- Si lock non acquis → worker `Result.retry()` ou succès no-op (selon politique KEEP).

### 3.3 Métadonnées (`sync_metadata`)

Clés :

- `location.last_sync_at` — epoch ms dernière sync réussie (au moins 1 accepted/duplicate)
- `location.last_batch_size` — taille du dernier lot envoyé
- `location.last_http_status` — dernier statut HTTP batch
- `location.last_timeline_json` — dernières entrées timeline UI (sans coords)

Pas de conservation des lignes `SYNCED` au-delà de la confirmation.

---

## 4. Abstraction `LocationEngine`

```kotlin
interface LocationEngine {
    fun start(config: LocationRequestConfig, listener: Listener)
    fun stop()
    fun lastKnown(): CapturedLocation?
}

class LocationManagerEngine(...) : LocationEngine  // Vague 2B
// class FusedLocationEngine(...) : LocationEngine  // Vague 2B+ — non livré
```

- Injection via `AppContainer`.
- Tests : `FakeLocationEngine`.
- `JarvisLocationService` ne touche plus `LocationManager` ni Retrofit directement.

### Fréquence adaptative (`AdaptiveLocationPolicy`)

| Mode | minTime | minDistance |
|------|---------|-------------|
| Déplacement | 5 min | 50 m |
| Immobile | 12 min | 100 m |
| Batterie faible (&lt; 20 %, non en charge) | 15 min | 150 m |

Recalcul sur chaque point retenu + événements batterie. Pas de tracking seconde par seconde.

---

## 5. Validation et déduplication

### Seuils (`LocationValidationConfig`)

| Règle | Valeur |
|-------|--------|
| Latitude | [-90, 90] |
| Longitude | [-180, 180] |
| Accuracy normale | &gt; 0 et ≤ **100 m** |
| Accuracy mode économie | ≤ **150 m** |
| Au-delà | **INVALID** (insert local, pas d’envoi) |
| Âge max | 3 minutes |
| Futur | now + 60 s → INVALID |
| Vitesse | &gt; 90 m/s → INVALID |
| Provider absent | INVALID |

Mode économie = batterie faible **ou** préférence utilisateur « économie GPS ».

### Déduplication (`LocationDeduplicator.shouldKeep`)

Comparer le candidat avec :

1. le **dernier** point non-`INVALID`/`CANCELLED` ;
2. les **5 derniers** points encore `PENDING` / `SENDING` / `FAILED_RETRYABLE` ;
3. les **5 derniers** points récemment synchronisés **si encore en mémoire courte** — comme les SYNCED sont purgés immédiatement, le store expose un **ring buffer en mémoire** (et/ou métadonnée) des 5 derniers fingerprints `(lat,lng,accuracy,capturedAt)` post-sync pour l’oscillation post-envoi.

Règles `shouldKeep` :

- Garder si distance ≥ 25 m **OU** Δt ≥ 60 s **OU** accuracy ≥ 30 % meilleure au même endroit.
- Rejeter (ne pas insert, ou INVALID selon cas) si distance &lt; 15 m et Δt &lt; 45 s et accuracy similaire vs l’un des points de comparaison.
- Changement de provider seul insuffisant si coords quasi identiques.

Points INVALID restent en base pour diagnostics ; purge selon rétention INVALID (§8).

---

## 6. Contrat batch backend

### Endpoint

`POST /api/location/batch` (existant, enrichi).

### Auth

- **Bearer mobile obligatoire** pour le batch (device actif, token non révoqué).
- Cookie non requis.
- Sans Bearer valide → **401** (pas d’accès anonyme sur le batch enrichi).
- `POST /api/location` unitaire : comportement actuel conservé (Shortcuts / `LOCATION_API_TOKEN`).

### Limite

```text
MAX_BATCH_SIZE = 50  # configurable (config / constante Android miroir)
```

Lot &gt; 50 → **400** avec message clair (ou 413 si déjà utilisé ailleurs ; préférer **400** cohérent API JARVIS).

### Payload

```json
{
  "points": [
    {
      "client_point_id": "uuid",
      "latitude": 50.0,
      "longitude": 3.0,
      "altitude": 20.0,
      "accuracy": 12.0,
      "speed": 1.2,
      "bearing": 180.0,
      "provider": "gps",
      "captured_at": 1784156400000,
      "source": "android_background"
    }
  ]
}
```

Alias acceptés côté serveur pour timestamps : `captured_at`, `timestamp`, `created_at`, `point_time`.

### Réponse

```json
{
  "accepted": ["uuid-1", "uuid-2"],
  "duplicates": ["uuid-3"],
  "rejected": [
    { "client_point_id": "uuid-4", "reason": "invalid_coordinates" }
  ]
}
```

### Idempotence

- Table `location_point_dedup` : PK `(device_id, client_point_id)`, colonnes `location_history_id`, `created_at`.
- Lookup **avant** `process_location()`.
- Hit → UUID dans `duplicates`, pas de second insert, pas de rejeu machine à états visites.
- Même UUID sur deux devices → deux lignes (scopé device).
- `device_id` extrait du Bearer (`verify_mobile_token`).

Endpoint unitaire inchangé pour clients sans `client_point_id`.

---

## 7. Réservation atomique des lots

Transaction Room :

1. Reclaim : `SENDING` avec `lastAttemptAt` &gt; **10 min** OU `batchId` dont le lock a expiré → remettre `PENDING`, clear `batchId`.
2. Acquérir `location_sync_lock` (sinon abort).
3. Générer `batchId = UUID`.
4. Sélectionner jusqu’à `MAX_BATCH_SIZE` lignes éligibles :
   - `syncState IN (PENDING, FAILED_RETRYABLE)`
   - `nextRetryAt IS NULL OR nextRetryAt <= now`
   - ordre `capturedAt ASC`
5. UPDATE → `SENDING`, `batchId`, `lastAttemptAt = now`.
6. Commit → HTTP hors transaction.
7. Appliquer réponse par `client_point_id` (transaction) :
   - accepted / duplicates → `SYNCED` puis **DELETE** immédiat
   - rejected → `FAILED_PERMANENT`, clear `batchId`
   - échec réseau lot entier → `FAILED_RETRYABLE`, backoff, clear `batchId`, `retryCount++`
8. Mettre à jour `sync_metadata` + timeline.
9. Libérer `sync_lock`.

---

## 8. `LocationSyncWorker` et retry

- Unique work name : `jarvis-location-sync`
- Contrainte : `NetworkType.CONNECTED`
- Périodique : **15 minutes**
- One-shot après insert : `ExistingWorkPolicy.KEEP`
- Déclencheurs : insert · ConnectivityObserver · Application.onCreate · boot · UI Localisation / Diagnostics

| Erreur | Action |
|--------|--------|
| Offline / timeout / DNS / TLS / 429 / 5xx | `FAILED_RETRYABLE`, backoff expo base 30 s max 1 h |
| 401 / device révoqué | Stop sync + signal réappairage ; **points conservés** |
| Rejected serveur | `FAILED_PERMANENT` |
| Succès partiel | Traiter chaque UUID indépendamment |

Réseau connecté ≠ serveur OK : seul HTTP 2xx + corps `accepted`/`duplicates` déclenche purge.

---

## 9. Reprise reboot

`JarvisBootReceiver` (`BOOT_COMPLETED`, `MY_PACKAGE_REPLACED`) :

1. Planifier worker périodique + one-shot si token présent.
2. Relancer FGS localisation si préférences + permissions (fine ; background si déjà accordé) et si autorisé par Android.
3. Aligner règles avec `MainActivity.resumePersistentFeatures`.
4. Force-stop : limite OS documentée (receiver silencieux jusqu’à ouverture manuelle).

---

## 10. Rétention

| État | Politique |
|------|-----------|
| `SYNCED` | **DELETE immédiat** après confirmation ; métadonnées seules |
| `PENDING` / `FAILED_RETRYABLE` | Max **30 jours** ou **20 000** points ; purge des plus anciens au-delà du seuil (compteur UI) |
| `FAILED_PERMANENT` | Purge auto **7 jours** ; suppression manuelle |
| `INVALID` | Purge auto **3 jours** ; visible Diagnostics |
| `CANCELLED` | Purge immédiate ou avec INVALID |
| Arrêt collecte | File pending **conservée** |

---

## 11. UI Localisation et diagnostics

### Écran Localisation

- Collecte on/off, permissions, état service
- Dernière capture (heure + précision, **pas** lat/lng par défaut)
- Dernière sync : **« il y a X min »** (+ heure absolue secondaire)
- Compteurs : pending, failed, invalid
- Mode fréquence courant
- Serveur joignable (dernier probe)
- **Timeline** (sans coords) :

```text
12:05   Capturé
12:06   Batch créé
12:06   Envoyé
12:06   Confirmé
```

Actions : sync maintenant · permissions · vider métadonnées / rien à vider côté SYNCED · supprimer pending (confirmation forte) · copier rapport sans coords.

### Diagnostics

Compléter avec : service GPS, provider, fine/background, pending, plus ancien pending, taille Room, dernier batch size, dernier HTTP, retry, worker, lock, token actif, backend reachable, **temps depuis dernière sync réussie**.

Rapport copié : pas de token, secrets, coords précises, certificats.

### Notification FGS

Texte non sensible + N pending + dernière sync relative. Actions Arrêter (stop collecte, file intacte) / Synchroniser.

---

## 12. Sécurité et vie privée

- HTTPS + Bearer Keystore uniquement vers backend pairé
- Pas de coords dans logs release
- Notification visible tant que collecte active
- Désactivation réelle du service
- Backup : exclure token ; documenter décision `pending_locations` (recommandation : exclure ou chiffrer — pas de backup cloud des traces GPS)

---

## 13. Tests

### Backend

Bearer ok/ko · device révoqué · batch vide · valide · &gt;50 · coords/timestamp invalides · doublon `client_point_id` · même UUID deux devices · réponse partielle · ordre chrono · unitaire inchangé · pas d’anonyme batch.

### Room

Insert · UNIQUE · sélection · réservation + `batchId` · reclaim SENDING · lock · accepted→purge · duplicate→purge · rejected · retryable · INVALID · migration 1→2 · concurrence.

### Worker

Offline · serveur down · succès total/partiel · 401/429/500 · timeout · unique work · lock contention · pas de double envoi.

### Service / Engine

FakeLocationEngine : valide · trop vieux · imprécis (&gt;100/150) · doublon vs 5 points · plus précis · permission · token · stop user.

### S24 (manuel)

Offline complet · reboot · permissions approximate/background · batterie (mesures réelles uniquement, documentées dans `android/docs/LOCATION.md`).

---

## 14. Documentation livrable

- `android/docs/LOCATION.md` (état avant + après, seuils, S24)
- `android/docs/OFFLINE_SYNC.md` (section GPS)
- `android/docs/API_CONTRACTS_PRODUCTION.md`
- `android/docs/ARCHITECTURE.md`
- `android/docs/SECURITY.md`
- `android/README.md`
- ADR-021

---

## 15. Version

Pas de release stable. Version alpha cohérente Vague 1 ; éviter conflit `versionCode` avec branches Chat — bump final à l’intégration.

---

## 16. Hors scope

Chat · voix continue · agenda/tâches complets · Fused implémenté · gzip · simplification géo · suppression Porcupine/FCM · release 2.0.0 stable.

---

## 17. Définition de terminé

- Tout point valide en Room avant réseau
- Aucune perte hors ligne / reboot app
- Batch Bearer idempotent, MAX 50, pas de doublon serveur
- Retry différencié, lock + reclaim SENDING
- UI timeline + « il y a X min »
- Migrations non destructives
- Tests Android + backend verts
- Scénario offline S24 documenté
- PR dédiée ciblant Vague 1 (ou main si #34 fusionnée)
