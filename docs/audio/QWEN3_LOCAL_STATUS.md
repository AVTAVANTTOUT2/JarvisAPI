# Qwen3-TTS local sur Apple Silicon — état réel

**Statut au 4 août 2026 : moteur unique de production, voix `jarvis-fr` clonée,
poids installés, mesures validées sur Mac mini M4 (10 cœurs, 32 Go).**

## Installation validée

| Élément | Valeur |
|---|---|
| Backend | `qwen3_local` |
| Modèle | `mlx-community/Qwen3-TTS-12Hz-0.6B-Base-6bit` |
| Révision | `4e44ed4bcee28a0f89a493e07bde16e6dccd43eb` |
| Poids | 1,7 Go — `models/qwen3-tts-12hz-0.6b-base-6bit` |
| Runtime | `mlx-audio 0.4.5` (`mlx 0.31.2`, `mlx-lm 0.31.3`) |
| Sidecar | `native_audio/qwen3_local.py` + `qwen3_synthesize` |
| Fréquence | 24 000 Hz — celle du pipeline, sans conversion |
| Débit de trames | 12,5 Hz |
| Réseau | aucun — `HF_HUB_OFFLINE=1` forcé par le lanceur |
| Clé API | aucune |
| Licence | Apache 2.0 |

## Voix `jarvis-fr` — mode de clonage réel

`voice_cloned: true` ne dit pas *comment* la voix est reproduite. Le chemin
effectif a été établi par instrumentation du modèle chargé, pas par lecture du
code :

| Champ | Valeur |
|---|---|
| `clone_mode` | **`icl+speaker_embedding`** |
| Fichier de référence | `voices/jarvis-fr/reference.wav` |
| Durée de la référence | **6 960 ms** (167 062 échantillons à 24 kHz) |
| Transcript | `voices/jarvis-fr/transcript.txt` — 133 caractères, texte humain vérifié |
| `reference_text_used` | `true` |
| Langue transmise | `french` |
| Diffusion | `native`, intervalle 0,4 s (5 trames) |

Le mode n'est pas un choix binaire. mlx-audio emprunte la voie *in-context
learning* dès que la référence **et** son transcript sont fournis et que le
tokenizer de parole porte un encodeur (`has_encoder = True`) ; cette voie
appelle **en plus** `extract_speaker_embedding` sur la référence. Les deux
mécanismes opèrent ensemble.

La voie ICL relève d'office la pénalité de répétition à 1,5 pour éviter la
dégénérescence des codes sur un préfixe long — la valeur demandée (1,05) est
donc ignorée, volontairement, côté mlx-audio.

## Pourquoi une référence de 7 s et non de 16

Le profil a d'abord porté 16,02 s d'échantillon. Quatre variantes ont été
générées avec le même texte, la même graine et les mêmes paramètres, puis
écoutées ; le propriétaire de la voix a retenu le mode ICL avec référence
courte. La référence a donc été recoupée sur une **fin de phrase** — 6,84 s,
plus 120 ms pour ne pas rogner la consonne finale — au lieu d'une coupe
arbitraire au milieu d'un mot.

Le gain n'est pas seulement subjectif : le prefill ICL est proportionnel à la
longueur de la référence.

| Référence | Premier son (médiane) | RTF |
|---|---:|---:|
| 16,02 s | 523 ms | 0,564 |
| **6,96 s** | **445 ms** | **0,524** |

Un détail a été corrigé au passage, et il comptait. La transcription de la
variante courte avait été produite automatiquement et comportait une erreur :
« Je lui ai volontiers de préparer un café » au lieu de « J'aurais volontiers
préparé un café ». En mode ICL, c'est la transcription qui aligne le texte sur
l'audio — une erreur y fait dériver la voix au lieu de l'affiner. Le transcript
de production est repris **mot pour mot** du texte humain vérifié.

Les caches `reference.npy` / `.npz` ont été supprimés du profil : ils portaient
les 16 s d'origine, ce backend ne les lit pas, et les conserver aurait laissé
croire à une source de vérité qui n'en est plus une.

## Paramètres d'inférence

```
temperature          0.9
top_k                50
top_p                1.0
max_tokens           4096
repetition_penalty   1.05 demandée -> 1.5 imposée par la voie ICL
streaming_interval   0.4 s
streaming_context    25
lang_code            french
```

## Mesures — Mac mini M4, trois passages par phrase

```
Chargement du modèle        1 223 ms   (moteur 1 957 Mo résidents)
Premier son (chaud, médiane)  445 ms   cible < 1 500, idéal < 800
Facteur temps réel (médiane)   0,524   cible < 1, idéal < 0,6
```

| Phrase | Premier son | RTF |
|---|---:|---:|
| courte (20 car.) | 442–450 ms | 0,548 |
| moyenne (100 car.) | 444–445 ms | 0,513–0,525 |
| longue (multi-phrases) | 443–445 ms | 0,511–0,523 |

Le premier son ne dépend plus de la longueur de l'énoncé : 442 à 450 ms sur les
trois phrases. C'est le signe que le coût fixe — prefill de la référence, puis
premier bloc de diffusion — domine, et qu'il est court.

Le RTF plus élevé des phrases courtes n'est pas une anomalie : l'amorçage,
constant, pèse proportionnellement plus sur un énoncé bref.

Un RTF de 0,524 signifie qu'une seconde d'audio se produit en 0,52 s : la
synthèse prend de l'avance sur la lecture au lieu d'accumuler du retard. C'est
la condition de la conversation continue.

## Chaîne vocale complète

Mesuré sur parole humaine réelle (5,96 s de français) et sur le vrai moteur :

| Maillon | À froid | À chaud | Nature |
|---|---:|---:|---|
| STT `large-v3-turbo` | 5 052 ms | **2 631 ms** | mesuré |
| Orchestration + file | — | 25 ms | mesuré |
| Premier token LLM DeepSeek | — | 2 218 ms | mesuré, variance 1 270–9 323 ms |
| Premier PCM | 1 223 ms (warmup) | **445 ms** | mesuré |
| **Fin de parole → premier son** | — | **≈ 5 320 ms** | **composition** |

La dernière ligne est une composition arithmétique, **pas** une mesure unique
de bout en bout : piloter le VAD et le micro demande une intervention humaine,
et le maillon LLM est un appel distant que la suite de tests bloque
délibérément. Présenter une somme comme une mesure serait malhonnête.

Ce que le changement de moteur déplace :

```
avant  2631 + 25 + 2218 + 4886  ≈  9 760 ms   TTS = 50 % du total
après  2631 + 25 + 2218 +  445  ≈  5 320 ms   TTS =  8 % du total
```

Environ 4,44 secondes retirées, et le goulot change de nature : le TTS n'est
plus le maillon dominant, le **STT** l'est. Le passage du modèle STT à `small`
le ramènerait à ~600 ms pour une transcription identique sur les énoncés de
test — c'est le prochain gain évident, et il est hors de ce lot.

## Deux pièges désamorcés

**La langue doit être nommée.** `lang_code="auto"` — le défaut de mlx-audio —
ne résout **aucun** identifiant de langue : le conditionnement disparaît sans
avertissement et le modèle devine d'après le texte. Le talker connaît dix
langues en toutes lettres (`codec_language_id`) tandis que JARVIS les code sur
deux lettres ; une table de correspondance et une validation au chargement
évitent qu'une faute de frappe coûte la prosodie en silence.

**L'échantillon se charge par chemin de fichier, jamais par cache.**
`load_audio` de mlx-audio renvoie un `mx.array` tel quel, sans le convertir :
lui passer un tableau revient à affirmer qu'il est déjà à la fréquence du
modèle. `jarvis-fr` est à 24 kHz comme Qwen3, donc aucune conversion n'a lieu
aujourd'hui — mais un profil régénéré à une autre fréquence produirait une voix
transposée sans lever la moindre exception. Le cache `.npy` nu est ignoré
puisqu'il ne porte aucune fréquence.

## Journal de démarrage

Le warmup publie l'état vocal effectif, côté sidecar comme côté fournisseur :

```
[qwen3-local] Qwen3 voice ready
[qwen3-local] voice=jarvis-fr
[qwen3-local] clone_mode=icl+speaker_embedding
[qwen3-local] reference_duration_ms=6960
[qwen3-local] reference_text_used=true
[qwen3-local] language=french
[qwen3-local] streaming=native
[qwen3-local] streaming_interval_s=0.4
[qwen3-local] frame_rate_hz=12.5
[qwen3-local] sample_rate=24000
```

## Installation et validation

```bash
python scripts/download_tts_model.py             # ~1,9 Go, reprenable
python scripts/download_tts_model.py --check
python scripts/benchmark_tts.py --provider qwen3_local --runs 3
```

Les poids ne sont déclarés présents qu'une fois le transfert **complet** :
la vérification refuse tout répertoire portant encore un `.incomplete`.

## Limites assumées

- Les mesures portent sur un Mac mini M4 base (10 cœurs, 32 Go). Une machine
  plus lente déplacerait le RTF proportionnellement à sa bande passante.
- L'annulation prend effet à la frontière d'un bloc de diffusion (0,4 s) ; la
  lecture, elle, s'arrête immédiatement.
- Le jugement de qualité vocale n'est pas automatisable : il reste une écoute
  humaine des démos de `data/voice-tests/qwen3/`.
