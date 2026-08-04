# Registre des fonctionnalités futures — JARVIS Companion Android

Chaque entrée correspond à un emplacement visuel/technique préparé pendant la refonte
`feat/android-ui-redesign`. Règle absolue : un placeholder ne simule jamais une
fonctionnalité active et n'affiche jamais de donnée mockée. Il n'existe aucun
commutateur runtime pour ces fonctions absentes : le placeholder doit être remplacé
par une implémentation testée lorsqu'elle est livrée. Les TODO code portent
l'identifiant de backlog stable ci-dessous.

| ID | Écran | Placeholder | Statut |
|---|---|---|---|
| JARVIS-FUTURE-VOICE-CONTINUOUS | Voix | Bouton secondaire désactivé + badge « Bientôt » | Logique absente |
| JARVIS-FUTURE-WAKE-ADVANCED | Réglages · Voix | Ligne descriptive sous le toggle Porcupine | Logique absente |
| JARVIS-FUTURE-LIVE-MAP | Localisation | `JarvisComingSoonCard` | Logique absente |
| JARVIS-FUTURE-TRIPS-HISTORY | Localisation | `JarvisComingSoonCard` | Logique absente |
| JARVIS-FUTURE-CALENDAR-CREATE | Agenda | Bouton création désactivé + badge | Logique absente |
| JARVIS-FUTURE-TASKS-MUTATIONS | Tâches | Bouton création désactivé + badge | Logique absente |
| JARVIS-FUTURE-CHAT-ATTACHMENTS | Chat (composer) | Icône trombone désactivée | Logique absente |
| JARVIS-FUTURE-SLASH-COMMANDS | Chat (composer) | Détection `/` → note « Bientôt » | Logique absente |
| JARVIS-FUTURE-NOTIFICATIONS-ACTIONS | Plus → Notifications | Liste cache en lecture seule | Actions absentes |
| JARVIS-FUTURE-MULTI-DEVICE | Réglages · Connexion | Ligne désactivée + badge | Logique absente |
| JARVIS-FUTURE-OFFLINE-DETAIL | Diagnostics | Section « File hors ligne détaillée » | Logique absente |
| JARVIS-FUTURE-MEMORY-VIEW | Plus | Tuile « Bientôt » | Écran absent |
| JARVIS-FUTURE-CONTACTS | Plus | Tuile « Bientôt » | Écran absent |
| JARVIS-FUTURE-AUTOMATIONS | Plus | Tuile « Bientôt » | Écran absent |
| JARVIS-FUTURE-WIDGETS | Réglages · Apparence | Ligne désactivée + badge | Logique absente |
| JARVIS-FUTURE-DASHBOARD-CUSTOM | Accueil | `JarvisFutureAction` inerte | Logique absente |

## Détails et contrats attendus

### JARVIS-FUTURE-VOICE-CONTINUOUS
- Point de branchement : `voice/VoiceActivity.kt` (bouton secondaire sous l'orbe).
- Dépendances : service audio continu + VAD client + anti-écho.
- Backend : session vocale WebSocket streaming (cf. pipeline `/voice` web,
  `api/ws_handler.py`).

### JARVIS-FUTURE-LIVE-MAP / JARVIS-FUTURE-TRIPS-HISTORY
- Point de branchement : `feature/location/LocationScreen.kt` (cartes dédiées).
- Backend : `GET /api/location/history`, `GET /api/trips` (existants côté serveur,
  non consommés par l'app — nécessite décision d'exposition mobile + confidentialité).

### JARVIS-FUTURE-CALENDAR-CREATE
- Point de branchement : `feature/agenda/AgendaScreen.kt` (bouton « Nouvel événement »).
- Backend : `POST /api/calendar/*` (à exposer au mobile Bearer).

### JARVIS-FUTURE-TASKS-MUTATIONS
- Point de branchement : `feature/tasks/TasksScreen.kt` (création/complétion) ; la file
  d'écriture offline `pending_chat_operations` sert de modèle.
- Backend : mutations tâches mobiles (PATCH/POST `/api/tasks` Bearer).

### JARVIS-FUTURE-CHAT-ATTACHMENTS
- Point de branchement : composer de `feature/chat/ChatScreen.kt`.
- Backend : `POST /api/conversations/{id}/upload` (existe côté web, cookie session) —
  version Bearer mobile à définir.

### JARVIS-FUTURE-SLASH-COMMANDS
- Point de branchement : composer (`ChatScreen`) — surface de suggestions au-dessus du
  champ. Contrat : commandes locales (`/nouveau`, `/cherche`, `/briefing`, `/tâche`)
  alignées sur le composer web.

### JARVIS-FUTURE-NOTIFICATIONS-ACTIONS
- Point de branchement : `feature/notifications/NotificationsScreen.kt` (créé — liste
  le cache `cached_notifications` en lecture seule, actions marquer-lu absentes).
- Backend : `POST /api/notifications/{id}/read` + `read-all` en Bearer mobile.

### JARVIS-FUTURE-MULTI-DEVICE
- Point de branchement : Réglages → Connexion.
- Backend : `GET /api/devices` + gestion de jetons par appareil.

### JARVIS-FUTURE-MEMORY-VIEW / CONTACTS / AUTOMATIONS
- Point de branchement : tuiles « Bientôt » dans `feature/more/MoreScreen.kt`
  (routes non déclarées tant que les écrans n'existent pas — pas d'écrans vides).
- Backend : `/api/memory`, `/api/people`, automatisations (à définir).

### JARVIS-FUTURE-WAKE-ADVANCED / HOME_WIDGETS / DASHBOARD_CUSTOM
- Entrées de backlog ; aucun engagement UI au-delà d'une ligne descriptive
  désactivée.

## Composants placeholder du design system

- `JarvisComingSoonBadge` — pastille « Bientôt » (ambre discret).
- `JarvisComingSoonCard` — carte verre désactivée : titre, description de l'état futur,
  badge. Aucune interaction.
- `JarvisFutureAction` — bouton/ligne désactivé avec badge et description courte.
- `JarvisFeatureDisabledState` — état plein écran « fonctionnalité non disponible ».

Validation : tous les placeholders listés sont inertes (aucun `onClick` actif), texte
explicite (« Bientôt disponible » / « sera activée dans une prochaine version »). Il
n'existe aucun flag permettant de les masquer ou de les présenter comme activés sans
implémentation.
