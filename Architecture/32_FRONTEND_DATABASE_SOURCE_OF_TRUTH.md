# 32 — Source de vérité : frontends et base SQLite

**Date** : 15 juillet 2026  
**Méthode** : audit du code exécutable sur `main` (pas de la documentation).  
**Contrôle automatique** : `tools/audit_architecture_truth.py` → `artifacts/architecture_truth.json`

> Ce document **remplace** les affirmations conflictuelles « 44 tables », « 72 tables », « 73 tables »
> et les formulations ambiguës sur le « frontend principal ».  
> En cas de conflit, croire le code (`api/frontend.py`, `database/core.py`, `supervisor.py`) et le rapport JSON.

---

## 1. Résumé exécutif

| Question | Réponse vérifiée |
|---|---|
| Combien de tables après `init_db()` (défaut, FTS5 disponible) ? | **76** entrées `sqlite_master` de type `table` (hors `sqlite_*`) |
| Combien hors objets FTS5 ? | **71** tables persistantes |
| D'où vient « **44** » ? | Dump statique `database/schema.sql` (44 tables applicatives + `sqlite_sequence`) — **non exécuté** par `init_db()` |
| D'où vient « **73** » ? | Inventaire Architecture (juil. 2026) légèrement en retard sur le code actuel (76 avec FTS) |
| Frontend canonique (FastAPI 8081) ? | **`frontend/`** — Next.js **15.5.20**, React **19.2.7** (lockfile), export → `frontend/out/` |
| Fallback racine FastAPI ? | **`web/dist/`** — Vite **6.4.2** + React **19.2.5** |
| PWA historique ? | **`pwa/`** — Next.js **14.2.29**, React **18.3.1**, servie sous **`/m/`** si build présent |
| TV ? | **`tv/`** — FastAPI + vanilla JS, port **5174** (processus séparé) |
| Orphelin ? | **`front_tv/`** — HTML bundlé non référencé |
| Supervisor (9000) sert quoi ? | **`frontend/out` en priorité**, puis **`web/dist`** (même politique que FastAPI — ADR-019) |

**Formulation canonique (à réutiliser partout) :**

```text
Le projet crée 73 tables persistantes après init_db() + migrations
(Vague 2B : location_point_dedup + mobile_chat_dedup ; délégation Cursor :
cursor_delegation_jobs), plus jusqu'à 5 objets FTS5 (messages_fts + 4
auxiliaires) lorsque FTS5 est disponible, soit 78 tables physiques sur une
base neuve avec configuration par défaut. Le dump database/schema.sql
(44 tables applicatives) est un snapshot historique, pas le schéma
d'exécution.

Le frontend canonique est frontend/ (Next.js 15 → frontend/out), servi en
priorité par FastAPI (port 8081) **et** par le supervisor (port 9000).
web/dist reste le fallback actif racine. pwa/out (Next.js 14) est la PWA
historique sous /m/ (absente du checkout si non buildée). tv/ (port 5174)
est le dashboard War Room dédié. Voir ADR-019.
```

---

## 2. Arbre des frontends

| Chemin | Framework | Version déclarée | Version verrouillée | Bundler | Rendu | Dev | Build | Sortie | SW | Manifeste | État |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `frontend/` | Next.js + React | next `15.5.20`, react `^19.2.5` | next `15.5.20`, react `19.2.7` | Next (webpack) | SSG export (`output: 'export'`) | `pnpm dev` | `pnpm build` | `frontend/out/` | `public/sw.js` | `manifest.webmanifest` | **Actif — canonique FastAPI** |
| `web/` | React + Vite | react `^19.0.0`, vite `^6.3.0` | react `19.2.5`, vite `6.4.2` | Vite | SPA CSR | `pnpm dev` (:5173) | `pnpm build` | `web/dist/` | `src/sw.ts` → Workbox | `vite-plugin-pwa` | **Fallback actif** + source vues desktop pour `frontend/` |
| `pwa/` | Next.js + React | next `14.2.29`, react `^18.3.1` | next `14.2.29`, react `18.3.1` | Next | export + `basePath: '/m'` | `npm run dev` | `npm run build` / `scripts/build_pwa.sh` | `pwa/out/` | `public/sw.js` + next-pwa | `manifest.json` | **Fallback historique `/m/`** (build souvent absent) |
| `jarvis_auth/` | React lib | peer `react>=18.3` | N/A | — | package partagé | — | — | — | — | — | **Actif** (SDK LockGate) |
| `tv/` | FastAPI + Jinja2 + vanilla JS | N/A (Python) | N/A | aucun | SSR templates | `python tv/server.py` | aucun | live | non | non | **Actif — TV 5174** |
| `front_tv/` | HTML bundlé | N/A | N/A | externe | fichier unique | — | — | — | — | — | **Orphelin / non référencé** |

### Dépendances structurantes (lockfiles)

| Projet | React | Routing | UI / data | PWA |
|---|---|---|---|---|
| `frontend/` | 19.2.7 | react-router-dom 7.18.1 (+ App Router Next) | TanStack Query 5.101.2, Tailwind 4.3.2, Leaflet, Recharts | SW maison `frontend/public/sw.js` |
| `web/` | 19.2.5 | react-router-dom (lock aligné 7.x) | Tailwind 4.2.4, Recharts, idb | vite-plugin-pwa 1.3.0 + workbox 7.4.x |
| `pwa/` | 18.3.1 | App Router Next 14 | TanStack Query, Leaflet, Tailwind 3.4.19 | next-pwa 5.6.0 |

### Builds présents dans le checkout audité (15/07/2026)

| Dossier | Présent ? |
|---|---|
| `frontend/out/` | oui |
| `web/dist/` | oui |
| `pwa/out/` | **non** |

---

## 3. Ordre réel de résolution frontend

Code : `api/frontend.py` → `_setup_frontend()` appelé depuis `main.py`.

### Requête `/` (backend FastAPI, port 8081)

```text
Requête GET /
→ si PWA_ENABLED et pwa/out/index.html : monte /m/* (indépendant de la suite)
→ si frontend/out/index.html + _next/static/ : sert frontend unifié (PRIORITAIRE) → STOP
→ sinon si web/dist/index.html :
      → si UA mobile ET PWA montée : Redirect 302 → /m/ (ou PWA_URL)
      → sinon sert web/dist SPA
→ sinon si web/templates/index.html : Jinja legacy
→ sinon : warning « Aucun frontend »
```

**Note viewport** : la détection mobile **serveur** (redirection `/` → `/m/`) n’existe que sur le chemin **fallback Vite**. Le frontend unifié Next.js choisit layout desktop/mobile **côté client** (`frontend/src/lib/device.ts` : UA + viewport) — pas de redirect HTTP.

### `/m/`

```text
GET /m/ , /m/{segment}
→ uniquement si PWA_ENABLED et pwa/out présent
→ sinon non monté (404 / route absente)
```

### `/api/*` et `/ws`

```text
Toujours le backend FastAPI (main.py) — jamais les builds frontend.
Auth fail-closed via api/middleware.py (hors allowlist).
```

### Assets

| Préfixe | Source si unifié | Source si fallback Vite |
|---|---|---|
| `/_next/static` | `frontend/out/_next/static` | — |
| `/assets` | — | `web/dist/assets` |
| `/icons` | `frontend/out/icons` ou `web/dist/icons` | idem |
| `/sw.js`, manifeste | build servi | build servi |
| `/static` | `web/static` si présent | idem |

### Supervisor (port 9000)

```text
GET /* sur :9000
→ même priorité desktop que FastAPI (core.frontend_resolution) :
   1. frontend/out (Next)
   2. web/dist (Vite fallback)
   3. JSON frontend_build_missing (503)
→ proxy /api/* et /ws/supervisor inchangés
→ diagnostic : GET /api/supervisor/status → { frontend: {...} }
```

Décision : `Architecture/adr/ADR-019-SUPERVISOR-FRONTEND-PRIORITY.md`.

### TV (port 5174)

```text
GET / sur tv/server.py
→ tv/templates/tv-v2.html + tv/static/
→ processus séparé (supervisor service tv_dashboard)
→ lit SQLite / proxy backend ; ne passe pas par api/frontend.py
```

### Dev

| Service | Port | Rôle |
|---|---|---|
| Next `frontend` | 3000 (défaut next) | HMR développement unifié |
| Vite `web` | 5173 | HMR desktop legacy / vues source |
| PWA `pwa` | 3000 | HMR mobile historique |
| Backend | 8081 | API + prod static |
| Supervisor | 9000 | ops + `web/dist` |
| TV | 5174 | War Room |

---

## 4. Versions réelles

| Package | `frontend/package.json` | `frontend/pnpm-lock.yaml` | Confiance |
|---|---|---|---|
| next | `15.5.20` (exact) | `15.5.20` | haute |
| react / react-dom | `^19.2.5` | `19.2.7` | haute |
| typescript | `^5.9.3` | `5.9.3` | haute |
| tailwindcss | `^4.2.4` | `4.3.2` | haute |

| Package | `web/package.json` | `web/pnpm-lock.yaml` | Confiance |
|---|---|---|---|
| vite | `^6.3.0` | `6.4.2` | haute |
| react | `^19.0.0` | `19.2.5` | haute |
| vite-plugin-pwa | `^1.3.0` | `1.3.0` | haute |
| workbox-* | `^7.4.1` | `7.4.1` | haute |

| Package | `pwa/package.json` | `pwa/package-lock.json` | Confiance |
|---|---|---|---|
| next | `14.2.29` | `14.2.29` | haute |
| react | `^18.3.1` | `18.3.1` | haute |

Aucune contradiction lockfile ↔ package.json majeure (les caret résolvent une version supérieure mineure/patch attendue).

---

## 5. Comptage des tables

### Pipeline d’exécution (source de vérité schéma)

```text
init_db()  [database/core.py]
  1. executescript(SCHEMA)     ← database/schema.py   (47 CREATE TABLE)
  2. run_migrations(conn)      ← database/migrations.py (+17 tables uniques + FTS + DevAgent)
       └─ migrate_devagent_tables()  ← database/devagent.py (6 tables)
```

`database/schema.sql` n’est **pas** lu par `init_db()`.

### Comptages distincts (vérifiés)

| ID | Définition | Nombre |
|---|---|---|
| A | Tables métier / domaines applicatifs (hors miroir iMessage, hors DevAgent, hors infra devops/auth pure) | **≈ 38** (voir §6) |
| B | Tables techniques / infra (auth, settings, quality, logs, devices daemon, schema_migrations…) | **≈ 17** |
| C | Tables miroir iMessage (copie locale) | **9** |
| D | Conditionnelles FTS5 | **5** si FTS5 dispo (`messages_fts` + 4 auxiliaires) ; **0** sinon |
| E | DevAgent | **6** |
| F | Tables de tests (fixtures pytest) | **0** dans la base applicative |
| — | Dump `schema.sql` (snapshot) | **44** applicatives + `sqlite_sequence` |
| — | `schema.py` seul | **47** |
| — | Persistantes post-`init_db` (hors FTS) | **71** |
| — | Physiques post-`init_db` défaut (FTS ON) | **76** |
| — | Référencées / créées par le code d’init | **71 + condition FTS** |

### Réconciliation 44 vs 73 vs 76

| Affirmation | Origine | Verdict |
|---|---|---|
| 44 | Contenu de `database/schema.sql` | Vrai pour ce fichier ; **faux** pour le runtime |
| 72 | Diagramme README | Obsolete |
| 73 | Architecture juil. 2026 | Approximatif ; **dépassé** par le runtime actuel (76) |
| 75 | Audit 15/07/2026 (pré-Vague 2B) | Dépassé ; +1 `location_point_dedup` |
| 76 | `tests/test_event_bus_integration.py` + Vague 2B | **Exact** si FTS5 disponible |

---

## 6. Carte des tables (groupées)

Statuts : `active` | `technique` | `miroir` | `conditionnelle` | `devagent`

### Auth / sessions / mobile / push

| Table | Création | Domaine | Statut | Base neuve défaut |
|---|---|---|---|---|
| `sessions` | migrations.py | auth | technique | oui |
| `mobile_devices` | migrations.py | mobile | technique | oui |
| `mobile_pairing_codes` | migrations.py | mobile | technique | oui |
| `push_subscriptions` | migrations.py | mobile | technique | oui |

### Conversations / messages

| Table | Création | Statut |
|---|---|---|
| `conversations`, `messages`, `conversation_documents` | schema.py | active |
| `conversation_turns` | migrations.py | active |
| `messages_fts` (+ `_config/_data/_docsize/_idx`) | migrations.py FTS5 | conditionnelle |
| `message_insights` | migrations.py | active |
| `llm_action_logs` | schema.py | technique |
| `event_log` | schema.py | technique |

### Mémoire / coach

| Table | Création | Statut |
|---|---|---|
| `episodes`, `life_profile`, `user_facts`, `patterns`, `mood_log`, `life_context`, `cross_insights` | schema.py | active |
| `memory_embeddings` | migrations.py | active |
| `jarvis_journal`, `day_scores`, `mood_signals` | migrations.py | active |

### Contacts / relations

| Table | Création | Statut |
|---|---|---|
| `people`, `people_events`, `relationship_profiles`, `relationship_events`, `imessage_analysis_cache` | schema.py | active |

### Productivité

| Table | Création | Statut |
|---|---|---|
| `tasks`, `email_summaries`, `daily_briefings`, `weekly_summaries`, `notifications` | schema.py | active |
| `commitments`, `daily_rituals` | migrations.py | active |

### École

| Table | Création | Statut |
|---|---|---|
| `school_subjects`, `school_documents`, `school_flashcards` | schema.py | active |

### Localisation

| Table | Création | Statut |
|---|---|---|
| `places`, `location_history`, `visits`, `trips`, `location_patterns` | schema.py | active |
| `location_point_dedup` | migrations.py | technique (idempotence batch GPS Vague 2B) |

### Audio / présence

| Table | Création | Statut |
|---|---|---|
| `recordings`, `voice_debug_log` | schema.py (+ mig voice) | active / technique |
| `presence_sessions` | migrations.py | active |

### Devices / écran

| Table | Création | Statut |
|---|---|---|
| `screen_activity`, `app_usage`, `devices`, `work_sessions` | schema.py | active |
| `agentic_workflows` | schema.py | active |
| `app_settings` | schema.py | technique |

### iMessage miroir

| Table | Création | Statut |
|---|---|---|
| `imessage_handles`, `imessage_chats`, `imessage_chat_handles`, `imessage_messages`, `imessage_attachments`, `imessage_message_attachments`, `imessage_reactions`, `imessage_sync_cursor`, `imessage_consumer_cursors` | schema.py (+ mig idempotente) | miroir |

### Qualité / observabilité / DevAgent

| Table | Création | Statut |
|---|---|---|
| `schema_migrations`, `perf_benchmarks`, `security_findings`, `duplicate_findings` | migrations.py | technique |
| `dev_projects`, `dev_interview_sessions`, `dev_spec`, `dev_loop_state`, `dev_loop_log`, `dev_deployments` | devagent.py | devagent |

---

## 7. Divergences documentaires

| Fichier | Affirmation | Cohérente ? | Action |
|---|---|---|---|
| `README.md` L100 | « 26+ tables » | non | Corriger → formulation multi-comptage |
| `README.md` L124 | « 72 tables » | non | Idem |
| `README.md` / supervisor | « sert le front » sans préciser `web/dist` | ambiguë | Clarifier |
| `Architecture/*` « 73 tables » | inventaire juil. 2026 | **partiellement** (écart vs 76 FTS) | Pointer vers ce document |
| `CLAUDE.md` L32 | « 73e table » = event_log | narratif historique | Nuancer |
| `CLAUDE.md` § PWA L1515 | « web/ SPA principale » | non (Phase 6) | Corriger |
| `database/schema.sql` | dump 44 tables | vrai dump, faux runtime | Annoter en tête (recommandé) |
| `CHANGELOG_HISTORIQUE.md` | web/dist = prod | historique | Conserver sans le traiter comme vérité actuelle |

---

## 8. Recommandation

1. **Citer toujours** les comptages A/B/C/D + total 71 / 76 — jamais un seul chiffre nu.
2. **Frontend** : phrase canonique du §1.
3. **Ne pas** supprimer `web/`, `pwa/`, `front_tv/` ni fusionner TV dans FastAPI sans plan dédié.
4. **Alignement supervisor / FastAPI** : réalisé le 16/07/2026 (ADR-019,
   `core/frontend_resolution.py`). Validation visuelle recommandée sur le port 9000.

### Cause de la divergence (preuves)

| # | Cause | Preuve |
|---|---|---|
| 1 | Documentation obsolète (README 26+/72) | `README.md` L100, L124 |
| 2 | Changement non documenté (tables migrations/FTS/DevAgent) | `migrations.py`, `devagent.py`, test `len==76` |
| 3 | Plusieurs générations frontend encore actives | `api/frontend.py` unifié + Vite + `/m/` |
| 4 | Fallback historique volontaire | commentaires Phase 6 + tests `test_phase6_frontend.py` |
| 5 | Tables conditionnelles / techniques comptées différemment | FTS5 + 73 vs 76 |
| 6 | Snapshot `schema.sql` ≠ schéma runtime | `core.py` importe `SCHEMA` depuis `schema.py` |
| 7 | Supervisor ≠ FastAPI pour le front | `supervisor.py` `DIST_DIR = web/dist` |
| 8 | Build PWA absent du checkout | `pwa/out` manquant le jour de l’audit |

---

*Fin — document généré par audit code-only du 15 juillet 2026.*
