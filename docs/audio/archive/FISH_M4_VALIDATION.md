# Fish Audio S2 Pro sur Mac mini M4 — rapport de validation archivé

```text
Status:      rejected for production on Mac mini M4
Reason:      physical throughput limit
Replacement: Qwen3-TTS-12Hz-0.6B-Base-6bit
Date:        2026-08-04
```

> Document **historique**. Aucun code actif du dépôt ne référence ce moteur ;
> un test structurel l'interdit hors de `docs/audio/archive/`. Il est conservé
> parce qu'un rejet motivé vaut mieux qu'un moteur qu'on réessaierait tous les
> six mois faute de trace.

**Le backend, son sidecar, ses lanceurs, sa configuration et ses poids ont été
retirés du dépôt.** Ce document conserve la mesure qui a motivé le retrait.

Le moteur de production est [Qwen3-TTS](../QWEN3_LOCAL_STATUS.md).

## Pourquoi : un plafond matériel, pas un défaut d'implémentation

Le codec de Fish (`fish_s1_dac`, `encoder_rates=[2,4,8,8]` puis
`downsample_factor=(2,2)`) impose **21,53 trames par seconde d'audio** — valeur
dérivée de la configuration puis confirmée par la mesure.

Chaque trame demande :

| Étape | Poids lus | Passes par trame | Total |
|---|---:|---:|---:|
| Backbone `fish_qwen3` (36 couches, dim 2560, ~4 G paramètres) | 4,26 Go | 1 | 4,26 Go |
| Décodeur de profondeur (4 couches, 10 codebooks) | 0,451 Go | 10 | 4,51 Go |
| **Par trame** | | | **8,77 Go** |

Bande passante mémoire soutenue mesurée sur cette machine, avec l'opération
exacte du décodage (`mx.quantized_matmul` 8 bits, 8192×8192) : **68 à 74 Go/s**,
contre 120 Go/s de pic théorique.

Atteindre le temps réel demanderait `8,77 Go × 21,53 = 189 Go/s` soutenus.

## La mesure qui clôt le débat

Passe de backbone isolée, modèle réellement chargé, cache chaud :

```
backbone 1 passe : 54,71 ms
```

Soit 4,26 Go / 54,7 ms = **78 Go/s** — le backbone tourne déjà au plafond de la
machine. Il n'y a rien à y optimiser.

Or `1 / 0,0547 = 18,3 trames/s`, alors qu'il en faut 21,53.

> **Même avec un décodeur audio de coût nul, Fish ne peut pas descendre sous un
> facteur temps réel de 1,18 sur cette machine.** Le décodeur ne peut pas être
> nul : ses dix passes coûtent 0,451 Go chacune.

Plancher réel avec une implémentation parfaite : `54,7 + 64 = 119 ms` par trame,
soit **RTF ≈ 2,6**. Mesuré en pratique : **4,0 à 5,7**.

## Mesures

Banc du dépôt, voix `jarvis-fr`, trois passages par phrase :

| | Fish S2 Pro | Qwen3-TTS |
|---|---:|---:|
| Chargement | 3 340 ms | 1 469 ms |
| Premier son (chaud, médiane) | 4 886 ms | 445 ms |
| Facteur temps réel | 5,75 | 0,524 |
| Poids sur disque | 6,7 Go | 1,7 Go |
| Diffusion | segmentée | native |
| Licence | recherche / non commercial | Apache 2.0 |

Sur les trois démos identiques : **18,2×, 17,9× et 26,3×** plus rapide au
premier son.

## Ce qui rendrait Fish viable

| Piste | RTF attendu | Verdict |
|---|---:|---|
| Optimisation logicielle parfaite | ~2,6 | insuffisant |
| Quantification 4 bits | ~1,35 | insuffisant, qualité dégradée |
| Mac mini M4 **Pro** (~160 Go/s réels) | ~1,2 | insuffisant |
| Mac Studio M4 **Max** (~330 Go/s réels) | ~0,57 | fonctionnerait |

Autrement dit : Fish demande une classe de machine supérieure, pas un meilleur
code.

## Licence des poids

Fish Audio Research License — recherche / non commercial. Aucun poids n'est
redistribué par ce dépôt.
