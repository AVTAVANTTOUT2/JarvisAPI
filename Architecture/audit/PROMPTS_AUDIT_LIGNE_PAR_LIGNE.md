# Prompts d’audit ligne par ligne — JARVIS

Document de distribution. Un agent = un périmètre. À la fin, un agent de consolidation fusionne toutes les réponses.

**Règles globales (à coller en tête de chaque session, ou déjà incluses dans chaque prompt) :**

- Lecture seule. Aucune modification de fichier, aucun commit, aucun `git push`.
- Racine du dépôt : le workspace JARVIS ouvert.
- Ignorer systématiquement : `venv/`, `node_modules/`, `__pycache__/`, `*.pyc`, `.git/`, `data/jarvis.db*`, `data/uploads/`, `certs/`, `credentials/`, `frontend/out/`, `web/dist/`, `android/app/build/`, `android/.gradle/`, `.pytest_cache/`, `.ruff_cache/`, `.worktrees/`, `.claude/worktrees/`.
- Couverture exigée : **chaque fichier source du périmètre**, ligne par ligne. Si un fichier est volontairement sauté, le lister dans `Fichiers non lus` avec motif.
- Pas de spéculation sans preuve : chaque finding cite `chemin:ligne` (ou plage) + extrait court.
- Langue de sortie : **français**.
- Ne pas auditer hors périmètre. Si un problème traverse une frontière, le noter en `Frontières / dépendances` avec le `ID_PERIMETRE` concerné, sans auditer l’autre code en profondeur.

---

## Schéma de sortie obligatoire (identique pour tous)

Chaque agent doit répondre **uniquement** avec cette structure Markdown (pas d’intro hors template) :

```markdown
# AUDIT — {ID_PERIMETRE} — {NOM}

## Métadonnées
- Agent / modèle :
- Date :
- Commit audité (`git rev-parse HEAD`) :
- Branche :
- Fichiers dans le périmètre (count) :
- Fichiers lus (count) :
- Couverture estimée : XX%

## Synthèse exécutive
(5–10 lignes. Verdict global du périmètre.)

## Findings
### F-{ID_PERIMETRE}-001
- Sévérité : CRITICAL | HIGH | MEDIUM | LOW | INFO
- Type : bug | sécurité | perf | dette | doc-drift | contrat-cassé | smell | dead-code
- Titre :
- Preuve : `chemin:ligne` (+ extrait ≤ 8 lignes)
- Impact :
- Repro / condition :
- Correctif proposé (sans coder) :
- Confiance : haute | moyenne | basse

(répéter F-…-002, …)

## Contrats vérifiés
| Contrat / invariant | Statut | Preuve |
|---|---|---|
| … | OK / KO / N/A | … |

## Frontières / dépendances
- Signale vers autre périmètre : …
- Attendus de ce périmètre consommés ailleurs : …

## Fichiers non lus
| Fichier | Motif |

## Couverture
- Liste exhaustive des fichiers lus (chemins relatifs), triée.
```

### Échelle de sévérité

| Niveau | Critère |
|---|---|
| CRITICAL | Exploitation réaliste, perte de données, auth bypass, RCE, fuite secrets |
| HIGH | Bug fonctionnel certain, contrat cassé bloquant, faille atténuée mais réelle |
| MEDIUM | Robustesse, perf, inconsistances, dette dangereuse |
| LOW | Style, commentaires trompeurs, dead code mineur |
| INFO | Observation utile, pas un défaut |

---

## Prompt 0 — Consolidation (à lancer en dernier)

```text
Tu es l’agent de consolidation d’un audit ligne par ligne du dépôt JARVIS.

MISSION
Fusionner les rapports d’audit fournis (un par périmètre P01…P18) en un constat général unique, sans ré-auditer le code.

ENTRÉES
Les messages/fichiers suivants contiennent les rapports bruts (colle-les tous ci-dessous ou joins-les) :
<<<RAPPORTS>>>
(… coller ici tous les AUDIT — Pxx …)
<<<FIN_RAPPORTS>>>

RÈGLES
1. Ne invente aucun finding. Tu peux seulement dédupliquer, reclasse, et croiser.
2. Si deux périmètres signalent le même problème, fusionne sous un ID canonique `G-XXX` et liste les IDs sources.
3. Si un finding d’un périmètre est contredit par un autre, marque `CONFLIT` et cite les deux preuves.
4. Classe le backlog : P0 (CRITICAL+HIGH exploitables) → P1 → P2 → P3.
5. Produis une carte de couverture : quels périmètres sont complets / partiels / absents.
6. Signale les trous : fichiers mentionnés nulle part alors qu’ils existent aux racines connues (main.py, auth.py, etc.).

SORTIE OBLIGATOIRE
# CONSTAT GÉNÉRAL — AUDIT JARVIS
## Couverture des périmètres
## Top 20 findings consolidés (G-001…)
## Matrice risques (sécurité / fiabilité / dette / doc)
## Contradictions entre agents
## Backlog priorisé (P0→P3)
## Zones saines (ce qui a été explicitement validé OK)
## Recommandation d’ordre de correctifs (5 étapes max)
```

---

## Prompt P01 — Bootstrap, config, assemblage FastAPI

```text
Tu es un auditeur senior FAANG. Audit LIGNE PAR LIGNE, lecture seule.

ID_PERIMETRE: P01
NOM: Bootstrap, config et assemblage

INCLUS (uniquement) :
- main.py
- config.py
- env_loader.py
- pipeline.py
- supervisor.py
- websocket_registry.py
- requirements.txt
- requirements-dev.txt
- requirements-agent.txt
- pytest.ini
- .env.example
- com.jarvis.supervisor.plist
- com.jarvis.imessage-daemon.plist

EXCLUS :
- Tout api/, agents/, database/, integrations/, frontend/, scripts/ (sauf plist listés)
- Secrets réels (.env), data/, certs/, credentials/

MISSION
Vérifier le démarrage, le chargement de config, le montage FastAPI/CORS/Uvicorn, les invariants d’environnement, et la cohérence des dépendances déclarées.

CHECKLIST OBLIGATOIRE
1. Chaque variable lue dans config.py : défaut sûr ? fail-closed si secret manquant ?
2. Bind réseau / TLS / WEB_ALLOW_NETWORK_BIND : pas de défaut dangereux.
3. CORS : origines, credentials, risque cross-port localhost.
4. Montage des routers dans main.py : oubli, double montage, ordre middleware.
5. pipeline.py : contrat public vs duplication avec api/.
6. requirements*.txt : imports critiques absents (mlx_audio, aiohttp, etc.) ou pins absents.
7. plist LaunchAgent : chemins, WorkingDirectory, KeepAlive, fuites d’env.
8. Contradiction avec CLAUDE.md seulement si tu as la preuve dans CE périmètre.

MÉTHODE
Lis chaque fichier inclus de haut en bas. Pour tout fichier > 400 lignes, audite par sections et note la couverture.

SORTIE : schéma obligatoire (voir document PROMPTS_AUDIT_LIGNE_PAR_LIGNE.md).
```

---

## Prompt P02 — Auth, sessions, sécurité HTTP, fichiers sensibles

```text
Tu es un auditeur sécurité (OWASP Top 10). Audit LIGNE PAR LIGNE, lecture seule.

ID_PERIMETRE: P02
NOM: Auth et sécurité HTTP

INCLUS (uniquement) :
- auth.py
- security_headers.py
- push.py
- api/middleware.py
- api/router_auth.py
- core/ (tout le dossier)
- jarvis/security/ (tout)
- jarvis/log_privacy.py
- jarvis/document_privacy.py
- jarvis/pii/ (tout)
- jarvis/uploads.py

EXCLUS :
- jarvis_auth/ frontend React (→ P14)
- web_mobile/js/auth.js (→ P15)
- android auth (→ P16)
- database/sessions.py détails CRUD sauf si importé : tu peux LIRE database/sessions.py et database/mobile.py UNIQUEMENT pour tracer les appels depuis ce périmètre, mais ne les audite pas ligne à ligne (signaler en Frontières vers P06).

MISSION
Prouver l’absence ou la présence de bypass auth, faiblesses PIN/passphrase, CSRF, CSP, sessions, rate-limit, pairing mobile/desktop, push VAPID, permissions fichiers.

CHECKLIST OWASP (explicite ✓/✗ dans Contrats vérifiés)
A01 contrôle d’accès — allowlists middleware, routes publiques exactes
A02 secrets — hashing scrypt/PIN, tokens hashés, pas de secret en clair
A03 injection — SQL via auth helpers, headers
A04 rate-limit — unlock, pairing device, pairing mobile
A05 misconfig — debug, messages d’erreur
A07 sessions — TTL, révocation, cookie flags Secure/SameSite
A08 intégrité jetons
A09 journalisation échecs auth
A10 redirects / SSRF absents

POINTS D’ATTENTION CONNUS À CONFIRMER OU INFIRMER
- Asymétrie rate-limit pairing desktop vs mobile (/api/mobile/pairing/complete)
- PIN min 4 vs doc 6
- CSRF si Origin/Referer absents
- Bypass supervisor loopback X-Jarvis-Supervisor
- Local unlock recovery

SORTIE : schéma obligatoire + table OWASP dans Contrats vérifiés.
```

---

## Prompt P03 — Couche API REST (routeurs domaine)

```text
Tu es un auditeur API FastAPI. Audit LIGNE PAR LIGNE, lecture seule.

ID_PERIMETRE: P03
NOM: API REST routeurs

INCLUS (uniquement) :
- api/router_*.py (tous)
- api/lifespan.py
- api/frontend.py
- api/web_mobile.py
- api/misc_*.py
- api/daemon_support.py
- api/service_control.py
- api/memory_background.py
- api/llm_logging.py
- api/people_support.py
- api/people_chat.py
- api/__init__.py

EXCLUS :
- api/middleware.py, api/router_auth.py (→ P02)
- api/ws_*.py, api/chat_*.py, api/voice_*.py, api/mobile_voice_service.py (→ P04)
- app/fitness/routes.py (→ P13)
- Contenu métier profond dans database/ et scripts/ : tracer les appels, findings de frontière seulement.

MISSION
Verrouiller contrats HTTP : validation entrées, codes 404/403/422, CSRF sur mutations cookie, pas d’import de main.py, taille modules, cohérence OpenAPI, serving frontend/mobile.

CHECKLIST
1. Chaque endpoint : auth attendue ? mutation CSRF-sensitive ?
2. rowcount / 404 sur UPDATE/DELETE.
3. Pas de fuite d’exception interne au client.
4. Fichiers > 500 lignes (contrat Phase 4).
5. Redirect /mobile/ et /m/ : logique UA, cookie desktop.
6. Double définition de routes.
7. Path traversal sur fichiers/static/backups.
8. Comparer le nombre de routes/OpenAPI au fingerprint tests/test_phase4_route_contract.py (lire le test en Frontières P18, ne pas modifier).

SORTIE : schéma obligatoire.
```

---

## Prompt P04 — Pipeline WebSocket, chat, voix, actions

```text
Tu es un auditeur systèmes temps réel. Audit LIGNE PAR LIGNE, lecture seule.

ID_PERIMETRE: P04
NOM: WebSocket, chat, voix, actions

INCLUS (uniquement) :
- api/ws_handler.py
- api/ws_messages.py
- api/chat_actions.py
- api/chat_cognitive.py
- api/chat_context.py
- api/chat_processing.py
- api/voice_cognitive.py
- api/voice_processing.py
- api/voice_support.py
- api/mobile_voice_service.py
- api/router_mobile_chat.py
- api/router_mobile_voice.py
- actions.py

EXCLUS :
- agents/* (→ P05)
- audio/* (→ P09)
- pipeline.py (→ P01) sauf pour comparer le contrat : lecture ciblée autorisée max 1 fois pour cohérence.

MISSION
Auditer le pipeline unifié _process_message / contexte enrichi / streaming / TTS hooks / action_confirm / mode vocal / anti-écho / auth WS.

CHECKLIST
1. Auth WebSocket fail-closed (codes 4401/4428).
2. Même pipeline texte et voix (pas de chemin parallèle oublié).
3. Extraction actions ```action``` : validation, confirmations terminal.
4. ACTIONS_WITH_FOLLOWUP : pas de stdout brut dangereux.
5. Contexte enrichi : injections mots-clés, taille, fuites PII dans logs.
6. Race is_processing / is_speaking.
7. Persistence messages : ordre, titre auto, documents.
8. Mobile chat/voice : _require_mobile_device vs cookie web.

SORTIE : schéma obligatoire.
```

---

## Prompt P05 — Agents, LLM, prompts

```text
Tu es un auditeur LLM/agents. Audit LIGNE PAR LIGNE, lecture seule.

ID_PERIMETRE: P05
NOM: Agents, LLM, prompts

INCLUS (uniquement) :
- llm.py
- agents/ (tout sauf agents/devagent/ → P12)
- prompts/ (tous les .txt et prompts/cursor/*.md)
- jarvis/backends/ (tout)
- jarvis/router.py
- jarvis/models.py
- jarvis/settings.py
- jarvis/exceptions.py
- jarvis/message_intelligence.py

EXCLUS :
- agents/devagent/, agents/briefing_engine.py si tu le classes… INCLUS briefing_engine ici.
- jarvis/cognitive/ (→ P12)
- integrations/deepseek_client.py : frontière P08 ; lecture ciblée OK si llm.py délègue.

MISSION
Persona unique, routing orchestrateur, modèles DeepSeek, heavy tasks, prompt injection, fuite “agent X”, émotions TTS, coûts, cache.

CHECKLIST
1. inject_persona True/False correct (orchestrator/memory).
2. Ordre system prompt life_profile + memory puis instructions.
3. classify_task_type / _route_task / VOICE_MAX_TOKENS / HEAVY_TASK_MAX_TOKENS.
4. Coach “escalade Opus” réelle ou morte (même modèle).
5. Parsing JSON fragile, ```save``` school.
6. Horodatage vs config.TIMEZONE.
7. Contenu prompts/persona.txt : règles anti-emoji, anti-agent.
8. Coûts trackés ; secrets absents des prompts.

SORTIE : schéma obligatoire.
```

---

## Prompt P06 — Database SQLite, migrations, helpers

```text
Tu es un auditeur données/SQLite. Audit LIGNE PAR LIGNE, lecture seule.

ID_PERIMETRE: P06
NOM: Database et migrations

INCLUS (uniquement) :
- database/ (tout : *.py, schema.py, schema.sql, migrations/)

EXCLUS :
- app/fitness DB helpers hors database/fitness.py (si logique dans app/fitness → P13)
- scripts/db_*.py (→ P11) ; lecture frontière autorisée.

MISSION
Schéma runtime vs schema.sql, migrations idempotentes, SQL injection, transactions, événements après commit, timezones, FTS, permissions fichiers.

CHECKLIST
1. Source de vérité schéma : schema.py vs schema.sql vs migrations.
2. get_db() : commits, rollback, threads.
3. Toutes les requêtes paramétrées (pas de f-string SQL sur input user).
4. update_* : allowlist colonnes (conversations, places, etc.).
5. Emit events après commit uniquement.
6. time_buckets / TIMEZONE.
7. Compter tables persistantes vs CLAUDE (doc-drift).
8. CASCADE / orphelins / UNIQUE.

SORTIE : schéma obligatoire + count tables runtime si dérivable du code.
```

---

## Prompt P07 — Event bus, notifications, registre WS

```text
Tu es un auditeur event-driven. Audit LIGNE PAR LIGNE, lecture seule.

ID_PERIMETRE: P07
NOM: Event bus et notifications

INCLUS (uniquement) :
- jarvis/event_bus.py
- jarvis/events.py
- jarvis/notification_service.py
- jarvis/__init__.py
- database/event_log.py
- database/notifications.py
- websocket_registry.py  (si déjà dans P01 : NE PAS re-auditer en profondeur ; ici focus handlers/bus — si conflit, P01 reste propriétaire du fichier et tu notes Frontière. Préférence : ce périmètre OWN websocket_registry.py pour le comportement runtime ; P01 ne fait que l’assemblage.)

Clarification ownership : pour cette campagne, P07 OWN `websocket_registry.py`. P01 l’exclut de sa checklist détaillée.

EXCLUS :
- push.py chiffrement (→ P02) ; ici seulement l’appel notification → push.
- scripts/audio_daemon consommation notifs (→ P09/P10).

MISSION
Contrats d’événements typés, checksum, concurrence handlers, persist event_log sync/async, dédup notifs, SSE/WS broadcast, replay manquant.

CHECKLIST
1. Classes dans events.py vs émetteurs.
2. emit vs emit_nowait vs bind_loop.
3. Handler sync SQLite sur loop asyncio.
4. Échec d’un handler n’arrête pas les autres.
5. Priorités urgent/high → push/TTS.
6. get_unprocessed_events sans replay auto.

SORTIE : schéma obligatoire.
```

---

## Prompt P08 — Intégrations externes et macOS

```text
Tu es un auditeur intégrations OS/cloud. Audit LIGNE PAR LIGNE, lecture seule.

ID_PERIMETRE: P08
NOM: Intégrations

INCLUS (uniquement) :
- integrations/ (tout le dossier *.py)

EXCLUS :
- Logique Cursor profonde déjà dans jarvis/cognitive (→ P12) : audite cursor_*.py ICI pour subprocess/sécurité ; signale la partie router vers P12.
- actions.py (→ P04) qui appelle ces intégrations.

MISSION
AppleScript Mail/Calendar/Contacts/Messages, chat.db readonly, shell computer, code_executor, weather, web_search, location, FCM, deepseek client, timeouts, secrets.

CHECKLIST
1. apple_data : UNIQUE façade chat.db, mode=ro, query_only.
2. Aucune autre connexion chat.db.
3. computer.run : shell vs allowlist ; is_safe.
4. shell_safety.py : plans one-shot, pas d’exécution avant confirm.
5. imessage send : échappement AppleScript, split 2000.
6. location Haversine / radius.
7. Timeouts httpx/subprocess partout.
8. code_executor : surface morte vs dangereuse.
9. Pas de clé API hardcodée.

SORTIE : schéma obligatoire.
```

---

## Prompt P09 — Audio STT/TTS natif et daemon audio

```text
Tu es un auditeur pipeline audio. Audit LIGNE PAR LIGNE, lecture seule.

ID_PERIMETRE: P09
NOM: Audio et TTS/STT

INCLUS (uniquement) :
- audio/ (tout)
- native_audio/ (tout)
- scripts/audio_daemon.py
- models/kokoro/ (structure/config seulement ; ignorer gros binaires — lister comme non lus si binaires)

EXCLUS :
- api/voice_*.py (→ P04)
- scripts/jarvis_daemon.py TTS queue (→ P10) sauf appels vers audio/

MISSION
STT local multi-moteurs, décodage WebM, TTS Edge/Kokoro/macOS/MLX, routing périphériques, formats WAV/M4A/MP3, fallbacks, latence, logs.

CHECKLIST
1. Jamais de repli cloud STT.
2. Kokoro MLX : stdout pollué ? format WAV ?
3. Sélection input/output device (Snowball auto vs default système).
4. afplay / afconvert / sounddevice.
5. Émotions TTS.
6. Gestion erreurs silencieuse vs crash.
7. Thread safety / queues.

SORTIE : schéma obligatoire.
```

---

## Prompt P10 — Daemon, screen watcher, devices, agent distant

```text
Tu es un auditeur systèmes agents permanents. Audit LIGNE PAR LIGNE, lecture seule.

ID_PERIMETRE: P10
NOM: Daemon multi-device et screen

INCLUS (uniquement) :
- scripts/jarvis_daemon.py
- scripts/screen_watcher.py
- scripts/jarvis_agent.py
- scripts/jarvis_launchd.py
- api/router_devices.py
- api/router_daemon.py
- database/screen_daemon.py
- requirements-agent.txt (cohérence agent distant ; déjà listé P01 — ici vérifier imports jarvis_agent seulement)

EXCLUS :
- Ollama guard code dans jarvis/cognitive (→ P12)
- audio_daemon (→ P09)

MISSION
Boucles daemon, triage Ollama, notifs iMessage/Mail, wake word, pairing devices, tokens hash, heartbeat, screenshots sans envoi image à Claude, sécurité agent Tailscale.

CHECKLIST
1. Pairing codes : TTL, one-time, rate-limit IP.
2. Token device : hash only, header uniforme.
3. Screenshot : Ollama local ; Claude texte only.
4. TTS cooldown / anti-spam.
5. Supervisor control auth.
6. Permissions / échecs silents.

SORTIE : schéma obligatoire.
```

---

## Prompt P11 — Workers, scheduler, qualité, maintenance, outils

```text
Tu es un auditeur jobs/batch. Audit LIGNE PAR LIGNE, lecture seule.

ID_PERIMETRE: P11
NOM: Scripts workers et outillage

INCLUS (uniquement) :
- scripts/ (TOUS les .py/.sh SAUF : audio_daemon.py, jarvis_daemon.py, screen_watcher.py, jarvis_agent.py, jarvis_launchd.py, tv_mcp_server.py, launch_tv_browser.sh)
- tools/ (tout)
- scripts/db_migrations.py, db_maintenance, self_healing, self_improvement, scheduler, email_watcher, relationship_*, location_analyzer, semantic_search, etc.

EXCLUS :
- TV MCP (→ P17)
- Daemon/screen/audio listés ci-dessus

MISSION
Idempotence jobs, coûts LLM, double notifications, self-healing opt-in, backups chiffrés, scans sécu, pas de mutation auto codebase sans garde-fou.

CHECKLIST
1. scheduler.py : chaque job flag ENABLED + try/except.
2. email_watcher : premier cycle, anti-doublon, Haiku JSON.
3. self_healing : SELF_HEALING_AUTO_APPLY défaut false.
4. Backups Fernet / permissions 0600.
5. semantic_search threads vs tests isolation.
6. Scripts shell : set -e, chemins.

SORTIE : schéma obligatoire. Liste tous les scripts inclus (inventory).
```

---

## Prompt P12 — Routage cognitif, Cursor, DevAgent, briefing

```text
Tu es un auditeur architecture LLM routing. Audit LIGNE PAR LIGNE, lecture seule.

ID_PERIMETRE: P12
NOM: Cognitif, Cursor, DevAgent

INCLUS (uniquement) :
- jarvis/cognitive/ (tout)
- agents/devagent/ (tout)
- agents/briefing_engine.py
- agents/autonomous_loop.py
- agents/devops.py
- integrations/cursor_delegation.py
- integrations/cursor_cli.py
- integrations/cursor_env.py
- integrations/cursor_prompt_composer.py
- integrations/cursor_required_tests.py
- database/cursor_jobs.py
- database/devagent.py
- database/devops.py
- api/router_cognitive.py
- api/router_devagent.py
- api/router_quality.py
- prompts/cursor/ (si non couvert : INCLUS ici ; P05 exclut prompts/cursor)

Clarification : P05 OWN prompts/*.txt hors cursor/. P12 OWN prompts/cursor/.

EXCLUS :
- Ollama usage dans screen_watcher (→ P10) ; ici ollama_guard allowlist.

MISSION
Politique Flash/Main/Cursor/Ollama, worktrees isolés, jamais main, PR-only self-mod, briefings, jobs persistants.

CHECKLIST
1. router.py déterministe avant LLM.
2. ollama_guard : allowlist + tests offenders.
3. cursor jobs : branche jarvis/cursor/*, worktree .jarvis/worktrees/.
4. SELF_MODIFICATION_MODE.
5. Reprise au restart.
6. PII boundary / redaction si croisé.

SORTIE : schéma obligatoire.
```

---

## Prompt P13 — Module Fitness

```text
Tu es un auditeur module métier. Audit LIGNE PAR LIGNE, lecture seule.

ID_PERIMETRE: P13
NOM: Fitness

INCLUS (uniquement) :
- app/fitness/ (tout source)
- database/fitness.py
- Toute vue fitness desktop : web/src/app/components/views/FitnessView.tsx et web/src/app/components/fitness/** s’ils existent
- Références fitness dans frontend/ (pages/routes) : chercher et auditer UNIQUEMENT les fichiers fitness

EXCLUS :
- web_mobile health.js (→ P15) — noter contrat API partagé en Frontières
- auth (→ P02)

MISSION
Modèles Pydantic, routes, services, validation reminder_*, calculs progress, conseils LLM, montage dans main (vérifier import seulement).

CHECKLIST
1. Validation stricte vs UI qui envoie ""/0.
2. Sync def vs async / threadpool.
3. except Exception: pass sur LLM.
4. Isolation schéma fitness.
5. Dead code FitnessForms vs FitnessView.

SORTIE : schéma obligatoire.
```

---

## Prompt P14 — Frontend bureau + Auth SDK React

```text
Tu es un auditeur frontend React/Next. Audit LIGNE PAR LIGNE, lecture seule.

ID_PERIMETRE: P14
NOM: Frontend bureau et jarvis_auth

INCLUS (uniquement) :
- frontend/ (src/, public/, e2e/, config package/tsconfig/next.config — ignorer out/)
- web/src/ (tout) + web/package.json, vite.config.ts, index.html, web/src/sw.ts
- jarvis_auth/ (tout)

EXCLUS :
- web_mobile/ (→ P15)
- web/dist/, frontend/out/
- FitnessView détaillée déjà P13 : tu PEUX relire pour chat/shell, mais findings fitness validation → renvoyer à P13 (pas de double liste sauf si UI shell).

MISSION
LockGate fail-closed, api.ts unique fetch+cookie+CSRF, UnifiedApp mobile redirect, SW ne cache pas /api, persona UI (pas de nom d’agent), offline queue, accessibilité basique.

CHECKLIST
1. Aucun fetch hors frontend/src/lib/api.ts et équivalent web si legacy.
2. LockGate : enfants privés non montés avant session.
3. Auto-lock + clearOfflineDB.
4. ChatView : affichage message.agent ?
5. CSP assumptions MapLibre.
6. Service worker scope/cache.
7. pnpm version pin 11.11.0.

SORTIE : schéma obligatoire.
```

---

## Prompt P15 — Interface mobile web autonome

```text
Tu es un auditeur mobile web vanilla. Audit LIGNE PAR LIGNE, lecture seule.

ID_PERIMETRE: P15
NOM: web_mobile

INCLUS (uniquement) :
- web_mobile/ (tout : html/css/js/icons/manifest)
- api/web_mobile.py (serving ; si P03 l’a déjà, focus comportement mobile ici — findings serving dupliqués = Frontière P03)

Ownership campagne : P15 OWN le contenu web_mobile/. P03 OWN api/web_mobile.py pour HTTP serving ; P15 peut le lire pour comprendre cache-bust/MIME.

MISSION
Fail-closed auth.js, CSRF api.js, WS, vues chat/voix/aujourd’hui/tâches/mails/santé, pas d’import web/frontend, pas de CDN, passphrase manquante, fitness mobile.

CHECKLIST
1. Aucun import depuis web/src, frontend, jarvis_auth.
2. Aucune ressource distante (CSP self).
3. Session avant montage vues / WS.
4. PIN only vs passphrase.
5. health.js contrats API fitness.
6. Tests structurels test_web_mobile.py : lire pour contrats, findings tests → P18.

SORTIE : schéma obligatoire.
```

---

## Prompt P16 — Application Android Companion

```text
Tu es un auditeur Android/Kotlin. Audit LIGNE PAR LIGNE, lecture seule.

ID_PERIMETRE: P16
NOM: Android Companion

INCLUS (uniquement) :
- android/app/src/ (main/debug/test/androidTest)
- android/docs/
- android/*.gradle* , gradle/wrapper properties (pas les caches .gradle ni build/)

EXCLUS :
- android/app/build/, android/.gradle/, android/build/

MISSION
Auth Bearer mobile, chat/voix, location, offline, feature flags FUTURE_*, secrets, ProGuard, parité API.

CHECKLIST
1. Stockage token (EncryptedSharedPreferences ?).
2. Certificate pinning / cleartext.
3. JARVIS-FUTURE-* : UI mensongère vs désactivée.
4. Synchronisation CSRF/cookie vs Bearer.
5. Permissions runtime.
6. Tests unitaires présents vs absents.

SORTIE : schéma obligatoire + inventaire FUTURE features.
```

---

## Prompt P17 — TV War Room et bridge MCP/CDP

```text
Tu es un auditeur surface TV/CDP. Audit LIGNE PAR LIGNE, lecture seule.

ID_PERIMETRE: P17
NOM: TV et MCP browser

INCLUS (uniquement) :
- tv/
- front_tv/
- scripts/tv_mcp_server.py
- scripts/launch_tv_browser.sh
- tv/**/*.plist s’ils existent

EXCLUS :
- Dashboard data venant de l’API principale : frontière seulement.

MISSION
Sécurité CDP exposé, ADB, URLs hardcodées, auth dashboard TV, MCP tools surface d’attaque, deps aiohttp.

CHECKLIST
1. CDP localhost only ?
2. Pas de commande arbitraire via MCP.
3. Secrets TV_IP dans repo.
4. XSS dashboard.
5. LaunchAgent auto-start risques.

SORTIE : schéma obligatoire.
```

---

## Prompt P18 — Tests, CI, contrats, docs de vérité

```text
Tu es un auditeur qualité/CI. Audit LIGNE PAR LIGNE (priorité contrats), lecture seule.

ID_PERIMETRE: P18
NOM: Tests, CI et vérité documentaire

INCLUS (uniquement) :
- tests/ (tout)
- .github/ (tout)
- CLAUDE.md
- README.md
- Architecture/32_FRONTEND_DATABASE_SOURCE_OF_TRUTH.md
- Architecture/LLM_POLICY.md
- artifacts/architecture_truth.json s’il existe
- tools/audit_architecture_truth.py
- FRONTEND_SPECS.md, RELEASE_CHECKLIST.md, STARTUP_PROTOCOL.md, VOCAL_PIPELINE_ANALYSIS.md (doc drift)
- Architecture/INDEX.md et docs listant counts routes/tables (échantillonner TOUS les .md Architecture/ qui mentionnent 12 routeurs, 207 routes, 76 tables)

EXCLUS :
- Ré-audit du code prod déjà couvert : tu confrontes docs/tests AUX claims, tu ne réécris pas l’audit P01–P17.

MISSION
Fingerprint Phase 4, skips dangereux, flaky sleeps, CI jobs vs CLAUDE claims, dérive documentation, couverture trous.

CHECKLIST
1. test_phase4_route_contract EXPECTED_* vs app réelle (importer main si env dispo).
2. test_phase4_architecture frontières.
3. conftest isolation DB/threads.
4. macOS job vs test_ci_macos assertions.
5. CLAUDE counts obsolètes (tables, routes, PIN, Vitest, Playwright).
6. Skip if frontend/out missing.

SORTIE : schéma obligatoire + liste “claims faux” avec preuve.
```

---

## Matrice de distribution rapide

| ID | Périmètre | Owner fichiers (résumé) | Parallelisable |
|---|---|---|---|
| P01 | Bootstrap/config | main, config, requirements, plists | oui |
| P02 | Auth/sécu | auth, middleware, push, core, pii | oui |
| P03 | API REST | api/router_*, lifespan, frontend serving | oui |
| P04 | WS/chat/voix/actions | api/ws|chat|voice*, actions.py | oui |
| P05 | Agents/LLM/prompts | agents/, llm, prompts/*.txt | oui |
| P06 | Database | database/ | oui |
| P07 | Event bus | jarvis/event*, notification_service | oui |
| P08 | Intégrations | integrations/ | oui |
| P09 | Audio | audio/, native_audio/, audio_daemon | oui |
| P10 | Daemon/devices | jarvis_daemon, screen, devices | oui |
| P11 | Workers/scripts | scripts/ restants, tools/ | oui |
| P12 | Cognitif/Cursor/DevAgent | cognitive, cursor_*, devagent | oui |
| P13 | Fitness | app/fitness, database/fitness, FitnessView | oui |
| P14 | Frontend bureau | frontend/, web/src, jarvis_auth | oui |
| P15 | Mobile web | web_mobile/ | oui |
| P16 | Android | android/app/src, docs | oui |
| P17 | TV/MCP | tv/, front_tv/, tv_mcp | oui |
| P18 | Tests/CI/docs | tests/, .github/, CLAUDE, Architecture | **après** ou parallèle prudent |
| P00 | Consolidation | tous les rapports | **dernier** |

---

## Conseils d’orchestration

1. Lancer **P01–P17 en parallèle** (18 agents max si besoin ; sinon vagues de 4–6).
2. Lancer **P18 en parallèle** aussi, mais lui coller les fingerprints attendus déjà connus.
3. Quand tous les rapports sont revenus, lancer **Prompt 0** avec le concat des sorties.
4. Si un agent déborde hors périmètre : jeter la partie hors-scope au merge.
5. Exiger le champ `Commit audité` identique sur tous les rapports (même SHA), sinon invalider le constat.
```

---

## Mini-prompt “rappel anti-dérive” (optionnel, à prepend)

```text
CONTRAINTE ABSOLUE : tu n’audites QUE les chemins INCLUS de ton ID_PERIMETRE. Toute découverte hors scope = une ligne dans Frontières, pas un finding détaillé. Sortie = template AUDIT — {ID} uniquement.
```
