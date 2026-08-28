# JARVIS Android 2.1.0-ui — compagnon natif

Application **100 % native Kotlin** (Jetpack Compose) qui relie le téléphone au JARVIS du Mac via HTTPS.

> **Pas de WebView.** Vague 2 ajoute le chat texte complet (Room v2, WebSocket streaming, fallback HTTP, offline queue). Voir `docs/CHAT.md`, `docs/ARCHITECTURE.md`, `docs/OFFLINE_SYNC.md`, `docs/VOICE.md`.

## Fonctions

- pairage natif par code à six chiffres + onboarding multi-étapes ;
- jeton natif chiffré (Android Keystore) ;
- navigation BottomBar (Accueil, Chat, Voix, Agenda, Plus) ;
- **Chat texte** : conversations persistantes, streaming WS, fallback `POST /api/mobile/chat`, file offline ;
- Accueil : briefing / tâches / agenda / notifs via cache Room (Bearer) ;
- sync périodique WorkManager (accueil + chat) ;
- notifications FCM si `google-services.json` est présent ;
- présence GPS en arrière-plan (service de premier plan) ;
- détection locale du mot « JARVIS » (Porcupine) ;
- conversation vocale native (`POST /api/mobile/voice/turn`) — STT/TTS sur le Mac ;
- diagnostics (rapport sans secrets) ;
- confiance CA privée JARVIS — **pas** de certificate pinning strict.

Les tests JVM et les builds CI ne remplacent pas la validation sur appareil :
pairage, réseau HTTPS, reprise offline, localisation et audio doivent être
rejoués sur le matériel cible avant release.

## HTTPS côté Mac (obligatoire)

L'app refuse le HTTP. **Ne pas utiliser de pont TLS temporaire.**

```bash
bash scripts/generate_ssl.sh
bash scripts/sync_android_ca.sh
bash scripts/android_dev_https.sh
# ou via supervisor (charge .env.config + WEB_HTTPS)
python supervisor.py
bash scripts/verify_backend_https.sh
```

Émulateur : suggestion debug `https://10.0.2.2:8081` (à confirmer à la première ouverture).  
Téléphone physique : saisir l'URL Tailscale/LAN HTTPS (ex. `https://100.x.x.x:8081`).

## Audio (côté Mac)

Par défaut (aucune clé cloud audio) :

| Rôle | Moteur | Modèle / voix |
|---|---|---|
| STT | faster-whisper local | `small`, avec relecture `large-v3-turbo` si nécessaire |
| TTS | Qwen3-TTS local | `mlx-community/Qwen3-TTS-12Hz-0.6B-Base-6bit` |

Aucun repli réseau n'est autorisé. Voir
[`docs/audio/QWEN3_LOCAL_STATUS.md`](../docs/audio/QWEN3_LOCAL_STATUS.md) à la
racine du dépôt et [`docs/VOICE.md`](docs/VOICE.md) pour le contrat Android
courant.

## Build

```bash
cd android
# JDK 17+ requis (AGP 8.7). Exemples :
# export JAVA_HOME="/Applications/Android Studio.app/Contents/jbr/Contents/Home"
# export JAVA_HOME="/opt/homebrew/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home"
./gradlew clean assembleDebug testDebugUnitTest lintDebug
```

Documentation production :

- [`docs/PRODUCTION_GAP_ANALYSIS.md`](docs/PRODUCTION_GAP_ANALYSIS.md) — snapshot historique 1.2.0
- [`docs/API_CONTRACTS_PRODUCTION.md`](docs/API_CONTRACTS_PRODUCTION.md) — contrats FastAPI
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — architecture superseded
- [`docs/VOICE.md`](docs/VOICE.md) — voix PTT

| Artefact | Usage |
|---|---|
| `app/build/outputs/apk/debug/app-debug.apk` | Validation technique (cert debug) |
| `app/build/outputs/apk/release/app-release-unsigned.apk` | Release non signée si `signing.properties` absent — **non distribuable** |

Release signée : copier `signing.properties.example` → `signing.properties` (gitignoré).

Vérifier un APK :

```bash
apksigner verify --verbose --print-certs app/build/outputs/apk/debug/app-debug.apk
shasum -a 256 app/build/outputs/apk/debug/app-debug.apk
```

Version courante : **versionName 2.1.0-ui** / **versionCode 10**.

## Firebase (optionnel)

Sans `android/app/google-services.json`, le build définit `FIREBASE_CONFIGURED=false` et l'UI l'indique clairement.

## Tests

```bash
bash scripts/android_e2e_pairing.sh
pytest tests/test_mobile_pairing.py tests/test_mobile_voice.py
cd android && ./gradlew testDebugUnitTest lintDebug assembleDebug
```

Validation appareil réelle : installation APK + pairage HTTPS + un tour vocal
(micro → Whisper local → DeepSeek → Qwen3-TTS local → lecture). Voir
`RELEASE_CHECKLIST.md` à la racine du dépôt.
