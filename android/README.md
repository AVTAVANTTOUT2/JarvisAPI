# JARVIS Android 1.0.3 — compagnon natif Kotlin

Application **100 % native Kotlin** (Jetpack Compose) qui relie le téléphone au JARVIS du Mac via HTTPS.

> **Pas de WebView** — périmètre : appairage, GPS, wake word, FCM, statut. Chat/voix : interface web JARVIS. Voir `docs/ARCHITECTURE.md`.

## Fonctions

- pairage natif par code à six chiffres ;
- jeton natif chiffré (Android Keystore) ;
- notifications FCM si `google-services.json` est présent ;
- présence GPS en arrière-plan (service de premier plan) ;
- détection locale du mot « JARVIS » (Porcupine) ;
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

## Build

```bash
cd android
JAVA_HOME="/Applications/Android Studio.app/Contents/jbr/Contents/Home" \
  ./gradlew clean assembleDebug test assembleRelease
```

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

## Firebase (optionnel)

Sans `android/app/google-services.json`, le build définit `FIREBASE_CONFIGURED=false` et l'UI l'indique clairement.

## Test pairage automatisé (contrat backend)

```bash
bash scripts/android_e2e_pairing.sh
pytest tests/test_mobile_pairing.py
```
