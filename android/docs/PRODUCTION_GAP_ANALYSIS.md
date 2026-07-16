# PRODUCTION_GAP_ANALYSIS — Companion Android JARVIS

**Date d’audit :** 2026-07-16  
**Branche de départ :** `main` @ `476d14c` (après merge PR #33)  
**Version audité :** `versionName 1.2.0` / `versionCode 7`  
**Package :** `fr.jarvis.companion`  
**Baseline CI locale :**

```text
JAVA_HOME=/opt/homebrew/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home
cd android && ./gradlew assembleDebug testDebugUnitTest lintDebug
→ BUILD SUCCESSFUL — 15 tests unitaires, 0 failure, lintDebug OK
```

Ce document décrit ce qui existe réellement dans le code sous `android/`, pas ce que le README promet pour une roadmap future.

---

## Vue d’ensemble

L’application 1.2.0 est un **compagnon de services** : pairage, GPS foreground, wake word, FCM optionnel, voix PTT. Ce n’est **pas** encore un client mobile complet (pas de navigation applicative, pas de Room, pas de chat texte, pas de briefing UI, pas d’agenda/tâches natives, pas de sync offline).

| Couche | État |
|--------|------|
| Kotlin + Compose | Présent |
| 2 Activities (`MainActivity`, `VoiceActivity`) | Présent |
| Navigation Compose | Absent |
| Room / WorkManager / DataStore | Absent |
| Retrofit + OkHttp | Présent |
| Keystore + AES-GCM | Présent |
| WebView | Explicitement interdit (tests garde-fou) |

---

## Fonctionnalités réellement opérationnelles

### Pairage par code à six chiffres

| Critère | État |
|---------|------|
| **Code** | `MainViewModel.completePairing` → `POST /api/mobile/pairing/complete` ; UI dialog dans `MainActivity` |
| **Tests** | Backend `tests/test_mobile_pairing.py` ; pas de test ViewModel Android |
| **Validation réelle** | Manuelle documentée + script `scripts/android_e2e_pairing.sh` (contrat backend) |
| **Limite** | Pas d’E2E ADB ; `POST /api/mobile/pairing/start` reste côté web (session cookie) |

### Jeton natif + Android Keystore

| Critère | État |
|---------|------|
| **Code** | `JarvisSecureStore` + `AndroidKeyStoreProvider` (alias `jarvis_companion_v1`) ; `allowBackup=false` |
| **Tests** | `FakeSecretKeyProvider` pour Robolectric ; pas de round-trip Keystore réel |
| **Validation** | Session `POST /api/mobile/session` + clear token sur 401 |
| **Limite** | Cookie `jarvis_session` posé par le backend **non stocké** par OkHttp (pas de CookieJar) |

### HTTPS + CA JARVIS

| Critère | État |
|---------|------|
| **Code** | `ServerUrlNormalizer` (rejette HTTP), `JarvisTls` (CA système + `res/raw/jarvis_ca.crt`), `usesCleartextTraffic=false` |
| **Tests** | `ServerUrlNormalizerTest`, `JarvisHttpClientTest`, MockWebServer HTTPS dans `VoiceRepositoryTest` |
| **Validation** | Scripts `generate_ssl.sh` / `sync_android_ca.sh` / `android_dev_https.sh` |
| **Limite** | Pas de pin strict ; rotation CA = rebuild APK ; debug `DEFAULT_SERVER=https://10.0.2.2:8081` |

### Localisation GPS (foreground)

| Critère | État |
|---------|------|
| **Code** | `JarvisLocationService` — intervalle fixe ~5 min / 50 m → `POST /api/location` avec Bearer |
| **Tests** | Aucun |
| **Validation** | Manuelle uniquement |
| **Limite** | Envoi immédiat sans file Room ; pas de batch ; boot receiver plus strict (exige `ACCESS_BACKGROUND_LOCATION`) que `resumePersistentFeatures()` |

### Notifications FCM

| Critère | État |
|---------|------|
| **Code** | `JarvisMessagingService` + `POST /api/mobile/push-token` ; canaux dans `JarvisNotifications` |
| **Tests** | Aucun (souvent `FIREBASE_CONFIGURED=false` sans `google-services.json`) |
| **Validation** | Manuelle si Firebase + FCM serveur configurés |
| **Limite** | Pas de deep link riche ; CI sans `google-services.json` |

### Wake word Porcupine

| Critère | État |
|---------|------|
| **Code** | `JarvisWakeWordService` (foreground micro) → ouvre `VoiceActivity` |
| **Tests** | Aucun |
| **Validation** | Manuelle + clé Picovoice saisie |
| **Limite** | Pas de redémarrage auto au boot (notif de rappel seulement) ; **n’est pas** une conversation continue |

### Redémarrage après reboot

| Critère | État |
|---------|------|
| **Code** | `JarvisBootReceiver` (`BOOT_COMPLETED`, `MY_PACKAGE_REPLACED`) |
| **Tests** | Aucun |
| **Validation** | Manuelle |
| **Limite** | GPS seulement si permission background ; wake word = notif « réactiver » |

### Conversation vocale push-to-talk

| Critère | État |
|---------|------|
| **Code** | `VoiceActivity` / `VoiceViewModel` / `VoiceRecorder` / `VoicePlayer` / `VoiceRepository` → `POST /api/mobile/voice/turn` (OkHttp multipart) |
| **Tests** | `VoiceRepositoryTest`, `VoiceStateTest` ; backend `tests/test_mobile_voice.py` |
| **Validation** | Robolectric + pytest ; appareil réel documenté dans checklist |
| **Limite** | Historique limité à l’écran voix ; amplitude UI non alimentée ; timeout lecture 180 s ; pas de file offline audio |

### Écran principal de configuration

| Critère | État |
|---------|------|
| **Code** | `MainActivity` / `MainViewModel` — serveur, pairage, toggles GPS/wake, statut phases |
| **Tests** | `MainActivityInstrumentationTest` (absence WebView uniquement) |
| **Validation** | Compile + lint |
| **Limite** | Pas de navigation applicative ; pas de Compose UI tests ; devient un onboarding dans la cible production |

### Thème visuel minimal

| Critère | État |
|---------|------|
| **Code** | `ui/theme/JarvisTheme.kt` (Material3 dark) |
| **Tests** | Aucun |
| **Limite** | Pas de design system (composants cartes, orbe, skeletons, badges réseau) |

### CI / Release config

| Critère | État |
|---------|------|
| **Code** | Job CI `assembleDebug testDebugUnitTest lintDebug` ; R8 `minifyEnabled` release ; `signing.properties.example` |
| **Tests** | 15 unitaires ; 1 instrumentation superficielle |
| **Limite** | `assembleRelease` non signé sans keystore local ; pas d’`assembleRelease` en CI |

---

## Fonctionnalités partielles

| Sujet | Preuve | Écart vers production |
|-------|--------|------------------------|
| GPS | Service + POST unitaire | Pas de `pending_locations`, pas de WorkManager, pas de batch Android (`/api/location/batch` existe côté backend) |
| Voix PTT | Tour complet fonctionne | Pas d’historique Room ; pas d’offline ; états UI incomplets vs spec production |
| Wake word | Détection → VoiceActivity | Distinct d’une conversation continue avec VAD + TTS anti-écho |
| Session | Bearer validé | Routes métier (tasks, calendar, briefing, conversations) **inaccessibles** sans cookie session |
| Boot | Receiver présent | Wake word non auto ; permissions inconsistantes GPS |
| FCM | Code branché | Souvent désactivé au build ; pas d’ouverture de destination métier |
| Docs ARCHITECTURE | Déclarent chat hors scope | Doivent être révisées pour la cible production |

---

## Fonctionnalités absentes (vérifiées dans le code)

Confirmé par inventaire des sources Kotlin + grep (0 match) pour Room / WorkManager / Navigation Compose :

| Domaine | Absent |
|---------|--------|
| Navigation BottomBar / NavigationRail | Oui |
| Onboarding multi-étapes | Oui (écran unique config) |
| Briefing / tableau de bord métier | Oui |
| Chat texte natif | Oui |
| Streaming WebSocket | Oui |
| Agenda natif | Oui |
| Tâches natives | Oui |
| Conversation vocale continue (service dédié) | Oui |
| Room (cache + pending ops) | Oui |
| WorkManager / SyncManager | Oui |
| ConnectivityObserver (réseau vs serveur vs auth) | Oui |
| File offline (messages, tâches, agenda, locations) | Oui |
| Écran Diagnostics production | Oui |
| Écran Paramètres structuré | Partiel (toggles sur Main) |
| DataStore | Oui (SharedPreferences seulement) |
| Identifiants clients idempotents | Oui |

---

## Inventaire des fichiers Kotlin (1.2.0)

### Main (`app/src/main/kotlin/fr/jarvis/companion/`)

| Fichier | Rôle |
|---------|------|
| `ui/MainActivity.kt` | Launcher Compose dashboard config |
| `ui/MainViewModel.kt` | Phases Loading / NeedsServer / NeedsPairing / Ready / Offline |
| `ui/theme/JarvisTheme.kt` | Thème Material3 |
| `voice/VoiceActivity.kt` | UI PTT |
| `voice/VoiceViewModel.kt` | Orchestration enregistrement → upload → lecture |
| `voice/VoiceRepository.kt` | Multipart voice turn |
| `voice/VoiceRecorder.kt` | MediaRecorder AAC/M4A |
| `voice/VoicePlayer.kt` | Lecture base64 |
| `voice/VoiceState.kt` | États UI voix |
| `data/JarvisRepository.kt` | Appels Retrofit métier mobile |
| `data/JarvisSettings.kt` | Préférences + secrets |
| `data/JarvisSecureStore.kt` | Chiffrement AES-GCM |
| `data/SecretKeyProvider.kt` | Keystore |
| `network/JarvisApiService.kt` | Interface Retrofit (6 routes) |
| `network/JarvisHttpClient.kt` | Client OkHttp/Retrofit |
| `network/JarvisTls.kt` | Trust CA |
| `network/ServerUrlNormalizer.kt` | Normalisation URL |
| `network/JarvisApiResult.kt` | DTO résultat |
| `services/JarvisLocationService.kt` | GPS foreground |
| `services/JarvisWakeWordService.kt` | Porcupine |
| `services/JarvisMessagingService.kt` | FCM |
| `receivers/JarvisBootReceiver.kt` | Boot |
| `notifications/JarvisNotifications.kt` | Canaux / helpers |

### Tests

| Fichier | Couverture |
|---------|------------|
| `ServerUrlNormalizerTest` | URLs |
| `JarvisHttpClientTest` | Base URL |
| `NoWebViewGuardTest` | Anti-WebView |
| `VoiceRepositoryTest` | Voice HTTPS + Bearer + 401 |
| `VoiceStateTest` | États UX |
| `FakeSecretKeyProvider` | Tests |
| `MainActivityInstrumentationTest` | No WebView |

---

## Décisions pour la Vague 1 (hors ce commit docs)

1. **Étendre l’auth Bearer** sur les routes métier lecture (briefing, tasks, calendar, notifications, conversations) plutôt qu’un CookieJar.
2. Introduire **Room + Sync skeleton** sans livrer encore chat texte / voix continue complets.
3. Remplacer l’écran unique par **Navigation Compose** + onboarding.
4. Conserver package `fr.jarvis.companion`, pairage, Keystore, GPS, FCM, Porcupine, Retrofit/OkHttp, voix PTT.
5. Version **reste 1.2.0 / code 7** jusqu’à une vague « app production complète » → `2.0.0`.

Voir aussi [`API_CONTRACTS_PRODUCTION.md`](./API_CONTRACTS_PRODUCTION.md).
