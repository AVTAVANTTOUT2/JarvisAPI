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
| **Duplication** | 15% | 5/10 | 9/10 | duplicate_scanner.py |
| **Dette technique** | 15% | 7/10 | 8/10 | TECH_DEBT.md (dettes × sévérité) |
| **Dépendances circulaires** | 10% | 9/10 | 10/10 | tests d'architecture + analyse statique |
| **Taille des modules** | 10% | 9/10 | 9/10 | `wc -l` (`main.py` et `api/` ≤ 500 lignes) |
| **Documentation** | 10% | 10/10 | 10/10 | Revue manuelle |
| **Performances** | 10% | 6/10 | 8/10 | Benchmarks |
| **Sécurité** | 5% | 7/10 | 9/10 | security_audit.py |
| **Observabilité** | 5% | 2/10 | 8/10 | /health, /metrics actifs |
| **Stabilité** | 5% | 5/10 | 9/10 | Crashs/24h, uptime |

## Calcul

```
Score = Σ (dimension_score × poids)
```

## Score actuel après Phase 5 : 6.50/10

| Dimension | Score | Justification |
|---|---|---|
| Couverture | 4 | 553 fonctions de test backend déclarées, couverture globale non mesurée de façon fiable |
| Duplication | 5 | 2 frontends restent, mais les lecteurs `chat.db` et la conversion Apple sont centralisés |
| Dette technique | 7 | God objects, bus, accès Apple et conversions sont remboursés ; les frontends et notifications concentrent la dette majeure restante |
| Dépendances | 9 | Cycle main↔daemons supprimé via `pipeline.py` ; aucun import inverse `api → main` |
| Taille modules | 9 | `main.py` fait 175 lignes et chaque module `api/` 500 lignes ou moins ; façade database 236 lignes, maximum DB 666 |
| Documentation | 10 | CLAUDE.md et Architecture/ synchronisés avec les preuves de chaque phase |
| Performance | 6 | SQLite WAL OK, mais pas de cache LLM |
| Sécurité | 7 | Auth robuste, mais PWA sans LockGate |
| Observabilité | 2 | Journal d'événements disponible, mais pas de `/health` ni de métriques |
| Stabilité | 5 | Supervisor auto-restart et handlers isolés ; pas de validation opérationnelle 24 h |

## Score cible après Phase 6 : ≥ 8.5/10

| Dimension | Cible | Comment |
|---|---|---|
| Couverture | 8 | Tests frontend (Phase 6) et couverture métier plus complète par route |
| Duplication | 9 | Frontend unifié (Phase 6) |
| Dette technique | 8 | Dettes CRITIQUES et MAJEURES résolues |
| Dépendances | 10 | 0 cycle (Phase 1) |
| Taille modules | 9 | ✅ Split main.py (Phase 4) et database (Phase 2) atteint |
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
