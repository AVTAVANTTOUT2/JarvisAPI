<!--
source_agent: bc-019fb8a4-40be-72b3-9274-9b03856ce253
agent_name: P15 web mobile audit
agent_url: https://cursor.com/agents/bc-019fb8a4-40be-72b3-9274-9b03856ce253
agent_status: IDLE
created_at: 2026-07-31T14:46:46.277000+00:00
extracted_msg_index: 143
extracted_at: 2026-07-31T15:02:18.219625+00:00
-->

# AUDIT — P15 — web_mobile

## Métadonnées
- Agent / modèle : Cloud Agent P15 (`bc-019fb8a4-40be-72b3-9274-9b03856ce253`) / Composer (Auto)
- Date : 2026-07-31
- Commit audité (`git rev-parse HEAD`) : `2a6e0adc528aed4723d3a0477e17667867945f77`
- Branche : `main`
- Fichiers dans le périmètre (count) : 17 (`web_mobile/` ×16 + `api/web_mobile.py`)
- Fichiers lus (count) : 18 (+ `tests/test_web_mobile.py` pour contrats)
- Couverture estimée : 100%

## Synthèse exécutive
`web_mobile/` est une SPA vanilla isolée, fail-closed sur la session, sans import React/CDN, avec CSRF cookie + WS same-origin. Les contrats structurels (isolation, CSP self, safe-area, PIN 4–12) tiennent. Le trou majeur est l’auth **PIN-only** : une passphrase bureau (≥10 car.) est impossible à saisir/déverrouiller sur mobile. Le soft-lock idle ne révoque pas la session (rechargement = bypass), et `api.logout` n’est branché nulle part. Fitness (`health.js`) parle correctement à `/api/fitness/dashboard|progress|meals|water|weights|advice|program`, mais réplique le piège `Number("")` des réglages et laisse morts les helpers IA repas/photo/wellbeing. Serving HTTP sain (MIME, traversal, no-cache) — ownership P03.

## Findings

### F-P15-001
- Sévérité : HIGH
- Type : contrat-cassé | sécurité
- Titre : Déverrouillage mobile PIN-only — passphrase bureau impossible
- Preuve : `web_mobile/js/auth.js:18-19,63-88,149`
```
const MIN_PIN = 4;
const MAX_PIN = 12;
const layout = ['1','2','3','4','5','6','7','8','9','OK','0','⌫'];
…
await api.unlock(entered);
```
- Impact : si le secret est une passphrase (politique `auth.validate_secret_strength`, 10+ car. non purement numériques), l’utilisateur ne peut ni déverrouiller ni configurer depuis le téléphone. Accès mobile refusé pour ce profil.
- Repro / condition : secret configuré via LockGate bureau en passphrase → ouvrir `/mobile/` → pavé chiffres seul.
- Correctif proposé (sans coder) : mode texte (`type=password`) en plus du pavé, ou bascule « Code alphanumérique » ; aligner messages setup sur PIN **ou** passphrase ; plafonner/documenter MAX côté auth si PIN digits-only.
- Confiance : haute

### F-P15-002
- Sévérité : MEDIUM
- Type : bug
- Titre : Soft-lock idle sans révocation session + rechargement contourne le verrou
- Preuve : `web_mobile/js/app.js:141-165,168-184` + `web_mobile/js/auth.js:237-261`
```
relockInFlight = (async () => {
  sessionOpen = false;
  …
  ws.disconnect();
  …
  await lock(reason);  // n'appelle pas api.logout
  await start();
})()
…
if (st.authenticated) { el.root.hidden = true; return; }
```
- Impact : après inactivité, l’UI est masquée mais le cookie `jarvis_session` reste valide. Un refresh (ou nouvel onglet) → `requireSession` voit `authenticated:true` → app + WS sans re-saisie. Soft-lock cosmétique (même famille que le soft lock bureau, mais mobile n’a même pas de bouton logout).
- Repro / condition : session ouverte → attendre auto-lock → recharger la page.
- Correctif proposé (sans coder) : sur idle, appeler `api.logout()` **ou** conserver soft-lock + `verify` (comme jarvis_auth) **et** exposer une action « Verrouiller / Déconnecter » ; ne pas court-circuiter le lock si un flag soft-lock local est posé.
- Confiance : haute

### F-P15-003
- Sévérité : MEDIUM
- Type : bug
- Titre : Réglages fitness — `Number(node.value)` transforme champ vide en `0`
- Preuve : `web_mobile/js/views/health.js:450-460`
```
const payload = {};
for (const [key, node] of fields) payload[key] = Number(node.value);
payload.reminder_time = reminderTime.value;
…
await api.patch('/api/fitness/program', payload);
```
- Impact : champ vidé → `0` envoyé (`calories_*` / `protein_*` acceptent `ge=0`) ; `reminder_interval_min: 0` ou `reminder_time: ""` → 422. Corruption silencieuse possible des cibles nutritionnelles (même classe que P13 desktop).
- Repro / condition : Fitness → Objectifs → vider « Calories min » → Enregistrer.
- Correctif proposé (sans coder) : omettre les clés vides ; `Number` seulement si `value.trim() !== ''` ; valider min/max côté UI avant PATCH.
- Confiance : haute

### F-P15-004
- Sévérité : MEDIUM
- Type : dead-code | contrat-cassé
- Titre : Helpers fitness api.js non branchés (IA repas / photo / wellbeing / summary)
- Preuve : `web_mobile/js/api.js:138-145` vs usage exclusif de chemins bruts dans `health.js:97,112,148,166,314,389,460,509`
```
fitnessSummaryToday, createWorkout, createMealFromText,
createMealFromPhoto, createWellbeing  // jamais appelés depuis views/
```
- Impact : surface API client morte ; fonctionnalités fitness IA (from-text / from-photo) absentes du mobile alors que le client les déclare ; divergence desktop/mobile.
- Repro / condition : grep des symboles dans `web_mobile/js/views/` → 0 hit.
- Correctif proposé (sans coder) : soit brancher l’UI (saisie libre / photo), soit retirer les helpers morts pour éviter une fausse parité.
- Confiance : haute

### F-P15-005
- Sévérité : LOW
- Type : smell
- Titre : `api.logout` exposé mais aucune UI de déconnexion
- Preuve : `web_mobile/js/api.js:121` ; aucun appel dans `app.js` / vues
- Impact : impossible de révoquer la session depuis le téléphone (vol/prêt d’appareil) sans attendre TTL/inactivité serveur.
- Repro / condition : parcourir les 6 onglets — pas d’action logout.
- Correctif proposé (sans coder) : action header « Verrouiller » → `logout` + `relock('expired')`.
- Confiance : haute

### F-P15-006
- Sévérité : LOW
- Type : smell
- Titre : PIN mobile plafonné à 12 chiffres alors que le backend n’a pas de max digits
- Preuve : `web_mobile/js/auth.js:19,104-109` (`MAX_PIN = 12`, auto-submit au plafond)
- Impact : un PIN digits >12 défini hors mobile (si jamais accepté) est inutilisable ici ; edge case.
- Repro / condition : secret digits length > 12.
- Correctif proposé (sans coder) : aligner max auth serveur ↔ mobile, ou relever `MAX_PIN`.
- Confiance : moyenne

### F-P15-007
- Sévérité : LOW
- Type : smell
- Titre : Aujourd’hui — cases « tâches en retard » non interactives
- Preuve : `web_mobile/js/views/today.js:90-98` (`h('span', { class: 'box' }, icon('check'))` sans handler)
- Impact : affordance de coche trompeuse ; l’utilisateur croit pouvoir terminer depuis Aujourd’hui.
- Repro / condition : avoir une tâche en retard → ouvrir Aujourd’hui → tap case.
- Correctif proposé (sans coder) : retirer la case ou brancher `api.updateTask` / naviguer vers Tâches.
- Confiance : haute

### F-P15-008
- Sévérité : INFO
- Type : doc-drift
- Titre : Cahier des charges §8 obsolète (noms de fichiers / fitness « vide »)
- Preuve : `Architecture/35_CAHIER_DES_CHARGES_WEB_MOBILE.md:177-181` (`dashboard.js`, `fitness.js # vide`) vs repo `today.js` + `health.js` (611 lignes)
- Impact : onboarding auditeur / doc interne trompeuse ; pas d’impact runtime.
- Repro / condition : lire le cahier vs arborescence actuelle.
- Correctif proposé (sans coder) : mettre à jour l’arborescence et le statut fitness.
- Confiance : haute

### F-P15-009
- Sévérité : INFO
- Type : smell
- Titre : Cache-bust `?v=` uniquement sur l’import `health.js`
- Preuve : `web_mobile/js/app.js:18` vs imports sans query lignes 13-17 ; `index.html:14,56`
- Impact : incohérence de versioning modules ES ; risque faible de cache partiel si un jour double import.
- Repro / condition : lecture `app.js`.
- Correctif proposé (sans coder) : uniformiser `?v=` sur tous les modules ou s’en remettre au `Cache-Control: no-cache` serveur.
- Confiance : haute

### F-P15-010
- Sévérité : INFO
- Type : smell
- Titre : Soft unlock idle appelle `unlock` (nouvelle session) au lieu de `verify`
- Preuve : `web_mobile/js/auth.js:149` toujours `api.unlock` ; contraste jarvis_auth `verify` si softLocked
- Impact : rotation de session inutile à chaque idle unlock ; pas de faille directe.
- Repro / condition : idle → saisir PIN → `/api/auth/unlock` (pas `/verify`).
- Correctif proposé (sans coder) : si cookie encore valide, `POST /api/auth/verify` puis rouvrir l’UI.
- Confiance : haute

## Contrats vérifiés
| Contrat / invariant | Statut | Preuve |
|---|---|---|
| 1. Aucun import `web/src`, `frontend`, `jarvis_auth` | OK | Scan sources + `tests/test_web_mobile.py:244-254` ; imports relatifs `./` / `../` uniquement |
| 2. Aucune ressource distante (CSP self) | OK | Seuls URI SVG `http://www.w3.org/2000/svg` (`ui.js:32-36`) ; test `test_no_remote_resources` ; CSS SF système (`app.css:23-24`) |
| 3. Session avant montage vues / WS | OK | `sessionOpen=false` (`app.js:48`) ; `render()` early-return (`110-111`) ; `requireSession()` avant `start()` (`194-195`) ; `ws.connect()` seulement dans `start()` (`181`) |
| 4. PIN only vs passphrase | KO | Pavé digits only (`auth.js:63-88`) ; pas de champ password ; F-P15-001 |
| 5. health.js contrats API fitness | OK* | `GET /dashboard`, `PUT …/progress`, `POST water/meals/weights/advice`, `PATCH program` + `sessions/{id}` alignés `app/fitness/routes.py` ; source `'pwa'` ∈ `FitnessSource` ; *piège Number — F-P15-003 ; helpers morts — F-P15-004 |
| 6. CSRF sur mutations cookie | OK | `UNSAFE` + `X-CSRF-Token` (`api.js:29,63,71,83-86`) ; refresh via `/api/auth/status` ; auth routes exclues |
| 7. Fail-closed auth UI | OK | `#app[hidden]` jusqu’à `start()` ; `relock` vide DOM + `setCsrfToken(null)` (`app.js:147-159`) |
| 8. XSS DOM (pas d’innerHTML user/LLM) | OK | `h()` → `textContent` / `createTextNode` (`ui.js:18-25`) |
| 9. Chat/voix via `/ws` (pas `/api/mobile/*`) | OK | `ws.js:45-47` ; commentaire Bearer Android (`ws.js:1-7`) |
| 10. Action refuse → `action_cancel` | OK | `chat.js:292` + `ws.js:150` ; test `test_action_refusal…` |
| 11. Voix `done_playing` même sans audio | OK | `voice.js:272-278` → `finishPlayback` → `donePlaying` |
| 12. Serving MIME / traversal / no-cache | OK (frontière P03) | `api/web_mobile.py:124-128,160-171` |
| 13. Safe-area + font 16px champs | OK | `app.css:29-30,160` ; tests 281-290 |
| 14. Pas d’affichage `message.agent` | OK | Aucune occurrence dans `chat.js` |
| 15. Tests structurels couvrent passphrase / sessionOpen / fitness | KO (→ P18) | `test_web_mobile.py` : isolation/CDN/PIN/WS OK ; **aucun** test passphrase, logout, `sessionOpen`, ni chemins `health.js` |

## Frontières / dépendances
- Signale vers **P03** : ownership serving `api/web_mobile.py` (MIME, traversal, redirect UA, cookie desktop) — findings serving déjà couverts P03 ; ici comportement client seulement.
- Signale vers **P02** : politique secret PIN/passphrase (`auth.validate_secret_strength`) ; CSRF middleware ; codes WS 4401/4428.
- Signale vers **P04** : contrat messages WS (`chunk`, `action_pending`, `speech_done`, `done_playing`).
- Signale vers **P13** : mêmes endpoints fitness + même piège `Number("")` ; UI mobile = clone fonctionnel partiel.
- Signale vers **P14** : contraste LockGate passphrase + soft-lock/`verify` ; mobile n’importe pas `jarvis_auth` (voulu).
- Signale vers **P18** : trous de couverture `tests/test_web_mobile.py` (passphrase, fail-closed `sessionOpen`, CSRF header, contrats `health.js`, logout) ; test `test_unknown_extensions_are_refused` écrit temporairement dans le vrai `web_mobile/`.
- Attendus de ce périmètre consommés ailleurs : redirect `/` → `/mobile/` (frontend setup) ; CSP `default-src 'self'` (middleware) ; cookie session SameSite=Strict.

## Fichiers non lus
| Fichier | Motif |
|---|---|
| *(aucun du périmètre inclus)* | — |
| Contenu binaire pixels des PNG au-delà IHDR | Métadonnées/taille vérifiées (192×192, 512×512) ; pixels non audités ligne à ligne |

## Couverture
- Liste exhaustive des fichiers lus (chemins relatifs), triée :
  - `api/web_mobile.py`
  - `tests/test_web_mobile.py` (contrats ; findings tests → P18)
  - `web_mobile/app.css`
  - `web_mobile/icons/icon-192.png`
  - `web_mobile/icons/icon-512.png`
  - `web_mobile/index.html`
  - `web_mobile/js/api.js`
  - `web_mobile/js/app.js`
  - `web_mobile/js/auth.js`
  - `web_mobile/js/ui.js`
  - `web_mobile/js/views/chat.js`
  - `web_mobile/js/views/health.js`
  - `web_mobile/js/views/mails.js`
  - `web_mobile/js/views/tasks.js`
  - `web_mobile/js/views/today.js`
  - `web_mobile/js/views/voice.js`
  - `web_mobile/js/ws.js`
  - `web_mobile/manifest.webmanifest`