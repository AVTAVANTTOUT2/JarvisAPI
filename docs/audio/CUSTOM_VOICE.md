# La voix de JARVIS

JARVIS a **une** voix. Pas de sélecteur, pas de catalogue : il est une entité,
et changer de timbre au fil des réponses le rendrait moins crédible, pas plus
utile.

## Emplacement

```text
voices/
└── jarvis/
    ├── metadata.json      versionné
    ├── reference.wav      optionnel — jamais versionné
    └── transcript.txt     optionnel — jamais versionné
```

Configuré par `TTS_VOICE_PATH` (défaut `./voices/jarvis`).

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

## Procédure

```bash
# 1. Enregistrer 20 s de parole naturelle (voix propre, pièce calme)
#    Format attendu : WAV mono. Exemple avec ffmpeg à partir d'un autre format :
ffmpeg -i source.m4a -ac 1 -ar 44100 voices/jarvis/reference.wav

# 2. Écrire la transcription exacte
$EDITOR voices/jarvis/transcript.txt

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
