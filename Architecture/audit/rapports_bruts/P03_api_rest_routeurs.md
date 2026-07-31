<!--
source_agent: bc-019fb866-2ab1-7b98-ae0d-6de5e08698b3
agent_name: Audit api routeurs
agent_url: https://cursor.com/agents/bc-019fb866-2ab1-7b98-ae0d-6de5e08698b3
agent_status: IDLE
created_at: 2026-07-31T13:38:51.453000+00:00
extracted_msg_index: 106
extracted_at: 2026-07-31T14:37:19.332505+00:00
-->

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━  
**RAPPORT D’AUDIT — P03 API REST routeurs**  
Mode : **lecture seule** · Date : 2026-07-31  
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

```yaml
schema_version: "1.0"
id_perimetre: P03
nom: "API REST routeurs"
verdict: CONDITIONAL_PASS
resume: >
  Frontières Phase 4 structurelles tenues (modules ≤500, aucun import api→main,
  serving /mobile|/m cohérent, anti-traversal outputs/mobile/backups OK, 404
  rowcount sur CRUD critiques). Échecs majeurs : fuites d’exceptions internes
  massives (str(e) au client) et dérive du fingerprint OpenAPI/routes Phase 4
  (232/206 mesurés vs 221/197 verrouillés). CSRF cookie délégué correctement
  au middleware (P02), hors bypass token documentés.
```

---

### MÉTA / PÉRIMÈTRE

| Champ | Valeur |
|---|---|
| Inclus | 14 `router_*.py` (hors `router_auth`), 6 `misc_*.py`, `lifespan`, `frontend`, `web_mobile`, supports listés |
| Exclus | `middleware.py`, `router_auth.py` (P02) ; `ws_*`/`chat_*`/`voice_*` (P04) ; `app/fitness/routes.py` (P13) |
| Assemblage | `main.py` monte **15** `router_*.py` + fitness + `_setup_frontend` |
| Preuve runtime | `venv` import `main.app` OK |

---

### CHECKLIST

| # | Critère | Statut | Preuve |
|---|---|---|---|
| 1 | Auth attendue / CSRF mutations cookie | **PASS** (délégation) | Mutations cookie : CSRF via `api/middleware.py` (P02). Bypass documentés : `POST /api/location{,/batch}`, `POST /api/devices/register`, device heartbeat/screen/tts, `/api/mobile/*` Bearer. Handlers P03 re-vérifient jetons localement (location, devices, mobile). |
| 2 | rowcount / 404 UPDATE·DELETE | **PASS** (mineurs) | 404 OK : tasks, places, conversations, devices activate/rotate/revoke, life-profile, commitments, recordings assign. Écarts soft : `people/send` → HTTP 200 + `ok:false` ; control unknown → 200 + `ok:false` (pas 404). |
| 3 | Pas de fuite d’exception interne | **FAIL** | ≥25 sites `HTTPException`/`JSONResponse` avec `str(e)` / `f"...{e}"` exposés au client (liste findings). |
| 4 | Fichiers > 500 lignes | **PASS** | `oversized == {}` ; max P03 ≈ 467 (`router_location.py`). Contrat `test_phase4_architecture` aligné. |
| 5 | Redirect `/mobile/` et `/m/` | **PASS** (+écart vite) | UA téléphone + cookie `jarvis_force_desktop` + `?desktop=1` ; `/m`→`/mobile/` ; `/m/fitness`→`#/sante`. **Écart** : repli Vite, `GET /` n’appelle pas `remember_desktop_choice` → cookie desktop non posé. |
| 6 | Double définition de routes | **PASS** | Aucun doublon API method+path. Doublons `/` `/{segment}` dans `frontend.py` = branches mutuellement exclusives (Next / Vite / Jinja / mobile-only). |
| 7 | Path traversal static/backups | **PASS** | `misc_files.api_outputs_download` : `resolve`+`relative_to` ; `web_mobile` : idem + allowlist extensions ; restore : `candidate.parent != backup_dir` → refuse (frontière `scripts/db_maintenance.py`). |
| 8 | Fingerprint OpenAPI / routes | **FAIL** | Attendu `221` ops / `197` paths. Mesuré **232** / **206**. Signatures divergentes. Test `tests/test_phase4_route_contract.py` **échouerait** (non modifié, P18). |

---

### FINDINGS

#### F-P03-001 — Fuite d’exceptions internes au client  
- **Sévérité** : HIGH  
- **OWASP** : A05  
- **Fichiers** (échantillon) : `router_people.py:89,122,146,229,250,277,301` ; `people_chat.py:200` ; `router_tasks.py:52` ; `router_recordings.py:25` ; `router_daemon.py:79` ; `router_devagent.py:103` ; `misc_integrations.py:173,320` ; `misc_life.py:156` ; `misc_relationships.py:101,182,263` ; `misc_status.py:91` (`/api/status` → `location.error`)  
- **Constat** : `except Exception as e: raise HTTPException(5xx, str(e))` (ou équivalent JSON) renvoie chemins, SQL, stack métier.  
- **Remédiation** : message générique client + `logger.exception` ; codes stables (`internal_error`).

#### F-P03-002 — Contrat Phase 4 OpenAPI/routes obsolète  
- **Sévérité** : HIGH (intégrité de verrou)  
- **Preuve** :
  - Attendu : `EXPECTED_ROUTE_COUNT=221`, `EXPECTED_OPENAPI_PATH_COUNT=197`
  - Mesuré : `232` ops, digest `0ef71611…` ; `206` paths, digest `843420cd…`
- **Attribution** : hors P03 pour le contenu fitness (**20** ops `/api/fitness/*` → P13) ; dérive globale à recalibrer en P18. P03 contribue via surface mobile/cognitive déjà montée.  
- **Remédiation** : régénérer fingerprint après gel des routes (P18) — ne pas toucher le test ici.

#### F-P03-003 — Cookie desktop absent sur repli Vite  
- **Sévérité** : MEDIUM  
- **Fichier** : `api/frontend.py:203-217` (`serve_spa_root`)  
- **Constat** : branche Next appelle `remember_desktop_choice` ; branche Vite sur `/` non. `?desktop=1` évite la redirect une fois, mais sans cookie la visite suivante rebascule mobile.  
- **Remédiation** : wrap `FileResponse` comme sur le chemin unifié.

#### F-P03-004 — Codes HTTP soft au lieu de 404  
- **Sévérité** : LOW  
- **Sites** : `router_people.py:149-176` (`/send` contact inconnu → 200) ; `service_control.py:247` / wrappers `router_daemon.py` (service inconnu → 200 `ok:false`).  
- **Impact** : clients/OpenAPI ne peuvent pas s’appuyer sur 404 ; monitoring faussé.  
- **Remédiation** : `HTTPException(404, …)`.

#### F-P03-005 — Endpoint debug en surface OpenAPI  
- **Sévérité** : LOW (session-gated)  
- **Fichier** : `misc_integrations.py:36` via `router_misc.py` `GET /api/debug/resolve/{name}`  
- **Constat** : expose étapes de résolution handle ; protégé par session mais présent en prod OpenAPI.  
- **Remédiation** : `include_in_schema=False` + garde debug, ou retirer.

#### F-P03-006 — Corps `dict` sans modèle Pydantic (422 faible)  
- **Sévérité** : LOW / INFO  
- **Sites** : majorité des mutations tasks/people/location/mobile (`payload: dict`) → validation manuelle → **400**, rarement **422**.  
- **Contraste** : `router_cognitive.py` et `DocumentPrivacyUpdate` font correctement du 422.  
- **Remédiation** : modèles Pydantic sur mutations sensibles.

#### F-P03-007 — Segment SPA `"mobile"` dans allowlist bureau  
- **Sévérité** : INFO  
- **Fichier** : `frontend.py:38` `_SPA_SEGMENTS` contient `"mobile"`  
- **Constat** : si `web_mobile` absent, `/{segment}` peut servir le shell bureau sur `/mobile`. Avec `web_mobile` présent (cas nominal), routes exactes priment.  

---

### CONTRÔLES PASSANTS (à conserver)

| Contrôle | Détail |
|---|---|
| Import `main` | `offenders == []` sur tout `api/*.py` ; `lifespan` documente l’absence de dépendance |
| Taille modules | Tous `api/*.py` ≤ 500 |
| Path traversal | outputs + `/mobile/{asset}` + restore backups |
| Serving mobile | `/mobile/`, redirects `/m`, `/m/fitness`, UA, cookie, order `web_mobile.setup` avant attrape-tout |
| 404 CRUD | conversations / tasks / places / devices / recordings assign / life-profile / commitments |
| Location auth | rate-limit + Bearer / `X-Location-Token` ; batch Bearer only ; fail-closed si token absent (`503`) |
| Mobile chat/voice | `_require_mobile_device` ; messages d’erreur 500 génériques (bon modèle) |

---

### MATRICE AUTH / CSRF (synthèse P03)

| Classe d’endpoints | Auth | CSRF cookie |
|---|---|---|
| CRUD session (`/api/tasks`, conversations, people, quality, control, backups…) | Session (428/401) | Oui si cookie + UNSAFE |
| `POST /api/location`, `/batch` | Token location / Bearer | N/A (bypass session) |
| Device heartbeat/screen/tts | `X-Device-Token` | N/A |
| `POST /api/devices/register` | Pairing code | N/A |
| `/api/mobile/chat|voice|conversations` | Bearer mobile (handler) | N/A (bypass) |
| Static `/`, `/mobile/*` | Aucune (assets) | N/A |

---

### FRONTIÈRES (hors P03 — tracer seulement)

| Cible | Note |
|---|---|
| P02 `middleware.py` / `router_auth.py` | CSRF, gate session, whitelist Bearer, pairing mobile |
| P04 `ws_*` / `chat_*` / `voice_*` | Pipeline message ; `people_chat` / mobile chat appellent `pipeline` / `_process_message_internal` |
| P13 `app/fitness/routes.py` | 20 ops montées dans `main` — poids majeur du delta fingerprint |
| P18 fingerprint | Mettre à jour `test_phase4_route_contract.py` après gel |
| `scripts/db_maintenance.restore_backup` | Anti-traversal OK ; API ne fait que relayer |
| `database/*` | rowcount propagé correctement aux handlers audités |

---

### MÉTRIQUES MESURÉES

```text
router_*.py fichiers          : 15 (contrat architecture à jour ; CLAUDE.md « 12 » obsolète)
Ops HTTP+WS filtrées contrat  : 232  (attendu 221)  FAIL
Chemins OpenAPI               : 206  (attendu 197)  FAIL
Modules api/*.py > 500 lignes : 0                   PASS
Imports api → main            : 0                   PASS
Sites fuite str(e) client     : ~25+ HTTP paths     FAIL
```

---

### VERDICT

**CONDITIONAL_PASS** pour la structure Phase 4 et le serving frontend/mobile ; **non merge-ready** sur la checklist sécurité/contrat tant que **F-P03-001** et **F-P03-002** ne sont pas traités (corrections hors scope lecture seule ; fingerprint → P18, fitness → P13).