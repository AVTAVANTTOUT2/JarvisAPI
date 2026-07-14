# 02 — Analyse des Problèmes

**Date** : 11 juillet 2026
**Total** : 23 problèmes (4 CRITIQUES, 6 MAJEURS, 8 MODÉRÉS, 5 MINEURS)

---

## CRITIQUES (P0) — Correction immédiate requise

### P0-1 — PWA sans écran de verrouillage ✅ RÉSOLU (Phase 6)

- **Gravité** : CRITIQUE
- **Fichiers** : `pwa/` (tout le frontend mobile)
- **Origine** : La PWA a été conçue comme une app mobile séparée, sans porter le composant `LockGate` existant dans `web/`. Le cookie `jarvis_session` est transmis automatiquement (même origine HTTP), donc l'API est accessible sans re-vérification du PIN.
- **Conséquence** : Si un téléphone déverrouillé accède à `http://IP:8081/m/`, toutes les données JARVIS (mails, journal, contacts, localisation GPS) sont exposées.
- **Correction** : SDK d'auth partagé + portage de `LockGate` dans la PWA (ADR-001)
- **Résolution** : `jarvis_auth/` est importé par le frontend unifié, `web/` et `pwa/`. `LockGate` attend une confirmation authentifiée de `/api/auth/status`, reste fermé en cas d'erreur réseau et ne rend jamais les enfants privés avant déverrouillage. Validé le 14/07/2026 par Vitest et Playwright mobile.

### P0-2 — Trois curseurs ROWID indépendants sur chat.db ✅ RÉSOLU (Phase 1)

- **Gravité** : CRITIQUE
- **Fichiers** : `integrations/imessage.py`, `scripts/jarvis_daemon.py`, `integrations/imessage_reader.py`
- **Origine** : Chaque composant a été développé indépendamment, sans coordination. Le bridge a besoin de son propre curseur pour répondre aux iMessages, le daemon pour les notifications vocales, le reader pour le sourcing.
- **Conséquence** : Un message entrant peut être traité par 2-3 composants différents. La coordination (`daemon.skip if bridge.running`) n'est pas atomique — race condition TOCTOU.
- **Correction** : Registre SQLite central avec un offset monotone par consommateur (`imessage_consumer_cursors`), puis Apple Data Service (ADR-002, ADR-006)
- **Résolution** : Registre SQLite central `imessage_consumer_cursors` avec 3 consommateurs (`reader.intelligence`, `daemon.notifications`, `bridge.reply:<target>`), offsets monotones et persistants via `integrations/imessage_cursor.py`. Implémenté le 11/07/2026, validé le 14/07/2026.

### P0-3 — Race condition sur le set WebSocket `connected_ws` ✅ RÉSOLU (Phase 1)

- **Gravité** : CRITIQUE
- **Fichiers historiques** : ancien `main.py`; état actuel dans `websocket_registry.py` et `api/ws_handler.py`
- **Origine** : `connected_ws` est un `set[WebSocket]` modifié pendant l'itération. `broadcast_ws()` itère pendant que `websocket_endpoint()` retire des éléments. Pas de `asyncio.Lock`.
- **Conséquence** : `RuntimeError: Set changed size during iteration` si un client se déconnecte pendant un broadcast.
- **Correction** : `asyncio.Lock` + copie défensive du set avant itération (ADR-003)
- **Résolution** : Registre WebSocket isolé dans `websocket_registry.py`, mutations via `add_websocket()`/`remove_websocket()` protégées par `asyncio.Lock`, snapshot défensif `tuple(connected_ws)` avant itération pour `broadcast_ws()`. Implémenté le 11/07/2026, validé le 14/07/2026.

### P0-4 — SQLite sans `busy_timeout` configuré ✅ RÉSOLU (Phase 1)

- **Gravité** : CRITIQUE
- **Fichier** : `database/core.py` (`get_connection()`)
- **Origine** : Les connexions SQLite sont créées sans `busy_timeout`. Si deux écritures concurrentes tentent d'accéder à `jarvis.db` simultanément (ex: scheduler écrit une notification + pipeline écrit un message), l'une échoue avec `SQLITE_BUSY`.
- **Conséquence** : Écritures silencieusement perdues. Pas de retry.
- **Correction** : `PRAGMA busy_timeout = 5000` dans `get_connection()` (ADR-004)
- **Résolution** : `database/core.py::get_connection()` configure `PRAGMA busy_timeout = 5000` sur chaque connexion applicative. Vérifié par test unitaire lisant `PRAGMA busy_timeout`. Implémenté le 11/07/2026, validé le 14/07/2026.

---

## MAJEURS (P1) — Correction prioritaire

### P1-1 — main.py : 7 197 lignes, 40+ responsabilités (état historique)

**État** : ✅ RÉSOLU le 14/07/2026 — `main.py` est réduit à 175 lignes d'assemblage ; 12 `APIRouter` et les handlers/support associés sont isolés dans `api/`, sans import inverse vers `main.py`.

- **Gravité** : MAJEURE
- **Fichier** : `main.py`
- **Origine** : Croissance organique sur 12+ mois. Chaque nouvelle feature a été ajoutée dans `main.py` plutôt que dans un module séparé.
- **Conséquence** : Impossible à tester unitairement. Toute modification dans un domaine risque de casser un autre. Conflits de merge fréquents. Onboarding impossible pour un nouveau développeur.
- **Correction** : Routeurs FastAPI par domaine (ADR-008). Les 174 opérations HTTP et le WebSocket ont conservé leur contrat ; l'OpenAPI reste stable à 157 chemins.

### P1-2 — database/__init__.py : 4 169 lignes, ~208 fonctions, 23 domaines

**État** : ✅ RÉSOLU le 14/07/2026 — façade de 236 lignes, 25 modules d'implémentation et réexports rétrocompatibles après ajout du journal d'événements en Phase 3.

- **Gravité** : MAJEURE
- **Fichier historique** : `database/__init__.py`
- **Origine historique** : Le module a commencé comme un simple fichier CRUD. Les features se sont accumulées sans séparation.
- **Conséquence historique** : Toute modification du schéma d'une table nécessitait de naviguer dans 4 169 lignes. Conflits de merge fréquents.
- **Correction** : 25 modules spécialisés ; `schema.py` et `migrations.py` isolent la structure, `core.py` porte l'initialisation, la façade conserve l'API publique (ADR-009).

### P1-3 — Deux frontends, zéro réutilisation ✅ RÉSOLU (Phase 6)

- **Gravité** : MAJEURE
- **Fichiers** : `web/` (41 fichiers), `pwa/` (33 fichiers)
- **Origine** : `web/` a été développé en premier (React 19 + Vite). `pwa/` a été ajouté plus tard (Next.js 14) sans plan d'unification.
- **Conséquence** : Les types `TaskItem`, `NotificationItem`, `Place` sont redéfinis dans chaque front avec des champs différents. Les wrappers API sont distincts. Tout bug corrigé dans un frontend doit être reporté manuellement dans l'autre.
- **Correction** : App Next.js 15 unifiée responsive (ADR-007)
- **Résolution** : `frontend/` est la cible canonique Next.js 15/React 19. Elle sélectionne le layout selon le terminal et réutilise directement les vues de `web/src` et `pwa/src`, le SDK auth, les types et le wrapper API central. Les anciens builds restent volontairement exécutables comme fallbacks de rollback, sans être deux chemins de production concurrents.

### P1-4 — Event bus existant mais sans consommateurs métiers

**État** : ✅ RÉSOLU le 14/07/2026 — 10 événements de domaine typés et 3 consommateurs réels : journal SQLite, diffusion WebSocket et TTS des notifications prioritaires. La PWA consomme le flux SSE pour invalider ses requêtes notifications/tâches sans polling.

- **Gravité** : MAJEURE
- **Fichiers** : `jarvis/event_bus.py`, `jarvis/events.py`, `database/event_log.py`, `websocket_registry.py`, `scripts/audio_daemon.py`
- **Origine historique** : Le bus existait comme infrastructure, sans handlers métiers enregistrés.
- **Conséquence historique** : Aucun découplage des réactions et polling nécessaire côté UI.
- **Correction** : Émission après commit depuis les points d'écriture DB, handlers isolés et concurrents, journal idempotent et synchronisation temps réel (ADR-005).

### P1-5 — 25+ fichiers ouvraient des connexions directes à chat.db ✅ RÉSOLU (Phase 5)

- **Gravité** : MAJEURE
- **État historique** : chaque lecteur ouvrait sa propre connexion et dupliquait parfois la conversion Apple timestamp.
- **Résolution** : `integrations/apple_data.py` est l'unique ouverture applicative read-only de `chat.db`; bridge, reader, daemons, import/backfill, diagnostics et TV y délèguent leurs lectures, et les analyseurs relationnels passent par `IMessageReader`.
- **Preuve** : contrat AST dans `tests/test_apple_data.py`, 67 tests ciblés et suite backend à 555 passants, 1 ignoré le 14/07/2026.

### P1-6 — Dépendance circulaire main.py ↔ jarvis_daemon.py

**État** : ✅ Résolu en Phase 1 via le contrat indépendant `pipeline.py`.

- **Gravité** : MAJEURE
- **Fichiers** : `main.py`, `scripts/jarvis_daemon.py`
- **Origine** : `main.py` importe le daemon pour le lancer. Le daemon importe `_process_message_internal` de `main.py` pour parler à JARVIS.
- **Conséquence** : Couplage fort. Impossible de tester le daemon sans `main.py`. Impossible de déplacer le pipeline sans casser le daemon.
- **Correction** : Extraire le pipeline dans `pipeline.py` (ADR-010)

---

## MODÉRÉS (P2) — À corriger dans le mois

### P2-1 — 15 producteurs appellent encore directement create_notification() sans orchestration

- **Gravité** : MODÉRÉE
- **Fichiers** : 15 fichiers consommateurs de l'API publique, vérifiés statiquement après la Phase 3.
- **Origine** : Chaque script a été développé indépendamment avec sa propre logique de notification.
- **Conséquence** : Certains scripts gèrent l'anti-doublon (`_notification_recently_sent()`), d'autres non. La priorité reste incohérente, même si chaque écriture déclenche désormais le bus en aval.
- **Correction** : Centraliser la politique dans un futur `NotificationService`; conserver l'émission `NotificationCreated` au point de persistance pour garantir qu'elle suit le commit.

### P2-2 — Multiples modules écrivent dans les mêmes tables sans coordination

**État** : 🟡 PARTIELLEMENT RÉDUIT en Phase 3 — les 10 mutations centrales publient maintenant un événement après commit, ce qui coordonne les réactions. L'unicité du propriétaire d'écriture reste à imposer aux producteurs.

- **Gravité** : MODÉRÉE
- **Fichiers** : `agents/memory.py`, `agents/coach.py`, `agents/journal.py`, `audio/continuous_recorder.py`, `scripts/relationship_analyzer.py`, `scripts/location_analyzer.py`
- **Origine** : Plusieurs agents ont leur propre logique d'écriture mémoire (faits, personnes, patterns).
- **Conséquence** : Risque de données incohérentes si deux agents écrivent simultanément sur la même personne.
- **Correction** : Centraliser via `memory_agent` + event bus

### P2-3 — ~40 endpoints (30%) sans consommateur frontend

- **Gravité** : MODÉRÉE
- **Fichiers** : routeurs `api/` (quality, self-healing, migrations, audio-daemon, imessage-import, voice-debug, commitments, DND, meetings, presence)
- **Origine** : Accumulation de fonctionnalités d'administration sans interface utilisateur.
- **Conséquence** : Code potentiellement mort. Maintenance inutile.
- **Correction** : Audit des endpoints non utilisés, suppression ou documentation

### P2-4 — Conversion Apple timestamp dupliquée 4 fois ✅ RÉSOLU (Phase 5)

- **Gravité** : MODÉRÉE
- **État historique** : chaque lecteur réimplémentait la conversion avec des constantes potentiellement divergentes.
- **Résolution** : `apple_epoch_to_datetime()` et `datetime_to_apple_epoch()` vivent dans `integrations/apple_data.py`; les wrappers historiques délèguent à ces fonctions.

### P2-5 — 29 jobs APScheduler, possibles chevauchements

- **Gravité** : MODÉRÉE
- **Fichier** : `scripts/scheduler.py`
- **Origine** : Accumulation de jobs sans vérification des chevauchements.
- **Conséquence** : Un job qui traîne (ex: debrief 21:45) peut chevaucher le suivant (ex: résumé 22:00). Pas de `max_instances` configuré.
- **Correction** : Ajouter `max_instances=1` et `misfire_grace_time` à chaque job

### P2-6 — Deux wrappers API frontend incompatibles ✅ RÉSOLU (Phase 6)

- **Gravité** : MODÉRÉE
- **Fichiers historiques** : `web/src/services/api.ts` (626 lignes), `pwa/src/lib/api.ts` (52 lignes), supprimés/déplacés en Phase 6
- **Origine** : Développement indépendant des deux frontends.
- **Conséquence** : Toute modification d'un endpoint nécessite deux mises à jour.
- **Correction** : Unification frontend (ADR-007)
- **Résolution** : `frontend/src/lib/api.ts` est l'unique point `fetch()` des trois arbres source. Il transmet systématiquement `credentials: 'include'`, y compris pour les uploads, la géolocalisation et le rejeu de la file hors-ligne. Un test d'architecture interdit le retour des deux anciens wrappers.

### P2-7 — Incohérence des versions frontend 🟡 RÉDUIT (Phase 6)

- **Gravité** : MODÉRÉE
- **Fichiers** : `web/package.json`, `pwa/package.json`
- **Origine** : React 19 dans `web/`, React 18 dans `pwa/`. Tailwind v4 dans `web/`, v3.4 dans `pwa/`.
- **Conséquence** : Impossible de partager des composants entre les deux frontends.
- **Correction** : Unification sur React 19 + Tailwind v4 (ADR-007)
- **État actuel** : le runtime canonique `frontend/` utilise Next.js 15, React 19 et Tailwind v4. Les manifestes Next.js 14/React 18 de `pwa/` sont conservés temporairement uniquement pour le rollback et seront supprimés avec les fallbacks historiques.

### P2-8 — Deux implémentations de carte totalement différentes

- **Gravité** : MODÉRÉE
- **Fichiers** : `web/src/app/components/views/MapView.tsx` (SVG custom, ~840l), `pwa/src/components/map/MapView.tsx` (Leaflet, 308l)
- **Origine** : `web/` a développé une carte SVG custom. `pwa/` a utilisé Leaflet.
- **Conséquence** : Toute nouvelle feature de carte doit être implémentée deux fois.
- **Correction** : Unification sur Leaflet (mieux adapté au mobile) (ADR-007)

---

## MINEURS (P3) — À traiter dans le backlog

### P3-1 — main.py importait 42 modules distincts

**État** : ✅ RÉSOLU en Phase 4 — `main.py` n'importe plus que les éléments nécessaires à l'assemblage. Les dépendances métier résident avec leurs routeurs et handlers.

### P3-2 — 13 lazy imports cachaient un couplage fort dans le monolithe

**État** : ✅ Concentration dans `main.py` résolue en Phase 4. Les imports ont été replacés dans les modules responsables ; toute exception lazy résiduelle reste soumise à la règle d'architecture dédiée.

### P3-3 — Pas de tests pour la détection mobile ✅ RÉSOLU (Phase 6)

`frontend/src/lib/device.test.ts` couvre téléphone, tablette Android et desktop ; les scénarios Playwright vérifient aussi la sélection réelle des layouts desktop/mobile.

### P3-4 — Service Worker dupliqué

`web/src/sw.ts` (Workbox injectManifest) et `pwa/public/sw.js` (next-pwa).

### P3-5 — Formatage de dates dupliqué

Fonctions `formatTime()`, `relativeDate()`, `formatDue()` dupliquées entre les deux frontends.

---

## Synthèse

| # | Problème | Sévérité | Effort correctif | Phase |
|---|---|---|---|---|
| P0-1 | PWA sans LockGate | ✅ RÉSOLU | 0 | Phase 6 — 14/07/2026 |
| P0-2 | 3 curseurs ROWID | ✅ RÉSOLU | 0 | Phase 1 — 11/07/2026 |
| P0-3 | Race condition WS | ✅ RÉSOLU | 0 | Phase 1 — 11/07/2026 |
| P0-4 | SQLite busy_timeout | ✅ RÉSOLU | 0 | Phase 1 — 11/07/2026 |
| P1-1 | main.py monolithe | ✅ RÉSOLU | 0 | Phase 4 — 14/07/2026 |
| P1-2 | database god object | ✅ RÉSOLU | 0 | Phase 2 — 14/07/2026 |
| P1-3 | Deux frontends | ✅ RÉSOLU (production) | 0 | Phase 6 — 14/07/2026 |
| P1-4 | Event bus sans consommateurs métiers | ✅ RÉSOLU | 0 | Phase 3 — 14/07/2026 |
| P1-5 | 25+ lecteurs chat.db | ✅ RÉSOLU | 0 | Phase 5 — 14/07/2026 |
| P1-6 | Cycle main↔daemon | ✅ RÉSOLU | 0 | Phase 1 — 11/07/2026 |
| P2-1 | 15 producteurs directs de notification | MODÉRÉE | 1 jour | Backlog `NotificationService` |
| P2-2 | Écritures non coordonnées | 🟡 PARTIEL | 2 jours | Gouvernance Data Ownership |
| P2-3 | 40 endpoints sans consommateur frontend | MODÉRÉE | 1 jour | Backlog audit API |
| P2-4 | Apple timestamp ×4 | ✅ RÉSOLU | 0 | Phase 5 — 14/07/2026 |
| P2-5 | 29 jobs scheduler | MODÉRÉE | 2 heures | Backlog scheduler |
| P2-6 | 2 wrappers API | ✅ RÉSOLU | 0 | Phase 6 — 14/07/2026 |
| P2-7 | Versions incohérentes | 🟡 RÉDUIT aux fallbacks | 1 jour | Retrait des fallbacks |
| P2-8 | 2 cartes différentes | MODÉRÉE | 2 jours | Backlog frontend post-Phase 6 |
| P3-1 | 42 imports concentrés dans main.py | ✅ RÉSOLU | 0 | Phase 4 — 14/07/2026 |
| P3-2 | Lazy imports concentrés dans main.py | ✅ RÉSOLU | 0 | Phase 4 — 14/07/2026 |
| P3-3 | Pas de tests mobile | ✅ RÉSOLU | 0 | Phase 6 — 14/07/2026 |
| P3-4 | SW dupliqué | MINEURE | 2 heures | Retrait des fallbacks |
| P3-5 | Dates dupliquées | MINEURE | 1 heure | Backlog frontend |
