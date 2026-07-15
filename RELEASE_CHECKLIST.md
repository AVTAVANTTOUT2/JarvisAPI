# Release checklist — JARVIS / Companion Android

Checklist obligatoire avant chaque publication (tag Git + release GitHub + APK).  
Ne cocher une case que lorsque la preuve existe (commande, log, SHA, capture).

## Identité de la release

| Champ | Valeur |
|---|---|
| Date | |
| Tag Git | `companion-vX.Y.Z` |
| Commit (`main`) | |
| versionName | |
| versionCode | |
| SHA-256 APK | |
| Signature | debug / release signée |

## 1. Git propre

- [ ] `git fetch --all --prune --tags`
- [ ] Branche `main` ; working tree clean
- [ ] `git rev-parse HEAD` == `git rev-parse origin/main`
- [ ] Aucun conflit, aucun stash oublié non archivé
- [ ] Aucune PR Android ouverte liée à la release

## 2. Cohérence dépôt

- [ ] Tags et releases GitHub alignés sur `main`
- [ ] Anciennes APK obsolètes marquées / retirées
- [ ] Protections `main` actives (status checks obligatoires)
- [ ] Branches de travail fusionnées ou fermées

## 3. CI verte

- [ ] **Tests Python (pytest)** — succès
- [ ] **Frontend (typecheck + build)** — succès
- [ ] **Frontend unifié (tests + build)** — succès
- [ ] **Android (assemble + tests + lint)** — succès  
  (`assembleDebug`, `testDebugUnitTest`, `lintDebug`)
- [ ] Aucun merge possible si un job échoue

## 4. Tests locaux

- [ ] Backend : `python -m pytest tests/test_mobile_voice.py tests/test_mobile_pairing.py tests/test_audio_defaults.py -q`
- [ ] Android : `./gradlew assembleDebug testDebugUnitTest lintDebug`

## 5. Pile audio (preuves)

- [ ] STT : Faster-Whisper
- [ ] Modèle STT : `large-v3-turbo` (repli local éventuel : `large-v3` uniquement)
- [ ] TTS : Kokoro
- [ ] Voix : `af_nicole`
- [ ] Sortie TTS = **WAV** (`RIFF…`)
- [ ] Aucun fallback Edge pour `TTS_ENGINE=kokoro`
- [ ] Aucun fallback ElevenLabs / cloud audio
- [ ] Preuve jointe (chemin fichier / log / timestamp) :

```text
# exemple
python - <<'PY'
import asyncio, config
from audio.tts import get_tts_by_name
e = get_tts_by_name("kokoro")
b = asyncio.run(e.synthesize("Preuve release."))
assert e.get_backend_name() == "kokoro" and b[:4] == b"RIFF"
print(config.STT_MODEL, config.KOKORO_VOICE, len(b))
PY
```

## 6. APK

- [ ] Rebuild **uniquement** depuis le commit `main` de la release
- [ ] SHA-256 calculé et recopié dans la release GitHub
- [ ] `apksigner verify` OK
- [ ] versionName / versionCode cohérents avec le tag

## 7. Sécurité

- [ ] Scan secrets (Gitleaks ou équivalent) sur l’historique
- [ ] Pas de clé privée / token / `.env` trackés
- [ ] CA Android = certificat **public** uniquement (`jarvis_ca.crt`)
- [ ] Dépôt public : revue PII / URLs personnelles

## 8. Validation téléphone réel

> Ne jamais cocher sans appareil physique et preuves.

- [ ] Installation APK
- [ ] Pairage
- [ ] HTTPS
- [ ] Capture micro
- [ ] Upload tour vocal
- [ ] Whisper `large-v3-turbo`
- [ ] DeepSeek
- [ ] Kokoro + lecture
- [ ] Deuxième tour avec contexte (`conversation_id`)
- [ ] Révocation
- [ ] Permissions
- [ ] Rotation écran
- [ ] Coupure réseau
- [ ] Réveil après verrouillage

Si non exécuté : laisser **non coché** et l’indiquer dans les notes de release.

## 9. Documentation

- [ ] `README.md`
- [ ] `Architecture/` (INDEX + docs Android / audio si impact)
- [ ] `android/README.md`, `android/docs/*`
- [ ] `.env.config.example` / `.env.example`
- [ ] Guides installation / audio alignés sur le code

## 10. Publication

- [ ] Tag Git annoté `companion-vX.Y.Z` sur le commit publié
- [ ] Release GitHub avec APK + SHA-256 + notes
- [ ] Rollback possible (tag précédent + APK précédente conservée)

## Décision

- [ ] **GO production** — toutes les cases critiques cochées
- [ ] **GO avec réserve** — préciser les validations manuelles restantes
- [ ] **NO-GO** — bloquant(s) :

```text

```
