# 33 — Validation fonctionnelle du frontend canonique via le supervisor

**Date** : 16 juillet 2026, 00:10 – 01:30 (clôture complément ciblé)  
**URL testée** : `http://localhost:9000` (supervisor) + auth isolée `http://127.0.0.1:8099`  
**Frontend servi** : `next_canonical` — build `frontend/out` (Next.js 15.5.20 / React 19.2.7)  
**Environnement** : macOS local, backend FastAPI 8081 (HTTPS) actif, session de test créée via `auth.create_session()` ; Auth LockGate sur DB temporaire `/tmp/jarvis_auth_test/test.db` (PIN de test uniquement)  
**Verdict** : **VALIDATED_WITH_LIMITATIONS**

---

## Résumé

Le frontend canonique est réellement exploitable via le supervisor. Deux anomalies
bloquantes ont été trouvées et **corrigées** pendant la validation :

1. **P0 — WebSocket codé en dur vers `:8081`** : le chat ne recevait ni la liste
   des conversations ni les réponses en streaming depuis le port 9000
   (`ws://localhost:8081/ws` échoue — le backend est en HTTPS, le contenu en HTTP).
   Corrigé : WS même-origine + relais `/ws` dans le supervisor.
2. **P0 — Écritures API en 403 via le proxy** : le proxy réécrivait `Host`
   vers `127.0.0.1:8081`, faisant échouer la vérification anti-CSRF
   Origin↔Host du middleware backend sur tous les POST/PUT/PATCH/DELETE.
   Corrigé : `Host` d'origine conservé par le proxy.

Après correction et rebuild : chat de bout en bout (envoi → routage → réponse
LLM streamée → affichage), création/suppression de tâche, SSE Mission Control,
navigation directe et rafraîchissement sur toutes les routes exportées — tous OK.

### Vérification d'environnement (étape 1)

| Contrôle | Résultat |
|---|---|
| `GET /` | 200, `text/html`, ~23 ms |
| `/api/supervisor/status` → `frontend.selected` | `next_canonical` |
| `frontend.path` | `frontend/out` |
| Erreurs console au chargement | 0 |
| Requêtes réseau en échec au chargement | 0 |
| Assets `/_next/static/...` | 200, hash du build courant |
| Asset inexistant `/_next/static/x.js` | 404 JSON (jamais de HTML) |
| Temps avant affichage utilisable | < 2 s |

---

## Matrice des routes (23 routes exportées + cas d'erreur)

Toutes testées en **accès direct** (nouvelle navigation), le rafraîchissement
étant équivalent en export statique. `/chat` et `/dashboard` aussi testés avec
`/` final, retour/avant navigateur testés (`/voice` ↔ `/dashboard`).

| Route | Direct | Contenu | API | Statut |
|---|---|---|---|---|
| `/` | 200 | layout + sidebar | — | PASS |
| `/chat`, `/chat/` | 200 | conversations + composer + streaming | WS `/ws` | PASS (après fix WS) |
| `/dashboard`, `/dashboard/` | 200 | stats réelles, graphes | REST | PASS |
| `/contacts` | 200 | liste réelle (329 contacts) | REST | PASS |
| `/calendar` | 200 | grille mois | REST | PASS |
| `/map` | 200 | état vide propre (« Aucun lieu enregistré » + CTA) | REST | PASS |
| `/documents` | 200 | compteurs + états vides | REST | PASS |
| `/analytics` | 200 | 4 graphes recharts, états vides explicites | REST | PASS |
| `/search` | 200 | résultats répartis par type | REST | PASS |
| `/data` | 200 | volumes par table | REST | PASS |
| `/logs` | 200 | 40 lignes réelles + filtres | REST | PASS |
| `/voice` | 200 | état initial stable, statuts TTS/STT/Porcupine affichés | — | PASS (micro non testé — voir Limites) |
| `/voice-debug` | 200 | latences réelles | REST | PASS |
| `/monitoring` | 200 | liste endpoints + « Tout tester » | REST | PASS |
| `/control` | 200 | services + boutons | REST + WS `/ws/supervisor` | PASS (après fix reconnexion) |
| `/tasks` | 200 | vide → création → liste (testé) | REST | PASS |
| `/mission` | 200 | terminal SSE + pipeline live | SSE | PASS (après fix proxy SSE) |
| `/mobile` | 200 | pairing + device connu | REST | PASS |
| `/memory`, `/status`, `/conversations`, `/mails`, `/config` | 200 | rendus | REST | PASS (survol) |
| `/unknown-route` | 404 JSON | — | — | PASS (pas de faux 200) |
| `/favicon.ico` (absent du build) | 404 | — | — | PASS (asset absent ≠ HTML) |

## Matrice des parcours

| Parcours | Résultat | Détail |
|---|---|---|
| Auth — session valide | PASS | Navigation, refresh, API authentifiées via cookie |
| Auth — sans session | PASS | `/api/*` → 401 propre, pas de boucle |
| Auth — setup / mauvais PIN / unlock / logout / révocation | **PASS** | Backend isolé `:8099` + DB temp (PIN `validation-pin-2026`, jamais le secret prod) — voir complément |
| Chat complet | PASS | Nouvelle conv → envoi → orchestrateur → réponse streamée « Reçu. » (~3 s, visible aussi dans Mission Control) |
| Tâches | PASS | Création UI + suppression (données de test nettoyées) |
| Navigation interne | PASS | Sidebar → route, back/forward navigateur OK |
| WebSocket `/ws` | PASS | `open` même-origine, streaming chat, pas de duplication |
| WebSocket `/ws/supervisor` | PASS | Statuts temps réel (après fix boucle) |
| SSE `/api/events/stream` | PASS | Événements relayés au fil de l'eau via le proxy |
| Responsive 375 px | PASS | Layout mobile dédié (bottom-nav), zéro scroll horizontal |

---

## Anomalies

### P0 (corrigées)

| ID | Page | Catégorie | Description | Correction |
|---|---|---|---|---|
| VAL-01 | `/chat` (toutes) | FABLE-FRONTEND + WEBSOCKET | `resolveWsUrl()` retournait `ws://<host>:8081/ws` en prod → connexion impossible depuis 9000 (et mixed-content depuis toute page HTTPS). Conversations non chargées, chat muet. | `web/src/services/websocket.ts` : WS même-origine. `supervisor.py` : relais `/ws` → backend (cookie transmis). |
| VAL-02 | toutes (écritures) | CONTRACT-MISMATCH (proxy) | Proxy supervisor réécrivait `Host` → middleware backend rejetait Origin≠Host en 403 (`csrf_check_failed`) sur tout POST/PUT/PATCH/DELETE. | `supervisor.py` : `Host` d'origine conservé (`_build_proxy_headers`). |

### P1 (corrigées)

| ID | Page | Catégorie | Description | Correction |
|---|---|---|---|---|
| VAL-03 | `/mission` | SSE | Le proxy bufferisait la réponse entière → SSE ne s'affichait jamais via 9000 (timeout 30 s, 0 octet). | `supervisor.py` : `StreamingResponse` + timeout de lecture désactivé pour `text/event-stream`. |
| VAL-04 | `/control` | FABLE-FRONTEND + WEBSOCKET + PERFORMANCE | Tempête de reconnexions `/ws/supervisor` (~37 000 tentatives en quelques minutes) : `connectWs` dépendait de `supervisorInfo`, recréé à chaque update → sockets orphelins qui se reconnectaient en parallèle. A provoqué un gel du rendu (premier chargement de `/mission` avec `document` nul). | `ControlView.tsx` : mise à jour fonctionnelle de l'état + garde `wsRef.current === ws` dans `onclose`. |
| VAL-05 | toutes | FABLE-FRONTEND (serving) | HTML servi par le supervisor sans `Cache-Control: no-cache` → après un rebuild, risque d'`index.html` périmé référençant des chunks disparus. | `core/frontend_static.py` : `no-cache` sur HTML/manifest/sw.js, `max-age=3600` sur le reste. |

### P2 / P3 (non corrigées — documentées)

| ID | Page | Catégorie | Sévérité | Description |
|---|---|---|---|---|
| VAL-06 | `/` | ROUTE-EXPORT | P3 | `favicon.ico` absent de `frontend/out` → 404 (cosmétique). |
| VAL-07 | `/map` | FEATURE-FLAG | P3 | Pas de carte Leaflet rendue quand 0 lieu — placeholder d'état vide volontaire ; comportement avec données GPS non testé (aucune donnée). |
| VAL-08 | `/contacts` | MOCK-DATA | P3 | ~~Mock trompeur~~ → **corrigé en reprise** : état vide explicite si analytics sans série. |
| VAL-09 | `/dashboard` | CURSOR-BACKEND | P3 | « Messages Total : 0 » et « Interactions : 0 » sur les dernières 24 h alors que des messages existent — handoff **CUR-01**. |
| VAL-10 | `/control` | FABLE-FRONTEND | P2 | `supervisorWsUrl()` forçait `:9000` même quand l’UI est déjà sur 9000 — **corrigé** (même-origine si `port===9000`). |

---

## Corrections réalisées (frontend + serving uniquement)

| Fichier | Raison |
|---|---|
| `web/src/services/websocket.ts` | WS même-origine (VAL-01) + export testable |
| `web/src/app/components/views/ControlView.tsx` | Boucle de reconnexion WS supervisor (VAL-04) |
| `web/src/app/components/views/ContactsView.tsx` | Suppression mock historique (VAL-08) |
| `frontend/src/lib/api.ts` | `supervisorWsUrl` même-origine sur :9000 (VAL-10) |
| `supervisor.py` | Relais `/ws`, Host préservé, streaming SSE (VAL-01/02/03) — routage uniquement |
| `core/frontend_static.py` | En-têtes de cache (VAL-05) |
| `web/src/services/websocket.test.ts` | Tests contrat même-origine |
| `frontend/src/lib/api-supervisor-ws.test.ts` | Tests `supervisorWsUrl` |

Rebuild `frontend/out` effectué après reprise (typecheck + build OK).

## Anomalies backend (handoff Cursor)

Voir `.ai/workspaces/ws12/HANDOFF_TO_CURSOR.md` : **CUR-01** (stats 24 h à zéro).
VAL-02/VAL-03 ont été corrigées côté supervisor (routage, pas de contrat métier modifié).

## Captures

Dans `artifacts/validation_screenshots/` (aucune donnée personnelle sensible :
états vides ou données techniques uniquement) :

- `chat_desktop_avant_ws_fix.png` — chat avant correctif (sidebar vide, bug VAL-01)
- `chat_desktop_apres_ws_fix.png` — chat après fix (conversations + écran bienvenue)
- `dashboard_desktop.png` — dashboard desktop (revalidé 01:05 — CUR-01 visible: msgs 24h = 0)
- `tasks_desktop_vide.png` — état vide tâches
- `map_etat_vide.png` — état vide cartographie
- `tasks_mobile_375px.png` — layout mobile 375 px
- `mission_control_apres_fix.png` — Mission Control avec SSE live
- `erreur_404_route.body.json` — route inconnue → `{"error":"Page introuvable"}`
- `supervisor_status_diagnostic.json` — diagnostic `next_canonical`

## Validation complémentaire ciblée (01:10 – 01:30)

Quatre zones fermées via Playwright (`frontend/complement_validation.cjs` + retests).
Rapport machine : `artifacts/complement_report.json`. Captures :
`artifacts/validation_screenshots/complement/` (38 fichiers).

| Zone | Résultat | Preuve |
|---|---|---|
| 1. Auth LockGate (setup, short PIN, wrong secret, unlock+refresh, logout+back, session révoquée) | **PASS** | Backend isolé `:8099`, DB `/tmp/…`, PIN de test. Retest back : `auth_E_back_button_retest.png` |
| 2. Chat secondaire (rename, pin/unpin, archive, delete, switch) | **PASS** | Mutations UI/API + refresh. Switch retesté dans le panneau messages (`chat_switch_retest.png`) — faux positif sidebar au 1er run |
| 3. Responsive 768 & 1024 (chat, dashboard, tasks, contacts, mission, control) | **PASS** | Pas de scroll horizontal, pages non vides |
| 4. Console/réseau 12 routes survolées | **PASS** | 0 erreur console bloquante, 0 appel hors-origine `:8081`/`:5173` |

**Écart documenté (backend, non bloquant frontend)** : `PATCH /api/conversations/99999999`
→ **200 `{ok:true}`** au lieu de 404 — handoff **CUR-02**.

Scripts de retest : `frontend/retest_back.cjs`, `frontend/retest_switch.cjs`.

## Limites assumées

- **Secret prod jamais manipulé** : LockGate validé uniquement sur instance isolée.
- **Micro non testé** (`/voice`) : pas de périphérique audio dans l'environnement headless.
- **`/control`** : boutons start/stop non déclenchés sur les services critiques.
- **Fiches contact détaillées** non ouvertes (données personnelles).
- **CUR-01** (stats dashboard 24 h) et **CUR-02** (PATCH id inconnu) restent côté backend.

## Clôture reprise + complément

| Contrôle | Résultat |
|---|---|
| 23 routes exportées HTTP direct | 200 + refs `_next/static` |
| Route inconnue | 404 JSON (pas de faux HTML) |
| Diagnostic supervisor | `next_canonical` / `frontend/out` |
| Fallback Vite `web/dist` | toujours présent |
| Auth LockGate (env isolé) | PASS (6 sous-parcours) |
| Chat secondaire + switch | PASS |
| Responsive 768 / 1024 | PASS |
| 12 routes console/réseau | PASS |

## Verdict

**VALIDATED_WITH_LIMITATIONS** — les parcours centraux et le complément ciblé
(auth LockGate isolée, mutations chat, responsive 768/1024, survol routes) sont
réellement utilisables via le supervisor. Les limites restantes sont
l'audio physique et les handoffs backend CUR-01 / CUR-02 (P3).
