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

- [ ] `busy_timeout` configuré → `PRAGMA busy_timeout` retourne 5000
- [ ] Race condition WS → zéro `Set changed size during iteration` dans les logs
- [x] Curseurs ROWID → registre central persistant, offsets monotones nommés, aucun ancien attribut mémoire
- [ ] Cycle main↔daemon → `grep "from main import" scripts/jarvis_daemon.py` retourne 0
- [ ] pipeline.py existe et est importé par main.py ET jarvis_daemon.py

### Phase 2 — Database modulaire

- [ ] `wc -l database/__init__.py` < 500 lignes
- [ ] Aucun import cassé → `python -c "from database import *"` réussit
- [ ] Chaque module extrait a un docstring et des type hints
- [ ] La couverture de tests n'a pas baissé

### Phase 3 — Event bus actif

- [ ] 10 types d'événements définis et documentés
- [ ] `grep "event_bus.emit" --include="*.py" -r` ≥ 5 (fichiers qui émettent)
- [ ] `grep "event_bus.on" --include="*.py" -r` ≥ 3 (fichiers qui consomment)
- [ ] Le polling 30s des notifications dans l'UI est remplacé par un push WebSocket
- [ ] Table `event_log` créée et alimentée

### Phase 4 — Routeurs FastAPI

- [ ] `wc -l main.py` < 500 lignes
- [ ] 12 fichiers dans `api/` avec des `APIRouter`
- [ ] `curl` sur chaque endpoint critique retourne 200
- [ ] Le lifespan de `main.py` n'a pas changé de comportement

### Phase 5 — Apple Data Service

- [ ] `grep -r "chat.db" --include="*.py" | grep -v apple_data | grep -v test` retourne 0
- [ ] `apple_epoch_to_datetime()` n'existe que dans `apple_data.py`
- [ ] Tous les consommateurs migrés (IMessageBridge, JarvisDaemon, RelationshipAnalyzer, etc.)
- [ ] Tests d'intégration passent avec mock de chat.db

### Phase 6 — Frontend unifié

- [ ] Un seul `package.json` pour tout le frontend (dans `frontend/`)
- [ ] Toutes les vues desktop ET mobile fonctionnent
- [ ] `cd frontend && pnpm test` → 0 échec
- [ ] Le SDK auth est dans `jarvis_auth/` et importé par le frontend
- [ ] L'ancien frontend (`web/dist/`, `pwa/out/`) coexiste sans erreur
- [ ] Playwright E2E passe sur desktop + mobile

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
