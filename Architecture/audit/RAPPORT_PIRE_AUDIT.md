# RAPPORT PIRE AUDIT — Consolidation des sorties Cursor

> Généré le 2026-07-31 14:41 UTC à partir des conversations cloud agents (audits ligne par ligne P01–P14).
> Sorties brutes complètes : `rapports_bruts/`. Findings classés du plus grave au moins grave.
> Traitement correctif prévu via Claude Opus, audit par audit — aucun correctif n’est appliqué ici.

## Couverture des périmètres

| ID | Périmètre | Statut agent | Findings | CRITICAL | HIGH | MEDIUM | LOW | INFO | UNKNOWN | Rapport brut |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| P01 | Bootstrap, config et assemblage | IDLE | 17 | 1 | 4 | 6 | 3 | 3 | 0 | [`P01_bootstrap_config_assemblage.md`](./rapports_bruts/P01_bootstrap_config_assemblage.md) |
| P02 | Auth et sécurité HTTP | IDLE | 10 | 0 | 1 | 4 | 3 | 2 | 0 | [`P02_auth_securite_http.md`](./rapports_bruts/P02_auth_securite_http.md) |
| P03 | API REST routeurs | IDLE | 7 | 0 | 2 | 1 | 3 | 1 | 0 | [`P03_api_rest_routeurs.md`](./rapports_bruts/P03_api_rest_routeurs.md) |
| P04 | WebSocket, chat, voix, actions | IDLE | 21 | 0 | 17 | 4 | 0 | 0 | 0 | [`P04_websocket_chat_voix_actions.md`](./rapports_bruts/P04_websocket_chat_voix_actions.md) |
| P05 | Agents, LLM, prompts | IDLE | 18 | 1 | 10 | 5 | 0 | 2 | 0 | [`P05_agents_llm_prompts.md`](./rapports_bruts/P05_agents_llm_prompts.md) |
| P06 | Database et migrations | IDLE | 7 | 0 | 2 | 3 | 2 | 0 | 0 | [`P06_database_migrations.md`](./rapports_bruts/P06_database_migrations.md) |
| P07 | Event bus et notifications | IDLE | 8 | 0 | 2 | 3 | 3 | 0 | 0 | [`P07_event_bus_notifications.md`](./rapports_bruts/P07_event_bus_notifications.md) |
| P08 | Intégrations OS/cloud | IDLE | 7 | 0 | 2 | 2 | 2 | 1 | 0 | [`P08_integrations_os_cloud.md`](./rapports_bruts/P08_integrations_os_cloud.md) |
| P09 | Audio STT/TTS | IDLE | 12 | 0 | 6 | 5 | 1 | 0 | 0 | [`P09_audio_stt_tts.md`](./rapports_bruts/P09_audio_stt_tts.md) |
| P10 | Daemon multi-device screen | IDLE | 12 | 0 | 1 | 3 | 4 | 4 | 0 | [`P10_daemon_multi_device_screen.md`](./rapports_bruts/P10_daemon_multi_device_screen.md) |
| P11 | Workers, scheduler, qualité | IDLE | 16 | 0 | 6 | 10 | 0 | 0 | 0 | [`P11_workers_scheduler_qualite.md`](./rapports_bruts/P11_workers_scheduler_qualite.md) |
| P12 | Cognitif, Cursor, DevAgent | IDLE | 12 | 0 | 2 | 6 | 3 | 1 | 0 | [`P12_cognitif_cursor_devagent.md`](./rapports_bruts/P12_cognitif_cursor_devagent.md) |
| P13 | Module Fitness | IDLE | 10 | 1 | 4 | 0 | 5 | 0 | 0 | [`P13_module_fitness.md`](./rapports_bruts/P13_module_fitness.md) |
| P14 | Frontend bureau + jarvis_auth | ERROR | 9 | 0 | 1 | 5 | 2 | 1 | 0 | [`P14_frontend_bureau_jarvis_auth.md`](./rapports_bruts/P14_frontend_bureau_jarvis_auth.md) |
| P15 | Interface mobile web | ABSENT | — | — | — | — | — | — | — | *aucun agent trouvé* |
| P16 | Android Companion | ABSENT | — | — | — | — | — | — | — | *aucun agent trouvé* |
| P17 | TV War Room MCP/CDP | ABSENT | — | — | — | — | — | — | — | *aucun agent trouvé* |
| P18 | Tests, CI, contrats, docs | ABSENT | — | — | — | — | — | — | — | *aucun agent trouvé* |

## Sources agents Cursor

| ID | Agent | bcId | URL |
|---|---|---|---|
| P01 | Bootstrap, config et assemblage | `bc-019fb865-afbc-7a96-a2b4-eeb1f8e38476` | https://cursor.com/agents/bc-019fb865-afbc-7a96-a2b4-eeb1f8e38476 |
| P02 | Auth et sécurité HTTP | `bc-019fb865-e1ca-7440-bdf3-87cbfd45fc6d` | https://cursor.com/agents/bc-019fb865-e1ca-7440-bdf3-87cbfd45fc6d |
| P03 | API REST routeurs | `bc-019fb866-2ab1-7b98-ae0d-6de5e08698b3` | https://cursor.com/agents/bc-019fb866-2ab1-7b98-ae0d-6de5e08698b3 |
| P04 | WebSocket, chat, voix, actions | `bc-019fb866-6788-7f50-a4c9-702e84c2357b` | https://cursor.com/agents/bc-019fb866-6788-7f50-a4c9-702e84c2357b |
| P05 | Agents, LLM, prompts | `bc-019fb866-b5a9-7c88-a270-32f88b2789dd` | https://cursor.com/agents/bc-019fb866-b5a9-7c88-a270-32f88b2789dd |
| P06 | Database et migrations | `bc-019fb866-e425-7a71-b0f2-f922ff187ad5` | https://cursor.com/agents/bc-019fb866-e425-7a71-b0f2-f922ff187ad5 |
| P07 | Event bus et notifications | `bc-019fb867-1d2f-7ae3-86d5-badae2c975fb` | https://cursor.com/agents/bc-019fb867-1d2f-7ae3-86d5-badae2c975fb |
| P08 | Intégrations OS/cloud | `bc-019fb867-4e8a-753b-bd17-a9bcda25b111` | https://cursor.com/agents/bc-019fb867-4e8a-753b-bd17-a9bcda25b111 |
| P09 | Audio STT/TTS | `bc-019fb86f-7099-70a4-8e3b-8fc13574cec2` | https://cursor.com/agents/bc-019fb86f-7099-70a4-8e3b-8fc13574cec2 |
| P10 | Daemon multi-device screen | `bc-019fb873-3149-73f9-87f4-f5472ce1d257` | https://cursor.com/agents/bc-019fb873-3149-73f9-87f4-f5472ce1d257 |
| P11 | Workers, scheduler, qualité | `bc-019fb873-7009-7ceb-8ff3-9016e9c23bbe` | https://cursor.com/agents/bc-019fb873-7009-7ceb-8ff3-9016e9c23bbe |
| P12 | Cognitif, Cursor, DevAgent | `bc-019fb873-a157-7bab-8ad5-1fad11fd3c30` | https://cursor.com/agents/bc-019fb873-a157-7bab-8ad5-1fad11fd3c30 |
| P13 | Module Fitness | `bc-019fb874-5fe8-7a95-ba92-e371bff39610` | https://cursor.com/agents/bc-019fb874-5fe8-7a95-ba92-e371bff39610 |
| P14 | Frontend bureau + jarvis_auth | `bc-019fb874-dfa3-7a38-bf46-e471cc3f8282` | https://cursor.com/agents/bc-019fb874-dfa3-7a38-bf46-e471cc3f8282 |

## Synthèse des sévérités

- Total findings indexés : **166**
- CRITICAL : 3
- HIGH : 60
- MEDIUM : 57
- LOW : 31
- INFO : 15

## Findings classés du pire au moindre

### CRITICAL

#### G-001 — `F-P01-001` (P01)
- Titre : ** LaunchAgents pointent vers un chemin inexistant
- Preuve : ** `ProgramArguments` / `WorkingDirectory` / logs → `/Users/zeldris/JarvisAPI/...` ; `ls /Users/zeldris/JarvisAPI` → *No such file or directory* ; workspace réel = `/Users/zeldris/JARVIS`.
- Impact : ** `launchd` ne peut pas démarrer supervisor ni daemon iMessage depuis ces plists.
- Rapport : [`P01_bootstrap_config_assemblage.md`](./rapports_bruts/P01_bootstrap_config_assemblage.md)

#### G-002 — `F-P05-010` (P05)
- Titre : Sévérité : MEDIUM
- Type : sécurité
- Preuve : `prompts/email_analyzer.txt:11-14` ; `prompts/imessage_extractor.txt:6-7` ; `prompts/continuous_extractor.txt:5-6` (pas d’instruction ignore-overrides) — contraste `agents/__init__.py:132-133` (voix seulement)
- Impact : Injection prompt via corps mail / messages / transcript vers extracteurs JSON (moins critique que terminal, mais peut polluer faits/notifs).
- Rapport : [`P05_agents_llm_prompts.md`](./rapports_bruts/P05_agents_llm_prompts.md)

#### G-003 — `P13-F-01` (P13)
- Titre : Avalement total des erreurs LLM advice
- Rapport : [`P13_module_fitness.md`](./rapports_bruts/P13_module_fitness.md)

### HIGH

#### G-004 — `F-P01-002` (P01)
- Titre : ** API supervisor non authentifiée (start/stop/restart/proxy)
- Preuve : ** Routes `POST /api/supervisor/{sid}/start|stop|restart`, `start-all`, `stop-all`, WS `/ws/supervisor` sans session/CSRF/token. Bind = `config.WEB_HOST`.
- Impact : ** Sur loopback (défaut) : tout process local contrôle le backend. Si `WEB_ALLOW_NETWORK_BIND=true`, surface réseau de contrôle total sans auth.
- Rapport : [`P01_bootstrap_config_assemblage.md`](./rapports_bruts/P01_bootstrap_config_assemblage.md)

#### G-005 — `F-P01-003` (P01)
- Titre : ** CORS credentials + origines cross-port localhost
- Preuve : **
- Impact : ** Navigateur sur un port listé peut envoyer le cookie `jarvis_session` cross-origin vers l’API. Atténué hors-périmètre par CSRF Origin+token, mais surface CORS trop large ; `http://0.0.0.0:3000` n’est pas une Origin navigateur réelle.
- Rapport : [`P01_bootstrap_config_assemblage.md`](./rapports_bruts/P01_bootstrap_config_assemblage.md)

#### G-006 — `F-P01-004` (P01)
- Titre : ** Secret LLM non fail-closed au bootstrap
- Preuve : ** `DEEPSEEK_API_KEY = _get("DEEPSEEK_API_KEY", "")` — process démarre sans clé. `.env.example` marque OBLIGATOIRE, `config` n’abort pas.
- Impact : ** Service « up » mais sourd cognitivement ; erreurs tardives ; monitoring trompeur.
- Rapport : [`P01_bootstrap_config_assemblage.md`](./rapports_bruts/P01_bootstrap_config_assemblage.md)

#### G-007 — `F-P01-005` (P01)
- Titre : ** `SECRET_ENV_KEYS` déclaré mais jamais appliqué
- Preuve : ** `SECRET_ENV_KEYS` frozenset ; `load_jarvis_env()` charge `.env.config` puis `.env` sans vérifier où vivent les secrets ; aucune autre référence repo à `SECRET_ENV_KEYS`.
- Impact : ** Secrets peuvent vivre dans `.env.config` versionnable / partagé ; politique non exécutée.
- Rapport : [`P01_bootstrap_config_assemblage.md`](./rapports_bruts/P01_bootstrap_config_assemblage.md)

#### G-008 — `F1` (P02)
- Titre : Pairing mobile sans rate — limit (A04)
- Rapport : [`P02_auth_securite_http.md`](./rapports_bruts/P02_auth_securite_http.md)

#### G-009 — `F-P03-001` (P03)
- Titre : Fuite d’exceptions internes au client
- Rapport : [`P03_api_rest_routeurs.md`](./rapports_bruts/P03_api_rest_routeurs.md)

#### G-010 — `F-P03-002` (P03)
- Titre : Contrat Phase 4 OpenAPI/routes obsolète
- Rapport : [`P03_api_rest_routeurs.md`](./rapports_bruts/P03_api_rest_routeurs.md)

#### G-011 — `F-P04-001` (P04)
- Titre : Sévérité : HIGH
- Type : sécurité
- Preuve : `api/ws_handler.py:41-49`
- Impact : une page same-site (autre port localhost) peut ouvrir `/ws` avec le cookie, lire le chat et déclencher des actions.
- Rapport : [`P04_websocket_chat_voix_actions.md`](./rapports_bruts/P04_websocket_chat_voix_actions.md)

#### G-012 — `F-P04-002` (P04)
- Titre : Sévérité : HIGH
- Type : sécurité
- Preuve : `api/ws_handler.py:323-336`
- Impact : calendrier, tâches, TV, open_app, clipboard… sans proposition serveur. Terminal reste protégé par `shell_plan_id` opaque, pas les autres types.
- Rapport : [`P04_websocket_chat_voix_actions.md`](./rapports_bruts/P04_websocket_chat_voix_actions.md)

#### G-013 — `F-P04-003` (P04)
- Titre : Sévérité : HIGH
- Type : sécurité
- Preuve : `api/chat_actions.py:230-241`
- Impact : « lance pas » / « exécute pas » déclenche l’action pending (dont plan shell).
- Rapport : [`P04_websocket_chat_voix_actions.md`](./rapports_bruts/P04_websocket_chat_voix_actions.md)

#### G-014 — `F-P04-004` (P04)
- Titre : Sévérité : HIGH
- Type : sécurité
- Preuve : `api/router_mobile_chat.py:136-141`
- Impact : UI « annulé » mais `_pending_proposal` et `shell_plan_id` restent vivants ; un « oui » ultérieur exécute.
- Rapport : [`P04_websocket_chat_voix_actions.md`](./rapports_bruts/P04_websocket_chat_voix_actions.md)

#### G-015 — `F-P04-005` (P04)
- Titre : Sévérité : HIGH
- Type : sécurité
- Preuve : `api/chat_actions.py:298-325` + usage `api/ws_handler.py:357-367`
- Impact : secrets/PII locaux et prompt injection via sorties non fiables quittent la machine vers DeepSeek ; le client reçoit aussi le brut dans `action_result`.
- Rapport : [`P04_websocket_chat_voix_actions.md`](./rapports_bruts/P04_websocket_chat_voix_actions.md)

#### G-016 — `F-P04-006` (P04)
- Titre : Sévérité : HIGH
- Type : sécurité
- Preuve : `api/chat_actions.py:387-406`
- Impact : exemple / citation / injection documentaire `{"type":"task",...}` devient action réelle.
- Rapport : [`P04_websocket_chat_voix_actions.md`](./rapports_bruts/P04_websocket_chat_voix_actions.md)

#### G-017 — `F-P04-007` (P04)
- Titre : Sévérité : HIGH
- Type : contrat-cassé
- Preuve : `api/voice_processing.py:108-111,191-197` ; `api/mobile_voice_service.py:143-149` ; contraste `api/ws_messages.py:51-58`
- Impact : pas d’enrichissement documents/mails/calendar/tâches ; actions/persistance/titrage différents du chat texte. CLAUDE.md annonce le contraire.
- Rapport : [`P04_websocket_chat_voix_actions.md`](./rapports_bruts/P04_websocket_chat_voix_actions.md)

#### G-018 — `F-P04-008` (P04)
- Titre : Sévérité : HIGH
- Type : bug
- Preuve : `api/ws_messages.py:226-232,261-263` (consommation) ; frontière `agents/school.py:78-97`
- Impact : chunks = affichage sans blocs action ; l’action n’est jamais extraite ni exécutée en stream SCHOOL.
- Rapport : [`P04_websocket_chat_voix_actions.md`](./rapports_bruts/P04_websocket_chat_voix_actions.md)

#### G-019 — `F-P04-009` (P04)
- Titre : Sévérité : HIGH
- Type : bug
- Preuve : `api/router_mobile_chat.py:68-76,90-119`
- Impact : double LLM / double action sur retry concurrent du même `client_message_id`.
- Rapport : [`P04_websocket_chat_voix_actions.md`](./rapports_bruts/P04_websocket_chat_voix_actions.md)

#### G-020 — `F-P04-010` (P04)
- Titre : Sévérité : HIGH
- Type : bug
- Preuve : `api/ws_handler.py:82-83,97-100,142-146`
- Impact : aucun JSON/`voice_cancel` lu pendant STT/LLM/TTS ; l’annulation arrive trop tard.
- Rapport : [`P04_websocket_chat_voix_actions.md`](./rapports_bruts/P04_websocket_chat_voix_actions.md)

#### G-021 — `F-P04-014` (P04)
- Titre : Sévérité : MEDIUM
- Type : bug
- Preuve : `api/ws_handler.py:142-146` + retours anticipés `api/ws_messages.py:186-214,478-488`
- Impact : blobs PTT ignorés indéfiniment (`if is_speaking: continue`) sans `speech_done`/`done_playing`.
- Rapport : [`P04_websocket_chat_voix_actions.md`](./rapports_bruts/P04_websocket_chat_voix_actions.md)

#### G-022 — `F-P04-016` (P04)
- Titre : Sévérité : MEDIUM
- Type : sécurité
- Preuve : `actions.py:292-295`
- Impact : `"confirmed":"false"` / `1` exécute si `shell_plan_id` valide.
- Rapport : [`P04_websocket_chat_voix_actions.md`](./rapports_bruts/P04_websocket_chat_voix_actions.md)

#### G-023 — `F-P04-017` (P04)
- Titre : Sévérité : MEDIUM
- Type : perf
- Preuve : `api/router_mobile_voice.py:24-25` ; `api/mobile_voice_service.py:47-51`
- Impact : OOM / DoS mémoire par client Bearer authentifié avant 413.
- Rapport : [`P04_websocket_chat_voix_actions.md`](./rapports_bruts/P04_websocket_chat_voix_actions.md)

#### G-024 — `F-P04-018` (P04)
- Titre : Sévérité : MEDIUM
- Type : contrat-cassé
- Preuve : `api/chat_cognitive.py:60-70,109-114` ; appel conditionnel `api/chat_processing.py:91-100`
- Impact : après « dites lance », le message `lance` ne confirme pas le job (sauf voie vocale).
- Rapport : [`P04_websocket_chat_voix_actions.md`](./rapports_bruts/P04_websocket_chat_voix_actions.md)

#### G-025 — `F-P04-019` (P04)
- Titre : Sévérité : LOW
- Type : sécurité
- Preuve : `api/chat_context.py:64` ; `api/mobile_voice_service.py:135-141`
- Impact : sujets relationnels / paroles utilisateur dans fichiers logs hors `llm_action_logs` redactés.
- Rapport : [`P04_websocket_chat_voix_actions.md`](./rapports_bruts/P04_websocket_chat_voix_actions.md)

#### G-026 — `F-P04-020` (P04)
- Titre : Sévérité : LOW
- Type : bug
- Preuve : `api/ws_messages.py:457-468`
- Impact : UI reçoit `title=None` ; pas de second event quand le titre existe.
- Rapport : [`P04_websocket_chat_voix_actions.md`](./rapports_bruts/P04_websocket_chat_voix_actions.md)

#### G-027 — `F-P04-021` (P04)
- Titre : Sévérité : INFO
- Type : doc-drift
- Preuve : `actions.py:1-6`
- Impact : masque les vrais appelants (`api/ws_*`, `chat_*`, `voice_*`, mobile).
- Rapport : [`P04_websocket_chat_voix_actions.md`](./rapports_bruts/P04_websocket_chat_voix_actions.md)

#### G-028 — `F-P05-001` (P05)
- Titre : Sévérité : HIGH
- Type : contrat-cassé | dead-code
- Preuve : `agents/coach.py:162-167` + `config.py:580`
- Impact : Pré-check flash à chaque tour coach non-vocal sans gain de qualité ; doc CLAUDE.md « Opus » mensongère.
- Rapport : [`P05_agents_llm_prompts.md`](./rapports_bruts/P05_agents_llm_prompts.md)

#### G-029 — `F-P05-002` (P05)
- Titre : Sévérité : HIGH
- Type : bug | contrat-cassé
- Preuve : `prompts/coach.txt:73-74` + `agents/display_text.py:41-43`
- Impact : Texte utilisateur peut commencer par `[DEEP_ANALYSIS]` ; l’escalade réelle est pré-appel (`_should_escalate`), pas ce tag → instruction morte + fuite.
- Rapport : [`P05_agents_llm_prompts.md`](./rapports_bruts/P05_agents_llm_prompts.md)

#### G-030 — `F-P05-003` (P05)
- Titre : Sévérité : HIGH
- Type : sécurité
- Preuve : `agents/__init__.py:96-117` (+ duplication messages chat `185-191`)
- Impact : Contenu user/assistant traité comme instructions système ; surface d’injection ; tokens doublés (system + messages).
- Rapport : [`P05_agents_llm_prompts.md`](./rapports_bruts/P05_agents_llm_prompts.md)

#### G-031 — `F-P05-005` (P05)
- Titre : Sévérité : HIGH
- Type : contrat-cassé | doc-drift
- Preuve : `prompts/devops.txt:1` + `agents/devops.py` (`inject_persona` défaut True) + `prompts/persona.txt:16`
- Impact : Conflit system prompt fort → risque élevé de réponse « je suis l’agent… » à l’utilisateur.
- Rapport : [`P05_agents_llm_prompts.md`](./rapports_bruts/P05_agents_llm_prompts.md)

#### G-032 — `F-P05-006` (P05)
- Titre : Sévérité : HIGH
- Type : doc-drift | sécurité
- Preuve : `jarvis/router.py:1-6`, `29-32`, `56-65` + `jarvis/models.py:16`
- Impact : Fausse promesse de privacy dans le system prompt ; `LocalBackend` / `DataSource.MESSAGES→LOCAL` hors sync avec la politique 2026.
- Rapport : [`P05_agents_llm_prompts.md`](./rapports_bruts/P05_agents_llm_prompts.md)

#### G-033 — `F-P05-012` (P05)
- Titre : Sévérité : LOW
- Type : dead-code | dette
- Preuve : `config.py:577` vs `agents/productivity.py` (`model = DEEPSEEK_MAIN_MODEL`, `_route_task` non-heavy → `self.model`)
- Impact : Triage toujours main (coût) ; doc « Haiku triage » morte.
- Rapport : [`P05_agents_llm_prompts.md`](./rapports_bruts/P05_agents_llm_prompts.md)

#### G-034 — `F-P05-013` (P05)
- Titre : Sévérité : LOW
- Type : smell
- Preuve : `agents/orchestrator.py:639-640`
- Impact : Violation persona (rare path).
- Rapport : [`P05_agents_llm_prompts.md`](./rapports_bruts/P05_agents_llm_prompts.md)

#### G-035 — `F-P05-015` (P05)
- Titre : Sévérité : LOW
- Type : doc-drift
- Preuve : `llm.py:75-80` (« use_cache … ignoré ») ; cache hit lu `147-148` seulement
- Impact : Appelants croient contrôler le cache ; pas de faille runtime.
- Rapport : [`P05_agents_llm_prompts.md`](./rapports_bruts/P05_agents_llm_prompts.md)

#### G-036 — `F-P05-017` (P05)
- Titre : Sévérité : INFO
- Type : doc-drift
- Preuve : `agents/orchestrator.py:3,338` ; `agents/info.py:16` ; `_call_claude` nom `agents/__init__.py:167` ; `agents/memory.py:9-10`
- Impact : Maintenabilité ; pas de bug runtime.
- Rapport : [`P05_agents_llm_prompts.md`](./rapports_bruts/P05_agents_llm_prompts.md)

#### G-037 — `F-P05-018` (P05)
- Titre : Sévérité : INFO
- Type : smell
- Preuve : scan `prompts/**` (seule mention `sk-` = guidance `prompts/cursor/security_audit.md:24`) ; `llm.py:150-157` + `agents/__init__.py:247-255`
- Impact : Point positif checklist 8 (partiel à cause de F-P05-004).
- Rapport : [`P05_agents_llm_prompts.md`](./rapports_bruts/P05_agents_llm_prompts.md)

#### G-038 — `P06-P1-01` (P06)
- Titre : Allowlist absente sur `upsert_person` — injection colonne via LLM
- Rapport : [`P06_database_migrations.md`](./rapports_bruts/P06_database_migrations.md)

#### G-039 — `P06-P1-02` (P06)
- Titre : Même motif sur `update_conversation` / `upsert_relationship_profile`
- Rapport : [`P06_database_migrations.md`](./rapports_bruts/P06_database_migrations.md)

#### G-040 — `F-P07-01` (P07)
- Titre : Handler sync SQLite sur la boucle asyncio
- Rapport : [`P07_event_bus_notifications.md`](./rapports_bruts/P07_event_bus_notifications.md)

#### G-041 — `F-P07-02` (P07)
- Titre : Handler TTS attendu dans le même `gather` que le bus
- Rapport : [`P07_event_bus_notifications.md`](./rapports_bruts/P07_event_bus_notifications.md)

#### G-042 — `F-P08-01` (P08)
- Titre : F-P08-01
- Rapport : [`P08_integrations_os_cloud.md`](./rapports_bruts/P08_integrations_os_cloud.md)

#### G-043 — `F-P08-02` (P08)
- Titre : F-P08-02
- Rapport : [`P08_integrations_os_cloud.md`](./rapports_bruts/P08_integrations_os_cloud.md)

#### G-044 — `F-P09-001` (P09)
- Titre : Sévérité : HIGH
- Type : bug
- Preuve : `scripts/audio_daemon.py:1368-1372` + `795-809`
- Impact : après un tour TTS (défaut `AUDIO_DAEMON_HALF_DUPLEX=True`), le lecteur PortAudio peut quitter ; micro mort jusqu’au restart watchdog / boucle immortelle (dizaines de secondes).
- Rapport : [`P09_audio_stt_tts.md`](./rapports_bruts/P09_audio_stt_tts.md)

#### G-045 — `F-P09-002` (P09)
- Titre : Sévérité : HIGH
- Type : contrat-cassé
- Preuve : `audio/tts.py:55-66` (validation puis `_synth_edge(text)` sans emotion) ; `audio/tts.py:277-296` (Kokoro) ; `audio/tts.py:363-373` (macOS) ; `native_audio/ttskit_mlx.py:102-108` (`instruct=None` explicite)
- Impact : tags `[warm]`/`[urgent]` etc. n’influencent ni débit, ni pitch, ni voix — contrat CLAUDE.md / persona non honoré côté synthèse.
- Rapport : [`P09_audio_stt_tts.md`](./rapports_bruts/P09_audio_stt_tts.md)

#### G-046 — `F-P09-009` (P09)
- Titre : Sévérité : LOW
- Type : dead-code
- Preuve : `scripts/audio_daemon.py:1534-1538` (`_start_wake_detection` no-op) ; `1554-1646` (boucles encore présentes)
- Impact : maintenance trompeuse ; wake réel = volume sur flux unique (`934-964`).
- Rapport : [`P09_audio_stt_tts.md`](./rapports_bruts/P09_audio_stt_tts.md)

#### G-047 — `F-P09-010` (P09)
- Titre : Sévérité : LOW
- Type : sécurité
- Preuve : `audio/vad_silero.py:81-86` (`torch.hub.load(..., trust_repo=True)`)
- Impact : hors contrat STT cloud, mais téléchargement réseau au démarrage VAD ; `trust_repo=True` élargit la surface.
- Rapport : [`P09_audio_stt_tts.md`](./rapports_bruts/P09_audio_stt_tts.md)

#### G-048 — `F-P09-011` (P09)
- Titre : Sévérité : INFO
- Type : doc-drift
- Preuve : `audio/tts.py:150-151` / `engine_config.py:12` pointent vers `models/kokoro/` ; `find` → répertoire inexistant
- Impact : backend `KOKORO_BACKEND=onnx` toujours `available=False` ici ; seuls MLX/macOS/Edge restent.
- Rapport : [`P09_audio_stt_tts.md`](./rapports_bruts/P09_audio_stt_tts.md)

#### G-049 — `F-P09-012` (P09)
- Titre : Sévérité : INFO
- Type : smell
- Preuve : `audio/tts.py:109-111`, `419-454` (stderr `DEVNULL` sur `say`/`afconvert`) ; `audio/stt_daemon.py:271-273`, `631-633`
- Impact : robustesse OK (pas de crash) ; diagnostic macOS TTS difficile (pas de stderr).
- Rapport : [`P09_audio_stt_tts.md`](./rapports_bruts/P09_audio_stt_tts.md)

#### G-050 — `P10-C01` (P10)
- Titre : Vision remote cassée (signature)
- Rapport : [`P10_daemon_multi_device_screen.md`](./rapports_bruts/P10_daemon_multi_device_screen.md)

#### G-051 — `P11-F1` (P11)
- Titre : `scheduler.py` — Critère « chaque job ENABLED » non tenu : briefing/soir/hebdo/relations/coffee/mood/doomscroll/missed/maintenance sans kill-switch dédié.
- Rapport : [`P11_workers_scheduler_qualite.md`](./rapports_bruts/P11_workers_scheduler_qualite.md)

#### G-052 — `P11-F2` (P11)
- Titre : `location_analyzer.py` — Fenêtre 30 j rejouée chaque nuit → inserts patterns/faits répétés + coût LLM récurrent + notifs possibles.
- Rapport : [`P11_workers_scheduler_qualite.md`](./rapports_bruts/P11_workers_scheduler_qualite.md)

#### G-053 — `P11-F3` (P11)
- Titre : `relationship_analyzer.py` — Sur échec LLM/JSON, le curseur ROWID avance quand même → messages perdus définitivement.
- Rapport : [`P11_workers_scheduler_qualite.md`](./rapports_bruts/P11_workers_scheduler_qualite.md)

#### G-054 — `P11-F4` (P11)
- Titre : `email_watcher.py` + `catchup_after_downtime.py` — Pas de verrou inter-processus : catch-up parallèle au watcher → double LLM + double tâches/mac/iMessage.
- Rapport : [`P11_workers_scheduler_qualite.md`](./rapports_bruts/P11_workers_scheduler_qualite.md)

#### G-055 — `P11-F5` (P11)
- Titre : `rituals.py` (roast/debrief) — Pas de guard « déjà généré aujourd’hui » avant LLM → rerun = re-coût + overwrite + TTS/notif.
- Rapport : [`P11_workers_scheduler_qualite.md`](./rapports_bruts/P11_workers_scheduler_qualite.md)

#### G-056 — `P11-F6` (P11)
- Titre : `db_migrations.py` — `executescript` puis `record_migration` hors même transaction ; `DB_MIGRATIONS_AUTO_APPLY=true` par défaut → risque replay partiel.
- Rapport : [`P11_workers_scheduler_qualite.md`](./rapports_bruts/P11_workers_scheduler_qualite.md)

#### G-057 — `F-P12-001` (P12)
- Titre : Sévérité : HIGH
- Type : sécurité
- Preuve : `agents/devagent/loop.py:37-41`
- Impact : un `rel` du type `../../../agents/foo.py` peut écrire dans le tree JARVIS hors `DEV_PROJECTS_ROOT/{slug}/`.
- Rapport : [`P12_cognitif_cursor_devagent.md`](./rapports_bruts/P12_cognitif_cursor_devagent.md)

#### G-058 — `F-P12-011` (P12)
- Titre : Sévérité : INFO
- Type : smell
- Preuve : `agents/autonomous_loop.py:234-242` — `auto_start=True`, `require_confirmation=False`. Commentaire : mode autonome explicite.
- Impact : risque accepté si l’utilisateur tape `/loop` ; pas un bypass chat/voix (ceux-ci passent par confirmation).
- Rapport : [`P12_cognitif_cursor_devagent.md`](./rapports_bruts/P12_cognitif_cursor_devagent.md)

#### G-059 — `P13-F-02` (P13)
- Titre : UI réglages → `0` / `""` vs bornes Pydantic
- Rapport : [`P13_module_fitness.md`](./rapports_bruts/P13_module_fitness.md)

#### G-060 — `P13-F-03` (P13)
- Titre : Async + SQLite sync sur event loop
- Rapport : [`P13_module_fitness.md`](./rapports_bruts/P13_module_fitness.md)

#### G-061 — `P13-F-04` (P13)
- Titre : Isolation schéma partielle
- Rapport : [`P13_module_fitness.md`](./rapports_bruts/P13_module_fitness.md)

#### G-062 — `P13-F-05` (P13)
- Titre : Calculs progress incohérents
- Rapport : [`P13_module_fitness.md`](./rapports_bruts/P13_module_fitness.md)

#### G-063 — `P14-F01` (P14)
- Titre : ChatView affiche le nom d’agent
- Rapport : [`P14_frontend_bureau_jarvis_auth.md`](./rapports_bruts/P14_frontend_bureau_jarvis_auth.md)

### MEDIUM

#### G-064 — `F-P01-006` (P01)
- Titre : ** Défauts config permissifs (capabilities actives)
- Preuve : ** Défauts `true` : `COMPUTER_ACCESS`, `CODE_EXECUTOR_ENABLED`, `DAEMON_ENABLED`, `SCREEN_WATCHER_ENABLED`, `LOOP_UNLIMITED`, `DEVAGENT_AUTO_PR`, `DEVAGENT_AUTO_DEPLOY_STAGING`, `CURSOR_DELEGATION_ENABLED`. Contrastent avec fail-closed sur push/PR Cursor (`CURSOR_ALLOW_PUSH/PR=false`, L481–482) et s
- Impact : ** Machine fraîche avec `.env` minimal active shell/computer, executor, daemon, loop illimité, auto-PR.
- Rapport : [`P01_bootstrap_config_assemblage.md`](./rapports_bruts/P01_bootstrap_config_assemblage.md)

#### G-065 — `F-P01-007` (P01)
- Titre : ** `COMPUTER_ACCESS` typé string, pas bool
- Preuve : ** `COMPUTER_ACCESS = _get("COMPUTER_ACCESS", "true")` (str). Consommateur hors-périmètre `bool(config.COMPUTER_ACCESS)` traiterait `"false"` comme True.
- Impact : ** Footgun de désactivation.
- Rapport : [`P01_bootstrap_config_assemblage.md`](./rapports_bruts/P01_bootstrap_config_assemblage.md)

#### G-066 — `F-P01-008` (P01)
- Titre : ** Fuite de descripteurs fichiers logs au restart backend
- Preuve : ** `stdout=open(..., "a")` sans `close` / context manager à chaque `_start_sync`.
- Impact : ** FD leak sous crash-loop health-check (L901–930).
- Rapport : [`P01_bootstrap_config_assemblage.md`](./rapports_bruts/P01_bootstrap_config_assemblage.md)

#### G-067 — `F-P01-009` (P01)
- Titre : ** Dépendance TTS critique absente de `requirements.txt`
- Preuve : ** Aucun `mlx-audio` / `mlx` dans `requirements*.txt`. Code productif (hors périmètre mais checklist) importe `mlx_audio`. Install « standard » → Kokoro MLX cassé.
- Impact : ** Écart déclaration / runtime ; onboarding trompeur.
- Rapport : [`P01_bootstrap_config_assemblage.md`](./rapports_bruts/P01_bootstrap_config_assemblage.md)

#### G-068 — `F-P01-010` (P01)
- Titre : ** `aiohttp` utilisé ailleurs, absent des requirements
- Preuve : ** Checklist ; `import aiohttp` présent dans le repo (`scripts/tv_mcp_server.py`, hors inclusion code mais in-scope deps).
- Impact : ** Environnement CI/dev incomplet pour outils TV MCP.
- Rapport : [`P01_bootstrap_config_assemblage.md`](./rapports_bruts/P01_bootstrap_config_assemblage.md)

#### G-069 — `F-P01-011` (P01)
- Titre : ** Ordre middleware : security outer, CORS inner
- Preuve : ** `add_middleware(CORS)` puis `app.middleware("http")(security_middleware)` → Starlette `insert(0)` place security en tête de `user_middleware` → wrap reverse → security outermost.
- Impact : ** Réponses anticipées 401/403/428 du security middleware peuvent sortir sans en-têtes `Access-Control-*` pour clients cross-origin (dev Vite).
- Rapport : [`P01_bootstrap_config_assemblage.md`](./rapports_bruts/P01_bootstrap_config_assemblage.md)

#### G-070 — `F2` (P02)
- Titre : PIN minimum 4 chiffres (A02 / doc drift)
- Rapport : [`P02_auth_securite_http.md`](./rapports_bruts/P02_auth_securite_http.md)

#### G-071 — `F3` (P02)
- Titre : Bypass `/api/control/*` local (A01)
- Rapport : [`P02_auth_securite_http.md`](./rapports_bruts/P02_auth_securite_http.md)

#### G-072 — `F4` (P02)
- Titre : CSP `connect — src` autorise tout WebSocket (A05)
- Rapport : [`P02_auth_securite_http.md`](./rapports_bruts/P02_auth_securite_http.md)

#### G-073 — `F5` (P02)
- Titre : CSRF Origin optionnel (A01)
- Rapport : [`P02_auth_securite_http.md`](./rapports_bruts/P02_auth_securite_http.md)

#### G-074 — `F-P03-003` (P03)
- Titre : Cookie desktop absent sur repli Vite
- Rapport : [`P03_api_rest_routeurs.md`](./rapports_bruts/P03_api_rest_routeurs.md)

#### G-075 — `F-P04-011` (P04)
- Titre : Sévérité : MEDIUM
- Type : bug
- Preuve : `api/ws_messages.py:186-214`
- Impact : plan expiré / `ok=false` → exception après `action_result`, erreur générique client.
- Rapport : [`P04_websocket_chat_voix_actions.md`](./rapports_bruts/P04_websocket_chat_voix_actions.md)

#### G-076 — `F-P04-012` (P04)
- Titre : Sévérité : MEDIUM
- Type : bug
- Preuve : `api/ws_messages.py:66-67,186` + `api/chat_actions.py:243-247`
- Impact : « oui » n’est plus une confirmation exacte → proposition annulée silencieusement.
- Rapport : [`P04_websocket_chat_voix_actions.md`](./rapports_bruts/P04_websocket_chat_voix_actions.md)

#### G-077 — `F-P04-013` (P04)
- Titre : Sévérité : MEDIUM
- Type : bug
- Preuve : `api/chat_actions.py:23-25,265-269`
- Impact : écrasement mutuel multi-onglets/appareils ; plans orphelins.
- Rapport : [`P04_websocket_chat_voix_actions.md`](./rapports_bruts/P04_websocket_chat_voix_actions.md)

#### G-078 — `F-P04-015` (P04)
- Titre : Sévérité : MEDIUM
- Type : sécurité
- Preuve : `actions.py:493-503`
- Impact : typo/`action` omise → lecture secrète + fuite follow-up (F-P04-005).
- Rapport : [`P04_websocket_chat_voix_actions.md`](./rapports_bruts/P04_websocket_chat_voix_actions.md)

#### G-079 — `F-P05-007` (P05)
- Titre : Sévérité : MEDIUM
- Type : bug
- Preuve : `agents/__init__.py:21-33` ; aussi `agents/briefing_engine.py:263,407` ; `agents/coach.py:142`
- Impact : Mauvaise « now » si TZ hôte ≠ Paris ou `TIMEZONE` env différent ; briefings/datation incorrects.
- Rapport : [`P05_agents_llm_prompts.md`](./rapports_bruts/P05_agents_llm_prompts.md)

#### G-080 — `F-P05-008` (P05)
- Titre : Sévérité : MEDIUM
- Type : bug | robustesse
- Preuve : `agents/school.py:30,110-118` ; `agents/journal.py:34,133-136` ; `agents/memory.py:47,155`
- Impact : Fence one-line DeepSeek (tolérée pour `action` dans `display_text.py:19`) → sauvegarde devoir / extraction journal/mémoire silencieusement ratée.
- Rapport : [`P05_agents_llm_prompts.md`](./rapports_bruts/P05_agents_llm_prompts.md)

#### G-081 — `F-P05-009` (P05)
- Titre : Sévérité : MEDIUM
- Type : contrat-cassé
- Preuve : `agents/orchestrator.py:568-590` (retourne seulement `memory_context` avec `[LIFE_PROFILE]`) + agents `setdefault("life_profile","")` ex. `coach.py:95` + `prompts/school.txt:1-3`
- Impact : Ordre « life puis memory » des prompts partiellement mort ; double section vide en tête.
- Rapport : [`P05_agents_llm_prompts.md`](./rapports_bruts/P05_agents_llm_prompts.md)

#### G-082 — `F-P05-014` (P05)
- Titre : Sévérité : LOW
- Type : smell
- Preuve : `agents/productivity.py:57-64` (`w.get('icon','')`)
- Impact : Contredit interdiction emoji ; peut biaiser la réponse.
- Rapport : [`P05_agents_llm_prompts.md`](./rapports_bruts/P05_agents_llm_prompts.md)

#### G-083 — `F-P05-016` (P05)
- Titre : Sévérité : INFO
- Type : smell
- Preuve : `jarvis/router.py:49` + méthodes `chat/mail/...` → DeepSeek uniquement ; `jarvis/backends/__init__.py:6-7`
- Impact : Dual-LLM trompeur ; code mort / confusion audit sécurité.
- Rapport : [`P05_agents_llm_prompts.md`](./rapports_bruts/P05_agents_llm_prompts.md)

#### G-084 — `P06-P2-03` (P06)
- Titre : `DATE(created_at)` / `DATE(completed_at)` sans `time_buckets`
- Rapport : [`P06_database_migrations.md`](./rapports_bruts/P06_database_migrations.md)

#### G-085 — `P06-P2-04` (P06)
- Titre : Doc-drift comptage tables
- Rapport : [`P06_database_migrations.md`](./rapports_bruts/P06_database_migrations.md)

#### G-086 — `P06-P2-05` (P06)
- Titre : `delete_conversation` ne purge pas `recordings` / `agentic_workflows`
- Rapport : [`P06_database_migrations.md`](./rapports_bruts/P06_database_migrations.md)

#### G-087 — `F-P07-03` (P07)
- Titre : Checksum ≠ sérialisation `event_log`
- Rapport : [`P07_event_bus_notifications.md`](./rapports_bruts/P07_event_bus_notifications.md)

#### G-088 — `F-P07-04` (P07)
- Titre : `emit_nowait` fallback `asyncio.run`
- Rapport : [`P07_event_bus_notifications.md`](./rapports_bruts/P07_event_bus_notifications.md)

#### G-089 — `F-P07-05` (P07)
- Titre : Dédup notifications non unique sous concurrence
- Rapport : [`P07_event_bus_notifications.md`](./rapports_bruts/P07_event_bus_notifications.md)

#### G-090 — `F-P08-03` (P08)
- Titre : F-P08-03
- Rapport : [`P08_integrations_os_cloud.md`](./rapports_bruts/P08_integrations_os_cloud.md)

#### G-091 — `F-P08-04` (P08)
- Titre : F-P08-04
- Rapport : [`P08_integrations_os_cloud.md`](./rapports_bruts/P08_integrations_os_cloud.md)

#### G-092 — `F-P09-003` (P09)
- Titre : Sévérité : MEDIUM
- Type : bug
- Preuve : `audio/stt_daemon.py:82-121` (filtre défini) ; `scripts/audio_daemon.py` — aucune occurrence ; usage hors périmètre `api/mobile_voice_service.py` (P04)
- Impact : sur silence/bruit, Whisper peut republier le `initial_prompt` ; le daemon ne rejette que ghosts YouTube + `avg_logprob`, pas l’écho de prompt.
- Rapport : [`P09_audio_stt_tts.md`](./rapports_bruts/P09_audio_stt_tts.md)

#### G-093 — `F-P09-004` (P09)
- Titre : Sévérité : MEDIUM
- Type : perf
- Preuve : `audio/audio_format.py:25-26` (`RIFF` → True) ; `audio/stt_daemon.py:651-653` (branche decode si encoded)
- Impact : latence STT accrue (ffmpeg/`decode_audio`) pour tout WAV déjà PCM, y compris chemins qui enverraient du RIFF.
- Rapport : [`P09_audio_stt_tts.md`](./rapports_bruts/P09_audio_stt_tts.md)

#### G-094 — `F-P09-005` (P09)
- Titre : Sévérité : MEDIUM
- Type : smell
- Preuve : `audio/stt_daemon.py:164` ; `166-207` / `216-217` (`preload_sync` sans `async with self._load_lock`)
- Impact : deux `transcribe_pcm` concurrentes au premier appel peuvent double-charger le modèle ou laisser `_load_failed` incohérent.
- Rapport : [`P09_audio_stt_tts.md`](./rapports_bruts/P09_audio_stt_tts.md)

#### G-095 — `F-P09-006` (P09)
- Titre : Sévérité : MEDIUM
- Type : smell
- Preuve : `audio/tts_cache.py:63-75` (`LastTTS._entry`) ; `78-124` (`SpeculativeTTS._cache` muté sans lock)
- Impact : daemon + WS (P04) peuvent lire/écrire concurremment → audio « répète » corrompu ou cache partiel.
- Rapport : [`P09_audio_stt_tts.md`](./rapports_bruts/P09_audio_stt_tts.md)

#### G-096 — `F-P09-007` (P09)
- Titre : Sévérité : MEDIUM
- Type : bug
- Preuve : `scripts/audio_daemon.py:1016-1020`
- Impact : sous charge (LLM lent), parole utilisateur perdue silencieusement (log only).
- Rapport : [`P09_audio_stt_tts.md`](./rapports_bruts/P09_audio_stt_tts.md)

#### G-097 — `P10-C02` (P10)
- Titre : Pas de plafond taille `image_b64`
- Rapport : [`P10_daemon_multi_device_screen.md`](./rapports_bruts/P10_daemon_multi_device_screen.md)

#### G-098 — `P10-C03` (P10)
- Titre : TTS remote sans cooldown / anti — spam
- Rapport : [`P10_daemon_multi_device_screen.md`](./rapports_bruts/P10_daemon_multi_device_screen.md)

#### G-099 — `P10-C04` (P10)
- Titre : Agent distant : pas d'exigence Tailscale/TLS
- Rapport : [`P10_daemon_multi_device_screen.md`](./rapports_bruts/P10_daemon_multi_device_screen.md)

#### G-100 — `P11-W1` (P11)
- Titre : `email_watcher.py` — `finally` ajoute l’ID même si analyse échoue → pas de retry jusqu’à restart/catch-up.
- Rapport : [`P11_workers_scheduler_qualite.md`](./rapports_bruts/P11_workers_scheduler_qualite.md)

#### G-101 — `P11-W10` (P11)
- Titre : `self_healing.py:221` — `getattr(..., "SELF_REPAIR_ENABLED", True)` — défaut dangereux si attribut absent (mitigé car présent dans `config`, défaut réel `false`).
- Rapport : [`P11_workers_scheduler_qualite.md`](./rapports_bruts/P11_workers_scheduler_qualite.md)

#### G-102 — `P11-W2` (P11)
- Titre : `email_watcher.py` — Cap 20 non-lus : backlog ancien peut rester bloqué si les 20 plus récents restent non lus.
- Rapport : [`P11_workers_scheduler_qualite.md`](./rapports_bruts/P11_workers_scheduler_qualite.md)

#### G-103 — `P11-W3` (P11)
- Titre : `email_watcher.py` — Fan-out volontaire mac + UI (+ push high) + iMessage ; dédup service 300 s ne couvre pas mac/iMessage.
- Rapport : [`P11_workers_scheduler_qualite.md`](./rapports_bruts/P11_workers_scheduler_qualite.md)

#### G-104 — `P11-W4` (P11)
- Titre : `commitments.py` / `jarvis_journal.py` — Reruns = re-coût LLM (journal overwrite upsert date).
- Rapport : [`P11_workers_scheduler_qualite.md`](./rapports_bruts/P11_workers_scheduler_qualite.md)

#### G-105 — `P11-W5` (P11)
- Titre : `self_improvement.py` — Sans fingerprint, chaque run peut re-proposer / re-déléguer Cursor si activé.
- Rapport : [`P11_workers_scheduler_qualite.md`](./rapports_bruts/P11_workers_scheduler_qualite.md)

#### G-106 — `P11-W6` (P11)
- Titre : `fitness_reminders.py` — Fenêtre entre notif et `record_prompt` → risque double high-notif sous concurrence.
- Rapport : [`P11_workers_scheduler_qualite.md`](./rapports_bruts/P11_workers_scheduler_qualite.md)

#### G-107 — `P11-W7` (P11)
- Titre : `contact_alerts.py` — Cutoff `datetime.now()` local vs timestamps UTC SQLite.
- Rapport : [`P11_workers_scheduler_qualite.md`](./rapports_bruts/P11_workers_scheduler_qualite.md)

#### G-108 — `P11-W8` (P11)
- Titre : `security_audit` / `duplicate_scanner` — Identité positionnelle → faux « nouveaux » findings si lignes bougent.
- Rapport : [`P11_workers_scheduler_qualite.md`](./rapports_bruts/P11_workers_scheduler_qualite.md)

#### G-109 — `P11-W9` (P11)
- Titre : `jarvis_full_restart.sh` — `$0` relatif fragile après `cd`.
- Rapport : [`P11_workers_scheduler_qualite.md`](./rapports_bruts/P11_workers_scheduler_qualite.md)

#### G-110 — `F-P12-002` (P12)
- Titre : Sévérité : MEDIUM
- Type : contrat-cassé
- Preuve : enqueue lit `CURSOR_ALLOW_PR` / `CURSOR_ALLOW_PUSH` (défauts false côté config) — `integrations/cursor_delegation.py:222-225` ; ouverture PR conditionnelle `427-431` ; `_maybe_open_pr` early-return si `not allow_push` `495-496`. Autonomy expose le mode `pr_only` `api/router_cognitive.py:321-324`. Te
- Impact : self-repair / self-improvement / loop peuvent marquer `completed` sur branche `jarvis/cursor/*` locale sans draft PR — conforme « jamais main », non conforme à la promesse produit « s’arrête à la PR ».
- Rapport : [`P12_cognitif_cursor_devagent.md`](./rapports_bruts/P12_cognitif_cursor_devagent.md)

#### G-111 — `F-P12-003` (P12)
- Titre : Sévérité : MEDIUM
- Type : sécurité
- Preuve : redaction secrets `jarvis/security/redaction.py:10-37` (hors P12, consommé ici) ; usage systématique `integrations/cursor_delegation.py:182-218,315,378` ; vue publique omet le brut `130-157` ; vue diagnostic garde `user_request` tronqué `160-172` ; API `diagnostic=true` `api/router_cognitive.py:143,
- Impact : emails/téléphones/noms dans une demande technique ne sont pas masqués avant envoi CLI / persistance ; un opérateur avec `diagnostic=true` lit le texte utilisateur (secrets masqués seulement).
- Rapport : [`P12_cognitif_cursor_devagent.md`](./rapports_bruts/P12_cognitif_cursor_devagent.md)

#### G-112 — `F-P12-004` (P12)
- Titre : Sévérité : MEDIUM
- Type : sécurité
- Preuve : `database/devagent.py:269-277` (`redact_action_log_payload`) vs `save_interview_context` JSON brut `184-195` ; `save_spec` / deployments sans redact (record_deployment ~344+).
- Impact : réponses d’interview / specs / stdout tests de staging peuvent persister secrets ou PII en clair dans SQLite.
- Rapport : [`P12_cognitif_cursor_devagent.md`](./rapports_bruts/P12_cognitif_cursor_devagent.md)

#### G-113 — `F-P12-005` (P12)
- Titre : Sévérité : MEDIUM
- Type : sécurité
- Preuve : `agents/devagent/executor.py:39-48` — `full_env = {**os.environ, **env}` puis `subprocess.run(...)`. Contraste Cursor : `build_cursor_safe_env` `integrations/cursor_env.py:42-70`.
- Impact : commandes projet (tests, git, LLM tools) héritent `DEEPSEEK_API_KEY`, tokens device, etc.
- Rapport : [`P12_cognitif_cursor_devagent.md`](./rapports_bruts/P12_cognitif_cursor_devagent.md)

#### G-114 — `F-P12-006` (P12)
- Titre : Sévérité : MEDIUM
- Type : robustesse / sécurité
- Preuve : `_insert_cursor_job_row` écrit `user_request`/`prompt_sent` tels quels `database/cursor_jobs.py:104-126` ; redaction seulement dans `update_cursor_job` `173-183` avec `except Exception: pass`.
- Impact : appelant qui bypasse `enqueue` (ou échec silencieux de l’import redaction) persiste du secret en clair ; update peut aussi skipper la redaction.
- Rapport : [`P12_cognitif_cursor_devagent.md`](./rapports_bruts/P12_cognitif_cursor_devagent.md)

#### G-115 — `F-P12-009` (P12)
- Titre : Sévérité : MEDIUM
- Type : contrat-cassé
- Preuve : `api/router_quality.py:64-73` (`security/{id}/fix`), `76-81` (génération tests), `20-28` (install hook git). Doc CLAUDE : report-only sur JARVIS sauf opt-in.
- Impact : contournement du chemin self-mod worktree+PR si flags `.env` activés — écriture directe tracked files / hooks.
- Rapport : [`P12_cognitif_cursor_devagent.md`](./rapports_bruts/P12_cognitif_cursor_devagent.md)

#### G-116 — `P14-F02` (P14)
- Titre : Mission Control expose les noms d’agents
- Rapport : [`P14_frontend_bureau_jarvis_auth.md`](./rapports_bruts/P14_frontend_bureau_jarvis_auth.md)

#### G-117 — `P14-F03` (P14)
- Titre : SSE hors client HTTP unique
- Rapport : [`P14_frontend_bureau_jarvis_auth.md`](./rapports_bruts/P14_frontend_bureau_jarvis_auth.md)

#### G-118 — `P14-F04` (P14)
- Titre : SW Vite : CacheFirst images sans denylist `/api`
- Rapport : [`P14_frontend_bureau_jarvis_auth.md`](./rapports_bruts/P14_frontend_bureau_jarvis_auth.md)

#### G-119 — `P14-F05` (P14)
- Titre : Soft lock ne purge pas IndexedDB
- Rapport : [`P14_frontend_bureau_jarvis_auth.md`](./rapports_bruts/P14_frontend_bureau_jarvis_auth.md)

#### G-120 — `P14-F06` (P14)
- Titre : Zoom bloqué (shell Vite)
- Rapport : [`P14_frontend_bureau_jarvis_auth.md`](./rapports_bruts/P14_frontend_bureau_jarvis_auth.md)

### LOW

#### G-121 — `F-P01-012` (P01)
- Titre : ** Pins dépendances trop larges
- Preuve : ** `fastapi==0.115.*`, `uvicorn[standard]==0.34.*`, `python-multipart==0.0.*`, `Pillow>=10.0`, `torch>=2.0`.
- Impact : ** Builds non reproductibles ; régression silencieuse.
- Rapport : [`P01_bootstrap_config_assemblage.md`](./rapports_bruts/P01_bootstrap_config_assemblage.md)

#### G-122 — `F-P01-013` (P01)
- Titre : ** Incohérences internes `.env.example`
- Preuve : ** `DEEPSEEK_BASE_URL` sans `/v1` puis avec `/v1` ; `TRIAGE_MODEL=qwen2.5:7b` alors que config défaut = `DEEPSEEK_FAST_MODEL` ; PIN « 4 chiffres » ; `DEV_PROJECTS_ROOT` dupliqué.
- Impact : ** Onboarding ambigu ; divergence runtime vs template.
- Rapport : [`P01_bootstrap_config_assemblage.md`](./rapports_bruts/P01_bootstrap_config_assemblage.md)

#### G-123 — `F-P01-014` (P01)
- Titre : ** Supervisor CORS divergent / WS control sans auth
- Preuve : ** Pas de `allow_credentials` ; origines proches de `main` mais pas `0.0.0.0` ; `/ws/supervisor` `accept()` immédiat.
- Impact : ** Incohérence ; état services exposé localement sans auth.
- Rapport : [`P01_bootstrap_config_assemblage.md`](./rapports_bruts/P01_bootstrap_config_assemblage.md)

#### G-124 — `F6` (P02)
- Titre : Clé VAPID privée en clair dans `app_settings` (A02)
- Rapport : [`P02_auth_securite_http.md`](./rapports_bruts/P02_auth_securite_http.md)

#### G-125 — `F7` (P02)
- Titre : `send_web_push` sans allowlist d’endpoint (A10)
- Rapport : [`P02_auth_securite_http.md`](./rapports_bruts/P02_auth_securite_http.md)

#### G-126 — `F8` (P02)
- Titre : Routes mobile bypass sans auth middleware (A01 surface)
- Rapport : [`P02_auth_securite_http.md`](./rapports_bruts/P02_auth_securite_http.md)

#### G-127 — `F-P03-004` (P03)
- Titre : Codes HTTP soft au lieu de 404
- Rapport : [`P03_api_rest_routeurs.md`](./rapports_bruts/P03_api_rest_routeurs.md)

#### G-128 — `F-P03-005` (P03)
- Titre : Endpoint debug en surface OpenAPI
- Rapport : [`P03_api_rest_routeurs.md`](./rapports_bruts/P03_api_rest_routeurs.md)

#### G-129 — `F-P03-006` (P03)
- Titre : Corps `dict` sans modèle Pydantic (422 faible)
- Rapport : [`P03_api_rest_routeurs.md`](./rapports_bruts/P03_api_rest_routeurs.md)

#### G-130 — `P06-P3-06` (P06)
- Titre : `get_db` imbriqués + double `commit` cursor_jobs
- Rapport : [`P06_database_migrations.md`](./rapports_bruts/P06_database_migrations.md)

#### G-131 — `P06-P3-07` (P06)
- Titre : `migrations/README.md` obsolète
- Rapport : [`P06_database_migrations.md`](./rapports_bruts/P06_database_migrations.md)

#### G-132 — `F-P07-06` (P07)
- Titre : Replay : API lecture sans moteur ni marquage
- Rapport : [`P07_event_bus_notifications.md`](./rapports_bruts/P07_event_bus_notifications.md)

#### G-133 — `F-P07-07` (P07)
- Titre : SSE : drop abonnés lents ; history RAM only
- Rapport : [`P07_event_bus_notifications.md`](./rapports_bruts/P07_event_bus_notifications.md)

#### G-134 — `F-P07-08` (P07)
- Titre : Types inconnus : warn puis emit quand même
- Rapport : [`P07_event_bus_notifications.md`](./rapports_bruts/P07_event_bus_notifications.md)

#### G-135 — `F-P08-05` (P08)
- Titre : F-P08-05
- Rapport : [`P08_integrations_os_cloud.md`](./rapports_bruts/P08_integrations_os_cloud.md)

#### G-136 — `F-P08-06` (P08)
- Titre : F-P08-06
- Rapport : [`P08_integrations_os_cloud.md`](./rapports_bruts/P08_integrations_os_cloud.md)

#### G-137 — `F-P09-008` (P09)
- Titre : Sévérité : LOW
- Type : dette
- Preuve : `scripts/audio_daemon.py:1828-1876` (input only) ; `audio/audio_output.py:96-97` / `161-163` (`OutputStream` sans `device=`)
- Impact : checklist « output device » non couverte — lecture toujours sur défaut système (AirPods etc.).
- Rapport : [`P09_audio_stt_tts.md`](./rapports_bruts/P09_audio_stt_tts.md)

#### G-138 — `P10-C05` (P10)
- Titre : Rate — limit pairing = IP socket
- Rapport : [`P10_daemon_multi_device_screen.md`](./rapports_bruts/P10_daemon_multi_device_screen.md)

#### G-139 — `P10-C06` (P10)
- Titre : Health devices : horodatages naïfs
- Rapport : [`P10_daemon_multi_device_screen.md`](./rapports_bruts/P10_daemon_multi_device_screen.md)

#### G-140 — `P10-C07` (P10)
- Titre : Curseur iMessage avancé avant traitement
- Rapport : [`P10_daemon_multi_device_screen.md`](./rapports_bruts/P10_daemon_multi_device_screen.md)

#### G-141 — `P10-C08` (P10)
- Titre : Échecs silencieux agent + save remote
- Rapport : [`P10_daemon_multi_device_screen.md`](./rapports_bruts/P10_daemon_multi_device_screen.md)

#### G-142 — `F-P12-007` (P12)
- Titre : Sévérité : LOW
- Type : dette
- Preuve : `jarvis/cognitive/router.py:293-324` — après `route()` déterministe, si `use_llm_fallback` et label `CURSOR`, force `execution_type="cursor"` sans rejouer le check `CURSOR_DELEGATION_ENABLED` / `_cli_info` (présent en sync `196-207`). Aucun caller prod de `route_async` (grep).
- Impact : surface morte aujourd’hui ; si branchée sans garde, fausse promesse Cursor.
- Rapport : [`P12_cognitif_cursor_devagent.md`](./rapports_bruts/P12_cognitif_cursor_devagent.md)

#### G-143 — `F-P12-008` (P12)
- Titre : Sévérité : LOW
- Type : doc-drift
- Preuve : allowlist `jarvis/cognitive/ollama_guard.py:18-23` inclut `app/fitness/meal_analysis.py` ; message d’erreur `145-147` « hors Screen Watcher / ollama_control » ; capability `screen_watcher.vision` dit « seul usage Ollama autorisé » `capability_registry.py:124-129`.
- Impact : opérateur / auditeur croit à 2 consommateurs alors qu’il y en a 3 ; faux positifs de diagnostic.
- Rapport : [`P12_cognitif_cursor_devagent.md`](./rapports_bruts/P12_cognitif_cursor_devagent.md)

#### G-144 — `F-P12-012` (P12)
- Titre : Sévérité : LOW
- Type : dette
- Preuve : écrit `cursor_delegation.py:222` ; grep runner — seule occurrence ; commits laissés au CLI dans le worktree. Garde réelle = `PROTECTED_BRANCHES` `411-421`.
- Impact : `CURSOR_ALLOW_COMMIT=false` n’a aucun effet.
- Rapport : [`P12_cognitif_cursor_devagent.md`](./rapports_bruts/P12_cognitif_cursor_devagent.md)

#### G-145 — `P13-F-06` (P13)
- Titre : Dead UI legacy
- Rapport : [`P13_module_fitness.md`](./rapports_bruts/P13_module_fitness.md)

#### G-146 — `P13-F-07` (P13)
- Titre : Stockage photo avalé
- Rapport : [`P13_module_fitness.md`](./rapports_bruts/P13_module_fitness.md)

#### G-147 — `P13-F-08` (P13)
- Titre : `ProgramExercise.duration_sec`
- Rapport : [`P13_module_fitness.md`](./rapports_bruts/P13_module_fitness.md)

#### G-148 — `P13-F-09` (P13)
- Titre : Timezone hardcodée
- Rapport : [`P13_module_fitness.md`](./rapports_bruts/P13_module_fitness.md)

#### G-149 — `P13-F-10` (P13)
- Titre : Clear notes/description desktop
- Rapport : [`P13_module_fitness.md`](./rapports_bruts/P13_module_fitness.md)

#### G-150 — `P14-F07` (P14)
- Titre : A11y LockGate incomplète
- Rapport : [`P14_frontend_bureau_jarvis_auth.md`](./rapports_bruts/P14_frontend_bureau_jarvis_auth.md)

#### G-151 — `P14-F08` (P14)
- Titre : Surfaces ops affichent `agent`
- Rapport : [`P14_frontend_bureau_jarvis_auth.md`](./rapports_bruts/P14_frontend_bureau_jarvis_auth.md)

### INFO

#### G-152 — `F-P01-015` (P01)
- Titre : ** `pipeline.py` — contrat public sain
- Preuve : ** Dataclass frozen, configure atomique, `PipelineNotConfiguredError`, pas d’import `api/`/`main`.
- Impact : ** Positif — casse la dépendance circulaire daemons ↔ FastAPI.
- Rapport : [`P01_bootstrap_config_assemblage.md`](./rapports_bruts/P01_bootstrap_config_assemblage.md)

#### G-153 — `F-P01-016` (P01)
- Titre : ** Bind/TLS — défauts sûrs respectés
- Preuve : ** `WEB_HOST=127.0.0.1`, `WEB_ALLOW_NETWORK_BIND=false`, `WEB_HTTPS=false`, exit si HTTPS sans certs, `validate_network_bind` des deux entrypoints.
- Impact : ** Pas de défaut dangereux réseau/TLS dans ce périmètre.
- Rapport : [`P01_bootstrap_config_assemblage.md`](./rapports_bruts/P01_bootstrap_config_assemblage.md)

#### G-154 — `F-P01-017` (P01)
- Titre : ** `websocket_registry` — pattern snapshot correct
- Preuve : ** Lock court → tuple recipients → I/O hors lock → purge dead. Handler `@event_bus.on` à l’import.
- Impact : ** OK pour broadcast ; `except Exception` large mais acceptable pour sockets mortes.
- Rapport : [`P01_bootstrap_config_assemblage.md`](./rapports_bruts/P01_bootstrap_config_assemblage.md)

#### G-155 — `F10` (P02)
- Titre : Sessions / horloges
- Rapport : [`P02_auth_securite_http.md`](./rapports_bruts/P02_auth_securite_http.md)

#### G-156 — `F9` (P02)
- Titre : Local recovery (A07)
- Rapport : [`P02_auth_securite_http.md`](./rapports_bruts/P02_auth_securite_http.md)

#### G-157 — `F-P03-007` (P03)
- Titre : Segment SPA `"mobile"` dans allowlist bureau
- Rapport : [`P03_api_rest_routeurs.md`](./rapports_bruts/P03_api_rest_routeurs.md)

#### G-158 — `F-P05-004` (P05)
- Titre : Sévérité : HIGH
- Type : bug | contrat-cassé
- Preuve : `agents/orchestrator.py:745-749`
- Impact : Sous-déclaration des coûts LLM pour le chat streamé (Info et agents sans `handle_stream` dédié).
- Rapport : [`P05_agents_llm_prompts.md`](./rapports_bruts/P05_agents_llm_prompts.md)

#### G-159 — `F-P05-011` (P05)
- Titre : Sévérité : MEDIUM
- Type : smell | perf
- Preuve : `agents/orchestrator.py:709-719` (`max_tok=4096`, pas `classify_task_type`)
- Impact : Productions longues via stream Info/autres ≠ plafond `HEAVY_TASK_MAX_TOKENS` ; school contourne via son `handle_stream` → inconsistance.
- Rapport : [`P05_agents_llm_prompts.md`](./rapports_bruts/P05_agents_llm_prompts.md)

#### G-160 — `F-P08-07` (P08)
- Titre : F-P08-07
- Rapport : [`P08_integrations_os_cloud.md`](./rapports_bruts/P08_integrations_os_cloud.md)

#### G-161 — `P10-C09` (P10)
- Titre : Docstring daemon obsolète (Ollama triage)
- Rapport : [`P10_daemon_multi_device_screen.md`](./rapports_bruts/P10_daemon_multi_device_screen.md)

#### G-162 — `P10-C10` (P10)
- Titre : Wake word stub (délégation P09)
- Rapport : [`P10_daemon_multi_device_screen.md`](./rapports_bruts/P10_daemon_multi_device_screen.md)

#### G-163 — `P10-C11` (P10)
- Titre : `requirements — agent.txt` cohérent
- Rapport : [`P10_daemon_multi_device_screen.md`](./rapports_bruts/P10_daemon_multi_device_screen.md)

#### G-164 — `P10-C12` (P10)
- Titre : launchd : chemins non quotés
- Rapport : [`P10_daemon_multi_device_screen.md`](./rapports_bruts/P10_daemon_multi_device_screen.md)

#### G-165 — `F-P12-010` (P12)
- Titre : Sévérité : INFO
- Type : dead-code / smell
- Preuve : `allow_merge` persisté `cursor_delegation.py:225` — aucun `gh pr merge` dans le module ; `ExecutionType` inclut `"workflow"` `models.py:10` jamais émis par `route()`.
- Impact : configuration trompeuse (`auto_merge_low_risk` aspirational).
- Rapport : [`P12_cognitif_cursor_devagent.md`](./rapports_bruts/P12_cognitif_cursor_devagent.md)

#### G-166 — `P14-F09` (P14)
- Titre : Worker MapLibre CSP non branché
- Rapport : [`P14_frontend_bureau_jarvis_auth.md`](./rapports_bruts/P14_frontend_bureau_jarvis_auth.md)

## Backlog pour Claude Opus

1. Traiter chaque `rapports_bruts/Pxx_*.md` individuellement (P01 → P14).
2. Relancer les audits manquants P15–P18.
3. Produire le `CONSTAT GÉNÉRAL` (Prompt 0) après dédoublonnage croisé.
4. Ne pas merger de correctifs sans revue humaine sur CRITICAL/HIGH.

## Annexes

- Prompts : [`PROMPTS_AUDIT_LIGNE_PAR_LIGNE.md`](./PROMPTS_AUDIT_LIGNE_PAR_LIGNE.md)
- Manifest : [`rapports_bruts/manifest.json`](./rapports_bruts/manifest.json)
- Index machine : [`rapports_bruts/findings_index.json`](./rapports_bruts/findings_index.json)
- Doublons / hors-série : [`rapports_bruts/extras/`](./rapports_bruts/extras/)

---

# ANNEXE — Rapports bruts concaténés

Contenu intégral extrait des conversations Cursor, prêt pour coller dans le Prompt 0 / traitement Opus.

<<<RAPPORTS>>>

<<<RAPPORT P01 file=P01_bootstrap_config_assemblage.md>>>

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

<<<FIN_RAPPORT P01>>>

<<<RAPPORT P02 file=P02_auth_securite_http.md>>>

# Audit P02 — Auth et sécurité HTTP

**Mode** : lecture seule · **ID** : P02 · **Fichiers lus** : ~3270 lignes  
**Verdict** : pas de bypass auth navigateur évident hors allowlists documentées ; **faiblesses confirmées** sur rate-limit pairing mobile, PIN 4 vs doc 6, CSRF sans Origin, bypass supervisor loopback, et CSP `ws:`/`wss:` trop larges.

---

## Points d’attention connus

| Point | Statut | Preuve |
|--------|--------|--------|
| Asymétrie rate-limit pairing desktop vs mobile | **Confirmé** | Desktop : `consume_device_pairing_code(..., max_attempts=DEVICE_PAIRING_MAX_ATTEMPTS)` → 429. Mobile : `consume_mobile_pairing_code` booléen seulement, **aucun** compteur / lockout dans `api/router_auth.py` ni `database/mobile.py`. |
| PIN min 4 vs doc 6 | **Confirmé** | `auth._MIN_PIN_DIGITS = 4` ; `CLAUDE.md` dit « PIN 6 chiffres ». Tests mobile verrouillent même `MIN_PIN = 4`. |
| CSRF si Origin/Referer absents | **Confirmé (par design)** | `_csrf_origin_allowed` → `True` si source vide ; jeton `X-CSRF-Token` reste obligatoire. Test `test_post_without_origin_header_allowed`. |
| Bypass supervisor `X-Jarvis-Supervisor` | **Confirmé** | Loopback + header `"1"` → saute le session gate pour `/api/control/*` (start/stop services, logs). Header **non secret**. |
| Local unlock recovery | **Confirmé, borné** | Loopback IP + Host local + `X-Jarvis-Local-Recovery: 1` + secret ; ignore plafond global ; `clear_all_rate_limits()` au succès. |

---

## Findings

### F1 — HIGH — Pairing mobile sans rate-limit (A04)
**Où** : `api/router_auth.py:266-278`, `database/mobile.py:19-29`  
**Quoi** : `/api/mobile/pairing/complete` est hors session gate et accepte un code 6 chiffres (10⁶) sans limite d’essais par IP. Desktop a `DEVICE_PAIRING_MAX_ATTEMPTS=5` + lockout 15 min.  
**Impact** : bruteforce du code pendant sa fenêtre TTL (10 min) depuis le Tailnet / réseau exposé. Usage unique atténue après succès, pas les échecs.  
**Contraste** : `api/router_devices.py:102-114`.

### F2 — MEDIUM — PIN minimum 4 chiffres (A02 / doc drift)
**Où** : `auth.py:77-90` vs `CLAUDE.md` (~l.1516)  
**Quoi** : espace 10⁴ vs 10⁶ documenté. Mitigé par scrypt + lockout (5 essais / 15 min) + délai progressif, mais politique réelle ≠ doc.  
**Note** : passphrase ≥ 10 OK.

### F3 — MEDIUM — Bypass `/api/control/*` local (A01)
**Où** : `api/middleware.py:128-135`  
**Quoi** : tout processus local pouvant joindre le backend (compromission locale, autre service loopback) contrôle audio/daemon/scheduler via header statique. Pas de jeton partagé.  
**Acceptable** si modèle de menace = machine mono-user de confiance ; sinon secret partagé ou socket Unix requis.

### F4 — MEDIUM — CSP `connect-src` autorise tout WebSocket (A05)
**Où** : `security_headers.py:20` — `connect-src 'self' ws: wss: …`  
**Quoi** : en cas d’XSS (aidé par `script-src 'unsafe-inline'`), exfiltration via WS arbitraire. Restreindre à l’origine self.

### F5 — LOW/MEDIUM — CSRF Origin optionnel (A01)
**Où** : `api/middleware.py:99-105`  
**Quoi** : sans Origin/Referer, seul le jeton synchronisé compte. Correct pour clients natifs ; réduit la défense en profondeur navigateur. Cookie `SameSite=strict` reste un filet.

### F6 — LOW — Clé VAPID privée en clair dans `app_settings` (A02)
**Où** : `push.py:48-60` — PEM `NoEncryption` via `set_setting`.  
**Impact** : fuite DB → usurpation d’émetteur push (pas lecture des payloads chiffrés end-to-end).

### F7 — LOW — `send_web_push` sans allowlist d’endpoint (A10)
**Où** : `push.py:141-156` — `httpx.post(endpoint, …)`  
**Quoi** : SSRF si abonnement malveillant enregistré (route subscribe hors P02, session requise). Timeout 10 s présent. Pas de validation schéma/host FCM/Mozilla/Apple dans ce module.

### F8 — LOW — Routes mobile bypass sans auth middleware (A01 surface)
**Où** : `api/middleware.py:145-154` — chat, voice/turn, conversations, pairing/complete…  
**Quoi** : le gate ne vérifie pas le Bearer ; la sécurité dépend des routeurs hors P02. Oubli d’auth sur une de ces routes = ouverture totale.  
**Frontière** : P15/P16 + routeurs mobile.

### F9 — INFO — Local recovery (A07)
**Où** : `api/router_auth.py:161-189`, `_is_loopback`  
**Quoi** : bien borné (IP + Host + header + secret). Host spoofing seul insuffisant (IP doit être loopback). Header non secret — OK dans ce modèle. Efface tous les rate-limits après succès (intentionnel).

### F10 — INFO — Sessions / horloges
**Où** : `auth.py:501-562` — `datetime.now()` naïf pour sessions vs UTC pour rate-limits.  
**Frontière P06** : IP stockée en clair dans `sessions.ip`.

---

## Contrats vérifiés (OWASP)

| ID | Contrôle | Résultat | Détail |
|----|----------|----------|--------|
| **A01** | Contrôle d’accès / allowlists | **✗ partiel** | ✓ `_PUBLIC_AUTH_ROUTES` exactes (frozenset méthode+path) ; ✓ fail-closed 428 ; ✓ mobile Bearer whitelist GET/mutations ; ✗ bypass supervisor loopback ; ✗ surface bypass mobile déléguée ; ✗ CSRF Origin omis accepté |
| **A02** | Secrets / hashing | **✗ partiel** | ✓ scrypt N=2¹⁴ + salt 16 B + `hmac.compare_digest` ; ✓ tokens session/mobile/pairing hash SHA-256 ; ✓ CSRF dérivé non réversible ; ✗ PIN min 4 ; ✗ VAPID PEM non chiffrée |
| **A03** | Injection | **✓** | Requêtes auth rate-limit paramétrées ; pas de SQL concaténé dans le périmètre ; uploads : namespace/`..` rejetés, `resolve` + `relative_to` |
| **A04** | Rate-limit | **✗** | ✓ unlock/verify/change-secret + recovery (sans global) ; ✓ progressive + hard + global ; ✗ **pairing mobile sans limite** (asymétrie vs desktop) ; setup public sans rate-limit (course 1er install) |
| **A05** | Misconfig | **✗ partiel** | ✓ nosniff, frame DENY, referrer no-referrer, HSTS si HTTPS ; ✓ `network_security.validate_network_bind` ; ✗ CSP `unsafe-inline` + `ws:`/`wss:` larges ; messages d’erreur génériques OK |
| **A07** | Sessions | **✓** | ✓ opaque `token_urlsafe(32)`, hash seul en DB ; ✓ TTL inactivité + max age ; ✓ révocation unitaire / globale (change-secret) ; ✓ cookie HttpOnly + SameSite=strict + Secure si HTTPS ; ✓ status `Cache-Control: no-store` |
| **A08** | Intégrité jetons | **✓** | ✓ compare_digest CSRF ; ✓ pairing codes hashés `pair:{code}` / mobile token 48 ; ✓ consume atomique `used_at` |
| **A09** | Journalisation auth | **✓** | ✓ `record_failed_attempt` → `log_llm_action` fingerprint tronqué + channel ; ✓ notif high sur hard lock ; pas de secret/IP brute dans l’audit |
| **A10** | Redirects / SSRF | **✗ partiel** | ✓ pas de redirect user-controlled dans le périmètre ; ✓ path traversal frontend bloqué ; ✗ push POST vers `endpoint` abonné sans allowlist |

---

## Contrôles solides (pas de finding)

- **Fail-closed** tant que secret non configuré (`428 setup_required`).
- **scrypt** + sel aléatoire ; secret jamais en clair.
- **CSRF synchronizer** obligatoire sur mutations cookie (même same-origin sans token → 403).
- **Origin exact** schéma+hôte+port quand présent ; `CSRF_ALLOWED_ORIGINS` opt-in.
- **Fichiers sensibles** : `core/file_security` 0700/0600, `O_NOFOLLOW`, anti-symlink ; uploads UUID, MIME/signature, quota, path confinement.
- **PII / logs** : `log_privacy` + `security/redaction` + `DataBoundary` ; mapping PII détruit après usage.
- **Document privacy** : strict local par défaut ; cloud seulement avec consentement (anonymisation via `JARVISRouter`, hors P02 mais cohérent avec la politique déclarée).
- **Local unlock** : double contrainte réseau (peer + Host) + secret.

---

## Frontières (hors audit ligne à ligne)

| Cible | Pourquoi |
|-------|----------|
| **P06** `database/sessions.py`, `database/mobile.py` | CRUD sessions, rate_limits, pairing consume — tracé depuis P02 uniquement |
| **P06** `database/screen_daemon.consume_device_pairing_code` | Rate-limit desktop de référence pour F1 |
| **Hors P** `api/router_devices.py`, `api/router_daemon.py` (`/api/control`) | Pairing desktop + impact supervisor |
| **P14/P15/P16** | UI auth React / `web_mobile` / Android |
| Routeurs `/api/mobile/chat|voice|…` | Auth Bearer attendue **dans** la route, pas au middleware |
| `jarvis/router.py` | Anonymisation réelle avant DeepSeek (document cloud) |
| `api/router_misc` push subscribe | Validation endpoint avant persistance (SSRF F7) |

---

## Matrice fichiers (couverture)

| Fichier | Focus audit |
|---------|-------------|
| `auth.py` | scrypt, PIN4, rate-limit, sessions, CSRF crypto |
| `api/middleware.py` | allowlists, CSRF, supervisor, mobile bearer |
| `api/router_auth.py` | unlock, local-unlock, pairing mobile, cookies |
| `security_headers.py` | CSP / headers |
| `push.py` | VAPID, aes128gcm, POST endpoint |
| `core/file_security.py` | modes 0600/0700 |
| `core/network_security.py` | bind HTTPS |
| `core/frontend_*` | path traversal static |
| `jarvis/uploads.py` | confinement upload |
| `jarvis/log_privacy.py`, `security/redaction.py` | fuite logs |
| `jarvis/pii/*`, `document_privacy.py` | frontière cloud/PII |

---

## Synthèse actionnable (hors scope de ce tour — audit only)

1. Aligner pairing mobile sur le modèle desktop (`max_attempts` / lockout / `Retry-After`).  
2. Aligner `_MIN_PIN_DIGITS` à 6 **ou** corriger la doc/CLAUDE.  
3. Remplacer `X-Jarvis-Supervisor: 1` par un secret partagé fichier 0600 (ou restreindre autrement).  
4. Resserrer CSP `connect-src` (pas `ws:`/`wss:` globaux).  
5. Allowlist des hosts push dans `send_web_push`.

Aucun correctif appliqué (mission lecture seule).

<<<FIN_RAPPORT P02>>>

<<<RAPPORT P03 file=P03_api_rest_routeurs.md>>>

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

<<<FIN_RAPPORT P03>>>

<<<RAPPORT P04 file=P04_websocket_chat_voix_actions.md>>>

# AUDIT — P04 — WebSocket, chat, voix, actions

## Métadonnées
- Agent / modèle : Auto (Composer) — auditeur systèmes temps réel
- Date : 2026-07-31
- Commit audité (`git rev-parse HEAD`) : `2191bf368a7e9a6f07d670a9e3464bd223c1d059`
- Branche : `elias/fitness-meal-ai-photo-8e4f`
- Fichiers dans le périmètre (count) : 13
- Fichiers lus (count) : 13
- Couverture estimée : 100%

## Synthèse exécutive
Le fail-closed WS (4428/4401) est en place, et le chat texte (WS + mobile REST) converge bien vers `_process_message` / `_process_message_internal` avec contexte enrichi. En revanche, le contrat « un seul pipeline texte/voix » est cassé : mains libres et Android vocal passent par `_process_voice_fast` (Flash direct, sans enrichissement). La surface `action_confirm` + confirmation textuelle est dangereuse (payload client arbitraire, négations acceptées, cancel mobile non révocatoire). Le streaming ignore `raw_response` et perd les blocs `action` School. Terminal a un noyau shell_safety solide, mais stdout/clipboard partent bruts vers le LLM de follow-up.

## Findings

### F-P04-001
- Sévérité : HIGH
- Type : sécurité
- Titre : WebSocket cookie sans validation d’Origin (CSWSH same-site)
- Preuve : `api/ws_handler.py:41-49`
```python
if not auth.is_configured():
    await ws.close(code=4428)
    return
session, mobile_device = resolve_websocket_auth(ws)
if not session and not mobile_device:
    await ws.close(code=4401)
    return
await ws.accept()
```
- Impact : une page same-site (autre port localhost) peut ouvrir `/ws` avec le cookie, lire le chat et déclencher des actions.
- Repro / condition : session active + `new WebSocket("ws://127.0.0.1:<port>/ws")` depuis une autre origine same-site.
- Correctif proposé (sans coder) : exiger Origin exact pour l’auth cookie avant `accept()` ; Bearer mobile sans Origin.
- Confiance : haute

### F-P04-002
- Sévérité : HIGH
- Type : sécurité
- Titre : `action_confirm` exécute un payload client arbitraire
- Preuve : `api/ws_handler.py:323-336`
```python
if msg_type == "action_confirm":
    act = msg.get("action")
    ...
    act = {**act, "confirmed": True}
    res = await execute_action(act)
```
- Impact : calendrier, tâches, TV, open_app, clipboard… sans proposition serveur. Terminal reste protégé par `shell_plan_id` opaque, pas les autres types.
- Repro / condition : WS authentifié → `{"type":"action_confirm","action":{"type":"calendar_create",...}}`.
- Correctif proposé (sans coder) : n’accepter qu’un id opaque de proposition serveur, consommé atomiquement, lié session/conversation.
- Confiance : haute

### F-P04-003
- Sévérité : HIGH
- Type : sécurité
- Titre : Confirmation textuelle accepte une négation préfixée
- Preuve : `api/chat_actions.py:230-241`
```python
confirmation_patterns = (..., "lance", "exécute", "execute", ...)
is_confirmation = (
    text_lower in confirmation_patterns
    or any(text_lower.startswith(p) for p in confirmation_patterns if len(p) > 3)
)
```
- Impact : « lance pas » / « exécute pas » déclenche l’action pending (dont plan shell).
- Repro / condition : proposition terminal en attente, puis message `lance pas`.
- Correctif proposé (sans coder) : match exact (ou whitelist de phrases entières) + rejet si négation ; idéalement confirmation structurée avec plan_id.
- Confiance : haute

### F-P04-004
- Sévérité : HIGH
- Type : sécurité
- Titre : Refus mobile n’annule pas la proposition / le plan
- Preuve : `api/router_mobile_chat.py:136-141`
```python
confirmed = bool(body.get("confirmed", False))
if not confirmed:
    return {"ok": True, "cancelled": True, "conversation_id": conversation_id}
```
- Impact : UI « annulé » mais `_pending_proposal` et `shell_plan_id` restent vivants ; un « oui » ultérieur exécute.
- Repro / condition : `POST /api/mobile/chat/confirm` avec `confirmed:false`, puis message « oui ».
- Correctif proposé (sans coder) : appeler `_cancel_pending_proposal` + révoquer le plan shell.
- Confiance : haute

### F-P04-005
- Sévérité : HIGH
- Type : sécurité
- Titre : Follow-up injecte stdout / clipboard bruts vers le LLM cloud
- Preuve : `api/chat_actions.py:298-325` + usage `api/ws_handler.py:357-367`
```python
if action_result.get("output"):
    parts.append("Résultat :\n" + str(action_result["output"])[:3000])
...
if t == "clipboard":
    return "Contenu du presse-papier :\n" + str(action_result.get("content", ""))
```
- Impact : secrets/PII locaux et prompt injection via sorties non fiables quittent la machine vers DeepSeek ; le client reçoit aussi le brut dans `action_result`.
- Repro / condition : `clipboard` get avec token dans le presse-papiers, ou terminal confirmé produisant du texte hostile.
- Correctif proposé (sans coder) : sanitize/redact avant follow-up ; clipboard local-only ; champs allowlistés + plafond.
- Confiance : haute

### F-P04-006
- Sévérité : HIGH
- Type : sécurité
- Titre : Fallback JSON inline exécute un faux bloc action
- Preuve : `api/chat_actions.py:387-406`
```python
m2 = _ACTION_JSON_INLINE_RE.search(text)
...
if isinstance(action, dict) and "type" in action:
    return action, clean
```
- Impact : exemple / citation / injection documentaire `{"type":"task",...}` devient action réelle.
- Repro / condition : réponse LLM contenant un JSON illustratif avec clé `type` hors fence ` ```action `.
- Correctif proposé (sans coder) : supprimer le fallback inline ; fence obligatoire + schéma par type.
- Confiance : haute

### F-P04-007
- Sévérité : HIGH
- Type : contrat-cassé
- Titre : Voix mains libres / mobile hors pipeline unifié
- Preuve : `api/voice_processing.py:108-111,191-197` ; `api/mobile_voice_service.py:143-149` ; contraste `api/ws_messages.py:51-58`
```python
# Historique recent (... pas de build_full_context)
raw = get_conversation_history(conversation_id, limit=10)
result = await llm.chat(messages=messages, model=config.DEEPSEEK_FAST_MODEL, ...)
```
- Impact : pas d’enrichissement documents/mails/calendar/tâches ; actions/persistance/titrage différents du chat texte. CLAUDE.md annonce le contraire.
- Repro / condition : même question en chat texte vs `conversation_start` / `POST /api/mobile/voice/turn`.
- Correctif proposé (sans coder) : cœur commun `_process_message_internal(..., voice_mode=True)` ; spécialiser seulement STT/TTS/latence.
- Confiance : haute

### F-P04-008
- Sévérité : HIGH
- Type : bug
- Titre : Streaming ignore `done.raw_response` → actions School perdues
- Preuve : `api/ws_messages.py:226-232,261-263` (consommation) ; frontière `agents/school.py:78-97`
```python
if event.get("type") == "done":
    pending_done = event
    ...
    continue
if event.get("type") == "chunk":
    full_response += event["content"]
...
action, after_action = _extract_action_from_text(raw_accumulated)
```
- Impact : chunks = affichage sans blocs action ; l’action n’est jamais extraite ni exécutée en stream SCHOOL.
- Repro / condition : chat stream + agent school avec bloc ` ```action `.
- Correctif proposé (sans coder) : canoniser `done.raw_response` (sinon `done.content`) pour extraction/persistance.
- Confiance : haute

### F-P04-009
- Sévérité : HIGH
- Type : bug
- Titre : Idempotence mobile chat non atomique (TOCTOU)
- Preuve : `api/router_mobile_chat.py:68-76,90-119`
```python
cached = get_mobile_chat_dedup(...)
...
result = await _process_message_internal(...)
save_mobile_chat_dedup(...)
```
- Impact : double LLM / double action sur retry concurrent du même `client_message_id`.
- Repro / condition : deux `POST /api/mobile/chat` simultanés, même device + `client_message_id`.
- Correctif proposé (sans coder) : réserve atomique `pending/completed` avant traitement.
- Confiance : haute

### F-P04-010
- Sévérité : HIGH
- Type : bug
- Titre : Boucle WS bloquée → `voice_cancel` / barge-in non temps réel
- Preuve : `api/ws_handler.py:82-83,97-100,142-146`
```python
packet = await ws.receive()
...
await _process_message(ws, text, conversation_id, voice_mode=True, stream=True, send_tts=True)
is_speaking = True
```
- Impact : aucun JSON/`voice_cancel` lu pendant STT/LLM/TTS ; l’annulation arrive trop tard.
- Repro / condition : réponse TTS longue puis `voice_cancel` avant `speech_done`.
- Correctif proposé (sans coder) : tour en tâche annulable ; boucle receive continue ; verrou + turn_id.
- Confiance : haute

### F-P04-011
- Sévérité : MEDIUM
- Type : bug
- Titre : Échec confirmation pending → `UnboundLocalError`
- Preuve : `api/ws_messages.py:186-214`
```python
if pending_result.get("ok") and not pending_result.get("needs_confirmation"):
    ...
    display_text = ...
    emotion = ...
return {"emotion": emotion, "response": display_text or ...}
```
- Impact : plan expiré / `ok=false` → exception après `action_result`, erreur générique client.
- Repro / condition : confirmer un plan shell expiré par « oui ».
- Correctif proposé (sans coder) : initialiser display/emotion avant la branche ; finaliseur commun.
- Confiance : haute

### F-P04-012
- Sévérité : MEDIUM
- Type : bug
- Titre : Documents attachés cassent / annulent la confirmation « oui »
- Preuve : `api/ws_messages.py:66-67,186` + `api/chat_actions.py:243-247`
```python
content = extra_context.pop("documents_context") + "\n\n" + content
pending_result = await _check_pending_proposal(ws, content, conversation_id)
```
- Impact : « oui » n’est plus une confirmation exacte → proposition annulée silencieusement.
- Repro / condition : doc `cloud_consent` + action pending + message « oui ».
- Correctif proposé (sans coder) : confirmer sur `original_text` uniquement.
- Confiance : haute

### F-P04-013
- Sévérité : MEDIUM
- Type : bug
- Titre : `_pending_proposal` singleton global cross-conversations
- Preuve : `api/chat_actions.py:23-25,265-269`
```python
_pending_proposal: dict | None = None
_pending_proposal = {"conversation_id": conversation_id, "action": action}
```
- Impact : écrasement mutuel multi-onglets/appareils ; plans orphelins.
- Repro / condition : deux conversations créent une proposition avant confirmation.
- Correctif proposé (sans coder) : map `(session, conversation_id)` + TTL + verrou.
- Confiance : haute

### F-P04-014
- Sévérité : MEDIUM
- Type : bug
- Titre : `is_speaking=True` même si TTS non démarré
- Preuve : `api/ws_handler.py:142-146` + retours anticipés `api/ws_messages.py:186-214,478-488`
- Impact : blobs PTT ignorés indéfiniment (`if is_speaking: continue`) sans `speech_done`/`done_playing`.
- Repro / condition : erreur orchestrateur ou branche pending sans TTS.
- Correctif proposé (sans coder) : n’armer `is_speaking` que si TTS a démarré ; always-clear on error.
- Confiance : haute

### F-P04-015
- Sévérité : MEDIUM
- Type : sécurité
- Titre : Clipboard sans `action` lit le presse-papiers par défaut
- Preuve : `actions.py:493-503`
```python
if action.get("action") == "set":
    return await computer.set_clipboard(...)
text = await computer.get_clipboard()
return {"ok": True, "content": text}
```
- Impact : typo/`action` omise → lecture secrète + fuite follow-up (F-P04-005).
- Repro / condition : `{"type":"clipboard"}` ou `action:"delete"`.
- Correctif proposé (sans coder) : exiger `action in {"get","set"}` strict.
- Confiance : haute

### F-P04-016
- Sévérité : MEDIUM
- Type : sécurité
- Titre : `confirmed` truthy non-booléen exécute un plan shell
- Preuve : `actions.py:292-295`
```python
if not action.get("confirmed"):
    return _shell_confirmation_response(plan)
return await execute_shell_plan(plan_id)
```
- Impact : `"confirmed":"false"` / `1` exécute si `shell_plan_id` valide.
- Repro / condition : plan créé puis rappel avec `"confirmed":"false"`.
- Correctif proposé (sans coder) : `action.get("confirmed") is True`.
- Confiance : haute

### F-P04-017
- Sévérité : MEDIUM
- Type : perf
- Titre : Audio mobile lu entièrement avant plafond taille
- Preuve : `api/router_mobile_voice.py:24-25` ; `api/mobile_voice_service.py:47-51`
- Impact : OOM / DoS mémoire par client Bearer authentifié avant 413.
- Repro / condition : multipart très volumineux sur `/api/mobile/voice/turn`.
- Correctif proposé (sans coder) : lecture bornée + préfiltre Content-Length.
- Confiance : haute

### F-P04-018
- Sévérité : MEDIUM
- Type : contrat-cassé
- Titre : Confirmation Cursor chat inaccessible (intent ≠ cursor)
- Preuve : `api/chat_cognitive.py:60-70,109-114` ; appel conditionnel `api/chat_processing.py:91-100`
- Impact : après « dites lance », le message `lance` ne confirme pas le job (sauf voie vocale).
- Repro / condition : proposition Cursor en chat texte, puis répondre `lance`.
- Correctif proposé (sans coder) : `maybe_confirm_pending_cursor` avant routage cognitif.
- Confiance : haute

### F-P04-019
- Sévérité : LOW
- Type : sécurité
- Titre : PII opérationnelle dans les logs (titre, transcript)
- Preuve : `api/chat_context.py:64` ; `api/mobile_voice_service.py:135-141`
- Impact : sujets relationnels / paroles utilisateur dans fichiers logs hors `llm_action_logs` redactés.
- Repro / condition : auto-titre « Analyse relation Alice » ; tour vocal Android.
- Correctif proposé (sans coder) : ids + longueurs uniquement ; redacteur central.
- Confiance : haute

### F-P04-020
- Sévérité : LOW
- Type : bug
- Titre : `conversation_updated` émis avant titre async
- Preuve : `api/ws_messages.py:457-468`
- Impact : UI reçoit `title=None` ; pas de second event quand le titre existe.
- Repro / condition : première réponse d’une conv sans titre.
- Correctif proposé (sans coder) : émettre depuis `_maybe_title_conversation` après update.
- Confiance : haute

### F-P04-021
- Sévérité : INFO
- Type : doc-drift
- Titre : En-tête `actions.py` prétend n’être appelé que depuis `main.py`
- Preuve : `actions.py:1-6`
- Impact : masque les vrais appelants (`api/ws_*`, `chat_*`, `voice_*`, mobile).
- Repro / condition : lecture du docstring vs imports.
- Correctif proposé (sans coder) : documenter tous les appelants + frontières auth/confirm.
- Confiance : haute

## Contrats vérifiés
| Contrat / invariant | Statut | Preuve |
|---|---|---|
| 1. Auth WS fail-closed 4428 si non configuré | OK | `api/ws_handler.py:41-43` |
| 1. Auth WS fail-closed 4401 si ni session ni device | OK | `api/ws_handler.py:44-47` |
| 1. Protection Origin / CSWSH cookie | KO | `api/ws_handler.py:49` (accept sans Origin) |
| 2. Même pipeline texte WS / REST interne | OK | `_process_message` ↔ `_process_message_internal` + `_build_enriched_context` |
| 2. Même pipeline texte et voix | KO | voix → `_process_voice_fast` ; texte → orchestrateur enrichi |
| 2. `voice_mode` force `stream=False` (PTT via handle) | OK | `api/ws_messages.py:51-52` |
| 3. Extraction fence ` ```action ` | OK partiel | `_ACTION_RE` OK ; fallback inline KO (`chat_actions.py:387-406`) |
| 3. Terminal : plan opaque, confirm, pas `computer.run` | OK | `actions.py:273-335` |
| 3. `confirmed:true` sans plan serveur n’exécute pas | OK | `actions.py:307-335` |
| 3. `action_confirm` lié à proposition serveur | KO | `ws_handler.py:323-336` |
| 3. Sémantique confirmation textuelle sûre | KO | `chat_actions.py:230-241` |
| 4. ACTIONS_WITH_FOLLOWUP sans stdout/PII brut | KO | `chat_actions.py:298-325` |
| 5. Documents : consentement + plafond + anonymisation | OK | `chat_context.py:175-206` |
| 5. Triggers mots-clés bornés / peu de faux positifs | KO | sous-chaînes larges `chat_context.py:212-223` |
| 5. Logs opérationnels sans PII | KO | `chat_context.py:64`, `mobile_voice_service.py:135-141` |
| 6. Anti-écho / `is_processing` mains libres | OK partiel | ignore processing `ws_handler.py:97-99` ; cancel bloqué F-010 |
| 6. Race `is_speaking` PTT | KO | armé même sans TTS `ws_handler.py:146` |
| 7. Ordre persist user → assistant + activité | OK (chemin nominal) | `ws_messages.py:69-77,439-455` |
| 7. Auto-titre | OK partiel | lancé `457-458` ; event UI trop tôt F-020 |
| 7. Docs non persistés dans message user | OK | préfixe sur `content`, save `original_text` |
| 8. Mobile chat/voice : Bearer `_require_mobile_device` | OK | `router_mobile_chat.py:32,56,127` ; `router_mobile_voice.py:24` |
| 8. Cookie web seul refusé sur `/api/mobile/*` | OK | `_require_mobile_device` → Bearer only (`router_auth.py:26-30`) |
| 8. Cancel mobile révoque pending | KO | `router_mobile_chat.py:136-138` |

## Frontières / dépendances
- Signale vers P02 (Auth) : `resolve_websocket_auth` / cookie flags / CSRF HTTP — Origin WS non couvert ici en profondeur.
- Signale vers P05 (Agents) : `SchoolAgent.handle_stream` expose `raw_response` ; placeholders ne consomment pas toutes les clés de `chat_context` (`screen_context`, etc.).
- Signale vers P09 (Audio) : STT/TTS, MIME TTSKit, barge-in réel dans `api/ws_handsfree.py` (hors INCLUS mais appelé depuis `ws_handler`).
- Signale vers P01 : `pipeline.py` non relu (contrat « max 1 lecture » non nécessaire — duplication claire avec `_process_message_internal`).
- Signale vers P12 : jobs Cursor globaux / confirmation `lance`.
- Attendus consommés ailleurs : `execute_action`, `_process_message`, `_process_message_internal`, `_process_voice_fast`, `ACTIONS_WITH_FOLLOWUP`.

## Fichiers non lus
| Fichier | Motif |
|---|---|
| _(aucun du périmètre)_ | — |
| `pipeline.py` | lecture ciblée non requise (divergence voix déjà prouvée dans P04) |
| `api/ws_session.py`, `api/ws_handsfree.py` | exclus ; inspectés en frontière uniquement |

## Couverture
- Liste exhaustive des fichiers lus (chemins relatifs), triée :
  - `actions.py`
  - `api/chat_actions.py`
  - `api/chat_cognitive.py`
  - `api/chat_context.py`
  - `api/chat_processing.py`
  - `api/mobile_voice_service.py`
  - `api/router_mobile_chat.py`
  - `api/router_mobile_voice.py`
  - `api/voice_cognitive.py`
  - `api/voice_processing.py`
  - `api/voice_support.py`
  - `api/ws_handler.py`
  - `api/ws_messages.py`

<<<FIN_RAPPORT P04>>>

<<<RAPPORT P05 file=P05_agents_llm_prompts.md>>>

# AUDIT — P05 — Agents, LLM, prompts

## Métadonnées
- Agent / modèle : Cloud Agent (Composer) — lecture seule
- Date : 2026-07-31
- Commit audité (`git rev-parse HEAD`) : `2191bf36`
- Branche : `elias/fitness-meal-ai-photo-8e4f`
- Fichiers dans le périmètre (count) : 61
- Fichiers lus (count) : 61
- Couverture estimée : 88% (agents/llm/jarvis backends + prompts user-facing lus ligne à ligne ; `prompts/cursor/*.md` scannés exhaustivement pour secrets / Claude / contrats, pas rejoués comme code exécutable)

## Synthèse exécutive
Persona unique et flags `inject_persona` orchestrator/memory sont corrects ; `persona.txt` interdit emoji et « agent X ». En revanche l’« escalade Opus » coach est morte (`coach_deep == DEEPSEEK_MAIN_MODEL`), le tag `[DEEP_ANALYSIS]` peut fuir vers l’UI, et `prompts/devops.txt` se présente comme « agent DEVOPS ». `_route_task` / heavy / voice sont réels pour school+productivity, mais le streaming générique force `cost=0` et ignore le heavy routing. L’historique conversationnel est collé dans le system prompt (injection + double coût). L’horodatage ignore `config.TIMEZONE`. Le package `jarvis/router` documente encore « chat LOCAL » alors que `chat()` envoie à DeepSeek avec un system prompt qui affirme tourner en local. Coûts OK sur `_call_claude` ; secrets absents des prompts. Verdict : **GO_AVEC_RESERVES** — pas de fuite de clés, mais plusieurs contrats persona/coûts/privacy cassés.

## Findings
### F-P05-001
- Sévérité : HIGH
- Type : contrat-cassé | dead-code
- Titre : Escalade coach « Opus / deep » est un no-op (même modèle)
- Preuve : `agents/coach.py:162-167` + `config.py:580`
```python
model = (
    config.AGENT_MODELS.get("coach_deep", config.DEEPSEEK_MAIN_MODEL)
    if escalate else config.DEEPSEEK_MAIN_MODEL
)
# config: "coach_deep": DEEPSEEK_MAIN_MODEL
```
- Impact : Pré-check flash à chaque tour coach non-vocal sans gain de qualité ; doc CLAUDE.md « Opus » mensongère.
- Repro / condition : Message coach structurant → `_should_escalate` True → même `DEEPSEEK_MAIN_MODEL`.
- Correctif proposé (sans coder) : Soit retirer escalade + tag prompt, soit mapper `coach_deep` vers un modèle réellement plus capable / budget tokens distinct ; aligner docs.
- Confiance : haute

### F-P05-002
- Sévérité : HIGH
- Type : bug | contrat-cassé
- Titre : Tag `[DEEP_ANALYSIS]` non stripé → fuite UI
- Preuve : `prompts/coach.txt:73-74` + `agents/display_text.py:41-43`
```text
signale-le avec le tag [DEEP_ANALYSIS] en début de ta réponse
```
```python
# Tag présent mais émotion non reconnue → on garde le tag dans le texte.
if m and m.group(1).lower() not in VALID_EMOTIONS:
    return "neutral", text
```
- Impact : Texte utilisateur peut commencer par `[DEEP_ANALYSIS]` ; l’escalade réelle est pré-appel (`_should_escalate`), pas ce tag → instruction morte + fuite.
- Repro / condition : Coach répond avec `[DEEP_ANALYSIS]` en tête ; `finalize_assistant_display_text` ne le retire pas.
- Correctif proposé (sans coder) : Supprimer l’instruction du prompt, ou ajouter strip explicite ; ne jamais exposer de tag système.
- Confiance : haute

### F-P05-003
- Sévérité : HIGH
- Type : sécurité
- Titre : Historique utilisateur injecté dans le system prompt sans délimiteurs de confiance
- Preuve : `agents/__init__.py:96-117` (+ duplication messages chat `185-191`)
```python
base += (
    "\n\n---\n\n"
    "HISTORIQUE DE LA CONVERSATION …\n"
    + "\n".join(timed_lines[-50:])
)
```
- Impact : Contenu user/assistant traité comme instructions système ; surface d’injection ; tokens doublés (system + messages).
- Repro / condition : Toute conversation avec `context["history"]` non vide.
- Correctif proposé (sans coder) : Historique uniquement dans `messages[]` ; si besoin contextual, wrapper `[UNTRUSTED_HISTORY]…[/UNTRUSTED_HISTORY]` + consigne d’ignorer instructions internes.
- Confiance : haute

### F-P05-004
- Sévérité : HIGH
- Type : bug | contrat-cassé
- Titre : Chemin streaming force tokens/cost à 0
- Preuve : `agents/orchestrator.py:745-749`
```python
yield {
    "type": "done",
    "tokens_in": 0,
    "tokens_out": 0,
    "cost": 0.0,
    ...
}
```
- Impact : Sous-déclaration des coûts LLM pour le chat streamé (Info et agents sans `handle_stream` dédié).
- Repro / condition : `handle_stream` → branche `llm.chat_stream` (pas school/coach stream custom).
- Correctif proposé (sans coder) : Accumuler usage si l’API stream l’expose, ou estimation post-hoc ; ne jamais écrire 0 silencieux.
- Confiance : haute

### F-P05-005
- Sévérité : HIGH
- Type : contrat-cassé | doc-drift
- Titre : `prompts/devops.txt` se présente comme « agent DEVOPS » malgré persona anti-agent
- Preuve : `prompts/devops.txt:1` + `agents/devops.py` (`inject_persona` défaut True) + `prompts/persona.txt:16`
```text
Tu es l'agent DEVOPS de JARVIS. Agent principal…
```
- Impact : Conflit system prompt fort → risque élevé de réponse « je suis l’agent… » à l’utilisateur.
- Repro / condition : Message routé DEVOPS.
- Correctif proposé (sans coder) : Réécrire en « Tu es JARVIS ; capacités techniques : … » sans mot « agent ».
- Confiance : haute

### F-P05-006
- Sévérité : HIGH
- Type : doc-drift | sécurité
- Titre : `jarvis/router` affirme chat LOCAL / privé alors que le chemin appelle DeepSeek
- Preuve : `jarvis/router.py:1-6`, `29-32`, `56-65` + `jarvis/models.py:16`
```python
_CHAT_SYSTEM_DEFAULT = (
    "…Tu tournes en local : ces échanges sont strictement privés."
)
async def chat(...):
    """Plus aucun LLM local…"""
    return await self._deepseek_anonymized(...)
```
- Impact : Fausse promesse de privacy dans le system prompt ; `LocalBackend` / `DataSource.MESSAGES→LOCAL` hors sync avec la politique 2026.
- Repro / condition : Appel `JARVISRouter.chat()` / message_intelligence.
- Correctif proposé (sans coder) : Aligner docs + `_CHAT_SYSTEM_DEFAULT` + `models.DataSource` sur DeepSeek+PII ; retirer claim « local ».
- Confiance : haute

### F-P05-007
- Sévérité : MEDIUM
- Type : bug
- Titre : Horodatage ignore `config.TIMEZONE` (Europe/Paris hardcodé)
- Preuve : `agents/__init__.py:21-33` ; aussi `agents/briefing_engine.py:263,407` ; `agents/coach.py:142`
```python
now = datetime.now()
# … "— Europe/Paris"
```
- Impact : Mauvaise « now » si TZ hôte ≠ Paris ou `TIMEZONE` env différent ; briefings/datation incorrects.
- Repro / condition : `TIMEZONE!=Europe/Paris` ou machine UTC.
- Correctif proposé (sans coder) : `ZoneInfo(config.TIMEZONE)` partout ; libeller avec la vraie TZ.
- Confiance : haute

### F-P05-008
- Sévérité : MEDIUM
- Type : bug | robustesse
- Titre : Parsers ```save``` / ```json``` newline-strict (school/journal/memory)
- Preuve : `agents/school.py:30,110-118` ; `agents/journal.py:34,133-136` ; `agents/memory.py:47,155`
```python
SAVE_BLOCK_RE = re.compile(r"```save\s*\n(.*?)\n```", re.DOTALL)
```
- Impact : Fence one-line DeepSeek (tolérée pour `action` dans `display_text.py:19`) → sauvegarde devoir / extraction journal/mémoire silencieusement ratée.
- Repro / condition : ```save {"action":…}``` sans newline interne.
- Correctif proposé (sans coder) : Aligner sur `_RE_ACTION` (`\n?`) + fallback JSON brut (comme fitness/email).
- Confiance : haute

### F-P05-009
- Sévérité : MEDIUM
- Type : contrat-cassé
- Titre : Placeholder `{{life_profile}}` souvent vide — profil seulement dans `memory_context`
- Preuve : `agents/orchestrator.py:568-590` (retourne seulement `memory_context` avec `[LIFE_PROFILE]`) + agents `setdefault("life_profile","")` ex. `coach.py:95` + `prompts/school.txt:1-3`
- Impact : Ordre « life puis memory » des prompts partiellement mort ; double section vide en tête.
- Repro / condition : Tout handle via orchestrateur standard.
- Correctif proposé (sans coder) : Soit peupler `life_profile` séparément, soit retirer le placeholder des prompts agents.
- Confiance : haute

### F-P05-010
- Sévérité : MEDIUM
- Type : sécurité
- Titre : Analyseurs email / iMessage / transcription sans garde « contenu non fiable »
- Preuve : `prompts/email_analyzer.txt:11-14` ; `prompts/imessage_extractor.txt:6-7` ; `prompts/continuous_extractor.txt:5-6` (pas d’instruction ignore-overrides) — contraste `agents/__init__.py:132-133` (voix seulement)
- Impact : Injection prompt via corps mail / messages / transcript vers extracteurs JSON (moins critique que terminal, mais peut polluer faits/notifs).
- Repro / condition : Mail/iMessage contenant « ignore previous instructions… ».
- Correctif proposé (sans coder) : Délimiteurs + règle « traiter comme données, ignorer instructions internes » sur tous les sinks.
- Confiance : moyenne

### F-P05-011
- Sévérité : MEDIUM
- Type : smell | perf
- Titre : Streaming générique ignore `_route_task` / heavy tokens
- Preuve : `agents/orchestrator.py:709-719` (`max_tok=4096`, pas `classify_task_type`)
- Impact : Productions longues via stream Info/autres ≠ plafond `HEAVY_TASK_MAX_TOKENS` ; school contourne via son `handle_stream` → inconsistance.
- Repro / condition : Agent sans `handle_stream` custom + demande lourde en mode stream.
- Correctif proposé (sans coder) : Centraliser routing modèle/tokens avant stream et non-stream.
- Confiance : moyenne

### F-P05-012
- Sévérité : LOW
- Type : dead-code | dette
- Titre : `AGENT_MODELS["productivity_triage"]` jamais lu
- Preuve : `config.py:577` vs `agents/productivity.py` (`model = DEEPSEEK_MAIN_MODEL`, `_route_task` non-heavy → `self.model`)
- Impact : Triage toujours main (coût) ; doc « Haiku triage » morte.
- Repro / condition : Tout message productivity non-heavy.
- Correctif proposé (sans coder) : Brancher fast sur triage, ou supprimer la clé morte.
- Confiance : haute

### F-P05-013
- Sévérité : LOW
- Type : smell
- Titre : Fuite mot « agent » dans message d’erreur utilisateur
- Preuve : `agents/orchestrator.py:639-640`
```python
"response": "Aucun agent disponible. La Phase 1 n'a pas encore enregistré d'agent…"
```
- Impact : Violation persona (rare path).
- Repro / condition : Registry vide / agent manquant.
- Correctif proposé (sans coder) : Message neutre (« JARVIS indisponible… »).
- Confiance : haute

### F-P05-014
- Sévérité : LOW
- Type : smell
- Titre : Contexte météo peut injecter des emoji dans le system prompt
- Preuve : `agents/productivity.py:57-64` (`w.get('icon','')`)
- Impact : Contredit interdiction emoji ; peut biaiser la réponse.
- Repro / condition : Weather API retourne une icône emoji.
- Correctif proposé (sans coder) : Strip / mapper icon → texte.
- Confiance : moyenne

### F-P05-015
- Sévérité : LOW
- Type : doc-drift
- Titre : `use_cache` / cache_control Anthropic morts ; docs CLAUDE encore Anthropic-style
- Preuve : `llm.py:75-80` (« use_cache … ignoré ») ; cache hit lu `147-148` seulement
- Impact : Appelants croient contrôler le cache ; pas de faille runtime.
- Repro / condition : N/A (comportement documenté dans code, pas dans CLAUDE).
- Correctif proposé (sans coder) : Mettre à jour CLAUDE.md ; déprécier clairement `use_cache`.
- Confiance : haute

### F-P05-016
- Sévérité : INFO
- Type : smell
- Titre : `LocalBackend` instancié mais inutilisé sur les chemins router publics
- Preuve : `jarvis/router.py:49` + méthodes `chat/mail/...` → DeepSeek uniquement ; `jarvis/backends/__init__.py:6-7`
- Impact : Dual-LLM trompeur ; code mort / confusion audit sécurité.
- Repro / condition : Inspection + tests `local_calls==0` (cité hors périmètre).
- Correctif proposé (sans coder) : Documenter « legacy / unused » ou retirer du chemin chaud.
- Confiance : moyenne

### F-P05-017
- Sévérité : INFO
- Type : doc-drift
- Titre : Docstrings agents encore Haiku/Sonnet/Opus/Claude
- Preuve : `agents/orchestrator.py:3,338` ; `agents/info.py:16` ; `_call_claude` nom `agents/__init__.py:167` ; `agents/memory.py:9-10`
- Impact : Maintenabilité ; pas de bug runtime.
- Repro / condition : Lecture code.
- Correctif proposé (sans coder) : Renommer helpers / docstrings → DeepSeek.
- Confiance : haute

### F-P05-018
- Sévérité : INFO
- Type : smell
- Titre : Aucun secret hardcodé dans prompts/ ; coûts trackés sur chemin non-stream
- Preuve : scan `prompts/**` (seule mention `sk-` = guidance `prompts/cursor/security_audit.md:24`) ; `llm.py:150-157` + `agents/__init__.py:247-255`
- Impact : Point positif checklist 8 (partiel à cause de F-P05-004).
- Repro / condition : N/A
- Correctif proposé (sans coder) : Conserver ; corriger stream.
- Confiance : haute

## Contrats vérifiés
| Contrat / invariant | Statut | Preuve |
|---|---|---|
| 1. `inject_persona` False orchestrator/memory ; True user-facing | OK | `orchestrator.py:333`, `memory.py:80`, défaut `agents/__init__.py:56` (coach/school/info/journal/productivity/devops sans override) |
| 2. Ordre system : life_profile + memory puis instructions | KO / PARTIEL | Prompts `school/coach/...` ont `{{life_profile}}` puis `{{memory_context}}`, mais `life_profile` souvent `""` (F-P05-009) ; persona/horodatage prepend (OK produit) |
| 3. `classify_task_type` / `_route_task` / `VOICE_MAX_TOKENS` / `HEAVY_TASK_MAX_TOKENS` | PARTIEL | Wired school+productivity (`school.py:50`, `productivity.py:183`, `__init__.py:333-364`, `llm.py:251-274`) ; stream générique sans heavy (F-P05-011) ; `productivity_triage` mort (F-P05-012) |
| 4. Escalade coach = modèle distinct | KO | `coach_deep == DEEPSEEK_MAIN_MODEL` (F-P05-001) |
| 5. Parsing JSON / ```save``` robuste | KO | Regex newline-strict school/journal/memory (F-P05-008) ; actions plus tolérantes |
| 6. Horodatage respecte `config.TIMEZONE` | KO | `datetime.now()` + label Paris hardcodé (F-P05-007) |
| 7. persona anti-emoji / anti-agent | OK (persona) / KO (devops) | `persona.txt:13-16,27-31` OK ; `devops.txt:1` contredit |
| 8. Coûts trackés ; secrets absents prompts | PARTIEL | Non-stream OK ; stream cost=0 ; pas de secrets dans prompts |
| Émotions TTS leading-tag | PARTIEL | Strip OK si émotion valide ; tags invalides / `[DEEP_ANALYSIS]` fuient ; stream bailout >20 chars |
| Prompt caching controllable | N/A | DeepSeek auto ; `use_cache` ignoré |
| `llm.py` ne délègue pas à `integrations/deepseek_client.py` | OK | `llm.py` httpx autonome (frontière P08 non traversée) |

## Frontières / dépendances
- Signale vers **P12** (`jarvis/cognitive/`, `agents/devagent/`) : routing cognitif Flash/Main/Cursor/Ollama hors scope ; non audité ici.
- Signale vers **P08** : `integrations/deepseek_client.py` non utilisé par `llm.py` ; package `jarvis/backends/deepseek.py` parallèle (double client DeepSeek).
- Signale vers **P04/pipeline** : persistance messages / WS consomme `emotion`, `agent`, `cost` — fuite `agent` dans events classification (`orchestrator.py:682`) à traiter côté UI (P14/P15).
- Signale vers **P06** : `save_message(..., cost=)` ; horodatage DB vs TIMEZONE.
- Attendus consommés ailleurs : `orchestrator.handle` / `handle_stream`, `BaseAgent._call_claude`, prompts `persona`+agents, `estimate_cost`, `VOICE_MAX_TOKENS`, `HEAVY_TASK_MAX_TOKENS`, `AGENT_MODELS`.

## Fichiers non lus
| Fichier | Motif |
|---|---|
| *(aucun fichier du périmètre omis)* | Les 17 `prompts/cursor/*.md` ont été scannés (secrets, modèles, rails), pas re-dérivés comme code métier agents |

## Couverture
- Liste exhaustive des fichiers lus (chemins relatifs), triée :
  - `agents/__init__.py`
  - `agents/autonomous_loop.py`
  - `agents/briefing_engine.py`
  - `agents/coach.py`
  - `agents/devops.py`
  - `agents/display_text.py`
  - `agents/easter_eggs.py`
  - `agents/info.py`
  - `agents/journal.py`
  - `agents/memory.py`
  - `agents/orchestrator.py`
  - `agents/productivity.py`
  - `agents/school.py`
  - `jarvis/backends/__init__.py`
  - `jarvis/backends/deepseek.py`
  - `jarvis/backends/local.py`
  - `jarvis/exceptions.py`
  - `jarvis/message_intelligence.py`
  - `jarvis/models.py`
  - `jarvis/router.py`
  - `jarvis/settings.py`
  - `llm.py`
  - `prompts/agent.txt`
  - `prompts/autonomous_loop.txt`
  - `prompts/coach.txt`
  - `prompts/contact_chat.txt`
  - `prompts/continuous_extractor.txt`
  - `prompts/continuous_synthesizer.txt`
  - `prompts/cursor/android_feature.md`
  - `prompts/cursor/backend_feature.md`
  - `prompts/cursor/bug_fix.md`
  - `prompts/cursor/ci_repair.md`
  - `prompts/cursor/database_migration.md`
  - `prompts/cursor/documentation_sync.md`
  - `prompts/cursor/feature_implementation.md`
  - `prompts/cursor/frontend_feature.md`
  - `prompts/cursor/integration_validation.md`
  - `prompts/cursor/performance_audit.md`
  - `prompts/cursor/refactor_safe.md`
  - `prompts/cursor/regression_review.md`
  - `prompts/cursor/release_build.md`
  - `prompts/cursor/runtime_diagnosis.md`
  - `prompts/cursor/security_audit.md`
  - `prompts/cursor/self_improvement.md`
  - `prompts/cursor/self_repair.md`
  - `prompts/cursor/test_creation.md`
  - `prompts/cursor/voice_pipeline.md`
  - `prompts/cursor_bug_fix.txt`
  - `prompts/devops.txt`
  - `prompts/email_analyzer.txt`
  - `prompts/fitness_meal_analyzer.txt`
  - `prompts/fitness_meal_vision.txt`
  - `prompts/imessage_extractor.txt`
  - `prompts/info.txt`
  - `prompts/journal.txt`
  - `prompts/location_analyzer.txt`
  - `prompts/memory.txt`
  - `prompts/orchestrator.txt`
  - `prompts/persona.txt`
  - `prompts/productivity.txt`
  - `prompts/school.txt`

<<<FIN_RAPPORT P05>>>

<<<RAPPORT P06 file=P06_database_migrations.md>>>

# AUDIT P06 — Database et migrations (lecture seule)

**Périmètre :** `database/`  
**Méthode :** inventaire DDL + exécution `SCHEMA` + `run_migrations()` sur `:memory:` + revue ligne-à-ligne des chemins critique (get_db, UPDATE dynamiques, emit, TZ, FTS, FK).  
**Verdict global :** couche runtime saine sur transactions / permissions / FTS ; **failles de défense en profondeur** sur UPDATE dynamiques ; **doc-drift** CLAUDE / en-tête `schema.sql` vs vérité mesurée **85 / 90**.

---

## 1. Source de vérité schéma

| Artefact | Rôle réel | Comptage dérivable |
|---|---|---|
| `database/schema.py` (`SCHEMA`) | **Source d’exécution** via `init_db()` → `executescript(SCHEMA)` | **50** `CREATE TABLE` |
| `database/migrations.py` (`run_migrations`) | Migrations **idempotentes** au boot (+ colonnes, tables, FTS, fitness, auth…) | + tables uniques vs schema.py |
| `database/devagent.py` | Appelé par `_migrate_devagent` dans `run_migrations` | **6** tables |
| `database/schema.sql` | **Snapshot historique**, **non exécuté** (header l’indique) | **46** applicatives (+ `sqlite_sequence`) |
| `database/migrations/*.sql` | Dossier **vide** (README seul) ; versionnées via `scripts/db_migrations.py` (frontière P11) | **0** fichier SQL |

Pipeline canonique :

```text
init_db()
  → executescript(SCHEMA)          # schema.py
  → run_migrations(conn)           # migrations.py (incl. DevAgent + FTS + fitness)
  → harden_sqlite_permissions()
(+ au démarrage app) apply_pending_migrations()  # scripts/ — 0 SQL aujourd’hui
```

**Écarts documentaires (doc-drift) :**

| Source | Affirme | Mesure code (2026-07-31) |
|---|---|---|
| `Architecture/32_…` | 85 persistantes / 90 FTS | **Exact** |
| `tests/test_event_bus_integration.py` | `len == 90` | **Exact** |
| `CLAUDE.md` | **76 / 81** | **Obsolète** |
| Header `schema.sql` | **70 / 75** | **Obsolète** |
| `migrations/README.md` | migrations dans `database/__init__.py` | **Faux** → `migrations.py` |

---

## 2. Comptage tables runtime (dérivé du code)

Exécution : `SCHEMA` + `run_migrations` sur `:memory:`, `PRAGMA foreign_keys=ON`.

| Définition | Count |
|---|---|
| Tables persistantes (hors `messages_fts*`) | **85** |
| Objets FTS5 (`messages_fts` + 4 auxiliaires) | **5** |
| Total physique FTS ON | **90** |
| Total FTS OFF (OperationalError → skip) | **85** |

Liste persistante (85) :

```
agentic_workflows, app_settings, app_usage, auth_rate_limits, commitments,
conversation_documents, conversation_turns, conversations, cross_insights,
cursor_delegation_jobs, daily_briefings, daily_rituals, day_scores,
dev_deployments, dev_interview_sessions, dev_loop_log, dev_loop_state,
dev_projects, dev_spec, device_pairing_attempts, device_pairing_codes, devices,
duplicate_findings, email_summaries, episodes, event_log,
fitness_program_sessions, fitness_programs, fitness_prompt_log,
fitness_session_progress, fitness_weight_logs, imessage_analysis_cache,
imessage_attachments, imessage_chat_handles, imessage_chats,
imessage_consumer_cursors, imessage_handles, imessage_message_attachments,
imessage_messages, imessage_reactions, imessage_sync_cursor, jarvis_journal,
life_context, life_profile, llm_action_logs, location_history, location_patterns,
location_point_dedup, meals, memory_embeddings, message_insights, messages,
mobile_chat_dedup, mobile_devices, mobile_pairing_codes, mood_log, mood_signals,
notifications, patterns, people, people_events, perf_benchmarks, places,
presence_sessions, push_subscriptions, recordings, relationship_events,
relationship_profiles, schema_migrations, school_documents, school_flashcards,
school_subjects, screen_activity, security_findings, sessions, tasks, trips,
user_facts, visits, voice_debug_log, water_intake, weekly_summaries,
wellbeing_logs, work_sessions, workouts
```

---

## CHECKLIST

### ✓/✗ 1. Source de vérité

- **OK runtime :** `schema.py` + `migrations.py`.
- **KO doc :** CLAUDE 76/81 ; header `schema.sql` 70/75 ; README migrations pointe mauvais fichier.
- **OK :** `schema.sql` annoté « NE PAS UTILISER ».

### ✓/✗ 2. `get_db()` — commits, rollback, threads

```59:70:database/core.py
@contextmanager
def get_db() -> Iterator[sqlite3.Connection]:
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
        harden_sqlite_permissions()
```

| Point | Statut |
|---|---|
| Commit en sortie nominale | ✓ |
| Rollback sur exception | ✓ |
| `foreign_keys=ON`, WAL, `busy_timeout=5000` | ✓ |
| Connexion neuve par appel (pas de partage thread) | ✓ (défaut `check_same_thread=True`) |
| Permissions 0600 DB/WAL/SHM + dir 0700 | ✓ via `harden_sqlite_permissions` / `ensure_private_*` |
| Double `conn.commit()` dans `cursor_jobs.py` (l.89,135,163,206) **à l’intérieur** de `get_db` | ⚠ redondant / odeur, pas de double-écriture |
| `get_db` imbriqués (`get_conversation_detail` → `get_conversation_history` ; `find_or_create_pattern` → `update_pattern` / `create_pattern`) | ⚠ 2 connexions, risque contention sous charge |

### ✓/✗ 3. Requêtes paramétrées / injection SQL

**Valeurs :** massivement `?` — OK.  
**Noms de colonnes en f-string :** plusieurs chemins **sans allowlist DB**.

| Site | Allowlist ? | Risque |
|---|---|---|
| `update_place` | ✓ `PLACE_MUTABLE_FIELDS` | Faible |
| `patch_person` | ✓ tuple `allowed` | Faible |
| fitness `update_*` | ✓ sets / `column_map` | Faible |
| `update_cursor_job_fields` | ✓ `allowed` | Faible |
| `update_voice_debug_latency` | colonnes hardcodées | Faible |
| `ALTER meals` migrations | noms littéraux tuple | Nul (DDL fixe) |
| **`update_conversation(**kwargs)`** | ✗ (allowlist seulement API) | Moyen — faille défense en profondeur |
| **`upsert_person(**kwargs)`** | ✗ | **Élevé** — `agents/memory.py` fait `upsert_person(name, **{field: value})` avec `field` issu du LLM |
| **`upsert_relationship_profile(**kwargs)`** | ✗ | Moyen — callers actuels contrôlés, API surface absente |
| `tasks.get_tasks` f-string `ORDER BY` | constante locale | Nul |

**Finding P1 — injection via nom de colonne :**  
`upsert_person` interpolate `kwargs` keys dans SQL. Un `field` LLM du type `x=1 WHERE id=1;--` / `name=?, ai_description=(SELECT…)` casse l’UPDATE ou exfiltre. Même pattern sur `update_conversation` si appelé hors API.

### ✓/✗ 4. `update_*` allowlist colonnes

| Fonction | Allowlist couche DB |
|---|---|
| `update_place` | ✓ |
| `patch_person` | ✓ |
| fitness / cursor_jobs | ✓ |
| `update_conversation` | ✗ |
| `upsert_person` | ✗ |
| `upsert_relationship_profile` | ✗ |

L’API conversations filtre `title|pinned|archived|tags` ; l’API people POST filtre 4 champs — **mais la couche DB reste ouverte**.

### ✓/✗ 5. Emit events après commit

Pattern dominant correct : `with get_db(): …` **puis** `event_bus.emit_nowait(...)`.

| Helper | Emit après commit ? |
|---|---|
| `save_message`, `create_conversation`, `update_conversation` | ✓ |
| `create_task`, `update_task_status` | ✓ |
| `add_fact`, `save_episode`, `create_pattern`, `upsert_person`, `add_life_context` | ✓ |
| `notifications` | pas d’emit direct (délègue service) | N/A |
| `find_or_create_pattern` (branche existante) | emit après `update_pattern` (autre connexion déjà commit) ; outer `get_db` n’a fait qu’un SELECT | ✓ data-wise / ⚠ nesting |

`event_log._persist_event` : handler `on("*")` → nouvel `get_db` après emit — cohérent avec « après commit » des producteurs.

### ✓/✗ 6. `time_buckets` / TIMEZONE

- `time_buckets.py` : `ZoneInfo(config.TIMEZONE)`, bornes UTC, DST 23/25h — **correct**.
- `core.get_usage_stats`, `stats.py` : utilisent `utc_bounds_for_local_dates` — **OK**.
- **Régressions TZ restantes :**
  - `patterns.get_today_messages` : `DATE(created_at) = ?` sur timestamps UTC stockés → **jour local faux** autour de minuit Paris.
  - `rituals.get_week_comparison` : `DATE(completed_at)` + `datetime.now().date()` **sans** `local_datetime` / bornes UTC.
  - `people.close_life_context` : `DATE('now')` = UTC SQLite, pas `TIMEZONE`.

### ✓/✗ 7. Doc-drift vs CLAUDE

CLAUDE affirme **76 / 81** ; runtime mesuré **85 / 90**. Doc canonique à jour : `Architecture/32_FRONTEND_DATABASE_SOURCE_OF_TRUTH.md` + test `len==90`.

### ✓/✗ 8. CASCADE / orphelins / UNIQUE

**CASCADE présents :** `people_events`, `relationship_*` → people ; `conversation_documents` → conversations ; `conversation_turns` → recordings ; fitness sessions/progress.

**NO ACTION / orphelins potentiels :**

| Relation | ON DELETE | Mitigation applicative |
|---|---|---|
| `messages.conversation_id` → conversations | NO ACTION | `delete_conversation` DELETE messages **à la main** ✓ |
| `recordings.conversation_id` | NO ACTION | pas de cleanup dans `delete_conversation` → **orphelins possibles** |
| `agentic_workflows.conversation_id` | NO ACTION | idem |
| `visits` / `trips` / `location_*` → places | NO ACTION | `delete_place` nettoie manuellement ✓ |
| iMessage mirror FKs | NO ACTION | cohérent miroir |
| DevAgent enfants → `dev_projects` | NO ACTION | orphelins si delete projet sans cascade app |

**UNIQUE notables :** `people.name`, `email_summaries.gmail_id`, `event_log.event_id`, devices/mobile tokens, dedup location/mobile chat, fitness `(program_id, position)`, etc. — solides.

**FTS :** external content + 3 triggers + rebuild si désync ; `_fts_query` quote les tokens — **bon**. Fallback LIKE si FTS5 absent.

---

## Findings (sévérité)

**[P1] Allowlist absente sur `upsert_person` — injection colonne via LLM** — `database/people.py:105-121`  
`agents/memory.py` passe `field` LLM dans `**{field: value}`. Interpole directement dans `UPDATE/INSERT`. Corriger : frozenset colonnes mutables comme `patch_person` / `PLACE_MUTABLE_FIELDS`.

**[P1] Même motif sur `update_conversation` / `upsert_relationship_profile`** — défense en profondeur manquante côté DB même si l’API conversations filtre.

**[P2] `DATE(created_at)` / `DATE(completed_at)` sans `time_buckets`** — `patterns.py:87`, `rituals.py:265-269` ; jour local faux vs UTC stocké.

**[P2] Doc-drift comptage tables** — CLAUDE 76/81, header `schema.sql` 70/75 vs **85/90** mesurés.

**[P2] `delete_conversation` ne purge pas `recordings` / `agentic_workflows`** — FK NO ACTION → lignes orphelines.

**[P3] `get_db` imbriqués + double `commit` cursor_jobs** — contention / dette, pas de bug fonctionnel évident en solo-user.

**[P3] `migrations/README.md` obsolète** (pointe `__init__.py`, dossier SQL vide).

---

## Sécurité (OWASP scoped DB)

| Item | Résultat |
|---|---|
| A01 Access control | Hors couche DB (middleware) |
| A02 Secrets / perms fichiers | ✓ 0600/0700 forcés |
| A03 Injection | ✗ UPDATE dynamiques sans allowlist DB |
| A04 Rate limit | N/A ici (`auth_rate_limits` existe) |
| A05 Errors | logs OK, pas de dump schéma runtime |
| A07 Sessions | table `sessions` + UNIQUE `token_hash` |
| A08 Intégrité migrations versionnées | checksum côté `scripts/db_migrations.py` ; runtime idempotent sans checksum de `_migrate_*` |

---

## Synthèse exécutive

La **source de vérité runtime est `schema.py` + `migrations.py`** (pas `schema.sql`). Post-`init_db` : **85 persistantes, 90 avec FTS5**. Transactions/`get_db`, FTS, permissions fichiers et emits post-commit sont globalement corrects. Priorité correctifs : **allowlists colonnes sur tous les UPDATE/INSERT dynamiques** (surtout `upsert_person`), aligner CLAUDE/header SQL sur **85/90**, remplacer les `DATE(...)` restants par `utc_bounds_for_local_dates`, et étendre le cleanup de `delete_conversation`.

<<<FIN_RAPPORT P06>>>

<<<RAPPORT P07 file=P07_event_bus_notifications.md>>>

```
════════════════════════════════════════════════════════════════════════
AUDIT P07 — Event bus et notifications
MODE          : lecture seule (aucune mutation)
OWNER         : P07 (incl. websocket_registry.py runtime)
DATE          : 2026-07-31
VERDICT       : PASS_WITH_FINDINGS
════════════════════════════════════════════════════════════════════════
```

## 1. Périmètre lu

| Fichier | Lignes | Rôle |
|---|---|---|
| `jarvis/event_bus.py` | 1–394 | Contrat `JarvisEvent`, bus, handlers, history, SSE helpers |
| `jarvis/events.py` | 1–221 | 10 classes domaine + assert catalogue |
| `jarvis/notification_service.py` | 1–98 | Priorité, dédup, push hook, emit |
| `jarvis/__init__.py` | 1–48 | Lazy `JARVISRouter` — pas de couplage bus/LLM |
| `database/event_log.py` | 1–86 | Persist `*`, lecture, unprocessed |
| `database/notifications.py` | 1–291 | Insert atomique + façade + push thread |
| `websocket_registry.py` | 1–47 | WS set + broadcast domaine |

Hors profondeur (preuves croisées uniquement) : émetteurs DB (`tasks/conversations/people/episodes/patterns/facts`), SSE `api/misc_integrations.py`, `api/lifespan.py` (`bind_loop`), handler TTS `scripts/audio_daemon.py` (Frontière P09/P10).

---

## 2. Checklist

| # | Item | Statut | Preuve |
|---|---|---|---|
| 1 | Classes `events.py` vs émetteurs | **PASS** (écarts mineurs) | 10/10 classes émises après commit ; trous sur delete/end non catalogués |
| 2 | `emit` / `emit_nowait` / `bind_loop` | **PASS_WITH_FINDINGS** | `lifespan` bind/unbind OK ; fallback `asyncio.run` dangereux ; `create_task(emit)` hors `_pending` |
| 3 | Handler sync SQLite sur loop asyncio | **FAIL** | `_persist_event` sync dans `gather` → I/O SQLite sur le thread de la loop |
| 4 | Échec handler n’arrête pas les autres | **PASS** | `_invoke_handler` catch `Exception` + `gather` |
| 5 | urgent/high → push/TTS | **PASS** (Frontière TTS) | push dans service ; TTS via `@event_bus.on("notification.created")` hors P07 |
| 6 | `get_unprocessed_events` sans replay auto | **PASS** (contrat tenu) | lecture seule ; **aucune** écriture `processed_by` dans le repo |

---

## 3. Cartographie classes ↔ émetteurs

| Classe | `EVENT_TYPE` | Émetteur | Après commit ? |
|---|---|---|---|
| `NotificationCreated` | `notification.created` | `notification_service.create` | Oui (`_insert` puis emit si `created`) |
| `TaskCreated` | `task.created` | `database/tasks.create_task` | Oui |
| `TaskUpdated` | `task.updated` | `update_task_status` | Oui (si rowcount>0) |
| `ConversationUpdated` | `conversation.updated` | `create_conversation` / `update_conversation` | Oui |
| `MessageSent` | `message.sent` | `save_message` | Oui |
| `MemoryUpdated` | `memory.updated` | `database/people` (`add_life_context`) | Oui |
| `PersonUpserted` | `person.upserted` | `upsert_person` | Oui |
| `EpisodeSaved` | `episode.saved` | `save_episode` | Oui |
| `PatternDetected` | `pattern.detected` | `create_pattern` / `find_or_create_pattern` | Oui |
| `FactAdded` | `fact.added` | `add_fact` | Oui |

`assert DOMAIN_EVENT_CLASSES == DOMAIN_EVENT_TYPES` (`events.py:221`) verrouille le catalogue.  
`DOMAIN_EVENT_TYPES = EVENT_TYPES[-10:]` (`event_bus.py:85`) : couplage positionnel fragile.

Trous d’émission (hors catalogue Phase 3, à noter) : `delete_task`, `end_conversation`, `update_pattern` seul — pas d’événement.

Écart sémantique : `find_or_create_pattern` ré-émet `PatternDetected` sur pattern **existant** (`patterns.py:45–47`).

---

## 4. Findings

### F-P07-01 — Handler sync SQLite sur la boucle asyncio
- **Sévérité** : HAUTE  
- **Checklist** : 3  
- **Où** : `database/event_log.py:12–35` + `jarvis/event_bus.py:320–335`  
- **Fait** : `@event_bus.on("*") def _persist_event` est synchrone (`get_db()` + INSERT). `_invoke_handler` l’exécute inline dans la coroutine ; `emit()` l’attend via `asyncio.gather`. Toute émission bloque le thread de la loop le temps du round-trip SQLite.  
- **Impact** : latence cross-cutting sur chat/voix/WS ; contention avec autres handlers async du même `emit`.  
- **Attendu** : `async` + `asyncio.to_thread` / executor, ou queue de journalisation découplée.

### F-P07-02 — Handler TTS attendu dans le même `gather` que le bus
- **Sévérité** : HAUTE (Frontière P09/P10 pour le corps TTS ; contrat bus = P07)  
- **Checklist** : 2, 5  
- **Où** : `scripts/audio_daemon.py:1973–1988` + `event_bus.emit` gather  
- **Fait** : `_speak_priority_notification` est async et `await audio_daemon._play_tts(...)`. `emit()` ne termine qu’après TTS. `emit_nowait` → task tracked → `wait_until_idle` / shutdown attendent la lecture audio.  
- **Impact** : couplage domaine→audio ; file d’émissions `notification.created` sérialisée derrière le TTS.  
- **Attendu** : handler fire-and-forget (`create_task`) ou file TTS dédiée, hors criticité du bus.

### F-P07-03 — Checksum ≠ sérialisation `event_log`
- **Sévérité** : MOYENNE  
- **Checklist** : 1 (intégrité contrat)  
- **Où** : `event_bus.py:130–141` vs `event_log.py:32`  
- **Fait** : checksum = `json.dumps(..., separators=(",", ":"))` ; persist = `json.dumps(...)` séparateurs défaut `(", ", ": ")`. Re-hash de `payload_json` ≠ `checksum`. Checksum ne couvre que le payload, pas `event_type`/`event_id`/`version`/`source`/`timestamp`.  
- **Impact** : vérification d’intégrité future cassée ; métadonnées non protégées.

### F-P07-04 — `emit_nowait` fallback `asyncio.run`
- **Sévérité** : MOYENNE  
- **Checklist** : 2  
- **Où** : `event_bus.py:345–361`  
- **Fait** : sans loop courante et sans `_loop` bound → `asyncio.run(self.emit(event))`. Nouveau loop vs `connected_ws_lock` / handlers async → risque « Lock bound to a different event loop ». OK en prod après `bind_loop` (`api/lifespan.py:56`) ; fragile avant bind / hors process app.  
- **Parallèle** : `asyncio.create_task(event_bus.emit(...))` (agents/tts/audio) **contourne** `_pending` → `wait_until_idle` incomplet.

### F-P07-05 — Dédup notifications non unique sous concurrence
- **Sévérité** : MOYENNE  
- **Checklist** : 5 (politique notifs)  
- **Où** : `notifications.py:45–71` ; index `idx_notif_dedup` non-UNIQUE (`schema.py:241`, `migrations.py:744–746`)  
- **Fait** : `INSERT…WHERE NOT EXISTS` dans une transaction ; deux connexions concurrentes peuvent toutes deux insérer. Index = lookup, pas contrainte.  
- **Atténuation** : usage solo local ; fenêtre 300s ; tests mono-thread OK (`test_notification_service.py`).

### F-P07-06 — Replay : API lecture sans moteur ni marquage
- **Sévérité** : BASSE (attendu documenté)  
- **Checklist** : 6  
- **Où** : `event_log.py:73–86` ; colonnes `processed_by`/`processed_at`/`error` (`schema.py:186–188`)  
- **Fait** : `get_unprocessed_events` liste `processed_by IS NULL`. **Aucune** fonction `mark_processed` dans le dépôt (`rg processed_by\s*=` → 0). Pas de replay au startup (`lifespan` bind seulement). Conforme ADR / `Architecture/10_GOUVERNANCE_EVENTS.md`.  
- **Écart** : `get_unprocessed_events` ne parse pas `payload_json` (contrairement à `get_event_log`).

### F-P07-07 — SSE : drop abonnés lents ; history RAM only
- **Sévérité** : BASSE  
- **Checklist** : 2 (diffusion)  
- **Où** : `event_bus.py:294–311` ; `api/misc_integrations.py:98–117`  
- **Fait** : `QueueFull` → unsubscribe (perte d’événements). History 200 en RAM, rejouée 30 au connect SSE — **pas** depuis `event_log`. Redémarrage = trou SSE ; journal DB orphelin côté UI temps réel.

### F-P07-08 — Types inconnus : warn puis emit quand même
- **Sévérité** : BASSE  
- **Où** : `event_bus.py:115–119` vs `on()` qui `raise` (`227–228`)  
- **Fait** : émission permissive ; abonnement strict. Clients peuvent recevoir des types hors catalogue.

---

## 5. Points validés (PASS)

**Concurrence / isolation handlers** — `test_event_bus_contract.py:47–77` : fast handler avance pendant que slow attend ; failing handler isolé ; wildcard sync OK.

**Immuabilité / checksum payload / aliases** — frozen dataclass ; `to_dict()` expose `event_type`+`type`, `payload`+`data` ; test unitaire OK.

**`bind_loop` cycle de vie** — `lifespan` : `bind_loop` au start, `wait_until_idle` + `unbind_loop` au stop.

**Push urgent/high** — uniquement si `created` ; dédup skip push+event (`notification_service.py:58–74`) ; thread daemon best-effort (`notifications.py:115–144`) — chiffrement push hors scope (→ P02).

**WS broadcast domaine** — `websocket_registry.py:44–47` sur `DOMAIN_EVENT_TYPES` ; snapshot hors lock ; morts retirés. P07 OWN runtime ; P01 = assemblage imports uniquement.

**`jarvis/__init__.py`** — pas d’import bus/LLM au load ; `__getattr__` lazy `JARVISRouter` — conforme CLAUDE.md.

**Idempotence journal** — `INSERT OR IGNORE` sur `event_id` ; re-emit même UUID ne duplique pas (`test_event_bus_integration.py:113–120`).

---

## 6. Flux runtime (synthèse)

```
DB mutation (commit)
  └─ emit_nowait(DomainEvent)
        ├─ [thread] run_coroutine_threadsafe → loop liée
        └─ [async]  create_task(emit)
              emit():
                history RAM
                queues SSE (drop si full)
                gather handlers:
                  ├─ _persist_event (SYNC SQLite)     ← F-P07-01
                  ├─ broadcast_domain_event (WS)
                  └─ _speak_priority_notification    ← F-P07-02 / P09
```

SSE `/api/events/stream` : history 30 + queue `*` (tous types, pas seulement domaine).

---

## 7. Frontières

| Sujet | Owner | Note |
|---|---|---|
| `websocket_registry.py` runtime | **P07** | P01 : montage/assemblage seulement |
| `push.py` crypto / VAPID | **P02** | P07 : appel `_dispatch_push_notification` seul |
| TTS / `audio_daemon` conso notifs | **P09/P10** | Handler enregistré sur le bus : contrat d’attente = finding P07 |
| SSE route `events_stream` | API (voisin) | Consommateur du bus ; comportement Queue/history audité ici |
| Émetteurs `database/*.py` | DB / P03 | Contrats d’émission vérifiés ; SQL métier non re-audité |

---

## 8. Matrice checklist → verdict local

| Checklist | Verdict |
|---|---|
| 1 Classes vs émetteurs | PASS (+ F-P07-03 intégrité, PatternDetected re-emit) |
| 2 emit / nowait / bind | PASS_WITH_FINDINGS (F-P07-02, F-P07-04, F-P07-07) |
| 3 Sync SQLite sur loop | **FAIL** (F-P07-01) |
| 4 Isolation handlers | PASS |
| 5 urgent/high push/TTS | PASS (push P07 ; TTS délégué, couplage gather = finding) |
| 6 Unprocessed sans replay | PASS (F-P07-06 = dette assumée) |

---

## 9. Verdict global

**PASS_WITH_FINDINGS** — architecture Phase 3 globalement respectée (10 événements typés, emit après commit, isolation handlers, dédup+push, journal idempotent, replay non implémenté comme documenté).

Bloquants de production à traiter en priorité :
1. **F-P07-01** — persist sync sur loop  
2. **F-P07-02** — TTS dans le criticité path de `emit`  
3. **F-P07-03** — aligner sérialisation checksum / `payload_json`

Pas de correctif dans ce passage (audit lecture seule).

<<<FIN_RAPPORT P07>>>

<<<RAPPORT P08 file=P08_integrations_os_cloud.md>>>

# AUDIT P08 — Intégrations

## 0. MÉTA

| Champ | Valeur |
|---|---|
| `ID_PERIMETRE` | P08 |
| `NOM` | Intégrations |
| `MODE` | Lecture seule — ligne par ligne |
| `INCLUS` | `integrations/**/*.py` (28 modules) |
| `EXCLUS` | `actions.py` → P04 ; routage cognitif Cursor → P12 |
| `DATE` | 2026-07-31 |
| `METHODE` | Lecture source + grep AST/patterns + croisement tests `test_apple_data.py` |

---

## 1. CHECKLIST

| # | Critère | Statut | Preuve |
|---|---|---|---|
| 1 | `apple_data` : façade unique `chat.db`, `mode=ro`, `query_only` | **PASS** | `apple_data.py:92-102` — `file:…?mode=ro` + `PRAGMA query_only = ON` + factory `_ReadOnlyConnection` |
| 2 | Aucune autre connexion `chat.db` | **PASS** | Seul `sqlite3.connect` sur Messages = `apple_data.py:94`. Reader/import/bridge délèguent. Garde AST `tests/test_apple_data.py:160-214`. `contacts.py:119` ouvre **AddressBook**, pas Messages |
| 3 | `computer.run` : shell vs allowlist ; `is_safe` | **FAIL partiel** | Denylist regex (`computer.py:18-50`) + `create_subprocess_shell` (`:64-71`) + `env={**os.environ}` — **pas d’allowlist**. Terminal LLM passe par `shell_safety` (P04) |
| 4 | `shell_safety.py` : plans one-shot, pas d’exec avant confirm | **PASS** | `prepare_shell_plan` enregistre sans exécuter (`:335-372`) ; `execute_shell_plan` consomme une fois (`:392-398`, `:431`) ; `create_subprocess_exec` sans shell (`:442`) |
| 5 | iMessage send : échappement AS + split 2000 | **FAIL partiel** | Corps échappé (`imessage.py:175-177`, `_applescript.py:212-221`) + `MESSAGE_CHUNK_SIZE=2000` (`:33`, `:215-218`). **`self.target` non échappé** (`:185`) |
| 6 | Location Haversine / radius | **PASS** (délégation) | `location.py` appelle `resolve_place` / `haversine` de `database/location_helpers.py:31-57` (`dist <= radius_meters`) |
| 7 | Timeouts httpx/subprocess partout | **PASS** avec notes | `_applescript` timeout ; weather 10s ; web_search 8s ; deepseek 120/15 ; fcm 15s ; shell_safety `wait_for` ; cursor `communicate(timeout=)`. Contacts AddressBook : **pas de `timeout=`** sur `sqlite3.connect` |
| 8 | `code_executor` : surface morte vs dangereuse | **FAIL** (dormant armé) | Aucun appelant `.execute()` hors status. Mais init avec `auto_run=True` si `CODE_EXECUTOR_ENABLED` défaut `true` (`config.py:216`, `code_executor.py:32-47`) |
| 9 | Pas de clé API hardcodée | **PASS** | Clés via `config.*` / fichier FCM path. Aucun `sk-` / Bearer littéral dans `integrations/` |

---

## 2. FINDINGS

### F-P08-01 — HAUTE  
**`computer.run` = shell + denylist, pas allowlist**  
`integrations/computer.py:44-71`

- `is_safe` = blacklist (rm -rf /, sudo rm, curl\|bash…). Contournable (`rm -rf "$HOME"`, `python -c …`, `osascript`, etc.).
- `create_subprocess_shell` + `executable=COMPUTER_SHELL` + copie complète de `os.environ` (fuite de secrets vers l’enfant).
- Atténuation : `_action_terminal` (P04) n’appelle plus `computer.run` — utilise `shell_safety`. Mais `find_files` / `open_app` / infos système passent encore par `run()` ; l’API publique reste dangereuse si réutilisée.

**Reco :** déprécier `run()` pour toute entrée LLM ; restreindre aux argv fixes via `create_subprocess_exec` ; env minimal.

---

### F-P08-02 — HAUTE  
**`code_executor` dormant mais armé**  
`integrations/code_executor.py:30-47`, `61-84`

- Open Interpreter, `auto_run = True`, `safe_mode = "auto"`, denylist FR très étroite.
- Défaut `CODE_EXECUTOR_ENABLED=true`.
- Aucun `.execute()` en prod (seul `api/misc_status.py` lit `.available`) → surface **morte pour le flux terminal**, **dangereuse si reconnectée**.

**Reco :** défaut `false` ; ne pas instancier l’interpréteur au import ; ou supprimer le module.

---

### F-P08-03 — MOYENNE  
**Adresse iMessage non échappée dans AppleScript**  
`integrations/imessage.py:182-187`

```185:186:integrations/imessage.py
            f'    set targetBuddy to participant "{self.target}" of targetService\n'
            f'    send "{escaped}" to targetBuddy\n'
```

- Le corps est échappé ; `self.target` non.
- `send_imessage_to_address(address, …)` (`:365-380`) accepte une adresse API → injection AppleScript possible via `"` / `\`.

**Reco :** `escape_applescript_string(self.target)` (et valider format téléphone/email).

---

### F-P08-04 — MOYENNE  
**FCM : `token_uri` issu du JSON service account**  
`integrations/fcm.py:50-82`

- `aud` et `httpx.post` utilisent `credentials.get("token_uri", …)`.
- Un fichier SA compromis → SSRF / vol de JWT signé.
- Timeouts OK (15s). Clé lue depuis path config, pas hardcodée.

**Reco :** allowlist `token_uri` ∈ `{https://oauth2.googleapis.com/token}` ; permissions fichier 0600 (hors P08).

---

### F-P08-05 — BASSE  
**Contacts AddressBook : `mode=ro` sans `query_only` / timeout**  
`integrations/contacts.py:119`

- Pas `chat.db` (checklist 2 OK).
- Incohérent avec le standard `apple_data`.

**Reco :** aligner sur `timeout=` + `PRAGMA query_only=ON`.

---

### F-P08-06 — BASSE  
**`escape_applescript_string` omet `\r`**  
`integrations/_applescript.py:212-221`

- Échappe `\`, `"`, `\n` seulement.
- `\r` dans titre notif / mail / message peut casser le littéral AS.

**Reco :** `.replace("\r", "\\r")`.

---

### F-P08-07 — INFO  
**Haversine hors `integrations/`**  
Logique correcte dans `database/location_helpers.py` ; `location.py` ne recalcule pas — acceptable pour P08, documenté.

---

## 3. INVENTAIRE MODULES (sécurité)

| Module | Rôle | Timeouts | Secrets | Risque résiduel |
|---|---|---|---|---|
| `apple_data.py` | Façade `chat.db` RO | sqlite timeout 5s | — | Faible |
| `imessage.py` / `imessage_reader.py` / `imessage_import.py` / `imessage_cursor.py` | Bridge / lecture / import | via façade + AS 30s | — | F-P08-03 |
| `imessage_daemon_client.py` | HTTP local :8193 | urllib 10s | — | Faible (loopback) |
| `_applescript.py` | osascript unifié | oui | — | F-P08-06 |
| `mail.py` / `calendar_api.py` / `contacts.py` / `notifications_macos.py` | AppleScript apps | oui (Calendar `pgrep`/`open` 2–2.5s) | — | Contacts SQLite F-P08-05 |
| `computer.py` | Shell denylist | `wait_for` | env complet | **F-P08-01** |
| `shell_safety.py` | Allowlist + plan opaque | oui | plans `token_urlsafe` | Faible (modèle correct) |
| `code_executor.py` | Open Interpreter | `wait_for` | `config.DEEPSEEK_*` | **F-P08-02** |
| `weather.py` / `web_search.py` / `deepseek_client.py` | HTTP | 10 / 8 / 120s | config | Faible |
| `fcm.py` | FCM v1 | 15s | fichier SA | F-P08-04 |
| `location.py` | Visites / trajets | N/A (DB) | — | Haversine OK (DB) |
| `ollama_client.py` / `ollama_control.py` | Vision / process | httpx + subprocess 3s | — | Popen serve volontairement sans timeout process |
| `cursor_*.py` | Délégation CLI | oui + killpg | `cursor_env` filtré | Subprocess OK ici ; **router → P12** |

---

## 4. CURSOR (`cursor_*.py`) — sous-périmètre P08 vs P12

**Audité ici (subprocess / sécurité) :**
- `cursor_cli.py` : `subprocess.run` timeout 15–20s, env safe, refuse CLI sans `--print`.
- `cursor_delegation.py` : worktree `jarvis/cursor/<id>`, jamais main ; `Popen` + `start_new_session` + `communicate(timeout)` + `killpg` ; prompt redacté ; confirmation avant run.
- `cursor_env.py` : pas de dump `os.environ` ; filtre KEY/TOKEN/SECRET.
- `cursor_required_tests.py` : `shell=False`, exe confinés au worktree, timeout borné ≤900s.

**À signaler à P12 (hors P08) :** composition prompts / routing Flash-Main / templates `prompts/cursor/*` / politiques `jarvis/cognitive/*` / confirmation UX / reprise jobs au lifespan.

---

## 5. RENVOIS

| Cible | Sujet |
|---|---|
| **P04** (`actions.py`) | `_action_terminal` → `prepare_shell_plan` / `execute_shell_plan` ; helpers `computer.open_app` / `find_files` / clipboard / battery |
| **P12** | Routeur cognitif, allowlist Ollama guard, orchestration délégation Cursor côté `jarvis/cognitive` |
| **P02 / auth** | Auth des endpoints qui appellent `send_imessage_to_address` / FCM (hors `integrations/`) |

---

## 6. VERDICT

| Dimension | Verdict |
|---|---|
| `chat.db` RO centralisé | **Conforme** |
| Shell LLM confirmé (`shell_safety`) | **Conforme** |
| Surface shell legacy (`computer.run`) | **Non conforme** (denylist + shell + env plein) |
| `code_executor` | **Non conforme** (dormant armé, défaut enabled) |
| iMessage send | **Partiel** (corps OK, destinataire KO) |
| HTTP / timeouts / secrets littéraux | **Conforme** |
| Cursor subprocess | **Conforme** (détail cognitif → P12) |

**Verdict global P08 : PARTIEL — 2 HAUTE, 2 MOYENNE, 2 BASSE.**  
Priorité correctifs : F-P08-03 (échappement `target`) → F-P08-02 (désarmer `code_executor`) → F-P08-01 (retirer/confiner `computer.run`) → F-P08-04 (allowlist `token_uri`).

<<<FIN_RAPPORT P08>>>

<<<RAPPORT P09 file=P09_audio_stt_tts.md>>>

# AUDIT — P09 — Audio et TTS/STT

## Métadonnées
- Agent / modèle : Auto (Composer) — auditeur pipeline audio
- Date : 2026-07-31
- Commit audité (`git rev-parse HEAD`) : `99a9b71833ceb457e8315efbda67982942e14dba`
- Branche : `main`
- Fichiers dans le périmètre (count) : 24 sources existants + `models/kokoro/` absent
- Fichiers lus (count) : 24
- Couverture estimée : 100 % des sources présentes ; 0 % binaires modèle (absents)

## Synthèse exécutive
Le contrat « STT local uniquement, pas de repli cloud STT » est tenu dans `audio/`, `native_audio/` et `scripts/audio_daemon.py` : chaîne faster-whisper / WhisperKit / whisper.cpp, décodage média local, TTS natif daemon sans Edge. Kokoro MLX sépare correctement logs (stderr) et audio WAV (stdout). Sélection micro Snowball/auto est en place ; sortie audio = défaut système (`sounddevice` puis `afplay`). Points faibles : half-duplex qui `stop_stream()` pendant qu’un thread lit encore le micro ; émotions TTS déclarées mais non appliquées à la synthèse ; filtre `is_stt_prompt_echo` absent du daemon natif ; caches TTS / verrou preload STT non thread-safe. `models/kokoro/` n’existe pas dans ce checkout.

## Findings
### F-P09-001
- Sévérité : HIGH
- Type : bug
- Titre : Half-duplex — `stop_stream()` concurrent au thread micro provoque la mort du capture
- Preuve : `scripts/audio_daemon.py:1368-1372` + `795-809`
```python
if self._half_duplex and self._stream:
    self._stream.stop_stream()
# … pendant que le thread fait :
data = stream.read(CHUNK_SAMPLES, exception_on_overflow=False)
except OSError as e:
    … break  # sortie définitive du thread input
```
- Impact : après un tour TTS (défaut `AUDIO_DAEMON_HALF_DUPLEX=True`), le lecteur PortAudio peut quitter ; micro mort jusqu’au restart watchdog / boucle immortelle (dizaines de secondes).
- Repro / condition : daemon actif, half-duplex on, une utterance → TTS → `stop_stream` pendant `read`.
- Correctif proposé (sans coder) : ne jamais `stop_stream` depuis le process loop ; s’appuyer uniquement sur `_tts_playing_event` pour ignorer les frames, ou ouvrir/fermer le stream dans le même thread que `read`.
- Confiance : haute

### F-P09-002
- Sévérité : HIGH
- Type : contrat-cassé
- Titre : Paramètre `emotion` TTS accepté puis ignoré par tous les moteurs
- Preuve : `audio/tts.py:55-66` (validation puis `_synth_edge(text)` sans emotion) ; `audio/tts.py:277-296` (Kokoro) ; `audio/tts.py:363-373` (macOS) ; `native_audio/ttskit_mlx.py:102-108` (`instruct=None` explicite)
- Impact : tags `[warm]`/`[urgent]` etc. n’influencent ni débit, ni pitch, ni voix — contrat CLAUDE.md / persona non honoré côté synthèse.
- Repro / condition : tout appel `synthesize(text, emotion="urgent")`.
- Correctif proposé (sans coder) : Edge SSML rate/pitch par émotion ; macOS `say -r` ; Kokoro/TTSKit mapping émotion→speed/instruct documenté ; ou retirer le contrat émotion du pipeline natif.
- Confiance : haute

### F-P09-003
- Sévérité : MEDIUM
- Type : bug
- Titre : Daemon natif n’applique pas `is_stt_prompt_echo` (contrairement au mobile)
- Preuve : `audio/stt_daemon.py:82-121` (filtre défini) ; `scripts/audio_daemon.py` — aucune occurrence ; usage hors périmètre `api/mobile_voice_service.py` (P04)
- Impact : sur silence/bruit, Whisper peut republier le `initial_prompt` ; le daemon ne rejette que ghosts YouTube + `avg_logprob`, pas l’écho de prompt.
- Repro / condition : utterance quasi silencieuse avec STT local + prompt FR.
- Correctif proposé (sans coder) : après transcription daemon, appeler `is_stt_prompt_echo(text)` et jeter comme le mobile.
- Confiance : haute

### F-P09-004
- Sévérité : MEDIUM
- Type : perf
- Titre : WAV RIFF traité comme conteneur compressé → re-décodage inutile
- Preuve : `audio/audio_format.py:25-26` (`RIFF` → True) ; `audio/stt_daemon.py:651-653` (branche decode si encoded)
- Impact : latence STT accrue (ffmpeg/`decode_audio`) pour tout WAV déjà PCM, y compris chemins qui enverraient du RIFF.
- Repro / condition : `DaemonSTT.transcribe(wav_bytes)`.
- Correctif proposé (sans coder) : exclure RIFF/WAV de `is_encoded_audio_container` ; decoder seulement WebM/MP3/OGG/M4A.
- Confiance : haute

### F-P09-005
- Sévérité : MEDIUM
- Type : smell
- Titre : `FasterWhisperBackend._load_lock` créé jamais utilisé — course au preload
- Preuve : `audio/stt_daemon.py:164` ; `166-207` / `216-217` (`preload_sync` sans `async with self._load_lock`)
- Impact : deux `transcribe_pcm` concurrentes au premier appel peuvent double-charger le modèle ou laisser `_load_failed` incohérent.
- Repro / condition : deux transcriptions async avant fin du premier preload.
- Correctif proposé (sans coder) : protéger `_loaded`/`_model` par le lock (sync `threading.Lock` dans l’executor).
- Confiance : moyenne

### F-P09-006
- Sévérité : MEDIUM
- Type : smell
- Titre : Caches TTS process-wide sans synchronisation
- Preuve : `audio/tts_cache.py:63-75` (`LastTTS._entry`) ; `78-124` (`SpeculativeTTS._cache` muté sans lock)
- Impact : daemon + WS (P04) peuvent lire/écrire concurremment → audio « répète » corrompu ou cache partiel.
- Repro / condition : `last_tts.store` pendant un `get` depuis un autre chemin async/thread.
- Correctif proposé (sans coder) : `threading.Lock` ou structure immutable copy-on-write.
- Confiance : moyenne

### F-P09-007
- Sévérité : MEDIUM
- Type : bug
- Titre : File d’utterances pleine → phrase jetée sans feedback utilisateur
- Preuve : `scripts/audio_daemon.py:1016-1020`
```python
except asyncio.QueueFull:
    logger.warning("[audio_daemon] utterance_queue pleine — utterance jetée")
```
- Impact : sous charge (LLM lent), parole utilisateur perdue silencieusement (log only).
- Repro / condition : `maxsize=3`, 4 phrases rapides pendant `processing`.
- Correctif proposé (sans coder) : drop oldest + bip / TTS court « Je n’ai pas tout saisi », ou backpressure VAD.
- Confiance : haute

### F-P09-008
- Sévérité : LOW
- Type : dette
- Titre : Pas de sélection de périphérique de sortie (seulement entrée Snowball)
- Preuve : `scripts/audio_daemon.py:1828-1876` (input only) ; `audio/audio_output.py:96-97` / `161-163` (`OutputStream` sans `device=`)
- Impact : checklist « output device » non couverte — lecture toujours sur défaut système (AirPods etc.).
- Repro / condition : multi-périphériques audio macOS.
- Correctif proposé (sans coder) : `AUDIO_DAEMON_OUTPUT_DEVICE` + `sounddevice`/`sd.default.device`.
- Confiance : haute

### F-P09-009
- Sévérité : LOW
- Type : dead-code
- Titre : Chemins Porcupine / volume wake séparés morts
- Preuve : `scripts/audio_daemon.py:1534-1538` (`_start_wake_detection` no-op) ; `1554-1646` (boucles encore présentes)
- Impact : maintenance trompeuse ; wake réel = volume sur flux unique (`934-964`).
- Repro / condition : lecture du code.
- Correctif proposé (sans coder) : supprimer ou isoler derrière un flag testé.
- Confiance : haute

### F-P09-010
- Sévérité : LOW
- Type : sécurité
- Titre : Silero VAD charge via `torch.hub` (réseau possible au boot)
- Preuve : `audio/vad_silero.py:81-86` (`torch.hub.load(..., trust_repo=True)`)
- Impact : hors contrat STT cloud, mais téléchargement réseau au démarrage VAD ; `trust_repo=True` élargit la surface.
- Repro / condition : premier boot sans cache hub, torch installé.
- Correctif proposé (sans coder) : bundle local du modèle Silero, `local_files_only` / chemin offline.
- Confiance : haute

### F-P09-011
- Sévérité : INFO
- Type : doc-drift
- Titre : `models/kokoro/` absent du checkout — ONNX non auditable
- Preuve : `audio/tts.py:150-151` / `engine_config.py:12` pointent vers `models/kokoro/` ; `find` → répertoire inexistant
- Impact : backend `KOKORO_BACKEND=onnx` toujours `available=False` ici ; seuls MLX/macOS/Edge restent.
- Repro / condition : clone sans artefacts modèle.
- Correctif proposé (sans coder) : documenter setup ONNX obligatoire ; CI skip ou fixture minimale non binaire.
- Confiance : haute

### F-P09-012
- Sévérité : INFO
- Type : smell
- Titre : Erreurs TTS/STT souvent avalées en `b""` / `None` + log (pas de crash)
- Preuve : `audio/tts.py:109-111`, `419-454` (stderr `DEVNULL` sur `say`/`afconvert`) ; `audio/stt_daemon.py:271-273`, `631-633`
- Impact : robustesse OK (pas de crash) ; diagnostic macOS TTS difficile (pas de stderr).
- Repro / condition : `say` échec voix absente.
- Correctif proposé (sans coder) : capturer stderr sur échec `returncode != 0`, logger les 200 premiers caractères.
- Confiance : haute

## Contrats vérifiés
| Contrat / invariant | Statut | Preuve |
|---|---|---|
| 1. Jamais de repli cloud STT | OK | Aucune ref cloud STT dans `audio/`, `native_audio/`, `audio_daemon.py` ; `FallbackSTTBackend` local-only (`stt_daemon.py:422-471`) ; log `Cloud fallback: disabled` (`engine_config.py:108`) |
| 2. Kokoro MLX : stdout propre + format WAV | OK | Logs → `sys.stderr` (`kokoro_mlx.py:132-136`, `208-210`) ; bridge lit `stdout` (`kokoro_bridge.py:76-89`) ; défaut `--format wav` + header RIFF (`kokoro_mlx.py:94-111`, `169-174`) |
| 3. Sélection input Snowball vs défaut | OK (input) / N/A partiel (output) | Priorité config → Snowball/Shiver → défaut système (`audio_daemon.py:1828-1876`) ; pas de device output (F-P09-008) |
| 4. afplay / afconvert / sounddevice | OK | `sounddevice` prioritaire (`audio_output.py`, `audio_daemon.py:1760-1763`) ; `afplay` fallback + sons wake/end (`1750-1803`) ; `afconvert` dans macOS TTS (`tts.py:407-438`) |
| 5. Émotions TTS | KO | Paramètre présent, synthèse inchangée (F-P09-002) |
| 6. Erreurs silencieuses vs crash | OK avec réserve | Daemon : boucle immortelle + logs ; moteurs : return vide + `logger` ; réserve F-P09-001/007/012 |
| 7. Thread safety / queues | KO partiel | `voice_queue` bien protégé (`asyncio.Lock`/`Condition`) ; micro→queue via `call_soon_threadsafe` OK ; faiblesses F-P09-001/005/006 |
| Décodage WebM/M4A/MP3/OGG local | OK | `DaemonSTT._decode_media_bytes` via `faster_whisper.audio.decode_audio` (`stt_daemon.py:614-633`) |
| TTS daemon sans Edge | OK | `get_native_tts_engine` : kokoro→macos→ttskit uniquement (`tts_native.py:105-132`) ; Kokoro fallback → macOS pas Edge (`tts.py:243-252`) |
| Diarisation | N/A (désactivée) | `transcribe_with_diarization` → `[]` (`stt_daemon.py:669-676`) ; `DIARIZATION_ENABLED` gate (`continuous_recorder.py:286-287`) |

## Frontières / dépendances
- Signale vers P04 : `api/voice_*.py`, `api/mobile_voice_service.py` (consomme `stt_local`, `is_stt_prompt_echo`, TTS web Edge) — non audité en profondeur.
- Signale vers P10 : `scripts/jarvis_daemon.py` file TTS sentinelle ; ici seulement handler `notification.created` → `_play_tts` (`audio_daemon.py:1975-1990`).
- Signale vers P01 : `config.TTS_ENGINE`, `STT_*`, `AUDIO_DAEMON_*`, `KOKORO_*`.
- Signale vers P03/P07 : `process_voice_fast` (`pipeline`) appelé depuis le daemon — hors P09.
- Attendus de ce périmètre consommés ailleurs : `audio.stt` / `tts` (`audio/__init__.py`), `voice_queue`, `native_audio_output`, sidecars `native_audio/*`.

## Fichiers non lus
| Fichier | Motif |
|---|---|
| `models/kokoro/**` (binaires `.onnx`, `voices.bin`, etc.) | Répertoire absent du checkout ; consignes = ignorer gros binaires |
| `native_audio/whisperkit_transcribe` | Binaire sidecar optionnel non présent (seul le bridge Python lu) |

## Couverture
- Liste exhaustive des fichiers lus (chemins relatifs), triée :
  - `audio/__init__.py`
  - `audio/audio_format.py`
  - `audio/audio_output.py`
  - `audio/continuous_recorder.py`
  - `audio/engine_config.py`
  - `audio/resample.py`
  - `audio/stt_daemon.py`
  - `audio/stt_local.py`
  - `audio/tts.py`
  - `audio/tts_cache.py`
  - `audio/tts_native.py`
  - `audio/vad_silero.py`
  - `audio/vad_utterance.py`
  - `audio/voice_queue.py`
  - `native_audio/__init__.py`
  - `native_audio/README.md`
  - `native_audio/kokoro_bridge.py`
  - `native_audio/kokoro_mlx.py`
  - `native_audio/kokoro_synthesize`
  - `native_audio/ttskit_bridge.py`
  - `native_audio/ttskit_mlx.py`
  - `native_audio/ttskit_synthesize`
  - `native_audio/whisperkit_bridge.py`
  - `scripts/audio_daemon.py`

<<<FIN_RAPPORT P09>>>

<<<RAPPORT P10 file=P10_daemon_multi_device_screen.md>>>

```yaml
id_perimetre: P10
nom: Daemon multi-device et screen
mode: lecture_seule
date: 2026-07-31
verdict_global: PASS_AVEC_RESERVES
resume: >
  Pairage (TTL / one-time / rate-limit IP), hash-only device token et header
  X-Device-Token sont correctement implémentés. Vision locale Ollama + Claude
  texte-only tenu sur le chemin local. Réserve majeure : l'analyse remote
  /api/devices/{id}/screen appelle _analyze_with_ollama(img) avec 1 argument
  alors que la signature en exige 3 — la vision distant est morte. TTS cooldown
  présent côté daemon local, absent côté file TTS remote. Control /api/control/*
  authentifié via session OU supervisor localhost+header.
```

---

## 1. Périmètre lu

| Fichier | Lignes | Statut lecture |
|---|---:|---|
| `scripts/jarvis_daemon.py` | 588 | complet |
| `scripts/screen_watcher.py` | 813 | complet |
| `scripts/jarvis_agent.py` | 376 | complet |
| `scripts/jarvis_launchd.py` | 298 | complet |
| `api/router_devices.py` | 295 | complet |
| `api/router_daemon.py` | 185 | complet |
| `database/screen_daemon.py` | 394 | complet |
| `requirements-agent.txt` | 14 | complet (imports agent) |

Hors périmètre cité mais nécessaire aux preuves checklist : `api/middleware.py` (gate session / device / supervisor), `config.py` (TTL / cooldown), `auth.hash_token`.

---

## 2. CHECKLIST

| # | Item | Statut | Preuve |
|---|---|---|---|
| 1 | Pairing codes : TTL, one-time, rate-limit IP | **OK** | `router_devices.py:74-116` + `screen_daemon.py:137-231` — TTL `DEVICE_PAIRING_TTL_MINUTES`, `used_at` one-shot, `device_pairing_attempts` par `client_key=request.client.host`, 429+Retry-After |
| 2 | Token device : hash only, header uniforme | **OK** | SHA-256 via `auth.hash_token` ; stocké `token_hash` ; raw rendu 1× au register/rotate ; listes API sans `token_hash` ; `_require_device_token` + `X-Device-Token` + `hmac.compare_digest` ; agent headers homogènes |
| 3 | Screenshot : Ollama local ; Claude texte only | **PARTIEL** | Local SW : images → Ollama only ; daemon `_on_screen_notable` envoie **texte** à `process_message_internal`. Remote : **appel cassé** (sig mismatch) → pas d'Ollama, fallback `remote_no_analysis` ; si ça marchait, Claude ne reçoit toujours que le texte `notable` |
| 4 | TTS cooldown / anti-spam | **PARTIEL** | Daemon local : `DAEMON_TTS_COOLDOWN` + DND + quiet hours (`jarvis_daemon.py:445-507`). Remote : queue `maxsize=10` sans cooldown ni quiet hours |
| 5 | Supervisor control auth | **OK** | `router_daemon.py` sans auth locale ; gate middleware : session navigateur **ou** `127.0.0.1/::1` + `X-Jarvis-Supervisor: 1` sur `/api/control/*` |
| 6 | Permissions / échecs silencieux | **PARTIEL** | Token agent `0600` + dir `0700` OK. Multiples `except: pass` (heartbeat/TTS agent, save screen remote). Capture/osascript échouent en warning/debug sans alerte opérateur |

---

## 3. CONSTATS

### P10-C01 — HAUTE — Vision remote cassée (signature)

- **Fichier** : `api/router_devices.py:174` vs `scripts/screen_watcher.py:645-647`
- **Fait** : `await _sw._analyze_with_ollama(img)` (1 positional) alors que la méthode exige `(img, app, window_info)` sans défaut. Confirmé AST : `call argc=1`, `defaults=0`.
- **Effet** : `TypeError` capturé L175-176 → `analysis=None` → branche `remote_no_analysis`. Aucune vision Ollama, aucun TTS notable remote. Pas de test couvrant `api_device_screen` + analyse.
- **Reco** : `await _sw._analyze_with_ollama(img, declared_app, None)` (ou wrapper public).

### P10-C02 — MOYENNE — Pas de plafond taille `image_b64`

- **Fichier** : `api/router_devices.py:149-164`
- **Fait** : décodage base64 + `PIL.Image.open` sans limite octets / dimensions. Device authentifié peut saturer RAM/CPU.
- **Reco** : rejeter si `len(image_b64)` > N ou pixels > plafond (aligné resize agent 1280×800).

### P10-C03 — MOYENNE — TTS remote sans cooldown / anti-spam

- **Fichier** : `api/router_devices.py:42-51, 203-216`
- **Fait** : file par device `maxsize=10` seulement. Pas de `DAEMON_TTS_COOLDOWN`, DND, ni quiet hours sur ce chemin (contrairement au daemon local).
- **Reco** : partager la politique cooldown/DND avant `queue.put_nowait`.

### P10-C04 — MOYENNE — Agent distant : pas d'exigence Tailscale/TLS

- **Fichier** : `scripts/jarvis_agent.py:339-345, 249-258`
- **Fait** : `--server` accepte `http://…` ; pas de vérif préfixe Tailscale / HTTPS ; screenshots JPEG voyage en clair si HTTP. Token 0600 OK. `ip_tailscale` non envoyé au register.
- **Reco** : refuser non-HTTPS hors loopback ; optionnellement vérifier CGNAT Tailscale ; envoyer `ip_tailscale`.

### P10-C05 — BASSE — Rate-limit pairing = IP socket

- **Fichier** : `api/router_devices.py:101` + `screen_daemon.py:149-171`
- **Fait** : `client_key = request.client.host`. Derrière proxy/Serve loopback, tous les essais partagent la même clé (lockout global ou contournement selon topologie).
- **Reco** : clé dérivée de `X-Forwarded-For` / Tailscale identity si proxy déclaré.

### P10-C06 — BASSE — Health devices : horodatages naïfs

- **Fichier** : `scripts/jarvis_daemon.py:550-565`
- **Fait** : `datetime.now()` local vs `last_heartbeat` SQLite `CURRENT_TIMESTAMP` (UTC typique) → faux offline / retard de bascule active.
- **Reco** : bornes UTC cohérentes (`timezone.utc`).

### P10-C07 — BASSE — Curseur iMessage avancé avant traitement

- **Fichier** : `scripts/jarvis_daemon.py:268-272`
- **Fait** : `advance_consumer_cursor(max rowid)` avant la boucle de triage/notif. Crash mid-loop → messages non notifiés, curseur déjà avancé.
- **Reco** : avancer après traitement réussi par message / batch.

### P10-C08 — BASSE — Échecs silencieux agent + save remote

- **Fichiers** : `jarvis_agent.py:202-203, 334-335` ; `router_devices.py:230-231`
- **Fait** : heartbeat/TTS `except: pass` ; save activity remote `except: pass`. Masque révocation token / disque plein.
- **Reco** : log warning throttlé ; backoff si 401.

### P10-C09 — INFO — Docstring daemon obsolète (Ollama triage)

- **Fichier** : `scripts/jarvis_daemon.py:7-16, 363-395`
- **Fait** : en-tête promet « triage local Ollama » ; `_local_triage` utilise DeepSeek Flash (`TRIAGE_MODEL` → `DEEPSEEK_FAST_MODEL`). Politique 2026 correcte ; doc mensongère.
- **Reco** : aligner le docstring (Ollama = Screen Watcher only).

### P10-C10 — INFO — Wake word stub (délégation P09)

- **Fichier** : `scripts/jarvis_daemon.py:522-546`
- **Fait** : si `WAKE_WORD_ENABLED`, boucle no-op + warning ; micro exclusif `audio_daemon`. Pas de double capture. Hors scope audio_daemon (P09).

### P10-C11 — INFO — `requirements-agent.txt` cohérent

- Imports tiers agent : `requests`, `PIL` uniquement — match `requests>=2.31`, `Pillow>=10.0`. OK.

### P10-C12 — INFO — launchd : chemins non quotés

- **Fichier** : `scripts/jarvis_launchd.py:46-52`
- **Fait** : `cd {PROJECT_DIR}` / `exec {VENV_PYTHON}` sans quotes. Casse si espaces dans le path. Auth N/A (local user LaunchAgent).

---

## 4. Carte de flux (vérifiée)

```text
[Local Mac Mini]
  jarvis_daemon.start
    ├─ _tts_loop (cooldown/DND/quiet) → voice_queue → audio_daemon play
    ├─ _notification_loop (5s)
    │    ├─ iMessage (skip si bridge) → DeepSeek triage → TTS + notif UI
    │    └─ Mail (skip si email_watcher) → DeepSeek triage → TTS + notif UI
    ├─ screen_watcher.ensure_started(require_ollama)
    │    └─ capture → crop → Ollama vision → on_notable(texte) → DeepSeek texte → TTS
    ├─ calendar 5 min
    ├─ device_health 30s (offline >120s)
    └─ wake_word (stub → P09)

[Remote jarvis_agent]
  pairing_code → POST /register (public, code one-time)
  token 0600 → X-Device-Token
  heartbeat 30s / screen JPEG si diff≥15% / poll TTS 2s
       │
       ▼
  router_devices.screen → Ollama ???  ← C01 CASSE
                       → process_message_internal(texte) si notable
                       → queue TTS device
```

**Invariant tenu (local)** : aucune image n'est passée à Claude/DeepSeek — uniquement `activity`/`notable` texte.

**Invariant remote** : même intention, mais analyse Ollama injoignable (C01).

---

## 5. Contrôles sécurité (synthèse)

| Contrôle | Résultat |
|---|---|
| Pairing start = session navigateur | OK (`middleware` non bypass ; test `test_pairing_start_requires_browser_session`) |
| Register = code one-time + rate-limit | OK |
| Token jamais re-listé | OK (tests pairing) |
| Heartbeat/screen/TTS = device token | OK (`_require_device_token` + bypass session ciblé) |
| Activate/rotate/revoke/list/screen-activity = session | OK |
| `/api/control/*` = session ou supervisor local+header | OK |
| Launchd KeepAlive 24/7 | OK (pas d'auth réseau ; scope user) |

---

## 6. VERDICT

| Champ | Valeur |
|---|---|
| **Verdict** | `PASS_AVEC_RESERVES` |
| **Bloquant prod multi-device** | **Oui — C01** (remote screen intelligence morte) |
| **Bloquant sécurité auth device** | Non — pairing/token/header solides |
| **Bloquant privacy image→cloud** | Non sur chemin local ; remote n'analyse pas (fail closed de fait) |
| **Actions prioritaires** | 1) Fix call `_analyze_with_ollama` 2) Cap taille image 3) Cooldown TTS remote 4) Durcir agent HTTPS |

Lecture seule : aucune modification, aucun commit, aucun PR.

<<<FIN_RAPPORT P10>>>

<<<RAPPORT P11 file=P11_workers_scheduler_qualite.md>>>

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━  
**AUDIT P11 — Scripts workers et outillage**  
**Mode :** lecture seule · **Date :** 2026-07-31  
**Verdict global :** **PASS conditionnel** — garde-fous self-healing / backups / shells / isolation tests OK ; écarts majeurs sur flags ENABLED du scheduler, idempotence LLM (location/rituals/relationship), et races email catch-up.  
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## 1. Périmètre

| | |
|---|---|
| **ID** | P11 |
| **Inclus** | `scripts/*.{py,sh}` sauf exclusions · `tools/` |
| **Exclus** | `audio_daemon.py`, `jarvis_daemon.py`, `screen_watcher.py`, `jarvis_agent.py`, `jarvis_launchd.py`, `tv_mcp_server.py`, `launch_tv_browser.sh` (→ P17 / daemons) |
| **Comptage** | **56** scripts + **1** tool = **57** artefacts |

---

## 2. Inventory (tous les fichiers inclus)

### 2.1 `scripts/` — Python (46)

| Fichier | Rôle | LLM | Notifs | Mutation code |
|---|---|---|---|---|
| `__init__.py` | package | — | — | — |
| `backfill_imessages.py` | CLI backfill iMessage | non | non | non |
| `catchup_after_downtime.py` | rattrapage mail + relations | via watchers | via email | non |
| `commitment_consistency.py` | score cohérence promesses | non | non | non |
| `commitments.py` | extract + overdue | fast | oui | non |
| `contact_alerts.py` | silence / non-réponse | non | oui | non |
| `contact_analytics.py` | métriques iMessage | non | non | non |
| `day_scoring.py` | scores journée | non | non | non |
| `db_maintenance.py` | backup Fernet + purge + budget | non | budget | non |
| `db_migrations.py` | migrations SQL | non | non | SQL only |
| `doomscroll_detector.py` | seuil apps | non | oui | non |
| `duplicate_scanner.py` | clones — rapport | non | résumé | non (report-only) |
| `email_watcher.py` | poll Mail → JSON → actions | fast | mac/UI/iMsg | non |
| `export_voice_debug.py` | export debug vocal | non | non | non |
| `favorite_places.py` | lieux délaissés | non | oui | non |
| `fitness_reminders.py` | rappels séance/repas | non | oui | non |
| `force_full_mac_sync.py` | sync Contacts+iMessage | non | non | DB only |
| `imessage_daemon.py` | daemon lecture chat.db | non | non | non |
| `imessage_import.py` | CLI import/doctor | non | non | non |
| `imessage_sync_health_check.py` | healthcheck sync | non | non | non |
| `install_git_hooks.py` | hooks locaux | non | non | hooks |
| `jarvis_journal.py` | journal majordome | main | non | non |
| `local_ci.py` | CI locale | non | non | non |
| `location_analyzer.py` | habitudes GPS | fast | mac+UI | non |
| `meeting.py` | clôture/résumé réunion | oui si capt | — | non |
| `message_predictor.py` | prédiction messages | non | non | non |
| `migrate_devagent.py` | migrate DevAgent | non | non | scoped |
| `perf_regression.py` | perf / rollback hook | non | non | non |
| `presence.py` | présence bureau | non | TTS | non |
| `procrastination_cost.py` | coût procrastination | non | non | non |
| `relationship_analyzer.py` | extract iMessage | fast | non | non |
| `relationship_graph.py` | graphe relations | non | non | non |
| `rituals.py` | roast/debrief/quote/… | fast/main | UI/TTS/iMsg | non |
| `scheduler.py` | APScheduler hub | via jobs | via jobs | non |
| `security_audit.py` | scan secrets | non | high | fix opt-in |
| `self_healing.py` | crash → diag / patch | oui | oui | **opt-in** |
| `self_improvement.py` | preuves → PR Cursor | via Cursor | non | **PR only** |
| `semantic_search.py` | embeddings locaux | non | non | non |
| `sync_contacts.py` | sync Contacts.app | non | non | DB |
| `test_coverage_scan.py` | génère tests | oui | non | **opt-in** |
| `test_kokoro.py` | smoke Kokoro | non | non | non |
| `test_macos_permissions.py` | diag permissions | non | non | non |
| `test_screen_capture.py` | smoke capture | non | non | non |
| `test_voice_pipeline.py` | smoke voice | non | non | non |
| `time_machine.py` | reconstruction jour | non | non | non |
| `timeline_generator.py` | timeline Haiku | fast | non | non |

### 2.2 `scripts/` — Shell (10 inclus)

| Fichier | `set -euo pipefail` | Ancrage chemins |
|---|---|---|
| `android_dev_https.sh` | oui | `cd` racine |
| `android_e2e_pairing.sh` | oui | `cd` racine |
| `generate_ssl.sh` | oui | `ROOT` via `$0` |
| `install_tailscale_cert.sh` | oui | abs via script |
| `jarvis_full_restart.sh` | oui | `cd` racine · WARN `$0` relatif |
| `launch_backend.sh` | oui | `cd` racine |
| `launch_supervisor.sh` | oui | `cd` racine |
| `setup_local_audio.sh` | oui | `cd` racine |
| `sync_android_ca.sh` | oui | `ROOT` |
| `verify_backend_https.sh` | oui | `cd` racine |

### 2.3 `tools/` (1)

| Fichier | Rôle |
|---|---|
| `tools/audit_architecture_truth.py` | audit non destructif → `artifacts/architecture_truth.json` (pas de DB runtime) |

---

## 3. Checklist (obligatoire)

| # | Critère | Statut | Preuve |
|---|---|---|---|
| **1** | `scheduler.py` : chaque job flag `ENABLED` + `try/except` | **FAIL partiel** | Tous les wrappers ont `try/except`. Flags absents ou incomplets pour plusieurs jobs (voir §4). |
| **2** | `email_watcher` : 1er cycle, anti-doublon, JSON fast | **PASS / WARN** | Premier cycle catchup L213–244 ; cache + `email_summaries` ; `llm.chat` fast `max_tokens=200` + `_parse_json`. WARN : races catch-up, `finally` marque traité même si LLM échoue, fan-out multi-canaux. |
| **3** | `SELF_HEALING_AUTO_APPLY` défaut `false` | **PASS** | `config.py:467-468` → `ENABLED=false`, `AUTO_APPLY=false` ; gate L268 `self_healing.py`. |
| **4** | Backups Fernet / permissions 0600 | **PASS** | V2 magic + Fernet ; `BACKUP_ENCRYPTION_ENABLED=true` ; `ensure_private_file` / `write_private_bytes` → `0o600`. |
| **5** | `semantic_search` threads vs isolation tests | **PASS** | Dispatch daemon dans `database/episodes.py:34-52` ; `conftest.py` autouse neutralise `_dispatch_semantic_indexing`. |
| **6** | Scripts shell : `set -e`, chemins | **PASS / WARN** | 10/10 `set -euo pipefail` ; 1 WARN `jarvis_full_restart.sh` (`$0` après `cd`). |

---

## 4. Matrice jobs `scheduler.py`

| Job id | try/except | Flag ENABLED | Notes |
|---|---|---|---|
| `morning_briefing` | ✓ | **aucun** | toujours enregistré ; coût LLM via agent |
| `check_overdue` | ✓ | `DESKTOP_NOTIFICATIONS` (partiel) | pas de notif DB, mac only |
| `fitness_reminders` | ✓ | `FITNESS_REMINDERS_ENABLED` (module) | ✓ |
| `location_analysis` | ✓ | `LOCATION_TRACKING` (module) | ✓ |
| `relationship_alerts` | ✓ | **aucun** | toujours /6h |
| `evening_summary` | ✓ | **aucun** | LLM |
| `weekly_summary` | ✓ | **aucun** | LLM |
| `relationship_analysis_daily` | ✓ | **aucun** | LLM batches |
| `db_backup` | ✓ | `BACKUP_ENABLED` | ✓ |
| `db_maintenance` | ✓ | **aucun** | toujours dim |
| `llm_budget` | ✓ | **aucun** | |
| `daily_roast` | ✓ | `RITUALS_ENABLED` | ✓ |
| `evening_debrief` | ✓ | `RITUALS_ENABLED` | ✓ |
| `daily_quote` | ✓ | `RITUALS_ENABLED` | ✓ |
| `birthday_check` | ✓ | `RITUALS_ENABLED` | ✓ |
| `coffee_break` | ✓ | **aucun** (`RITUALS` non checké) | /20 min 9–22 |
| `weekly_debrief` | ✓ | `RITUALS_ENABLED` | ✓ |
| `mood_signal` | ✓ | **aucun** | |
| `presence_tick` | ✓ | `PRESENCE_ENABLED` (module) | ✓ |
| `streaming_binge` | ✓ | **aucun** dédié | |
| `late_return` | ✓ | `LATE_RETURN_ENABLED` (module) | ✓ |
| `meeting_tick` | ✓ | `MEETING_CAPTURE_ENABLED` (module) | défaut false ✓ |
| `commitments_extract` | ✓ | `RITUALS_ENABLED` | ✓ |
| `commitments_overdue` | ✓ | `RITUALS_ENABLED` | ✓ |
| `duplicate_scan` | ✓ | `DUPLICATE_SCAN_ENABLED` (module) | ✓ |
| `security_audit` | ✓ | `SECURITY_AUDIT_ENABLED` (module) | ✓ |
| `test_gen` | ✓ | `AUTO_TEST_GEN_ENABLED` (module, false) | ✓ |
| `jarvis_journal` | ✓ | `JARVIS_JOURNAL_ENABLED` | ✓ |
| `doomscroll_check` | ✓ | **aucun** | |
| `missed_opportunities` | ✓ | **aucun** | |
| `self_improvement` | ✓ | `SELF_IMPROVEMENT_ENABLED` (défaut **false**) | enregistré seulement si true |

`setup_scheduler()` est idempotent (`replace_existing=True`).

---

## 5. Findings (par sévérité)

### FAIL

| ID | Fichier | Problème |
|---|---|---|
| F1 | `scheduler.py` | Critère « chaque job ENABLED » non tenu : briefing/soir/hebdo/relations/coffee/mood/doomscroll/missed/maintenance sans kill-switch dédié. |
| F2 | `location_analyzer.py` | Fenêtre 30 j rejouée chaque nuit → inserts patterns/faits répétés + coût LLM récurrent + notifs possibles. |
| F3 | `relationship_analyzer.py` | Sur échec LLM/JSON, le curseur ROWID avance quand même → messages perdus définitivement. |
| F4 | `email_watcher.py` + `catchup_after_downtime.py` | Pas de verrou inter-processus : catch-up parallèle au watcher → double LLM + double tâches/mac/iMessage. |
| F5 | `rituals.py` (roast/debrief) | Pas de guard « déjà généré aujourd’hui » avant LLM → rerun = re-coût + overwrite + TTS/notif. |
| F6 | `db_migrations.py` | `executescript` puis `record_migration` hors même transaction ; `DB_MIGRATIONS_AUTO_APPLY=true` par défaut → risque replay partiel. |

### WARN

| ID | Fichier | Problème |
|---|---|---|
| W1 | `email_watcher.py` | `finally` ajoute l’ID même si analyse échoue → pas de retry jusqu’à restart/catch-up. |
| W2 | `email_watcher.py` | Cap 20 non-lus : backlog ancien peut rester bloqué si les 20 plus récents restent non lus. |
| W3 | `email_watcher.py` | Fan-out volontaire mac + UI (+ push high) + iMessage ; dédup service 300 s ne couvre pas mac/iMessage. |
| W4 | `commitments.py` / `jarvis_journal.py` | Reruns = re-coût LLM (journal overwrite upsert date). |
| W5 | `self_improvement.py` | Sans fingerprint, chaque run peut re-proposer / re-déléguer Cursor si activé. |
| W6 | `fitness_reminders.py` | Fenêtre entre notif et `record_prompt` → risque double high-notif sous concurrence. |
| W7 | `contact_alerts.py` | Cutoff `datetime.now()` local vs timestamps UTC SQLite. |
| W8 | `security_audit` / `duplicate_scanner` | Identité positionnelle → faux « nouveaux » findings si lignes bougent. |
| W9 | `jarvis_full_restart.sh` | `$0` relatif fragile après `cd`. |
| W10 | `self_healing.py:221` | `getattr(..., "SELF_REPAIR_ENABLED", True)` — défaut dangereux si attribut absent (mitigé car présent dans `config`, défaut réel `false`). |

### INFO / PASS notables

- **Self-healing** : double opt-in, git-tracked only, `py_compile`, rollback régression, mode Cursor `pr_only` préféré.
- **Security auto-fix** : `SECURITY_AUTO_FIX_ENABLED=false` ; scan scheduler = rapport seul.
- **Auto test gen** : `AUTO_TEST_GEN_ENABLED=false` + dirs vides.
- **Self-improvement** : défaut `false` ; mode `pr_only`.
- **Backup** : chiffrement V2 Fernet + fail path permissions documenté.
- **Semantic** : threads daemon isolés en tests via conftest.
- **Shell** : 100 % `set -euo pipefail` dans le périmètre.

---

## 6. Mission themes — synthèse

| Thème | État |
|---|---|
| Idempotence jobs | Mitigée (email UNIQUE, doomscroll/favorites titres, security INSERT OR IGNORE) ; **faible** sur location/rituals/relationship cursor/self_improvement |
| Coûts LLM | Caps présents (email 200 tok / 20 mails ; commitments 300 ; rituals bornés) ; **fuites** via reruns non gardés |
| Double notifications | Dédup 300 s `(source,title,email_id)` ; mac/iMessage/TTS hors dédup ; catch-up parallèle = risque réel |
| Self-healing opt-in | **Conforme** (ENABLED + AUTO_APPLY false) |
| Backups chiffrés | **Conforme** (Fernet V2, 0600) |
| Scans sécu | Report-only planifié ; fix mécanique opt-in |
| Mutation auto codebase | Guards OK (`SELF_HEALING_AUTO_APPLY`, `SECURITY_AUTO_FIX`, `AUTO_TEST_GEN`, `SELF_IMPROVEMENT` false, Cursor PR) |

---

## 7. Config defaults critiques (réf.)

```
BACKUP_ENABLED=true
BACKUP_ENCRYPTION_ENABLED=true
SELF_HEALING_ENABLED=false
SELF_HEALING_AUTO_APPLY=false
SELF_REPAIR_ENABLED=false
SELF_IMPROVEMENT_ENABLED=false
SELF_MODIFICATION_MODE=pr_only
SECURITY_AUTO_FIX_ENABLED=false
AUTO_TEST_GEN_ENABLED=false
DB_MIGRATIONS_AUTO_APPLY=true   ← attention
MEETING_CAPTURE_ENABLED=false
```

---

## 8. Limites de cet audit

- Lecture statique uniquement (pas d’exécution des jobs, pas de mesure coût réel).
- Daemons exclus (P11) : interactions email_watcher ↔ `jarvis_daemon` hors scope strict, mais le daemon **court-circuite** sa boucle mail si le watcher tourne (hors inventaire).
- `__pycache__` / artefacts générés ignorés.

---

### Recommandations prioritaires (sans implémentation)

1. Ajouter un kill-switch `*_ENABLED` (ou réutiliser `RITUALS_ENABLED`) pour coffee/mood/doomscroll/missed/briefings/relationship jobs.  
2. Idempotence LLM : early-return si entrée du jour déjà présente (rituals, journal, location fingerprints).  
3. Relationship : n’avancer le curseur qu’après parse JSON réussi.  
4. Email : claim atomique DB / lock fichier avant analyse ; ne pas `add` l’ID en `finally` sur échec LLM.  
5. Migrations : une transaction unique SQL + `record_migration`, ou ledger dans le même `conn`.

<<<FIN_RAPPORT P11>>>

<<<RAPPORT P12 file=P12_cognitif_cursor_devagent.md>>>

# AUDIT — P12 — Cognitif, Cursor, DevAgent

## Métadonnées
- Agent / modèle : Cloud Agent (Composer) — auditeur architecture LLM routing
- Date : 2026-07-31
- Commit audité (`git rev-parse HEAD`) : `0c8f82296e884412fd1caceeadf04b291584f945`
- Branche : `elias/fitness-meal-ai-photo-8e4f`
- Fichiers dans le périmètre (count) : 53
- Fichiers lus (count) : 53
- Couverture estimée : 94% (prompts/cursor : invariants « jamais main / pr_only » vérifiés sur les 19 templates ; corps métier deep-read sur les chemins critiques)

## Synthèse exécutive
Le cœur de politique Flash/Main/Cursor/Ollama est **solide** : `CognitiveRouter.route()` est déterministe (regex, zéro LLM), Ollama est hors conversation et verrouillé par allowlist + contrat `offenders == []`, les jobs Cursor vivent en `jarvis/cursor/<job_id>` sous `.jarvis/worktrees/`, avec reprise au restart et refus explicite de travailler sur `main`/`master`. Les briefings respectent Main (écran) / Flash (voix) + dédup. Les écarts réels sont : (1) isolation DevAgent poreuse via `../` dans les écritures LLM, (2) « PR-only » affaibli par `CURSOR_ALLOW_PR=false` (défaut) et mutations quality opt-in hors Cursor, (3) redaction Cursor = secrets seulement (pas PII), (4) logs interview/spec DevAgent non rédigés. Verdict global : **PARTIEL — fondations conformes, trous d’isolation/redaction/contrat PR**.

## Findings

### F-P12-001
- Sévérité : HIGH
- Type : sécurité
- Titre : Path traversal DevAgent — écriture hors sandbox via chemins relatifs LLM
- Preuve : `agents/devagent/loop.py:37-41`
```python
def _write_generated_files(project_path: Path, files: dict[str, str]) -> None:
    for rel, content in files.items():
        full = project_path / "src" / rel
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content, encoding="utf-8")
```
  Même motif : `agents/devagent/refactor.py:105-108`. Aucun `resolve()` + `is_relative_to(project_path)`.
- Impact : un `rel` du type `../../../agents/foo.py` peut écrire dans le tree JARVIS hors `DEV_PROJECTS_ROOT/{slug}/`.
- Repro / condition : boucle DevAgent ou refactor avec payload `files` contrôlé par le LLM (ou réponse JSON malveillante/compromised).
- Correctif proposé (sans coder) : résoudre chaque cible et exiger `full.resolve().is_relative_to((project_path / "src").resolve())` ; rejeter `..` dans les parts ; tests d’injection.
- Confiance : haute

### F-P12-002
- Sévérité : MEDIUM
- Type : contrat-cassé
- Titre : Mode `pr_only` / self-mod : jobs peuvent terminer sans PR
- Preuve : enqueue lit `CURSOR_ALLOW_PR` / `CURSOR_ALLOW_PUSH` (défauts false côté config) — `integrations/cursor_delegation.py:222-225` ; ouverture PR conditionnelle `427-431` ; `_maybe_open_pr` early-return si `not allow_push` `495-496`. Autonomy expose le mode `pr_only` `api/router_cognitive.py:321-324`. Templates exigent PR (`prompts/cursor/self_repair.md:17,34,62`).
- Impact : self-repair / self-improvement / loop peuvent marquer `completed` sur branche `jarvis/cursor/*` locale sans draft PR — conforme « jamais main », non conforme à la promesse produit « s’arrête à la PR ».
- Repro / condition : `SELF_MODIFICATION_MODE=pr_only` + `CURSOR_ALLOW_PR=false` (défaut) + job self_repair réussi.
- Correctif proposé (sans coder) : pour templates autonomy (`self_repair`, `self_improvement`), forcer `allow_push`/`allow_pr=True` ou échouer le job si PR impossible ; documenter l’écart dans `/autonomy/settings`.
- Confiance : haute

### F-P12-003
- Sévérité : MEDIUM
- Type : sécurité
- Titre : Frontière PII Cursor = secrets only ; diagnostic expose `user_request`
- Preuve : redaction secrets `jarvis/security/redaction.py:10-37` (hors P12, consommé ici) ; usage systématique `integrations/cursor_delegation.py:182-218,315,378` ; vue publique omet le brut `130-157` ; vue diagnostic garde `user_request` tronqué `160-172` ; API `diagnostic=true` `api/router_cognitive.py:143,167`.
- Impact : emails/téléphones/noms dans une demande technique ne sont pas masqués avant envoi CLI / persistance ; un opérateur avec `diagnostic=true` lit le texte utilisateur (secrets masqués seulement).
- Repro / condition : enqueue avec PII dans `user_request` ; GET job `?diagnostic=true`.
- Correctif proposé (sans coder) : appliquer `PIIAnonymizer` (ou équivalent) avant composition prompt + insert ; restreindre diagnostic aux sessions admin et journaliser l’accès.
- Confiance : haute

### F-P12-004
- Sévérité : MEDIUM
- Type : sécurité
- Titre : Logs DevAgent — redaction seulement sur `dev_loop_log`
- Preuve : `database/devagent.py:269-277` (`redact_action_log_payload`) vs `save_interview_context` JSON brut `184-195` ; `save_spec` / deployments sans redact (record_deployment ~344+).
- Impact : réponses d’interview / specs / stdout tests de staging peuvent persister secrets ou PII en clair dans SQLite.
- Repro / condition : interview contenant un token ; ou staging avec stderr contenant un chemin/clé.
- Correctif proposé (sans coder) : même boundary `redact_action_log_payload` / `redact_sensitive_*` sur `context_json`, `spec_json`, `dev_deployments.log`.
- Confiance : haute

### F-P12-005
- Sévérité : MEDIUM
- Type : sécurité
- Titre : `run_isolated` DevAgent propage tout `os.environ`
- Preuve : `agents/devagent/executor.py:39-48` — `full_env = {**os.environ, **env}` puis `subprocess.run(...)`. Contraste Cursor : `build_cursor_safe_env` `integrations/cursor_env.py:42-70`.
- Impact : commandes projet (tests, git, LLM tools) héritent `DEEPSEEK_API_KEY`, tokens device, etc.
- Repro / condition : n’importe quel `run_isolated` pendant une boucle DevAgent sur machine avec `.env` chargé.
- Correctif proposé (sans coder) : réutiliser / adapter `build_cursor_safe_env` (ou allowlist) pour le sandbox DevAgent.
- Confiance : haute

### F-P12-006
- Sévérité : MEDIUM
- Type : robustesse / sécurité
- Titre : Redaction DB Cursor non défensive en profondeur
- Preuve : `_insert_cursor_job_row` écrit `user_request`/`prompt_sent` tels quels `database/cursor_jobs.py:104-126` ; redaction seulement dans `update_cursor_job` `173-183` avec `except Exception: pass`.
- Impact : appelant qui bypasse `enqueue` (ou échec silencieux de l’import redaction) persiste du secret en clair ; update peut aussi skipper la redaction.
- Repro / condition : `create_cursor_job({...prompt_sent: "sk-..."})` direct ; ou exception dans le bloc try d’update.
- Correctif proposé (sans coder) : redact dans `_insert_cursor_job_row` ; ne pas avaler l’échec de redaction (fail-closed ou log ERROR + redact fallback grossier).
- Confiance : moyenne

### F-P12-007
- Sévérité : LOW
- Type : dette
- Titre : `route_async` LLM fallback peut proposer Cursor sans recheck CLI
- Preuve : `jarvis/cognitive/router.py:293-324` — après `route()` déterministe, si `use_llm_fallback` et label `CURSOR`, force `execution_type="cursor"` sans rejouer le check `CURSOR_DELEGATION_ENABLED` / `_cli_info` (présent en sync `196-207`). Aucun caller prod de `route_async` (grep).
- Impact : surface morte aujourd’hui ; si branchée sans garde, fausse promesse Cursor.
- Repro / condition : appeler `route_async(..., use_llm_fallback=True)` avec Cursor off.
- Correctif proposé (sans coder) : supprimer l’API ou y réappliquer les mêmes gardes que `route()` ; ajouter un test de non-régression.
- Confiance : haute

### F-P12-008
- Sévérité : LOW
- Type : doc-drift
- Titre : Registry / message d’erreur Ollama omettent meal_analysis
- Preuve : allowlist `jarvis/cognitive/ollama_guard.py:18-23` inclut `app/fitness/meal_analysis.py` ; message d’erreur `145-147` « hors Screen Watcher / ollama_control » ; capability `screen_watcher.vision` dit « seul usage Ollama autorisé » `capability_registry.py:124-129`.
- Impact : opérateur / auditeur croit à 2 consommateurs alors qu’il y en a 3 ; faux positifs de diagnostic.
- Repro / condition : lecture registry / erreur policy.
- Correctif proposé (sans coder) : aligner libellés sur `OLLAMA_ALLOWED_MODULES`.
- Confiance : haute

### F-P12-009
- Sévérité : MEDIUM
- Type : contrat-cassé
- Titre : Quality router — mutations opt-in sur le codebase JARVIS (hors PR Cursor)
- Preuve : `api/router_quality.py:64-73` (`security/{id}/fix`), `76-81` (génération tests), `20-28` (install hook git). Doc CLAUDE : report-only sur JARVIS sauf opt-in.
- Impact : contournement du chemin self-mod worktree+PR si flags `.env` activés — écriture directe tracked files / hooks.
- Repro / condition : `SECURITY_AUTO_FIX_ENABLED=true` puis POST fix ; ou `AUTO_TEST_GEN_ENABLED`.
- Correctif proposé (sans coder) : router ces mutations via Cursor `pr_only`, ou exiger confirmation + audit log + refus si `SELF_MODIFICATION_MODE=pr_only`.
- Confiance : haute

### F-P12-010
- Sévérité : INFO
- Type : dead-code / smell
- Titre : `allow_merge` / `ExecutionType=workflow` non opérationnels
- Preuve : `allow_merge` persisté `cursor_delegation.py:225` — aucun `gh pr merge` dans le module ; `ExecutionType` inclut `"workflow"` `models.py:10` jamais émis par `route()`.
- Impact : configuration trompeuse (`auto_merge_low_risk` aspirational).
- Repro / condition : lecture config / modèles.
- Correctif proposé (sans coder) : retirer ou implémenter explicitement ; documenter « non branché ».
- Confiance : haute

### F-P12-011
- Sévérité : INFO
- Type : smell
- Titre : `/loop` auto-start Cursor sans confirmation humaine
- Preuve : `agents/autonomous_loop.py:234-242` — `auto_start=True`, `require_confirmation=False`. Commentaire : mode autonome explicite.
- Impact : risque accepté si l’utilisateur tape `/loop` ; pas un bypass chat/voix (ceux-ci passent par confirmation).
- Repro / condition : `/loop corrige le module X`.
- Correctif proposé (sans coder) : optionnel — confirmation pour `risk_level=high` même en loop ; journaliser dans autonomy settings.
- Confiance : haute

### F-P12-012
- Sévérité : LOW
- Type : dette
- Titre : Flag `allow_commit` stocké mais jamais lu dans le runner Cursor
- Preuve : écrit `cursor_delegation.py:222` ; grep runner — seule occurrence ; commits laissés au CLI dans le worktree. Garde réelle = `PROTECTED_BRANCHES` `411-421`.
- Impact : `CURSOR_ALLOW_COMMIT=false` n’a aucun effet.
- Repro / condition : désactiver le flag et lancer un job.
- Correctif proposé (sans coder) : honorer le flag (prompt + post-check clean tree) ou le supprimer de la config/API.
- Confiance : haute

## Contrats vérifiés
| Contrat / invariant | Statut | Preuve |
|---|---|---|
| 1. `router.py` déterministe avant LLM (chemin prod) | OK | `route()` L142-291 regex-only ; callers = `route_request` ; Ollama jamais dans `ExecutionType` |
| 1b. Aucun LLM dans le chemin sync | OK | pas d’import `llm` dans `route()` ; `route_async` optionnel/non branché (F-007) |
| 2. `ollama_guard` allowlist runtime | OK | `OLLAMA_ALLOWED_MODULES` L18-23 ; resolve path exact L116-148 |
| 2b. Scan statique `offenders == []` | OK | `tests/test_cognitive_routing.py:150-162` |
| 2c. Router ne sélectionne jamais Ollama conversation | OK | backends answer/tool/cursor seulement |
| 3. Branche `jarvis/cursor/<job_id>` | OK | `cursor_delegation.py:121-122` |
| 3b. Worktree `.jarvis/worktrees/` | OK | `_worktree_root` L99-105 + `worktree add` L133 |
| 3c. Jamais travail sur main/master (live HEAD worktree) | OK | `PROTECTED_BRANCHES` L48, refus L411-421 |
| 4. `SELF_MODIFICATION_MODE` défaut `pr_only` | OK | `config.py:488` ; prompts self_repair/self_improvement |
| 4b. Self-mod JARVIS uniquement via Cursor worktree (nominal) | OK / PARTIEL | nominal OK ; trous F-002, F-009 (+ frontière self_healing AUTO_APPLY → P11) |
| 5. Reprise jobs au restart | OK | `resume_pending_jobs` L592-625 ; câblé lifespan (frontière P01/P03) |
| 5b. Jobs persistants SQLite | OK | `database/cursor_jobs.py` table + `VALID_STATUSES` + capacity lock |
| 6. Redaction secrets Cursor (prompt/API/DB update) | OK | enqueue + update + public view |
| 6b. Redaction PII sur croisement Cursor/DevAgent | KO / PARTIEL | F-003, F-004 |
| Briefings Main écran + Flash voix + dédup | OK | `briefing_engine.py:55-64,365-401` |
| `/loop` route cognitive → Cursor worktree | OK | `autonomous_loop.py:222-257` |
| DevAgent isole sous `DEV_PROJECTS_ROOT` (intention) | PARTIEL | `spec_builder.py:18-24` ; cassé par F-001 |
| Quality « report-only » sur JARVIS | PARTIEL | doublons OK ; fix/tests/hooks = F-009 |
| Tests requis Cursor sans shell | OK | `cursor_required_tests.py` allowlist + anti-`..` |
| Env Cursor minimal (pas `os.environ` entier) | OK | `cursor_env.py` |
| Templates `prompts/cursor/*` « jamais main » | OK | 18/19 match explicite ; `regression_review.md` équivalent |

## Frontières / dépendances
- Signale vers **P01/P03** (`api/lifespan.py`) : appel `cursor_delegation.resume_pending_jobs()` au startup — hors lecture ligne-à-ligne ici, contrat repris OK.
- Signale vers **P02** (`jarvis/security/redaction.py`, `jarvis/log_privacy.py`, `jarvis/pii/`) : redaction secrets vs PII — findings F-003/F-004 consomment ces modules.
- Signale vers **P05** : agents historiques / orchestrateur / `agents/devops.py` conversationnel (Main) sans passer par le routeur cognitif pour chaque tour interne.
- Signale vers **P08** : sécurité subprocess Cursor CLI déjà partiellement couverte ici ; P08 OWN `cursor_*.py` pour subprocess.
- Signale vers **P10** : usage Ollama dans `screen_watcher` / meal_analysis — ici uniquement allowlist `ollama_guard`.
- Signale vers **P11** (`scripts/self_healing.py`, `self_improvement.py`, `scheduler.py`) : si `SELF_REPAIR_ENABLED=false` et `SELF_HEALING_AUTO_APPLY=true`, patch local possible même avec `SELF_MODIFICATION_MODE=pr_only` (`self_healing.py:219-271`) — hors fichiers INCLUS, mais casse le contrat P12.
- Signale vers **P13** : `app/fitness/meal_analysis.py` dans allowlist Ollama.
- Attendus de P12 consommés ailleurs : `route_request` (chat/voix/loop), `cursor_delegation` (API + autonomy), `briefing_engine` (scheduler/API), `ollama_http_request` (client Ollama).

## Fichiers non lus
| Fichier | Motif |
|---|---|
| *(aucun fichier du périmètre non ouvert)* | — |
| `prompts/cursor/*.md` (corps hors invariants) | Invariants « main / pr_only / hors-périmètre » vérifiés sur les 19 ; pas chaque paragraphe pédagogique relu ligne à ligne (couverture partielle assumée dans le 94%). |

## Couverture
- Liste exhaustive des fichiers lus (chemins relatifs), triée.

```
agents/autonomous_loop.py
agents/briefing_engine.py
agents/devagent/__init__.py
agents/devagent/autorun.py
agents/devagent/coder.py
agents/devagent/executor.py
agents/devagent/git_ops.py
agents/devagent/interview.py
agents/devagent/loop.py
agents/devagent/models.py
agents/devagent/planner.py
agents/devagent/pr.py
agents/devagent/refactor.py
agents/devagent/spec_builder.py
agents/devagent/staging.py
agents/devagent/utils.py
agents/devops.py
api/router_cognitive.py
api/router_devagent.py
api/router_quality.py
database/cursor_jobs.py
database/devagent.py
database/devops.py
integrations/cursor_cli.py
integrations/cursor_delegation.py
integrations/cursor_env.py
integrations/cursor_prompt_composer.py
integrations/cursor_required_tests.py
jarvis/cognitive/__init__.py
jarvis/cognitive/capability_registry.py
jarvis/cognitive/context_planner.py
jarvis/cognitive/models.py
jarvis/cognitive/ollama_guard.py
jarvis/cognitive/router.py
prompts/cursor/android_feature.md
prompts/cursor/backend_feature.md
prompts/cursor/bug_fix.md
prompts/cursor/ci_repair.md
prompts/cursor/database_migration.md
prompts/cursor/documentation_sync.md
prompts/cursor/feature_implementation.md
prompts/cursor/frontend_feature.md
prompts/cursor/integration_validation.md
prompts/cursor/performance_audit.md
prompts/cursor/refactor_safe.md
prompts/cursor/regression_review.md
prompts/cursor/release_build.md
prompts/cursor/runtime_diagnosis.md
prompts/cursor/security_audit.md
prompts/cursor/self_improvement.md
prompts/cursor/self_repair.md
prompts/cursor/test_creation.md
prompts/cursor/voice_pipeline.md
```

<<<FIN_RAPPORT P12>>>

<<<RAPPORT P13 file=P13_module_fitness.md>>>

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
AUDIT MODULE MÉTIER
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ID_PERIMETRE: P13
NOM: Fitness
MODE: lecture seule, ligne par ligne
DATE: 2026-07-31
VERDICT: CONDITIONAL_PASS
SCORE_CHECKLIST: 2/5 OK · 3/5 KO
FINDINGS: 1 CRITIQUE · 4 MAJEURS · 5 MINEURS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

## 1. Identité

| Champ | Valeur |
|---|---|
| ID | P13 |
| Nom | Fitness |
| Montage | `main.py:54` import `app.fitness.routes.router` · `main.py:110` `include_router(fitness_router)` — import + montage seuls, OK |
| Surface HTTP | `APIRouter(prefix="/api/fitness")` — 18 opérations |
| Couches | models → routes → services → `database/fitness.py` · voice · meal_analysis |

## 2. Inventaire (inclus uniquement)

| Fichier | Lignes | Rôle |
|---|---|---|
| `app/fitness/__init__.py` | 1 | docstring |
| `app/fitness/models.py` | 446 | contrats Pydantic `strict=True` / `extra=forbid` |
| `app/fitness/routes.py` | 269 | HTTP |
| `app/fitness/services.py` | 516 | métier + advice LLM + repas IA |
| `app/fitness/meal_analysis.py` | 348 | vision Ollama + macros DeepSeek |
| `app/fitness/voice.py` | 537 | parser vocal déterministe |
| `database/fitness.py` | 724 | persistance SQLite |
| `web/.../FitnessView.tsx` | 620 | UI desktop live |
| `web/.../fitness/FitnessForms.tsx` | 383 | **dead code** |
| `web/.../fitness/FitnessSummary.tsx` | 110 | **dead code** |
| `web/.../fitness/types.ts` | 30 | types morts (Forms uniquement) |
| `frontend/src/lib/api.ts` L224–250 | — | client API fitness |
| `frontend/src/lib/device.ts` L9 | — | segment route `fitness` |
| Aucune page `frontend/src/**/Fitness*` | — | route via `[segment]` + vues `web/` |

Exclus non lus en profondeur (frontières seulement) : `web_mobile/js/views/health.js`, auth.

## 3. Checklist

| # | Item | Verdict | Preuve |
|---|---|---|---|
| 1 | Validation stricte vs UI `""` / `0` | **KO** | Backend strict OK ; UI desktop envoie `Number("") → 0` sur `reminder_interval_min` / `weekly_min_sessions` → 422. `reminder_time=""` échoue le pattern. |
| 2 | Sync `def` vs `async` / threadpool | **KO** | Routes sync `def` = threadpool FastAPI OK. Routes `async` (`/meals/from-text`, `/meals/from-photo`, `/advice`) appellent ensuite du SQLite sync (`create_meal`, `dashboard`) **sur la boucle event loop**. |
| 3 | `except Exception: pass` sur LLM | **KO** | `services.advice` L507–508 : avale toute erreur LLM sans log, repli silencieux. |
| 4 | Isolation schéma fitness | **KO** | Tables programme préfixées `fitness_*` ; logs cœur en noms globaux `meals` / `workouts` / `water_intake` / `wellbeing_logs` (collision / ownership floue). |
| 5 | Dead code FitnessForms vs FitnessView | **OK** | `FitnessForms` / `FitnessSummary` / `types.ts` : zéro import hors leurs fichiers. UI live = `FitnessView` seule. |

## 4. Findings

### CRITIQUE

**F-01 — Avalement total des erreurs LLM advice**  
`app/fitness/services.py:507-508`
```python
except Exception:
    pass
```
Toute panne DeepSeek / import / parse → fallback sans `logger`. Opacité ops, checklist #3.

### MAJEURS

**F-02 — UI réglages → `0` / `""` vs bornes Pydantic**  
`FitnessView.tsx:594-595` : `Number(event.target.value)` sur champ vidé → `0`.  
Contrats : `reminder_interval_min ge=30`, `weekly_min_sessions ge=1`, `reminder_time` pattern `HH:MM`.  
Effet : sauvegarde objectifs/rappels cassée dès clear champ (même bug miroir dans `health.js` hors périmètre — frontière).

**F-03 — Async + SQLite sync sur event loop**  
`routes.py:90-96`, `99-119`, `250-255` → `services.create_meal` / `dashboard` sync.  
Risque de blocage sous charge (photo/vision + write DB).

**F-04 — Isolation schéma partielle**  
`database/migrations.py:_migrate_fitness` : `fitness_programs|sessions|progress|weight_logs|prompt_log` OK ; `meals/workouts/water_intake/wellbeing_logs` non namespacés. Module propriétaire via migration, pas via préfixe.

**F-05 — Calculs progress incohérents**  
`weekly_done_count` compte `fitness_session_progress` **+** `workouts` legacy (`database/fitness.py:443-475`).  
`current_week_streak` ne compte **que** `fitness_session_progress` (`:478-499`).  
Dashboard peut afficher semaine OK et streak 0 (ou l’inverse selon legacy).

### MINEURS

**F-06 — Dead UI legacy** — `FitnessForms.tsx` + `FitnessSummary.tsx` + `types.ts` non montés ; double maintenance latente.  
**F-07 — Stockage photo avalé** — `services.py:249-251` `except Exception:` → `photo_path=None` sans log (analyse OK, photo perdue).  
**F-08 — `ProgramExercise.duration_sec`** — `int | ShortText | None` sans `ge` (`models.py:114`) : durée négative acceptée côté modèle.  
**F-09 — Timezone hardcodée** — `services.py:41` `ZoneInfo("Europe/Paris")` ignore `config.TIMEZONE`.  
**F-10 — Clear notes/description desktop** — `FitnessView` envoie `undefined` (omit) ; mobile envoie `null` (clear). Contrat PATCH asymétrique.

## 5. reminder_* (mission)

| Couche | État |
|---|---|
| `FitnessProgramUpdate.reminder_time` | pattern `^(?:[01]\d\|2[0-3]):[0-5]\d$` — OK |
| `reminder_interval_min` | `ge=30, le=720` — OK |
| SQLite `fitness_programs` | CHECK interval 30–720 ; `reminder_time` TEXT **sans** CHECK format |
| Consommateur | `scripts/fitness_reminders.py` (hors INCLUS) lit programme + `fitness_prompt_log` ; scheduler `to_thread` — frontière OK |
| UI | envoi `0`/`""` casse PATCH — F-02 |

## 6. Conseils LLM / progress / meal AI

| Flux | Comportement |
|---|---|
| `POST /advice` | snapshot dashboard → DeepSeek fast → `FitnessAdvice` ; fallback déterministe si vide/erreur — **erreur masquée** (F-01) |
| Progress | `PUT .../sessions/{id}/progress` upsert UNIQUE `(program_session_id, date)` ; voix `set_scheduled_session_status` sans routes HTTP dédiées |
| Meal text | `async` DeepSeek JSON → normalize → persist |
| Meal photo | Ollama vision → DeepSeek macros → upload `fitness/meals` ; vision `except` → 503 explicite (OK) ; upload générique silencieux (F-07) |

## 7. Frontières

| Frontière | Contrat | Note |
|---|---|---|
| **P15 mobile** `health.js` | Même API : `GET /dashboard`, `PUT /sessions/{id}/progress`, `POST /water|/meals|/weights|/advice`, `PATCH /program`, `PATCH /program/sessions/{id}` ; `api.js` expose aussi `from-text` / `from-photo` / workouts / wellbeing / summary | UI mobile = clone fonctionnel de FitnessView ; même piège `Number("")` settings |
| **P02 auth** | Routes sous `/api/*` → session middleware ; photos `Cache-Control: private, no-store` | Non audité ici |
| Voix | `api/voice_processing.py` → `maybe_handle_fitness_voice` ; endpoints virtuels `/sessions/today/complete\|skip` (pas de routes HTTP) | Hors UI |
| Rappels | `scripts/fitness_reminders.py` + job scheduler | Hors INCLUS, dépend des champs `reminder_*` |
| Frontend Next | Pas de composant fitness dédié ; `api.ts` + allowlist `device.ts` ; page `/fitness` via segment unifié + `web` views | Conforme consigne « uniquement fichiers fitness » |

## 8. Montage `main`

```54:54:main.py
from app.fitness.routes import router as fitness_router
```
```110:110:main.py
app.include_router(fitness_router)
```
Aucun autre couplage métier dans `main.py`. **OK.**

## 9. Synthèse checklist → actions

| Priorité | Action |
|---|---|
| P0 | Remplacer `except Exception: pass` advice par log + fallback explicite |
| P1 | UI : ne pas envoyer `0`/`""` ; omettre champs vides ou valider avant PATCH |
| P1 | Offloader writes SQLite hors loop dans handlers `async` (`asyncio.to_thread`) |
| P2 | Aligner streak sur la même règle que `weekly_done_count` (legacy inclus ou exclu partout) |
| P2 | Prefixe/namespace tables logs **ou** documenter ownership exclusive dans vérité d’archi |
| P3 | Supprimer ou brancher `FitnessForms`/`FitnessSummary` |

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
VERDICT FINAL
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CONDITIONAL_PASS — contrats Pydantic et montage sains ;
3 items checklist KO bloquants pour prod soignée
(advice silencieux, async/SQLite, UI ""/0, schéma partiel).
Dead code Forms confirmé. Frontière mobile = même contrat API.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

<<<FIN_RAPPORT P13>>>

<<<RAPPORT P14 file=P14_frontend_bureau_jarvis_auth.md>>>

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

<<<FIN_RAPPORT P14>>>

<<<FIN_RAPPORTS>>>
