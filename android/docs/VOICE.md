# Conversation vocale Android

**Revue :** 27 août 2026
**Statut :** push-to-talk implémenté ; validation appareil réelle requise.

La source exécutable est
`android/app/src/main/kotlin/fr/jarvis/companion/voice/` côté téléphone et
`api/router_mobile_voice.py` + `api/mobile_voice_service.py` côté Mac.

## Flux courant

```text
Microphone Android (AAC/M4A mono)
  → POST /api/mobile/voice/turn (HTTPS, Bearer natif)
  → STT local Mac (faster-whisper par défaut)
  → pipeline JARVIS (voice_mode=true)
  → TTS local Qwen3-TTS via MLX
  → JSON { transcript, response_text, audio_base64, stt_engine }
  → lecture MediaPlayer sur le téléphone
```

Aucun repli TTS réseau n’est autorisé. Le moteur canonique, son modèle et les
preuves locales sont documentés dans
[`docs/audio/QWEN3_LOCAL_STATUS.md`](../../docs/audio/QWEN3_LOCAL_STATUS.md).

## Configuration de référence

Les exemples publics vivent dans `.env.config.example` et `.env.example` :

```env
STT_ENGINE=faster-whisper
AUDIO_DAEMON_STT_ENGINE=faster-whisper
TTS_PROVIDER=qwen3_local
TTS_MODEL_PATH=mlx-community/Qwen3-TTS-12Hz-0.6B-Base-6bit
```

Les limites du tour mobile sont pilotées par
`MOBILE_VOICE_MAX_BYTES`, `MOBILE_VOICE_MIN_BYTES`,
`MOBILE_VOICE_MAX_DURATION_SEC`, `MOBILE_VOICE_STT_TIMEOUT_SEC`,
`MOBILE_VOICE_LLM_TIMEOUT_SEC` et `MOBILE_VOICE_TTS_TIMEOUT_SEC`.
Le code de `config.py` fait foi pour leurs valeurs.

## Interaction Android

- premier tap : démarrer l’enregistrement ;
- second tap / bouton STOP : arrêter et envoyer ;
- bouton Annuler : supprimer le fichier temporaire et revenir à l’état initial ;
- le wake word Porcupine ouvre l’écran vocal sans conserver un flux micro
  distant permanent ;
- le jeton reste dans `JarvisSecureStore` (Android Keystore) ;
- aucune WebView.

## Validation

```bash
python -m pytest tests/test_mobile_voice.py -q
cd android && ./gradlew testDebugUnitTest lintDebug assembleDebug
```

Ces contrôles ne prouvent pas le microphone, le haut-parleur, les permissions,
la latence, la veille ni la reconnexion du téléphone cible. La matrice
matérielle doit archiver un enregistrement, sa transcription, la réponse audio
Qwen3, l’annulation et la reprise réseau avant release.
