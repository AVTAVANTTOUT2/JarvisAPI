# Fish Audio local sur Apple Silicon — état réel

**Statut au 3 août 2026 : intégration écrite et câblée, non validée sur cette
machine.** Les poids n'ont pas pu être installés (voir « Ce qui bloque »).
Aucune mesure de latence Fish n'est donc rapportée ici — il n'y en a pas.

Ce document dit ce qui est vrai, y compris ce qui manque.

## Ce qui existe et fonctionne

| Élément | État |
|---|---|
| Implémentation MLX de Fish Speech | présente : `mlx_audio.tts.models.fish_qwen3_omni` (mlx-audio 0.4.5, installé dans `~/mlx-env`) |
| Backend JARVIS | `jarvis/audio/tts/backends/fish_local.py` |
| Sidecar | `native_audio/fish_local.py` + lanceur `native_audio/fish_synthesize` |
| Dépendance CUDA | **aucune** — l'implémentation est en MLX pur, GPU Metal |
| Appel réseau au runtime | **aucun** — `HF_HUB_OFFLINE=1`, résolution locale des poids |
| Erreur si poids absents | explicite, avec la commande d'installation |

## Ce qui bloque la validation

Le modèle `mlx-community/fish-audio-s2-pro-8bit` pèse **6,73 Go**. Sur la
liaison de cette machine, mesuré le 3 août 2026 :

| Source | Débit mesuré |
|---|---|
| Cloudflare (référence) | ~105 ko/s |
| Hugging Face (`model.safetensors`) | ~0,7 ko/s, connexions coupées régulièrement |

Le téléchargement a atteint 64 Mo en une quarantaine de minutes avant d'être
interrompu. À ce rythme, l'installation complète demande plus de vingt-quatre
heures. Ce n'est pas un défaut de l'intégration : c'est la bande passante
disponible.

Les octets déjà obtenus sont conservés et la reprise fonctionne :

```bash
python scripts/download_tts_model.py            # reprend où il s'est arrêté
python scripts/download_tts_model.py --check    # présent ? complet ?
```

## Ce qu'il reste à vérifier une fois les poids installés

La commande unique qui répond à tout :

```bash
python scripts/benchmark_tts.py --provider fish_local --runs 4 --json fish.json
```

Elle produit, pour de vrai : chargement du modèle, premier fragment audio,
synthèse totale, facteur temps réel, mémoire résidente du moteur — à froid et
à chaud, sans aucun mock.

Éléments à confirmer avant de déclarer Fish validé :

| Point | Comment | Attendu |
|---|---|---|
| Aucun CUDA requis | `info().device` | `mlx` |
| Mémoire réelle | ligne « moteur : … Mo résidents » du banc | doit tenir sur 32 Go avec le STT chargé |
| Premier son | `median_first_chunk_ms_warm` | à comparer aux 318 ms mesurés sur `current_local` |
| Facteur temps réel | `median_real_time_factor_warm` | < 1, sinon la lecture attendra |
| Qualité française | écoute d'une phrase FR | prononciation, liaisons, nombres |

## Identité exacte de ce qui est intégré

| Champ | Valeur |
|---|---|
| Paquet | `mlx-audio` 0.4.5 (`~/mlx-env`, Python 3.14) |
| Module modèle | `mlx_audio/tts/models/fish_qwen3_omni/fish_speech.py` |
| Dépôt de poids | `mlx-community/fish-audio-s2-pro-8bit` (6,73 Go) — variante bf16 : 11,01 Go |
| Format | safetensors quantifiés 8 bits + `codec.safetensors` (1,87 Go) |
| Moteur d'inférence | MLX (Metal), aucun CUDA |
| Fréquence déclarée | lue dans `config.json` du modèle, transmise au pipeline |
| Licence du code | Apache 2.0 (mlx-audio) |
| Licence des poids | **Fish Audio Research License** — recherche et usage non commercial. Un usage commercial exige un accord distinct avec Fish Audio. Aucun poids n'est redistribué par ce dépôt. |
| Lancement reproductible | `native_audio/fish_synthesize --serve --model <dir>` |

## Limite technique connue, indépendante du téléchargement

L'implémentation MLX **ne diffuse pas** au niveau du jeton :

```python
# mlx_audio/tts/models/fish_qwen3_omni/fish_speech.py
if stream:
    raise NotImplementedError("Fish Speech streaming is not implemented yet.")
```

Le backend déclare donc `streaming="segmented"` et non `"native"`. JARVIS
découpe le texte et joue chaque segment dès qu'il est prêt : le premier son
arrive avant la fin de la réponse, mais le mérite revient au segmenteur, pas au
modèle. Le jour où l'amont implémentera le mode `stream`, seul ce backend
changera.

## Conclusion honnête

```text
Fish Audio local non validé sur Apple Silicon : poids non installés.
Architecture locale prête, backend Fish écrit et câblé, non activé.
Backend actif en attendant : current_local (transitoire).
```

Ce qui **est** démontré sur ce Mac mini M4 : l'architecture locale fonctionne
de bout en bout, hors ligne, avec préchauffage, diffusion par segment,
annulation corrélée et métriques — mesures dans `LOCAL_TTS_ARCHITECTURE.md`.

Ce qui **n'est pas** démontré : que Fish Audio S2 Pro tient la latence sur
cette machine. Personne ne peut l'affirmer tant que le banc n'a pas tourné.
