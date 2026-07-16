# Spec — Vague 1 Companion Android production foundation

**Date :** 2026-07-16  
**Branche :** `feature/android-production-app`  
**Baseline :** `main` @ PR #33 ; Android 1.2.0 (15 unit tests green)  
**Décisions validées :**

- Vague 1 seulement (pas mission 1–33 complète)
- Auth : étendre middleware Bearer sur routes métier lecture
- Ordre : audit docs (fait) → spec/plan → implémentation

## Objectif

Transformer le Companion 1.2.0 (écran config unique) en **coquille applicative production** : onboarding, navigation, thème, Room/Sync skeleton, Accueil alimenté par données réelles (via Bearer), paramètres/diagnostics basiques — **sans** remplacer le package, le pairage, le Keystore, GPS, FCM, Porcupine, Retrofit, ni la voix PTT.

## Hors scope Vague 1

- Chat texte streaming complet + file offline messages
- Conversation vocale continue + VAD anti-écho
- File GPS offline + WorkManager batch (stub Room uniquement)
- UI Agenda / Tâches complètes (placeholders navigation OK)
- Bump version `2.0.0` / release GitHub
- Hilt

## Architecture

```
UI (Compose NavHost)
  → ViewModels
    → Repositories (lisent Room, écrivent Room)
      → SyncManager / WorkManager (réseau → Room)
      → JarvisRepository / JarvisApiService (Retrofit Bearer)
Backend: middleware accepte Bearer sur whitelist GET métier
```

Injection : `AppContainer` léger (pas Hilt).

## Backend

Fichier : `api/middleware.py` (+ helpers `auth.verify_mobile_token`).

Pour chaque requête `/api/*` non bypassée :

1. Cookie session valide → OK (comportement actuel)
2. Sinon Bearer `Authorization` valide → OK, attacher device au `request.state` si utile
3. Sinon 401/428

**Whitelist Vague 1 (lecture) :**

- `GET /api/briefing`
- `GET /api/notifications`, `GET /api/notifications/all`
- `GET /api/tasks`
- `GET /api/calendar`
- `GET /api/conversations`, `GET /api/conversations/search`, `GET /api/conversations/{id}`
- `GET /api/visits/today`, `GET /api/location/status` (optionnel dashboard)

Tests pytest : Bearer OK, révoqué, cookie OK, sans auth 401, setup 428.

Mutations HTTP : **non ouvertes** en Vague 1 (sauf already-bypass mobile).

## Android — couches

### Dependances nouvelles

- `navigation-compose`
- `room` (+ ksp)
- `datastore-preferences` (ou conserver SharedPreferences pour settings non secrets — DataStore pour flags onboarding)
- `work-runtime-ktx`
- Lifecycle ViewModel déjà présent

### Room (v1 schema)

Tables minimales :

- `cached_briefing` (kind, content, fetchedAt, validForDate)
- `cached_task` (mirror serveur + updatedAt)
- `cached_event` (fenêtre + updatedAt)
- `cached_notification` (mirror)
- `sync_metadata` (key, lastSuccessAt, lastError)
- `pending_location` (**stub** : insert inutilisé par le service jusqu’à Vague localization — table créée pour migration forward-safe)

Pas de migration destructive. `fallbackToDestructiveMigration` **interdit**.

### Sync

- `ConnectivityObserver` : NetworkCallback + distinction `NoNetwork` / `NetworkNoServer` / `ServerOk` / `Unauthorized`
- `SyncManager.refreshHome()` : pulls GET Bearer → Room
- `SyncWorker` WorkManager `NetworkType.CONNECTED` périodique + one-shot au resume app
- UI lit Flow Room ; bannière « cache » / offline

### Navigation

Bottom bar : Accueil | Chat (placeholder « Vague chat ») | Voix (ouvre `VoiceActivity` ou route Compose wrapper) | Agenda (placeholder) | Plus

Plus : Tâches (placeholder), Localisation (état service existant), Notifications (liste cache), Diagnostics, Paramètres.

`NavigationRail` si `windowWidthSizeClass` ≥ Medium.

### Onboarding

5 étapes Compose ; après Ready token → `MainScaffold`. Session 401 → écran réappariage **sans** wipe Room non sensible.

### Accueil

Salutation + statut sync + widgets : briefing, tâches todo (top N), events du jour, notifs urgent/high. Erreur isolée par widget. Pas de données mockées : états empty/error/offline réels.

### Design system

Étendre `JarvisTheme` : couleurs cyan/bleu, surfaces, `JarvisCard`, `NetworkStatusBadge`, skeletons, `ErrorCallout`. Pas d’emoji. Mode sombre défaut + follow system.

### Sécurité (inchangé)

HTTPS release strict, Keystore token, pas de trust-all, logs sans token/GPS précis en release.

## Tests Vague 1

- Backend : Bearer whitelist
- Android unit : Room DAO, mapping DTO→Entity, SyncManager mock, Connectivity mapping, Nav destination IDs, ServerUrl (existants)
- Instrumentation : lancement + onboarding skip si déjà pairé (si possible émulateur)
- Gradle baseline inchangé : `assembleDebug testDebugUnitTest lintDebug`

## Critères de done Vague 1

1. Docs gap + contracts déjà mergés sur la branche
2. Middleware Bearer testé
3. Navigation + onboarding + Accueil avec données réelles **ou** états honest empty/offline
4. Room + worker compilent ; table pending_location prête
5. Pairage / Keystore / GPS / voice PTT non régressés
6. PR partielle ouverte vers `main` avec limites honnêtes

## Non-régression

Garder `NoWebViewGuardTest`. Ne pas changer `applicationId`. Version reste 1.2.0 / code 7.
