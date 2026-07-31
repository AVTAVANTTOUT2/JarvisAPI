<!--
source_agent: bc-019fb8a4-d78c-799e-bf5f-0c2f4d7bac17
agent_name: Android companion audit
agent_url: https://cursor.com/agents/bc-019fb8a4-d78c-799e-bf5f-0c2f4d7bac17
agent_status: IDLE
created_at: 2026-07-31T14:47:24.574000+00:00
extracted_msg_index: 133
extracted_at: 2026-07-31T15:02:18.221852+00:00
-->

## Audit P16 — Android Companion (lecture seule)

**Verdict :** Companion `2.1.0-ui` cohérent sur Bearer mobile, HTTPS sans cleartext, placeholders FUTURE inertes. Pas d’`EncryptedSharedPreferences` ni de certificate pinning strict. Risque release : R8 + Gson sans règles keep. Docs `ARCHITECTURE` / `PRODUCTION_GAP` en retard sur le chat/offline déjà livrés.

---

### Schéma obligatoire (checklist)

| # | Contrôle | Verdict | Preuve | Sévérité |
|---|---|---|---|---|
| 1 | Stockage token | **OK partiel** — AES-GCM + AndroidKeyStore, **pas** `EncryptedSharedPreferences` | `JarvisSecureStore.kt`, `SecretKeyProvider.kt`, `JarvisSettings.nativeToken` | MOYENNE (écarts vs best-practice Jetpack Security) |
| 2a | Certificate pinning | **ABSENT** (volontaire) — confiance CA privée + fallback système | `JarvisTls.kt` L12–13, L73–78 ; README | INFO / accepté |
| 2b | Cleartext | **OK** — interdit | `network_security_config.xml`, Manifest `usesCleartextTraffic=false`, `ServerUrlNormalizer` rejette `http://` | OK |
| 3 | JARVIS-FUTURE-* UI | **OK** — placeholders inertes ; flags `false` ; bascule `true` sans logique = no-op fail-closed | `JarvisFeatureFlags.kt`, écrans + `FUTURE_FEATURES.md` | BASSE (flags More/Settings non lus) |
| 4 | CSRF / cookie vs Bearer | **OK** — Bearer seul ; pas de CookieJar ; CSRF N/A | `JarvisRepository` `cookie=null` ; WS/HTTP `Authorization: Bearer` ; docs API | OK |
| 5 | Permissions runtime | **OK** — fine→background, mic, notifs T+ | `MainActivity`, `VoiceActivity`, Manifest | OK |
| 6 | Tests | **Partiel** — ~77 unitaires + ~10 instrumentés ; trous auth/TLS/WS/services/chat | `src/test`, `src/androidTest` | MOYENNE |
| — | ProGuard/R8 release | **Risque** — `minifyEnabled true`, keep Gson/Retrofit/data classes absents | `app/build.gradle`, `proguard-rules.pro` | HAUTE (release) |
| — | Secrets non-token | **OK** — Porcupine dans SecureStore ; prefs plain pour flags UI | `JarvisSettings` | OK |
| — | Parité API | **Partielle** — lecture + chat/voix/location OK ; mutations tasks/calendar/notifs FUTURE | `JarvisApiService`, flags | voir matrice |

---

### 1. Stockage token

```14:25:android/app/src/main/kotlin/fr/jarvis/companion/data/JarvisSecureStore.kt
    private val preferences =
        context.applicationContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
    // ... AES/GCM/NoPadding, payload Base64(iv).Base64(ciphertext)
```

| Point | État |
|---|---|
| `EncryptedSharedPreferences` | **Non utilisé** |
| Clé | AndroidKeyStore AES-256-GCM, alias `jarvis_companion_v1`, non exportable |
| Conteneur | SharedPreferences `jarvis_secure` (ciphertext) |
| Secrets | `native_token`, `porcupine_access_key` |
| Backup | `allowBackup=false` |
| Auth utilisateur sur clé | **Non** (`setUserAuthenticationRequired` absent) |
| Tests Keystore | `FakeSecretKeyProvider` seulement — pas de round-trip Keystore réel |

**Écarts vs EncryptedSharedPreferences :** pas de migration Jetpack Security, pas de binding biométrique, IV/GCM gérés à la main (correct mais custom). Fonctionnellement acceptable ; ne pas documenter comme « EncryptedSharedPreferences ».

---

### 2. TLS / cleartext / pinning

| Contrôle | État |
|---|---|
| Cleartext | Bloqué (NSC + Manifest + normalizer) |
| Pinning SPKI/cert | **Non** |
| Trust | Composite : CA `res/raw/jarvis_ca.crt` **puis** CA système |
| Clients | Retrofit, Voice OkHttp, Chat WebSocket — tous via `JarvisTls` |
| Rotation CA | Rebuild APK (`sync_android_ca.sh`) |

`JarvisHttpClient.normalizeBaseUrl` n’interdit pas HTTP (ajoute `/`) ; la porte d’entrée UI/settings passe par `ServerUrlNormalizer` qui refuse `http://`. Filet NSC en dernier recours.

---

### 3. CSRF / cookie vs Bearer

| Canal | Auth réelle |
|---|---|
| REST Retrofit | `Authorization: Bearer {native_token}` |
| Voix multipart | Bearer |
| WebSocket `/ws` | Bearer handshake |
| Cookie `jarvis_session` | **Non stocké** (`cookie` toujours `null`, pas de CookieJar) |
| `X-CSRF-Token` | **Absent** (correct : pas de session cookie) |

Aligné avec le contrat mobile : mutations Bearer hors CSRF web. `POST /api/mobile/session` peut poser un Set-Cookie serveur — **ignoré** côté app (documenté).

---

### 4. Permissions runtime

| Permission | Déclaration | Runtime |
|---|---|---|
| `RECORD_AUDIO` | oui | VoiceActivity + wake word |
| `ACCESS_FINE/COARSE` | oui | avant start FGS location |
| `ACCESS_BACKGROUND_LOCATION` | oui | Q = dialog ; R+ = settings app (conforme Play) |
| `POST_NOTIFICATIONS` | oui | API 33+ |
| FGS location / microphone | types déclarés | services non exportés |

Boot : GPS seulement si fine (+ logique background dans receiver) ; wake word = rappel notif, pas auto-start sans conditions.

---

### 5. ProGuard / secrets build

- Release : `minifyEnabled true`, `shrinkResources true`, `DEFAULT_SERVER=""`.
- Keep : BuildConfig, Firebase, Porcupine, Room.
- **Manquant :** modèles Gson (`VoiceTurnResponse`, `LocationBatchResponse`, DTO Retrofit), interfaces Retrofit, OkHttp/Gson génériques → risque crash release sur désérialisation.
- `signing.properties` gitignoré ; sans fichier → APK unsigned (warning Gradle).

---

### 6. Parité API (Android ↔ backend)

| Domaine | Backend | Client Android | Auth |
|---|---|---|---|
| Pairing / session / push / capabilities | OK | OK | Bearer / public pairing |
| Location single + batch | OK | OK (Room + worker) | Bearer |
| Voice turn | OK | OK | Bearer |
| Chat create/send/confirm | OK | OK + offline queue | Bearer |
| Conversations CRUD/pin/archive | OK Bearer | OK | Bearer |
| WS streaming | Bearer | OK | Bearer |
| Briefing / tasks / calendar / notifs **GET** | OK Bearer | OK + Room cache | Bearer |
| Tasks POST/PATCH | existe | **FUTURE** | — |
| Calendar POST | existe | **FUTURE** | — |
| Notifs mark-read | existe | **FUTURE** (cache lecture seule) | — |
| Upload conversation | cookie web | **FUTURE** Bearer | — |
| Memory / people / devices | session | **FUTURE** | — |

---

### Inventaire FUTURE features

| ID | Flag Kotlin | Valeur | UI | Inerte ? | Branche flag lue ? |
|---|---|---|---|---|---|
| JARVIS-FUTURE-VOICE-CONTINUOUS | `CONTINUOUS_VOICE` | `false` | Voix · `JarvisComingSoonCard` | oui | oui |
| JARVIS-FUTURE-WAKE-ADVANCED | `WAKE_WORD_ADVANCED` | `false` | Réglages · `JarvisFutureAction` | oui | **non** (liste hardcodée) |
| JARVIS-FUTURE-LIVE-MAP | `LIVE_MAP` | `false` | Localisation · ComingSoon **toujours affiché** | oui | oui (texte seulement) |
| JARVIS-FUTURE-TRIPS-HISTORY | `TRIPS_HISTORY` | `false` | idem | oui | oui (texte seulement) |
| JARVIS-FUTURE-CALENDAR-CREATE | `CALENDAR_CREATE` | `false` | Agenda · FutureAction | oui | oui |
| JARVIS-FUTURE-TASKS-MUTATIONS | `TASKS_MUTATIONS` | `false` | Tâches · FutureAction | oui | oui |
| JARVIS-FUTURE-CHAT-ATTACHMENTS | `CHAT_ATTACHMENTS` | `false` | Composer · bouton disabled | oui | oui (`true` = bouton disparaît, pas d’upload) |
| JARVIS-FUTURE-SLASH-COMMANDS | `SLASH_COMMANDS` | `false` | Hint « bientôt » si `/` | oui | oui (`true` = hint off, pas de slash réel) |
| JARVIS-FUTURE-NOTIFICATIONS-ACTIONS | `NOTIFICATIONS_ACTIONS` | `false` | Notifs · FutureAction | oui | oui |
| JARVIS-FUTURE-MULTI-DEVICE | `MULTI_DEVICE` | `false` | Réglages | oui | **non** |
| JARVIS-FUTURE-OFFLINE-DETAIL | `OFFLINE_DETAIL` | `false` | Diagnostics | oui | oui |
| JARVIS-FUTURE-MEMORY-VIEW | `MEMORY_VIEW` | `false` | Plus · tuile `FuturePlaceholder` | oui (`onClick=null`) | **non** (kind hardcodé) |
| JARVIS-FUTURE-CONTACTS | `CONTACTS_VIEW` | `false` | Plus | oui | **non** |
| JARVIS-FUTURE-AUTOMATIONS | `AUTOMATIONS` | `false` | Plus | oui | **non** |
| JARVIS-FUTURE-WIDGETS | `HOME_WIDGETS` | `false` | Réglages | oui | **non** |
| JARVIS-FUTURE-DASHBOARD-CUSTOM | `DASHBOARD_CUSTOM` | `false` | Accueil | oui | oui |

**Règle respectée :** pas de données mockées, pas d’`onClick` actif sur placeholders.  
**Nuance :** Live Map / Trajets restent en ComingSoon même si flag `true` (« Activation en cours ») — fail-closed, légèrement trompeur.  
**Nuance :** 5 flags (`WAKE_*`, `MULTI_DEVICE`, `HOME_WIDGETS`, Memory/Contacts/Automations) ne pilotent pas l’affichage ; UI toujours placeholder.

---

### Tests — présents vs absents

| Zone | Présent | Absent / faible |
|---|---|---|
| URL / HTTPS reject | `ServerUrlNormalizerTest`, Voice rejects HTTP | — |
| Bearer voix | `VoiceRepositoryTest` | `JarvisRepository` générique |
| SecureStore / Keystore | `FakeSecretKeyProvider` helper | **aucun** test put/get/remove |
| JarvisTls / pinning | — | **aucun** |
| Chat WebSocket | — | **aucun** |
| Offline GPS / Room | Validator, Dedup, Dao, Migration, Worker constants | Coordinator E2E mock HTTP |
| SyncManager / ChatSync | — | **aucun** |
| Feature flags / FUTURE inertes | MoreMenuLogic, SettingsPresentation | pas de test « flag true → no crash » |
| Services / Boot / FCM | — | **aucun** |
| UI Compose instrumenté | More, Onboarding, Repair, Settings, Voice, Main no-WebView | Chat, Home, Location, Agenda |
| ProGuard / R8 | — | **aucun** smoke release |

Ordre de grandeur : **~77** `@Test` unitaires, **~10** instrumentés, **88** fichiers main Kotlin.

---

### Findings prioritaires

1. **HAUTE — R8 release** : minify sans keep Gson/DTO/Retrofit → risque régression production silencieuse.
2. **MOYENNE — pas EncryptedSharedPreferences** : store custom solide mais hors Jetpack Security ; clé utilisable sans auth utilisateur.
3. **MOYENNE — couverture tests** : auth store, TLS, WS, SyncManager, chat sync, services non couverts.
4. **BASSE — flags FUTURE orphelins** : More/Settings n’utilisent pas les `const` de `JarvisFeatureFlags`.
5. **BASSE — docs obsolètes** : `ARCHITECTURE.md` dit encore chat hors scope ; `PRODUCTION_GAP_ANALYSIS.md` figé en 1.2.0 alors que `2.1.0-ui` a chat+Room+WS.
6. **INFO — pas de pin cert** : documenté ; rotation = rebuild ; MITM possible si CA système compromise pour hôtes non-JARVIS (composite trust).

---

### Synthèse checklist → action

| Checklist | Statut audit |
|---|---|
| 1. Token EncryptedSharedPreferences ? | **Non** — AES-GCM + Keystore custom |
| 2. Pinning / cleartext | Cleartext **OK** ; pinning **non** (CA privée) |
| 3. FUTURE mensongère vs désactivée | **Désactivée / inerte** (pas mensongère) |
| 4. CSRF/cookie vs Bearer | **Bearer only**, CSRF N/A — correct |
| 5. Permissions runtime | **Conformes** |
| 6. Tests | **Présents sur cœur GPS/voix/UI logic** ; **absents** store/TLS/WS/sync/services |

Audit lecture seule — aucun commit / PR.