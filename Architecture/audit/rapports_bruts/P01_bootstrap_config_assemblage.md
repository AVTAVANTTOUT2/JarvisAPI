<!--
source_agent: bc-019fb865-afbc-7a96-a2b4-eeb1f8e38476
agent_name: Démarrage configuration assemblage
agent_url: https://cursor.com/agents/bc-019fb865-afbc-7a96-a2b4-eeb1f8e38476
agent_status: IDLE
created_at: 2026-07-31T13:38:20.098000+00:00
extracted_msg_index: 135
extracted_at: 2026-07-31T14:37:19.332284+00:00
-->

# AUDIT LIGNE PAR LIGNE — P01

```
ID_PERIMETRE: P01
NOM: Bootstrap, config et assemblage
MODE: lecture seule (aucune modification)
DATE: 2026-07-31
GIT_REF: 2191bf36
BRANCH: elias/fitness-meal-ai-photo-8e4f
SCHEMA_SOURCE: PROMPTS_AUDIT_LIGNE_PAR_LIGNE.md — ABSENT du workspace ; schéma reconstruit
```

---

## 1. COUVERTURE

| Fichier | Lignes | Méthode | Couverture |
|---|---:|---|---|
| `main.py` | 217 | intégral | 1–217 |
| `config.py` | 592 | sections | 1–592 (DeepSeek/audio 1–117 ; intégrations/iMessage 119–146 ; système/TLS 148–188 ; computer/TV/code 194–222 ; daemon/audio/loop 247–325 ; fiabilité/rituels/fitness 327–393 ; présence/autonomy/auth 399–563 ; frontends/agents/mobile 565–592) |
| `env_loader.py` | 50 | intégral | 1–50 |
| `pipeline.py` | 77 | intégral | 1–77 |
| `supervisor.py` | 1115 | sections | 1–160 conf/lock ; 161–320 helpers/status ; 324–519 start/stop ; 522–735 routes/WS ; 738–887 proxy/WS ; 890–1078 frontend/health/lifecycle ; 1084–1115 entry |
| `websocket_registry.py` | 47 | intégral | 1–47 |
| `requirements.txt` | 58 | intégral | 1–58 |
| `requirements-dev.txt` | 7 | intégral | 1–7 |
| `requirements-agent.txt` | 14 | intégral | 1–14 |
| `pytest.ini` | 3 | intégral | 1–3 |
| `.env.example` | 459 | intégral | 1–459 |
| `com.jarvis.supervisor.plist` | 45 | intégral | 1–45 |
| `com.jarvis.imessage-daemon.plist` | 42 | intégral | 1–42 |

**Total audité : 2726 lignes / 13 fichiers. Aucun fichier inclus omis.**

---

## 2. CHECKLIST OBLIGATOIRE

| # | Item | Verdict | Preuve |
|---|---|---|---|
| 1 | Variables `config.py` : défaut sûr / fail-closed secrets | **PARTIEL** | Bind/TLS/auth flags sûrs ; `DEEPSEEK_API_KEY=""` sans abort au load ; plusieurs opt-in dangereux à `true` |
| 2 | Bind / TLS / `WEB_ALLOW_NETWORK_BIND` | **OK** | Défauts `127.0.0.1` / `false` / `false` ; `validate_network_bind` + exit 1 si HTTPS sans certs |
| 3 | CORS origines / credentials / cross-port | **RISQUE** | `allow_credentials=True` + origines multi-ports localhost ; `0.0.0.0:3000` ; supervisor sans credentials |
| 4 | Montage routers / ordre middleware | **OK+ÉCART DOC** | 16 `include_router` + 1 WS ; pas de double montage ; security outer vs CORS |
| 5 | `pipeline.py` contrat vs duplication | **OK** | Façade pure ; zéro logique métier ; handlers injectés depuis `main` |
| 6 | `requirements*.txt` imports critiques / pins | **ÉCART** | `mlx-audio` absent (venv séparé) ; `aiohttp` absent ; pins `==X.*` larges |
| 7 | LaunchAgent plists | **CRITIQUE** | Chemins `/Users/zeldris/JarvisAPI` inexistants ; KeepAlive OK ; pas de secret dans env plist |
| 8 | Contradiction CLAUDE.md (preuve in-périmètre) | **OUI** | 12 routers / 175 lignes / PIN 6 vs `.env.example` 4 |

---

## 3. FINDINGS

### F-P01-001 — CRITIQUE
**Titre:** LaunchAgents pointent vers un chemin inexistant  
**Fichier:** `com.jarvis.supervisor.plist` L10–15, L30–33 ; `com.jarvis.imessage-daemon.plist` L10–17, L28–32  
**Preuve:** `ProgramArguments` / `WorkingDirectory` / logs → `/Users/zeldris/JarvisAPI/...` ; `ls /Users/zeldris/JarvisAPI` → *No such file or directory* ; workspace réel = `/Users/zeldris/JARVIS`.  
**Impact:** `launchd` ne peut pas démarrer supervisor ni daemon iMessage depuis ces plists.  
**Reco:** Régénérer les plists avec le chemin réel du dépôt (ou variable / script d’install) ; vérifier `launchctl print`.

---

### F-P01-002 — HAUT
**Titre:** API supervisor non authentifiée (start/stop/restart/proxy)  
**Fichier:** `supervisor.py` L526–618, L717–735, L759–817, L1088–1115  
**Preuve:** Routes `POST /api/supervisor/{sid}/start|stop|restart`, `start-all`, `stop-all`, WS `/ws/supervisor` sans session/CSRF/token. Bind = `config.WEB_HOST`.  
**Impact:** Sur loopback (défaut) : tout process local contrôle le backend. Si `WEB_ALLOW_NETWORK_BIND=true`, surface réseau de contrôle total sans auth.  
**Reco:** Fail-closed auth sur `/api/supervisor/*` (cookie session admin ou token dédié) indépendamment du bind ; refuser le network bind du supervisor sans auth.

---

### F-P01-003 — HAUT
**Titre:** CORS credentials + origines cross-port localhost  
**Fichier:** `main.py` L86–106  
**Preuve:**
```86:106:main.py
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        ...
        "http://0.0.0.0:3000",
        ...
        "http://localhost:8081",
        "http://127.0.0.1:8081",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```
**Impact:** Navigateur sur un port listé peut envoyer le cookie `jarvis_session` cross-origin vers l’API. Atténué hors-périmètre par CSRF Origin+token, mais surface CORS trop large ; `http://0.0.0.0:3000` n’est pas une Origin navigateur réelle.  
**Reco:** Restreindre aux origines réellement utilisées ; retirer `0.0.0.0` ; préférer same-origin via supervisor `:9000`.

---

### F-P01-004 — HAUT
**Titre:** Secret LLM non fail-closed au bootstrap  
**Fichier:** `config.py` L33 ; `.env.example` L6–8  
**Preuve:** `DEEPSEEK_API_KEY = _get("DEEPSEEK_API_KEY", "")` — process démarre sans clé. `.env.example` marque OBLIGATOIRE, `config` n’abort pas.  
**Impact:** Service « up » mais sourd cognitivement ; erreurs tardives ; monitoring trompeur.  
**Reco:** Abort explicite au démarrage (ou mode dégradé documenté) si clé vide hors tests.

---

### F-P01-005 — HAUT
**Titre:** `SECRET_ENV_KEYS` déclaré mais jamais appliqué  
**Fichier:** `env_loader.py` L23–50  
**Preuve:** `SECRET_ENV_KEYS` frozenset ; `load_jarvis_env()` charge `.env.config` puis `.env` sans vérifier où vivent les secrets ; aucune autre référence repo à `SECRET_ENV_KEYS`.  
**Impact:** Secrets peuvent vivre dans `.env.config` versionnable / partagé ; politique non exécutée.  
**Reco:** Warn/fail si une clé secrète est présente dans `.env.config` ; étendre la liste (FCM path/creds si applicable).

---

### F-P01-006 — MOYEN
**Titre:** Défauts config permissifs (capabilities actives)  
**Fichier:** `config.py` L195–196, L216, L250–251, L318, L459–460, L474  
**Preuve:** Défauts `true` : `COMPUTER_ACCESS`, `CODE_EXECUTOR_ENABLED`, `DAEMON_ENABLED`, `SCREEN_WATCHER_ENABLED`, `LOOP_UNLIMITED`, `DEVAGENT_AUTO_PR`, `DEVAGENT_AUTO_DEPLOY_STAGING`, `CURSOR_DELEGATION_ENABLED`. Contrastent avec fail-closed sur push/PR Cursor (`CURSOR_ALLOW_PUSH/PR=false`, L481–482) et self-healing.  
**Impact:** Machine fraîche avec `.env` minimal active shell/computer, executor, daemon, loop illimité, auto-PR.  
**Reco:** Aligner les défauts « puissance » sur fail-closed ; garder opt-in explicite.

---

### F-P01-007 — MOYEN
**Titre:** `COMPUTER_ACCESS` typé string, pas bool  
**Fichier:** `config.py` L195  
**Preuve:** `COMPUTER_ACCESS = _get("COMPUTER_ACCESS", "true")` (str). Consommateur hors-périmètre `bool(config.COMPUTER_ACCESS)` traiterait `"false"` comme True.  
**Impact:** Footgun de désactivation.  
**Reco:** Normaliser comme les autres flags (`.lower() == "true"`).

---

### F-P01-008 — MOYEN
**Titre:** Fuite de descripteurs fichiers logs au restart backend  
**Fichier:** `supervisor.py` L416–421, L437–442, L464–468  
**Preuve:** `stdout=open(..., "a")` sans `close` / context manager à chaque `_start_sync`.  
**Impact:** FD leak sous crash-loop health-check (L901–930).  
**Reco:** Ouvrir via helper qui garde/ferme le handle, ou `subprocess.DEVNULL` + FileHandler logging.

---

### F-P01-009 — MOYEN
**Titre:** Dépendance TTS critique absente de `requirements.txt`  
**Fichier:** `requirements.txt` (entier) ; `.env.example` L31–36 (`KOKORO_BACKEND=mlx`, commentaire mlx-audio)  
**Preuve:** Aucun `mlx-audio` / `mlx` dans `requirements*.txt`. Code productif (hors périmètre mais checklist) importe `mlx_audio`. Install « standard » → Kokoro MLX cassé.  
**Impact:** Écart déclaration / runtime ; onboarding trompeur.  
**Reco:** Documenter clairement le venv `JARVIS_VENV` dans `requirements.txt` + pin optionnel, ou extra `requirements-mlx.txt`.

---

### F-P01-010 — MOYEN
**Titre:** `aiohttp` utilisé ailleurs, absent des requirements  
**Fichier:** `requirements.txt` / `requirements-dev.txt` / `requirements-agent.txt`  
**Preuve:** Checklist ; `import aiohttp` présent dans le repo (`scripts/tv_mcp_server.py`, hors inclusion code mais in-scope deps).  
**Impact:** Environnement CI/dev incomplet pour outils TV MCP.  
**Reco:** Ajouter `aiohttp` au requirements approprié ou retirer l’import.

---

### F-P01-011 — MOYEN
**Titre:** Ordre middleware : security outer, CORS inner  
**Fichier:** `main.py` L86–108  
**Preuve:** `add_middleware(CORS)` puis `app.middleware("http")(security_middleware)` → Starlette `insert(0)` place security en tête de `user_middleware` → wrap reverse → security outermost.  
**Impact:** Réponses anticipées 401/403/428 du security middleware peuvent sortir sans en-têtes `Access-Control-*` pour clients cross-origin (dev Vite).  
**Reco:** Vérifier en test ; éventuellement composer CORS en outermost explicite.

---

### F-P01-012 — BAS
**Titre:** Pins dépendances trop larges  
**Fichier:** `requirements.txt` L2–7, L15, etc.  
**Preuve:** `fastapi==0.115.*`, `uvicorn[standard]==0.34.*`, `python-multipart==0.0.*`, `Pillow>=10.0`, `torch>=2.0`.  
**Impact:** Builds non reproductibles ; régression silencieuse.  
**Reco:** Pins exacts ou lockfile (`uv.lock` / `pip-tools`).

---

### F-P01-013 — BAS
**Titre:** Incohérences internes `.env.example`  
**Fichier:** `.env.example` L8–9 vs L21–22 ; L137–138 doublons ; L229 vs `config.py` L265 ; L228 vs `config.py` L260–263 ; L388  
**Preuve:** `DEEPSEEK_BASE_URL` sans `/v1` puis avec `/v1` ; `TRIAGE_MODEL=qwen2.5:7b` alors que config défaut = `DEEPSEEK_FAST_MODEL` ; PIN « 4 chiffres » ; `DEV_PROJECTS_ROOT` dupliqué.  
**Impact:** Onboarding ambigu ; divergence runtime vs template.  
**Reco:** Dédupliquer, aligner défauts template ↔ `config.py`.

---

### F-P01-014 — BAS
**Titre:** Supervisor CORS divergent / WS control sans auth  
**Fichier:** `supervisor.py` L88–106, L717–735  
**Preuve:** Pas de `allow_credentials` ; origines proches de `main` mais pas `0.0.0.0` ; `/ws/supervisor` `accept()` immédiat.  
**Impact:** Incohérence ; état services exposé localement sans auth.  
**Reco:** Même politique CORS+auth que le backend pour les surfaces admin.

---

### F-P01-015 — INFO
**Titre:** `pipeline.py` — contrat public sain  
**Fichier:** `pipeline.py` L1–77  
**Preuve:** Dataclass frozen, configure atomique, `PipelineNotConfiguredError`, pas d’import `api/`/`main`.  
**Impact:** Positif — casse la dépendance circulaire daemons ↔ FastAPI.  
**Reco:** Conserver ; typer plus strictement le dict de retour si besoin.

---

### F-P01-016 — INFO
**Titre:** Bind/TLS — défauts sûrs respectés  
**Fichier:** `config.py` L168–188 ; `main.py` L164–182 ; `supervisor.py` L1088–1105  
**Preuve:** `WEB_HOST=127.0.0.1`, `WEB_ALLOW_NETWORK_BIND=false`, `WEB_HTTPS=false`, exit si HTTPS sans certs, `validate_network_bind` des deux entrypoints.  
**Impact:** Pas de défaut dangereux réseau/TLS dans ce périmètre.  
**Reco:** Aucune pour les défauts ; garder le check au boot.

---

### F-P01-017 — INFO
**Titre:** `websocket_registry` — pattern snapshot correct  
**Fichier:** `websocket_registry.py` L25–47  
**Preuve:** Lock court → tuple recipients → I/O hors lock → purge dead. Handler `@event_bus.on` à l’import.  
**Impact:** OK pour broadcast ; `except Exception` large mais acceptable pour sockets mortes.  
**Reco:** Optionnel : logger DEBUG sur échec send.

---

## 4. CONTRADICTIONS CLAUDE.md (preuve dans P01 uniquement)

| Affirmation CLAUDE.md | Réalité P01 | Sévérité |
|---|---|---|
| `main.py` « 175 lignes » | 217 lignes | Doc |
| « exactement 12 `APIRouter` » | 16 `include_router` (`fitness` + 15) | Doc / contrat Phase 4 |
| PIN « 6 chiffres » | `.env.example` L388 : « PIN de 4 chiffres » | Doc / sécurité perçue |
| TTS Edge défaut (exemples historiques) | `config`/`\.env.example` défaut Kokoro MLX | Doc drift |
| Triage Ollama `qwen2.5:7b` | `config.TRIAGE_MODEL` défaut = `DEEPSEEK_FAST_MODEL` | Doc / runtime |

---

## 5. PARCOURS DÉMARRAGE (synthèse)

```
env_loader.load_jarvis_env()
  → .env.config (override=True) puis .env (override=True)
config.py module load (pas d’abort secret)
main:app
  → CORSMiddleware + security_middleware
  → 16 routers + /ws
  → pipeline.configure_pipeline(...)
  → _setup_frontend(app)
main()/supervisor __main__
  → validate_network_bind(...)
  → fail si WEB_HTTPS sans certs
  → uvicorn.bind(WEB_HOST, port)
```

Supervisor (si launchd OK) : lock `/tmp/jarvis_supervisor.lock` → autostart Ollama/backend → health-check restart → proxy `/api/*` + passthrough `/ws`.

---

## 6. VERDICT

| Axe | Note |
|---|---|
| Bind / TLS defaults | Solide |
| Assemblage FastAPI / pipeline | Solide (doc Phase 4 périmée) |
| Secrets / fail-closed bootstrap | Faible |
| CORS | À resserrer |
| LaunchAgents | Cassés (chemin mort) |
| Dépendances déclarées | Incomplètes (mlx-audio, aiohttp) + pins lâches |
| Supervisor control plane | Non authentifié |

**Verdict global P01 : NON MERGE-READY pour production réseau ; ACCEPTABLE en loopback solo après correction des plists.**

Bloquants avant confiance ops : **F-P01-001**, **F-P01-002** (si bind non-loopback), **F-P01-003/004** selon menace.

---

## 7. LIMITES DE CET AUDIT

- `PROMPTS_AUDIT_LIGNE_PAR_LIGNE.md` absent : schéma reconstruit.
- `api/middleware.py`, `core/network_security.py`, `auth.py` exclus — atténuations CSRF/fail-closed auth citées sans relecture ligne-à-ligne.
- Pas d’exécution runtime / `launchctl` / import smoke dans cet audit.
- Pas de commit/PR : mission lecture seule.