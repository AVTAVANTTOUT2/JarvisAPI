# 07 — Feuille de Route Technique

**Date** : 10 août 2026

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
- [x] Phase 4 : Routeurs FastAPI (validée le 14/07/2026, puis étendue — 18 routeurs montés, `main.py` sous 500 lignes, contrat API verrouillé)
- [x] Phase 5 : Apple Data Service (validée le 14/07/2026 — accès `chat.db` centralisé et conversion Apple unique)

**Semaines 3-4 — Unification**
- [x] Phase 6 : Frontend unifié + SDK Auth (validée sur `main` le 14/07/2026 — Next.js 15 responsive, LockGate partagé, wrapper API unique, fallbacks conservés)
- [x] CI de non-régression complète (Python, Vite et Next.js unifié) sur le commit de merge Phase 6
- [x] NotificationService : 16 producteurs migrés, déduplication atomique, Web Push et contrat de compatibilité validés le 14/07/2026
- [x] Porte de release backend reproductible : lock Python 3.12 propre, suite standard sans réservation Metal, cycle de vie Qwen3/Event Bus fiabilisé
- [x] Outillage de campagne 24 h livré (`tools/run_release_soak.py`, artefact JSON borné et sans payload sensible)
- [x] Socle de stabilisation audio post-PR #17 : STT local partagé, Qwen3 local explicite, moteurs optionnels sans repli silencieux et contrats automatisés
- [ ] Validation manuelle sur appareils réels (installation, veille, GPS et ergonomie)
- [ ] Clôture audio : campagne 24 h sur le Mac cible, scénarios micro réels et enregistrements 1/30/180 minutes selon `30_PLAN_STABILISATION_AUDIO.md`

### Q4 2026 — Améliorations (Septembre-Décembre)

**Fondation solide acquise → nouvelles features possibles**

- [ ] Offline First complet — primitives IndexedDB présentes, lectures/écritures à généraliser à toutes les vues
- [ ] Sync queue — UUID/timestamp/retry présents, checksum, version d'entité et résolution de conflits à ajouter
- [ ] Health Dashboard (`/health`) — lot parallèle réservé à Claude, à cocher après validation et fusion de sa PR
- [ ] Monitoring (métriques temps réel) — même lot observabilité, sans chevauchement avec la stabilisation de release
- [x] Socle de recherche unifiée backend (FTS5 + embeddings)
- [ ] Expérience de recherche unifiée dans le frontend
- [ ] Authentification biométrique (Face ID / Touch ID)

### Ordre recommandé des travaux restants

1. Exécuter et archiver la campagne de release 24 h, puis les preuves sur appareils physiques.
2. Terminer Offline First et la résolution de conflits de la sync queue.
3. Valider et fusionner le lot parallèle Health Dashboard / monitoring.
4. Unifier l'expérience de recherche, puis ajouter la biométrie après stabilisation des flux offline.

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
| Tests frontend | 28 Vitest + 3 E2E | 50+ | 100+ |
| Applications frontend | 1 prioritaire + 2 fallbacks de rollback | 1 + fallbacks | 1 |
| Connexions directes `chat.db` | 0 hors `AppleDataService` | 0 | 0 |
| Temps démarrage backend | ~3s | <2s | <1s |
| UI polling notifications/tâches | Push SSE depuis Phase 3 | Push | Push |
