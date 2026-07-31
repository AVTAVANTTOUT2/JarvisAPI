<!--
source_agent: bc-019fb873-7009-7ceb-8ff3-9016e9c23bbe
agent_name: Audit jobs batch
agent_url: https://cursor.com/agents/bc-019fb873-7009-7ceb-8ff3-9016e9c23bbe
agent_status: IDLE
created_at: 2026-07-31T13:53:26.779000+00:00
extracted_msg_index: 103
extracted_at: 2026-07-31T14:37:19.333008+00:00
-->

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━  
**AUDIT P11 — Scripts workers et outillage**  
**Mode :** lecture seule · **Date :** 2026-07-31  
**Verdict global :** **PASS conditionnel** — garde-fous self-healing / backups / shells / isolation tests OK ; écarts majeurs sur flags ENABLED du scheduler, idempotence LLM (location/rituals/relationship), et races email catch-up.  
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## 1. Périmètre

| | |
|---|---|
| **ID** | P11 |
| **Inclus** | `scripts/*.{py,sh}` sauf exclusions · `tools/` |
| **Exclus** | `audio_daemon.py`, `jarvis_daemon.py`, `screen_watcher.py`, `jarvis_agent.py`, `jarvis_launchd.py`, `tv_mcp_server.py`, `launch_tv_browser.sh` (→ P17 / daemons) |
| **Comptage** | **56** scripts + **1** tool = **57** artefacts |

---

## 2. Inventory (tous les fichiers inclus)

### 2.1 `scripts/` — Python (46)

| Fichier | Rôle | LLM | Notifs | Mutation code |
|---|---|---|---|---|
| `__init__.py` | package | — | — | — |
| `backfill_imessages.py` | CLI backfill iMessage | non | non | non |
| `catchup_after_downtime.py` | rattrapage mail + relations | via watchers | via email | non |
| `commitment_consistency.py` | score cohérence promesses | non | non | non |
| `commitments.py` | extract + overdue | fast | oui | non |
| `contact_alerts.py` | silence / non-réponse | non | oui | non |
| `contact_analytics.py` | métriques iMessage | non | non | non |
| `day_scoring.py` | scores journée | non | non | non |
| `db_maintenance.py` | backup Fernet + purge + budget | non | budget | non |
| `db_migrations.py` | migrations SQL | non | non | SQL only |
| `doomscroll_detector.py` | seuil apps | non | oui | non |
| `duplicate_scanner.py` | clones — rapport | non | résumé | non (report-only) |
| `email_watcher.py` | poll Mail → JSON → actions | fast | mac/UI/iMsg | non |
| `export_voice_debug.py` | export debug vocal | non | non | non |
| `favorite_places.py` | lieux délaissés | non | oui | non |
| `fitness_reminders.py` | rappels séance/repas | non | oui | non |
| `force_full_mac_sync.py` | sync Contacts+iMessage | non | non | DB only |
| `imessage_daemon.py` | daemon lecture chat.db | non | non | non |
| `imessage_import.py` | CLI import/doctor | non | non | non |
| `imessage_sync_health_check.py` | healthcheck sync | non | non | non |
| `install_git_hooks.py` | hooks locaux | non | non | hooks |
| `jarvis_journal.py` | journal majordome | main | non | non |
| `local_ci.py` | CI locale | non | non | non |
| `location_analyzer.py` | habitudes GPS | fast | mac+UI | non |
| `meeting.py` | clôture/résumé réunion | oui si capt | — | non |
| `message_predictor.py` | prédiction messages | non | non | non |
| `migrate_devagent.py` | migrate DevAgent | non | non | scoped |
| `perf_regression.py` | perf / rollback hook | non | non | non |
| `presence.py` | présence bureau | non | TTS | non |
| `procrastination_cost.py` | coût procrastination | non | non | non |
| `relationship_analyzer.py` | extract iMessage | fast | non | non |
| `relationship_graph.py` | graphe relations | non | non | non |
| `rituals.py` | roast/debrief/quote/… | fast/main | UI/TTS/iMsg | non |
| `scheduler.py` | APScheduler hub | via jobs | via jobs | non |
| `security_audit.py` | scan secrets | non | high | fix opt-in |
| `self_healing.py` | crash → diag / patch | oui | oui | **opt-in** |
| `self_improvement.py` | preuves → PR Cursor | via Cursor | non | **PR only** |
| `semantic_search.py` | embeddings locaux | non | non | non |
| `sync_contacts.py` | sync Contacts.app | non | non | DB |
| `test_coverage_scan.py` | génère tests | oui | non | **opt-in** |
| `test_kokoro.py` | smoke Kokoro | non | non | non |
| `test_macos_permissions.py` | diag permissions | non | non | non |
| `test_screen_capture.py` | smoke capture | non | non | non |
| `test_voice_pipeline.py` | smoke voice | non | non | non |
| `time_machine.py` | reconstruction jour | non | non | non |
| `timeline_generator.py` | timeline Haiku | fast | non | non |

### 2.2 `scripts/` — Shell (10 inclus)

| Fichier | `set -euo pipefail` | Ancrage chemins |
|---|---|---|
| `android_dev_https.sh` | oui | `cd` racine |
| `android_e2e_pairing.sh` | oui | `cd` racine |
| `generate_ssl.sh` | oui | `ROOT` via `$0` |
| `install_tailscale_cert.sh` | oui | abs via script |
| `jarvis_full_restart.sh` | oui | `cd` racine · WARN `$0` relatif |
| `launch_backend.sh` | oui | `cd` racine |
| `launch_supervisor.sh` | oui | `cd` racine |
| `setup_local_audio.sh` | oui | `cd` racine |
| `sync_android_ca.sh` | oui | `ROOT` |
| `verify_backend_https.sh` | oui | `cd` racine |

### 2.3 `tools/` (1)

| Fichier | Rôle |
|---|---|
| `tools/audit_architecture_truth.py` | audit non destructif → `artifacts/architecture_truth.json` (pas de DB runtime) |

---

## 3. Checklist (obligatoire)

| # | Critère | Statut | Preuve |
|---|---|---|---|
| **1** | `scheduler.py` : chaque job flag `ENABLED` + `try/except` | **FAIL partiel** | Tous les wrappers ont `try/except`. Flags absents ou incomplets pour plusieurs jobs (voir §4). |
| **2** | `email_watcher` : 1er cycle, anti-doublon, JSON fast | **PASS / WARN** | Premier cycle catchup L213–244 ; cache + `email_summaries` ; `llm.chat` fast `max_tokens=200` + `_parse_json`. WARN : races catch-up, `finally` marque traité même si LLM échoue, fan-out multi-canaux. |
| **3** | `SELF_HEALING_AUTO_APPLY` défaut `false` | **PASS** | `config.py:467-468` → `ENABLED=false`, `AUTO_APPLY=false` ; gate L268 `self_healing.py`. |
| **4** | Backups Fernet / permissions 0600 | **PASS** | V2 magic + Fernet ; `BACKUP_ENCRYPTION_ENABLED=true` ; `ensure_private_file` / `write_private_bytes` → `0o600`. |
| **5** | `semantic_search` threads vs isolation tests | **PASS** | Dispatch daemon dans `database/episodes.py:34-52` ; `conftest.py` autouse neutralise `_dispatch_semantic_indexing`. |
| **6** | Scripts shell : `set -e`, chemins | **PASS / WARN** | 10/10 `set -euo pipefail` ; 1 WARN `jarvis_full_restart.sh` (`$0` après `cd`). |

---

## 4. Matrice jobs `scheduler.py`

| Job id | try/except | Flag ENABLED | Notes |
|---|---|---|---|
| `morning_briefing` | ✓ | **aucun** | toujours enregistré ; coût LLM via agent |
| `check_overdue` | ✓ | `DESKTOP_NOTIFICATIONS` (partiel) | pas de notif DB, mac only |
| `fitness_reminders` | ✓ | `FITNESS_REMINDERS_ENABLED` (module) | ✓ |
| `location_analysis` | ✓ | `LOCATION_TRACKING` (module) | ✓ |
| `relationship_alerts` | ✓ | **aucun** | toujours /6h |
| `evening_summary` | ✓ | **aucun** | LLM |
| `weekly_summary` | ✓ | **aucun** | LLM |
| `relationship_analysis_daily` | ✓ | **aucun** | LLM batches |
| `db_backup` | ✓ | `BACKUP_ENABLED` | ✓ |
| `db_maintenance` | ✓ | **aucun** | toujours dim |
| `llm_budget` | ✓ | **aucun** | |
| `daily_roast` | ✓ | `RITUALS_ENABLED` | ✓ |
| `evening_debrief` | ✓ | `RITUALS_ENABLED` | ✓ |
| `daily_quote` | ✓ | `RITUALS_ENABLED` | ✓ |
| `birthday_check` | ✓ | `RITUALS_ENABLED` | ✓ |
| `coffee_break` | ✓ | **aucun** (`RITUALS` non checké) | /20 min 9–22 |
| `weekly_debrief` | ✓ | `RITUALS_ENABLED` | ✓ |
| `mood_signal` | ✓ | **aucun** | |
| `presence_tick` | ✓ | `PRESENCE_ENABLED` (module) | ✓ |
| `streaming_binge` | ✓ | **aucun** dédié | |
| `late_return` | ✓ | `LATE_RETURN_ENABLED` (module) | ✓ |
| `meeting_tick` | ✓ | `MEETING_CAPTURE_ENABLED` (module) | défaut false ✓ |
| `commitments_extract` | ✓ | `RITUALS_ENABLED` | ✓ |
| `commitments_overdue` | ✓ | `RITUALS_ENABLED` | ✓ |
| `duplicate_scan` | ✓ | `DUPLICATE_SCAN_ENABLED` (module) | ✓ |
| `security_audit` | ✓ | `SECURITY_AUDIT_ENABLED` (module) | ✓ |
| `test_gen` | ✓ | `AUTO_TEST_GEN_ENABLED` (module, false) | ✓ |
| `jarvis_journal` | ✓ | `JARVIS_JOURNAL_ENABLED` | ✓ |
| `doomscroll_check` | ✓ | **aucun** | |
| `missed_opportunities` | ✓ | **aucun** | |
| `self_improvement` | ✓ | `SELF_IMPROVEMENT_ENABLED` (défaut **false**) | enregistré seulement si true |

`setup_scheduler()` est idempotent (`replace_existing=True`).

---

## 5. Findings (par sévérité)

### FAIL

| ID | Fichier | Problème |
|---|---|---|
| F1 | `scheduler.py` | Critère « chaque job ENABLED » non tenu : briefing/soir/hebdo/relations/coffee/mood/doomscroll/missed/maintenance sans kill-switch dédié. |
| F2 | `location_analyzer.py` | Fenêtre 30 j rejouée chaque nuit → inserts patterns/faits répétés + coût LLM récurrent + notifs possibles. |
| F3 | `relationship_analyzer.py` | Sur échec LLM/JSON, le curseur ROWID avance quand même → messages perdus définitivement. |
| F4 | `email_watcher.py` + `catchup_after_downtime.py` | Pas de verrou inter-processus : catch-up parallèle au watcher → double LLM + double tâches/mac/iMessage. |
| F5 | `rituals.py` (roast/debrief) | Pas de guard « déjà généré aujourd’hui » avant LLM → rerun = re-coût + overwrite + TTS/notif. |
| F6 | `db_migrations.py` | `executescript` puis `record_migration` hors même transaction ; `DB_MIGRATIONS_AUTO_APPLY=true` par défaut → risque replay partiel. |

### WARN

| ID | Fichier | Problème |
|---|---|---|
| W1 | `email_watcher.py` | `finally` ajoute l’ID même si analyse échoue → pas de retry jusqu’à restart/catch-up. |
| W2 | `email_watcher.py` | Cap 20 non-lus : backlog ancien peut rester bloqué si les 20 plus récents restent non lus. |
| W3 | `email_watcher.py` | Fan-out volontaire mac + UI (+ push high) + iMessage ; dédup service 300 s ne couvre pas mac/iMessage. |
| W4 | `commitments.py` / `jarvis_journal.py` | Reruns = re-coût LLM (journal overwrite upsert date). |
| W5 | `self_improvement.py` | Sans fingerprint, chaque run peut re-proposer / re-déléguer Cursor si activé. |
| W6 | `fitness_reminders.py` | Fenêtre entre notif et `record_prompt` → risque double high-notif sous concurrence. |
| W7 | `contact_alerts.py` | Cutoff `datetime.now()` local vs timestamps UTC SQLite. |
| W8 | `security_audit` / `duplicate_scanner` | Identité positionnelle → faux « nouveaux » findings si lignes bougent. |
| W9 | `jarvis_full_restart.sh` | `$0` relatif fragile après `cd`. |
| W10 | `self_healing.py:221` | `getattr(..., "SELF_REPAIR_ENABLED", True)` — défaut dangereux si attribut absent (mitigé car présent dans `config`, défaut réel `false`). |

### INFO / PASS notables

- **Self-healing** : double opt-in, git-tracked only, `py_compile`, rollback régression, mode Cursor `pr_only` préféré.
- **Security auto-fix** : `SECURITY_AUTO_FIX_ENABLED=false` ; scan scheduler = rapport seul.
- **Auto test gen** : `AUTO_TEST_GEN_ENABLED=false` + dirs vides.
- **Self-improvement** : défaut `false` ; mode `pr_only`.
- **Backup** : chiffrement V2 Fernet + fail path permissions documenté.
- **Semantic** : threads daemon isolés en tests via conftest.
- **Shell** : 100 % `set -euo pipefail` dans le périmètre.

---

## 6. Mission themes — synthèse

| Thème | État |
|---|---|
| Idempotence jobs | Mitigée (email UNIQUE, doomscroll/favorites titres, security INSERT OR IGNORE) ; **faible** sur location/rituals/relationship cursor/self_improvement |
| Coûts LLM | Caps présents (email 200 tok / 20 mails ; commitments 300 ; rituals bornés) ; **fuites** via reruns non gardés |
| Double notifications | Dédup 300 s `(source,title,email_id)` ; mac/iMessage/TTS hors dédup ; catch-up parallèle = risque réel |
| Self-healing opt-in | **Conforme** (ENABLED + AUTO_APPLY false) |
| Backups chiffrés | **Conforme** (Fernet V2, 0600) |
| Scans sécu | Report-only planifié ; fix mécanique opt-in |
| Mutation auto codebase | Guards OK (`SELF_HEALING_AUTO_APPLY`, `SECURITY_AUTO_FIX`, `AUTO_TEST_GEN`, `SELF_IMPROVEMENT` false, Cursor PR) |

---

## 7. Config defaults critiques (réf.)

```
BACKUP_ENABLED=true
BACKUP_ENCRYPTION_ENABLED=true
SELF_HEALING_ENABLED=false
SELF_HEALING_AUTO_APPLY=false
SELF_REPAIR_ENABLED=false
SELF_IMPROVEMENT_ENABLED=false
SELF_MODIFICATION_MODE=pr_only
SECURITY_AUTO_FIX_ENABLED=false
AUTO_TEST_GEN_ENABLED=false
DB_MIGRATIONS_AUTO_APPLY=true   ← attention
MEETING_CAPTURE_ENABLED=false
```

---

## 8. Limites de cet audit

- Lecture statique uniquement (pas d’exécution des jobs, pas de mesure coût réel).
- Daemons exclus (P11) : interactions email_watcher ↔ `jarvis_daemon` hors scope strict, mais le daemon **court-circuite** sa boucle mail si le watcher tourne (hors inventaire).
- `__pycache__` / artefacts générés ignorés.

---

### Recommandations prioritaires (sans implémentation)

1. Ajouter un kill-switch `*_ENABLED` (ou réutiliser `RITUALS_ENABLED`) pour coffee/mood/doomscroll/missed/briefings/relationship jobs.  
2. Idempotence LLM : early-return si entrée du jour déjà présente (rituals, journal, location fingerprints).  
3. Relationship : n’avancer le curseur qu’après parse JSON réussi.  
4. Email : claim atomique DB / lock fichier avant analyse ; ne pas `add` l’ID en `finally` sur échec LLM.  
5. Migrations : une transaction unique SQL + `record_migration`, ou ledger dans le même `conn`.