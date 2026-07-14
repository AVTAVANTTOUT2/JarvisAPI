# 07 — Feuille de Route Technique

**Date** : 11 juillet 2026

## Priorisation

Les travaux sont classés selon l'ordre de priorité suivant :

1. **Corrections critiques** — Risque immédiat pour les données ou la sécurité
2. **Fiabilité** — Stabilité du système 24/7
3. **Sécurité** — Protection des données personnelles
4. **Cohérence des données** — Absence de doublons, intégrité
5. **Performances** — Temps de réponse, utilisation ressources
6. **Simplification de l'architecture** — Maintenabilité long terme
7. **Expérience utilisateur** — Interface unifiée, offline
8. **Nouvelles fonctionnalités** — Ajouts après stabilisation

## Roadmap

### Q3 2026 — Stabilisation (Juillet-Août)

**Semaine 1 — Fondations**
- [x] Audit architectural complet
- [x] Phase 1 : Quick Wins P0 (validée le 14/07/2026)
  - `busy_timeout` SQLite, race WS, curseur ROWID, `pipeline.py`
- [x] Phase 2 : Database modulaire (validée le 14/07/2026 — façade 236 lignes, 25 modules après ajout du journal Phase 3)

**Semaine 2 — Découplage**
- [x] Phase 3 : Event bus actif (validée le 14/07/2026 — 10 événements, 3 consommateurs, PWA temps réel)
- [x] Phase 4 : Routeurs FastAPI (validée le 14/07/2026 — 12 routeurs, `main.py` 175 lignes, contrat API inchangé)
- [x] Phase 5 : Apple Data Service (validée le 14/07/2026 — accès `chat.db` centralisé et conversion Apple unique)

**Semaines 3-4 — Unification**
- [x] Phase 6 : Frontend unifié + SDK Auth (validée localement le 14/07/2026 — Next.js 15 responsive, LockGate partagé, wrapper API unique, fallbacks conservés)
- [ ] CI de non-régression complète sur la branche Phase 6 et validation manuelle sur appareils réels

### Q4 2026 — Améliorations (Septembre-Décembre)

**Fondation solide acquise → nouvelles features possibles**

- [ ] Offline First complet (IndexedDB toutes les vues)
- [ ] Sync queue avec UUID/timestamp/checksum
- [ ] Health Dashboard (`/health`)
- [ ] Monitoring (métriques temps réel)
- [ ] Moteur de recherche unifié (FTS5 + embeddings)
- [ ] Authentification biométrique (Face ID / Touch ID)

### 2027 — Maturité

- [ ] Mode multi-utilisateur (si pertinent)
- [ ] Chiffrement complet au repos
- [ ] Sauvegarde cloud chiffrée
- [ ] API publique documentée (OpenAPI)
- [ ] SDK développeurs

## Règle d'or

**Toute nouvelle fonctionnalité doit être justifiée si une faiblesse critique d'architecture reste non résolue.**

Avant d'ajouter une feature :
1. Tous les P0 sont-ils corrigés ?
2. Tous les P1 sont-ils au moins planifiés ?
3. La nouvelle feature introduit-elle de la duplication ?
4. La nouvelle feature a-t-elle des tests ?

## Métriques de succès

| Métrique | État actuel | Cible Q3 2026 | Cible Q4 2026 |
|---|---|---|---|
| Problèmes critiques | 0 | 0 | 0 |
| Problèmes majeurs | 1 | 1 | 0 |
| God objects API/DB (>1000 lignes) | 0 | 0 | 0 |
| Duplications majeures | 8 | 3 | 0 |
| Couverture tests backend | ~60% | 80% | 90% |
| Tests frontend | 27 Vitest + 3 E2E | 50+ | 100+ |
| Applications frontend | 1 prioritaire + 2 fallbacks de rollback | 1 + fallbacks | 1 |
| Connexions directes `chat.db` | 0 hors `AppleDataService` | 0 | 0 |
| Temps démarrage backend | ~3s | <2s | <1s |
| UI polling notifications/tâches | Push SSE depuis Phase 3 | Push | Push |
