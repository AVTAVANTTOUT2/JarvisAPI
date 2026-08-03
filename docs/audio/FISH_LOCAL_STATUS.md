# Fish Audio local sur Apple Silicon — état réel

**Statut au 3 août 2026 : moteur unique `fish_local`, voix `jarvis-fr`
préparée, poids en cours d'installation sur cette machine.**

## Ce qui est en place

| Élément | État |
|---|---|
| Backend unique | `fish_local` — `current_local` / Kokoro retirés |
| Voix active | `voices/jarvis-fr/` (metadata versionnée) |
| Préparation auto | `scripts/prepare_jarvis_voice.py` |
| Démos | `scripts/demo_jarvis_voice.py` → `data/voice-tests/` |
| Sidecar | `native_audio/fish_local.py` + `fish_synthesize` |
| Cache de référence | `reference.npy` / `.npz` chargé au warmup |
| CUDA / cloud | aucun |
| Clé API TTS | aucune |

## Voix `jarvis-fr`

Profil créé automatiquement depuis
`VoixJARVIS_source_clonage_24k.wav` (master privé sous
`data/private/voice-sources/jarvis-fr/`) :

- extrait choisi par score (énergie, stabilité, absence de clipping) ;
- ~16 s de parole masculine française, phrases complètes ;
- transcript local (faster-whisper, hors modification du moteur STT) ;
- tenseur float32 pré-encodé pour un chargement immédiat.

## Installation des poids

```bash
python scripts/download_tts_model.py            # ~6,7 Go, reprenable
python scripts/download_tts_model.py --check
# ou chemin local :
# TTS_MODEL_PATH=./models/fish-audio-s2-pro-8bit
```

Sur une liaison lente, le transfert peut prendre plusieurs heures. Ce n'est
pas un défaut de l'intégration.

## Validation une fois les poids présents

```bash
python scripts/benchmark_tts.py --runs 4 --json data/voice-tests/fish.json
python scripts/demo_jarvis_voice.py
```

## Diffusion

L'implémentation MLX ne diffuse pas au jeton (`NotImplementedError` sur
`stream=True`). JARVIS déclare `streaming="segmented"` et joue chaque segment
dès qu'il est prêt. Le premier son arrive avant la fin de la réponse.

## Licence des poids

Fish Audio Research License — recherche / non commercial. Aucun poids n'est
redistribué par ce dépôt.
