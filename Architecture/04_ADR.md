# 04 — Architecture Decision Records

**Date** : 11 juillet 2026
**Total** : 10 ADR

---

## ADR-001 — PWA sans écran de verrouillage

**Problème** : La PWA mobile n'a pas de composant LockGate.

**Solutions** :
- A. SDK d'auth partagé (jarvis_auth/) utilisable par web/, pwa/, supervisor. Client AuthClient + hook useLockGate() + composant LockGate. Une seule source de vérité.
- B. Port direct — copier LockGate.tsx dans pwa/. Rapide mais duplication.
- C. Middleware Next.js — incompatible avec output: 'export'.

**Recommandation** : Solution A — SDK d'auth partagé. Effort : 2 jours.

---

## ADR-002 — Trois curseurs ROWID indépendants

**Problème** : IMessageBridge, JarvisDaemon, IMessageReader maintiennent chacun leur propre last_rowid.

**Solutions** :
- A. Registre unique dans SQLite avec un offset monotone par consommateur. Un unique scalaire partagé ferait perdre des messages lorsqu'un consommateur avance avant les autres.
- B. AppleDataService unique (plus lourd mais plus propre).
- C. Coordination par verrou (ne résout pas le fond).

**Décision** : A, implémentée via `imessage_consumer_cursors` et `integrations/imessage_cursor.py`. Les offsets survivent aux redémarrages et ne peuvent jamais reculer. Migration vers B (ADR-006) maintenue pour supprimer ensuite les lectures directes de `chat.db`.

**Statut** : Implémenté le 11 juillet 2026.

---

## ADR-003 — Race condition WebSocket

**Problème** : connected_ws (set) modifié pendant itération dans broadcast_ws().

**Solutions** :
- A. asyncio.Lock + copie défensive du set avant itération.
- B. Remplacer par dict[int, WebSocket] avec ID unique.

**Recommandation** : Solution A — corrige en 15 minutes, zéro risque.

---

## ADR-004 — SQLite sans busy_timeout

**Problème** : Écritures concurrentes échouent silencieusement avec SQLITE_BUSY.

**Solutions** :
- A. PRAGMA busy_timeout = 5000 dans get_connection().
- B. Pool de connexions avec file d'attente — overkill.

**Recommandation** : Solution A — corrige en 5 minutes, compatible WAL existant.

---

## ADR-005 — Event bus sans consommateurs métiers

**Problème** : jarvis/event_bus.py existe mais 0 événement émis, 0 handler enregistré.

**Solutions** :
- A. Émission depuis les points d'écriture DB (create_task → event_bus.emit(TaskCreated(...))). Consommateurs (WS, TTS, daemon) s'abonnent.
- B. Bus externe Redis/NATS — overkill pour mono-utilisateur local.

**Recommandation** : Solution A. Phase 1 : 10 événements (NotificationCreated, TaskCreated, TaskUpdated, ConversationUpdated, MemoryUpdated, PersonUpserted, EpisodeSaved, PatternDetected, FactAdded, MessageSent). Effort : 2 jours.

---

## ADR-006 — 25+ connexions directes à chat.db

**Problème** : Chaque fichier ouvre sa propre connexion sqlite3, duplique la conversion Apple timestamp.

**Solutions** :
- A. AppleDataService unique — SEUL à ouvrir chat.db, expose get_new_messages(), get_conversation(), search_messages(), resolve_handle(), get_contacts(). UNE SEULE conversion Apple timestamp.
- B. Wrapper fin sans refactoring des consommateurs — coexistence temporaire.

**Recommandation** : Solution A avec migration progressive (1 consommateur/jour). Effort : 3 jours.

---

## ADR-007 — Deux frontends, zéro réutilisation

**Problème** : web/ (React 19 + Vite) et pwa/ (Next.js 14) partagent 0 composants, 0 types, 0 hooks.

**Solutions** :
- A. App Next.js 15 unifiée responsive. Layout desktop vs mobile automatique. Composants partagés.
- B. Package @jarvis/shared + deux apps séparées. Moins risqué mais moins propre.
- C. Abandonner desktop, tout PWA mobile. Trop régressif.

**Recommandation** : Solution A. Coexistence avec l'ancien frontend pendant la migration. Effort : 5 jours.

---

## ADR-008 — main.py monolithe (7 194 lignes)

**Problème** : 183 routes, 40+ responsabilités dans un seul fichier.

**Solutions** :
- A. Routeurs APIRouter FastAPI par domaine — 12 routeurs. main.py → ~200 lignes.
- B. Extraction progressive (1 domaine/semaine) — trop lent.

**Recommandation** : Solution A. Extraction mécanique, groupe par groupe. Effort : 3 jours.

---

## ADR-009 — database/__init__.py (4 169 lignes, 23 domaines)

**Problème** : 23 domaines métier dans un seul fichier.

**Solutions** :
- A. Modules par domaine (17 fichiers) + __init__.py ré-exporte tout (backward compatible).
- B. Pas d'alternative — c'est la seule approche viable.

**Recommandation** : Solution A. Purement mécanique, zéro risque. Effort : 1 jour.

**Statut** : En cours — 12 modules extraits avec réexports compatibles au 11 juillet 2026.

---

## ADR-010 — Dépendance circulaire main.py ↔ jarvis_daemon.py

**Problème** : main.py importe daemon, daemon importe _process_message_internal de main.py.

**Solutions** :
- A. Introduire un contrat `pipeline.py` indépendant. `main.py` enregistre les implémentations actuelles ; les daemons n'importent plus le point d'entrée. Le déplacement physique des implémentations se poursuit pendant la Phase 4.
- B. Daemon envoie via WebSocket — ajoute latence.

**Décision** : Solution A, implémentée le 11 juillet 2026. Le contrat conserve les signatures publiques et échoue explicitement s'il est utilisé avant configuration.

**Statut** : Implémenté pour le découplage ; déplacement des implémentations planifié en Phase 4.

---

## Récapitulatif

| ADR | Problème | Solution | Effort | Prérequis |
|---|---|---|---|---|
| 001 | PWA sans LockGate | SDK auth partagé | 2j | Aucun |
| 002 | 3 curseurs ROWID mémoire | Registre SQLite central, offset par consommateur | 2h | Aucun |
| 003 | Race WS set | Lock + copie défensive | 15min | Aucun |
| 004 | SQLite busy | busy_timeout=5000 | 5min | Aucun |
| 005 | Event bus à usage minimal | Émission depuis DB | 2j | ADR-009 |
| 006 | 25+ lecteurs chat.db | AppleDataService | 3j | ADR-002 |
| 007 | Deux frontends | App Next.js unifiée | 5j | ADR-001 |
| 008 | main.py monolithe | Routeurs par domaine | 3j | ADR-009 |
| 009 | database god object | Modules par domaine | 1j | Aucun |
| 010 | Cycle main↔daemon | pipeline.py | 4h | Aucun |
