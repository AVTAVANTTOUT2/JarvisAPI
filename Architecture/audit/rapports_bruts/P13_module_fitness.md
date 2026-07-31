<!--
source_agent: bc-019fb874-5fe8-7a95-ba92-e371bff39610
agent_name: Module métier fitness
agent_url: https://cursor.com/agents/bc-019fb874-5fe8-7a95-ba92-e371bff39610
agent_status: IDLE
created_at: 2026-07-31T13:54:27.606000+00:00
extracted_msg_index: 165
extracted_at: 2026-07-31T14:37:19.333126+00:00
-->

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
AUDIT MODULE MÉTIER
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ID_PERIMETRE: P13
NOM: Fitness
MODE: lecture seule, ligne par ligne
DATE: 2026-07-31
VERDICT: CONDITIONAL_PASS
SCORE_CHECKLIST: 2/5 OK · 3/5 KO
FINDINGS: 1 CRITIQUE · 4 MAJEURS · 5 MINEURS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

## 1. Identité

| Champ | Valeur |
|---|---|
| ID | P13 |
| Nom | Fitness |
| Montage | `main.py:54` import `app.fitness.routes.router` · `main.py:110` `include_router(fitness_router)` — import + montage seuls, OK |
| Surface HTTP | `APIRouter(prefix="/api/fitness")` — 18 opérations |
| Couches | models → routes → services → `database/fitness.py` · voice · meal_analysis |

## 2. Inventaire (inclus uniquement)

| Fichier | Lignes | Rôle |
|---|---|---|
| `app/fitness/__init__.py` | 1 | docstring |
| `app/fitness/models.py` | 446 | contrats Pydantic `strict=True` / `extra=forbid` |
| `app/fitness/routes.py` | 269 | HTTP |
| `app/fitness/services.py` | 516 | métier + advice LLM + repas IA |
| `app/fitness/meal_analysis.py` | 348 | vision Ollama + macros DeepSeek |
| `app/fitness/voice.py` | 537 | parser vocal déterministe |
| `database/fitness.py` | 724 | persistance SQLite |
| `web/.../FitnessView.tsx` | 620 | UI desktop live |
| `web/.../fitness/FitnessForms.tsx` | 383 | **dead code** |
| `web/.../fitness/FitnessSummary.tsx` | 110 | **dead code** |
| `web/.../fitness/types.ts` | 30 | types morts (Forms uniquement) |
| `frontend/src/lib/api.ts` L224–250 | — | client API fitness |
| `frontend/src/lib/device.ts` L9 | — | segment route `fitness` |
| Aucune page `frontend/src/**/Fitness*` | — | route via `[segment]` + vues `web/` |

Exclus non lus en profondeur (frontières seulement) : `web_mobile/js/views/health.js`, auth.

## 3. Checklist

| # | Item | Verdict | Preuve |
|---|---|---|---|
| 1 | Validation stricte vs UI `""` / `0` | **KO** | Backend strict OK ; UI desktop envoie `Number("") → 0` sur `reminder_interval_min` / `weekly_min_sessions` → 422. `reminder_time=""` échoue le pattern. |
| 2 | Sync `def` vs `async` / threadpool | **KO** | Routes sync `def` = threadpool FastAPI OK. Routes `async` (`/meals/from-text`, `/meals/from-photo`, `/advice`) appellent ensuite du SQLite sync (`create_meal`, `dashboard`) **sur la boucle event loop**. |
| 3 | `except Exception: pass` sur LLM | **KO** | `services.advice` L507–508 : avale toute erreur LLM sans log, repli silencieux. |
| 4 | Isolation schéma fitness | **KO** | Tables programme préfixées `fitness_*` ; logs cœur en noms globaux `meals` / `workouts` / `water_intake` / `wellbeing_logs` (collision / ownership floue). |
| 5 | Dead code FitnessForms vs FitnessView | **OK** | `FitnessForms` / `FitnessSummary` / `types.ts` : zéro import hors leurs fichiers. UI live = `FitnessView` seule. |

## 4. Findings

### CRITIQUE

**F-01 — Avalement total des erreurs LLM advice**  
`app/fitness/services.py:507-508`
```python
except Exception:
    pass
```
Toute panne DeepSeek / import / parse → fallback sans `logger`. Opacité ops, checklist #3.

### MAJEURS

**F-02 — UI réglages → `0` / `""` vs bornes Pydantic**  
`FitnessView.tsx:594-595` : `Number(event.target.value)` sur champ vidé → `0`.  
Contrats : `reminder_interval_min ge=30`, `weekly_min_sessions ge=1`, `reminder_time` pattern `HH:MM`.  
Effet : sauvegarde objectifs/rappels cassée dès clear champ (même bug miroir dans `health.js` hors périmètre — frontière).

**F-03 — Async + SQLite sync sur event loop**  
`routes.py:90-96`, `99-119`, `250-255` → `services.create_meal` / `dashboard` sync.  
Risque de blocage sous charge (photo/vision + write DB).

**F-04 — Isolation schéma partielle**  
`database/migrations.py:_migrate_fitness` : `fitness_programs|sessions|progress|weight_logs|prompt_log` OK ; `meals/workouts/water_intake/wellbeing_logs` non namespacés. Module propriétaire via migration, pas via préfixe.

**F-05 — Calculs progress incohérents**  
`weekly_done_count` compte `fitness_session_progress` **+** `workouts` legacy (`database/fitness.py:443-475`).  
`current_week_streak` ne compte **que** `fitness_session_progress` (`:478-499`).  
Dashboard peut afficher semaine OK et streak 0 (ou l’inverse selon legacy).

### MINEURS

**F-06 — Dead UI legacy** — `FitnessForms.tsx` + `FitnessSummary.tsx` + `types.ts` non montés ; double maintenance latente.  
**F-07 — Stockage photo avalé** — `services.py:249-251` `except Exception:` → `photo_path=None` sans log (analyse OK, photo perdue).  
**F-08 — `ProgramExercise.duration_sec`** — `int | ShortText | None` sans `ge` (`models.py:114`) : durée négative acceptée côté modèle.  
**F-09 — Timezone hardcodée** — `services.py:41` `ZoneInfo("Europe/Paris")` ignore `config.TIMEZONE`.  
**F-10 — Clear notes/description desktop** — `FitnessView` envoie `undefined` (omit) ; mobile envoie `null` (clear). Contrat PATCH asymétrique.

## 5. reminder_* (mission)

| Couche | État |
|---|---|
| `FitnessProgramUpdate.reminder_time` | pattern `^(?:[01]\d\|2[0-3]):[0-5]\d$` — OK |
| `reminder_interval_min` | `ge=30, le=720` — OK |
| SQLite `fitness_programs` | CHECK interval 30–720 ; `reminder_time` TEXT **sans** CHECK format |
| Consommateur | `scripts/fitness_reminders.py` (hors INCLUS) lit programme + `fitness_prompt_log` ; scheduler `to_thread` — frontière OK |
| UI | envoi `0`/`""` casse PATCH — F-02 |

## 6. Conseils LLM / progress / meal AI

| Flux | Comportement |
|---|---|
| `POST /advice` | snapshot dashboard → DeepSeek fast → `FitnessAdvice` ; fallback déterministe si vide/erreur — **erreur masquée** (F-01) |
| Progress | `PUT .../sessions/{id}/progress` upsert UNIQUE `(program_session_id, date)` ; voix `set_scheduled_session_status` sans routes HTTP dédiées |
| Meal text | `async` DeepSeek JSON → normalize → persist |
| Meal photo | Ollama vision → DeepSeek macros → upload `fitness/meals` ; vision `except` → 503 explicite (OK) ; upload générique silencieux (F-07) |

## 7. Frontières

| Frontière | Contrat | Note |
|---|---|---|
| **P15 mobile** `health.js` | Même API : `GET /dashboard`, `PUT /sessions/{id}/progress`, `POST /water|/meals|/weights|/advice`, `PATCH /program`, `PATCH /program/sessions/{id}` ; `api.js` expose aussi `from-text` / `from-photo` / workouts / wellbeing / summary | UI mobile = clone fonctionnel de FitnessView ; même piège `Number("")` settings |
| **P02 auth** | Routes sous `/api/*` → session middleware ; photos `Cache-Control: private, no-store` | Non audité ici |
| Voix | `api/voice_processing.py` → `maybe_handle_fitness_voice` ; endpoints virtuels `/sessions/today/complete\|skip` (pas de routes HTTP) | Hors UI |
| Rappels | `scripts/fitness_reminders.py` + job scheduler | Hors INCLUS, dépend des champs `reminder_*` |
| Frontend Next | Pas de composant fitness dédié ; `api.ts` + allowlist `device.ts` ; page `/fitness` via segment unifié + `web` views | Conforme consigne « uniquement fichiers fitness » |

## 8. Montage `main`

```54:54:main.py
from app.fitness.routes import router as fitness_router
```
```110:110:main.py
app.include_router(fitness_router)
```
Aucun autre couplage métier dans `main.py`. **OK.**

## 9. Synthèse checklist → actions

| Priorité | Action |
|---|---|
| P0 | Remplacer `except Exception: pass` advice par log + fallback explicite |
| P1 | UI : ne pas envoyer `0`/`""` ; omettre champs vides ou valider avant PATCH |
| P1 | Offloader writes SQLite hors loop dans handlers `async` (`asyncio.to_thread`) |
| P2 | Aligner streak sur la même règle que `weekly_done_count` (legacy inclus ou exclu partout) |
| P2 | Prefixe/namespace tables logs **ou** documenter ownership exclusive dans vérité d’archi |
| P3 | Supprimer ou brancher `FitnessForms`/`FitnessSummary` |

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
VERDICT FINAL
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CONDITIONAL_PASS — contrats Pydantic et montage sains ;
3 items checklist KO bloquants pour prod soignée
(advice silencieux, async/SQLite, UI ""/0, schéma partiel).
Dead code Forms confirmé. Frontière mobile = même contrat API.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```