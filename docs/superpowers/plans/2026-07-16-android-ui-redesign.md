# Android UI Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Terminer la refonte visuelle Compose de JARVIS Companion, préserver tous les contrats fonctionnels existants et ouvrir une PR dédiée vérifiée sur le Samsung S24.

**Architecture:** Le design system `core/ui` reste sans état métier et fournit les surfaces verre, états, boutons, métriques, listes, navigation et orbe. Chaque écran consomme uniquement son ViewModel ou les flux Room existants ; les fonctions futures restent derrière `JarvisFeatureFlags` avec placeholders inertes et TODO documentés.

**Tech Stack:** Kotlin 2, Jetpack Compose Material 3, Navigation Compose, Room, WorkManager, Retrofit/OkHttp, JUnit/Robolectric, Android instrumentation.

## Global Constraints

- 100 % Jetpack Compose, aucune WebView et aucune donnée simulée.
- Ne modifier ni les contrats backend ni Room, WorkManager, Retrofit, WebSocket ou les services foreground.
- Chaque action visible est réelle ou explicitement désactivée.
- Préserver les états chargement, succès, vide, erreur, offline, permission refusée et token révoqué.
- Respecter TalkBack, les cibles tactiles de 48 dp, les grandes polices, la réduction des animations et les orientations.
- Aucun blur plein écran ni animation GPU permanente hors état actif.

---

### Task 1: Stabiliser les fondations interrompues

**Files:**
- Modify: `android/app/src/main/kotlin/fr/jarvis/companion/core/ui/**`
- Modify: `android/app/src/main/kotlin/fr/jarvis/companion/navigation/**`
- Test: `android/app/src/test/kotlin/fr/jarvis/companion/core/ui/**`

**Interfaces:**
- Produces: composants Compose sans état métier et navigation adaptative mobile/rail.

- [ ] Vérifier le diff interrompu et compiler `:app:compileDebugKotlin`.
- [ ] Ajouter les tests unitaires des fonctions pures de formatage et des décisions de navigation.
- [ ] Corriger les défauts d’accessibilité, responsive et performance détectés.
- [ ] Exécuter les tests ciblés et la compilation.

### Task 2: Finaliser Accueil, Chat, Agenda, Tâches et Notifications

**Files:**
- Modify: `android/app/src/main/kotlin/fr/jarvis/companion/feature/{home,chat,agenda,tasks,notifications}/**`
- Test: `android/app/src/test/kotlin/fr/jarvis/companion/feature/**`

**Interfaces:**
- Consumes: flux Room, connectivité et `SyncManager.refreshHome()`.
- Produces: écrans branchés sur données réelles, états offline/erreur/vide et placeholders inertes.

- [ ] Vérifier que les écrans conservent les actions et contrats existants.
- [ ] Extraire et tester les regroupements/filtres temporels purs.
- [ ] Vérifier streaming, retry, confirmation d’action sensible et IME du chat.
- [ ] Exécuter tests et compilation.

### Task 3: Refaire l’écran Voix

**Files:**
- Modify: `android/app/src/main/kotlin/fr/jarvis/companion/voice/VoiceActivity.kt`
- Test: `android/app/src/test/kotlin/fr/jarvis/companion/voice/VoiceStateTest.kt`
- Test: `android/app/src/androidTest/kotlin/fr/jarvis/companion/voice/VoiceScreenTest.kt`

**Interfaces:**
- Consumes: `VoiceUiState`, `VoicePhase`, callbacks PTT existants.
- Produces: mapping déterministe phase→orbe, transcription réelle, actions annuler/arrêter/réessayer.

- [ ] Écrire les tests du mapping phase/orbe et des libellés.
- [ ] Remplacer le bouton technique par l’orbe réactif sans changer le pipeline PTT.
- [ ] Ajouter le placeholder inerte de conversation continue.
- [ ] Vérifier TalkBack et réduction des animations.

### Task 4: Refaire Localisation et Diagnostics

**Files:**
- Modify: `android/app/src/main/kotlin/fr/jarvis/companion/feature/location/**`
- Modify: `android/app/src/main/kotlin/fr/jarvis/companion/feature/diagnostics/**`
- Test: `android/app/src/test/kotlin/fr/jarvis/companion/feature/{location,diagnostics}/**`

**Interfaces:**
- Consumes: `LocationUiState`, DAO Room, connectivité et métadonnées de sync.
- Produces: verdicts purs testés, métriques lisibles, actions existantes et rapport technique replié.

- [ ] Écrire les tests des verdicts de santé et de masquage.
- [ ] Implémenter héros, métriques, timeline et permissions sans coordonnées.
- [ ] Supprimer le `runBlocking` de la composition au profit d’un chargement non bloquant.
- [ ] Préparer carte live, trajets et offline détaillé derrière flags.

### Task 5: Refaire Réglages, Plus, Réparation et Onboarding

**Files:**
- Modify: `android/app/src/main/kotlin/fr/jarvis/companion/feature/{settings,more,repair,onboarding}/**`
- Test: `android/app/src/androidTest/kotlin/fr/jarvis/companion/feature/**`

**Interfaces:**
- Consumes: callbacks et stockage sécurisé existants.
- Produces: sections cohérentes, confirmations explicites, stepper et appairage à six chiffres.

- [ ] Conserver toutes les mutations et validations existantes.
- [ ] Ajouter confirmations aux opérations de réparation destructives.
- [ ] Préparer les futures entrées sans routes vides visibles.
- [ ] Tester les parcours et états principaux.

### Task 6: Validation complète et appareil réel

**Files:**
- Modify: `android/docs/UI_AUDIT.md`
- Modify: `android/docs/UI_DIRECTION.md`
- Modify: `android/docs/FUTURE_FEATURES.md`
- Create: `android/docs/validation/ANDROID_UI_REDESIGN_VALIDATION.md`

**Interfaces:**
- Produces: preuves reproductibles build, tests, lint, appareil et APK.

- [ ] Exécuter `assembleDebug`, `testDebugUnitTest`, `lintDebug`.
- [ ] Exécuter `connectedDebugAndroidTest` sur le S24 connecté.
- [ ] Installer avec `adb install -r` sans effacer les données.
- [ ] Vérifier portrait/paysage, navigation et écrans principaux, puis capturer les écrans.
- [ ] Calculer le SHA-256 de l’APK.

### Task 7: Revue, commits et PR

**Files:**
- Modify: uniquement les corrections issues des revues.

**Interfaces:**
- Produces: branche poussée et PR dédiée vers `main`.

- [ ] Faire une revue conformité au cahier des charges.
- [ ] Faire une revue qualité finale et corriger tout constat important.
- [ ] Vérifier le diff, les secrets, les TODO stables et le registre des flags.
- [ ] Committer par lots cohérents, pousser `feat/android-ui-redesign` et ouvrir la PR.
