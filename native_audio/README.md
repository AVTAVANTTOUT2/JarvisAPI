# Sidecars audio natifs optionnels

JARVIS n'installe et ne télécharge aucun modèle pendant une conversation. Sans
sidecar, le STT revient à faster-whisper local et le TTS à Kokoro ou `say`.

## WhisperKit

Compilez un binaire `whisperkit_transcribe` et placez-le ici, ou installez
`jarvis-whisperkit` dans le PATH.

Le sidecar doit accepter :

```
whisperkit_transcribe --input /path/to.wav --model large-v3-v20240930_626MB --language fr [--prompt "..."]
```

Et imprimer sur stdout un JSON :

```json
{"text": "...", "segments": [], "language": "fr"}
```

Python (`native_audio/whisperkit_bridge.py`) supervise l'appel ; aucun téléchargement
de modèle n'est déclenché automatiquement par JARVIS.

## TTSKit

Placez un binaire exécutable `ttskit_synthesize` dans ce dossier, ou installez
`jarvis-ttskit` dans le PATH. Le sidecar reçoit le texte et doit écrire sur
stdout du PCM signé 16 bits, mono, 24 kHz au fil de sa génération :

```
ttskit_synthesize --model qwen3-tts-0.6b --language fr \
  --format pcm_s16le --sample-rate 24000 --text "Bonjour Monsieur."
```

Les logs éventuels vont sur stderr. Le processus est interrompu si la lecture
est annulée.
