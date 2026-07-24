# Rapport de campagne — Cohérence architecture & frontend canonique

**Période** : 15–16 juillet 2026  
**Dépôt** : `AVTAVANTTOUT2/JarvisAPI` (workspace local `/Users/zeldris/JARVIS`)  
**Périmètre** : quatre missions successives demandées dans la même campagne  
**Verdict global** : **OBJECTIFS ATTEINTS AVEC LIMITATIONS DOCUMENTÉES**

---

## 1. Ce qui a été demandé

La campagne comprenait **quatre demandes** chaînées. Chacune s’appuyait sur le résultat de la précédente.

| # | Demande | Objectif demandé |
|---|---------|------------------|
| **M1** | Audit de cohérence DB + frontends | Départager contradictions doc (44 vs 73 tables ; Next 15 vs Vite ; PWA Next 14) sans toucher à la logique métier |
| **M2** | Alignement supervisor ↔ frontend canonique | Faire servir au port `9000` la même UI prioritaire que FastAPI (`frontend/out` > `web/dist`) |
| **M3** | Validation fonctionnelle & visuelle via `:9000` | Prouver que le frontend Next 15 est réellement utilisable derrière le supervisor (routes, auth session, chat, WS, SSE, responsive, anomalies classées) |
| **M4** | Complément ciblé | Fermer les 4 zones laissées ouvertes par M3 : LockGate (env isolé), chat secondaire, responsive 768/1024, console/réseau des 12 routes |

---

## 2. Synthèse exécutive

Le frontend canonique Next.js 15 est **identifié**, **servi aussi par le supervisor**, et **validé fonctionnellement** via `http://localhost:9000`. Les blocages P0/P1 du chemin supervisor (WebSocket, CSRF Host, SSE, tempête de sockets) ont été **trouvés, corrigés et retestés**. Les scénarios Auth LockGate ont été validés sur une **instance isolée** (jamais le secret production). Il reste des écarts **P3 backend** (stats dashboard, PATCH conversation inexistante) et des limites normales (micro physique, fiches contacts personnelles).

| Mission | Statut | Verdict local |
|---------|--------|----------------|
| M1 Audit | **FAIT** | Source de vérité établie (70 / 75 tables ; `frontend/` canonique) |
| M2 Alignement supervisor | **FAIT** | `next_canonical` sur `:9000` (ADR-019) |
| M3 Validation via supervisor | **FAIT** | `VALIDATED_WITH_LIMITATIONS` |
| M4 Complément ciblé | **FAIT** | 4/4 zones **PASS** |

---

## 3. Mission 1 — Audit de cohérence

### Demande
Répondre factuellement : combien de tables réellement après `init_db()` ? quel frontend est servi en priorité ? rôle de `web/`, `pwa/`, `tv/`, `front_tv/` ? corriger la doc conflictuelle ; produire un contrôle automatique.

### Réalisé
| Fait vérifié | Valeur |
|---|---|
| Tables persistantes après `init_db()` | **70** |
| Tables physiques avec FTS5 | **75** |
| Origine « 44 » | dump `database/schema.sql` — **non exécuté** par `init_db()` |
| Origine « 73 » | inventaire Architecture obsolète |
| Frontend canonique | `frontend/` — Next.js **15.5.20** / React **19.2.7** → `frontend/out` |
| Fallback desktop | `web/dist` — Vite |
| PWA historique | `pwa/` sous `/m/` (Next 14) |
| TV | `tv/` port **5174** (processus séparé) |
| Orphelin | `front_tv/` non référencé |

### Livrables M1
- `Architecture/32_FRONTEND_DATABASE_SOURCE_OF_TRUTH.md`
- `tools/audit_architecture_truth.py` → `artifacts/architecture_truth.json`
- `tests/test_audit_architecture_truth.py`
- Alignements doc (README, CLAUDE, Architecture, ADR-017, en-tête `schema.sql`)

### Non fait (hors scope demandé)
- Aucune migration schéma, aucun refactor agents/daemons.

---

## 4. Mission 2 — Alignement supervisor

### Demande
Avant : le supervisor `:9000` ne servait que `web/dist`. Après : même priorité que FastAPI (`frontend/out` → `web/dist` → 503 explicite), avec diagnostic et tests.

### Réalisé
- Module partagé `core/frontend_resolution.py` (`next_canonical` / `vite_fallback` / `missing`)
- Montage HTTP `core/frontend_static.py`
- Branchement `supervisor.py` + `api/frontend.py`
- Diagnostic : `GET /api/supervisor/status` → bloc `frontend`
- ADR : `Architecture/adr/ADR-019-SUPERVISOR-FRONTEND-PRIORITY.md`
- Tests : `tests/test_frontend_resolution.py`, `tests/test_supervisor_frontend.py`

### Non fait
- Suppression de `web/` (explicitement interdit / non demandé) — fallback conservé.

---

## 5. Mission 3 — Validation fonctionnelle via `:9000`

### Demande
Valider réellement (navigateur / HTTP) le frontend canonique derrière le supervisor : environnement, inventaire routes, accès directs & refresh, auth, pages centrales, WS/SSE, responsive, corrections frontend/serving autorisées, captures, rapport + JSON + handoff backend.

### Environnement observé
| Contrôle | Résultat |
|---|---|
| URL | `http://localhost:9000` |
| `frontend.selected` | `next_canonical` |
| Chemin servi | `frontend/out` |
| Routes exportées testées | **23/23** HTTP 200 |
| Route inconnue | **404 JSON** (pas de faux HTML) |

### Anomalies trouvées et traitées

| ID | Sévérité | Problème | Traitement |
|----|----------|----------|------------|
| VAL-01 | P0 | WS hardcodé `:8081` → chat muet via 9000 | **Corrigé** — same-origin + relais `/ws` supervisor |
| VAL-02 | P0 | Proxy réécrivait `Host` → 403 CSRF sur écritures | **Corrigé** — Host d’origine conservé |
| VAL-03 | P1 | SSE bufferisé → Mission Control mort | **Corrigé** — StreamingResponse supervisor |
| VAL-04 | P1 | Tempête reconnect `/ws/supervisor` | **Corrigé** — `ControlView.tsx` |
| VAL-05 | P1 | HTML sans `no-cache` | **Corrigé** — `core/frontend_static.py` |
| VAL-08 | P3 | Mock graphique contacts | **Corrigé** — état vide |
| VAL-10 | P2 | `supervisorWsUrl` forçait `:9000` | **Corrigé** — same-origin si déjà 9000 |
| VAL-09 | P3 | Dashboard stats 24 h à 0 | **Corrigé le 24/07/2026** — bornes `TIMEZONE` converties en UTC, tests heure d'été/hiver |
| VAL-06/07 | P3 | favicon / carte GPS | Ouverts / cosmétique |

### Parcours M3
| Parcours | Résultat |
|---|---|
| Auth session valide | PASS |
| Chat streaming bout-en-bout | PASS (après VAL-01/02) |
| Tâches create/delete | PASS |
| Navigation + refresh routes | PASS |
| WS `/ws` | PASS |
| WS `/ws/supervisor` | PASS (après VAL-04) |
| SSE `/api/events/stream` | PASS (après VAL-03) |
| Responsive 375 px | PASS |
| Auth LockGate setup/lock (prod) | **NON TESTÉ** en M3 (secret réel protégé) → repris en M4 |

### Livrables M3
- `Architecture/33_CANONICAL_FRONTEND_VALIDATION.md`
- `artifacts/frontend_validation.json`
- `.ai/workspaces/ws12/HANDOFF_TO_CURSOR.md` (CUR-01)
- Captures `artifacts/validation_screenshots/`
- Tests : `web/src/services/websocket.test.ts`, `frontend/src/lib/api-supervisor-ws.test.ts`

### Verdict M3
**VALIDATED_WITH_LIMITATIONS**

---

## 6. Mission 4 — Complément ciblé

### Demande
Fermer uniquement 4 zones, sans élargir le scope métier.

### Zone 1 — Auth LockGate (environnement isolé)
| Sous-parcours | Résultat |
|---|---|
| Écran setup (non configuré) | PASS |
| PIN trop court rejeté | PASS |
| Setup crée session | PASS |
| Mauvais secret → 401, reste verrouillé | PASS |
| Unlock + refresh conserve session | PASS |
| Logout + back sans flash privé | PASS (retest) |
| Session révoquée → LockGate | PASS |

**Moyen** : backend dédié `:8099`, DB `/tmp/jarvis_auth_test/test.db`, PIN de test uniquement (`validation-pin-2026`). Secret production **jamais** touché.

### Zone 2 — Chat secondaire
| Action | Résultat |
|---|---|
| Rename (+ refresh) | PASS |
| Pin / unpin | PASS |
| Archive | PASS |
| Delete | PASS |
| Switch A↔B sans mélange de messages | PASS (retest panneau messages ; 1er FAIL = faux positif sidebar) |

### Zone 3 — Responsive 768 & 1024
6 pages chacune (`/chat`, `/dashboard`, `/tasks`, `/contacts`, `/mission`, `/control`) : **tous PASS** (pas de scroll horizontal, page non vide).

### Zone 4 — Console / réseau (12 routes)
`/calendar`, `/map`, `/documents`, `/analytics`, `/search`, `/data`, `/logs`, `/voice`, `/voice-debug`, `/monitoring`, `/mobile`, `/control` : **tous PASS** (pas d’erreur console bloquante, pas d’appels hors-origine `:8081` / `:5173`).

### Écart backend découvert (non bloquant frontend)
| ID | Constat | Handoff |
|----|---------|---------|
| CUR-02 | `PATCH /api/conversations/99999999` → **200** au lieu de 404 | documenté |

### Livrables M4
- `artifacts/complement_report.json`
- Captures `artifacts/validation_screenshots/complement/` (38 fichiers)
- Scripts : `frontend/complement_validation.cjs`, `retest_back.cjs`, `retest_switch.cjs`
- Mise à jour doc 33 + JSON validation + handoff CUR-02

### Verdict M4
**PASS** sur les 4 zones. Verdict global inchangé : `VALIDATED_WITH_LIMITATIONS` (audio physique + CUR-01/CUR-02).

---

## 7. Corrections de code (fichiers touchés)

| Fichier | Raison |
|---|---|
| `core/frontend_resolution.py` | Résolution desktop partagée |
| `core/frontend_static.py` | Montage assets + cache HTML |
| `supervisor.py` | Frontend prioritaire, relais `/ws`, Host, SSE stream |
| `api/frontend.py` | Alignement résolution FastAPI |
| `web/src/services/websocket.ts` | WS same-origin |
| `web/src/app/components/views/ControlView.tsx` | Stop tempête reconnect |
| `web/src/app/components/views/ContactsView.tsx` | Suppression mock |
| `frontend/src/lib/api.ts` | `supervisorWsUrl` same-origin |
| Docs Architecture / README / CLAUDE / ADR | Alignement vérité |

**Hors périmètre respecté** : pas de suppression de `web/`, pas de migration schéma, pas de modification agents/daemons/métier LLM, pas de manipulation du PIN prod.

---

## 8. Ce qui reste ouvert

| ID | Type | Description | Sévérité |
|----|------|-------------|----------|
| CUR-01 | Backend | Stats dashboard « dernières 24 h » à 0 malgré messages récents | P3 |
| CUR-02 | Backend | PATCH conversation inexistante → 200 | P3 |
| VAL-06 | Frontend build | `favicon.ico` absent | P3 |
| VAL-07 | Données / env | Carte Leaflet non vérifiée avec GPS réelle | P3 |
| — | Environnement | Micro `/voice` non testé en headless | Limite |
| — | Product | Start/stop services critiques via `/control` non déclenchés | Limite |

---

## 9. Inventaire des preuves

### Documentation
1. `Architecture/32_FRONTEND_DATABASE_SOURCE_OF_TRUTH.md` — vérité DB/frontends  
2. `Architecture/adr/ADR-019-SUPERVISOR-FRONTEND-PRIORITY.md` — décision serving `:9000`  
3. `Architecture/33_CANONICAL_FRONTEND_VALIDATION.md` — validation + complément  
4. `.ai/workspaces/ws12/HANDOFF_TO_CURSOR.md` — CUR-01, CUR-02  
5. **Ce rapport** : `Architecture/34_RAPPORT_CAMPAGNE_FRONTEND_16-07-2026.md`

### Rapports machine
- `artifacts/architecture_truth.json`
- `artifacts/frontend_validation.json`
- `artifacts/complement_report.json`

### Captures
- `artifacts/validation_screenshots/` (validation M3)
- `artifacts/validation_screenshots/complement/` (M4)

### Tests automatisés (état à clôture)
- Vitest `frontend/` : **13 passed**
- Vitest `web/` : **22 passed**
- Pytest : `test_audit_architecture_truth`, `test_frontend_resolution`, `test_supervisor_frontend`

---

## 10. Verdict final

**La demande initiale de départager la vérité architecture (M1) et le chaînage demandé ensuite (M2→M4) sont satisfaits.**

Formulation opérationnelle à retenir :

> Le projet a **71 tables persistantes** (76 avec FTS). Le frontend canonique est **`frontend/`** (Next 15 → `frontend/out`), servi en priorité par FastAPI **et** le supervisor `:9000`. Ce frontend est **réellement utilisable** via le supervisor après correction des défauts de routage/proxy/WS. Auth LockGate validée sur instance isolée. Il reste des bugs backend P3 (CUR-01, CUR-02) et des limites d’environnement (micro, données GPS).

**Verdict machine consolidé** : `VALIDATED_WITH_LIMITATIONS`

---

*Rapport rédigé le 16 juillet 2026 — campagne 15–16/07/2026.*
