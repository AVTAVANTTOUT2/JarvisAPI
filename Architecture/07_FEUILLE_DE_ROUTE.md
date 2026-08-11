# 07 — Feuille de Route Technique

**Date** : 11 août 2026

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

> Les cases cochées le 11 août 2026 correspondent à du code implémenté et
> validé dans la pile de PR brouillon #202, #204 à #212. Elles ne signifient
> pas que ces PR ont été fusionnées sur `main`. Les validations 24 h, audio et
> appareils physiques restent volontairement ouvertes tant que leurs preuves
> réelles ne sont pas archivées.

### Q3 2026 — Stabilisation (Juillet-Août)

**Semaine 1 — Fondations**
- [x] Audit architectural complet
- [x] Phase 1 : Quick Wins P0 (validée le 14/07/2026)
  - `busy_timeout` SQLite, race WS, curseur ROWID, `pipeline.py`
- [x] Phase 2 : Database modulaire (validée le 14/07/2026 — façade 236 lignes, 25 modules après ajout du journal Phase 3)

**Semaine 2 — Découplage**
- [x] Phase 3 : Event bus actif (validée le 14/07/2026 — 10 événements, 3 consommateurs, PWA temps réel)
- [x] Phase 4 : Routeurs FastAPI (validée le 14/07/2026, puis étendue — 19 routeurs montés, `main.py` sous 500 lignes, contrat API verrouillé)
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

- [x] Offline First complet — accès IndexedDB généralisé, lectures dégradées, écritures en file et reprise réseau livrés par la PR #202
- [x] Sync queue — checksum, version d'entité, détection et résolution explicite des conflits livrés par la PR #204
- [x] Health Dashboard (`/health`) — contrat backend unifié, sonde publique `/api/health/live` et vue responsive livrés par la PR #199
- [x] Monitoring opérationnel instantané — métriques santé/voix publiques, rafraîchissement borné et états dégradés/indisponibles validés par la PR #199
- [x] Historique des métriques — séries temporelles, rétention bornée et tendances livrées par la PR #207
- [x] Socle de recherche unifiée backend (FTS5 + embeddings)
- [x] Expérience de recherche unifiée dans le frontend — classement partagé, états offline et navigation vers les résultats livrés par la PR #205
- [x] Authentification biométrique (Face ID / Touch ID) — portes natives macOS et Android livrées par la PR #206

### Ordre recommandé de clôture

1. Terminer et archiver la campagne de release 24 h, les scénarios micro réels et les preuves sur appareils physiques.
2. Attendre la fin de toutes les CI de la pile, puis intégrer dans l'ordre #202, #204, #205, #206, #207, #208, #209, #210, #211 et #212 après feu vert explicite.
3. Après intégration, publier les notes de version et taguer le contrat OpenAPI et le SDK Python `1.0.0`.

### 2027 — Maturité

- [x] Mode multi-utilisateur — profils isolés de bout en bout et contexte explicite livrés par la PR #208
- [x] Chiffrement complet au repos — SQLCipher, migration atomique et politique fail-closed livrés par la PR #209
- [x] Sauvegarde cloud chiffrée — réplication WebDAV chiffrée, vérifiée et sans secret persistant livrée par la PR #210
- [x] API publique documentée (OpenAPI) — contrat OpenAPI 3.1 versionné `1.0.0`, 269 opérations et documentation protégée livrés par la PR #211
- [x] SDK développeurs — client Python `1.0.0` généré depuis le contrat, sans dépendance runtime et avec transport sécurisé livré par la PR #212

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
