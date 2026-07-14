# 06 — Plan de Tests

**Date initiale** : 11 juillet 2026

**Dernière validation locale** : 14 juillet 2026
**Couverture actuelle** : 554 fonctions de test backend déclarées dans 68 fichiers, 27 tests Vitest passants (18 historiques + 9 unifiés) et 3 scénarios Playwright Phase 6. La dernière suite backend complète demeure la validation Phase 5 à 555 cas passés et 1 ignoré ; le rejeu local Phase 6 est bloqué avant collecte par l'absence de `portaudio.h` lors de l'installation de PyAudio.

## Stratégie

### Niveaux de test

| Niveau | Outil | Cible | Actuel | Cible |
|---|---|---|---|---|
| Unitaires backend | pytest | Fonctions pures, classes | 554 fonctions déclarées | Maintenir et mesurer la couverture |
| Intégration backend | pytest | Routes API, DB | Partiel | 50+ |
| Unitaires frontend | Vitest | Composants, hooks, stores | 27 passants : 18 web + 9 frontend unifié | 100+ |
| Intégration frontend | Playwright | Flux utilisateur complets | 3 passants desktop/mobile | 30+ |
| Tests offline | Vitest + fake-indexeddb | IndexedDB, SW, sync | Partiel (2) | 20+ |
| Tests PWA | Lighthouse + manuel | Installation, push, cache | 0 | 15+ |
| Sécurité | pytest + OWASP ZAP | Auth, injection, CSRF | Partiel | 20+ |
| Performance | Locust / k6 | Charge API, concurrence | 0 | 10+ |
| Reprise après panne | pytest | Crash recovery, WAL | 0 | 10+ |

## Couverture des zones critiques

### P0 — Couverture critique actuelle (mise à jour le 14/07/2026)

1. Détection mobile (`_is_mobile_device`) — couverte indirectement ; contrat API/frontend conservé par la suite complète
2. Race condition WebSocket — couverte par 2 tests (snapshot stable, I/O hors verrou)
3. SQLite `busy_timeout` — couvert par lecture réelle du PRAGMA configuré
4. Registre des curseurs ROWID — couvert par 2 tests (isolation, monotonie, redémarrage)
5. PWA LockGate — ✅ fail-closed, absence de contenu privé et déverrouillage couverts en Vitest + Playwright
6. Event bus — couvert par 4 tests de contrat et d'intégration sur les 10 mutations de domaine

### P1 — Couverture insuffisante

7. Pipeline `_process_message` — testé indirectement via WebSocket
8. Actions (`execute_action`) — tests partiels
9. Apple Data Service — ✅ service read-only, conversion centrale et garde-fou d'architecture couverts en Phase 5
10. Offline queue — 2 tests seulement (création tâche)
11. Routeurs API — structure, signatures et schéma OpenAPI verrouillés ; couverture métier par route encore partielle

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

### Phase 4 — implémentée et validée le 14/07/2026

Preuves : 6 tests ciblés passants ; signatures des 174 opérations HTTP et du WebSocket inchangées ; hash des 157 chemins OpenAPI inchangé ; exactement 12 routeurs ; `main.py` à 175 lignes ; aucun import `api → main`. Suite complète : 548 passants, 1 ignoré ; `compileall`, Ruff et `git diff --check` réussis. Aucun serveur réel n'a été lancé pour une campagne `curl`, donc aucune validation opérationnelle 24 h n'est revendiquée.

| Fichier | Contenu |
|---|---|
| `tests/test_phase4_route_contract.py` | Snapshot déterministe des signatures de routes et du schéma OpenAPI avant/après extraction |
| `tests/test_phase4_architecture.py` | 12 `APIRouter`, limites de taille, absence d'import inverse et montage explicite du lifespan |
| Suite existante | Tests `TestClient`, WebSocket et métiers assurant la non-régression comportementale |

### Phase 5 — implémentée et validée le 14/07/2026

Preuves : 7 contrats `test_apple_data.py` (base temporaire, lecture seule, conversion centralisée, compatibilité UTC du backfill, conversations/recherche/statistiques et Contacts injecté), plus 60 tests iMessage/pipeline reliés ; 67 tests ciblés passants. Le garde-fou AST interdit toute nouvelle ouverture directe de `chat.db` et toute seconde définition de `apple_epoch_to_datetime()`. Suite complète : 555 passants, 1 ignoré ; `compileall` et `git diff --check` réussis. La validation TCC sur le `chat.db` réel et l'observation opérationnelle 24 h ne sont pas vérifiables en CI.

| Fichier | Contenu |
|---|---|
| `tests/test_apple_data.py` | Mock `chat.db`, mode read-only, conversion timestamp, conversations, recherche, statistiques, provider Contacts et invariant d'architecture |

### Phase 6 — implémentée et validée localement le 14/07/2026

Preuves : 9 tests Vitest, typecheck et build Next.js 15 réussis ; 3 scénarios Playwright desktop/mobile ; 4 contrats FastAPI ; 18 tests et build Vite historiques ; build PWA Next.js 14 historique. Le workflow CI exécute désormais le fallback Vite et un job séparé `Frontend unifié (tests + build)`. La suite backend complète n'a pas été contournée artificiellement : l'installation PyAudio s'arrête sur `fatal error: 'portaudio.h' file not found`; les 4 tests Phase 6 autonomes passent avec les dépendances FastAPI minimales.

| Fichier | Contenu |
|---|---|
| `frontend/src/device.test.ts` | Sélection déterministe des layouts téléphone, tablette et desktop |
| `frontend/src/auth-client.test.ts` | Contrat auth, erreurs 401 typées, cookie et uploads via le wrapper partagé |
| `frontend/src/lock-gate.test.tsx` | Fail-closed, contenu privé masqué puis révélé après déverrouillage |
| `frontend/e2e/unified.spec.ts` | Playwright — navigation desktop, dashboard mobile et refus mobile non authentifié |
| `tests/test_phase6_frontend.py` | Priorité/fallback/coexistence FastAPI et unicité du wrapper réseau |

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
| Fonctions de test backend déclarées | 546 | 540 | 554 |
| Tests intégration backend | ~14 | ~14 | 50+ |
| Tests frontend | 18 web, 0 PWA | 18 web, 0 PWA | 27 passants |
| Tests E2E | 0 | 0 | 3 passants |
| Tests sécurité | ~5 | 5 | 25+ |
| Tests offline | 2 | 2 | 20+ |
