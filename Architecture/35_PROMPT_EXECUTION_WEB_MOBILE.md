# Prompt d'exécution — `web_mobile/`

Brief autonome. L'exécutant n'a accès à aucune conversation préalable : tout ce
qui est nécessaire est ici ou dans le cahier des charges référencé.

---

## MISSION

Dans le dépôt `/Users/zeldris/JARVIS` (Python 3.12 + FastAPI, frontend Next.js 15
+ Vite), construire **une interface web mobile entièrement nouvelle et isolée**
dans `web_mobile/`, rediriger automatiquement les téléphones vers elle, et
**supprimer l'ancienne PWA `pwa/`**.

Le cahier des charges complet est
[`Architecture/35_CAHIER_DES_CHARGES_WEB_MOBILE.md`](35_CAHIER_DES_CHARGES_WEB_MOBILE.md).
**Lis-le en entier avant d'écrire une ligne.** Ce prompt ne le répète pas ; il
donne la méthode, les pièges et les critères de sortie.

Le résultat doit être livré **exclusivement sous forme de pull request**.
`main` est protégée : aucun commit direct, aucun push sur `main`.

---

## POURQUOI

L'interface mobile actuelle est le layout desktop rétréci, mal utilisable sur
iPhone. Trois causes, toutes vérifiées :

1. `frontend/src/components/MobileApp.tsx` ne mappe que 5 segments
   (`dashboard`, `map`, `mails`, `tasks`, `config`) contre 21 routes desktop.
   Les 16 autres tombent dans `MOBILE_PAGES[segment] ?? DashboardPage` — un
   fallback silencieux. Le chat et la voix sont inatteignables sur mobile.
2. Dès que `shouldUseMobileLayout()` renvoie `false` (tablette, largeur ≥ 768,
   mode bureau), c'est `web/src/app/components/layout/BigBrotherLayout.tsx` qui
   s'affiche : sidebar `hidden md:flex`, barre de pilules `overflow-x-auto`, et
   des vues sans aucun breakpoint (`CalendarView` en `grid-cols-7`,
   `ContactsView` en `grid-cols-6`).
3. La redirection mobile serveur est **morte** : `_setup_unified_frontend()`
   fait un `return` dans `_setup_frontend()` avant que le bloc de redirection
   ne soit atteint. `_is_mobile_device()` n'est jamais consulté en pratique.

Par ailleurs `/m/` n'existe plus : `pwa/out` n'a jamais été buildé, donc
`_setup_pwa_frontend()` renvoie `False` et toutes les routes `/m/*` sont en 404.

---

## CONTRAINTES NON NÉGOCIABLES

| # | Contrainte |
|---|---|
| C1 | HTML + CSS + JS vanilla. **Aucun** framework, build step, bundler, `package.json` ou `node_modules` dans `web_mobile/`. |
| C2 | **Zéro** import depuis `web/src`, `pwa/src`, `frontend/src`, `jarvis_auth`. Isolation stricte : casser le mobile ne doit jamais casser le desktop. |
| C3 | Servi par FastAPI sur la **même origine et le même port**. Le cookie `jarvis_session` est `SameSite=Strict`, le middleware vérifie `Origin`/`Referer`, la CSP est `default-src 'self'`. Une autre origine casse les trois. |
| C4 | **Aucune modification des routers `api/router_*.py` ni de la logique métier.** Seul `api/frontend.py` est touché côté API, pour le montage et la redirection. |
| C5 | Fail-closed : aucune donnée rendue avant `authenticated === true`. |
| C6 | CSP respectée : aucun CDN, aucune webfont Google, aucun script externe. Tout en local ou inline. |
| C7 | Le desktop doit rester **strictement intact** pour un navigateur non-mobile. |

---

## TRAVAIL, PAR ÉTAPES

Committe à chaque étape. Messages en conventional commits, comme l'historique
du dépôt (`feat(scope): …`, `refactor(scope): …`, `test(scope): …`).

### Étape 0 — Branche

```bash
git checkout -b feat/web-mobile-standalone
```

### Étape 1 — `web_mobile/` (le gros du travail)

Structure cible (§8 du cahier des charges) : `index.html`, `app.css`,
`js/{api,auth,ws}.js`, `js/views/{chat,voice,dashboard,tasks,mails,fitness}.js`,
`icons/`.

Navigation par hash (`#/chat`) — pas de route serveur supplémentaire, retour
iOS fonctionnel, rechargement dur sans 404.

**Ordre recommandé** : `api.js` → `auth.js` (écran de déverrouillage) → shell +
navigation → dashboard → tâches → mails → `ws.js` → chat → voix. L'auth d'abord :
sans elle, toutes les API renvoient 401/428 et rien n'est testable.

Points d'attention :

- **Auth par cookie de session**, pas Bearer. Les endpoints `/api/mobile/*`
  (`chat`, `chat/confirm`, `conversations`, `voice/turn`) passent par
  `_require_mobile_device()` → jeton réservé au Companion Android natif,
  **inutilisables depuis un navigateur**. Ne les utilise pas.
- **Chat et voix passent par `WS /ws`**, authentifié par `resolve_websocket_auth()`
  (cookie de session ou device mobile), fermeture 4401 sinon. Le cookie posé au
  déverrouillage suffit. Protocole dans `api/ws_handler.py` : `text`,
  `new_conversation`, `switch_conversation`, `action_confirm`, `done_playing`.
- **Voix : push-to-talk uniquement.** Pas de VAD, pas de mains libres — Safari
  iOS exige un geste utilisateur pour `getUserMedia` et pour la lecture audio.
- `fetch` systématiquement avec `credentials: 'include'`. Un 428 signifie
  « verrou non configuré », un 401 « session expirée » → retour à l'écran de
  déverrouillage.
- `js/views/fitness.js` reste **vide** : emplacement réservé, 6ᵉ onglet présent
  dans la navigation. Le module backend existe déjà et est monté
  (`app/fitness/`, `/api/fitness/{workouts,meals,water/today,wellbeing,summary/today}`)
  mais **son UI n'est pas dans le périmètre** — ne l'implémente pas, ne devine
  pas ses écrans.

### Étape 2 — Montage et redirection dans `api/frontend.py`

```
GET /
 ├── UA téléphone (_is_mobile_device) et pas de cookie jarvis_force_desktop
 │      → RedirectResponse 302 vers /mobile/
 └── sinon → frontend/out (desktop, inchangé)

GET /mobile/*  → web_mobile/ en statique, jamais de redirection
```

- Réutilise `_is_mobile_device()` tel quel (`api/frontend.py:66`) : il exclut
  déjà correctement iPad et tablettes Android.
- La détection doit être évaluée **avant** le `return` de
  `_setup_unified_frontend()`, sinon elle reste morte (cause n°3 ci-dessus).
- **Échappatoire obligatoire** : `?desktop=1` pose le cookie
  `jarvis_force_desktop` qui désactive la redirection ; lien « Version bureau »
  en pied de l'interface mobile. Sans ça, le desktop devient inatteignable
  depuis un iPhone, y compris pour déboguer.
- La redirection ne s'applique qu'à `/`. Les liens profonds desktop restent
  accessibles si on les ouvre directement.
- Nouvelle variable de config `WEB_MOBILE_DIR` (défaut `./web_mobile`), sur le
  modèle de `FRONTEND_DIST_DIR`. Si le répertoire est absent : pas de
  redirection, log d'avertissement, desktop servi normalement.

### Étape 3 — Suppression de `pwa/`

`pwa/` est suivi par git (50 fichiers, arbre propre) → la suppression est
annulable par `git revert`.

`git rm -r pwa/`, puis retirer **toutes** les références (§9 du cahier des
charges) :

| Fichier | Ce qu'il faut retirer |
|---|---|
| `frontend/next.config.js` | alias webpack `@mobile` |
| `frontend/tsconfig.json` | path `@mobile/*` |
| `frontend/vitest.config.ts:15` | alias `@mobile` |
| `frontend/src/app/globals.css` | `@source '../../../pwa/src/**'` |
| `frontend/src/components/MobileApp.tsx` | fichier entier |
| `frontend/src/components/UnifiedApp.tsx` | branche mobile → ne rend plus que `DesktopApp` |
| `frontend/src/lib/device.ts` | `MOBILE_ROUTES`, `shouldUseMobileLayout`, `isJarvisAndroidApp` si plus utilisés |
| `frontend/src/lib/device.test.ts` | tests des fonctions supprimées |
| `api/frontend.py` | `_setup_pwa_frontend`, `_PWA_PREFIX`, `_PWA_SEGMENTS`, `PWA_DIR` |
| `main.py:31` | ré-export `_setup_pwa_frontend` |
| `config.py` | `PWA_ENABLED`, `PWA_DIR`, `PWA_URL` |
| `.env.example`, `.env.config.example` | variables `PWA_*` |
| `scripts/build_pwa.sh` | fichier entier |
| `.github/workflows/ci.yml:114` | ligne `npm --prefix ../pwa ci` |
| `tools/audit_architecture_truth.py` | entrées `pwa/out`, `next-pwa`, `/m/`, `PWA_DIR_default` |
| `CLAUDE.md` | sections décrivant `/m/` et `pwa/` |

`UnifiedApp` ne rend plus que le desktop — cohérent, puisque tout terminal
mobile est redirigé en amont.

### Étape 4 — Tests

Ces tests encodent l'ancienne architecture et **doivent être mis à jour
délibérément**, jamais contournés ni supprimés en bloc :

- `tests/test_phase6_frontend.py` — `test_historical_pwa_coexists_with_unified_frontend`
  et les assertions sur `pwa/src` (lignes ~42-96). Remplace-les par la nouvelle
  réalité : redirection mobile, montage `/mobile/`, absence de `pwa/`.
- `tests/test_phase4_route_contract.py` — `EXPECTED_ROUTE_COUNT = 214` et
  `EXPECTED_OPENAPI_PATH_COUNT = 192` sont des égalités strictes. Retirer les
  routes `/m/*` et ajouter `/mobile/*` les fait bouger : **recalcule et mets à
  jour les constantes**, et justifie le delta dans la description de la PR.
- `tests/test_audit_architecture_truth.py` — suit `tools/audit_architecture_truth.py`.

Ajoute des tests neufs :

- redirection : UA iPhone sur `/` → 302 vers `/mobile/` ; UA desktop → 200 HTML ;
  UA iPad et tablette Android → desktop ; `?desktop=1` → cookie posé, plus de
  redirection.
- montage : `/mobile/` sert `web_mobile/index.html` ; `/mobile/js/api.js` sert le
  bon type MIME ; `web_mobile/` absent → pas de redirection, pas de crash.
- isolation (test de non-régression) : aucun fichier de `web_mobile/` ne
  contient d'import vers `web/src`, `pwa/src`, `frontend/src`, `jarvis_auth`, et
  `web_mobile/` ne contient ni `package.json` ni `node_modules`.

---

## PIÈGES — NE PAS TOUCHER

Ces occurrences du mot « pwa » **n'ont rien à voir** avec le répertoire `pwa/`.
Les modifier casse des choses sans rapport :

- **`database/migrations.py:688,703,711,720`** — `CHECK(source IN ('voice','pwa'))`.
  C'est une **valeur stockée en base**. Y toucher casse le schéma et les données
  existantes. **Interdit.**
- **`web/src/app/components/pwa/InstallPrompt.tsx`** et `NotificationsPrompt.tsx`
  — composants PWA **du desktop**, importés par `web/src/App.tsx`. À conserver.
- **`web/vite.config.ts`** (`vite-plugin-pwa`) et **`web/package.json`** — PWA du
  build Vite desktop. À conserver.
- **`jarvis/cognitive/ollama_guard.py:159`** — `"pwa"` figure dans une liste de
  répertoires exclus du scan statique. Retire `"pwa"` **et ajoute `"web_mobile"`**,
  sinon le nouveau JS sera scanné inutilement.

Autres règles :

- Ne touche pas au backend métier, aux agents, aux prompts, à la base.
- Ne « corrige » pas d'autres bugs croisés en passant — le §12 du cahier des
  charges en liste quatre (route `/cognitive` en 404, CSS `.debug-prompt`
  manquante, `cartographyMap.css` orphelin, Service Worker Workbox hérité dans
  `web/dist/`). **Hors périmètre.** Mentionne-les dans la PR, ne les traite pas.
- Si tu découvres un blocage qui remet en cause le cahier des charges : arrête,
  documente précisément, ne pars pas dans une solution de contournement
  silencieuse.

---

## VÉRIFICATION AVANT PR

Toutes ces commandes doivent passer. Reporte les résultats réels — si quelque
chose échoue, dis-le avec la sortie, ne le masque pas.

```bash
source venv/bin/activate && python -m pytest tests/ -q
```

```bash
pnpm --dir frontend install --frozen-lockfile && pnpm --dir frontend test && pnpm --dir frontend typecheck && pnpm --dir frontend build
```

```bash
pnpm --dir web install --frozen-lockfile && pnpm --dir web test && pnpm --dir web build
```

Le build `frontend` est le juge de paix de l'étape 3 : s'il passe, la
suppression de `pwa/` est complète.

Vérification fonctionnelle, serveur lancé :

```bash
curl -s -o /dev/null -w "%{http_code} %{redirect_url}\n" -H "User-Agent: Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1" http://localhost:8081/
```

Attendu : `302` vers `/mobile/`. Avec un UA desktop : `200`.

Vérifie ensuite le rendu réel de `/mobile/` dans un navigateur en viewport 390×844,
en mode sombre : pas de scroll horizontal, pas de zoom involontaire au focus
d'un champ, rien masqué par l'encoche ni par la barre d'accueil, cibles
tactiles ≥ 44 px. Fournis des captures dans la PR.

**Critère d'acceptation final** (§11 du cahier des charges) : déverrouillage par
PIN → question dans le chat avec réponse en streaming → question dictée avec
réponse audible → briefing, tâches et mails du jour visibles → une tâche cochée.

---

## PULL REQUEST

`main` est protégée. Aucun commit direct, aucun push sur `main`.

```bash
git push -u origin feat/web-mobile-standalone
```

```bash
gh pr create --base main --title "feat(mobile): interface web_mobile autonome et suppression de pwa/" --body-file <fichier>
```

Le corps de la PR doit contenir :

1. **Le problème** — les trois causes ci-dessus, chiffrées (5 écrans mobiles sur
   21 routes, redirection morte, `/m/` en 404).
2. **La solution** — `web_mobile/` vanilla isolé, redirection serveur,
   suppression de `pwa/`.
3. **Le périmètre** — écrans livrés : Chat, Voix, Dashboard, Tâches, Mails.
   Emplacement Sport/Santé réservé et vide. Hors périmètre : carte, contacts,
   recherche, analytics, journal, mémoire, monitoring, logs, cognitive,
   control ; pas de Service Worker, pas d'offline, pas de push.
4. **Les constantes de test modifiées** — `EXPECTED_ROUTE_COUNT`,
   `EXPECTED_OPENAPI_PATH_COUNT` : ancienne valeur, nouvelle valeur, et la
   raison exacte du delta.
5. **Rollback** — `git revert` restaure `pwa/` intégralement (50 fichiers suivis).
6. **Résultats de vérification** — sorties réelles des commandes ci-dessus,
   captures d'écran mobile.
7. **Limites et défauts connus laissés en l'état**, dont les quatre du §12.

Fin du corps de PR :

```
🤖 Generated with [Claude Code](https://claude.com/claude-code)
```

Fin de chaque message de commit :

```
Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
```

Ne fusionne pas la PR toi-même — laisse la revue se faire.
