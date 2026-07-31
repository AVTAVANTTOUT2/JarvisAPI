# 35 — Cahier des charges : `web_mobile/`

Interface mobile JARVIS refaite de zéro, isolée, servie par FastAPI.
Rédigé le 30/07/2026. **Statut : implémenté, corrigé et audité le 31/07/2026.**

---

## 1. Contexte — pourquoi on refait

L'interface mobile actuelle (`pwa/src`, compilée dans `frontend/out` via
l'alias webpack `@mobile`) présente trois défauts structurels :

1. **Couverture 5 / 21.** `MobileApp.tsx` ne mappe que `dashboard`, `map`,
   `mails`, `tasks`, `config`. Les 16 autres routes tombent dans un fallback
   silencieux `MOBILE_PAGES[segment] ?? DashboardPage` — le chat et la voix,
   cœur de l'usage iPhone, sont inatteignables.
2. **Pas d'isolation.** Le mobile est compilé dans le même bundle Next que le
   desktop, partage `frontend/src/app/globals.css`, et sa propre config
   Tailwind (`pwa/tailwind.config.ts`) n'est jamais chargée.
3. **Fuite du desktop.** Dès que l'heuristique client `shouldUseMobileLayout()`
   dit non (tablette, largeur ≥ 768, mode bureau), c'est `BigBrotherLayout` qui
   s'affiche — sidebar `hidden md:flex`, barre de pilules `overflow-x-auto`,
   vues sans breakpoints (`CalendarView` en `grid-cols-7`, `ContactsView` en
   `grid-cols-6`). Soit exactement « le desktop en petit ».

`/m/` n'existe plus : `pwa/out` n'a jamais été buildé sur cette machine, donc
`_setup_pwa_frontend()` renvoie `False` et toutes les routes `/m/*` sont en 404.

## 2. Objectif

Une interface mobile **autonome**, lisible sans outillage, conçue pour un
iPhone tenu à une main, branchée sur les API existantes sans modification du
backend.

## 3. Contraintes non négociables

| # | Contrainte | Raison |
|---|---|---|
| C1 | HTML + CSS + JS vanilla. Pas de framework, pas de build, pas de `node_modules`. | On édite un fichier, on recharge, on voit. |
| C2 | Zéro import depuis `web/src`, `pwa/src`, `frontend/src`, `jarvis_auth`. | Isolation réelle : casser le mobile ne doit jamais casser le desktop. |
| C3 | Servi par FastAPI, **même origine**, même port. | Le cookie `jarvis_session` est `SameSite=Strict` ; le middleware vérifie `Origin`/`Referer` ; la CSP est `default-src 'self'`. Une autre origine casse les trois. |
| C4 | Aucune modification des routers `api/*`. | Le backend est stable, la refonte est purement frontale. |
| C5 | Fail-closed sur l'auth. | Aucune donnée affichée avant session confirmée. |
| C6 | CSP respectée : pas de CDN, pas de police Google, pas de script externe. | `default-src 'self'` ; `style-src` autorise l'inline. |

### Note sur PHP

PHP 8.5.8 est présent (Homebrew), mais écarté : il imposerait un second process
(php-fpm supervisé) ou un reverse-proxy, sans rien apporter — la page ne fait
que lire du JSON et l'afficher. Le bénéfice recherché (« pas de framework, pas
de build, un fichier que je comprends ») est obtenu intégralement en C1.

## 4. Détection et redirection

Aujourd'hui `_setup_unified_frontend()` retourne avant d'atteindre le bloc de
redirection mobile de `_setup_frontend()` — la détection est morte. Correctif :
**remonter la détection au-dessus du frontend unifié**.

```
GET /
 ├── UA téléphone (_is_mobile_device)  → 302 → /mobile/
 └── sinon                             → frontend/out (desktop inchangé)

GET /mobile/*  → web_mobile/ (statique, jamais de redirection)
```

- Réutilise `_is_mobile_device()` ([api/frontend.py:66](../api/frontend.py))
  tel quel : iPhone/iPod, Android *Mobile*, Windows Phone, Opera Mini,
  BlackBerry, IEMobile. Les tablettes Android et l'iPad restent sur desktop.
- **Échappatoire obligatoire** : `?desktop=1` pose un cookie `jarvis_force_desktop`
  qui désactive la redirection. Sans ça, impossible d'atteindre le desktop
  depuis un iPhone. Lien « Version bureau » en pied de l'interface mobile.
- La redirection générale ne s'applique qu'à `/`. Les deux anciens points
  d'entrée installables `/chat` et `/dashboard` migrent aussi vers leur écran
  mobile équivalent ; les autres liens profonds desktop restent accessibles.

## 5. Authentification

`web_mobile` s'authentifie **par cookie de session**, pas par jeton Bearer.

> Les endpoints `/api/mobile/*` (`chat`, `chat/confirm`, `conversations`,
> `voice/turn`) exigent `_require_mobile_device()` → un Bearer réservé au
> Companion Android natif. **Inutilisables depuis un navigateur.** Le chat et la
> voix passeront donc par `/ws`, comme le desktop.

Écran de déverrouillage à réécrire (≈ 60 lignes) :

1. `GET /api/auth/status` → `{configured, authenticated, locked_out, lockout_seconds}`
2. non configuré → `POST /api/auth/setup {secret}`
3. non authentifié → `POST /api/auth/unlock {secret}`
4. `locked_out` → compte à rebours, aucun champ actif
5. verrouillage auto après `AUTO_LOCK_MINUTES` d'inactivité
6. rien d'autre n'est rendu tant que `authenticated !== true`

Clavier numérique iOS : `inputmode="numeric"` + `autocomplete="one-time-code"`.

## 6. Écrans v1

Cinq onglets, barre de navigation basse, `env(safe-area-inset-bottom)`.

### 6.1 Chat — écran par défaut

| Besoin | Source |
|---|---|
| Envoi / réception streaming | `WS /ws` — `{"type":"text","content":…}` → `response` |
| Historique | `GET /api/conversations`, `GET /api/conversations/{id}` |
| Nouvelle conversation | `{"type":"new_conversation"}` |
| Changer de conversation | `{"type":"switch_conversation","conversation_id":N}` |
| Confirmation d'action sensible | `{"type":"action_confirm", …}` |

Le WS est authentifié par `resolve_websocket_auth()` (cookie de session ou
device mobile) et se ferme en 4401 sinon — le cookie posé au déverrouillage
suffit. Reconnexion automatique avec backoff.

### 6.2 Voix — push-to-talk

`MediaRecorder` → blob WebM → `ws.send(blob)` → `processing` → chunks TTS →
`speech_done` → lecture. Push-to-talk uniquement en v1 : **pas** de VAD, pas de
mode mains libres — Safari iOS impose un geste utilisateur pour `getUserMedia`
et pour la lecture audio.

### 6.3 Dashboard / briefing

`GET /api/briefing` · `GET /api/notifications` · `GET /api/tasks` ·
`GET /api/calendar` · `GET /api/rituals/today`

Priorité d'affichage : notifications urgentes → agenda du jour → tâches en
retard → briefing.

### 6.4 Tâches

`GET /api/tasks` · `POST /api/tasks` · `PATCH /api/tasks/{id}` ·
`DELETE /api/tasks/{id}`

### 6.5 Mails

`GET /api/emails` (résumés déjà analysés par l'email watcher) ·
`GET /api/notifications` · `POST /api/notifications/{id}/read` ·
`POST /api/notifications/read-all`

Lecture seule : pas de rédaction ni d'envoi en v1.

### 6.6 Sport / Santé — emplacement réservé

Section en cours de conception par l'utilisateur, **hors périmètre de ce
document**. Le module backend existe déjà et est monté
([main.py:109](../main.py)) : `app/fitness/` expose
`/api/fitness/{workouts,meals,water/today,wellbeing,summary/today}`.

À prévoir dès la v1 : un sixième emplacement dans la barre de navigation et un
fichier d'écran vide, pour que l'ajout ne demande aucune refonte de la
navigation. Le contenu sera spécifié séparément.

## 7. Design

- Portrait, une main, cible tactile ≥ 44 px.
- Thème sombre repris du reste de JARVIS : fond `#0a0a0f`, accent `#4a9eff`,
  cartes `rgba(255,255,255,.035)`, bordures `rgba(255,255,255,.07)`.
- Police système (`-apple-system`) — pas de webfont, C6.
- `viewport-fit=cover` + safe areas haut et bas.
- `overscroll-behavior: none`, pas de zoom involontaire
  (`font-size: 16px` sur les champs).
- Pas d'emoji dans les textes produits par JARVIS (persona).

## 8. Structure proposée

```
web_mobile/
├── index.html          # shell + barre d'onglets + écran de verrouillage
├── app.css             # une feuille, pas de préprocesseur
├── js/
│   ├── api.js          # fetch credentials:'include' + gestion 401/428
│   ├── auth.js         # LockGate réécrit
│   ├── ws.js           # WebSocket + reconnexion
│   └── views/
│       ├── chat.js
│       ├── voice.js
│       ├── dashboard.js
│       ├── tasks.js
│       ├── mails.js
│       └── fitness.js  # vide, réservé §6.6
└── icons/              # copiés, pas partagés
```

Navigation par hash (`#/chat`) : aucune route serveur supplémentaire, retour
iOS fonctionnel, rechargement dur sans 404.

## 9. Suppression de `pwa/`

Décision : suppression complète.

`pwa/` est suivi par git (50 fichiers, arbre propre) → **restaurable par
`git revert`**. Références à retirer, vérifiées :

| Fichier | Action |
|---|---|
| `frontend/next.config.js` | alias `@mobile` |
| `frontend/tsconfig.json` | path `@mobile/*` |
| `frontend/src/app/globals.css` | `@source '../../../pwa/src/**'` |
| `frontend/src/components/MobileApp.tsx` | fichier supprimé |
| `frontend/src/components/UnifiedApp.tsx` | branche mobile supprimée |
| `frontend/src/lib/device.ts` | `MOBILE_ROUTES`, `shouldUseMobileLayout` |
| `frontend/vitest.config.ts` | alias |
| `api/frontend.py` | `_setup_pwa_frontend`, `_PWA_PREFIX`, `_PWA_SEGMENTS` |
| `config.py` | `PWA_ENABLED`, `PWA_DIR`, `PWA_URL` |
| `scripts/build_pwa.sh` | supprimé |
| `.github/workflows/ci.yml` | job PWA |
| `tests/test_phase6_frontend.py` | assertions mobile |
| `main.py`, `database/migrations.py`, `tools/audit_architecture_truth.py`, `artifacts/architecture_truth.json` | mentions à auditer |

Après suppression, `UnifiedApp` ne rend plus que le desktop — cohérent, puisque
tout terminal mobile est redirigé vers `/mobile/` en amont.

## 10. Hors périmètre v1

Carte, contacts, recherche, analytics, journal, mémoire, monitoring, logs,
cognitive, control — restent desktop. Pas de Service Worker, pas de mode
hors-ligne, pas de push : à traiter une fois la v1 utilisée au quotidien.

## 11. Critère d'acceptation

Depuis l'iPhone, sur le réseau Tailscale : ouvrir la racine → redirection →
déverrouillage par PIN → poser une question dans le chat et recevoir la
réponse en streaming → dicter une question et entendre la réponse → voir le
briefing, les tâches et les mails du jour → cocher une tâche. Sans zoom, sans
scroll horizontal, sans contenu masqué par l'encoche ou la barre d'accueil.

## 12. Défauts annexes relevés (hors périmètre, à traiter séparément)

- `GET /cognitive` → 404 : `_UNIFIED_SEGMENTS` ne contient pas `cognitive`
  alors que `frontend/out/cognitive/index.html` existe.
- `web/src/index.css` n'est jamais importé par le build unifié ; `.debug-prompt`
  et `.latency-*` (VoiceDebugView) sont absents de `frontend/src/app/globals.css`.
- `web/src/app/components/map/cartographyMap.css` n'est importé nulle part.
- `web/dist/` conserve un Service Worker Workbox de 28 Ko et un manifest
  `start_url: /chat` hérités — susceptibles de servir l'ancien shell desktop à
  un appareil ayant installé la PWA à l'époque.
