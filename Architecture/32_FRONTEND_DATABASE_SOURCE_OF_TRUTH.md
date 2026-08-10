# 32 — Source de vérité : frontends, API et base SQLite

**Date** : 3 août 2026
**Méthode** : audit du code exécutable sur `main` (pas de la documentation).  
**Contrôle automatique** : `tools/audit_architecture_truth.py --check --schema-output database/schema.sql`

Runtime SQLite canonique : **90 tables persistantes**, **95 tables physiques avec FTS5**, schéma généré : **91 déclarations de tables**.

Surface API canonique : **261 opérations**, **232 chemins**, **124 consommées et testées**, **52 consommées sans référence de test**, **37 non-frontend documentées et testées**, **48 non-frontend documentées sans référence de test**, **0 non attribuées**.

Structure API canonique : **259 opérations HTTP + 2 WebSockets**, **230 chemins OpenAPI**, **17 routeurs api/router_*.py + Fitness = 18 montés**, main.py **214 lignes**.

> Ce document **remplace** les affirmations conflictuelles « 44 tables », « 72 tables », « 73 tables »
> et les formulations ambiguës sur le « frontend principal ».  
> En cas de conflit, croire le code (`api/frontend.py`, `api/web_mobile.py`, `database/core.py`, `supervisor.py`) et le rapport JSON.

> **Mise à jour du 31 juillet 2026 — l'arbre `pwa/` et le montage `/m/` n'existent plus.**
> Les téléphones sont redirigés côté serveur vers `/mobile/`, servi depuis `web_mobile/`
> (HTML/CSS/JS vanilla, sans build). Les sections de routage ci-dessous ont été
> réécrites en conséquence ; les inventaires de paquets datés du 24/07 sont
> conservés tels quels et ne décrivent plus l'état courant pour la ligne `pwa/`.
>
> **Mise à jour du 4 août 2026 — le runtime Vite est retiré.** `web/src`
> demeure la bibliothèque de vues compilée par Next.js. Il n'a plus d'entrée,
> de build, de Service Worker, de serveur dev ni de fallback FastAPI/supervisor.

---

## 1. Résumé exécutif

| Question | Réponse vérifiée |
|---|---|
| Combien de tables après `init_db()` (défaut, FTS5 disponible) ? | **95** entrées `sqlite_master` de type `table` (hors `sqlite_*`) |
| Combien hors objets FTS5 ? | **90** tables persistantes |
| Pourquoi `schema.sql` en déclare 91 ? | Il contient les 90 persistantes et `messages_fts`; SQLite crée les 4 tables auxiliaires FTS5 restantes |
| D'où vient « **73** » ? | Inventaire Architecture antérieur au chat mobile, à la délégation Cursor et au pairage desktop sécurisé |
| Frontend canonique (FastAPI 8081) ? | **`frontend/`** — Next.js **15.5.20**, React **19.2.7** (lockfile), export → `frontend/out/` |
| Fallback racine FastAPI ? | **Aucun** — réponse 503 explicite si `frontend/out` manque |
| Interface mobile | **`web_mobile/`** — HTML/CSS/JS vanilla, aucun build, servie sous **`/mobile/`** |
| TV ? | **`tv/`** — FastAPI + vanilla JS, port **5174** (processus séparé) |
| Maquette TV historique | Supprimée du tree runtime le 03/08/2026 ; `tv-v2` est l'unique implémentation |
| Supervisor (9000) sert quoi ? | **`frontend/out` uniquement** (même politique que FastAPI — ADR-019) |
| Surface API ? | **261 opérations / 232 chemins**, inventoriés automatiquement avec leurs consommateurs et tests |

**Formulation canonique (à réutiliser partout) :**

```text
Le projet crée 90 tables persistantes après init_db() + migrations, plus
5 objets FTS5 (messages_fts + 4 auxiliaires) lorsque FTS5 est disponible,
soit 95 tables physiques sur une base neuve avec configuration par défaut.
database/schema.sql est un miroir généré de 91 déclarations : les 90 tables
persistantes et la table virtuelle messages_fts. Il n'est pas exécuté par
init_db(), mais la CI interdit toute divergence avec le runtime frais.

Le frontend bureau unique est frontend/ (Next.js 15 → frontend/out), servi par
FastAPI (port 8081) **et** par le supervisor (port 9000). web/src est sa
bibliothèque de vues, pas une application exécutable. Si frontend/out manque,
le bureau répond 503. web_mobile/ est l'interface mobile autonome servie sous
/mobile/, sans build. tv/ (port 5174) est le dashboard War Room dédié.
Voir ADR-019.
```

---

## 2. Arbre des frontends

| Chemin | Framework | Version déclarée | Version verrouillée | Bundler | Rendu | Dev | Build | Sortie | SW | Manifeste | État |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `frontend/` | Next.js + React | next `15.5.20`, react `^19.2.5` | next `15.5.20`, react `19.2.7` | Next (webpack) | SSG export (`output: 'export'`) | `pnpm dev` | `pnpm build` | `frontend/out/` | `public/sw.js` | `manifest.webmanifest` | **Actif — canonique FastAPI** |
| `web/` | Bibliothèque React | react `^19.0.0` | react `19.2.5` | compilée par Next | composants | — | typecheck/tests seulement | — | — | — | **Source des vues desktop de `frontend/`** |
| `jarvis_auth/` | React lib | peer `react>=18.3` | N/A | — | package partagé | — | — | — | — | — | **Actif** (SDK LockGate) |
| `tv/` | FastAPI + Jinja2 + vanilla JS | N/A (Python) | N/A | aucun | SSR templates | `python tv/server.py` | aucun | live | non | non | **Actif — TV 5174** |

### Dépendances structurantes (lockfiles)

| Projet | React | Routing | UI / data | PWA |
|---|---|---|---|---|
| `frontend/` | 19.2.7 | react-router-dom 7.18.1 (+ App Router Next) | TanStack Query 5.101.2, Tailwind 4.3.2, Leaflet, Recharts | SW maison `frontend/public/sw.js` |
| `web/` | 19.2.5 | react-router-dom (lock aligné 7.x) | Recharts, idb ; compilés par `frontend/` | aucun SW propre |

### Builds présents dans le checkout audité (15/07/2026)

| Dossier | Présent ? |
|---|---|
| `frontend/out/` | oui |

---

## 3. Ordre réel de résolution frontend

Code : `api/frontend.py` → `_setup_frontend()` appelé depuis `main.py`.

### Requête `/` (backend FastAPI, port 8081)

```text
Montage : si web_mobile/index.html : monte /mobile/* (indépendant de la suite)

Requête GET /
→ si UA téléphone ET pas de cookie jarvis_force_desktop : Redirect 302 → /mobile/
→ sinon si frontend/out/index.html + _next/static/ : sert frontend unifié
→ sinon si web_mobile présent : racine mobile-seule (bureau → 503)
→ sinon : warning « Aucun frontend »

`?desktop=1` pose le cookie jarvis_force_desktop (1 an) et sert le bureau.
La redirection ne s'applique qu'à la racine : les liens profonds restent ouvrables.
```

**Note** : la détection est **serveur** et s'applique avant l'unique shell
Next. `frontend/src/lib/device.ts` ne contient plus de détection.

### `/mobile/`

```text
GET /mobile/ , /mobile/{asset:path}
→ uniquement si web_mobile/index.html présent
→ extensions inconnues et traversée de répertoire : 404
→ fichier absent : 404 franc, jamais l'index déguisé (routage par fragment)
```

### `/api/*` et `/ws`

```text
Toujours le backend FastAPI (main.py) — jamais les builds frontend.
Auth fail-closed via api/middleware.py (hors allowlist).
```

### Inventaire route → consommateur → test

`artifacts/architecture_truth.json`, clé `api_surface.routes`, relie chaque
opération FastAPI à son fichier serveur, aux clients qui mentionnent son chemin
et aux tests qui le référencent. L'analyse est statique et n'importe pas
`main.py`; elle couvre aussi les décorateurs avec préfixe `APIRouter`,
`add_api_route(...)` et l'enregistrement fonctionnel du WebSocket `/ws`.

| Surface cliente | Chemins référencés |
|---|---:|
| Next canonique (`frontend/`) | 115 |
| Bibliothèque de vues (`web/src`, incluse dans Next) | 42 |
| Web mobile (`web_mobile/`) | 28 |
| Android | 26 |
| TV | 9 |
| SDK auth partagé | 6 |
| macOS | 15 |

Les références de test couvrent 128 chemins distincts. Les catégories de
l'artefact sont comptées par opération : `consumer_and_tested`,
`consumer_without_path_test`, `owned_non_frontend_and_tested` et
`owned_non_frontend_without_path_test`. Une référence de chemin ne prouve pas à
elle seule la couverture de chaque verbe ou du comportement métier ; les tests
gardent cette responsabilité.

Les 85 opérations sans client direct sont attribuées par le registre versionné
`Architecture/api_route_ownership.json`. Chaque règle donne un propriétaire,
une audience et une justification précises :

| Audience non-frontend | Opérations | Usage |
|---|---:|---|
| `operator` | 74 | Exploitation, diagnostic ou contrôle humain authentifié |
| `device-agent` | 4 | Protocole machine-à-machine des agents desktop |
| `automation` | 5 | Déclencheur manuel de secours d'un job scheduler |
| `indirect-client` | 1 | URL de ressource produite indirectement par une réponse API |
| `integration-client` | 1 | Contrat stable destiné à une intégration sans vue dédiée |

Le contrôle CI exige exactement une règle pour chaque opération sans client,
rejette toute opération non attribuée, toute règle devenue orpheline et toute
règle qui masquerait une route désormais consommée par un client. Le registre
ne sert donc pas d'allowlist générique pour accumuler de nouvelles routes.

Toute modification de route ou de référence client/test rend l'artefact
obsolète et fait échouer la CI. Régénération :

```bash
python tools/audit_architecture_truth.py --schema-output database/schema.sql
```

### Assets

| Préfixe | Source unique |
|---|---|
| `/_next/static` | `frontend/out/_next/static` |
| `/icons` | `frontend/out/icons` |
| `/sw.js`, manifeste | `frontend/out` |

### Supervisor (port 9000)

```text
GET /* sur :9000
→ même contrat desktop que FastAPI (core.frontend_resolution) :
   1. frontend/out (Next)
   2. JSON frontend_build_missing (503)
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
| Backend | 8081 | API + prod static |
| Supervisor | 9000 | ops + `frontend/out` |
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
| react | `^19.0.0` | `19.2.5` | haute |
| typescript | `^5.8.0` | version verrouillée | haute |
| vitest | `^4.1.10` | version verrouillée | haute |
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
  1. executescript(SCHEMA)     ← database/schema.py   (55 CREATE TABLE)
  2. run_migrations(conn)      ← database/migrations.py (+29 tables uniques + FTS + DevAgent)
       └─ migrate_devagent_tables()  ← database/devagent.py (6 tables)
```

`database/schema.sql` n’est **pas** lu par `init_db()`.

### Comptages distincts (vérifiés)

| ID | Définition | Nombre |
|---|---|---|
| A | Tables métier estimées (hors miroir iMessage, DevAgent et infra) | **56** (voir artefact JSON) |
| B | Tables techniques / infra estimées | **19** |
| C | Tables miroir iMessage (copie locale) | **9** |
| D | Conditionnelles FTS5 | **5** si FTS5 dispo (`messages_fts` + 4 auxiliaires) ; **0** sinon |
| E | DevAgent | **6** |
| F | Tables de tests (fixtures pytest) | **0** dans la base applicative |
| — | `schema.sql` généré | **91** déclarations, dont `messages_fts` |
| — | `schema.py` seul | **55** |
| — | Persistantes post-`init_db` (hors FTS) | **90** |
| — | Physiques post-`init_db` défaut (FTS ON) | **95** |
| — | Référencées / créées par le code d’init | **90 + 5 objets FTS** |

### Réconciliation des anciens comptages avec 90 / 95

| Affirmation | Origine | Verdict |
|---|---|---|
| 46 | Ancien snapshot de `database/schema.sql` | Dépassé ; le fichier est maintenant généré |
| 72 | Diagramme README | Obsolete |
| 73 | Architecture juil. 2026 | Dépassé par les migrations mobile/Cursor/device |
| 75 | Total avant le limiteur d'authentification par client | Dépassé |
| 76 | Total avant les extensions auth et Fitness | Dépassé |
| 78 | Audit intermédiaire de juillet 2026 | Dépassé |
| 81 | Audit intermédiaire avant le programme Fitness complet | Dépassé |
| 85 | Total persistant avant les dernières tables runtime | Dépassé |
| 90 | Total persistant actuel, hors objets FTS5 | **Exact** |
| 95 | Total physique actuel avec FTS5 | **Exact** |

---

## 6. Carte des tables (groupées)

Statuts : `active` | `technique` | `miroir` | `conditionnelle` | `devagent`

### Auth / sessions / mobile / push

| Table | Création | Domaine | Statut | Base neuve défaut |
|---|---|---|---|---|
| `sessions` | migrations.py | auth | technique | oui |
| `auth_rate_limits` | migrations.py | auth | technique | oui |
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

### Fitness

| Table | Création | Statut |
|---|---|---|
| `workouts`, `meals`, `water_intake`, `wellbeing_logs` | migrations.py | historique actif |
| `fitness_programs`, `fitness_program_sessions` | migrations.py | programme modifiable |
| `fitness_session_progress`, `fitness_weight_logs`, `fitness_prompt_log` | migrations.py | suivi interactif / relances |

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
| `README.md` / supervisor | « sert le front » sans préciser `frontend/out` | ambiguë | Clarifier |
| `Architecture/*` anciens totaux | inventaires de juillet 2026 | dépassés | Pointer vers ce document |
| `CLAUDE.md` L32 | « 73e table » = event_log | narratif historique | Nuancer |
| `CLAUDE.md` § PWA L1515 | « web/ SPA principale » | non (Phase 6) | Corriger |
| `database/schema.sql` | ancien dump manuel | résolu | Généré depuis une base fraîche et vérifié en CI |
| `CHANGELOG_HISTORIQUE.md` | web/dist = prod | historique | Conserver sans le traiter comme vérité actuelle |

---

## 8. Recommandation

1. **Citer toujours** la formulation canonique 90 persistantes / 95 physiques / 91 déclarations.
2. **Frontend** : phrase canonique du §1.
3. **Conserver** `web/src` comme bibliothèque de vues ; son ancien shell Vite
   ne doit pas être restauré. La maquette `front_tv/` et le template TV legacy
   ont été retirés le 03/08. `pwa/` a été supprimé le 31/07.
4. **Alignement supervisor / FastAPI** : réalisé le 16/07/2026 (ADR-019,
   `core/frontend_resolution.py`). Validation visuelle recommandée sur le port 9000.

### Cause de la divergence (preuves)

| # | Cause | Preuve |
|---|---|---|
| 1 | Documentation obsolète (README 26+/72) | `README.md` L100, L124 |
| 2 | Changement non documenté (tables migrations/FTS/DevAgent) | Résolu par génération et contrôle CI |
| 3 | Plusieurs générations frontend encore actives | Résolu : Next unique, `web/src` bibliothèque seulement |
| 4 | Fallback historique volontaire | Résolu : absence de build → 503 explicite |
| 5 | Tables conditionnelles / techniques comptées différemment | FTS5 : 90 persistantes, 95 physiques |
| 6 | Snapshot `schema.sql` ≠ schéma runtime | Résolu : miroir généré, `init_db()` reste basé sur les sources Python |
| 7 | Supervisor ≠ FastAPI pour le front | Résolu par le résolveur Next unique partagé |
| 8 | Build PWA absent du checkout | `pwa/out` manquant le jour de l’audit — arbre supprimé depuis |

---

*Fin — document généré par audit code-only du 15 juillet 2026.*
