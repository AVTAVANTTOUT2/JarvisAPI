---
id: release_build
version: 2.0.0
date: 2026-07-16
domain: release
variables: user_request,acceptance_criteria,required_tests,context_files,extra_context,repo_rules,result_format,date,template_version
---

# Build de release

# Objectif
{{user_request}}

# Contexte
Date: {{date}}
Template version: {{template_version}}
Artefacts de release JARVIS : frontend Next.js 15 (`pnpm build` dans `frontend/` → `frontend/out`), fallback Vite (`pnpm build` dans `web/` → `web/dist`), app Android (`./gradlew assembleRelease` dans `android/`, signature via `signing.properties`). La checklist officielle vit dans `RELEASE_CHECKLIST.md` à la racine.

Contexte JARVIS :
{{extra_context}}

# Symptômes / preuve disponible
- Ouvre `RELEASE_CHECKLIST.md` en PREMIER et déroule chaque item dans l'ordre ; le rapport final reprend la checklist avec un verdict par item.
- État du dépôt avant build : branche, commit exact (`git rev-parse HEAD`), arbre propre ou non — consigne ces informations, un build sur arbre sale doit être signalé.
- Vérifie les versions déclarées (versionCode/versionName Android, versions des package.json) et leur cohérence avant de builder.

# Périmètre
- Builds dans l'ordre : backend (suite pytest verte), `pnpm build` dans `frontend/`, `pnpm build` dans `web/`, `cd android && ./gradlew assembleRelease`.
- Consigner pour chaque build : commande exacte, durée, résultat, chemin et taille de l'artefact produit.
- Tagger UNIQUEMENT si la demande l'exige explicitement, au format déjà utilisé par le dépôt (vérifier `git tag --list` avant d'inventer un format).

# Hors périmètre
- JAMAIS de publication automatique : pas de push de tag, pas d'upload d'APK, pas de déploiement, pas de release GitHub — la publication est une décision humaine.
- Corriger des bugs découverts pendant le build : les consigner comme bloqueurs de release dans le rapport (un correctif = un autre job avec le template adapté).
- Toucher aux secrets de signature (`signing.properties`, keystores) : les lire pour vérifier leur présence, jamais les modifier ni les copier.

# Fichiers probables
{{context_files}}
- `RELEASE_CHECKLIST.md`, `frontend/package.json`, `web/package.json`, `android/app/build.gradle` (versionCode/versionName), `android/signing.properties.example`, `.github/workflows/ci.yml` (parité avec la CI).

# Règles d'architecture
{{repo_rules}}
- Un build de release se fait sur un commit précis et le rapport doit permettre de le reproduire à l'identique.

# Critères d'acceptation
{{acceptance_criteria}}
- Chaque item de `RELEASE_CHECKLIST.md` a un verdict explicite (OK / KO / non applicable + raison).
- Tous les artefacts listés existent aux chemins attendus (`frontend/out`, `web/dist`, APK sous `android/app/build/outputs/`).

# Tests obligatoires
{{required_tests}}
- Suite backend complète (`pytest tests/ -q`) verte avant tout build d'artefact.
- `./gradlew testDebugUnitTest` vert avant `assembleRelease`.

# Validation réelle
- Vérifier physiquement chaque artefact : présence, taille non nulle, horodatage postérieur au début du build.
- Frontend : contrôler que `frontend/out` contient bien l'export statique attendu (pages HTML + `_next/static`).
- Android : vérifier que l'APK est signé (ou consigner qu'il ne l'est pas et pourquoi).
- Aucun build ne doit avoir modifié de fichier source (git status propre après builds, hors artefacts ignorés).

# Stratégie Git
- Jamais de modification directe de main.
- Travailler uniquement dans le worktree / la branche fournie.
- Commits clairs si des fichiers de version sont mis à jour ; tag local uniquement si demandé, jamais poussé automatiquement.

# Format du rapport final (OBLIGATOIRE)
{{result_format}}

## Qualité constante
- Chercher la cause racine d'un build qui échoue, ne pas relancer en boucle en espérant.
- Ne pas masquer le problème : un warning de build inhabituel se consigne, ne se supprime pas de la sortie.
- Ne rien inventer : chaque verdict de checklist correspond à une vérification réellement faite.
- Ne pas supprimer une fonction existante sans preuve.
- Respecter les conventions du dépôt ; tester avant de builder.
- Comparer avec la release précédente si disponible (tailles, nombre de pages) ; préserver les contrats existants.
- Rapport précis ; ne pas déclarer COMPLETED sans preuve (chemins + tailles des artefacts, checklist déroulée).
