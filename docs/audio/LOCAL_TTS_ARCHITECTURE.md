# Synthèse vocale locale — architecture

JARVIS parle sans réseau. Aucune clé d'API, aucun hôte de service vocal, aucun
téléchargement pendant une conversation. Débranchez le Mac : la voix continue.

## Le pipeline

```text
LLM (flux de jetons)
    │
    ▼
TextStreamSegmenter          jarvis/audio/tts/segmenter.py
    │  segments prononçables (30 / 80 / 180 caractères, délai de vidage 250 ms)
    ▼
LocalTTSProvider             jarvis/audio/tts/base.py      ← le seul contrat connu du reste
    │
    ▼
Backend local                jarvis/audio/tts/backends/    ← qwen3_local
    │  sidecar chaud, modèle chargé une fois
    ▼
AudioChunk (PCM16 mono)      jarvis/audio/tts/base.py
    │
    ▼
Lecteur CoreAudio            jarvis/audio/tts/playback.py → audio/audio_output.py
```

Le reste de JARVIS — daemon vocal, WebSocket, API mobile, appareils distants —
ne connaît ni Qwen3, ni MLX, ni aucun moteur. Il appelle :

```python
from jarvis.audio.tts import get_local_tts_provider

provider = get_local_tts_provider()
await provider.warmup()
async for chunk in provider.stream(text, request_id=rid, utterance_id=uid):
    ...
```

## Ce que le contrat garantit

| Élément | Garantie | Pourquoi |
|---|---|---|
| `AudioChunk` | porte fréquence, canaux, encodage, marqueur de fin | la sortie ouvre son flux avant le premier échantillon ; une fréquence devinée déforme la voix sans jamais lever d'exception |
| `warmup()` | charge le modèle hors tour de parole | sinon le premier énoncé paie plusieurs secondes, au pire moment |
| `cancel(request_id)` | annulation **corrélée** | une annulation tardive ne doit pas couper la réponse suivante |
| `info()` | déclare `streaming`, `offline`, modèle, voix | on ne devine rien à partir du nom du moteur |
| `close()` | libère modèle et sous-processus | un redémarrage du daemon doit être propre |

## Deux natures de diffusion, nommées

`info().streaming` vaut `native` ou `segmented`, et ce n'est pas cosmétique :

- **`native`** — le modèle rend l'audio au fil de la génération.
- **`segmented`** — JARVIS découpe le texte et joue chaque segment dès qu'il
  est synthétisé pendant que le suivant se génère.

Le backend `qwen3_local` est `native` : le modèle rend l'audio par blocs de
`streaming_interval × 12,5` trames, décodés incrémentalement par le tokenizer
de parole. La valeur `segmented` reste déclarable — le moteur précédent l'était,
son implémentation levant `NotImplementedError` sur `stream` — et annoncer
« natif » à sa place aurait été faux.

Le segmenteur reste en place et reste utile avec un backend natif : il borne la
longueur du premier énoncé envoyé au moteur et permet d'annuler à une frontière
propre. Il n'est simplement plus la seule source de fragments.

**Mesuré** sur Mac mini M4, voix `jarvis-fr`, trois passages par phrase :
premier son à **523 ms** (médiane à chaud), facteur temps réel **0,564**. Le
détail est dans [QWEN3_LOCAL_STATUS.md](QWEN3_LOCAL_STATUS.md) ; le rapport de
rejet du moteur précédent, plafonné par la bande passante mémoire de la
machine, dans [archive/FISH_M4_VALIDATION.md](archive/FISH_M4_VALIDATION.md).

### Le premier segment obéit à des seuils plus courts

`TTS_FIRST_CHUNK_MIN_CHARS=15` / `TTS_FIRST_CHUNK_MAX_CHARS=60` — et une
virgule y suffit à couper, alors qu'elle est refusée ensuite sous la taille
cible. Ce n'est pas une incohérence : le premier segment est le seul dont la
longueur se paie en **silence pur**, pendant que l'utilisateur attend. Les
suivants se synthétisent derrière une lecture déjà commencée, donc leur durée
ne s'entend pas.

Mesuré sur une phrase de 94 caractères sans point interne (« Il fait dix-huit
degrés à Lille, ciel couvert, et une averse est attendue en fin d'après-midi. ») :

| Seuils | Premier son | Synthèse totale |
|---|---:|---:|
| uniformes (30/80/180) | 564 ms | 583 ms |
| premier segment court | **242 ms** | 585 ms |

Le coût est réel et assumé : plus de segments = un peu plus de temps de
synthèse **total** (sur la réponse longue, 1 095 ms → 1 316 ms). Ce temps est
masqué par la lecture déjà en cours ; le silence initial, lui, ne l'est pas.

## Contre-pression

`play_chunks` alimente la file bornée de `audio/audio_output.py` (16 fragments).
Un moteur plus rapide que la lecture est donc naturellement freiné : il ne
remplit pas la mémoire, et il prend de l'avance juste ce qu'il faut pour que la
lecture ne s'interrompe jamais entre deux segments.

## Le chemin fichier, séparé du chemin temps réel

Le navigateur, le téléphone et les appareils distants reçoivent un **fichier
WAV complet** (`jarvis/audio/tts/wav.py`), pas un flux : des fragments WAV
concaténés ne forment pas un fichier valide, contrairement au MP3. Le WAV est
retenu parce qu'il n'exige aucun encodeur installé.

Le tour de parole local — celui dont la latence compte — ne passe jamais par
là : il diffuse fragment par fragment.

## Interruption

Sur barge-in, trois choses arrivent dans cet ordre : la lecture s'arrête
(`native_audio_output.stop()`), la requête est annulée
(`provider.cancel(request_id)`), les fragments restants sont drainés sans être
livrés.

Le drainage est délibéré : cesser de lire le tuyau du sidecar laisserait des
trames orphelines, et la requête suivante lirait l'audio de la précédente. Le
gaspillage est borné à un segment déjà lancé ; l'utilisateur, lui, entend le
silence immédiatement.

## Échec : pas de repli, un état

Un modèle absent ou une synthèse échouée ne déclenche **aucune** bascule vers
un autre moteur ni vers un service distant. La réponse texte est conservée, le
pipeline se réarme, et l'état est visible :

```json
GET /api/status → { "audio": { "tts_provider": "qwen3_local", "tts_offline": true, … } }
```

Faire parler l'utilisateur avec une voix qu'il n'a pas choisie, sans le lui
dire, serait pire qu'un silence expliqué.

Erreurs déclarées (`jarvis/audio/tts/errors.py`) : `TTSUnavailableError`,
`TTSModelNotFoundError`, `TTSUnsupportedDeviceError`, `TTSSynthesisError`,
`TTSCancelledError`.

## Configuration

Un seul jeu de réglages, quel que soit le backend. Aucune clé, aucune URL.

```bash
TTS_PROVIDER=qwen3_local
TTS_MODEL_PATH=mlx-community/Qwen3-TTS-12Hz-0.6B-Base-6bit
TTS_VOICE_PATH=./voices/jarvis-fr
TTS_DEVICE=auto                  # auto | mlx | cpu — jamais cuda
TTS_STREAMING=true
TTS_SAMPLE_RATE=24000            # indicatif : la valeur du modèle fait foi
TTS_CHANNELS=1
TTS_WARMUP=true
TTS_TIMEOUT_SECONDS=30
TTS_MIN_CHUNK_CHARS=30
TTS_TARGET_CHUNK_CHARS=80
TTS_MAX_CHUNK_CHARS=180
TTS_FLUSH_TIMEOUT_MS=250
TTS_FIRST_CHUNK_MIN_CHARS=15     # premier segment : seuils plus courts
TTS_FIRST_CHUNK_MAX_CHARS=60
```

Les anciennes variables (`TTS_ENGINE`, `TTS_VOICE`, `EDGE_TTS_*`,
`MACOS_TTS_VOICE`, `KOKORO_BACKEND`…) sont déclarées dans
`config.RETIRED_ENV_VARS` : un `.env` qui les définit encore reçoit un
avertissement au démarrage plutôt qu'un silence trompeur.

## Installation hors ligne

```bash
# 1. Runtime MLX (une fois)
python3.12 -m venv ~/mlx-env && source ~/mlx-env/bin/activate
pip install mlx-audio soundfile

# 2. Poids (≈ 1,9 Go) — jamais déclenché par JARVIS
python scripts/download_tts_model.py
python scripts/download_tts_model.py --check   # vérifie sans rien écrire

# 3. Mesure réelle sur la machine
python scripts/benchmark_tts.py
```

Emplacements : les poids vont dans le cache Hugging Face
(`~/.cache/huggingface/hub`) ou dans le répertoire passé à `--dest` ; la voix
vit dans `voices/jarvis-fr/`.

`resolve_local_model_dir` n'accepte qu'un répertoire existant ou un dépôt déjà
en cache, et vérifie que les poids sont **complets** — un téléchargement
interrompu s'arrête presque toujours sur les gros fichiers, et un cache
partiel déclarerait à tort le modèle installé. Le sidecar part avec
`HF_HUB_OFFLINE=1`.

## Instrumentation

Douze événements (`jarvis/audio/tts/events.py`), du fournisseur créé à la
lecture terminée, avec une **allowlist de champs** : longueurs, noms de moteur,
durées, identifiants de corrélation. Jamais un texte, jamais une clé — la
propriété est vérifiée par un test, pas par une convention.

La chronologie du tour de parole complet reste dans `audio/voice_latency.py`,
avec `end_of_speech_to_first_audio_ms` comme métrique principale et une
distinction stricte entre : fin de VAD, STT, orchestration, premier jeton LLM,
premier segment texte, premier fragment TTS, début réel de lecture.

## Remplacer le backend

1. écrire un module dans `jarvis/audio/tts/backends/` qui satisfait
   `LocalTTSProvider` ;
2. l'enregistrer dans `_BUILDERS` (`factory.py`) et dans `KNOWN_PROVIDERS` ;
3. lancer `python scripts/benchmark_tts.py --provider <nom>` ;
4. supprimer l'ancien module et ses variables.

Rien d'autre ne bouge : ni le VAD, ni le STT, ni l'orchestration, ni le lecteur
audio, ni l'API. C'est la seule promesse que cette architecture doit tenir.

## Dépannage

| Symptôme | Cause probable | Geste |
|---|---|---|
| `TTSModelNotFoundError` au démarrage | poids absents ou incomplets | `python scripts/download_tts_model.py` |
| `venv MLX introuvable` | `JARVIS_VENV` ne pointe pas sur un venv avec `mlx-audio` | `pip install mlx-audio` dans ce venv |
| `TTSUnsupportedDeviceError` | `TTS_DEVICE=cuda` | `TTS_DEVICE=auto` : Apple Silicon passe par Metal |
| Voix par défaut au lieu de la vôtre | `reference.wav` sans `transcript.txt` | voir `CUSTOM_VOICE.md` |
| Silence, aucune erreur | sortie audio indisponible | vérifier `AUDIO_DAEMON_OUTPUT_DEVICE` et les journaux `[audio_output]` |
| Premier énoncé très lent | préchauffage désactivé | `TTS_WARMUP=true` |
