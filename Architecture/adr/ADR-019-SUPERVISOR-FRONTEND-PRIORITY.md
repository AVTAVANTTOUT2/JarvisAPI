# ADR-019 — Priorité frontend identique Supervisor / FastAPI

**Date** : 2026-07-16  
**Statut** : Accepté  
**Remplace** : l’écart documenté dans `Architecture/32_FRONTEND_DATABASE_SOURCE_OF_TRUTH.md`
(supervisor = `web/dist` uniquement).

## Contexte

Le backend FastAPI (port 8081) servait déjà `frontend/out` (Next.js 15) en priorité,
avec `web/dist` (Vite) en fallback. Le supervisor (port 9000) servait uniquement
`web/dist`, produisant une UI différente selon le point d’entrée.

## Décision

Le supervisor et FastAPI utilisent la même politique desktop, centralisée dans
`core/frontend_resolution.py` :

1. **`frontend/out`** si build Next exploitable (`index.html` + `_next/static/`)
2. **`web/dist`** en fallback Vite (`index.html` présent)
3. **Erreur explicite** (`frontend_build_missing`) si aucun build n’est disponible

Le montage HTTP supervisor est dans `core/frontend_static.py`. FastAPI conserve
PWA `/m/` et Jinja, mais délègue le critère de validité Next/Vite au module partagé.

## Raisons

* Cohérence UI entre ports 8081 et 9000
* Réduction des divergences documentaires
* Conservation d’un fallback de sécurité (Vite)
* Migration progressive — aucune suppression de `web/`

## Conséquences

* Le build Next est requis pour l’interface canonique sur le port 9000
* Vite reste supporté temporairement
* `/api/supervisor/status` expose un bloc `frontend` (chemins relatifs)
* Aucun build n’est lancé automatiquement par le supervisor

## Alternatives rejetées

| Alternative | Pourquoi non |
|---|---|
| Forcer uniquement Next sur le supervisor | Cassait les checkouts sans `frontend/out` |
| Continuer Vite-only sur 9000 | Divergence UX permanente |
| Générer le build au démarrage | Hors périmètre, lenteur, effets de bord npm |
