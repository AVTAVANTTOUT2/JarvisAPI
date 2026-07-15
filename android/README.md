# JARVIS Android 1.0.2 — compagnon natif Kotlin

Application **100 % native Kotlin** (Jetpack Compose) qui relie le téléphone au JARVIS du Mac via HTTPS.

> **Pas de WebView** : l'interface est un tableau de bord natif (appairage, GPS, wake word, statut FCM).

## Fonctions

- pairage natif par code à six chiffres ;
- jeton natif chiffré (Android Keystore) ;
- notifications FCM si `google-services.json` est présent ;
- présence GPS en arrière-plan (service de premier plan) ;
- détection locale du mot « JARVIS » (Porcupine) ;
- certificat JARVIS intégré (`res/raw/jarvis_ca.crt`) — pas de contournement TLS ;
- révocation depuis l'interface web JARVIS.

## HTTPS côté Mac (obligatoire)

L'app refuse le HTTP. **Ne pas utiliser de pont TLS temporaire.**

```bash
# Certificats + sync CA Android
bash scripts/generate_ssl.sh
bash scripts/sync_android_ca.sh

# Démarrer JARVIS en HTTPS natif
bash scripts/android_dev_https.sh
```

Émulateur Android : adresse par défaut `https://10.0.2.2:8081` (debug).  
Téléphone physique (Tailscale) : `https://100.123.50.38:8081` (release).

## Build

```bash
cd android
JAVA_HOME="/Applications/Android Studio.app/Contents/jbr/Contents/Home" \
  ./gradlew clean assembleDebug test
cp app/build/outputs/apk/debug/app-debug.apk ../jarvis-companion.apk
```

Release signée : voir `signing.properties` (gitignoré).

## Firebase (optionnel)

Sans `android/app/google-services.json`, le build définit `FIREBASE_CONFIGURED=false` et l'UI l'indique clairement.
