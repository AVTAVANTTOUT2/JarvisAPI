# 26 — Score de Santé de l'Architecture

**Date** : 11 juillet 2026
**Statut** : Métrique de référence — mesuré après chaque phase

---

## Définition

Le score de santé mesure la qualité architecturale du projet sur 10 dimensions. Il est calculé après chaque phase de refactoring.

## Dimensions et pondérations

| Dimension | Poids | Score actuel | Cible Phase 6 | Méthode de mesure |
|---|---|---|---|---|
| **Couverture de tests** | 15% | 4/10 | 8/10 | pytest --cov |
| **Duplication** | 15% | 3/10 | 9/10 | duplicate_scanner.py |
| **Dette technique** | 15% | 5/10 | 8/10 | TECH_DEBT.md (dettes × sévérité) |
| **Dépendances circulaires** | 10% | 8/10 | 10/10 | architecture_check.py |
| **Taille des modules** | 10% | 5/10 | 9/10 | wc -l (modules >1000l = 0) |
| **Documentation** | 10% | 10/10 | 10/10 | Revue manuelle |
| **Performances** | 10% | 6/10 | 8/10 | Benchmarks |
| **Sécurité** | 5% | 7/10 | 9/10 | security_audit.py |
| **Observabilité** | 5% | 2/10 | 8/10 | /health, /metrics actifs |
| **Stabilité** | 5% | 5/10 | 9/10 | Crashs/24h, uptime |

## Calcul

```
Score = Σ (dimension_score × poids)
```

## Score actuel après Phase 3 : 5.40/10

| Dimension | Score | Justification |
|---|---|---|
| Couverture | 4 | 540 fonctions de test backend déclarées, couverture globale non mesurée de façon fiable |
| Duplication | 3 | 2 frontends, 8 duplications majeures, 25+ lecteurs chat.db |
| Dette technique | 5 | God object database et bus sans consommateurs remboursés ; main.py et les frontends concentrent encore la dette majeure |
| Dépendances | 8 | Cycle main↔daemons supprimé via `pipeline.py` ; imports lazy résiduels sans cycle applicatif identifié |
| Taille modules | 5 | Un seul module >1000 lignes (`main.py`) ; façade database 236 lignes, maximum DB 666 |
| Documentation | 10 | CLAUDE.md et Architecture/ synchronisés avec les preuves de chaque phase |
| Performance | 6 | SQLite WAL OK, mais pas de cache LLM |
| Sécurité | 7 | Auth robuste, mais PWA sans LockGate |
| Observabilité | 2 | Journal d'événements disponible, mais pas de `/health` ni de métriques |
| Stabilité | 5 | Supervisor auto-restart et handlers isolés ; pas de validation opérationnelle 24 h |

## Score cible après Phase 6 : ≥ 8.5/10

| Dimension | Cible | Comment |
|---|---|---|
| Couverture | 8 | Tests frontend (Phase 6), tests par routeur (Phase 4) |
| Duplication | 9 | Frontend unifié (Phase 6), AppleDataService (Phase 5) |
| Dette technique | 8 | Dettes CRITIQUES et MAJEURES résolues |
| Dépendances | 10 | 0 cycle (Phase 1) |
| Taille modules | 9 | Split main.py (Phase 4) et database (Phase 2) |
| Documentation | 10 | Mise à jour continue |
| Performance | 8 | Cache LLM (travail futur) |
| Sécurité | 9 | LockGate PWA (Phase 6) |
| Observabilité | 8 | `/health`, `/metrics` (Q4) |
| Stabilité | 9 | Monitoring proactif (Q4) |

## Maintien du score

- Le score est mesuré après chaque phase
- Il ne doit **jamais baisser**
- Si une PR fait baisser le score, elle est refusée (sauf ADR)
- Cible revue annuellement
