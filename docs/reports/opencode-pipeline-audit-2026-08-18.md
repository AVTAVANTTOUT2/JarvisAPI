# Audit pipeline OpenCode — 18 août 2026

## Verdict

Le pipeline **n'était pas cassé dans le runner**. Le binaire empaqueté répond. Ce qui cassait l'usage, c'était un diagnostic faux (PATH / `health` à froid) et deux silences applicatifs (runtime désactivé, runtime absent).

Après correction : **ça marche, ou ça échoue explicitement**. Plus de « tâche lancée » fantôme.

Diagnostic live du 18/08/2026 : **WARN** (attendu).

## Étape 1 — Audit

| Contrôle | Résultat |
|---|---|
| Git | `main` alignée sur `origin/main`, arbre propre avant les correctifs |
| `opencode` dans le PATH | **absent** |
| Binaire JARVIS | `integrations/opencode/.runtime/bin/opencode` présent, mode `700`, Mach-O arm64, **1.18.16** |
| `CODE_EXECUTOR_*` | retiré (Open Interpreter). Sans rapport |
| Plugin | `integrations/opencode/plugin.json` `enabled: true`, entrée `integrations.opencode.register:create_runtime` |
| Runner existant | `OpenCodeProcessManager.start()` lance déjà `opencode serve --pure --hostname 127.0.0.1` en subprocess non interactif, timeout, logs privés |

Un second runner n'a **pas** été ajouté : il existait déjà.

## Étape 2 — OpenCode seul

Commande non destructive :

```text
integrations/opencode/.runtime/bin/opencode --version
→ 1.18.16 (exit 0)
```

`--help` écrit sur stderr (TUI), ce n'est pas une panne.

**Fonctionne : oui** (binaire empaqueté). **Fonctionne : non** si on teste seulement `opencode` dans le PATH.

## Étape 3 — Chemin réel et point de rupture

```text
chat / voix / iMessage
  → api/agentic_processing.maybe_start_agentic_run
  → jarvis.cognitive.route_request          (agentic si runtime ≠ disabled)
  → classify_agentic_request                (profil coding si écriture)
  → [AGENTIC_REQUIRE_PLAN_APPROVAL=true]    tâche planifiée, pas de run
  → jarvis.agentic.service.create_and_start
  → registry plugin.json → OpenCodeRuntime
  → lifecycle.process.OpenCodeProcessManager.start
  → subprocess.Popen([binary, serve, --pure, 127.0.0.1, <port>])
  → logs .runtime/logs/server.stdout.log et server.stderr.log
```

Où ça cassait, sans bruit utile :

1. **PATH** — un `opencode` introuvable faisait croire que JARVIS n'avait pas de runtime. JARVIS n'utilise jamais le PATH ; il pointe vers `.runtime/bin/opencode`.
2. **`manager health` à froid** — levait `ProcessManagerError: Serveur OpenCode non démarré`. Le serve partagé n'est **pas** un démon permanent : un processus privé démarre **par run**. L'inactivité est l'état normal.
3. **`AGENTIC_RUNTIME=disabled`** — `maybe_start_agentic_run` retournait `None` avant même la classification. Un `/agent …` retombait sur l'orchestrateur sans dire que le runtime était coupé.
4. **Runtime introuvable** — `resolve_runtime_id()` rendait `None`, `create_run` écrivait `runtime_id="unavailable"`, et l'utilisateur entendait « La tâche est lancée ».

Le vocal n'est pas sur ce chemin (STT/TTS inchangés). Les contrôles vocaux d'un run déjà ouvert passent avant ces gardes.

## Étape 4 — Corrections minimales

| Fichier | Changement |
|---|---|
| `integrations/opencode/lifecycle/process.py` | `health()` à froid → `HealthReport(error_code="not_started")`, plus d'exception |
| `api/agentic_processing.py` | `runtime_disabled` et `runtime_unavailable` renvoyés comme erreur parlée, `action_result.ok=false` |
| `scripts/diagnose_opencode_pipeline.py` | **nouveau** — PASS/WARN/FAIL, probe `--version` borné, aucun secret |
| tests + rapport | couverture des silences, timeout, logs, binaire absent |

Pas de nouveau runner. Pas de `.env` modifié. Pas de refonte. `api/agentic_processing.py` reste sous 500 lignes (492).

## Étape 5 — Script de diagnostic

```bash
python scripts/diagnose_opencode_pipeline.py
```

Sortie JSON + ligne finale `PASS` / `WARN` / `FAIL`. Code de sortie 1 seulement si `FAIL`.

Preuve live (18/08/2026) : **WARN**

- PASS : plugin, `AGENTIC_RUNTIME=auto`, binaire 1.18.16, install, logs, discovery, clé modèle présente (booléen uniquement)
- WARN : PATH vide ; serve partagé inactif ; `AGENTIC_REQUIRE_PLAN_APPROVAL=true`
- Aucune valeur de secret dans la sortie

## Étape 6 — Tests

Lancés :

```text
ruff check <fichiers touchés>                    OK
pytest tests/test_diagnose_opencode_pipeline.py
      tests/test_agentic_processing.py
      integrations/opencode/tests/test_provider_process.py
      tests/test_cognitive_routing.py
      tests/test_phase4_architecture.py
→ 53 passed
```

Couvert :

| Cas | Preuve |
|---|---|
| OpenCode désactivé | `test_disabled_runtime_returns_explicit_error_for_delegated_task`, `test_diagnose_disabled_runtime_is_fail` |
| Binaire absent | `test_probe_reports_missing_binary`, `test_diagnose_missing_binary_is_fail`, `test_start_fails_clearly_when_binary_is_invalid` |
| Succès | `test_probe_reports_success`, `test_diagnose_success_and_idle_serve_are_not_silent` |
| Échec subprocess | `test_start_fails_when_child_exits_immediately`, `test_probe_reports_nonzero_exit` |
| Timeout | `test_probe_reports_timeout`, `test_start_times_out_when_health_never_arrives` |
| Logs écrits | `test_process_start_…` assert `server.stdout.log` / `server.stderr.log` |
| Idle ≠ exception | `test_health_of_idle_runtime_is_not_started_not_an_exception` |
| Vocal / routing | `test_cognitive_routing` (runtime disabled → `answer`, pas de fausse délégation) |

Frontend non relancé : aucun fichier `web/` / `frontend/` touché.

`tests/test_agentic_profiles.py` a été interrompu : il appelle `prepare_turn` (contexte chat) et s'est bloqué hors de ce diff. Comportement antérieur, pas introduit ici.

## Risques restants

- **`AGENTIC_REQUIRE_PLAN_APPROVAL=true` (défaut)** : une demande coding crée un plan, pas un `opencode serve`. OpenCode ne démarre qu'après validation humaine. C'est l'invariant ADR-034, pas une panne.
- **PATH vide** : inoffensif pour JARVIS ; trompeur si on teste le CLI à la main.
- **Sans `DEEPSEEK_API_KEY`** : le run échoue déjà avec le message `_MISSING_DEEPSEEK_KEY_MESSAGE` (pas de repli sur le modèle anonyme `opencode`).
- Le diagnostic ne relance pas un serve : il ne doit pas.

## Action suivante

Pour un run coding réel : valider le plan dans Tâches, ou (déconseillé, ça rouvre l'exécution immédiate) `AGENTIC_REQUIRE_PLAN_APPROVAL=false` dans la config locale — **sans commit de `.env`**.

Pour un `opencode` dans le PATH, un symlink vers le binaire empaqueté suffit. JARVIS n'en a pas besoin.
