# Android Production Wave 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fondation production du Companion Android (Bearer métier, Room/Sync, navigation, onboarding, Accueil réel) sans casser pairing/Keystore/GPS/voix PTT.

**Architecture:** Étendre `api/middleware.py` pour accepter `Authorization: Bearer` sur une whitelist GET ; côté Android ajouter Navigation Compose + Room + WorkManager + `AppContainer`, Accueil lit Room alimenté par sync Bearer.

**Tech Stack:** Kotlin, Jetpack Compose, Navigation, Room, WorkManager, Retrofit/OkHttp existants, FastAPI, pytest.

## Global Constraints

- Package Android: `fr.jarvis.companion` (ne pas changer)
- Pas de WebView
- Token uniquement Keystore / `JarvisSecureStore`
- Pas de données mockées présentées comme réelles
- Pas de `fallbackToDestructiveMigration` Room
- HTTPS release strict ; pas de trust-all
- Version reste `1.2.0` / `versionCode 7`
- JDK 17+ pour Gradle (`JAVA_HOME=/opt/homebrew/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home`)
- Spec: `docs/superpowers/specs/2026-07-16-android-production-wave1-design.md`
- Contrats: `android/docs/API_CONTRACTS_PRODUCTION.md`

---

### Task 1: Middleware Bearer whitelist + tests pytest

**Files:**
- Modify: `api/middleware.py`
- Create: `tests/test_mobile_bearer_routes.py`
- Modify: `android/docs/API_CONTRACTS_PRODUCTION.md` (marquer section 10 « implémenté »)

**Interfaces:**
- Consumes: `auth.verify_mobile_token(token: str | None) -> dict | None`
- Produces: requêtes GET whitelist autorisées avec Bearer valide

- [ ] **Step 1: Write failing tests**

```python
# tests/test_mobile_bearer_routes.py
# Arrange: device paired with native token (reuse fixtures from test_mobile_pairing)
# Act: GET /api/tasks with Authorization: Bearer <token> and no session cookie
# Assert: 200 (or empty tasks list shape)
# Act: Bearer revoked → 401
# Act: cookie session still works without Bearer
# Act: GET /api/tasks without auth → 401 (or 428 if unconfigured)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_mobile_bearer_routes.py -v
```

Expected: FAIL (401 on Bearer for /api/tasks)

- [ ] **Step 3: Implement middleware**

In `security_middleware`, before rejecting missing session:

```python
# Pseudocode — adapter au style existant
auth_header = request.headers.get("authorization", "")
if auth_header.lower().startswith("bearer "):
    device = auth.verify_mobile_token(auth_header[7:].strip())
    if device and _mobile_bearer_allows(method, path):
        request.state.mobile_device = device
        # skip cookie failure path; proceed to call_next
```

Define `_mobile_bearer_allows` as exact method+path prefixes from the Wave 1 whitelist in the spec (GET only).

- [ ] **Step 4: Run tests to verify pass**

```bash
pytest tests/test_mobile_bearer_routes.py tests/test_mobile_pairing.py -v
```

- [ ] **Step 5: Commit**

```bash
git add api/middleware.py tests/test_mobile_bearer_routes.py android/docs/API_CONTRACTS_PRODUCTION.md
git commit -m "feat(api): accept mobile Bearer on read business routes"
```

---

### Task 2: Gradle deps Navigation / Room / WorkManager / KSP

**Files:**
- Modify: `android/build.gradle`, `android/app/build.gradle`, `android/settings.gradle` if needed for KSP plugin
- Modify: `android/gradle.properties` (optionnel `android.suppressUnsupportedCompileSdk=36`)

**Interfaces:**
- Produces: project compiles with Room/Navigation/WorkManager on classpath

- [ ] **Step 1: Add dependencies matching AGP 8.7 / Kotlin version already in project**

Align versions with existing Compose BOM in `app/build.gradle`. Use `ksp` for Room compiler (not kapt).

- [ ] **Step 2: Assemble**

```bash
cd android && ./gradlew :app:assembleDebug
```

- [ ] **Step 3: Commit**

```bash
git commit -am "build(android): add Navigation Room WorkManager dependencies"
```

---

### Task 3: Room schema + DAOs + mapping tests

**Files:**
- Create: `android/app/src/main/kotlin/fr/jarvis/companion/core/database/JarvisDatabase.kt`
- Create: entities/DAOs under `core/database/`
- Create: `android/app/src/test/kotlin/fr/jarvis/companion/core/database/RoomEntitiesTest.kt` (Robolectric in-memory)

**Interfaces:**
- Produces: `JarvisDatabase` with entities `CachedBriefingEntity`, `CachedTaskEntity`, `CachedEventEntity`, `CachedNotificationEntity`, `SyncMetadataEntity`, `PendingLocationEntity`
- DAO methods: `observe*`, `upsert*`, `clearSyncedLocations` (stub)

- [ ] **Step 1: Failing DAO test insert+observe briefing**
- [ ] **Step 2: Implement database version 1 + converters for Instant/ISO**
- [ ] **Step 3: Tests pass**
- [ ] **Step 4: Commit** `feat(android): add Room schema for offline cache`

---

### Task 4: ConnectivityObserver + SyncManager + SyncWorker

**Files:**
- Create: `core/connectivity/ConnectivityObserver.kt`
- Create: `core/sync/SyncManager.kt`, `SyncWorker.kt`
- Create: unit tests with mocked API + in-memory Room
- Modify: `JarvisApiService.kt` — add GET briefing/tasks/calendar/notifications/conversations
- Modify: `JarvisRepository.kt` — methods returning DTOs

**Interfaces:**
- `SyncManager.refreshHome(): SyncResult`
- `ConnectivityObserver.state: StateFlow<ConnectivityState>`
- Worker unique name `jarvis_sync`

- [ ] **Step 1: Tests SyncManager upserts Room on 200; maps 401 → Unauthorized; offline skips network**
- [ ] **Step 2: Implement**
- [ ] **Step 3: Pass + commit** `feat(android): sync manager refreshes home cache via Bearer`

---

### Task 5: Design system Theme components

**Files:**
- Modify: `ui/theme/JarvisTheme.kt`
- Create: `core/ui/components/*.kt` (`JarvisCard`, `NetworkStatusBadge`, `ErrorCallout`, `LoadingSkeleton`)

- [ ] **Step 1: Implement colors/spacing/typography; no screenshot requirement**
- [ ] **Step 2: Compile + commit** `feat(android): expand Jarvis design system components`

---

### Task 6: AppContainer + JarvisApp NavHost + BottomBar

**Files:**
- Create: `app/JarvisApplication.kt`, `app/AppContainer.kt`, `app/JarvisApp.kt`
- Create: `navigation/JarvisDestination.kt`, `navigation/JarvisNavHost.kt`
- Modify: `AndroidManifest.xml` — `android:name=".app.JarvisApplication"`
- Modify: `MainActivity` — host `JarvisApp` instead of only CompanionScreen

**Interfaces:**
- Destinations sealed: Home, ChatPlaceholder, Voice, CalendarPlaceholder, More, Onboarding, Diagnostics, Settings, RePair
- Voix: start `VoiceActivity` from nav action (preserve existing activity)

- [ ] **Step 1: Wire Application + empty NavHost with placeholders**
- [ ] **Step 2: Instrumentation still no WebView**
- [ ] **Step 3: Commit** `feat(android): add Navigation Compose shell`

---

### Task 7: Onboarding 5 steps + session invalid screen

**Files:**
- Create: `feature/onboarding/*`
- Modify: settings flag `onboarding_complete`
- Hook 401 from SyncManager → navigate RePair (clear token only, keep Room cache)

- [ ] **Step 1: Compose flows Welcome → Server URL (reuse ServerUrlNormalizer) → Pairing → Permissions progressive → Done**
- [ ] **Step 2: Unit test URL step validation errors**
- [ ] **Step 3: Commit** `feat(android): multi-step onboarding and re-pair gate`

---

### Task 8: Home / Briefing screen from Room

**Files:**
- Create: `feature/home/HomeScreen.kt`, `HomeViewModel.kt`
- Wire `SyncManager.refreshHome` on resume + pull-to-refresh

- [ ] **Step 1: HomeViewModel exposes StateFlow combining Room + ConnectivityObserver**
- [ ] **Step 2: Widgets isolés (briefing / tasks / events / notifs) with independent error**
- [ ] **Step 3: Commit** `feat(android): home dashboard from Room sync`

---

### Task 9: Settings + Diagnostics screens

**Files:**
- Create: `feature/settings/SettingsScreen.kt`, `feature/diagnostics/DiagnosticsScreen.kt`
- Diagnostics copy report **without** token / porcupine full key / precise GPS

- [ ] **Step 1: Implement**
- [ ] **Step 2: Commit** `feat(android): settings and diagnostics screens`

---

### Task 10: Docs Vague 1 + PR

**Files:**
- Update: `android/docs/ARCHITECTURE.md`, `android/README.md`
- Create: `android/docs/OFFLINE_SYNC.md` (skeleton Wave 1)
- Possibly ADR: `Architecture/adr/ADR-020-android-offline-first.md` (short)

- [ ] **Step 1: Document what shipped vs remaining waves**
- [ ] **Step 2: Full verify**

```bash
cd android && ./gradlew assembleDebug testDebugUnitTest lintDebug
pytest tests/test_mobile_bearer_routes.py tests/test_mobile_pairing.py tests/test_mobile_voice.py
```

- [ ] **Step 3: Push branch + `gh pr create`** with matrix + honest limits (no continuous voice, no chat text, no GPS queue yet)

---

## Spec coverage check

| Spec item | Task |
|-----------|------|
| Bearer whitelist | 1 |
| Room entities | 3 |
| Sync / WorkManager | 4 |
| Design system | 5 |
| Navigation shell | 6 |
| Onboarding | 7 |
| Accueil | 8 |
| Settings / Diagnostics | 9 |
| Docs / PR | 10 |
| No Hilt / keep package / version | Global |
| GPS queue / continuous voice / chat | Deferred (documented) |
