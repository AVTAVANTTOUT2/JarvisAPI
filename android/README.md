# JARVIS Android 1.2.0 — compagnon natif Kotlin

Application **100 % native Kotlin** (Jetpack Compose) qui relie le téléphone au JARVIS du Mac via HTTPS.

> **Pas de WebView** — périmètre : appairage, GPS, wake word, FCM, statut, **conversation vocale push-to-talk**. Voir `docs/ARCHITECTURE.md` et `docs/VOICE.md`.

## Fonctions

- pairage natif par code à six chiffres ;
- jeton natif chiffré (Android Keystore) ;
- notifications FCM si `google-services.json` est présent ;
- présence GPS en arrière-plan (service de premier plan) ;
- détection locale du mot « JARVIS » (Porcupine) ;
- conversation vocale native (`POST /api/mobile/voice/turn`) — STT/TTS sur le Mac ;
- confiance CA privée JARVIS (`JarvisTls` + `res/raw/jarvis_ca.crt`) — **pas** de certificate pinning strict ;
- révocation depuis l'interface web JARVIS.

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
| STT | faster-whisper | `large-v3-turbo` |
| TTS | Edge Henri (FR) / macOS Thomas | WAV/MP3/M4A |

Pas de repli Edge ni fournisseur cloud audio retiré lorsque `TTS_ENGINE=kokoro`. Voir `docs/VOICE.md` et `native_audio/README.md`.

## Build

```bash
cd android
# JDK 17+ requis (AGP 8.7). Exemples :
# export JAVA_HOME="/Applications/Android Studio.app/Contents/jbr/Contents/Home"
# export JAVA_HOME="/opt/homebrew/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home"
./gradlew clean assembleDebug testDebugUnitTest lintDebug
```

Documentation production :

- [`docs/PRODUCTION_GAP_ANALYSIS.md`](docs/PRODUCTION_GAP_ANALYSIS.md) — audit code 1.2.0
- [`docs/API_CONTRACTS_PRODUCTION.md`](docs/API_CONTRACTS_PRODUCTION.md) — contrats FastAPI
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — périmètre
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

Version courante : **versionName 1.2.0** / **versionCode 7**.

## Firebase (optionnel)

Sans `android/app/google-services.json`, le build définit `FIREBASE_CONFIGURED=false` et l'UI l'indique clairement.

## Tests

```bash
bash scripts/android_e2e_pairing.sh
pytest tests/test_mobile_pairing.py tests/test_mobile_voice.py
cd android && ./gradlew testDebugUnitTest lintDebug assembleDebug
```

Validation appareil réelle : installation APK + pairage HTTPS + un tour vocal (micro → Whisper → DeepSeek → Kokoro → lecture). Voir `RELEASE_CHECKLIST.md` à la racine du dépôt.
