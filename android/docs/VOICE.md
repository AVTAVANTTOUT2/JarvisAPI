# Conversation vocale Android

Push-to-talk natif entre le Companion Android et le Mac JARVIS.

## Flux

```text
Microphone Android (AAC/M4A mono 16 kHz)
  → POST /api/mobile/voice/turn (HTTPS, Bearer token natif)
  → STT local Mac (faster-whisper, modèle configuré)
  → pipeline JARVIS (_process_message_internal, voice_mode=true)
  → TTS Mac (Edge Henri FR par défaut ; macOS Thomas en local ; Kokoro = EN only)
  → JSON { transcript, response_text, audio_base64 }
  → lecture MediaPlayer sur le téléphone
```

## Configuration serveur (.env.config)

```env
AUDIO_DAEMON_STT_ENGINE=faster-whisper
AUDIO_DAEMON_STT_MODEL=large-v3-turbo
TTS_ENGINE=edge
TTS_VOICE=fr-FR-HenriNeural
WEB_HTTPS=true
```

> **Voix FR** : le modèle Kokoro installé (`v0.19`) n’a que des voix EN (`af_nicole`).
> Pour du français natif, utiliser `TTS_ENGINE=edge` (Henri) ou `macos` (Thomas).

## Interaction Android

- **Tap** sur le micro pour démarrer, **tap** à nouveau pour envoyer (pas besoin de maintenir).
- Bouton Annuler pendant l’enregistrement.

Limites (surchargeables) :

| Variable | Défaut |
|---|---|
| MOBILE_VOICE_MAX_BYTES | 5 Mo |
| MOBILE_VOICE_MIN_BYTES | 1000 |
| MOBILE_VOICE_STT_TIMEOUT_SEC | 120 |
| MOBILE_VOICE_LLM_TIMEOUT_SEC | 90 |
| MOBILE_VOICE_TTS_TIMEOUT_SEC | 60 |

## Android

- Écran **Conversation vocale** depuis le dashboard (appairage requis).
- Push-to-talk : maintenir le bouton micro, relâcher pour envoyer.
- Wake word Porcupine ouvre l'écran vocal sans enregistrer en arrière-plan.
- Aucune WebView ; token dans Android Keystore via `JarvisSecureStore`.

## Tests

```bash
python -m pytest tests/test_mobile_voice.py -q
cd android && ./gradlew test lintDebug assembleDebug
```

Validation appareil réelle : enregistrement + réponse Kokoro sur Mac avec modèles installés.
