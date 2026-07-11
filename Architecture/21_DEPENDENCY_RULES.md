# 21 — Dependency Rule Book

**Date** : 11 juillet 2026
**Statut** : Règle absolue — toute violation doit être justifiée par un ADR

---

## Règles de dépendances autorisées

Les dépendances suivent une hiérarchie stricte de couches. Une couche supérieure peut dépendre d'une couche inférieure, **jamais l'inverse**.

```
┌──────────────────────────────────────────────┐
│              Frontend (React)                │
│              Peut dépendre de :              │
│              - API Layer                     │
│              - jarvis_auth (SDK)             │
│              - IndexedDB (offline)           │
├──────────────────────────────────────────────┤
│              API Layer (FastAPI)              │
│              Peut dépendre de :              │
│              - Services                      │
│              - Pipeline                      │
│              - jarvis_auth (server)          │
│              NE PEUT PAS contenir de         │
│              logique métier                  │
├──────────────────────────────────────────────┤
│              Services (Domain)                │
│              Peut dépendre de :              │
│              - Repositories (database/)      │
│              - Event Bus                     │
│              - AI Service                    │
├──────────────────────────────────────────────┤
│              Repositories (database/)         │
│              Peut dépendre de :              │
│              - SQLite (uniquement)           │
│              - config.py                     │
│              NE PEUT PAS dépendre de :       │
│              - Services, API, Frontend       │
├──────────────────────────────────────────────┤
│              SQLite (data/)                   │
│              Ne dépend de rien               │
└──────────────────────────────────────────────┘
```

## Dépendances interdites

### Interdiction #1 — Frontend → SQLite
Un composant React ne doit jamais accéder directement à SQLite.
- ❌ `import sqlite3` dans un fichier .tsx
- ✅ Passer par l'API REST ou le service d'abstraction

### Interdiction #2 — Routeur → Logique métier
Un routeur FastAPI ne doit jamais contenir de logique métier.
- ❌ Une fonction dans `api/router_tasks.py` qui fait plus que valider + appeler le service
- ✅ Le routeur appelle `task_service.create_task()`, le service fait le travail

### Interdiction #3 — Service → Service (direct)
Un service ne doit jamais importer un autre service si un événement peut être utilisé.
- ❌ `from services.notification_service import create_notification` dans `task_service.py`
- ✅ `event_bus.emit(TaskCreated(...))` → `NotificationService` écoute et réagit

### Interdiction #4 — chat.db hors AppleDataService
Aucun accès direct à `chat.db` en dehors du service dédié.
- ❌ `sqlite3.connect("...chat.db")` dans n'importe quel fichier sauf `integrations/apple_data.py`
- ✅ `apple_data.get_new_messages(rowid)`

### Interdiction #5 — Appel LLM direct
Aucun module ne doit appeler directement l'API DeepSeek ou Ollama.
- ❌ `llm.chat(messages)` dans un agent ou un script
- ✅ `ai_service.chat(messages, task_type="summarize")`

### Interdiction #6 — Écriture directe dans les tables d'un autre service
- ❌ `INSERT INTO people` dans `agents/coach.py`
- ✅ `event_bus.emit(PersonUpserted(...))` → MemoryService traite

### Interdiction #7 — Dépendance circulaire
- ❌ A → B → A (même via lazy import)
- ✅ Extraire le code partagé dans un module tiers (ex: `pipeline.py`)

### Interdiction #8 — Import de module UI dans le backend
- ❌ `from web.src.components import ...` dans `main.py`
- ✅ Le backend est agnostique du frontend

## Vérification automatique

```bash
# Vérifie les règles de dépendances
python scripts/architecture_check.py --check-deps

# Règles vérifiées :
# - Pas d'import sqlite3 hors database/ et apple_data
# - Pas d'accès chat.db hors apple_data
# - Pas d'appel llm.chat() hors ai_service
# - Pas de cycle (détection statique)
# - Pas d'import UI dans le backend
```

## Exceptions documentées

Toute exception doit être documentée dans un ADR avec :
1. La règle violée
2. La justification
3. La date d'expiration de l'exception (si temporaire)
4. Le plan pour résoudre la violation
