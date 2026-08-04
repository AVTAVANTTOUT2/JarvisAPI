# Sidecars audio natifs

JARVIS n'installe et ne télécharge aucun modèle pendant une conversation.

Ces sidecars tournent dans `JARVIS_VENV` (défaut `~/mlx-env`), un environnement
séparé de `venv/` : `mlx-audio` est propre à Apple Silicon et n'a rien à faire
dans les dépendances du serveur.

## Qwen3-TTS local — `qwen3_synthesize` (moteur vocal)

Sidecar du moteur vocal courant : `native_audio/qwen3_local.py`, lancé par
`native_audio/qwen3_synthesize`. Modèle chargé **une seule fois**, puis une
synthèse par requête JSON lue sur stdin, fragments PCM16 sur stdout.

```bash
# Mode serveur — celui qu'utilise le pipeline
native_audio/qwen3_synthesize --serve --model /chemin/vers/les/poids \
    --voice-dir ./voices/jarvis-fr

# Diagnostic : le modèle est-il installé et complet ?
native_audio/qwen3_synthesize --probe --model /chemin

# Une synthèse, WAV sur stdout
native_audio/qwen3_synthesize --model /chemin --voice-dir ./voices/jarvis-fr \
    --format wav --text "Bonjour Monsieur." > out.wav
```

Deux propriétés voulues :

- **Le profil vocal est passé par répertoire** (`--voice-dir`), pas par
  un argument. Le sidecar y lit lui-même `reference.wav` et `transcript.txt` ;
  passer le transcript en argument l'exposerait dans la sortie de `ps`.
- **L'échantillon est chargé par chemin de fichier, jamais par cache.**
  `load_audio` de mlx-audio renvoie un `mx.array` tel quel sans rien
  rééchantillonner : lui passer un tableau revient à affirmer qu'il est déjà à
  la fréquence du modèle. `jarvis-fr` est à 24 kHz comme Qwen3, donc aucune
  conversion n'a lieu aujourd'hui — mais un profil régénéré à une autre
  fréquence produirait une voix transposée sans lever la moindre exception.
  Passer par le fichier laisse mlx-audio lire la fréquence dans l'en-tête. Le
  cache `.npy` nu est ignoré puisqu'il ne porte aucune fréquence.

Installation des poids : `python scripts/download_tts_model.py` (Apache 2.0,
environ 1,9 Go).


## WhisperKit — `whisperkit_transcribe` (STT, optionnel)

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

Python (`native_audio/whisperkit_bridge.py`) supervise l'appel ; aucun
téléchargement de modèle n'est déclenché automatiquement par JARVIS.

## Setup du venv MLX (une fois)

```bash
python3.12 -m venv "${JARVIS_VENV:-$HOME/mlx-env}"
source "${JARVIS_VENV:-$HOME/mlx-env}/bin/activate"
python -m pip install -r requirements-mlx.txt
```
