<!--
source_agent: bc-019fb874-dfa3-7a38-bf46-e471cc3f8282
agent_name: Audit frontend bureau jarvis_auth
agent_url: https://cursor.com/agents/bc-019fb874-dfa3-7a38-bf46-e471cc3f8282
agent_status: ERROR
created_at: 2026-07-31T13:55:00.731000+00:00
extracted_msg_index: 135
extracted_at: 2026-07-31T14:37:19.333188+00:00
-->

# AUDIT P14 — Frontend bureau et jarvis_auth

```yaml
id_perimetre: P14
nom: Frontend bureau et jarvis_auth
mode: lecture_seule
date: 2026-07-31
branche: elias/fitness-meal-ai-photo-8e4f
commit: 2191bf36
inclus:
  - frontend/src|public|e2e|configs (hors out/)
  - web/src + package.json + vite.config.ts + index.html + sw.ts
  - jarvis_auth/
exclus:
  - web_mobile/ → P15
  - frontend/out/, web/dist/
  - FitnessView métier → P13 (shell route seule notée)
```

---

## Checklist

| # | Critère | Statut | Preuve |
|---|---------|--------|--------|
| 1 | Aucun `fetch` hors `api.ts` / AuthClient | **PASS** | Seul `fetch` app : `frontend/src/lib/api.ts:97` ; auth : `jarvis_auth/src/client.ts:68` ; SW : `frontend/public/sw.js:23` |
| 2 | LockGate fail-closed (enfants non montés) | **PASS** | `LockGate.tsx:50` — children seulement si `!loading && authenticated && !softLocked` |
| 3 | Auto-lock + `clearOfflineDB` | **PASS*** | Auto-lock `useLockGate.ts:62-67` ; purge IndexedDB **uniquement** logout `App.tsx:53` + `useLockGate.ts:107` (*soft lock ≠ purge) |
| 4 | ChatView : pas d’affichage `message.agent` | **FAIL** | `ChatView.tsx:879` rend `message.agent` en UI user |
| 5 | CSP MapLibre (blob + OpenFreeMap) | **PASS** | CSP serveur `worker-src blob:` + `tiles.openfreemap.org` ; style `next.config.js:23-24` |
| 6 | SW ne cache pas `/api` / données perso | **PASS** (canonique) / **PARTIAL** (Vite) | `frontend/public/sw.js:16-17` OK ; `web/src/sw.ts:30-36` CacheFirst images sans denylist `/api` |
| 7 | pnpm pin `11.11.0` | **PASS** | `frontend/package.json:5`, `web/package.json:5` ; contrat `tests/test_pnpm_contract.py` |

Compléments mission :

| Critère | Statut |
|---------|--------|
| UnifiedApp / redirect mobile | **PASS** — desktop only `UnifiedApp.tsx:16-23` ; redirect serveur `/mobile/` (hors P14 code, documenté) |
| Offline queue | **PASS** — `queue.ts` via `jarvisRawFetch` ; sync sur auth ; clear logout |
| A11y basique LockGate | **PARTIAL** — `aria-label` OK ; erreurs sans `role="alert"` ; Vite `user-scalable=no` |

---

## Findings

### P14-F01 — ChatView affiche le nom d’agent
| Champ | Valeur |
|-------|--------|
| Sévérité | **HAUTE** |
| Fichier | `web/src/app/components/views/ChatView.tsx` |
| Lignes | 204, 210, 344, **879** |
| Preuve | `{message.agent && <span className="font-mono">{message.agent}</span>}` — alimenté depuis WS `response` et historique |
| Impact | Fuite persona : l’utilisateur voit `school` / `coach` / `info`… |
| Reco | Ne plus mapper/afficher `agent` dans le chat user ; garder éventuellement en debug ops |

### P14-F02 — Mission Control expose les noms d’agents
| Champ | Valeur |
|-------|--------|
| Sévérité | **MOYENNE** |
| Fichier | `web/src/components/mission/AgentBar.tsx` |
| Lignes | 4–14, 67 |
| Preuve | Pills `orchestrator`, `school`, `coach`… en clair |
| Impact | HUD ops, pas le chat — viole quand même « jamais le mot agent » |
| Reco | Labels neutres (« Info », « École ») ou surface admin explicite |

### P14-F03 — SSE hors client HTTP unique
| Champ | Valeur |
|-------|--------|
| Sévérité | **MOYENNE** |
| Fichier | `web/src/pages/MissionControl.tsx` |
| Ligne | 18 |
| Preuve | `new EventSource("/api/events/stream")` |
| Impact | Contourne `api.ts` (cookie same-origin OK ; pas de CSRF sur GET) |
| Reco | Wrapper SSE dans `api.ts` / AuthClient pour centraliser auth-required |

### P14-F04 — SW Vite : CacheFirst images sans denylist `/api`
| Champ | Valeur |
|-------|--------|
| Sévérité | **MOYENNE** |
| Fichier | `web/src/sw.ts` |
| Lignes | 30–36 |
| Preuve | `request.destination === 'image'` → CacheFirst, aucun filtre `/api` |
| Impact | Risque futur si endpoint image perso ; **chemin canonique** `frontend/public/sw.js` non concerné |
| Reco | Exclure `/api/` (et `/upload`) dans le matcher, ou retirer la route |

### P14-F05 — Soft lock ne purge pas IndexedDB
| Champ | Valeur |
|-------|--------|
| Sévérité | **MOYENNE** (design) |
| Fichiers | `jarvis_auth/src/useLockGate.ts:104-108`, `web/src/App.tsx:50-53`, `web/src/lib/offline/db.ts:56-60` |
| Preuve | `onUnauthenticated` seulement si `authenticated === false` ; soft lock garde le cookie |
| Impact | File offline / readCache restent sur disque pendant soft lock (device partagé) |
| Reco | Documenter explicitement ; optionnel : clear aussi au soft lock si threat model device partagé |
| Note | Docstring `db.ts:56` dit « verrouillage/logout » — trop large vs code |

### P14-F06 — Zoom bloqué (shell Vite)
| Champ | Valeur |
|-------|--------|
| Sévérité | **MOYENNE** (a11y) |
| Fichier | `web/index.html` |
| Ligne | 5 |
| Preuve | `maximum-scale=1.0, user-scalable=no` |
| Impact | WCAG : empêche zoom ; layout Next (`frontend/src/app/layout.tsx`) n’a pas cette restriction |
| Reco | Retirer `user-scalable=no` / `maximum-scale` |

### P14-F07 — A11y LockGate incomplète
| Champ | Valeur |
|-------|--------|
| Sévérité | **BASSE** |
| Fichier | `jarvis_auth/src/LockGate.tsx` |
| Lignes | 133–160 |
| Preuve | `aria-label` présents ; erreurs en `<p>` sans `role="alert"` / `aria-describedby` |
| Reco | Lier erreurs au champ ; `role="alert"` |

### P14-F08 — Surfaces ops affichent `agent`
| Champ | Valeur |
|-------|--------|
| Sévérité | **BASSE** |
| Fichiers | `LogsView.tsx:164`, `MonitoringView.tsx:958`, `DataView.tsx:469-472` |
| Impact | Vues admin/monitoring — acceptable si hors parcours user quotidien |
| Reco | Confirmer classification « ops only » |

### P14-F09 — Worker MapLibre CSP non branché
| Champ | Valeur |
|-------|--------|
| Sévérité | **INFO** |
| Fichiers | `frontend/public/maplibre-gl-csp-worker.js`, `web/public/maplibre-gl-csp-worker.js` |
| Preuve | Aucun `setWorkerUrl` ; runtime = blob workers (autorisés CSP) |
| Reco | Documenter ou supprimer assets morts |

---

## Contrôles validés (détail)

**Fetch unique** — Vues desktop importent `@unified/lib/api` (= `frontend/src/lib/api.ts`). Pas de `api.ts` legacy sous `web/`. CSRF : `X-CSRF-Token` si méthode unsafe + token (`api.ts:92-96`, `client.ts:65-67`). Cookies : `credentials: 'include'`.

**LockGate** — Fail-closed à l’échec réseau (`useLockGate.ts:44-47` → `softLocked` + pas d’enfants). Tests : `frontend/src/lock-gate.test.tsx` (offline, pre-auth, auto-lock coupe services privés).

**UnifiedApp** — Plus de branche mobile client ; enregistre `/sw.js` ; monte `DesktopApp` sous LockGate via `web/src/App.tsx`.

**SW canonique** — Uniquement `/_next/static/` + `/icons/` ; early-return `/api`, `/ws`, `/upload`.

**pnpm** — `packageManager: pnpm@11.11.0` sur les deux manifests.

---

## Renvois hors périmètre

| Sujet | Renvoi |
|-------|--------|
| FitnessView validation métier / formulaires | **P13** — route shell `/fitness` sous LockGate uniquement (`App.tsx:74`) |
| `web_mobile/` auth/SW | **P15** |
| CSP source `security_headers.py` | Backend (consommée par Map/E2E ; pas dans INCLUS code) |
| Scripts `frontend/retest_*.cjs` / `complement_validation.cjs` | Hors `src|public|e2e|config` — raw `fetch` en harness manuel, non prod |

---

## Verdict

| Niveau | Compte |
|--------|--------|
| Critique | 0 |
| Haute | 1 (persona ChatView) |
| Moyenne | 5 |
| Basse | 2 |
| Info | 1 |

**Contrat sécurité cœur (LockGate fail-closed, `api.ts`+cookie+CSRF, SW unifié sans `/api`, pnpm 11.11.0, CSP MapLibre) : conforme.**

**Bloquant persona UI : FAIL checklist #4** — retirer l’affichage de `message.agent` dans ChatView avant de considérer P14 vert.

Aucune modification de code (audit lecture seule).