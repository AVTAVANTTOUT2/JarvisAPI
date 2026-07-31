<!--
source_agent: bc-019fb8a5-d4c9-7eba-917a-d7b7d9090f21
agent_name: Tests ci vérité documentaire
agent_url: https://cursor.com/agents/bc-019fb8a5-d4c9-7eba-917a-d7b7d9090f21
agent_status: IDLE
created_at: 2026-07-31T14:48:29.358000+00:00
extracted_msg_index: 149
extracted_at: 2026-07-31T15:02:18.228252+00:00
-->

# AUDIT — P18 — Tests, CI et vérité documentaire

## Métadonnées
- Agent / modèle : Auto (Composer) — auditeur qualité/CI
- Date : 2026-07-31
- Commit audité (`git rev-parse HEAD`) : `2a6e0adc528aed4723d3a0477e17667867945f77`
- Branche : `main` (detached HEAD sur ce SHA)
- Fichiers dans le périmètre (count) : ~140 (117 `tests/**/*.py` + fixtures ; 1 workflow ; docs listées ; `tools/audit_architecture_truth.py` ; artifact ; ~14 `Architecture/*.md` échantillonnés sur counts)
- Fichiers lus (count) : ~45 lus en profondeur + ~20 échantillonnés (rg/line citations)
- Couverture estimée : **~40%** des fichiers tests (priorité contrats/CI/skips) ; **~95%** des claims counts documentaires ciblés

## Synthèse exécutive
Le fingerprint Phase 4 **est cohérent avec l’app réelle** (`232` ops / OpenAPI `206`, signatures OK). En revanche, **CLAUDE.md et une grande partie de `Architecture/` affirment encore des counts obsolètes** (12 routeurs, 207/189 ou 174/157, 76/81 tables, `main.py` 175 lignes, PIN 6, 18 Vitest, 9 Playwright). La source de vérité tables (`Architecture/32` §1 + `tools/audit_architecture_truth.py` live) dit **85/90**, mais le JSON commité est **périmé (80/85)** et le §7–8 du même doc 32 **se contredit** (encore 76/81). Les tests structurels CI macOS **sous-verrouillent** 3 fichiers présents dans le job. Skip silencieux si `frontend/out` absent : la régression CSP Next **ne tourne pas** sur le job backend Ubuntu sans build. Couverture route heuristique : ~71/206 chemins OpenAPI mentionnés dans `tests/`.

## Findings
### F-P18-001
- Sévérité : HIGH
- Type : doc-drift | contrat-cassé
- Titre : CLAUDE.md counts Phase 4 faux vs fingerprint et code
- Preuve : `CLAUDE.md:45-47`
```
montage des 12 `APIRouter`… Les 207 opérations HTTP et le WebSocket `/ws`…
l'OpenAPI expose 189 chemins.
api/router_*.py contient exactement 12 routeurs… aucun ne dépasse 447 lignes.
```
vs runtime vérifié : `EXPECTED_ROUTE_COUNT=232`, `EXPECTED_OPENAPI_PATH_COUNT=206` (`tests/test_phase4_route_contract.py:8-15`) — import `main` → MATCH True ; `ls api/router_*.py` → **15** ; `wc -l main.py` → **217** ; `api/router_location.py` **467**, `api/router_misc.py` **453**.
- Impact : toute PR/agent qui croit CLAUDE régresse vs les tests Phase 4.
- Repro / condition : `python -c` import main + asserts fingerprint (fait sur ce commit).
- Correctif proposé (sans coder) : aligner CLAUDE sur 15 `router_*.py` (+ fitness monté séparément), 232/206, `main.py` 217, plafond lignes réel ou retirer « 447 ».
- Confiance : haute

### F-P18-002
- Sévérité : HIGH
- Type : doc-drift
- Titre : Comptage tables CLAUDE 76/81 faux ; vérité live 85/90
- Preuve : `CLAUDE.md:33` « **76 persistantes**, **81** avec FTS » ; `tests/test_event_bus_integration.py:53-54` `assert len(table_names) == 90` ; `tests/test_audit_architecture_truth.py:178-179` `persistantes_post_init == 85` ; live `audit.analyze_tables` → 85/90.
- Impact : confusion ops/migrations ; doc pointe vers 32 qui dit autre chose au §1.
- Repro / condition : `python tools/audit_architecture_truth.py` (analyse) ou pytest event_bus.
- Correctif proposé : CLAUDE → 85/90 ; cesser de citer 76/81 comme runtime.
- Confiance : haute

### F-P18-003
- Sévérité : HIGH
- Type : doc-drift
- Titre : `artifacts/architecture_truth.json` commité stale (80/85) vs live (85/90)
- Preuve : artifact restauré `persistantes_post_init: 80`, `physiques_max_default_fts_on: 85`, `generated_at: 2026-07-30T23:22:54Z` ; live analyze → 85/90 ; doc 32 §1 L23-24 = 85/90.
- Impact : consommateurs du JSON (CI locale, agents) lisent une vérité fausse.
- Repro / condition : comparer JSON vs `audit.analyze_tables(ROOT)` sans écrire.
- Correctif proposé : régénérer et committer l’artifact ; éventuellement job CI qui échoue si drift.
- Confiance : haute

### F-P18-004
- Sévérité : HIGH
- Type : doc-drift
- Titre : Architecture/32 se contredit (85/90 vs recommandations 76/81)
- Preuve : `Architecture/32_FRONTEND_DATABASE_SOURCE_OF_TRUTH.md:23-24,37-42` (85/90) vs `:344,354,368` (« 76 persistantes / 81 avec FTS », « total 76 / 81 », « test len==81 »).
- Impact : la « source de vérité » n’est pas auto-cohérente.
- Repro / condition : lecture §1 vs §7–8.
- Correctif proposé : réécrire §7–8 et causes sur 85/90 ; retirer références 76/81/len==81.
- Confiance : haute

### F-P18-005
- Sévérité : HIGH
- Type : doc-drift
- Titre : Architecture/28 marque « ✅ » des counts routes/tables faux
- Preuve : `Architecture/28_VALIDATION_COHERENCE.md:19-20,29` — 207+WS/189, 75/80, main 175/12 routeurs, statut ✅.
- Impact : validation de cohérence elle-même mensongère.
- Repro / condition : croiser avec fingerprint 232/206 et analyze 85/90.
- Correctif proposé : invalider le rapport ou le republier avec counts actuels.
- Confiance : haute

### F-P18-006
- Sévérité : MEDIUM
- Type : doc-drift
- Titre : Essaim Architecture/* encore sur 12 routeurs / 174·157 ou 207·189
- Preuve (échantillon) : `Architecture/INDEX.md:87,95,124-125,181` ; `01_CARTOGRAPHIE.md:14-15,730` (mélange 207/189 et 174/157) ; `03_AUDIT_TECHNIQUE.md:12-13` ; `04_ADR.md:119` ; `05_PLAN_MIGRATION.md:161` ; `06_PLAN_TESTS.md:74` ; `07_FEUILLE_DE_ROUTE.md:30` ; `08_ARCHITECTURE_CIBLE.md:22,103` ; `23_TECHNICAL_DEBT.md:16,40` ; `27_RAPPORT_PRET_REFACTORING.md:82` ; `17_DEFINITION_OF_DONE.md:58`.
- Impact : INDEX a tables 85/90 OK mais routes/routeurs faux → vérité partielle trompeuse.
- Repro / condition : `rg '12 routeurs|174 opérations|157 chemins|207 opérations' Architecture`
- Correctif proposé : une passe unique « counts canoniques » pointant fingerprint + audit_architecture_truth.
- Confiance : haute

### F-P18-007
- Sévérité : MEDIUM
- Type : doc-drift
- Titre : CLAUDE validation frontend (18 Vitest / 9 Playwright) faux
- Preuve : `CLAUDE.md:61` « 18 Vitest… 9 scénarios Playwright » ; frontend `it|test(` = **17** ; `test('@` e2e = **8** ; `tests/test_ci_playwright.py:31` `assert source.count("test('@") == 8`. Web `it(` = **50** (celui-ci OK).
- Impact : claim CI/qualité gonflé ; le contrat Playwright verrouille 8, pas 9.
- Repro / condition : compter les fichiers + assert test_ci_playwright.
- Correctif proposé : « 17 Vitest frontend, 8 Playwright @, 50 Vitest web ».
- Confiance : haute

### F-P18-008
- Sévérité : MEDIUM
- Type : doc-drift
- Titre : PIN « 6 chiffres » dans CLAUDE vs code/tests (min 4)
- Preuve : `CLAUDE.md:1516` « PIN 6 chiffres » ; `auth.py:78` `_MIN_PIN_DIGITS = 4` ; `.env.example` « PIN de 4 chiffres » ; `tests/test_web_mobile.py` assert `MIN_PIN = 4`.
- Impact : mauvaise config utilisateur / docs sécurité.
- Repro / condition : setup secret `"1234"` accepté.
- Correctif proposé : CLAUDE → PIN ≥ 4 chiffres.
- Confiance : haute

### F-P18-009
- Sévérité : MEDIUM
- Type : contrat-cassé
- Titre : `test_ci_macos` ne verrouille pas 3 fichiers déjà dans le job CI
- Preuve : `.github/workflows/ci.yml:84-95` inclut `test_imessage_consumer_cursor.py`, `test_imessage_sourcing.py`, `test_no_legacy_audio_provider.py` ; `tests/test_ci_macos.py:29-38` ne les assert pas.
- Impact : un retrait CI de ces fichiers passe le contrat structurel.
- Repro / condition : comparer listes CI vs asserted (fait).
- Correctif proposé : étendre la boucle d’assert aux 10 fichiers du job.
- Confiance : haute

### F-P18-010
- Sévérité : MEDIUM
- Type : bug | smell
- Titre : Skip silencieux si `frontend/out` absent (régression CSP non exécutée en CI backend)
- Preuve : `tests/test_security_middleware.py:388-393` `pytest.skip("frontend/out absent dans ce checkout")` ; job `backend` (`.github/workflows/ci.yml:97-151`) n’build pas Next ; checkout cloud sans `frontend/out`/`web/dist`.
- Impact : garde-fou page noire / CSP `unsafe-inline` Next peut être vert sans jamais s’exécuter sur Ubuntu pytest.
- Repro / condition : pytest ce test sans `frontend/out` → SKIP.
- Correctif proposé : fail (pas skip) si CI ; ou dépendre du artifact `unified_frontend` ; ou marquer `@pytest.mark.requires_frontend_build`.
- Confiance : haute

### F-P18-011
- Sévérité : MEDIUM
- Type : smell
- Titre : Skip dangereux `test_message_insights_table_exists` si DB absente
- Preuve : `tests/test_message_intelligence.py:111-118` skip « DB inexistante » sur `config.DB_PATH` réel, sans `tmp_db`/init.
- Impact : absence de table runtime non détectée en CI fresh.
- Repro / condition : DB_PATH manquant → skip.
- Correctif proposé : créer DB temp + `init_db()` comme les autres tests.
- Confiance : haute

### F-P18-012
- Sévérité : LOW
- Type : smell
- Titre : Sleeps flaky potentiels (délégation Cursor, auth inactivity)
- Preuve : `tests/test_cursor_delegation.py:314` `await asyncio.sleep(3)` ; `tests/test_auth.py:384` `time.sleep(1.1)` ; `tests/test_screen_watcher_control.py:48` sleep(10) dans tâche annulée (moins risqué).
- Impact : ralentissement / flakiness CI sous charge.
- Repro / condition : suite parallèle / machine lente.
- Correctif proposé : événements/polls déterministes ; freezegun / horloge mockée pour sessions.
- Confiance : moyenne

### F-P18-013
- Sévérité : LOW
- Type : doc-drift
- Titre : Docs satellites vides ou anachroniques
- Preuve : `FRONTEND_SPECS.md` taille 0 ; `VOCAL_PIPELINE_ANALYSIS.md` cite `main.py:3767` alors que `main.py` fait 217 lignes ; `RELEASE_CHECKLIST.md:32-40` omet jobs `production_dependencies` et `macos_smoke` pourtant revendiqués dans `CLAUDE.md:62`.
- Impact : onboarding / release incomplets.
- Repro / condition : lecture fichiers.
- Correctif proposé : supprimer/archiver FRONTEND_SPECS ; annoter VOCAL comme historique ; checklist CI = 6 jobs.
- Confiance : haute

### F-P18-014
- Sévérité : INFO
- Type : dette
- Titre : Trou de couverture OpenAPI (heuristique)
- Preuve : 206 chemins OpenAPI ; ~71 mentionnés dans `tests/` ; ~135 non mentionnés (ex. `/api/control/*`, nombreux `/api/devagent/*`, `/api/cursor/*`, `/api/audio-daemon/*`, parties fitness).
- Impact : fingerprint empêche la dérive de signature, pas le comportement.
- Repro / condition : script heuristique path-in-tests (fait).
- Correctif proposé : prioriser contrats TestClient sur control/cursor/devagent/audio-daemon.
- Confiance : moyenne (heuristique chaînes, faux négatifs possibles)

### F-P18-015
- Sévérité : INFO
- Type : doc-drift
- Titre : CLAUDE « 31 cas » web_mobile vs 28 fonctions (~41 paramétrés)
- Preuve : `CLAUDE.md:1832` ; AST `tests/test_web_mobile.py` → 28 `test_*`, ~41 avec parametrize.
- Impact : mineur.
- Correctif proposé : citer collecte pytest exacte.
- Confiance : haute

### F-P18-016
- Sévérité : INFO
- Type : smell
- Titre : Phase 4 architecture compte 15 `router_*.py` mais ignore `fitness_router` monté
- Preuve : `tests/test_phase4_architecture.py:20-23` assert 15 ; `main.py:110-125` **16** `include_router` (fitness + 15).
- Impact : claim « N routeurs de domaine » ambigu (fitness hors `api/router_*.py`).
- Correctif proposé : documenter explicitement fitness comme 16ᵉ router monté.
- Confiance : haute

## Contrats vérifiés
| Contrat / invariant | Statut | Preuve |
|---|---|---|
| `EXPECTED_ROUTE_COUNT` 232 vs app | OK | import main → len(routes)=232, sig MATCH |
| `EXPECTED_OPENAPI_PATH_COUNT` 206 vs app | OK | openapi paths=206, sig MATCH |
| Exactement 15 `api/router_*.py` + APIRouter | OK | test_phase4_architecture + ls |
| `api/*.py` ≤ 500 lignes | OK | aucun module >500 ; routers max 467 |
| `api/` n’importe pas `main` | OK | AST offenders=[] |
| lifespan extrait monté | OK | main FastAPI(lifespan=lifespan) |
| conftest neutralise `_dispatch_*` + workers lifespan | OK | `tests/conftest.py:16-88` |
| Job macOS-14 + brew + requirements-dev | OK | ci.yml + test_ci_macos (partiel) |
| Job macOS liste complète verrouillée | KO | 3 fichiers CI absents des asserts |
| Playwright CI + `test('@` == 8 | OK | test_ci_playwright + e2e |
| Production pip install + smoke imports | OK | test_ci_production_install vs ci.yml |
| pnpm 11.11.0 frozen | OK | test_pnpm_contract + ci.yml |
| CLAUDE 12 routers / 207 / 189 / 76 tables | KO | F-P18-001/002 |
| CLAUDE 18 Vitest / 9 Playwright | KO | F-P18-007 |
| CLAUDE PIN 6 | KO | F-P18-008 |
| artifact JSON = live analyze | KO | 80 vs 85 |
| Architecture/32 interne cohérent | KO | F-P18-004 |
| Skip frontend/out | KO (dangereux) | F-P18-010 |

## Liste « claims faux » (preuve)
| Claim | Où | Vérité mesurée |
|---|---|---|
| 12 `APIRouter` / 12 `router_*.py` | CLAUDE:45-47,623-624 ; INDEX ; ADR ; etc. | **15** `router_*.py` ; **16** include_router (fitness) |
| 207 ops HTTP + WS ; OpenAPI 189 | CLAUDE:45 ; 01_CARTOGRAPHIE:15 ; 28:19 | **231 HTTP + 1 WS = 232** ; OpenAPI **206** |
| 174 ops / 157 OpenAPI | INDEX:87,125 ; 03 ; 04 ; 05 ; 06 ; 17 | idem 232 / 206 |
| aucun router > 447 lignes | CLAUDE:47 | location **467**, misc **453** |
| main.py 175 lignes | CLAUDE:623 ; INDEX ; Phase 4 docs | **217** |
| 76 persistantes / 81 FTS | CLAUDE:33 ; 32§7-8 | **85 / 90** |
| 75/80 tables « ✅ » | Architecture/28:20 | **85 / 90** |
| artifact 80/85 | artifacts/architecture_truth.json | live **85 / 90** |
| 18 Vitest (Next) | CLAUDE:61 | **17** |
| 9 scénarios Playwright | CLAUDE:61 | **8** (`test('@`) |
| PIN 6 chiffres | CLAUDE:1516 | min **4** |
| 31 cas web_mobile | CLAUDE:1832 | 28 fonctions / ~41 param. |
| Architecture/28 « documentation validée » routes | 28:9-19 | counts faux malgré ✅ |

## Frontières / dépendances
- Signale vers **P03** : montage routers / OpenAPI réel (fingerprint OK ici).
- Signale vers **P01** : `main.py` linecount / include_router fitness.
- Signale vers **P02** : politique PIN (`auth._MIN_PIN_DIGITS`).
- Signale vers **P06** : counts tables runtime (85/90).
- Signale vers **P14** : présence `frontend/out` pour tests CSP.
- Attendus consommés ailleurs : fingerprints Phase 4, `architecture_truth.json`, claims CLAUDE comme vérité agents.

## Fichiers non lus
| Fichier | Motif |
|---|---|
| ~70 fichiers `tests/test_*.py` hors contrats/skips/CI | Priorité checklist P18 ; échantillonnés via rg skips/sleeps seulement |
| `tests/fixtures/*` (hors inventaire) | Binaire/fixture audio |
| `STARTUP_PROTOCOL.md` (intégralité) | rg counts négatif ; pas de claim routes/tables |
| `Architecture/LLM_POLICY.md` au-delà L1-63 | Pas de claim counts P18 ; politique LLM OK vs `test_cognitive_routing` (frontière P12) |
| Nombreux `Architecture/0x` hors matches rg | Hors échantillon counts |

## Couverture
Fichiers lus (relativisés, triés) :
- `.github/workflows/ci.yml`
- `Architecture/01_CARTOGRAPHIE.md` (extrait counts)
- `Architecture/03_AUDIT_TECHNIQUE.md` (extrait)
- `Architecture/04_ADR.md` (extrait)
- `Architecture/05_PLAN_MIGRATION.md` (extrait)
- `Architecture/06_PLAN_TESTS.md` (extrait)
- `Architecture/07_FEUILLE_DE_ROUTE.md` (extrait)
- `Architecture/08_ARCHITECTURE_CIBLE.md` (extrait)
- `Architecture/17_DEFINITION_OF_DONE.md` (extrait)
- `Architecture/23_TECHNICAL_DEBT.md` (extrait)
- `Architecture/27_RAPPORT_PRET_REFACTORING.md` (extrait)
- `Architecture/28_VALIDATION_COHERENCE.md`
- `Architecture/32_FRONTEND_DATABASE_SOURCE_OF_TRUTH.md`
- `Architecture/INDEX.md`
- `Architecture/LLM_POLICY.md`
- `Architecture/audit/PROMPTS_AUDIT_LIGNE_PAR_LIGNE.md` (schéma + P18)
- `artifacts/architecture_truth.json`
- `CLAUDE.md` (sections counts/CI/PIN/web_mobile)
- `FRONTEND_SPECS.md`
- `README.md` (stack ; plus de 26+/72 sur ce commit)
- `RELEASE_CHECKLIST.md`
- `VOCAL_PIPELINE_ANALYSIS.md` (échantillon lignes main.py)
- `tools/audit_architecture_truth.py` (via tests + exécution analyze)
- `tests/conftest.py`
- `tests/test_audit_architecture_truth.py`
- `tests/test_auth.py` (sleep + PIN)
- `tests/test_ci_macos.py`
- `tests/test_ci_playwright.py`
- `tests/test_ci_production_install.py`
- `tests/test_cursor_delegation.py` (sleep)
- `tests/test_event_bus_integration.py` (extrait tables)
- `tests/test_message_intelligence.py` (skip DB)
- `tests/test_macos_runtime.py`
- `tests/test_phase4_architecture.py`
- `tests/test_phase4_route_contract.py`
- `tests/test_pnpm_contract.py`
- `tests/test_screen_watcher_control.py` (sleep)
- `tests/test_security_middleware.py` (skip frontend/out)
- `tests/test_web_mobile.py` (AST counts)
- `main.py` (frontière lecture montages routers — non ré-audit P01)
- `auth.py` (frontière PIN digits)
- `frontend/e2e/*.spec.ts` (comptage `test('@`)
- `api/router_*.py` (wc -l uniquement)