# 17 — Definition of Done

**Date** : 11 juillet 2026
**Statut** : Règle de gouvernance

---

Ce document définit les critères obligatoires pour considérer une phase de refactoring comme terminée.

## Critères universels (toutes phases)

Chaque phase doit satisfaire AU MINIMUM :

| # | Critère | Vérification |
|---|---|---|
| 1 | **Tous les tests passent** | `python -m pytest tests/ -q` → 0 échec |
| 2 | **Couverture minimale atteinte** | La couverture ne doit PAS baisser vs avant la phase. Cible : +5% par phase. |
| 3 | **Documentation mise à jour** | README, CLAUDE.md, et les fichiers Architecture/ sont à jour |
| 4 | **ADR mis à jour** | Tout ADR modifié par la phase a son statut changé (Proposé → Accepté → Implémenté) |
| 5 | **Performances égales ou meilleures** | Temps de démarrage, latence API, utilisation mémoire — pas de régression >10% |
| 6 | **Aucun nouveau warning critique** | `grep -i "error\|critical" data/logs/backend.log` des dernières 24h |
| 7 | **Plan de rollback documenté** | Dans le message de commit ou le document de phase : comment revenir en arrière |
| 8 | **Code review passée** | Review par un pair ou auto-review avec la checklist Architecture/ |

## Critères spécifiques par phase

### Phase 1 — Quick Wins P0

- [x] `busy_timeout` configuré → `PRAGMA busy_timeout` retourne 5000 (vérifié par test le 14/07/2026)
- [x] Race condition WS → snapshot défensif + asyncio.Lock, mutations sérialisées et I/O réseau hors verrou (vérifié par 2 tests le 14/07/2026)
- [x] Curseurs ROWID → registre central persistant, offsets monotones nommés, aucun ancien attribut mémoire
- [x] Cycle main↔daemon → aucun import de `main` dans `jarvis_daemon.py` ou `audio_daemon.py`
- [x] `pipeline.py` existe, est configuré par `main.py` et consommé par les deux daemons

La validation opérationnelle sur 24 heures n'est pas reproductible en CI faute de logs de production. Elle reste un contrôle de déploiement ; les invariants de code de la Phase 1 sont couverts par 7 tests ciblés déterministes.

### Phase 2 — Database modulaire

- [x] `wc -l database/__init__.py` = 236 lignes après ajout du réexport `event_log` en Phase 3 (< 500)
- [x] Aucun import cassé → `python -c "from database import *"` réussit
- [x] Chaque module extrait a un docstring et des type hints — contrôlé par test statique
- [x] La couverture de tests n'a pas baissé — 538 passants, 1 ignoré après extraction ; 542 passants, 1 ignoré après Phase 3

### Phase 3 — Event bus actif

- [x] 10 types d'événements immuables, versionnés et documentés dans `jarvis/events.py`
- [x] `rg -l "event_bus\.emit" --glob '*.py' .` → 13 fichiers correspondants, dont 11 émetteurs de production
- [x] `rg -l "event_bus\.on" --glob '*.py' .` → 3 fichiers consommateurs réels
- [x] Le polling périodique notifications/tâches de la PWA est remplacé par le flux SSE et les invalidations React Query
- [x] Table `event_log` créée par `init_db()`, alimentée après commit et idempotente par `event_id`

Preuves exécutées le 14/07/2026 : 4 tests Phase 3 passants, suite backend complète à 542 passants et 1 ignoré, build PWA réussi, `compileall` et `git diff --check` réussis. Le journal rend un futur rejeu possible, mais le rejeu automatique reste hors périmètre. L'observation opérationnelle sur 24 heures n'est pas vérifiable dans l'environnement de test.

### Phase 4 — Routeurs FastAPI

- [x] `wc -l main.py` < 500 lignes — 175 lignes
- [x] Exactement 12 fichiers `api/router_*.py` exposent des `APIRouter`
- [x] Les signatures des 174 opérations HTTP, du WebSocket et les 157 chemins OpenAPI sont identiques à la baseline ; les endpoints couverts par la suite existante passent via `TestClient`
- [x] Le lifespan extrait dans `api/lifespan.py` est monté explicitement et la suite de non-régression reste verte

Preuves exécutées le 14/07/2026 : 6 tests Phase 4 passants, suite complète à 548 passants et 1 ignoré, `compileall`, Ruff et `git diff --check` réussis. Tous les modules `api/` restent à 500 lignes ou moins et aucun n'importe `main.py`. Aucun serveur réel, campagne `curl` exhaustive ou observation opérationnelle sur 24 h n'a été exécuté dans cet environnement.

### Phase 5 — Apple Data Service

- [x] `AppleDataService` est l'unique ouverture applicative de `chat.db` (`mode=ro` + `PRAGMA query_only`)
- [x] `apple_epoch_to_datetime()` n'est définie que dans `integrations/apple_data.py`
- [x] Les consommateurs iMessage sont migrés directement ou via `IMessageReader` (bridge, daemons, import/backfill, diagnostics, TV et analyseurs relationnels)
- [x] Tests d'intégration passent avec mock de chat.db et garde-fou AST contre les accès directs

Preuves exécutées le 14/07/2026 : 67 tests ciblés passants, suite backend complète à 555 passants et 1 ignoré, `compileall` et `git diff --check` réussis. La validation Full Disk Access/TCC sur un `chat.db` réel et l'observation opérationnelle 24 h restent manuelles.

### Phase 6 — Frontend unifié

- [x] `frontend/package.json` est le manifeste canonique du frontend unifié ; les manifestes historiques restent uniquement pour les fallbacks réversibles
- [x] Les vues desktop ET mobile sont réutilisées par le layout responsive et leurs builds réussissent
- [x] `cd frontend && pnpm test` → 9 passants, 0 échec
- [x] Le SDK auth est dans `jarvis_auth/` et importé par le frontend, `web/` et `pwa/`
- [x] L'ancien frontend (`web/dist/`, `pwa/out/`) coexiste sans erreur
- [x] Playwright E2E passe sur desktop + mobile (3 scénarios)

Preuves exécutées le 14/07/2026 : typecheck et build Next.js 15 de 25 pages, 10 tests Vitest (dont cleanup des services privés au soft lock), 3 E2E Playwright, 4 contrats FastAPI, 18 tests web historiques et builds des deux fallbacks. Le workflow CI comprend désormais un job dédié au frontend unifié, en plus du fallback Vite. Le Service Worker unifié exclut les API et données privées. La suite backend complète reste à confirmer en CI : l'environnement local ne fournit pas `portaudio.h` à PyAudio. Les appareils physiques, le comportement d'installation natif et l'observation opérationnelle 24 h restent des validations manuelles.

## Checklist de code review

Avant de merger une phase, vérifier :

```
[ ] Pas de duplication introduite (DRY)
[ ] Pas de lazy import (tous les imports sont top-level ou justifiés)
[ ] Pas de god object créé (>500 lignes)
[ ] Type hints sur toutes les fonctions publiques
[ ] Docstrings sur toutes les classes et fonctions publiques
[ ] Pas de `except Exception` nu (toujours spécifier le type)
[ ] Pas de `print()` (utiliser `logging`)
[ ] Pas de secret dans le code (tout dans `.env`)
[ ] Tests pour le nouveau code
[ ] Tests pour les edge cases (null, vide, erreur)
```

## Métriques de succès par phase

| Phase | Critère principal | Seuil |
|---|---|---|
| 1 | Problèmes P0 résolus | 4/4 |
| 2 | Lignes database/__init__.py | < 500 |
| 3 | Événements actifs | ≥ 10 types |
| 4 | Lignes main.py | < 500 |
| 5 | Connexions directes chat.db | 0 (hors apple_data) |
| 6 | Applications frontend | 1 |
