# Sidecar WhisperKit (optionnel)

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
