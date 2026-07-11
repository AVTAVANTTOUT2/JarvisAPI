# 14 — AI Service (ADR-014)

**Date** : 11 juillet 2026
**ADR** : ADR-014
**Statut** : Proposé

---

## Problème

Actuellement, les appels aux LLM sont dispersés dans tout le code :
- `llm.py` est utilisé directement par les agents, l'orchestrateur, et les scripts
- Chaque agent gère son propre prompt et son propre appel API
- Pas de cache centralisé, pas de métriques unifiées, pas de routage intelligent entre modèles
- Les embeddings (`sentence-transformers`) sont gérés séparément

## Solution

Créer un **AI Service** — point d'entrée unique pour toutes les interactions avec les modèles d'IA.

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         AI SERVICE                               │
├─────────────────────────────────────────────────────────────────┤
│                                                                   │
│  ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────────┐ │
│  │  Router  │   │  Cache   │   │ Embedding│   │ Token Counter│ │
│  │  Model   │   │  Prompt  │   │  Engine  │   │  & Cost      │ │
│  └────┬─────┘   └────┬─────┘   └────┬─────┘   └──────┬───────┘ │
│       │              │              │                 │          │
│  ┌────▼──────────────▼──────────────▼─────────────────▼───────┐ │
│  │                      LLM Clients                            │ │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────────────────────┐  │ │
│  │  │ DeepSeek │  │ Ollama   │  │ sentence-transformers    │  │ │
│  │  │ (cloud)  │  │ (local)  │  │ (local embeddings)      │  │ │
│  │  └──────────┘  └──────────┘  └──────────────────────────┘  │ │
│  └────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
```

## API du service

```python
class AIService:
    # ── Chat / Completion ──
    async def chat(
        self,
        messages: list[dict],
        model: str | None = None,
        max_tokens: int | None = None,
        temperature: float = 0.7,
        stream: bool = False,
    ) -> ChatResult: ...

    async def chat_stream(
        self,
        messages: list[dict],
        model: str | None = None,
        max_tokens: int | None = None,
    ) -> AsyncGenerator[str, None]: ...

    # ── Classification (modèle rapide) ──
    async def classify(
        self,
        text: str,
        categories: list[str],
    ) -> str: ...

    # ── Embeddings ──
    async def embed(
        self,
        texts: list[str],
        model: str = "all-MiniLM-L6-v2",
    ) -> list[list[float]]: ...

    # ── Résumé ──
    async def summarize(
        self,
        text: str,
        max_length: int = 200,
    ) -> str: ...

    # ── Cache ──
    def cache_get(self, key: str) -> str | None: ...
    def cache_set(self, key: str, value: str, ttl: int = 3600) -> None: ...

    # ── Métriques ──
    def get_usage(self) -> UsageStats: ...
```

## Routage entre modèles

```python
class ModelRouter:
    """Route automatiquement vers le bon modèle selon la tâche."""

    ROUTES = {
        "classify": "deepseek-v4-flash",       # Rapide, pas cher
        "summarize_short": "deepseek-v4-flash", # Rapide
        "summarize_long": "deepseek-v4-pro",    # Contexte long
        "chat": "deepseek-v4-pro",              # Qualité
        "heavy_task": "deepseek-v4-pro",        # Production longue
        "vision": "qwen2.5-vl:7b",             # Local, gratuit
        "triage": "qwen2.5:7b",                # Local, gratuit
        "embedding": "all-MiniLM-L6-v2",        # Local
    }

    def route(self, task_type: str) -> str:
        return self.ROUTES.get(task_type, "deepseek-v4-pro")
```

## Cache

Le cache évite les appels redondants. Exemples :
- Même question posée deux fois
- Résumé d'un email déjà analysé
- Embedding d'un texte déjà indexé

```sql
CREATE TABLE IF NOT EXISTS ai_cache (
    cache_key TEXT PRIMARY KEY,  -- SHA256(system_prompt + user_message)
    response TEXT NOT NULL,
    model TEXT NOT NULL,
    tokens_in INTEGER,
    tokens_out INTEGER,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    hit_count INTEGER DEFAULT 1
);
CREATE INDEX idx_ai_cache_expires ON ai_cache(expires_at);
```

## Métriques exposées

```python
@dataclass
class UsageStats:
    calls_total: int
    tokens_in_total: int
    tokens_out_total: int
    cost_total: float
    cache_hits: int
    cache_misses: int
    avg_latency_ms: float
    calls_by_model: dict[str, int]
    calls_by_task: dict[str, int]
```

## Règle absolue

**Aucun autre module n'appelle directement `llm.chat()` ou `httpx` vers l'API DeepSeek.** Tout passe par `ai_service`.
