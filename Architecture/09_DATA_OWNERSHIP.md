# 09 — Data Ownership (ADR-011)

**Date** : 11 juillet 2026
**ADR** : ADR-011
**Statut** : Partiellement appliqué — émissions post-commit activées en Phase 3, propriétaires de services encore à consolider

---

## Problème

Actuellement, plusieurs modules écrivent dans les mêmes tables sans coordination :
- `upsert_person()` appelé depuis 7 fichiers
- `add_fact()` appelé depuis 5 fichiers
- `create_notification()` appelé directement depuis 15 fichiers après la Phase 3

Ces écritures concurrentes créent un risque d'incohérence et violent le principe de source unique de vérité.

## Solution

Chaque donnée du système a **un propriétaire unique**. Seul le propriétaire peut écrire. Les autres composants peuvent lire via l'API publique du propriétaire, ou réagir aux événements émis par le propriétaire via l'Event Bus.

## Tableau de propriété des données

| Donnée | Propriétaire | Peut écrire | Peut lire | Ne doit JAMAIS modifier |
|---|---|---|---|---|
| **Messages iMessage bruts** | AppleDataService | Lecture seule via `integrations/apple_data.py` | Tous via sa façade | Tout autre module |
| **Contacts (people)** | À consolider | `contacts_reader` + synchronisation existante | Tous | agents/memory.py, agents/coach.py, agents/journal.py |
| **Calendrier** | À consolider | Intégration Calendar existante | Tous | Tout autre module |
| **Emails** | À consolider | Intégration Mail existante | Tous | Tout autre module |
| **Faits utilisateur** | Memory Service | MemoryService | Tous (via API) | agents/coach.py, audio/continuous_recorder.py, scripts/relationship_analyzer.py |
| **Patterns** | Memory Service | MemoryService | Tous (via API) | Tout autre module |
| **Life Profile** | Memory Service | MemoryService | Tous (via API) | Tout autre module |
| **Épisodes** | Memory Service | MemoryService | Tous (via API) | Tout autre module |
| **Relationships** | Memory Service | MemoryService | Tous (via API) | scripts/relationship_analyzer.py |
| **Tâches** | Task Service | TaskService | Tous (via API) | Tout autre module |
| **Journal** | Journal Service | JournalService | Tous (via API) | Tout autre module |
| **Notifications** | Notification Service | NotificationService | Tous (via API) | Les 15 producteurs directs actuels |
| **Conversations** | Conversation Service | ConversationService | Tous (via API) | Tout autre module |
| **Messages (chat)** | Conversation Service | ConversationService | Tous (via API) | Tout autre module |
| **Promesses (commitments)** | Commitment Service | CommitmentService | Tous (via API) | Tout autre module |
| **Rituels** | Ritual Service | RitualService | Tous (via API) | Tout autre module |
| **Localisation GPS** | Location Service | LocationService | Tous (via API) | Tout autre module |
| **Lieux (places)** | Location Service | LocationService | Tous (via API) | Tout autre module |
| **Visites (visits)** | Location Service | LocationService | Tous (via API) | Tout autre module |
| **Activité écran** | Screen Service | ScreenService | Tous (via API) | Tout autre module |
| **Devices** | Device Service | DeviceService | Tous (via API) | Tout autre module |
| **Embeddings** | Search Service | SearchService | Tous (via API) | Tout autre module |
| **Humeur (mood)** | Mood Service | MoodService | Tous (via API) | Tout autre module |
| **Sessions (auth)** | Auth Service | AuthService | AuthService uniquement | Tout autre module |

## Principe de communication

```
┌──────────────┐     écriture      ┌──────────────┐
│  Composant A │ ──── interdit ──▶ │  Donnée X    │
│              │                   │ (propriétaire│
│              │                   │  Service Y)  │
└──────┬───────┘                   └──────┬───────┘
       │                                  │
       │ event_bus.emit(                  │
       │   DataNeeded(...)                │
       │ )                                │
       │                                  ▼
       │                         ┌──────────────┐
       │                         │  Service Y   │
       └────────────────────────▶│  traite      │
          réponse via event       │  l'événement │
                                  └──────────────┘
```

**Règle absolue** : si un composant a besoin de modifier une donnée dont il n'est pas propriétaire, il émet un événement. Le propriétaire traite l'événement et effectue la modification.

## Exemple : création de notification

**État transitoire après Phase 3 (15 producteurs directs)** :
```python
# scripts/email_watcher.py
create_notification(source="email", title="...", priority="high")

# scripts/contact_alerts.py
create_notification(source="relationship", title="...", priority="medium")
```

Ces appels persistent d'abord la notification, puis `database/notifications.py` émet `NotificationCreated` après commit. Le journal, le WebSocket, le TTS et la PWA réagissent donc déjà sans couplage direct aux producteurs. La centralisation de la politique reste à faire.

**Cible après introduction de NotificationService** :
```python
# scripts/email_watcher.py
event_bus.emit(EmailAnalyzed(email_id=..., priority="high", summary="..."))

# scripts/contact_alerts.py
event_bus.emit(RelationshipAlert(contact=..., reason="silence", priority="medium"))

# notification_service.py (handler enregistré)
@event_bus.on(EmailAnalyzed)
async def handle_email(event):
    notification = self.create_notification(source="email", ...)
    event_bus.emit(NotificationCreated(notification))

@event_bus.on(RelationshipAlert)
async def handle_relationship(event):
    notification = self.create_notification(source="relationship", ...)
    event_bus.emit(NotificationCreated(notification))
```

## Contrôle d'accès

Chaque service expose une API publique (lecture) et des handlers d'événements (écriture indirecte). L'accès direct à la base de données n'est autorisé que pour le service propriétaire.

**Violation détectable** : `grep -r "INSERT INTO people" --include="*.py" | grep -v memory_service` doit retourner 0 résultat.
