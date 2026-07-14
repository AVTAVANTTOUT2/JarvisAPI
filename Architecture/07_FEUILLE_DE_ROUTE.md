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
- [ ] Phase 2 : Database modulaire (en cours — 12 modules extraits)

**Semaine 2 — Découplage**
- [ ] Phase 3 : Event bus actif (10 événements)
- [ ] Phase 4 : Routeurs FastAPI (12 routeurs)
- [ ] Phase 5 : Apple Data Service (début)

**Semaines 3-4 — Unification**
- [ ] Phase 5 : Apple Data Service (fin)
- [ ] Phase 6 : Frontend unifié + SDK Auth
- [ ] Tests de non-régression complets

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
| Problèmes critiques | 4 | 0 | 0 |
| Problèmes majeurs | 6 | 2 | 0 |
| God objects (>1000 lignes) | 2 | 0 | 0 |
| Duplications majeures | 8 | 3 | 0 |
| Couverture tests backend | ~60% | 80% | 90% |
| Tests frontend | 18 (web offline) | 50+ | 100+ |
| Applications frontend | 2 | 1.5 (coexistence) | 1 |
| Connexions directes `chat.db` | 25+ | 10 | 0 |
| Temps démarrage backend | ~3s | <2s | <1s |
| UI polling | 30s | Push (event bus) | Push |
