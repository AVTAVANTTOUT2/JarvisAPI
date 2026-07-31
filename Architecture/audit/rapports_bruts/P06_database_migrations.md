<!--
source_agent: bc-019fb866-e425-7a71-b0f2-f922ff187ad5
agent_name: Database et migrations
agent_url: https://cursor.com/agents/bc-019fb866-e425-7a71-b0f2-f922ff187ad5
agent_status: IDLE
created_at: 2026-07-31T13:39:38.914000+00:00
extracted_msg_index: 127
extracted_at: 2026-07-31T14:37:19.332690+00:00
-->

# AUDIT P06 — Database et migrations (lecture seule)

**Périmètre :** `database/`  
**Méthode :** inventaire DDL + exécution `SCHEMA` + `run_migrations()` sur `:memory:` + revue ligne-à-ligne des chemins critique (get_db, UPDATE dynamiques, emit, TZ, FTS, FK).  
**Verdict global :** couche runtime saine sur transactions / permissions / FTS ; **failles de défense en profondeur** sur UPDATE dynamiques ; **doc-drift** CLAUDE / en-tête `schema.sql` vs vérité mesurée **85 / 90**.

---

## 1. Source de vérité schéma

| Artefact | Rôle réel | Comptage dérivable |
|---|---|---|
| `database/schema.py` (`SCHEMA`) | **Source d’exécution** via `init_db()` → `executescript(SCHEMA)` | **50** `CREATE TABLE` |
| `database/migrations.py` (`run_migrations`) | Migrations **idempotentes** au boot (+ colonnes, tables, FTS, fitness, auth…) | + tables uniques vs schema.py |
| `database/devagent.py` | Appelé par `_migrate_devagent` dans `run_migrations` | **6** tables |
| `database/schema.sql` | **Snapshot historique**, **non exécuté** (header l’indique) | **46** applicatives (+ `sqlite_sequence`) |
| `database/migrations/*.sql` | Dossier **vide** (README seul) ; versionnées via `scripts/db_migrations.py` (frontière P11) | **0** fichier SQL |

Pipeline canonique :

```text
init_db()
  → executescript(SCHEMA)          # schema.py
  → run_migrations(conn)           # migrations.py (incl. DevAgent + FTS + fitness)
  → harden_sqlite_permissions()
(+ au démarrage app) apply_pending_migrations()  # scripts/ — 0 SQL aujourd’hui
```

**Écarts documentaires (doc-drift) :**

| Source | Affirme | Mesure code (2026-07-31) |
|---|---|---|
| `Architecture/32_…` | 85 persistantes / 90 FTS | **Exact** |
| `tests/test_event_bus_integration.py` | `len == 90` | **Exact** |
| `CLAUDE.md` | **76 / 81** | **Obsolète** |
| Header `schema.sql` | **70 / 75** | **Obsolète** |
| `migrations/README.md` | migrations dans `database/__init__.py` | **Faux** → `migrations.py` |

---

## 2. Comptage tables runtime (dérivé du code)

Exécution : `SCHEMA` + `run_migrations` sur `:memory:`, `PRAGMA foreign_keys=ON`.

| Définition | Count |
|---|---|
| Tables persistantes (hors `messages_fts*`) | **85** |
| Objets FTS5 (`messages_fts` + 4 auxiliaires) | **5** |
| Total physique FTS ON | **90** |
| Total FTS OFF (OperationalError → skip) | **85** |

Liste persistante (85) :

```
agentic_workflows, app_settings, app_usage, auth_rate_limits, commitments,
conversation_documents, conversation_turns, conversations, cross_insights,
cursor_delegation_jobs, daily_briefings, daily_rituals, day_scores,
dev_deployments, dev_interview_sessions, dev_loop_log, dev_loop_state,
dev_projects, dev_spec, device_pairing_attempts, device_pairing_codes, devices,
duplicate_findings, email_summaries, episodes, event_log,
fitness_program_sessions, fitness_programs, fitness_prompt_log,
fitness_session_progress, fitness_weight_logs, imessage_analysis_cache,
imessage_attachments, imessage_chat_handles, imessage_chats,
imessage_consumer_cursors, imessage_handles, imessage_message_attachments,
imessage_messages, imessage_reactions, imessage_sync_cursor, jarvis_journal,
life_context, life_profile, llm_action_logs, location_history, location_patterns,
location_point_dedup, meals, memory_embeddings, message_insights, messages,
mobile_chat_dedup, mobile_devices, mobile_pairing_codes, mood_log, mood_signals,
notifications, patterns, people, people_events, perf_benchmarks, places,
presence_sessions, push_subscriptions, recordings, relationship_events,
relationship_profiles, schema_migrations, school_documents, school_flashcards,
school_subjects, screen_activity, security_findings, sessions, tasks, trips,
user_facts, visits, voice_debug_log, water_intake, weekly_summaries,
wellbeing_logs, work_sessions, workouts
```

---

## CHECKLIST

### ✓/✗ 1. Source de vérité

- **OK runtime :** `schema.py` + `migrations.py`.
- **KO doc :** CLAUDE 76/81 ; header `schema.sql` 70/75 ; README migrations pointe mauvais fichier.
- **OK :** `schema.sql` annoté « NE PAS UTILISER ».

### ✓/✗ 2. `get_db()` — commits, rollback, threads

```59:70:database/core.py
@contextmanager
def get_db() -> Iterator[sqlite3.Connection]:
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
        harden_sqlite_permissions()
```

| Point | Statut |
|---|---|
| Commit en sortie nominale | ✓ |
| Rollback sur exception | ✓ |
| `foreign_keys=ON`, WAL, `busy_timeout=5000` | ✓ |
| Connexion neuve par appel (pas de partage thread) | ✓ (défaut `check_same_thread=True`) |
| Permissions 0600 DB/WAL/SHM + dir 0700 | ✓ via `harden_sqlite_permissions` / `ensure_private_*` |
| Double `conn.commit()` dans `cursor_jobs.py` (l.89,135,163,206) **à l’intérieur** de `get_db` | ⚠ redondant / odeur, pas de double-écriture |
| `get_db` imbriqués (`get_conversation_detail` → `get_conversation_history` ; `find_or_create_pattern` → `update_pattern` / `create_pattern`) | ⚠ 2 connexions, risque contention sous charge |

### ✓/✗ 3. Requêtes paramétrées / injection SQL

**Valeurs :** massivement `?` — OK.  
**Noms de colonnes en f-string :** plusieurs chemins **sans allowlist DB**.

| Site | Allowlist ? | Risque |
|---|---|---|
| `update_place` | ✓ `PLACE_MUTABLE_FIELDS` | Faible |
| `patch_person` | ✓ tuple `allowed` | Faible |
| fitness `update_*` | ✓ sets / `column_map` | Faible |
| `update_cursor_job_fields` | ✓ `allowed` | Faible |
| `update_voice_debug_latency` | colonnes hardcodées | Faible |
| `ALTER meals` migrations | noms littéraux tuple | Nul (DDL fixe) |
| **`update_conversation(**kwargs)`** | ✗ (allowlist seulement API) | Moyen — faille défense en profondeur |
| **`upsert_person(**kwargs)`** | ✗ | **Élevé** — `agents/memory.py` fait `upsert_person(name, **{field: value})` avec `field` issu du LLM |
| **`upsert_relationship_profile(**kwargs)`** | ✗ | Moyen — callers actuels contrôlés, API surface absente |
| `tasks.get_tasks` f-string `ORDER BY` | constante locale | Nul |

**Finding P1 — injection via nom de colonne :**  
`upsert_person` interpolate `kwargs` keys dans SQL. Un `field` LLM du type `x=1 WHERE id=1;--` / `name=?, ai_description=(SELECT…)` casse l’UPDATE ou exfiltre. Même pattern sur `update_conversation` si appelé hors API.

### ✓/✗ 4. `update_*` allowlist colonnes

| Fonction | Allowlist couche DB |
|---|---|
| `update_place` | ✓ |
| `patch_person` | ✓ |
| fitness / cursor_jobs | ✓ |
| `update_conversation` | ✗ |
| `upsert_person` | ✗ |
| `upsert_relationship_profile` | ✗ |

L’API conversations filtre `title|pinned|archived|tags` ; l’API people POST filtre 4 champs — **mais la couche DB reste ouverte**.

### ✓/✗ 5. Emit events après commit

Pattern dominant correct : `with get_db(): …` **puis** `event_bus.emit_nowait(...)`.

| Helper | Emit après commit ? |
|---|---|
| `save_message`, `create_conversation`, `update_conversation` | ✓ |
| `create_task`, `update_task_status` | ✓ |
| `add_fact`, `save_episode`, `create_pattern`, `upsert_person`, `add_life_context` | ✓ |
| `notifications` | pas d’emit direct (délègue service) | N/A |
| `find_or_create_pattern` (branche existante) | emit après `update_pattern` (autre connexion déjà commit) ; outer `get_db` n’a fait qu’un SELECT | ✓ data-wise / ⚠ nesting |

`event_log._persist_event` : handler `on("*")` → nouvel `get_db` après emit — cohérent avec « après commit » des producteurs.

### ✓/✗ 6. `time_buckets` / TIMEZONE

- `time_buckets.py` : `ZoneInfo(config.TIMEZONE)`, bornes UTC, DST 23/25h — **correct**.
- `core.get_usage_stats`, `stats.py` : utilisent `utc_bounds_for_local_dates` — **OK**.
- **Régressions TZ restantes :**
  - `patterns.get_today_messages` : `DATE(created_at) = ?` sur timestamps UTC stockés → **jour local faux** autour de minuit Paris.
  - `rituals.get_week_comparison` : `DATE(completed_at)` + `datetime.now().date()` **sans** `local_datetime` / bornes UTC.
  - `people.close_life_context` : `DATE('now')` = UTC SQLite, pas `TIMEZONE`.

### ✓/✗ 7. Doc-drift vs CLAUDE

CLAUDE affirme **76 / 81** ; runtime mesuré **85 / 90**. Doc canonique à jour : `Architecture/32_FRONTEND_DATABASE_SOURCE_OF_TRUTH.md` + test `len==90`.

### ✓/✗ 8. CASCADE / orphelins / UNIQUE

**CASCADE présents :** `people_events`, `relationship_*` → people ; `conversation_documents` → conversations ; `conversation_turns` → recordings ; fitness sessions/progress.

**NO ACTION / orphelins potentiels :**

| Relation | ON DELETE | Mitigation applicative |
|---|---|---|
| `messages.conversation_id` → conversations | NO ACTION | `delete_conversation` DELETE messages **à la main** ✓ |
| `recordings.conversation_id` | NO ACTION | pas de cleanup dans `delete_conversation` → **orphelins possibles** |
| `agentic_workflows.conversation_id` | NO ACTION | idem |
| `visits` / `trips` / `location_*` → places | NO ACTION | `delete_place` nettoie manuellement ✓ |
| iMessage mirror FKs | NO ACTION | cohérent miroir |
| DevAgent enfants → `dev_projects` | NO ACTION | orphelins si delete projet sans cascade app |

**UNIQUE notables :** `people.name`, `email_summaries.gmail_id`, `event_log.event_id`, devices/mobile tokens, dedup location/mobile chat, fitness `(program_id, position)`, etc. — solides.

**FTS :** external content + 3 triggers + rebuild si désync ; `_fts_query` quote les tokens — **bon**. Fallback LIKE si FTS5 absent.

---

## Findings (sévérité)

**[P1] Allowlist absente sur `upsert_person` — injection colonne via LLM** — `database/people.py:105-121`  
`agents/memory.py` passe `field` LLM dans `**{field: value}`. Interpole directement dans `UPDATE/INSERT`. Corriger : frozenset colonnes mutables comme `patch_person` / `PLACE_MUTABLE_FIELDS`.

**[P1] Même motif sur `update_conversation` / `upsert_relationship_profile`** — défense en profondeur manquante côté DB même si l’API conversations filtre.

**[P2] `DATE(created_at)` / `DATE(completed_at)` sans `time_buckets`** — `patterns.py:87`, `rituals.py:265-269` ; jour local faux vs UTC stocké.

**[P2] Doc-drift comptage tables** — CLAUDE 76/81, header `schema.sql` 70/75 vs **85/90** mesurés.

**[P2] `delete_conversation` ne purge pas `recordings` / `agentic_workflows`** — FK NO ACTION → lignes orphelines.

**[P3] `get_db` imbriqués + double `commit` cursor_jobs** — contention / dette, pas de bug fonctionnel évident en solo-user.

**[P3] `migrations/README.md` obsolète** (pointe `__init__.py`, dossier SQL vide).

---

## Sécurité (OWASP scoped DB)

| Item | Résultat |
|---|---|
| A01 Access control | Hors couche DB (middleware) |
| A02 Secrets / perms fichiers | ✓ 0600/0700 forcés |
| A03 Injection | ✗ UPDATE dynamiques sans allowlist DB |
| A04 Rate limit | N/A ici (`auth_rate_limits` existe) |
| A05 Errors | logs OK, pas de dump schéma runtime |
| A07 Sessions | table `sessions` + UNIQUE `token_hash` |
| A08 Intégrité migrations versionnées | checksum côté `scripts/db_migrations.py` ; runtime idempotent sans checksum de `_migrate_*` |

---

## Synthèse exécutive

La **source de vérité runtime est `schema.py` + `migrations.py`** (pas `schema.sql`). Post-`init_db` : **85 persistantes, 90 avec FTS5**. Transactions/`get_db`, FTS, permissions fichiers et emits post-commit sont globalement corrects. Priorité correctifs : **allowlists colonnes sur tous les UPDATE/INSERT dynamiques** (surtout `upsert_person`), aligner CLAUDE/header SQL sur **85/90**, remplacer les `DATE(...)` restants par `utc_bounds_for_local_dates`, et étendre le cleanup de `delete_conversation`.