# Vague 2B Offline Location — Implementation Plan

> **Statut : implémenté sur `origin/main` @ `6becf26` ; plan archivé.**
> **Preuve :** `api/router_location.py`, `tests/test_location_batch.py`,
> `android/app/src/main/kotlin/fr/jarvis/companion/core/sync/LocationSyncWorker.kt`,
> `services/JarvisLocationService.kt` et les tests Android associés. Les cases
> ci-dessous sont le journal d’exécution d’origine et ne sont pas cochées
> rétroactivement ; la validation GPS sur appareil reste à rejouer.

> **For agentic workers:** Execute task-by-task. Checkbox tracking.

**Goal:** Android GPS offline-first with Room queue, idempotent batch sync, WorkManager, UI.

**Architecture:** LocationEngine → Room pending_locations → LocationSyncCoordinator/Worker → POST /api/location/batch (Bearer + client_point_id dedup).

**Tech Stack:** Kotlin/Room/WorkManager, FastAPI/SQLite, pytest, JUnit.

**Spec:** `docs/superpowers/specs/2026-07-16-android-offline-location-design.md`

## Tasks

1. Backend: migration `location_point_dedup`, batch auth Bearer for idempotent path, MAX 50, response accepted/duplicates/rejected
2. Backend tests `tests/test_location_batch.py`
3. Android Room v2 entity/DAO/lock/migration
4. LocationEngine + AdaptivePolicy + Validation + Deduplicator + tests
5. LocationSyncCoordinator + LocationSyncWorker + API DTOs
6. JarvisLocationService rewrite + BootReceiver + AppContainer
7. LocationScreen UI + Diagnostics + Notifications
8. Docs LOCATION.md, OFFLINE_SYNC, API contracts, README
9. Gradle + pytest verification + PR
