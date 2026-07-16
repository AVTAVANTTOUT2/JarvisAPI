# Validation — Refonte UI Android JARVIS Companion

Branche : `feat/android-ui-redesign`  
Date : 2026-07-16  
Version : `2.1.0-ui` (`versionCode` 10)

## Build

```bash
export JAVA_HOME="/Applications/Android Studio.app/Contents/jbr/Contents/Home"
cd android
./gradlew assembleDebug testDebugUnitTest lintDebug connectedDebugAndroidTest
```

Prérequis : JDK 17+ (AGP 8.7). Java 8 échoue à la configuration.

## Résultats

| Commande | Résultat |
|---|---|
| `assembleDebug` | SUCCESS |
| `testDebugUnitTest` | SUCCESS |
| `lintDebug` | SUCCESS |
| `connectedDebugAndroidTest` | SUCCESS — 10/10 sur SM-S921B (Android 15) |

## APK

```text
android/app/build/outputs/apk/debug/app-debug.apk
```

SHA-256 (voir aussi `APK_SHA256.txt`) :

```text
9405951ba1a2b08952de80f70245d4999b8dd93fac7dc9de63b829b0d38e660e
```

## Appareil

- Samsung Galaxy S24 (`SM_S921B`, serial `RFCX51B4HHM`)
- Install : `adb -s RFCX51B4HHM install -r app/build/outputs/apk/debug/app-debug.apk`
- Données conservées (`-r`, pas d'`uninstall`)

## Captures

`android/docs/validation/screenshots/`

- `01-launch.png` — onboarding / lancement
- `02-home.png` … `06-voice.png` — navigation bas de barre
- `07-onboarding-contrast.png` — contraste textes onboarding

## Checklist manuelle

- [x] Lancement
- [x] Install S24 sans wipe
- [x] Design system glass / orbe visibles
- [x] Tests instrumentés verts sur device
- [ ] Re-appairage + parcours Accueil/Chat/Voix après token (état onboarding selon token local)
- [ ] Portrait + paysage approfondis
- [ ] Mode avion → bannières offline
