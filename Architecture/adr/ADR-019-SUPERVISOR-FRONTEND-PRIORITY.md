# ADR-019 — Frontend bureau unique pour Supervisor et FastAPI

**Date** : 2026-07-16  
**Statut** : Accepté, amendé le 4 août 2026
**Remplace** : l’écart documenté dans `Architecture/32_FRONTEND_DATABASE_SOURCE_OF_TRUTH.md`
(supervisor = `web/dist` uniquement).

## Contexte

Le backend FastAPI (port 8081) et le supervisor (port 9000) pouvaient servir
deux applications bureau distinctes : `frontend/out` (Next.js 15) et
`web/dist` (Vite). Ce repli prolongeait deux shells, deux builds et deux Service
Workers, avec des comportements différents selon le point d'entrée.

## Décision

Le supervisor et FastAPI utilisent le même contrat desktop, centralisé dans
`core/frontend_resolution.py` :

1. **`frontend/out`** si build Next exploitable (`index.html` + `_next/static/`)
2. **Erreur explicite** (`frontend_build_missing`, HTTP 503) sinon

Le montage HTTP supervisor est dans `core/frontend_static.py`. `web/src` reste
la bibliothèque de vues importée par Next.js, mais n'a plus d'entrée Vite, de
commande de build, de Service Worker ou de chemin runtime.
> Note (31/07/2026) : le montage PWA `/m/` mentionné ici a depuis été supprimé
> au profit de `web_mobile/` servi sous `/mobile/`.

## Raisons

* Cohérence UI entre ports 8081 et 9000
* Réduction des divergences documentaires
* Une seule application bureau à construire, sécuriser et tester
* Échec visible plutôt qu'un rollback silencieux vers un produit divergent

## Conséquences

* Le build Next est requis pour l’interface bureau sur les ports 8081 et 9000
* Un ancien `web/dist` local est volontairement ignoré
* `/api/supervisor/status` expose un bloc `frontend` (chemins relatifs)
* Aucun build n’est lancé automatiquement par le supervisor

## Alternatives rejetées

| Alternative | Pourquoi non |
|---|---|
| Restaurer Vite si Next manque | Réintroduit un second produit et masque un déploiement incomplet |
| Continuer Vite-only sur 9000 | Divergence UX permanente |
| Générer le build au démarrage | Hors périmètre, lenteur, effets de bord npm |
