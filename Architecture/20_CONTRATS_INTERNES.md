# 20 — Contrats Internes entre Services

**Date** : 11 juillet 2026
**Statut** : Référence — toute modification de service doit respecter ces contrats

---

## Principe

Chaque service expose une **interface publique** clairement documentée. Les autres modules ne doivent **jamais** accéder directement à son implémentation interne. Ce document définit ces interfaces.

## Chaîne de responsabilité

```
AppleDataService → MemoryService → TimelineService → NotificationService → SearchService
                                       │
                                       ▼
                                  AIService → Frontend
```

## AppleDataService

**Responsabilité** : Point d'entrée unique pour toutes les données Apple (chat.db, Contacts, Calendar, Mail).

**Interface publique** :
```python
class AppleDataService:
    # Messages
    def get_new_messages(since_rowid: int) -> list[Message]
    def get_conversation(handle: str, limit: int, since_rowid: int) -> list[Message]
    def search_messages(query: str, limit: int) -> list[Message]
    def get_all_conversation_stats() -> list[ConversationStats]
    
    # Contacts
    def resolve_handle(handle: str) -> str | None
    def get_contacts() -> list[Contact]
    
    # Calendar
    def get_events(start: datetime, end: datetime) -> list[CalendarEvent]
    def create_event(summary: str, start: datetime, end: datetime) -> str
    
    # Mail
    def get_unread_emails(limit: int) -> list[Email]
    
    # Utilitaires
    def apple_epoch_to_datetime(ts: float) -> datetime  # UNE SEULE conversion
    def get_global_rowid() -> int
    def advance_global_rowid(rowid: int) -> None
```

**Interdictions** : Aucun autre module ne doit ouvrir `chat.db`, Contacts, Calendar, ou Mail directement.

## MemoryService

**Responsabilité** : Mémoire épisodique, faits, patterns, life profile, relations.

**Interface publique** :
```python
class MemoryService:
    # Faits
    def add_fact(category: str, content: str, confidence: float, source: str) -> int
    def get_facts(category: str | None) -> list[Fact]
    def supersede_fact(fact_id: int, new_fact_id: int) -> None
    
    # People
    def upsert_person(name: str, relationship: str | None) -> int
    def get_all_people() -> list[Person]
    def upsert_relationship_profile(person_id: int, profile: dict) -> None
    
    # Épisodes
    def save_episode(content: str, summary: str, importance: int, agent: str) -> int
    def get_recent_episodes(limit: int) -> list[Episode]
    
    # Patterns
    def find_or_create_pattern(type: str, description: str) -> int
    def get_active_patterns() -> list[Pattern]
    
    # Contexte de vie
    def add_life_context(type: str, description: str) -> int
    def get_active_life_context() -> list[LifeContext]
    
    # Contexte complet
    def build_full_context() -> dict  # Assemblé pour injection LLM
```

**Interdictions** : Aucun autre module ne doit écrire dans les tables `people`, `facts`, `patterns`, `life_profile`, `episodes`, `relationship_profiles`.

## TaskService

**Responsabilité** : CRUD des tâches.

**Interface publique** :
```python
class TaskService:
    def create_task(title: str, priority: str, due_date: str | None, category: str | None) -> int
    def get_tasks(status: str | None) -> list[Task]
    def update_task(task_id: int, changes: dict) -> bool
    def delete_task(task_id: int) -> bool
```

**Événements émis** : `task.created`, `task.updated`, `task.completed`, `task.deleted`

## NotificationService

**Responsabilité** : Création, stockage, et diffusion des notifications.

**État implémenté le 14/07/2026** : `jarvis.notification_service.notification_service` est l'API unique des producteurs applicatifs et des routes de notifications. Il normalise les priorités, déduplique atomiquement les doublons récents par `source`/`title`/`email_id`, déclenche le Web Push best-effort pour `urgent`/`high` puis émet `notification.created` après le commit. `database.notifications.create_notification()` reste une façade de compatibilité qui délègue au service.

**Interface publique** :
```python
class NotificationService:
    def create(source: str, title: str, content: str | None = None,
               priority: str = "medium", email_id: str | None = None,
               *, deduplication_window_seconds: int | None = 300) -> int
    def get_unread(limit: int = 50) -> list[Notification]
    def get_recent(limit: int = 50) -> list[Notification]
    def mark_read(notification_id: int) -> bool
    def mark_all_read() -> int
```

**Événements émis** : `notification.created`

**Interdiction active** : aucun producteur applicatif ne doit appeler `create_notification()` directement. `tests/test_notification_service.py` impose cette règle sur `agents/` et `scripts/`; `NotificationCreated` demeure un événement de fait émis uniquement après persistance.

## AIService

**Responsabilité** : Point d'entrée unique pour tous les appels aux modèles d'IA.

**Interface publique** :
```python
class AIService:
    def chat(messages: list[dict], model: str | None, max_tokens: int | None) -> ChatResult
    def chat_stream(messages: list[dict], model: str | None) -> AsyncGenerator[str]
    def classify(text: str, categories: list[str]) -> str
    def embed(texts: list[str]) -> list[list[float]]
    def summarize(text: str, max_length: int) -> str
    def get_usage() -> UsageStats
```

**Interdictions** : Aucun module ne doit appeler directement `llm.chat()`, l'API DeepSeek, ou `sentence-transformers`.

## SearchService

**Responsabilité** : Recherche plein-texte et sémantique unifiée.

**Interface publique** :
```python
class SearchService:
    def search(query: str, limit: int, domains: list[str] | None) -> list[SearchResult]
    def index_text(source_type: str, source_id: int, text: str) -> None
    def semantic_search(query: str, limit: int) -> list[SearchResult]
```

## QueueEngine

**Responsabilité** : File de traitement pour les opérations lourdes.

**Interface publique** :
```python
class QueueEngine:
    def enqueue(task_type: str, payload: dict, priority: int) -> str
    def dequeue() -> QueueTask | None
    def mark_completed(task_id: str, result: dict) -> None
    def mark_failed(task_id: str, error: str) -> None
    def get_stats() -> QueueStats
```

## EventBus

**Responsabilité** : Communication inter-modules via événements.

**Interface publique** :
```python
class EventBus:
    def emit(event: JarvisEvent) -> None
    def on(event_type: str, handler: Callable) -> None
    def get_history(limit: int) -> list[JarvisEvent]
```

## Règles des contrats

1. **Respecter l'interface publique** — ne pas contourner un service pour accéder directement à la DB
2. **Ne pas modifier les données d'un autre service** — utiliser les événements
3. **Documenter les changements d'interface** — tout breaking change nécessite un ADR
4. **Tester les contrats** — chaque service doit avoir des tests d'intégration
