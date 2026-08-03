# Sidecars audio natifs

JARVIS n'installe et ne télécharge aucun modèle pendant une conversation.

Ces sidecars tournent dans `JARVIS_VENV` (défaut `~/mlx-env`), un environnement
séparé de `venv/` : `mlx-audio` est propre à Apple Silicon et n'a rien à faire
dans les dépendances du serveur.

## Qwen3-TTS local — `qwen3_synthesize` (moteur de production)

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

Deux différences avec le sidecar Fish, toutes deux voulues :

- **Le profil vocal est passé par répertoire** (`--voice-dir`), pas par
  `--ref-text`. Le sidecar y lit lui-même `reference.wav` et `transcript.txt` ;
  passer le transcript en argument l'exposait dans la sortie de `ps`.
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

## Fish Audio local — `fish_synthesize` (conservé, hors temps réel)

Fish reste sélectionnable via `TTS_PROVIDER=fish_local` mais n'est plus le
défaut. Mesuré sur ce Mac mini M4 : il produit 21,53 trames par seconde
d'audio, chacune coûtant une passe d'un backbone de 4 milliards de paramètres
(54,7 ms mesurés, soit déjà la bande passante mémoire maximale de la machine)
plus dix passes de son décodeur de profondeur. Même avec un décodeur gratuit il
plafonnerait à 18,3 trames/s. Facteur temps réel constaté : 4 à 5,7.

Installation : `python scripts/download_tts_model.py --engine fish`.


Sidecar du moteur vocal : `native_audio/fish_local.py`, lancé par
`native_audio/fish_synthesize`. Modèle chargé **une seule fois**, puis une
synthèse par requête JSON lue sur stdin, fragments PCM16 sur stdout.

```bash
# Mode serveur — celui qu'utilise le pipeline
native_audio/fish_synthesize --serve --model /chemin/vers/les/poids

# Diagnostic : le modèle est-il installé et complet ?
native_audio/fish_synthesize --probe

# Une synthèse, WAV sur stdout
native_audio/fish_synthesize --model /chemin --format wav --text "Bonjour Monsieur." > out.wav
```

Protocole du mode serveur : trame = tag ASCII 4 octets + longueur big-endian
4 octets + charge utile. `RDY` (métadonnées JSON : fréquence, canaux, voix
clonée), `CHK` (PCM16), `END`, `ERR`. Binaire de bout en bout — aucun encodage
texte ne peut corrompre l'audio.

`HF_HUB_OFFLINE=1` est forcé par le lanceur, et `resolve_local_model_dir`
n'accepte qu'un répertoire présent ou un dépôt déjà en cache, poids **complets**
vérifiés. Un modèle absent produit une erreur avec la commande d'installation,
jamais un téléchargement.

Installation des poids : `python scripts/download_tts_model.py`.
État réel de l'intégration : `docs/audio/FISH_LOCAL_STATUS.md`.

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
