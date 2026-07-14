# 06 — Plan de Tests

**Date** : 11 juillet 2026
**Couverture actuelle** : 534 fonctions de test déclarées dans 59 fichiers (backend uniquement). La collecte complète doit être exécutée avec la version Python supportée par le projet.

## Stratégie

### Niveaux de test

| Niveau | Outil | Cible | Actuel | Cible |
|---|---|---|---|---|
| Unitaires backend | pytest | Fonctions pures, classes | 534 fonctions déclarées | Maintenir et mesurer la couverture |
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
6. Event bus — pas de test d'émission/consommation

### P1 — Couverture insuffisante

7. Pipeline `_process_message` — testé indirectement via WebSocket
8. Actions (`execute_action`) — tests partiels
9. Apple Data Service — n'existe pas encore
10. Offline queue — 2 tests seulement (création tâche)
11. Routeurs API — pas de tests par route (seulement via WebSocket e2e)

## Plan de création de tests

### Phase 1 — implémentée et validée

| Fichier | Contenu |
|---|---|
| `tests/test_phase1_stability.py` | `busy_timeout`, snapshot WebSocket, nettoyage des sockets mortes et I/O hors verrou |
| `tests/test_imessage_consumer_cursor.py` | Isolation, monotonie et persistance des offsets par consommateur |
| `tests/test_pipeline_contract.py` | Échec explicite avant configuration et délégation des trois handlers |

### Phase 2 (accompagne Database modulaire)

| Fichier | Contenu |
|---|---|
| `tests/test_database_core.py` | `get_db`, `init_db`, `build_full_context` |
| `tests/test_database_people.py` | `upsert_person`, `get_all_people` |
| `tests/test_database_tasks.py` | CRUD tâches |
| `tests/test_database_conversations.py` | CRUD conversations |

### Phase 3 (accompagne Event bus)

| Fichier | Contenu |
|---|---|
| `tests/test_event_bus_integration.py` | Émission → consommation (NotificationCreated, TaskCreated, etc.) |
| `tests/test_notification_pipeline.py` | `create_notification` → WS broadcast |

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
| Couverture backend (%) | ~60% | 75% | 90% |
| Fonctions de test backend déclarées | 534 | ≥534 | ≥534 |
| Tests intégration backend | ~10 | 40 | 50+ |
| Tests frontend | 18 | ≥18 | 100+ |
| Tests E2E | 0 | 0 | 30+ |
| Tests sécurité | ~5 | 5 | 25+ |
| Tests offline | 2 | 2 | 20+ |
