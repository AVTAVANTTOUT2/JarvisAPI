# Architecture — Source de vérité officielle de JARVIS API

**Date initiale** : 11 juillet 2026

**Dernière mise à jour** : 3 août 2026
**Périmètre** : 273 fichiers Python (56 261 lignes), 99 fichiers source frontend (18 770 lignes),
90 tables SQLite persistantes après `init_db()` et 95 tables physiques avec FTS5.
Runtime SQLite canonique : **92 tables persistantes**, **97 tables physiques avec FTS5**, schéma généré : **93 déclarations de tables**.
Structure API canonique : **261 opérations HTTP + 2 WebSockets**, **232 chemins OpenAPI**, **17 routeurs api/router_*.py + Fitness = 18 montés**, main.py **214 lignes**.
Voir [32_FRONTEND_DATABASE_SOURCE_OF_TRUTH.md](./32_FRONTEND_DATABASE_SOURCE_OF_TRUTH.md).
**État** : **Documentation officielle — toute modification du code doit rester cohérente avec ce dossier**

---

## RÈGLE ABSOLUE

> **Le dossier `Architecture/` est la source de vérité officielle du projet JARVIS.**
>
> Tout changement d'architecture DOIT obligatoirement mettre à jour :
> - les ADR concernés ;
> - les diagrammes Mermaid ;
> - le plan de migration si nécessaire ;
> - les contrats API si impactés ;
> - les règles de gouvernance.
>
> Le code doit TOUJOURS être cohérent avec cette documentation.
> Une PR modifiant l'architecture sans mettre à jour la documentation sera refusée.

---

## Structure du rapport

| Document | Contenu |
|---|---|
| [00_VISION.md](./00_VISION.md) | Vision long terme, principes non négociables |
| [01_CARTOGRAPHIE.md](./01_CARTOGRAPHIE.md) | Cartographie complète : modules, dépendances, flux de données |
| [02_ANALYSE_PROBLEMES.md](./02_ANALYSE_PROBLEMES.md) | 23 problèmes identifiés avec gravité, origine, conséquences |
| [03_AUDIT_TECHNIQUE.md](./03_AUDIT_TECHNIQUE.md) | Audit backend, frontend, DB, synchronisation, sécurité |
| [04_ADR.md](./04_ADR.md) | 10 Architecture Decision Records core |
| [05_PLAN_MIGRATION.md](./05_PLAN_MIGRATION.md) | Plan de migration en 6 phases, 15 jours |
| [06_PLAN_TESTS.md](./06_PLAN_TESTS.md) | Stratégie de tests, couverture actuelle, zones à améliorer |
| [07_FEUILLE_DE_ROUTE.md](./07_FEUILLE_DE_ROUTE.md) | Priorisation et roadmap technique |
| [08_ARCHITECTURE_CIBLE.md](./08_ARCHITECTURE_CIBLE.md) | Architecture cible post-refactoring (diagrammes Mermaid) |
| [09_DATA_OWNERSHIP.md](./09_DATA_OWNERSHIP.md) | Data Ownership — ADR-011, propriétaires uniques |
| [10_GOUVERNANCE_EVENTS.md](./10_GOUVERNANCE_EVENTS.md) | Gouvernance des événements — ADR-005-bis, catalogue |
| [11_QUEUE_ENGINE.md](./11_QUEUE_ENGINE.md) | Queue Engine — ADR-012, traitements lourds |
| [12_OBSERVABILITE.md](./12_OBSERVABILITE.md) | Observabilité — /health, /metrics, /ready, alertes |
| [13_PLUGINS.md](./13_PLUGINS.md) | Architecture de plugins — ADR-013, interface standard |
| [14_AI_SERVICE.md](./14_AI_SERVICE.md) | AI Service — ADR-014, point d'entrée LLM unique |
| [15_SAUVEGARDES.md](./15_SAUVEGARDES.md) | Stratégie de sauvegardes — ADR-015 |
| [16_CONTRATS_API.md](./16_CONTRATS_API.md) | Contrats API REST + WebSocket, versionnement |
| [17_DEFINITION_OF_DONE.md](./17_DEFINITION_OF_DONE.md) | Definition of Done — critères par phase |
| [18_GOUVERNANCE.md](./18_GOUVERNANCE.md) | Gouvernance — 12 règles d'architecture |
| [19_VALIDATION_FINALE.md](./19_VALIDATION_FINALE.md) | Score de maturité 7.60/10, risques, recommandations |
| [20_CONTRATS_INTERNES.md](./20_CONTRATS_INTERNES.md) | Contrats internes — interfaces entre services |
| [21_DEPENDENCY_RULES.md](./21_DEPENDENCY_RULES.md) | Règles de dépendances autorisées et interdites |
| [22_FITNESS_FUNCTIONS.md](./22_FITNESS_FUNCTIONS.md) | Architecture Fitness Functions — règles CI |
| [23_TECHNICAL_DEBT.md](./23_TECHNICAL_DEBT.md) | Registre canonique des 41 dettes techniques et preuves de résolution |
| [technical_debt_registry.json](./technical_debt_registry.json) | Source structurée validée en CI du registre de dette |
| [24_GOUVERNANCE_ADR.md](./24_GOUVERNANCE_ADR.md) | Gouvernance du cycle de vie des ADR |
| [25_REVUE_ARCHITECTURE.md](./25_REVUE_ARCHITECTURE.md) | Checklist de revue d'architecture |
| [26_SCORE_SANTE.md](./26_SCORE_SANTE.md) | Score de santé — mesure qualité architecture |
| [27_RAPPORT_PRET_REFACTORING.md](./27_RAPPORT_PRET_REFACTORING.md) | Rapport final : prêt pour le refactoring |
| [28_VALIDATION_COHERENCE.md](./28_VALIDATION_COHERENCE.md) | Vérification de cohérence entre documentation et code |
| [29_JARVIS_ANDROID_H24.md](./29_JARVIS_ANDROID_H24.md) | Architecture du compagnon Android permanent |
| [30_PLAN_STABILISATION_AUDIO.md](./30_PLAN_STABILISATION_AUDIO.md) | Phases de stabilisation audio après la PR #17 |
| [32_FRONTEND_DATABASE_SOURCE_OF_TRUTH.md](./32_FRONTEND_DATABASE_SOURCE_OF_TRUTH.md) | **Source de vérité** frontends + surface API + comptages SQLite |
| [api_route_ownership.json](./api_route_ownership.json) | Attribution contrôlée des opérations sans client direct |
| [36_CANAL_WEBSOCKET_TV.md](./36_CANAL_WEBSOCKET_TV.md) | Canal `/ws/tv/events` — authentifié, lecture seule, séparé du chat |
| [adr/](./adr/) | ADR individuels — ADR-016 à ADR-022 |
| [diagrams/](./diagrams/) | Diagrammes Mermaid source |
| [audit/](./audit/) | Rapports d'audit détaillés par domaine |
| [audit/RAPPORT_PIRE_AUDIT.md](./audit/RAPPORT_PIRE_AUDIT.md) | Consolidation sorties agents Cursor (P01–P18) + findings pire→moindre |

---

## Résumé exécutif

### Chiffres clés

```
┌─────────────────────────────────────────────────────────┐
│                     JARVIS API                           │
├─────────────────────────────────────────────────────────┤
│  Backend           │ 273 fichiers Python, 56 261 lignes  │
│  Frontend unifié   │ 14 fichiers, 1 016 lignes           │
│  Vues desktop      │ 38 fichiers, 12 940 lignes          │
│  Vues mobiles      │ 32 fichiers, 4 641 lignes           │
│  SDK auth partagé  │ 4 fichiers, 373 lignes              │
│  Base de données   │ 90 persistantes (+FTS→95), mode WAL │
│  Routes API        │ 259 HTTP + 2 WS, 230 OpenAPI        │
│  WebSocket         │ 1 endpoint, handler dédié           │
│  Agents LLM        │ 7 agents + orchestrateur            │
│  Jobs schedulés    │ 30 (APScheduler)                    │
│  Démons            │ 5 (screen, audio, email, imessage)  │
│  Tests backend     │ 565 pytest, 66 fichiers             │
│  Tests frontend    │ 28 Vitest + 3 Playwright            │
├─────────────────────────────────────────────────────────┤
│  Couche API        │ main.py 211 lignes, 18 routeurs     │
│  Database          │ façade 236 lignes, 25 modules       │
│  Event bus         │ 10 événements, 3 consommateurs      │
│  Frontend          │ 1 bureau + bibliothèque + mobile    │
│  Partage           │ auth, client API, types et vues     │
│                    │ 0 lecteur direct hors AppleDataService│
│                    │ 1 conversion Apple canonique         │
│  Problèmes         │ 4 critiques, 6 majeurs,             │
│                    │ 8 modérés, 5 mineurs                 │
└─────────────────────────────────────────────────────────┘
```

### Architecture actuelle

```mermaid
graph TB
    subgraph "Clients"
        FRONT["Frontend unifié<br/>Next.js 15 + React 19<br/>frontend/out/"]
        VIEWS["Bibliothèque de vues<br/>web/src<br/>non exécutable"]
        MOBILE["Mobile autonome<br/>web_mobile<br/>/mobile/"]
        TV["TV Dashboard<br/>port 5174"]
        IMESSAGE["iPhone<br/>iMessage bridge"]
        AGENT["MacBook Agent<br/>jarvis_agent.py"]
    end

    subgraph "Supervisor (port 9000)"
        SUP["Supervisor 24/7<br/>frontend/out uniquement<br/>proxy WS<br/>auto-restart backend"]
    end

    subgraph "Backend (port 8081)"
        MAIN["main.py — 211 lignes<br/>assemblage FastAPI<br/>18 routeurs montés<br/>2 WebSockets<br/>sert frontend/out en priorité"]
        API["api/<br/>261 opérations HTTP<br/>232 chemins OpenAPI<br/>handlers et support"]
        BUS["Event Bus actif<br/>10 événements de domaine<br/>SSE + WebSocket + TTS"]
    end

    subgraph "Agents"
        ORCH["Orchestrator<br/>(classify + dispatch)"]
        AG["6 agents<br/>info, school, productivity,<br/>coach, journal, memory"]
    end

    subgraph "Database"
        DB[(SQLite WAL<br/>jarvis.db<br/>90 persistantes<br/>+ FTS → 95)]
    end

    subgraph "Données Apple"
        APPLE["AppleDataService<br/>seul accès applicatif read-only"]
        CHATDB[(chat.db macOS<br/>READONLY)]
    end

    subgraph "Scripts"
        SCHED["APScheduler<br/>30 jobs par défaut"]
        DAEMON["JarvisDaemon<br/>screen watcher<br/>TTS, notifications"]
        EMAILW["EmailWatcher<br/>Apple Mail polling"]
        AUDIOD["AudioDaemon<br/>micro, VAD, wake word"]
    end

    FRONT --> SUP
    VIEWS --> FRONT
    MOBILE --> MAIN
    TV --> MAIN
    IMESSAGE --> MAIN
    AGENT --> MAIN
    SUP --> MAIN
    MAIN --> API
    API --> ORCH
    ORCH --> AG
    API --> DB
    DB --> BUS
    BUS --> DB
    BUS --> API
    API --> SCHED
    API --> DAEMON
    API --> EMAILW
    API --> AUDIOD
    MAIN --> APPLE
    DAEMON --> APPLE
    APPLE --> CHATDB
    IMESSAGE -. "Messages.app" .-> CHATDB
```

### Top 5 des problèmes identifiés — état au 14 juillet 2026

| # | Problème | Sévérité initiale | Impact | État |
|---|---|---|---|---|
| 1 | PWA sans écran de verrouillage | CRITIQUE | Données exposées si téléphone déverrouillé | ✅ Résolu — Phase 6 (`jarvis_auth/`) |
| 2 | 3 curseurs ROWID indépendants sur chat.db | CRITIQUE | Messages traités 2-3 fois | ✅ Résolu — Phase 1 |
| 3 | Race condition sur le set WebSocket | CRITIQUE | Crash potentiel (`Set changed size during iteration`) | ✅ Résolu — Phase 1 |
| 4 | SQLite sans `busy_timeout` | CRITIQUE | Écritures silencieusement perdues | ✅ Résolu — Phase 1 |
| 5 | main.py : 7 197 lignes, 40+ responsabilités (état historique) | MAJEURE | Impossible à tester, toute modification risquée | ✅ Résolu — assemblage sous 500 lignes, 18 routeurs montés |

### Plan de migration — 6 phases, 15 jours

```
Semaine 1 │ Phase 1: Quick wins P0 (1j) │ Phase 2: Database modulaire (1j)
Semaine 2 │ Phase 3: Event bus actif (2j, fait) │ Phase 4: Routeurs FastAPI (fait)
Semaine 3 │ Phase 5: Apple Data Service (fait) │ Phase 6: Frontend unifié (fait)
```

Chaque phase est **indépendante**, **réversible**, **testée**, et **sans interruption de service**.

---

## Comment lire ce rapport

**Audit & Diagnostic (01-03)** : Comprendre l'état actuel
- [01_CARTOGRAPHIE.md](./01_CARTOGRAPHIE.md) — structure, dépendances, flux
- [02_ANALYSE_PROBLEMES.md](./02_ANALYSE_PROBLEMES.md) — 23 problèmes classés
- [03_AUDIT_TECHNIQUE.md](./03_AUDIT_TECHNIQUE.md) — audit backend, frontend, DB, sécurité

**Décisions (04, 09-15 + adr/)** : 22 ADR documentés
- [04_ADR.md](./04_ADR.md) — 10 ADR core (résumé)
- [09_DATA_OWNERSHIP.md](./09_DATA_OWNERSHIP.md) — ADR-011 propriétaires de données
- [10_GOUVERNANCE_EVENTS.md](./10_GOUVERNANCE_EVENTS.md) — ADR-005-bis contrats événements
- [11_QUEUE_ENGINE.md](./11_QUEUE_ENGINE.md) — ADR-012 file de traitements
- [12_OBSERVABILITE.md](./12_OBSERVABILITE.md) — ADR implicite monitoring
- [13_PLUGINS.md](./13_PLUGINS.md) — ADR-013 connecteurs externes
- [14_AI_SERVICE.md](./14_AI_SERVICE.md) — ADR-014 point d'entrée LLM unique
- [15_SAUVEGARDES.md](./15_SAUVEGARDES.md) — ADR-015 backup & restore
- [adr/ADR-016](./adr/ADR-016-applescript-integration-apple.md) — AppleScript comme unique intégration Apple
- [adr/ADR-017](./adr/ADR-017-sqlite-base-unique.md) — SQLite comme base de données unique
- [adr/ADR-018](./adr/ADR-018-dual-llm-router.md) — Architecture dual-LLM (local + cloud)
- [adr/ADR-019](./adr/ADR-019-SUPERVISOR-FRONTEND-PRIORITY.md) — Priorité frontend supervisor
- [adr/ADR-020](./adr/ADR-020-android-offline-first-bearer.md) — Fondation Android offline-first
- [adr/ADR-021](./adr/ADR-021-android-offline-location-batch.md) — Synchronisation des positions Android
- [adr/ADR-022](./adr/ADR-022-DATA-AT-REST.md) — Protection des données au repos

**Planification (05-07)** : Exécution
- [05_PLAN_MIGRATION.md](./05_PLAN_MIGRATION.md) — 6 phases, 15 jours
- [06_PLAN_TESTS.md](./06_PLAN_TESTS.md) — stratégie de tests
- [07_FEUILLE_DE_ROUTE.md](./07_FEUILLE_DE_ROUTE.md) — Q3/Q4 2026 → 2027

**Architecture cible & Gouvernance (08, 16-19)** : Vision long terme
- [08_ARCHITECTURE_CIBLE.md](./08_ARCHITECTURE_CIBLE.md) — architecture finale visée
- [16_CONTRATS_API.md](./16_CONTRATS_API.md) — versionnement, pagination, erreurs
- [17_DEFINITION_OF_DONE.md](./17_DEFINITION_OF_DONE.md) — critères de complétion
- [18_GOUVERNANCE.md](./18_GOUVERNANCE.md) — 12 règles d'architecture
- [19_VALIDATION_FINALE.md](./19_VALIDATION_FINALE.md) — score, risques, recommandations

---

## Statut

- [x] 00_VISION.md — vision long terme et principes non négociables
- [x] Audit complet — 01-03 (cartographie, 23 problèmes, audit technique)
- [x] 22 ADR (04, 09-15, 24, adr/ADR-016—022)
- [x] Architecture cible documentée (08)
- [x] Planification (05-07) : migration, tests, roadmap
- [x] Contrats (16, 20) : API REST/WebSocket + interfaces internes
- [x] Gouvernance (00, 17-19, 21-27) : 12 règles, DoD, dépendances, fitness, dette, score, revue
- [x] Score de santé : 7.20/10 après Phase 6 ; la cible 8.5 exige encore observabilité, stabilité 24 h et couverture mesurée
- [x] Rapport final — prêt pour le refactoring (27)
- [ ] Validation par l'utilisateur
- [x] Phases 1 à 6 implémentées et validées sur `main` le 14/07/2026

**Dossier Architecture/ : 35 fichiers Markdown + 3 sous-répertoires — source de vérité officielle du projet**

**Prochaine étape** : solder les dettes voix actives du
[`23_TECHNICAL_DEBT.md`](./23_TECHNICAL_DEBT.md), puis valider les parcours audio,
Android et macOS sur les appareils physiques ciblés.

---

## Processus de modification de l'architecture

```mermaid
flowchart TD
    A[Proposition de changement] --> B{Impact architecture ?}
    B -- Non --> C[Implémentation directe]
    B -- Oui --> D[Rédiger/Mettre à jour ADR]
    D --> E[Mettre à jour contrats & diagrammes]
    E --> F[Vérifier checklist revue]
    F --> G{Passe ?}
    G -- Non --> H[Réviser la proposition]
    H --> D
    G -- Oui --> I[Implémentation]
    I --> J[Mettre à jour dette technique si nécessaire]
    J --> K[Recalculer score de santé]
```

## Convention de nommage

- ADR : `adr/ADR-XXX-titre-court.md` (numérotation séquentielle à 3 chiffres)
- Diagrammes : Mermaid inline dans les documents markdown
- Dates : format ISO 8601 (`YYYY-MM-DD`)
- Documents d'architecture : numérotés `NN_NOM.md` (00-30)

## Responsabilité

Ce dossier est maintenu par le développeur principal.
Tout agent IA (Cursor, Claude, DevAgent) qui modifie l'architecture doit proposer les mises à jour documentaires correspondantes dans le même commit.
