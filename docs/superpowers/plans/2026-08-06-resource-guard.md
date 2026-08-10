# Resource Guard Implementation Plan

> **For agentic workers:** Implement task-by-task. Steps use checkbox syntax.

**Goal:** Watchdog RAM/process JARVIS-only dans le supervisor pour stopper Ollama idle et tuer TTS/daemons orphelins ou en double.

**Architecture:** Module pur `jarvis/resource_guard.py` (décisions testables) branché sur le health-check du supervisor ; réutilise `stop_ollama` et `_kill_orphan_tts_sidecars` / kill tree existants.

**Tech Stack:** Python 3.12, FastAPI supervisor, `memory_pressure` / `ps` macOS, pytest.

## Global Constraints

- Politique A : jamais Codex / Claude Desktop / Chrome / Cursor
- Marqueurs = chemins absolus sous le dépôt JARVIS
- Opt-out via `RESOURCE_GUARD_ENABLED=false` ; `RESOURCE_GUARD_DRY_RUN` pour observer

---

### Task 1: Module `jarvis/resource_guard.py` + tests

- [ ] Écrire tests unitaires (classification, plan actions, dry-run, spare managed)
- [ ] Implémenter inventaire / plan / tick
- [ ] Faire passer les tests

### Task 2: Config + supervisor wiring

- [ ] Ajouter variables dans `config.py` et `.env.example`
- [ ] Tick dans `_health_check_loop` + endpoint `GET /api/supervisor/resources`
- [ ] Tests d’intégration légers (mock tick)

### Task 3: Vérification

- [ ] `pytest tests/test_resource_guard.py tests/test_supervisor_orphan_sidecars.py -q`
- [ ] Smoke inventaire dry-run si possible
