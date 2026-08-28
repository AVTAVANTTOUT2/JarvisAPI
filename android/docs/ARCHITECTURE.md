# Architecture Android — document superseded

> **SUPERSEDED — baseline 1.2.0 du 16 juillet 2026.** Cette page ne décrit plus
> le code courant. Utiliser [`android/README.md`](../README.md) pour le
> périmètre livré, [`OFFLINE_SYNC.md`](./OFFLINE_SYNC.md) et
> [`CHAT.md`](./CHAT.md) pour les contrats actifs, puis
> [`FUTURE_FEATURES.md`](./FUTURE_FEATURES.md) pour les capacités absentes.

La baseline historique limitait le Companion aux services mobiles et à la voix
PTT. Depuis, la navigation Compose, Room, WorkManager, le chat texte, le
streaming WebSocket avec repli HTTP et la file offline sont versionnés. Le
statut global reste « validation sur matériel requise » tant que les parcours
réseau, audio, localisation et reprise ne sont pas rejoués sur l’appareil
cible.

Les décisions historiques restent consultables dans les ADR
[`ADR-020`](../../Architecture/adr/ADR-020-android-offline-first-bearer.md) et
[`ADR-021`](../../Architecture/adr/ADR-021-android-offline-location-batch.md).
