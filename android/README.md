# JARVIS Android 0.1

Prototype local pour Galaxy S24. Il charge l'interface JARVIS du Mac via
Tailscale et ajoute les permissions Android pour le micro, le GPS et les fichiers.

## Construire

```bash
cd android
./gradlew assembleDebug
```

APK : `app/build/outputs/apk/debug/app-debug.apk`.

## Installer

1. Connecter le Mac et le S24 au même réseau Tailscale.
2. Installer l'APK debug sur le S24.
3. Conserver l'adresse proposée : `https://100.123.50.38:8081`.
4. Vérifier le serveur avant d'accepter le certificat local pour la session.
5. Déverrouiller JARVIS avec le secret habituel.

Empreinte SHA-256 actuelle du certificat JARVIS :

`6E:88:09:46:58:B2:92:88:BA:9E:30:3F:44:90:3A:19:E0:42:31:C1:A0:E3:99:0C:DB:E2:C6:B7:D8:B6:E8:4C`

## Limites 0.1

- session web par cookie avant le pairage natif ;
- pas encore de FCM application fermée ;
- pas encore de GPS ou wake word en arrière-plan ;
- certificat autosigné accepté explicitement par session ;
- APK debug, non destiné à une distribution publique.
