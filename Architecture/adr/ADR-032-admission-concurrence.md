# ADR-032 — Admission et concurrence des runs agentiques

- Statut : accepté
- Date : 2026-08-13

## Contexte

Plusieurs runs agentiques ont saturé la RAM du Mac mini M4 (32 Go).

## Décision

L'admission est déterministe, avant tout démarrage de provider :

- concurrence globale, par profil, et d'écriture ;
- mémoire minimale libre (`AGENTIC_MIN_FREE_MEMORY_MB`, défaut 2048) ;
- priorité des origines interactives (user/voice/imessage/macos/android/web) ;
- cooldown après crash provider ;
- file bornée (`AGENTIC_MAX_QUEUE_WAIT_S`).

Un hold n'invente pas de statut SQL : le run reste `queued` et émet
`agent.run.resource_wait` + `admission_reason`. Expiration → `blocked` +
`resource_pressure`.

Les défauts sont documentés pour le M4 32 Go, pas hardcodés comme vérité
universelle.

## Conséquences

Mieux vaut une file explicite qu'un OOM. Les métriques restent locales, sans
contenu utilisateur.
