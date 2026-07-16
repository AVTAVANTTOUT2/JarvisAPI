---
id: android_feature
version: 2.0.0
date: 2026-07-16
domain: android
variables: user_request,acceptance_criteria,required_tests,context_files,extra_context,repo_rules,result_format,date,template_version
---

# Fonctionnalité Android

# Objectif
{{user_request}}

# Contexte
Date: {{date}}
Template version: {{template_version}}
App : `android/` — Kotlin, Jetpack Compose, Room (file offline-first), Retrofit/OkHttp vers le backend JARVIS avec Bearer token. Packages sous `android/app/src/main/kotlin/fr/companion/` : `app`, `core`, `data`, `feature`, `navigation`, `network`, `notifications`, `receivers`, `services`, `ui`, `voice`.

Contexte JARVIS :
{{extra_context}}

# Symptômes / preuve disponible
- Avant de coder, lis le feature package le plus proche de la demande et copie sa structure (écran Compose + ViewModel + repository + DAO Room si persistance).
- Vérifie le contrat backend correspondant côté serveur (`api/router_*.py`) : ne code jamais contre un endpoint supposé — cite la route réelle dans le rapport.
- État de compilation initial : lance `./gradlew assembleDebug` AVANT tes changements pour distinguer les erreurs préexistantes des tiennes.

# Périmètre
- Écrans en Compose ; logique dans des ViewModels TESTABLES : aucune dépendance Android directe dans le ViewModel (injecter repository/dispatcher), afin que `testDebugUnitTest` les couvre sans émulateur.
- Réseau via la stack Retrofit/OkHttp existante du package `network` (Bearer token géré là) — pas de client HTTP parallèle.
- Persistance via Room dans `data` ; toute écriture destinée au backend passe par la file offline-first existante (enqueue local puis synchronisation), jamais un appel réseau direct qui perdrait la donnée hors connexion.

# Hors périmètre
- Casser l'offline-first : une fonctionnalité qui ne fonctionne que connectée doit être signalée et justifiée dans le rapport.
- Nouvelle bibliothèque tierce sans justification (préférer les dépendances déjà déclarées dans `android/app/build.gradle`).
- Modification du backend Python : si le contrat serveur manque, le documenter dans le rapport comme dépendance bloquante.

# Fichiers probables
{{context_files}}
- Écrans : `feature/` et `ui/` ; accès réseau : `network/` ; entités/DAO/file : `data/` ; navigation : `navigation/` ; services de fond et notifications : `services/`, `notifications/`, `receivers/` ; vocal : `voice/`.
- Build : `android/app/build.gradle`, `android/build.gradle`, `android/settings.gradle`.

# Règles d'architecture
{{repo_rules}}
- Un sens de dépendance : ui/feature → viewmodel → repository → (Room | Retrofit). Jamais l'inverse.

# Critères d'acceptation
{{acceptance_criteria}}
- `./gradlew assembleDebug` compile sans nouvelle erreur ni nouveau warning bloquant.
- Le comportement hors connexion est défini et vérifié (donnée en file Room, resynchronisation au retour réseau).

# Tests obligatoires
{{required_tests}}
- `./gradlew testDebugUnitTest` vert ; tests unitaires des nouveaux ViewModels (états, erreurs réseau simulées) et des mappers/DAO ajoutés.

# Validation réelle
- Compiler : `cd android && ./gradlew assembleDebug` — inclure le résultat exact (BUILD SUCCESSFUL/FAILED) dans le rapport.
- Exécuter `./gradlew testDebugUnitTest` et rapporter le décompte de tests.
- Dérouler mentalement (ou via test) le scénario avion : action utilisateur hors ligne → donnée en file Room → retour réseau → synchronisation sans perte ni doublon.

# Stratégie Git
- Jamais de modification directe de main.
- Travailler uniquement dans le worktree / la branche fournie.
- Commits clairs, séparant data/network/UI quand c'est possible.

# Format du rapport final (OBLIGATOIRE)
{{result_format}}

## Qualité constante
- Chercher la cause racine des échecs Gradle (dépendance, version Kotlin) plutôt que d'empiler des workarounds.
- Ne pas masquer le problème ; ne pas ignorer un test unitaire qui échoue.
- Ne rien inventer : pas d'API backend supposée, pas de champ de réponse imaginé.
- Ne pas supprimer une fonction existante sans preuve.
- Respecter les conventions du dépôt (structure des packages) ; tester avant chaque commit.
- Comparer avant/après (build + tests) ; préserver les contrats existants.
- Rapport précis ; ne pas déclarer COMPLETED sans preuve (sorties Gradle réelles).
