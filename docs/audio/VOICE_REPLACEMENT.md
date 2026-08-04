# Remplacer la voix ou le moteur vocal

Deux opérations distinctes, souvent confondues : changer **la voix** (le timbre
de JARVIS) et changer **le moteur** (le logiciel qui la produit). La première
est une question de fichiers, la seconde une question de code.

## Changer la voix

Le profil vit dans `voices/jarvis-fr/` et n'est **jamais** versionné :

| Fichier | Rôle | Versionné |
|---|---|---|
| `metadata.json` | identité, langue, consentement | oui |
| `reference.wav` | échantillon de la voix, 24 kHz mono | **non** |
| `transcript.txt` | transcription exacte de l'échantillon | **non** |
| `reference.npy` / `.npz` | cache d'échantillons (héritage) | **non** |

```bash
python scripts/prepare_jarvis_voice.py   # source privée → profil complet
```

Trois contraintes qui ne se devinent pas :

- **La transcription doit correspondre exactement à l'échantillon.** Le mode
  `icl` la fournit au modèle pour aligner texte et audio ; une transcription
  approximative fait dériver la voix au lieu de l'affiner.
- **La fréquence de l'échantillon doit être celle du modèle** (24 kHz). Le
  sidecar charge par chemin de fichier précisément pour que mlx-audio lise la
  fréquence dans l'en-tête ; un tableau d'échantillons passé directement serait
  cru sur parole et produirait une voix transposée, sans aucune exception.
- **Une durée de référence plus longue n'améliore pas mécaniquement le
  résultat.** Mesuré : entre 6 s et 16 s, l'écart de latence est marginal
  (203 → 232 ms en `speaker_embedding`, 503 → 521 ms en `icl`) et la
  ressemblance ne suit pas une règle simple. Écoutez avant de trancher.

## Choisir le mode de clonage

```bash
TTS_CLONE_MODE=icl                # référence + transcription (défaut)
TTS_CLONE_MODE=speaker_embedding  # référence seule
```

Ce que le réglage change réellement : en `speaker_embedding`, la transcription
n'est plus transmise au modèle. C'est ce qui fait basculer mlx-audio hors de la
voie *in-context learning* — rien n'est modifié sur le disque, et revenir en
arrière est immédiat.

Mesuré sur Mac mini M4, même texte, même graine, mêmes paramètres :

| Mode | Référence | Premier PCM | RTF | Fragments |
|---|---:|---:|---:|---:|
| `speaker_embedding` | 5,96 s | 203 ms | 0,494 | 19 |
| `speaker_embedding` | 16,02 s | 232 ms | 0,508 | 19 |
| `icl` | 5,96 s | 503 ms | 0,553 | 17 |
| `icl` | 16,02 s | 521 ms | 0,591 | 15 |

Les quatre passent largement les seuils de temps réel (premier son < 1,5 s,
RTF < 1). Le mode par vecteur de locuteur est 2,4 fois plus rapide au premier
son parce qu'il évite le prefill de la référence. **La vitesse ne doit pas
décider seule** : les deux sont assez rapides, donc l'arbitrage porte sur le
timbre, qui ne se mesure pas.

Pour comparer sur votre machine :

```bash
# Les quatre variantes, fichiers privés non versionnés
ls data/voice-tests/qwen3-comparison/
afplay data/voice-tests/qwen3-comparison/01_embedding_6s.wav
afplay data/voice-tests/qwen3-comparison/02_embedding_16s.wav
afplay data/voice-tests/qwen3-comparison/03_icl_6s.wav
afplay data/voice-tests/qwen3-comparison/04_icl_16s.wav
```

## Changer le moteur

Le reste du dépôt ne nomme aucun moteur : il passe par la fabrique et consomme
des `AudioChunk`. Un remplacement demande donc trois gestes, et trois
seulement :

1. écrire un module sous `jarvis/audio/tts/backends/` qui satisfait
   `LocalTTSProvider` (`jarvis/audio/tts/base.py`) ;
2. l'enregistrer dans `_BUILDERS` (`factory.py`) et dans `KNOWN_PROVIDERS` ;
3. si le moteur a besoin d'un runtime séparé, ajouter un sidecar sous
   `native_audio/` — le protocole de trames, la réservation de stdout, la
   conversion PCM et la résolution des poids sont déjà dans
   `sidecar_protocol.py`.

La table des fournisseurs est **fermée** : un nom inconnu lève. C'est ce qui
rend impossible l'apparition d'un service distant par simple variable
d'environnement, et c'est aussi ce qui a permis de remplacer le moteur
précédent sans toucher au daemon, au lecteur, au barge-in ni à la voix.

### Ce qu'un candidat doit tenir

Le critère décisif n'est pas la taille du modèle mais le produit **trames par
seconde d'audio × coût d'une trame**, comparé à la bande passante mémoire de la
machine. Le moteur précédent demandait 21,53 trames/s à environ 8,8 Go lus par
trame, soit 189 Go/s soutenus, quand ce Mac mini M4 en délivre environ 70 : le
temps réel y était hors d'atteinte quelle que soit la qualité du code. Le
rapport complet est archivé dans
[archive/FISH_M4_VALIDATION.md](archive/FISH_M4_VALIDATION.md).

Exigences minimales pour un remplaçant :

- voix masculine française, clonage depuis un échantillon fourni ;
- Apple Silicon, sans CUDA ;
- premier son sous 1 s, RTF sous 1 ;
- diffusion incrémentale ;
- fonctionnement hors ligne complet, sans clé ni hôte.

Vérifiez sur la machine, pas sur la fiche du modèle :

```bash
python scripts/benchmark_tts.py --provider <nom> --runs 3
python -m pytest tests/test_qwen3_streaming.py -q   # diffusion et annulation réelles
```
