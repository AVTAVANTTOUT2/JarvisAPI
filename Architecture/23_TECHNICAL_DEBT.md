# 23 — Gestion de la Dette Technique

**Date** : 11 juillet 2026
**Statut** : Processus — toute nouvelle dette doit être enregistrée

---

## Définition

Une dette technique est un choix d'implémentation qui sacrifie la qualité long terme pour un bénéfice court terme. Elle doit être **consciente, documentée, et planifiée** pour remboursement.

## Inventaire actuel

| ID | Description | Sévérité | Localisation | Estimé (h) | Planifié |
|---|---|---|---|---|---|
| TD-001 | God object main.py (7194 lignes) | CRITIQUE | main.py | 24h | Phase 4 |
| TD-002 | God object database/__init__.py (4169 lignes) | CRITIQUE | database/__init__.py | 8h | Phase 2 |
| TD-003 | PWA sans LockGate | CRITIQUE | pwa/ | 16h | Phase 6 |
| TD-004 | 3 curseurs ROWID indépendants | MAJEURE | 3 fichiers | 2h | Phase 1 |
| TD-005 | 19 appels directs create_notification() | MAJEURE | 19 fichiers | 8h | Phase 3 |
| TD-006 | 2 frontends, 0 composants partagés | MAJEURE | web/ + pwa/ | 40h | Phase 6 |
| TD-007 | 25+ connexions directes chat.db | MAJEURE | 25+ fichiers | 24h | Phase 5 |
| TD-008 | Event bus sans consommateurs métiers | MAJEURE | jarvis/event_bus.py | 16h | Phase 3 |
| TD-009 | 4 conversions Apple timestamp | MODÉRÉE | 4 fichiers | 1h | Phase 5 |
| TD-010 | Cycle main↔daemon | MODÉRÉE | 2 fichiers | 4h | Phase 1 |
| TD-011 | 42 imports dans main.py | MODÉRÉE | main.py | 24h | Phase 4 |
| TD-012 | Service Worker dupliqué | MINEURE | web/ + pwa/ | 2h | Phase 6 |
| TD-013 | Dates relatives dupliquées | MINEURE | 2 frontends | 1h | Phase 6 |

## Comment identifier une nouvelle dette

Lors d'une code review, si l'un des critères suivants est détecté, une entrée de dette DOIT être créée :

- Duplication de code (>10 lignes identiques)
- Fonction >100 lignes sans raison valable
- Module >500 lignes ajouté sans split
- Nouveau lazy import non justifié
- Nouvel accès direct à chat.db
- Nouvel appel direct au LLM hors ai_service
- Test manquant pour une fonction critique

## Comment documenter une dette

Créer une entrée dans la table `technical_debt` et dans ce document :

```sql
INSERT INTO technical_debt (id, description, severity, location, estimated_hours, created_at, status)
VALUES ('TD-XXX', 'Description claire', 'CRITICAL|MAJOR|MODERATE|MINOR', 'fichier.py', 8, datetime('now'), 'OPEN');
```

## Comment prioriser

| Sévérité | Critère | Délai max de résolution |
|---|---|---|
| CRITIQUE | Risque sécurité, perte de données, instabilité | Prochaine phase |
| MAJEURE | Duplication massive, god object, couplage fort | 2 phases |
| MODÉRÉE | Duplication localisée, code mort, warning | 4 phases |
| MINEURE | Cosmétique, convention, optimisation | Backlog |

## Comment suivre la résolution

- Chaque phase de refactoring résout un ensemble de dettes planifiées
- À la fin de chaque phase, les dettes résolues passent à `RESOLVED`
- Les nouvelles dettes introduites (involontairement) sont ajoutées avec le tag `REGRESSION`
- Un rapport de dette est généré à chaque fin de phase

## Règles

1. **Toute nouvelle dette doit être enregistrée** — pas de « on nettoiera plus tard » sans trace
2. **La dette critique bloque les nouvelles features** — règle d'or
3. **Le remboursement est planifié** — une dette sans échéance est une fuite
4. **La dette est visible** — ce document est la source de vérité
