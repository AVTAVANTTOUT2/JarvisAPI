# JARVIS Android H24 — vue superseded

> **SUPERSEDED — ne pas utiliser comme état courant.** Ce document décrivait le
> prototype Android de juillet 2026. La vérité produit vit dans
> [`android/README.md`](../android/README.md), le statut de validation dans
> [`Architecture/28_VALIDATION_COHERENCE.md`](./28_VALIDATION_COHERENCE.md) et
> les capacités volontairement absentes dans
> [`android/docs/FUTURE_FEATURES.md`](../android/docs/FUTURE_FEATURES.md).

Le prototype initial ne possédait pas encore la navigation native, Room,
WorkManager, le chat texte et la file offline. Ces affirmations sont
historiques : le code courant contient désormais ces briques et ne doit pas
être évalué à partir de cette baseline.

La release courante reste néanmoins sous statut
`IMPLEMENTED_NEEDS_REAL_VALIDATION` : les tests JVM/CI ne remplacent pas les
parcours sur le téléphone cible (pairage, HTTPS, biométrie, reprise offline,
GPS, FCM, wake word et voix). Aucun résultat matériel n’est revendiqué ici.
