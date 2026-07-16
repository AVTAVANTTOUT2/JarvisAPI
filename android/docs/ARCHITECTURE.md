# Périmètre du Companion Android (décision d'architecture)

> **Évolution 2026-07-16** — branche `feature/android-production-app` : transformation vers client mobile production (navigation, offline Room, briefing, etc.).  
> État factuel 1.2.0 : [`PRODUCTION_GAP_ANALYSIS.md`](./PRODUCTION_GAP_ANALYSIS.md).  
> Contrats API : [`API_CONTRACTS_PRODUCTION.md`](./API_CONTRACTS_PRODUCTION.md).

## Cas retenu — services mobiles + voix native (baseline 1.2.0)

Le Companion Android est un client **natif Kotlin/Compose** (pas de WebView). En 1.2.0, l'interface web JARVIS reste le canal principal pour le chat texte et les écrans riches ; la voix courte sur téléphone passe par le Companion. La Vague 1+ étend ce périmètre sans WebView.

### Responsabilités du Companion

| Fonction | Statut |
|---|---|
| Pairage natif (code 6 chiffres + jeton Keystore) | Inclus |
| GPS / localisation arrière-plan | Inclus |
| Wake word Porcupine (local) | Inclus |
| Notifications FCM | Inclus (si `google-services.json`) |
| Capacités device (`/api/mobile/capabilities`) | Inclus |
| Conversation vocale push-to-talk | Inclus — `POST /api/mobile/voice/turn` |
| Chat texte multi-agents riche | Hors scope app — utiliser `frontend/` / PWA |

### Historique

- PR #18 : chat/voix via WebView (retiré).
- PR #22 : migration Kotlin/Compose native, tableau de bord services.
- PR #26 : conversation vocale native (micro → Mac STT/LLM/TTS → lecture).
- PR #27 / #28 : CI Android + extraction `SecretKeyProvider`.

### Stack réseau (v1.0.4)

- **OkHttp** + **Retrofit** + fonctions **suspend** dans `JarvisRepository`
- **JarvisTls** : confiance CA privée JARVIS (pas de certificate pinning strict)
- ViewModel et services n'appellent plus `JarvisApi` directement (supprimé)
- Voix : détail dans [`VOICE.md`](./VOICE.md)

Rotation TLS : régénérer avec `bash scripts/generate_ssl.sh`, puis `bash scripts/sync_android_ca.sh`, publier une nouvelle version Companion.

### Release

Voir `android/README.md`, `android/signing.properties.example` et `RELEASE_CHECKLIST.md` (racine du dépôt).
