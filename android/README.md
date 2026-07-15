# JARVIS Android 1.0 — Galaxy S24

Application native privée qui relie le téléphone au JARVIS du Mac via Tailscale HTTPS.

## Fonctions

- pairage natif par code à six chiffres, sans conserver le PIN JARVIS sur le téléphone ;
- jeton natif chiffré par Android Keystore et cookie web recréé automatiquement ;
- notifications FCM lorsque l'interface ou l'application est fermée ;
- présence GPS en arrière-plan par service de premier plan économe ;
- détection locale du mot « JARVIS » avec Porcupine ;
- certificat JARVIS intégré : toute autre chaîne HTTPS est refusée ;
- révocation du téléphone et de toutes ses sessions depuis l'interface web ;
- build release signé pour installation privée.

## Configuration Firebase obligatoire pour le push réel

1. Créer une application Android Firebase avec l'identifiant `fr.jarvis.companion`.
2. Placer son fichier public `google-services.json` dans `android/app/google-services.json`.
3. Télécharger un compte de service Firebase Admin dans
   `credentials/firebase-service-account.json`.
4. Ajouter dans `.env` :

```dotenv
FCM_SERVICE_ACCOUNT_FILE=./credentials/firebase-service-account.json
FCM_PROJECT_ID=identifiant-du-projet
```

Ces deux fichiers sont ignorés par Git. Sans eux, le même APK reste utilisable mais
le build indique `FIREBASE_CONFIGURED=false` et n'enregistre aucun jeton FCM.

## Certificat HTTPS

Le certificat public actuel est intégré dans
`app/src/main/res/raw/jarvis_ca.crt`. Il correspond à `certs/cert.pem` :

```text
SHA-256 6E:88:09:46:58:B2:92:88:BA:9E:30:3F:44:90:3A:19:
        E0:42:31:C1:A0:E3:99:0C:DB:E2:C6:B7:D8:B6:E8:4C
SAN     100.123.50.38, 127.0.0.1, localhost
Expire  17 octobre 2028
```

Quand le certificat change, remplacer aussi la ressource Android et reconstruire
l'APK. L'application ne propose jamais de contourner une erreur TLS.

## Build release privé

Le fichier local ignoré `android/signing.properties` doit contenir :

```properties
storeFile=release.keystore
storePassword=mot-de-passe-fort
keyAlias=jarvis-release
keyPassword=mot-de-passe-fort
```

Puis créer la clé une seule fois et la sauvegarder hors du Mac :

```bash
keytool -genkeypair -v \
  -keystore android/release.keystore \
  -alias jarvis-release -keyalg RSA -keysize 4096 -validity 10000

cd android
JAVA_HOME="/Applications/Android Studio.app/Contents/jbr/Contents/Home" \
  ./gradlew clean assembleRelease
```

APK : `app/build/outputs/apk/release/app-release.apk`.

## Premier pairage

1. Démarrer JARVIS avec HTTPS sur le Mac et connecter Mac + S24 à Tailscale.
2. Dans l'interface web : `Téléphone` puis `Générer un code`.
3. Ouvrir l'APK, conserver `https://100.123.50.38:8081` et saisir le code.
4. Dans les réglages natifs JARVIS, activer GPS H24 puis sélectionner
   `Toujours autoriser` dans les réglages Android.
5. Pour le mot-clé, créer une AccessKey sur Picovoice Console et la coller dans
   les réglages natifs. La clé est chiffrée par Android Keystore et l'audio reste local.

Sur Samsung, régler également `Batterie > Limites utilisation arrière-plan` afin
que JARVIS ne soit pas placé dans les applications en veille profonde. Android exige
une ouverture visible de l'application après un redémarrage avant de réactiver le
micro ; JARVIS affiche alors une notification de réactivation. Le GPS reprend seul.
