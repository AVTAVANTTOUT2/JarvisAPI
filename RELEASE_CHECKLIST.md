# Release checklist — JARVIS backend / Companion Android

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
- [ ] Aucune PR ouverte liée à la release

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

- [ ] Backend standard : `.venv/bin/python -m pytest tests/ jarvis/tests agents/devagent -q`
- [ ] Dépendances reproductibles : `.venv/bin/python -m pip check`
- [ ] Lint : `.venv/bin/ruff check .`
- [ ] Android : `./gradlew assembleDebug testDebugUnitTest lintDebug`

## 5. Pile audio (preuves)

- [ ] STT : Faster-Whisper
- [ ] Modèle STT : `large-v3-turbo` (repli local éventuel : `large-v3` uniquement)
- [ ] TTS : Qwen3 local (`TTS_ENGINE=qwen3_local`)
- [ ] Modèle TTS : `Qwen3-TTS-12Hz-0.6B-Base-6bit`, présent localement
- [ ] Profil vocal : `voices/jarvis-fr` vérifié sans exposer ses données privées
- [ ] Sortie TTS streamée = PCM16 mono, 24 kHz
- [ ] Aucun fallback implicite entre fournisseurs TTS
- [ ] Aucun fallback vers un fournisseur cloud audio retiré
- [ ] Porte matérielle Qwen3 exécutée **daemon JARVIS arrêté** : `.venv/bin/python -m pytest -m integration_tts -v`
- [ ] Campagne 24 h : `.venv/bin/python tools/run_release_soak.py --duration-hours 24 --output artifacts/release_soak.json`
- [ ] `artifacts/release_soak.json` archivé avec zéro dépassement du budget d'échecs retenu
- [ ] Preuves jointes (artefact / log / timestamp) :

```text
# Exemple :
# - artifacts/release_soak.json
# - sortie pytest du marqueur integration_tts
# - timestamp et identifiant du Mac/appareil physique
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

## 8. Validations matérielles réelles

> Ne jamais cocher sans appareil physique et preuves.

- [ ] Installation APK
- [ ] Pairage
- [ ] HTTPS
- [ ] Capture micro
- [ ] Upload tour vocal
- [ ] Whisper `large-v3-turbo`
- [ ] DeepSeek
- [ ] Qwen3 + lecture
- [ ] Deuxième tour avec contexte (`conversation_id`)
- [ ] Révocation
- [ ] Permissions
- [ ] Rotation écran
- [ ] Coupure réseau
- [ ] Réveil après verrouillage
- [ ] Mac cible : permission micro refusée puis réaccordée
- [ ] Mac cible : déconnexion/reconnexion du périphérique audio
- [ ] Mac cible : enregistrements 1/30/180 minutes
- [ ] Mac cible : observation 24 h sans crash loop ni saturation de file

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
