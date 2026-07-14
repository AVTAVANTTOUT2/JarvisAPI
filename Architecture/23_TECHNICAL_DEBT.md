# 23 — Gestion de la Dette Technique

**Date** : 11 juillet 2026
**Statut** : Processus — toute nouvelle dette doit être enregistrée

---

## Définition

Une dette technique est un choix d'implémentation qui sacrifie la qualité long terme pour un bénéfice court terme. Elle doit être **consciente, documentée, et planifiée** pour remboursement.

## Inventaire actuel

| ID | Description | Sévérité | Localisation | Estimé (h) | Planifié |
|---|---|---|---|---|---|
| TD-001 | God object main.py (7 197 lignes) | RÉSOLUE | `main.py` 175 lignes + 12 routeurs | 0h | Phase 4 — 14/07/2026 |
| TD-002 | God object database/__init__.py | RÉSOLUE | Façade 236 lignes, 25 modules | 0h | Phase 2 — 14/07/2026 |
| TD-003 | PWA sans LockGate | RÉSOLUE | `jarvis_auth/` partagé | 0h | Phase 6 — 14/07/2026 |
| TD-004 | 3 curseurs ROWID indépendants | RÉSOLUE | `imessage_cursor.py` | 0h | Phase 1 — 11/07/2026 |
| TD-005 | Producteurs directs de `create_notification()` | RÉSOLUE | `jarvis/notification_service.py` + 16 producteurs migrés | 0h | 14/07/2026 |
| TD-006 | 2 frontends, 0 composants partagés | RÉSOLUE pour le chemin canonique | `frontend/` + sources réutilisées | 0h | Phase 6 — 14/07/2026 |
| TD-007 | 25+ connexions directes chat.db | RÉSOLUE | `integrations/apple_data.py` + consommateurs migrés | 0h | Phase 5 — 14/07/2026 |
| TD-008 | Event bus sans consommateurs métiers | RÉSOLUE | 10 événements, 3 consommateurs | 0h | Phase 3 — 14/07/2026 |
| TD-009 | 4 conversions Apple timestamp | RÉSOLUE | `apple_epoch_to_datetime()` / `datetime_to_apple_epoch()` | 0h | Phase 5 — 14/07/2026 |
| TD-010 | Cycle main↔daemon | RÉSOLUE | `pipeline.py` | 0h | Phase 1 — 11/07/2026 |
| TD-011 | 42 imports concentrés dans main.py | RÉSOLUE | Dépendances réparties dans `api/` | 0h | Phase 4 — 14/07/2026 |
| TD-012 | Service Workers des fallbacks conservés | MINEURE | frontend/ + web/ + pwa/ | 2h | Retrait des fallbacks |
| TD-013 | Dates relatives dupliquées | MINEURE | sources desktop/mobile | 1h | Backlog frontend |

## Dettes critiques remboursées

| Description | Résolution | Date |
|---|---|---|
| SQLite sans `busy_timeout` | `PRAGMA busy_timeout = 5000` sur chaque connexion applicative | 11/07/2026 |
| Race condition sur le set WebSocket | Verrou sur les mutations et snapshot avant diffusion | 11/07/2026 |
| Curseurs ROWID uniquement en mémoire | Registre SQLite central avec offset monotone par consommateur | 11/07/2026 |
| Cycle d'import `main.py` ↔ daemons | Contrat `pipeline.py` configuré par injection | 11/07/2026 |
| Producteurs directs de notifications | `NotificationService`, façade compatible et garde-fou statique sur 16 producteurs | 14/07/2026 |
| Event bus sans consommateurs métiers | 10 événements typés, journal SQLite, WebSocket, TTS et PWA SSE | 14/07/2026 |
| God object API | `main.py` réduit à 175 lignes, 12 routeurs et handlers/support spécialisés sous 500 lignes | 14/07/2026 |
| Imports concentrés dans `main.py` | Dépendances déplacées avec leur responsabilité, sans import inverse `api → main` | 14/07/2026 |
| Lecteurs directs de `chat.db` et conversions Apple dupliquées | `AppleDataService` read-only centralisé et garde-fou AST | 14/07/2026 |
| PWA sans écran de verrouillage | SDK `jarvis_auth/` commun, LockGate fail-closed et tests mobile | 14/07/2026 |
| Frontends sans réutilisation et wrappers API concurrents | Application Next.js 15 responsive, vues sources réutilisées et unique client réseau authentifié | 14/07/2026 |

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
