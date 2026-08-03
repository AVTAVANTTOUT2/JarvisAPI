# Fish Audio local sur Apple Silicon — état réel

**Statut au 3 août 2026 : validé sur ce Mac mini M4.** Moteur unique
`fish_local`, voix `jarvis-fr` clonée, poids installés localement, démos et
banc de mesure produits.

## Ce qui est en place

| Élément | État |
|---|---|
| Backend unique | `fish_local` — `current_local` / Kokoro retirés |
| Voix active | `voices/jarvis-fr/` (metadata versionnée) |
| Préparation auto | `scripts/prepare_jarvis_voice.py` |
| Démos | `scripts/demo_jarvis_voice.py` → `data/voice-tests/` |
| Sidecar | `native_audio/fish_local.py` + `fish_synthesize` |
| Cache de référence | `reference.npy` / `.npz` chargé au warmup |
| Poids | `./models/fish-audio-s2-pro-8bit` (codec 1,87 Go + model 4,85 Go) |
| CUDA / cloud | aucun |
| Clé API TTS | aucune |

## Voix `jarvis-fr`

Profil créé automatiquement depuis
`VoixJARVIS_source_clonage_24k.wav` (master privé sous
`data/private/voice-sources/jarvis-fr/`) :

- extrait choisi par score (énergie, stabilité, absence de clipping) ;
- ~16 s de parole masculine française, phrases complètes ;
- transcript local (faster-whisper, hors modification du moteur STT) ;
- tenseur float32 pré-encodé pour un chargement immédiat ;
- `voice_cloned: true` confirmé sur les trois démos.

## Mesures (3 août 2026, Mac mini M4, modèle chaud)

```bash
PYTHONPATH=. JARVIS_VENV=~/mlx-env python scripts/benchmark_tts.py --runs 3
```

| Métrique | Valeur |
|---|---:|
| Chargement modèle | 6,9 s |
| Mémoire moteur (RSS) | ~802 Mo |
| Premier son à chaud (médiane) | **4 886 ms** |
| Facteur temps réel à chaud (médiane) | **5,75** |
| Fréquence audio | 44 100 Hz (déclarée par le modèle) |
| Diffusion | `segmented` (pas de stream natif MLX) |

Le RTF > 1 signifie que la synthèse est plus lente que le temps réel : la
lecture attend encore le moteur. La segmentation limite le silence initial sur
les réponses longues, mais le premier segment paie toujours ~3–5 s à chaud.

Démos locales (clonage confirmé) :

| Fichier | Premier fragment |
|---|---:|
| `data/voice-tests/demo_01_greeting.wav` | 11,8 s (inclut warmup cold path) |
| `data/voice-tests/demo_02_status.wav` | 9,8 s |
| `data/voice-tests/demo_03_long.wav` | 15,4 s |

## Installation des poids

```bash
python scripts/download_tts_model.py            # ~6,7 Go, reprenable
python scripts/download_tts_model.py --check
# chemin local utilisé ici :
# TTS_MODEL_PATH=./models/fish-audio-s2-pro-8bit
```

## Diffusion

L'implémentation MLX ne diffuse pas au jeton (`NotImplementedError` sur
`stream=True`). JARVIS déclare `streaming="segmented"` et joue chaque segment
dès qu'il est prêt.

## Licence des poids

Fish Audio Research License — recherche / non commercial. Aucun poids n'est
redistribué par ce dépôt.
