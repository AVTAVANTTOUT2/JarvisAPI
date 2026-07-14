# 19 — Validation Finale

**Date** : 11 juillet 2026
**Statut** : Synthèse avant implémentation

---

## Score global de maturité de l'architecture

| Dimension | Score (/10) | Poids | Pondéré |
|---|---|---|---|
| **Séparation des responsabilités** | 3/10 | 20% | 0.6 |
| **Couplage** | 4/10 | 15% | 0.6 |
| **Cohésion** | 5/10 | 10% | 0.5 |
| **Testabilité** | 4/10 | 15% | 0.6 |
| **Documentation** | 7/10 | 10% | 0.7 |
| **Sécurité** | 7/10 | 15% | 1.05 |
| **Performance** | 6/10 | 10% | 0.6 |
| **Évolutivité** | 4/10 | 5% | 0.2 |
| **TOTAL** | | **100%** | **4.85/10** |

**Interprétation** : L'architecture actuelle est à **4.85/10**. L'objectif après refactoring est **8.5/10**.

### Justification des scores

- **Séparation (3/10)** : 2 god objects (main.py 7194l, database 3284l), 40+ responsabilités dans main.py, 23 domaines dans database. Score très bas.
- **Couplage (4/10)** : cycle main↔daemon supprimé, mais 42 imports dans main.py et 25+ connexions directes à chat.db. Event bus limité à un abonné debug, sans consommateurs métiers.
- **Cohésion (5/10)** : Les agents sont bien séparés, les intégrations aussi. Mais la database et main.py sont des fourre-tout.
- **Testabilité (4/10)** : 534 fonctions de test backend déclarées dans 59 fichiers, mais couverture par route non mesurée de façon fiable.
- **Documentation (7/10)** : CLAUDE.md est excellent (1500+ lignes). README complet. Architecture/ vient d'être créé. Manque des diagrammes de séquence.
- **Sécurité (7/10)** : Auth robuste (scrypt, sessions, anti-brute-force). CSP, CORS, CSRF configurés. Mais PWA sans LockGate, pas de chiffrement au repos, HTTP par défaut.
- **Performance (6/10)** : SQLite WAL, `busy_timeout = 5000` et batch import. Le cache LLM et le monitoring restent à implémenter.
- **Évolutivité (4/10)** : Difficile d'ajouter un nouveau connecteur (pas d'interface plugin). Deux frontends à maintenir. Refactoring lourd pour toute nouvelle feature transverse.

## Principaux risques restants

| # | Risque | Probabilité | Impact | Mitigation |
|---|---|---|---|---|
| 1 | PWA sans LockGate — accès non autorisé aux données | Moyenne | Élevé | Phase 6 (SDK auth) |
| 2 | SQLite sans busy_timeout — perte silencieuse d'écriture | Faible | Élevé | ✅ Résolu Phase 1 (`PRAGMA busy_timeout = 5000`, 11/07/2026) |
| 3 | Race condition WebSocket — crash broadcast | Faible | Moyen | ✅ Résolu Phase 1 (verrou + snapshot défensif, 11/07/2026) |
| 4 | Messages iMessage traités en double | Moyenne | Faible | ✅ Résolu Phase 1 (curseur unique `imessage_consumer_cursors`, 11/07/2026) |
| 5 | Conflits de merge sur main.py et database/__init__.py | Élevée | Moyen | Phase 2 + 4 (split) |
| 6 | Dette technique croissante | Élevée | Élevé | Tout le plan de refactoring |
| 7 | Couverture frontend limitée (18 tests web, 0 PWA) | Élevée | Moyen | Phase 6 (plan de tests) |
| 8 | 25+ connexions à chat.db — contention | Faible | Faible | Phase 5 (AppleDataService) |

## Dépendances critiques

| Dépendance | Type | Risque si indisponible |
|---|---|---|
| **DeepSeek API** | Externe (cloud) | **CRITIQUE** — JARVIS ne peut plus répondre |
| **Ollama** | Local (optionnel) | Vision locale désactivée, fallback texte |
| **chat.db (macOS)** | Local | iMessage bridge/sourcing inopérant |
| **SQLite (jarvis.db)** | Local | **CRITIQUE** — toutes les données inaccessibles |
| **ElevenLabs API** | Externe (cloud) | STT/TTS dégradé (fallback: faster-whisper local + Edge TTS) |
| **OpenWeatherMap** | Externe (cloud) | Météo indisponible (non critique) |
| **Tavily** | Externe (cloud) | Recherche web indisponible (non critique) |

## Blocages avant le début du refactoring

| # | Blocage | Résolution |
|---|---|---|
| 1 | **Couverture frontend limitée** | 18 tests Vitest couvrent l'offline web ; les composants et la PWA restent à couvrir en Phase 6. |
| 2 | **CI automatisée — ✅ STABILISÉE le 14/07/2026** | GitHub Actions run #24 : 139 modules importés, 536 tests backend passants, 1 ignoré, frontend Vitest/typecheck/build vert. Le sous-ensemble de dépendances CI doit rester aligné sur les imports applicatifs. |
| 3 | **Dépendance au cookie jarvis_session pour l'auth PWA** | La Phase 6 créera le SDK auth partagé. En attendant, la PWA partage le cookie (même origine HTTP). |
| 4 | **Manque de monitoring** | Le `/health` endpoint sera ajouté en Phase 3 (avec l'Event Bus). Pas bloquant pour commencer. |

## Recommandations avant le début du refactoring

1. ~~**Commencer IMMÉDIATEMENT la Phase 1** (Quick Wins P0 — 1 jour). Les 4 corrections sont simples, sans risque, et corrigent des problèmes critiques. Aucune raison d'attendre.~~ **FAIT** — Phase 1 validée le 14/07/2026.

2. **Ne pas attendre la fin du refactoring pour bénéficier des améliorations**. Chaque phase est indépendante et apporte de la valeur immédiatement : Phase 2 rend le code plus lisible, Phase 3 rend l'UI réactive, etc.

3. **Prioriser la stabilité sur la rapidité**. Mieux vaut 15 jours de refactoring sans régression que 5 jours avec des bugs.

4. **Documenter chaque phase dans le CHANGELOG**. Le README et CLAUDE.md doivent refléter l'état réel du code.

5. **Ne pas introduire de nouvelles features pendant le refactoring**. La règle d'or s'applique : corriger les fondations avant d'ajouter des étages.

## Prochaine action

**Phase 1 — Quick Wins P0 validée le 14/07/2026. Prochaine action : finaliser la Phase 2 (Database modulaire).**

```
Phase 1 : 1 jour
├── busy_timeout SQLite (5 min)
├── Race condition WebSocket (15 min)
├── Curseur ROWID unique (2 heures)
└── pipeline.py (4 heures)
```
