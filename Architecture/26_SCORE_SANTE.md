# 26 — Score de Santé de l'Architecture

**Date** : 11 juillet 2026
**Statut** : Métrique de référence — mesuré après chaque phase

---

## Définition

Le score de santé mesure la qualité architecturale du projet sur 10 dimensions. Il est calculé après chaque phase de refactoring.

## Dimensions et pondérations

| Dimension | Poids | Score actuel | Prochaine cible | Méthode de mesure |
|---|---|---|---|---|
| **Couverture de tests** | 15% | 5/10 | 8/10 | pytest --cov |
| **Duplication** | 15% | 7/10 | 9/10 | duplicate_scanner.py |
| **Dette technique** | 15% | 8/10 | 9/10 | TECH_DEBT.md (dettes × sévérité) |
| **Dépendances circulaires** | 10% | 9/10 | 10/10 | tests d'architecture + analyse statique |
| **Taille des modules** | 10% | 9/10 | 9/10 | `wc -l` (`main.py` et `api/` ≤ 500 lignes) |
| **Documentation** | 10% | 10/10 | 10/10 | Revue manuelle |
| **Performances** | 10% | 6/10 | 8/10 | Benchmarks |
| **Sécurité** | 5% | 9/10 | 9/10 | security_audit.py |
| **Observabilité** | 5% | 2/10 | 8/10 | /health, /metrics actifs |
| **Stabilité** | 5% | 5/10 | 9/10 | Crashs/24h, uptime |

## Calcul

```
Score = Σ (dimension_score × poids)
```

## Score actuel après Phase 6 + NotificationService : 7.20/10

| Dimension | Score | Justification |
|---|---|---|
| Couverture | 5 | 565 tests pytest collectés (564 passants, 1 ignoré), 28 Vitest et 3 E2E ; couverture globale non mesurée de façon fiable |
| Duplication | 7 | Frontend canonique, auth et client API unifiés ; vues carte/date et fallbacks restent dupliqués |
| Dette technique | 8 | Toutes les dettes critiques recensées sont remboursées ; retrait des fallbacks et dates relatives dupliquées restent ouverts |
| Dépendances | 9 | Cycle main↔daemons supprimé via `pipeline.py` ; aucun import inverse `api → main` |
| Taille modules | 9 | `main.py` fait 175 lignes et chaque module `api/` 500 lignes ou moins ; façade database 236 lignes, maximum DB 666 |
| Documentation | 10 | README, CLAUDE.md et Architecture/ synchronisés avec les preuves des six phases |
| Performance | 6 | SQLite WAL OK, mais pas de cache LLM |
| Sécurité | 9 | Auth robuste et LockGate fail-closed partagé sur desktop/mobile ; pas de pentest externe |
| Observabilité | 2 | Journal d'événements disponible, mais pas de `/health` ni de métriques |
| Stabilité | 5 | Supervisor auto-restart et handlers isolés ; pas de validation opérationnelle 24 h |

## Cible suivante : ≥ 8.5/10

La Phase 6 améliore nettement la sécurité et la duplication, mais ne suffit pas honnêtement à atteindre 8.5 : l'observabilité, la couverture mesurée, les performances et une validation 24 h restent nécessaires.

| Dimension | Cible | Comment |
|---|---|---|
| Couverture | 8 | Tests frontend (Phase 6) et couverture métier plus complète par route |
| Duplication | 9 | Retrait des fallbacks et utilitaires UI dupliqués |
| Dette technique | 9 | Retrait des fallbacks et dettes frontend résiduelles traités |
| Dépendances | 10 | 0 cycle (Phase 1) |
| Taille modules | 9 | ✅ Split main.py (Phase 4) et database (Phase 2) atteint |
| Documentation | 10 | Mise à jour continue |
| Performance | 8 | Cache LLM (travail futur) |
| Sécurité | 9 | ✅ LockGate partagé atteint ; maintenir et auditer |
| Observabilité | 8 | `/health`, `/metrics` (Q4) |
| Stabilité | 9 | Monitoring proactif (Q4) |

## Maintien du score

- Le score est mesuré après chaque phase
- Il ne doit **jamais baisser**
- Si une PR fait baisser le score, elle est refusée (sauf ADR)
- Cible revue annuellement
