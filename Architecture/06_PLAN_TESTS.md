# 06 — Plan de Tests

**Date initiale** : 11 juillet 2026

**Dernière validation locale** : 14 juillet 2026
**Couverture actuelle** : 540 fonctions de test déclarées dans 61 fichiers (backend uniquement). Validation après Phase 3 : 542 cas passés, 1 ignoré et 0 échec avec la commande backend complète.

## Stratégie

### Niveaux de test

| Niveau | Outil | Cible | Actuel | Cible |
|---|---|---|---|---|
| Unitaires backend | pytest | Fonctions pures, classes | 540 fonctions déclarées | Maintenir et mesurer la couverture |
| Intégration backend | pytest | Routes API, DB | Partiel | 50+ |
| Unitaires frontend | Vitest | Composants, hooks, stores | 18 tests web, 0 PWA | 100+ |
| Intégration frontend | Playwright | Flux utilisateur complets | 0 | 30+ |
| Tests offline | Vitest + fake-indexeddb | IndexedDB, SW, sync | Partiel (2) | 20+ |
| Tests PWA | Lighthouse + manuel | Installation, push, cache | 0 | 15+ |
| Sécurité | pytest + OWASP ZAP | Auth, injection, CSRF | Partiel | 20+ |
| Performance | Locust / k6 | Charge API, concurrence | 0 | 10+ |
| Reprise après panne | pytest | Crash recovery, WAL | 0 | 10+ |

## Couverture des zones critiques

### P0 — Couverture critique actuelle (mise à jour le 14/07/2026)

1. Détection mobile (`_is_mobile_device`) — pas de tests
2. Race condition WebSocket — couverte par 2 tests (snapshot stable, I/O hors verrou)
3. SQLite `busy_timeout` — couvert par lecture réelle du PRAGMA configuré
4. Registre des curseurs ROWID — couvert par 2 tests (isolation, monotonie, redémarrage)
5. PWA LockGate — pas de test de flux auth mobile
6. Event bus — couvert par 4 tests de contrat et d'intégration sur les 10 mutations de domaine

### P1 — Couverture insuffisante

7. Pipeline `_process_message` — testé indirectement via WebSocket
8. Actions (`execute_action`) — tests partiels
9. Apple Data Service — n'existe pas encore
10. Offline queue — 2 tests seulement (création tâche)
11. Routeurs API — pas de tests par route (seulement via WebSocket e2e)

## Plan de création de tests

### Phase 1 — implémentée et validée

Preuves exécutées le 14/07/2026 : 7 tests ciblés Phase 1 passants ; suite backend complète `tests/ jarvis/tests agents/devagent` à 536 passants et 1 ignoré ; GitHub Actions run #24 vert (backend et frontend).

| Fichier | Contenu |
|---|---|
| `tests/test_phase1_stability.py` | `busy_timeout`, snapshot WebSocket, nettoyage des sockets mortes et I/O hors verrou |
| `tests/test_imessage_consumer_cursor.py` | Isolation, monotonie et persistance des offsets par consommateur |
| `tests/test_pipeline_contract.py` | Échec explicite avant configuration et délégation des trois handlers |

### Phase 2 — implémentée et validée le 14/07/2026

Preuves : 6 tests de contrat dans `test_database_modularization.py`, import étoile réussi, façade désormais à 236 lignes après ajout du journal Phase 3, modules documentés et typés, suite complète à 538 passants et 1 ignoré au point de clôture Phase 2.

| Fichier | Contenu |
|---|---|
| `tests/test_database_modularization.py` | Réexports, chemin DB dynamique, CRUD, taille de façade, docstrings/type hints et absence d'import interne de la façade |

### Phase 3 — implémentée et validée le 14/07/2026

Preuves : 4 tests Phase 3 passants ; suite backend complète à 542 passants et 1 ignoré ; `compileall` et `git diff --check` réussis ; build de production PWA réussi. Le workspace `web/` ne peut pas être relancé localement avec `pnpm` car son `pnpm-workspace.yaml` historique ne déclare pas `packages`; ce défaut de baseline n'a pas été contourné. GitHub Actions sur la PR #12 confirme toutefois le frontend desktop (job vert en 38 s) et le backend (job vert en 1 min 38).

| Fichier | Contenu |
|---|---|
| `tests/test_event_bus_contract.py` | Contrat immuable/versionné, checksum, compatibilité des alias, handlers concurrents, isolation d'erreur et drainage de `emit_nowait()` |
| `tests/test_event_bus_integration.py` | Les 10 écritures réelles émettent ; journal SQLite idempotent et diffusion WebSocket vérifiés |

### Phase 4 (accompagne Routeurs)

| Fichier | Contenu |
|---|---|
| `tests/test_router_people.py` | CRUD people + analytics + timeline |
| `tests/test_router_tasks.py` | CRUD tâches |
| `tests/test_router_conversations.py` | CRUD conversations |
| `tests/test_router_location.py` | GPS, places, visites, trips |

### Phase 5 (accompagne Apple Data Service)

| Fichier | Contenu |
|---|---|
| `tests/test_apple_data.py` | Mock `chat.db`, conversion timestamp, déduplication |

### Phase 6 (accompagne Frontend unifié)

| Fichier | Contenu |
|---|---|
| `frontend/src/__tests__/components/` | TaskList, TaskCreator, MailList, MapView |
| `frontend/src/__tests__/auth/` | LockGate (setup, unlock, auto-lock, logout) |
| `frontend/src/__tests__/offline/` | IndexedDB queue (création, flush, conflit) |
| `e2e/` | Playwright — flux complet desktop + mobile |

### Tests de sécurité (Phase 6)

| Fichier | Contenu |
|---|---|
| `tests/test_auth_bruteforce.py` | Verrouillage après 5 échecs |
| `tests/test_session_expiry.py` | Expiration inactivité + absolue |
| `tests/test_csrf.py` | Vérification Origin/Referer |
| `tests/test_injection.py` | SQL, XSS, path traversal |
| `tests/test_device_token.py` | Usurpation device_id sans token |

## Métriques de couverture

| Métrique | Actuel | Après Phase 3 | Après Phase 6 |
|---|---|---|---|
| Couverture backend (%) | Non mesurée de façon fiable | À mesurer | 90% |
| Fonctions de test backend déclarées | 540 | 540 | ≥540 |
| Tests intégration backend | ~14 | ~14 | 50+ |
| Tests frontend | 18 web, 0 PWA | 18 web, 0 PWA | 100+ |
| Tests E2E | 0 | 0 | 30+ |
| Tests sécurité | ~5 | 5 | 25+ |
| Tests offline | 2 | 2 | 20+ |
