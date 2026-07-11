# 09 — Data Ownership (ADR-011)

**Date** : 11 juillet 2026
**ADR** : ADR-011
**Statut** : Proposé

---

## Problème

Actuellement, plusieurs modules écrivent dans les mêmes tables sans coordination :
- `upsert_person()` appelé depuis 7 fichiers
- `add_fact()` appelé depuis 5 fichiers
- `create_notification()` appelé depuis 19 fichiers

Ces écritures concurrentes créent un risque d'incohérence et violent le principe de source unique de vérité.

## Solution

Chaque donnée du système a **un propriétaire unique**. Seul le propriétaire peut écrire. Les autres composants peuvent lire via l'API publique du propriétaire, ou réagir aux événements émis par le propriétaire via l'Event Bus.

## Tableau de propriété des données

| Donnée | Propriétaire | Peut écrire | Peut lire | Ne doit JAMAIS modifier |
|---|---|---|---|---|
| **Messages iMessage bruts** | Apple Data Service | AppleDataService | Tous (via API) | Tout autre module |
| **Contacts (people)** | Apple Data Service | AppleDataService | Tous (via API) | agents/memory.py, agents/coach.py, agents/journal.py |
| **Calendrier** | Apple Data Service | AppleDataService | Tous (via API) | Tout autre module |
| **Emails** | Apple Data Service | AppleDataService | Tous (via API) | Tout autre module |
| **Faits utilisateur** | Memory Service | MemoryService | Tous (via API) | agents/coach.py, audio/continuous_recorder.py, scripts/relationship_analyzer.py |
| **Patterns** | Memory Service | MemoryService | Tous (via API) | Tout autre module |
| **Life Profile** | Memory Service | MemoryService | Tous (via API) | Tout autre module |
| **Épisodes** | Memory Service | MemoryService | Tous (via API) | Tout autre module |
| **Relationships** | Memory Service | MemoryService | Tous (via API) | scripts/relationship_analyzer.py |
| **Tâches** | Task Service | TaskService | Tous (via API) | Tout autre module |
| **Journal** | Journal Service | JournalService | Tous (via API) | Tout autre module |
| **Notifications** | Notification Service | NotificationService | Tous (via API) | Les 19 émetteurs directs actuels |
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

**Avant (19 émetteurs directs)** :
```python
# scripts/email_watcher.py
create_notification(source="email", title="...", priority="high")

# scripts/contact_alerts.py
create_notification(source="relationship", title="...", priority="medium")
```

**Après (Event Bus)** :
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
