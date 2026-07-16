# ADR-020 — Fondation offline-first Companion Android + Bearer métier

- **Statut :** Accepté (Vague 1 — 2026-07-16)
- **Contexte :** Le Companion 1.2.0 ne pouvait lire que les routes déjà en Bearer. Tasks, calendar, briefing et conversations exigeaient le cookie `jarvis_session`, non stocké côté OkHttp. Pas de Room ni de navigation applicative.

## Décision

1. **Auth :** étendre `api/middleware.py` pour accepter `Authorization: Bearer` mobile sur une **whitelist GET** (briefing, tasks, calendar, notifications, conversations, location status / visits today). Les mutations métier restent cookie session.
2. **Client :** architecture offline-first légère — Room comme source UI, `SyncManager` + WorkManager pour refresh, `AppContainer` sans Hilt.
3. **UI :** Navigation Compose (BottomBar + NavigationRail tablette), onboarding 5 étapes, Accueil basé sur le cache Room.
4. **Différé :** chat texte, voix continue, file GPS offline, version `2.0.0`.

## Conséquences

- Un téléphone appairé peut afficher un briefing réel hors cookie web.
- Surface d’attaque Bearer limitée aux lectures ; révocation device reste effective.
- `pending_location` existe en schema pour migration forward-safe sans brancher encore le service GPS.
- Prochaines vagues doivent prolonger la whitelist et la file offline sans casser le schema v1.

## Alternatives rejetées

- CookieJar OkHttp (CSRF / rotation fragiles)
- Routes `/api/mobile/*` facades (duplication)
- Hilt (migration lourde pour Vague 1)
