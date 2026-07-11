# 02 — Analyse des Problèmes

**Date** : 11 juillet 2026
**Total** : 23 problèmes (4 CRITIQUES, 6 MAJEURS, 8 MODÉRÉS, 5 MINEURS)

---

## CRITIQUES (P0) — Correction immédiate requise

### P0-1 — PWA sans écran de verrouillage

- **Gravité** : CRITIQUE
- **Fichiers** : `pwa/` (tout le frontend mobile)
- **Origine** : La PWA a été conçue comme une app mobile séparée, sans porter le composant `LockGate` existant dans `web/`. Le cookie `jarvis_session` est transmis automatiquement (même origine HTTP), donc l'API est accessible sans re-vérification du PIN.
- **Conséquence** : Si un téléphone déverrouillé accède à `http://IP:8081/m/`, toutes les données JARVIS (mails, journal, contacts, localisation GPS) sont exposées.
- **Correction** : SDK d'auth partagé + portage de `LockGate` dans la PWA (ADR-001)

### P0-2 — Trois curseurs ROWID indépendants sur chat.db

- **Gravité** : CRITIQUE
- **Fichiers** : `integrations/imessage.py`, `scripts/jarvis_daemon.py`, `integrations/imessage_reader.py`
- **Origine** : Chaque composant a été développé indépendamment, sans coordination. Le bridge a besoin de son propre curseur pour répondre aux iMessages, le daemon pour les notifications vocales, le reader pour le sourcing.
- **Conséquence** : Un message entrant peut être traité par 2-3 composants différents. La coordination (`daemon.skip if bridge.running`) n'est pas atomique — race condition TOCTOU.
- **Correction** : Curseur unique dans SQLite (`imessage_sync_cursor`), puis Apple Data Service (ADR-002, ADR-006)

### P0-3 — Race condition sur le set WebSocket `connected_ws`

- **Gravité** : CRITIQUE
- **Fichier** : `main.py` (`broadcast_ws()`, `websocket_endpoint()`)
- **Origine** : `connected_ws` est un `set[WebSocket]` modifié pendant l'itération. `broadcast_ws()` itère pendant que `websocket_endpoint()` retire des éléments. Pas de `asyncio.Lock`.
- **Conséquence** : `RuntimeError: Set changed size during iteration` si un client se déconnecte pendant un broadcast.
- **Correction** : `asyncio.Lock` + copie défensive du set avant itération (ADR-003)

### P0-4 — SQLite sans `busy_timeout` configuré

- **Gravité** : CRITIQUE
- **Fichier** : `database/__init__.py` (`get_connection()`)
- **Origine** : Les connexions SQLite sont créées sans `busy_timeout`. Si deux écritures concurrentes tentent d'accéder à `jarvis.db` simultanément (ex: scheduler écrit une notification + pipeline écrit un message), l'une échoue avec `SQLITE_BUSY`.
- **Conséquence** : Écritures silencieusement perdues. Pas de retry.
- **Correction** : `PRAGMA busy_timeout = 5000` dans `get_connection()` (ADR-004)

---

## MAJEURS (P1) — Correction prioritaire

### P1-1 — main.py : 7 194 lignes, 183 routes, 40+ responsabilités

- **Gravité** : MAJEURE
- **Fichier** : `main.py`
- **Origine** : Croissance organique sur 12+ mois. Chaque nouvelle feature a été ajoutée dans `main.py` plutôt que dans un module séparé.
- **Conséquence** : Impossible à tester unitairement. Toute modification dans un domaine risque de casser un autre. Conflits de merge fréquents. Onboarding impossible pour un nouveau développeur.
- **Correction** : Routeurs FastAPI par domaine (ADR-008). 12 routeurs extraits de `main.py`.

### P1-2 — database/__init__.py : 4 169 lignes, ~208 fonctions, 23 domaines

**État** : 🟡 En cours — 3 284 lignes après le troisième lot de Phase 2, 12 modules extraits.

- **Gravité** : MAJEURE
- **Fichier** : `database/__init__.py`
- **Origine** : Le module a commencé comme un simple fichier CRUD. Les features se sont accumulées sans séparation.
- **Conséquence** : Toute modification du schéma d'une table nécessite de naviguer dans 4 169 lignes. Conflits de merge fréquents.
- **Correction** : Modules par domaine — 17 fichiers extraits (ADR-009)

### P1-3 — Deux frontends, zéro réutilisation

- **Gravité** : MAJEURE
- **Fichiers** : `web/` (41 fichiers), `pwa/` (32 fichiers)
- **Origine** : `web/` a été développé en premier (React 19 + Vite). `pwa/` a été ajouté plus tard (Next.js 14) sans plan d'unification.
- **Conséquence** : Les types `TaskItem`, `NotificationItem`, `Place` sont redéfinis dans chaque front avec des champs différents. Les wrappers API sont distincts. Tout bug corrigé dans un frontend doit être reporté manuellement dans l'autre.
- **Correction** : App Next.js 15 unifiée responsive (ADR-007)

### P1-4 — Event bus existant mais sans consommateurs métiers

- **Gravité** : MAJEURE
- **Fichiers** : `jarvis/event_bus.py`, 19 émetteurs directs de `create_notification()`
- **Origine** : L'event bus a été créé comme infrastructure, mais personne n'a migré les appels directs.
- **Conséquence** : 19 modules appellent directement `create_notification()` avec leur propre logique de priorité et d'anti-doublon. Couplage fort. Pas de réactivité (l'UI doit poller).
- **Correction** : Émission d'événements depuis les points d'écriture DB (ADR-005)

### P1-5 — 25+ fichiers ouvrent des connexions directes à chat.db

- **Gravité** : MAJEURE
- **Fichiers** : `integrations/imessage.py`, `scripts/jarvis_daemon.py`, `scripts/relationship_analyzer.py`, `scripts/timeline_generator.py`, `scripts/contact_analytics.py`, `scripts/contact_alerts.py`, `scripts/message_predictor.py`, `scripts/backfill_imessages.py`, `scripts/force_full_mac_sync.py`, `main.py`, ...
- **Origine** : Aucune couche d'abstraction n'a été créée pour l'accès à `chat.db`. Chaque développeur a ouvert sa propre connexion.
- **Conséquence** : 25+ connexions SQLite ouvertes simultanément. 4 implémentations de la conversion Apple timestamp → datetime. Impossible de changer le format de date sans toucher 4 fichiers.
- **Correction** : AppleDataService unique (ADR-006)

### P1-6 — Dépendance circulaire main.py ↔ jarvis_daemon.py

**État** : ✅ Résolu en Phase 1 via le contrat indépendant `pipeline.py`.

- **Gravité** : MAJEURE
- **Fichiers** : `main.py`, `scripts/jarvis_daemon.py`
- **Origine** : `main.py` importe le daemon pour le lancer. Le daemon importe `_process_message_internal` de `main.py` pour parler à JARVIS.
- **Conséquence** : Couplage fort. Impossible de tester le daemon sans `main.py`. Impossible de déplacer le pipeline sans casser le daemon.
- **Correction** : Extraire le pipeline dans `pipeline.py` (ADR-010)

---

## MODÉRÉS (P2) — À corriger dans le mois

### P2-1 — 19 appels directs à create_notification() sans orchestration

- **Gravité** : MODÉRÉE
- **Fichiers** : 19 fichiers (scripts/scheduler.py, scripts/email_watcher.py, scripts/jarvis_daemon.py, scripts/rituals.py, scripts/doomscroll_detector.py, scripts/contact_alerts.py, scripts/commitments.py, scripts/location_analyzer.py, scripts/self_healing.py, scripts/security_audit.py, scripts/duplicate_scanner.py, scripts/perf_regression.py, scripts/favorite_places.py, scripts/meeting.py, scripts/db_maintenance.py, scripts/test_coverage_scan.py, agents/devagent/staging.py, database/__init__.py, scripts/screen_watcher.py)
- **Origine** : Chaque script a été développé indépendamment avec sa propre logique de notification.
- **Conséquence** : Certains scripts gèrent l'anti-doublon (`_notification_recently_sent()`), d'autres non. La priorité est incohérente.
- **Correction** : Remplacer par `event_bus.emit(NotificationCreated(...))` (ADR-005)

### P2-2 — Multiples modules écrivent dans les mêmes tables sans coordination

- **Gravité** : MODÉRÉE
- **Fichiers** : `agents/memory.py`, `agents/coach.py`, `agents/journal.py`, `audio/continuous_recorder.py`, `scripts/relationship_analyzer.py`, `scripts/location_analyzer.py`
- **Origine** : Plusieurs agents ont leur propre logique d'écriture mémoire (faits, personnes, patterns).
- **Conséquence** : Risque de données incohérentes si deux agents écrivent simultanément sur la même personne.
- **Correction** : Centraliser via `memory_agent` + event bus

### P2-3 — ~40 endpoints (30%) sans consommateur frontend

- **Gravité** : MODÉRÉE
- **Fichiers** : `main.py` (endpoints quality, self-healing, migrations, audio-daemon, imessage-import, voice-debug, commitments, DND, meetings, presence)
- **Origine** : Accumulation de fonctionnalités d'administration sans interface utilisateur.
- **Conséquence** : Code potentiellement mort. Maintenance inutile.
- **Correction** : Audit des endpoints non utilisés, suppression ou documentation

### P2-4 — Conversion Apple timestamp dupliquée 4 fois

- **Gravité** : MODÉRÉE
- **Fichiers** : `integrations/imessage_reader.py`, `integrations/imessage_import.py`, `scripts/backfill_imessages.py`, `scripts/force_full_mac_sync.py`
- **Origine** : Chaque développeur a réimplémenté la conversion.
- **Conséquence** : Constantes légèrement différentes, bugs potentiels.
- **Correction** : UNE SEULE fonction dans AppleDataService (ADR-006)

### P2-5 — 29 jobs APScheduler, possibles chevauchements

- **Gravité** : MODÉRÉE
- **Fichier** : `scripts/scheduler.py`
- **Origine** : Accumulation de jobs sans vérification des chevauchements.
- **Conséquence** : Un job qui traîne (ex: debrief 21:45) peut chevaucher le suivant (ex: résumé 22:00). Pas de `max_instances` configuré.
- **Correction** : Ajouter `max_instances=1` et `misfire_grace_time` à chaque job

### P2-6 — Deux wrappers API frontend incompatibles

- **Gravité** : MODÉRÉE
- **Fichiers** : `web/src/services/api.ts` (626 lignes), `pwa/src/lib/api.ts` (52 lignes)
- **Origine** : Développement indépendant des deux frontends.
- **Conséquence** : Toute modification d'un endpoint nécessite deux mises à jour.
- **Correction** : Unification frontend (ADR-007)

### P2-7 — Incohérence des versions frontend

- **Gravité** : MODÉRÉE
- **Fichiers** : `web/package.json`, `pwa/package.json`
- **Origine** : React 19 dans `web/`, React 18 dans `pwa/`. Tailwind v4 dans `web/`, v3.4 dans `pwa/`.
- **Conséquence** : Impossible de partager des composants entre les deux frontends.
- **Correction** : Unification sur React 19 + Tailwind v4 (ADR-007)

### P2-8 — Deux implémentations de carte totalement différentes

- **Gravité** : MODÉRÉE
- **Fichiers** : `web/src/app/components/views/MapView.tsx` (SVG custom, ~840l), `pwa/src/components/map/MapView.tsx` (Leaflet, 308l)
- **Origine** : `web/` a développé une carte SVG custom. `pwa/` a utilisé Leaflet.
- **Conséquence** : Toute nouvelle feature de carte doit être implémentée deux fois.
- **Correction** : Unification sur Leaflet (mieux adapté au mobile) (ADR-007)

---

## MINEURS (P3) — À traiter dans le backlog

### P3-1 — main.py importe 42 modules distincts

42 imports top-level, dont 8 singletons d'agents importés individuellement.

### P3-2 — 13 lazy imports cachent un couplage fort

`from X import Y` dans les fonctions au lieu d'imports top-level.

### P3-3 — Pas de tests pour la détection mobile

`_is_mobile_device()` n'a pas de tests unitaires.

### P3-4 — Service Worker dupliqué

`web/src/sw.ts` (Workbox injectManifest) et `pwa/public/sw.js` (next-pwa).

### P3-5 — Formatage de dates dupliqué

Fonctions `formatTime()`, `relativeDate()`, `formatDue()` dupliquées entre les deux frontends.

---

## Synthèse

| # | Problème | Sévérité | Effort correctif | Phase |
|---|---|---|---|---|
| P0-1 | PWA sans LockGate | CRITIQUE | 2 jours | Phase 6 |
| P0-2 | 3 curseurs ROWID | CRITIQUE | 2 heures | Phase 1 |
| P0-3 | Race condition WS | CRITIQUE | 15 min | Phase 1 |
| P0-4 | SQLite busy_timeout | CRITIQUE | 5 min | Phase 1 |
| P1-1 | main.py monolithe | MAJEURE | 3 jours | Phase 4 |
| P1-2 | database god object | MAJEURE | 1 jour | Phase 2 |
| P1-3 | Deux frontends | MAJEURE | 5 jours | Phase 6 |
| P1-4 | Event bus à usage minimal (1 abonné debug) | MAJEURE | 2 jours | Phase 3 |
| P1-5 | 25+ lecteurs chat.db | MAJEURE | 3 jours | Phase 5 |
| P1-6 | Cycle main↔daemon | ✅ RÉSOLU | 0 | Phase 1 — 11/07/2026 |
| P2-1 | 19 create_notification | MODÉRÉE | 1 jour | Phase 3 |
| P2-2 | Écritures non coordonnées | MODÉRÉE | 2 jours | Phase 3 |
| P2-3 | 40 endpoints orphelins | MODÉRÉE | 1 jour | Phase 4 |
| P2-4 | Apple timestamp ×4 | MODÉRÉE | 1 heure | Phase 5 |
| P2-5 | 29 jobs scheduler | MODÉRÉE | 2 heures | Phase 1 |
| P2-6 | 2 wrappers API | MODÉRÉE | 1 jour | Phase 6 |
| P2-7 | Versions incohérentes | MODÉRÉE | 3 jours | Phase 6 |
| P2-8 | 2 cartes différentes | MODÉRÉE | 2 jours | Phase 6 |
| P3-1 | 42 imports main.py | MINEURE | — | Phase 4 |
| P3-2 | 13 lazy imports | MINEURE | — | Phase 1 |
| P3-3 | Pas de tests mobile | MINEURE | 30 min | Phase 1 |
| P3-4 | SW dupliqué | MINEURE | 2 heures | Phase 6 |
| P3-5 | Dates dupliquées | MINEURE | 1 heure | Phase 6 |
