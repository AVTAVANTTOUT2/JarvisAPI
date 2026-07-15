# Périmètre du Companion Android (décision d'architecture)

## Cas A retenu — services mobiles uniquement

Le Companion Android **n'est pas** un client chat/voix. L'interface web JARVIS (`frontend/`, `/chat`, `/voice`) reste le canal conversationnel.

### Responsabilités du Companion

| Fonction | Statut |
|---|---|
| Pairage natif (code 6 chiffres + jeton Keystore) | Inclus |
| GPS / localisation arrière-plan | Inclus |
| Wake word Porcupine (local) | Inclus |
| Notifications FCM | Inclus (si `google-services.json`) |
| Capacités device (`/api/mobile/capabilities`) | Inclus |
| Chat / voix | **Hors scope** — utiliser le navigateur ou la PWA |

### Historique

- PR #18 : chat/voix via WebView (retiré).
- PR #22 : migration Kotlin/Compose native, tableau de bord services uniquement.

### TLS

Confiance à la **CA privée JARVIS** (`res/raw/jarvis_ca.crt`) injectée via `JarvisTls` pour l'hôte serveur configuré. Ce n'est **pas** du certificate pinning strict.

Rotation : regénérer avec `bash scripts/generate_ssl.sh`, puis `bash scripts/sync_android_ca.sh`, publier une nouvelle version Companion.

### Release

Voir `android/README.md` et `android/signing.properties.example`.
