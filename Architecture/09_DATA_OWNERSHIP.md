# 09 — Data Ownership (ADR-011)

**Date** : 11 juillet 2026
**ADR** : ADR-011
**Statut** : Partiellement appliqué — émissions post-commit actives en Phase 3 ; Notifications consolidées le 14/07/2026

---

## Problème

Actuellement, plusieurs modules écrivent dans les mêmes tables sans coordination :
- `upsert_person()` appelé depuis 7 fichiers
- `add_fact()` appelé depuis 5 fichiers
- notifications désormais écrites via `NotificationService`; les autres domaines restent à consolider

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
| **Notifications** | Notification Service | `NotificationService` | Tous (via API) | Tout accès direct au repository hors du service |
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

**État historique après Phase 3** :
```python
# scripts/email_watcher.py
create_notification(source="email", title="...", priority="high")

# scripts/contact_alerts.py
create_notification(source="relationship", title="...", priority="medium")
```

Ces appels persistaient directement les notifications. Ils sont conservés ici comme contexte historique uniquement.

**État appliqué le 14/07/2026** :
```python
# scripts/email_watcher.py
notification_service.create(
    source="email", title=summary, priority="high", email_id=email_id
)

# scripts/contact_alerts.py
notification_service.create(
    source="relationship", title=title, content=content, priority="medium"
)

# jarvis/notification_service.py
# persiste via database/notifications.py, puis émet NotificationCreated
# uniquement pour une nouvelle ligne validée.
```

`NotificationService` normalise les priorités, déduplique atomiquement les doublons récents par `source`/`title`/`email_id`, déclenche le Web Push best-effort et expose les opérations de lecture/marquage utilisées par l'API. La façade historique `database.create_notification()` délègue au service ; le test statique couvre l'absence d'appel direct dans `agents/` et `scripts/`.

## Contrôle d'accès

Chaque service expose une API publique (lecture) et des handlers d'événements (écriture indirecte). L'accès direct à la base de données n'est autorisé que pour le service propriétaire.

**Violation détectable** : `grep -r "INSERT INTO people" --include="*.py" | grep -v memory_service` doit retourner 0 résultat.
