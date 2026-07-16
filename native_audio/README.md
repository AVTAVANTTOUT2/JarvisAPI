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

## TTSKit (Qwen3-TTS MLX)

Le dépôt fournit `native_audio/ttskit_synthesize` : sidecar Python qui exécute
**Qwen3-TTS-12Hz-0.6B-CustomVoice** via `mlx-audio` dans `JARVIS_VENV`
(défaut `~/mlx-env`). Aucun cloud, streaming PCM16 24 kHz sur stdout.

### Setup (une fois)

```bash
source ~/mlx-env/bin/activate   # ou $JARVIS_VENV
pip install -U 'mlx-audio>=0.3.0'
# Premier appel : télécharge le modèle HF (~cache Hugging Face)
```

### Config (`.env.config`)

```bash
TTS_ENGINE=ttskit
TTS_MODEL=qwen3-tts-0.6b
TTS_LANGUAGE=fr
TTS_SPEAKER=Ryan          # CustomVoice : Ryan | Aiden | Vivian | …
# TTS_MODEL_PATH=         # optionnel : chemin local du modèle déjà téléchargé
```

### Contrat CLI

```
ttskit_synthesize --model qwen3-tts-0.6b --language fr \
  --format pcm_s16le --sample-rate 24000 --text "Bonjour Monsieur."
```

Alias `--model qwen3-tts-0.6b` →
`mlx-community/Qwen3-TTS-12Hz-0.6B-CustomVoice-6bit`.
Pas d'`instruct` émotionnel (voix stable). Logs sur stderr ; le processus est
interrompu si la lecture est annulée.

Alternative : placez un autre binaire `ttskit_synthesize` ici, ou
`jarvis-ttskit` dans le PATH (même contrat).
