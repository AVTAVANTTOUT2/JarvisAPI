# Localisation Android — Vague 2B

## État avant Vague 2B

- `JarvisLocationService` envoyait chaque point immédiatement via `POST /api/location`.
- La table `pending_locations` (Room v1) existait mais n'était jamais alimentée.
- Hors ligne, timeout ou erreur HTTP : point perdu silencieusement.
- Pas de batch, pas de `client_point_id`, pas de retry différencié.

## Architecture après Vague 2B

```text
LocationManagerEngine
  → validation (LocationValidator)
  → déduplication (LocationDeduplicator + SyncFingerprintCache)
  → Room pending_locations (PENDING | INVALID)
  → LocationSyncWorker (15 min + one-shot)
  → LocationSyncCoordinator (lock + batch)
  → POST /api/location/batch (Bearer, max 50)
  → accepted|duplicates → purge immédiate
  → rejected → FAILED_PERMANENT
  → erreur réseau → FAILED_RETRYABLE + backoff
```

### Composants

| Fichier | Rôle |
|---------|------|
| `core/location/LocationEngine.kt` | Interface + `CapturedLocation` |
| `core/location/LocationManagerEngine.kt` | Implémentation `LocationManager` |
| `core/location/AdaptiveLocationPolicy.kt` | Fréquence adaptative |
| `core/location/LocationValidator.kt` | Rejet local INVALID |
| `core/location/LocationDeduplicator.kt` | Anti-oscillation |
| `core/location/PendingLocationStore.kt` | Façade DAO |
| `core/location/LocationSyncCoordinator.kt` | Réservation lot + HTTP |
| `core/sync/LocationSyncWorker.kt` | WorkManager unique |
| `feature/location/LocationScreen.kt` | UI sans coordonnées |

### Room v2

- `pending_locations` enrichie (`clientPointId` UNIQUE, états, batch, retry).
- `location_sync_lock` singleton (verrou 5 min).
- Migration `MIGRATION_1_2` non destructive.

### Seuils

| Règle | Valeur |
|-------|--------|
| Batch max | 50 points |
| Précision normale | ≤ 100 m |
| Précision économie | ≤ 150 m |
| Âge max point | 3 min |
| Déplacement | 5 min / 50 m |
| Immobile | 12 min / 100 m |
| Batterie faible | 15 min / 150 m |
| Rétention pending | 30 j / 20 000 points |
| Rétention INVALID | 3 jours |
| Rétention FAILED_PERMANENT | 7 jours |

### Métadonnées sync

- `location.last_sync_at`
- `location.last_batch_size`
- `location.last_http_status`
- `location.last_timeline_json`

## Checklist S24 (manuel)

| Scénario | Statut |
|----------|--------|
| Offline complet puis retour réseau | À valider sur device |
| Reboot avec file pending | À valider sur device |
| Permissions approximate / background | À valider sur device |
| Mesures batterie réelles | Documenter ici après test |

## Hors scope

- Fused Location Provider (interface prête, implémentation future).
- Gzip batch.
- Coords dans l'UI par défaut.
