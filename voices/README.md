# Voix de JARVIS

Une seule voix est active : `voices/jarvis-fr/`. Il n'y a pas de sélecteur
multi-voix, et il n'y en aura pas — JARVIS est une entité, pas un catalogue.

## Contenu

| Fichier | Obligatoire | Rôle |
|---|---|---|
| `metadata.json` | oui | identité, langue, licence, consentement |
| `reference.wav` | non | échantillon de la voix à cloner (10–30 s, mono, propre) |
| `reference.npy` / `.npz` | non | cache float32 pré-encodé (warmup rapide) |
| `transcript.txt` | non | transcription **exacte** de `reference.wav` |

Préparation automatique depuis un master WAV :

```bash
python scripts/prepare_jarvis_voice.py
```

Tant que `reference.wav` est absent, le moteur parle avec la voix par défaut du
modèle. C'est volontaire : une installation neuve doit pouvoir parler.

`reference.wav` sans `transcript.txt` ne suffit pas — le clonage a besoin des
deux, sinon l'alignement est deviné et la voix dérive. Dans ce cas JARVIS
journalise un avertissement et reste sur la voix par défaut.

## Licence et consentement

Les fichiers audio ne sont **pas** versionnés (voir `.gitignore`). Ne déposez
ici qu'une voix dont l'usage vous est acquis :

- votre propre voix ;
- une voix sous licence explicite ;
- une voix enregistrée avec le consentement écrit de la personne ;
- une voix originale de synthèse.

Imiter la voix d'une personne réelle sans son accord — célébrité ou non — n'est
pas un cas d'usage supporté.

Procédure détaillée : `docs/audio/CUSTOM_VOICE.md`.
