# La voix de JARVIS

JARVIS a **une** voix. Pas de sélecteur, pas de catalogue : il est une entité,
et changer de timbre au fil des réponses le rendrait moins crédible, pas plus
utile.

## Emplacement

```text
voices/
└── jarvis-fr/
    ├── metadata.json      versionné
    ├── reference.wav      optionnel — jamais versionné
    ├── reference.npy      optionnel — cache float32, jamais versionné
    ├── reference.npz      optionnel — cache + sample_rate, jamais versionné
    └── transcript.txt     optionnel — jamais versionné
```

Configuré par `TTS_VOICE_PATH` (défaut `./voices/jarvis-fr`).

## Migration depuis `voices/jarvis`

Le profil par défaut s'appelait `voices/jarvis`. Les échantillons n'étant
jamais versionnés, une mise à jour les laisse dans l'ancien répertoire : le
nouveau profil est vide, et JARVIS repart sur la voix par défaut du modèle.
Il le dit — un avertissement au démarrage du moteur nomme les deux
répertoires — mais rien ne se répare tout seul. Deux issues :

```bash
python scripts/prepare_jarvis_voice.py   # régénère jarvis-fr depuis le master
                                         # et archive l'ancien profil sous
                                         # data/private/ (rien n'est supprimé)
```

ou, pour garder l'échantillon existant tel quel :

```bash
mv voices/jarvis/reference.wav voices/jarvis/transcript.txt voices/jarvis-fr/
rmdir voices/jarvis
```

## Sans échantillon

Le modèle parle avec sa voix par défaut. C'est volontaire : une installation
neuve doit pouvoir parler. `metadata.json` décrit alors l'intention (langue,
genre visé), pas un clonage.

## Avec échantillon

Le clonage vocal a besoin des **deux** fichiers :

- `reference.wav` — 10 à 30 secondes, mono, sans musique ni bruit de fond,
  débit de parole naturel ;
- `transcript.txt` — la transcription **exacte** de cet enregistrement, à la
  ponctuation près.

Un `reference.wav` sans transcript est refusé silencieusement : le modèle
devinerait l'alignement et la voix dériverait. JARVIS journalise alors un
avertissement et reste sur la voix par défaut — plutôt qu'un timbre approximatif
que vous n'avez pas choisi.

## Procédure automatique (recommandée)

```bash
# Placez le master WAV (préféré : VoixJARVIS_source_clonage_24k.wav) puis :
python scripts/prepare_jarvis_voice.py

# Le script analyse le master, choisit le meilleur extrait (10–30 s),
# transcrit en local, écrit voices/jarvis-fr/ et le cache reference.npy.
python scripts/download_tts_model.py   # une fois, ~6,7 Go
python scripts/demo_jarvis_voice.py    # WAV de démo dans data/voice-tests/
```

## Procédure manuelle

```bash
# 1. Enregistrer 20 s de parole naturelle (voix propre, pièce calme)
#    Format attendu : WAV mono. Exemple avec ffmpeg à partir d'un autre format :
ffmpeg -i source.m4a -ac 1 -ar 24000 voices/jarvis-fr/reference.wav

# 2. Écrire la transcription exacte
$EDITOR voices/jarvis-fr/transcript.txt

# 3. Redémarrer le daemon vocal, puis vérifier
python scripts/benchmark_tts.py --runs 2
```

Au démarrage, le sidecar journalise `voix de référence chargée (N échantillons
@ R Hz)`. Son absence dans les journaux signifie que l'échantillon n'a pas été
pris en compte.

## Licence et consentement — non négociable

Ne déposez ici qu'une voix dont l'usage vous est acquis :

- **votre propre voix** ;
- une voix **sous licence** explicite ;
- une voix enregistrée avec le **consentement écrit** de la personne ;
- une voix **originale** de synthèse.

Imiter la voix d'une personne réelle — célébrité, proche, collègue — sans son
accord n'est pas un cas d'usage supporté. Ce n'est pas une limite technique :
c'est la ligne que ce projet ne franchit pas.

Aucun fichier audio n'est versionné (`.gitignore`). `metadata.json` porte un
champ `license` et un champ `consent` : renseignez-les, ils servent à vous — ou
à quelqu'un d'autre — six mois plus tard.

Les poids du modèle ont leur propre licence, distincte de celle de la voix :
voir `FISH_LOCAL_STATUS.md`.

## Retirer une voix

Supprimez `reference.wav` et `transcript.txt`, redémarrez. JARVIS revient à la
voix par défaut du modèle. Le cache de synthèse spéculative s'invalide seul —
sa signature inclut le fournisseur et la voix, précisément pour éviter d'entendre
deux timbres dans la même conversation.
