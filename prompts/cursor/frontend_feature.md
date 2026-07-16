---
id: frontend_feature
version: 2.0.0
date: 2026-07-16
domain: frontend
variables: user_request,acceptance_criteria,required_tests,context_files,extra_context,repo_rules,result_format,date,template_version
---

# Fonctionnalité frontend

# Objectif
{{user_request}}

# Contexte
Date: {{date}}
Template version: {{template_version}}
Trois arbres frontend : `frontend/` = application canonique Next.js 15 / React 19 en EXPORT STATIQUE (`frontend/out`, servi en priorité par FastAPI, layout mobile/desktop choisi par `UnifiedApp`) ; `web/` = fallback Vite ET source des vues desktop importées par le frontend unifié ; `jarvis_auth/` = SDK partagé `AuthClient` + `useLockGate()` + `LockGate`, rendu fail-closed (jamais monter les enfants privés avant confirmation de session).

Contexte JARVIS :
{{extra_context}}

# Symptômes / preuve disponible
- Identifie d'abord dans QUEL arbre vivra le changement : vue desktop → source dans `web/src` (importée par `frontend/`) ; vue mobile ou layout → `frontend/src` ; auth → `jarvis_auth/` uniquement.
- Lis `frontend/src/lib/api.ts` avant tout appel réseau : c'est l'UNIQUE endroit autorisé à appeler `fetch()` dans les trois arbres (cookie de session inclus systématiquement). Vérifie si la méthode API dont tu as besoin existe déjà.
- État initial : `pnpm build` dans `frontend/` doit passer avant tes changements ; sinon, signale l'état préexistant dans le rapport.

# Périmètre
- Tous les appels API passent par le client `frontend/src/lib/api.ts` (ajouter la méthode typée manquante s'il le faut) — JAMAIS de `fetch()` direct dans un composant.
- Respecter l'export statique Next.js 15 : pas d'API route Next, pas de rendu serveur dynamique, pas de dépendance à `headers()`/`cookies()` côté serveur.
- Ne jamais contourner le LockGate : aucun contenu privé rendu avant confirmation de session ; les nouveaux écrans privés se montent SOUS le gate existant.
- Si une vue desktop est modifiée dans `web/src`, vérifier qu'elle reste importable par le frontend unifié sans duplication.

# Hors périmètre
- Nouveau state manager, nouvelle lib UI ou refonte du design system non demandés.
- Modification du Service Worker (`frontend/public/sw.js`) : il ne doit jamais cacher `/api`, HTML ou données personnelles — tout changement SW est hors périmètre sauf demande explicite.
- Toucher aux anciens builds (`web/dist`, `pwa/out`) : ce sont des artefacts.

# Fichiers probables
{{context_files}}
- Client API et types : `frontend/src/lib/api.ts` ; détection device : `frontend/src/lib/device.ts` ; vues desktop : `web/src/app/components/views/` ; auth : `jarvis_auth/src/`.
- Serving FastAPI : `api/frontend.py` (ordre frontend/out → web/dist → pwa/out).

# Règles d'architecture
{{repo_rules}}
- Dark mode par défaut, pas d'emoji dans l'UI JARVIS, français partout.

# Critères d'acceptation
{{acceptance_criteria}}
- `pnpm build` passe dans `frontend/` (typecheck inclus) ET dans `web/` si `web/src` a été touché.
- Aucun `fetch()` direct introduit hors `frontend/src/lib/api.ts` (grep de vérification).

# Tests obligatoires
{{required_tests}}
- Vitest existants verts (`pnpm test` dans l'arbre touché) ; ajouter des tests pour la logique non triviale introduite.

# Validation réelle
- Builds : `pnpm build` dans `frontend/` (export statique 25 pages attendu) et dans `web/` si modifié — inclure les sorties dans le rapport.
- Grep final : `fetch(` ne doit apparaître dans aucun nouveau composant (uniquement `lib/api.ts`).
- Vérifier le comportement fail-closed : l'écran ajouté n'apparaît pas sans session (lecture du montage sous LockGate, ou test Playwright si disponible).

# Stratégie Git
- Jamais de modification directe de main.
- Travailler uniquement dans le worktree / la branche fournie.
- Commits clairs, séparant client API, vues et tests.

# Format du rapport final (OBLIGATOIRE)
{{result_format}}

## Qualité constante
- Chercher la cause racine des erreurs de build (types, imports croisés entre arbres), pas de `any` ni de `@ts-ignore` pour masquer.
- Ne pas masquer le problème ; ne rien inventer (pas d'endpoint backend supposé — vérifier dans `api/router_*.py`).
- Ne pas supprimer une fonction existante sans preuve qu'elle est morte dans les TROIS arbres.
- Respecter les conventions du dépôt ; tester avant chaque commit.
- Comparer avant/après (build + rendu) ; préserver les contrats existants.
- Rapport précis ; ne pas déclarer COMPLETED sans preuve (sorties de build réelles).
