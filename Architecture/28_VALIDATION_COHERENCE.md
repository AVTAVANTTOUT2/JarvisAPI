# 28 — État de vérité du projet

<!-- GENERATED FILE — DO NOT EDIT. -->
<!-- Source: Architecture/project_truth_registry.json + code runtime. -->
<!-- Regenerate: python tools/audit_architecture_truth.py --schema-output database/schema.sql --status-output Architecture/28_VALIDATION_COHERENCE.md -->

**Revue du registre** : 2026-08-27

Runtime SQLite canonique : **119 tables persistantes**, **124 tables physiques avec FTS5**, schéma généré : **126 déclarations de tables**.

Surface API canonique : **326 opérations**, **290 chemins**, **152 consommées et testées**, **68 consommées sans référence de test**, **53 non-frontend documentées et testées**, **53 non-frontend documentées sans référence de test**, **0 non attribuées**.

Structure API canonique : **323 opérations HTTP + 3 WebSockets**, **287 chemins OpenAPI**, **23 routeurs api/router_*.py + Fitness = 24 montés**, main.py **271 lignes**.

Les statuts décrivent ce qui est démontré sur `main`. `IMPLEMENTED_VERIFIED` signifie qu'une preuve de code et une preuve automatisée existent ; ce statut ne remplace pas une validation matérielle lorsqu'elle est explicitement demandée.

## Matrice de vérité

| Domaine | Portée | Statut | État et preuves |
|---|---|---|---|
| Backend / API | main | IMPLEMENTED_VERIFIED | Assemblage FastAPI modulaire et inventaire statique route-consommateur-test contrôlés en CI. Preuves : `main.py` (code), `tests/test_audit_architecture_truth.py` (test). |
| DB / migrations | main | IMPLEMENTED_VERIFIED | Le schéma frais est construit par schema.py puis migrations.py et devagent.py ; schema.sql est un miroir généré. Preuves : `database/core.py` (code), `tests/test_audit_architecture_truth.py` (test). |
| Mémoire | main | IMPLEMENTED_VERIFIED | La mémoire conversationnelle et l'historique personnel disposent d'une persistance et de contrats automatisés. Preuves : `agents/memory.py` (code), `tests/test_person_history.py` (test). |
| Agentique / task-control | main | PARTIAL | Le service, la persistance et l'approbation fail-closed existent côté backend et bureau canonique. Preuves : `jarvis/task_control/service.py` (code), `tests/test_task_control_service.py` (test). Écarts : La parité de contrôle n'est pas démontrée sur toutes les surfaces clientes. Le flux complet doit encore être validé avec un fournisseur réel et des tâches longues. |
| OpenCode | main | IMPLEMENTED_NEEDS_REAL_VALIDATION | L'adaptateur, le cycle de vie, le pont MCP et les garde-fous sont couverts hors processus réel. Preuves : `integrations/opencode/adapter.py` (code), `integrations/opencode/tests/test_adapter.py` (test). Validation restante : Exécuter integrations/opencode/tests/test_real_binary_e2e.py avec le binaire et un fournisseur réels. |
| Navigateur | main | PARTIAL | Le routage de capacité et des intégrations spécialisées existent, mais pas un adaptateur navigateur agentique générique exploitable. Preuves : `jarvis/agentic/profiles.py` (code), `tests/test_agentic_open_world.py` (test). Écarts : Aucun fournisseur navigateur générique n'est relié au runtime agentique de main. Les preuves d'exécution navigateur réelles et la reprise après interruption restent à établir. |
| Audio | main | IMPLEMENTED_NEEDS_REAL_VALIDATION | Les pipelines STT/TTS, la sortie audio et le daemon disposent de tests sans matériel. Preuves : `scripts/audio_daemon.py` (code), `tests/test_audio_pipeline_fixes.py` (test). Validation restante : Valider micro, haut-parleur, interruption et latence sur le Mac cible. |
| Enregistrements longs | main | PARTIAL | Le spool backend reprend, réconcilie et purge les sessions de façon testée. Preuves : `audio/recording_spool.py` (code), `tests/test_recording_spool.py` (test). Écarts : Aucune capture produit canonique ne démontre encore les scénarios 1, 30 et 180 minutes. Le parcours client de démarrage, reprise, progression et export reste à livrer. |
| Frontend | main | IMPLEMENTED_VERIFIED | frontend/ est l'unique bureau Next exporté ; web/src est sa bibliothèque et web_mobile reste autonome. Preuves : `core/frontend_resolution.py` (code), `tests/test_frontend_runtime_uniqueness.py` (test). |
| Android | main | IMPLEMENTED_NEEDS_REAL_VALIDATION | L'application native, les tests JVM/instrumentés et les garde-fous release sont versionnés. Preuves : `android/app/build.gradle` (code), `tests/test_ci_android_release.py` (test). Validation restante : Rejouer les tests instrumentés et les parcours réseau/audio sur un appareil physique cible. |
| macOS | main | IMPLEMENTED_NEEDS_REAL_VALIDATION | L'app SwiftUI, le widget, une cible de tests et la compilation Release CI sont définis. Preuves : `native_mac/project.yml` (code), `tests/test_ci_macos.py` (test). Validation restante : Valider signature, permissions, lancement, widget et services Apple sur le Mac cible. |
| Sécurité | main | PARTIAL | Auth, CSRF/origine et hygiène des artefacts publics disposent de contrôles automatisés ; la preuve globale egress, release et matériel reste ouverte. Preuves : `api/middleware.py` (code), `tests/test_security_middleware.py` (test), `tools/audit_architecture_truth.py` (code), `tests/test_audit_architecture_truth.py` (test). Écarts : La PR de sécurité #282 reste une livraison séparée tant qu'aucun SHA fusionné n'est prouvé sur main. Les frontières egress complètes, la signature de distribution et les parcours matériels ne sont pas attestés par les seuls tests middleware. |
| Observabilité | main | PARTIAL | Les sondes live/ready/detail et l'historique de métriques sont présents. Preuves : `api/health_support.py` (code), `tests/test_health_contract.py` (test). Écarts : L'export Prometheus, les alertes et les SLO opérationnels ne sont pas livrés. |
| Release | main | PARTIAL | Les builds CI, contrats de versionnement et checklists existent, sans preuve de livraison finale signée. Preuves : `.github/workflows/ci.yml` (ci), `tests/test_release_soak.py` (test). Écarts : RELEASE_CHECKLIST.md reste à exécuter et signer sur les artefacts candidats. Les essais prolongés et la validation matérielle finale ne sont pas attestés. |

## Périmètre documentaire

Chaque Markdown des racines gouvernées doit être classé. Dans les documents `current`, seuls les types déclarés dans `required_claims` portent des assertions numériques opposables. Les documents `historical` conservent une photographie datée ; les documents `superseded` renvoient vers leur remplacement.

### Current

- `README.md` — frontend
- `CLAUDE.md` — database, api_structure, frontend
- `STARTUP_PROTOCOL.md` — frontend
- `Architecture/INDEX.md` — database, api_surface, api_structure
- `Architecture/28_VALIDATION_COHERENCE.md` — database, api_surface, api_structure
- `Architecture/32_FRONTEND_DATABASE_SOURCE_OF_TRUTH.md` — database, api_surface, api_structure, frontend
- `Architecture/adr/ADR-017-sqlite-base-unique.md` — database
- `prompts/cursor/release_build.md` — frontend
- `prompts/cursor/frontend_feature.md` — frontend
- `Architecture/00_VISION.md` — aucun comptage
- `Architecture/04_ADR.md` — aucun comptage
- `Architecture/07_FEUILLE_DE_ROUTE.md` — aucun comptage
- `Architecture/08_ARCHITECTURE_CIBLE.md` — aucun comptage
- `Architecture/09_DATA_OWNERSHIP.md` — aucun comptage
- `Architecture/10_GOUVERNANCE_EVENTS.md` — aucun comptage
- `Architecture/11_QUEUE_ENGINE.md` — aucun comptage
- `Architecture/12_OBSERVABILITE.md` — aucun comptage
- `Architecture/15_SAUVEGARDES.md` — aucun comptage
- `Architecture/17_DEFINITION_OF_DONE.md` — aucun comptage
- `Architecture/18_GOUVERNANCE.md` — aucun comptage
- `Architecture/20_CONTRATS_INTERNES.md` — aucun comptage
- `Architecture/21_DEPENDENCY_RULES.md` — aucun comptage
- `Architecture/22_FITNESS_FUNCTIONS.md` — aucun comptage
- `Architecture/23_TECHNICAL_DEBT.md` — aucun comptage
- `Architecture/24_GOUVERNANCE_ADR.md` — aucun comptage
- `Architecture/25_REVUE_ARCHITECTURE.md` — aucun comptage
- `Architecture/30_PLAN_STABILISATION_AUDIO.md` — aucun comptage
- `Architecture/33_API_PUBLIQUE_OPENAPI.md` — aucun comptage
- `Architecture/34_SDK_DEVELOPPEURS.md` — aucun comptage
- `Architecture/36_CANAL_WEBSOCKET_TV.md` — aucun comptage
- `Architecture/BRIEFING_ENGINE.md` — aucun comptage
- `Architecture/CAPABILITY_REGISTRY.md` — aucun comptage
- `Architecture/COGNITIVE_ROUTING.md` — aucun comptage
- `Architecture/CURSOR_DELEGATION.md` — aucun comptage
- `Architecture/ENGINEERING_TEAM.md` — aucun comptage
- `Architecture/FITNESS_MODULE.md` — aucun comptage
- `Architecture/LLM_POLICY.md` — aucun comptage
- `Architecture/SELF_IMPROVEMENT.md` — aucun comptage
- `Architecture/SELF_REPAIR.md` — aucun comptage
- `Architecture/VOICE_PIPELINE.md` — aucun comptage
- `Architecture/adr/ADR-016-applescript-integration-apple.md` — aucun comptage
- `Architecture/adr/ADR-018-dual-llm-router.md` — aucun comptage
- `Architecture/adr/ADR-019-SUPERVISOR-FRONTEND-PRIORITY.md` — aucun comptage
- `Architecture/adr/ADR-020-android-offline-first-bearer.md` — aucun comptage
- `Architecture/adr/ADR-021-android-offline-location-batch.md` — aucun comptage
- `Architecture/adr/ADR-022-DATA-AT-REST.md` — aucun comptage
- `Architecture/adr/ADR-023-PROFILS-UTILISATEUR.md` — aucun comptage
- `Architecture/adr/ADR-024-SAUVEGARDE-CLOUD-CHIFFREE.md` — aucun comptage
- `Architecture/adr/ADR-025-CONTRAT-OPENAPI-PUBLIC.md` — aucun comptage
- `Architecture/adr/ADR-026-SDK-DEVELOPPEURS.md` — aucun comptage
- `Architecture/adr/ADR-027-claw3d-ui-optionnelle.md` — aucun comptage
- `Architecture/adr/ADR-028-politique-de-parole-vocale.md` — aucun comptage
- `Architecture/adr/ADR-029-apple-shortcuts-bridge.md` — aucun comptage
- `Architecture/adr/ADR-029-worktree-lifecycle.md` — aucun comptage
- `Architecture/adr/ADR-030-annulation-agentique.md` — aucun comptage
- `Architecture/adr/ADR-031-approbations-dynamiques.md` — aucun comptage
- `Architecture/adr/ADR-032-admission-concurrence.md` — aucun comptage
- `Architecture/adr/ADR-033-git-ownership.md` — aucun comptage
- `Architecture/adr/ADR-034-cycle-de-vie-des-taches-agentiques.md` — aucun comptage
- `Architecture/adr/ADR-036-apple-music-outil-jarvis.md` — aucun comptage
- `Architecture/adr/ADR-037-charte-majordome.md` — aucun comptage
- `Architecture/diagrams/README.md` — aucun comptage
- `.ai/workspaces/ws12/HANDOFF_TO_CURSOR.md` — aucun comptage
- `AGENTS.md` — aucun comptage
- `CHANGELOG_HISTORIQUE.md` — aucun comptage
- `FRONTEND_SPECS.md` — aucun comptage
- `RELEASE_CHECKLIST.md` — aucun comptage
- `SETUP_IMESSAGE_IMPORT.md` — aucun comptage
- `VOCAL_PIPELINE_ANALYSIS.md` — aucun comptage
- `android/README.md` — aucun comptage
- `android/docs/API_CONTRACTS_PRODUCTION.md` — aucun comptage
- `android/docs/CHAT.md` — aucun comptage
- `android/docs/FUTURE_FEATURES.md` — aucun comptage
- `android/docs/LOCATION.md` — aucun comptage
- `android/docs/OFFLINE_SYNC.md` — aucun comptage
- `android/docs/UI_AUDIT.md` — aucun comptage
- `android/docs/UI_DIRECTION.md` — aucun comptage
- `android/docs/VOICE.md` — aucun comptage
- `artifacts/JARVIS_BENCHMARK_FIXTURES.md` — aucun comptage
- `artifacts/JARVIS_BENCHMARK_PROMPTS.md` — aucun comptage
- `database/migrations/README.md` — aucun comptage
- `docs/APPLE_SHORTCUTS.md` — aucun comptage
- `docs/CLAW3D.md` — aucun comptage
- `docs/PYTHON_DEPENDENCIES.md` — aucun comptage
- `docs/VOICE_DISPLAY.md` — aucun comptage
- `docs/audio/CUSTOM_VOICE.md` — aucun comptage
- `docs/audio/LOCAL_TTS_ARCHITECTURE.md` — aucun comptage
- `docs/audio/QWEN3_LOCAL_STATUS.md` — aucun comptage
- `docs/audio/VOICE_REPLACEMENT.md` — aucun comptage
- `docs/audio/archive/FISH_M4_VALIDATION.md` — aucun comptage
- `docs/superpowers/plans/2026-07-16-android-offline-location.md` — aucun comptage
- `docs/superpowers/plans/2026-07-16-android-production-wave1.md` — aucun comptage
- `docs/superpowers/plans/2026-07-16-android-ui-redesign.md` — aucun comptage
- `docs/superpowers/plans/2026-08-06-resource-guard.md` — aucun comptage
- `docs/superpowers/plans/2026-08-19-apple-music-native.md` — aucun comptage
- `docs/superpowers/plans/2026-08-19-person-history-memory.md` — aucun comptage
- `docs/superpowers/plans/2026-08-20-majordome-launch.md` — aucun comptage
- `docs/superpowers/specs/2026-07-16-android-offline-location-design.md` — aucun comptage
- `docs/superpowers/specs/2026-07-16-android-production-wave1-design.md` — aucun comptage
- `docs/superpowers/specs/2026-08-06-resource-guard-design.md` — aucun comptage
- `docs/superpowers/specs/2026-08-19-person-history-memory-design.md` — aucun comptage
- `integrations/opencode/CHANGELOG.md` — aucun comptage
- `integrations/opencode/README.md` — aucun comptage
- `integrations/opencode/THIRD_PARTY_NOTICES.md` — aucun comptage
- `integrations/opencode/client/openapi/README.md` — aucun comptage
- `integrations/opencode/docs/AGENTS.md` — aucun comptage
- `integrations/opencode/docs/ARCHITECTURE.md` — aucun comptage
- `integrations/opencode/docs/CONFIGURATION.md` — aucun comptage
- `integrations/opencode/docs/INSTALLATION.md` — aucun comptage
- `integrations/opencode/docs/MCP.md` — aucun comptage
- `integrations/opencode/docs/MCP_BRIDGE.md` — aucun comptage
- `integrations/opencode/docs/OPERATIONS.md` — aucun comptage
- `integrations/opencode/docs/PIPELINE_AUDIT_2026-08-18.md` — aucun comptage
- `integrations/opencode/docs/README.md` — aucun comptage
- `integrations/opencode/docs/RECOVERY.md` — aucun comptage
- `integrations/opencode/docs/REMOVAL.md` — aucun comptage
- `integrations/opencode/docs/REMOVAL_PROOF.md` — aucun comptage
- `integrations/opencode/docs/SECURITY.md` — aucun comptage
- `integrations/opencode/docs/TEST_REPORT.md` — aucun comptage
- `integrations/opencode/docs/THREAT_MODEL.md` — aucun comptage
- `integrations/opencode/docs/UNPLUG_RUNBOOK.md` — aucun comptage
- `integrations/opencode/docs/UPGRADE.md` — aucun comptage
- `native_audio/README.md` — aucun comptage
- `native_mac/APPLICATION.md` — aucun comptage
- `native_mac/README.md` — aucun comptage
- `prompts/cursor/android_feature.md` — aucun comptage
- `prompts/cursor/backend_feature.md` — aucun comptage
- `prompts/cursor/bug_fix.md` — aucun comptage
- `prompts/cursor/ci_repair.md` — aucun comptage
- `prompts/cursor/database_migration.md` — aucun comptage
- `prompts/cursor/documentation_sync.md` — aucun comptage
- `prompts/cursor/feature_implementation.md` — aucun comptage
- `prompts/cursor/integration_validation.md` — aucun comptage
- `prompts/cursor/performance_audit.md` — aucun comptage
- `prompts/cursor/refactor_safe.md` — aucun comptage
- `prompts/cursor/regression_review.md` — aucun comptage
- `prompts/cursor/runtime_diagnosis.md` — aucun comptage
- `prompts/cursor/security_audit.md` — aucun comptage
- `prompts/cursor/self_improvement.md` — aucun comptage
- `prompts/cursor/self_repair.md` — aucun comptage
- `prompts/cursor/test_creation.md` — aucun comptage
- `prompts/cursor/voice_pipeline.md` — aucun comptage
- `prompts/engineering_team/claude-reviewer.md` — aucun comptage
- `prompts/engineering_team/codex-developer.md` — aucun comptage
- `prompts/engineering_team/codex-repair.md` — aucun comptage
- `prompts/engineering_team/codex-roadmap.md` — aucun comptage
- `sdk/python/README.md` — aucun comptage
- `tv/README.md` — aucun comptage
- `voices/README.md` — aucun comptage

### Historical

- `Architecture/01_CARTOGRAPHIE.md` — snapshot 2026-07-11
- `Architecture/03_AUDIT_TECHNIQUE.md` — snapshot 2026-07-11
- `Architecture/26_SCORE_SANTE.md` — snapshot 2026-07-11
- `Architecture/27_RAPPORT_PRET_REFACTORING.md` — snapshot 2026-07-11
- `Architecture/AUDIT_SECURITE_2026-08.md` — snapshot 2026-08
- `android/docs/PRODUCTION_GAP_ANALYSIS.md` — snapshot 2026-07-14
- `android/docs/validation/ANDROID_UI_REDESIGN_VALIDATION.md` — snapshot 2026-08-10
- `Architecture/02_ANALYSE_PROBLEMES.md` — snapshot 2026-07-11
- `Architecture/05_PLAN_MIGRATION.md` — snapshot 2026-07-11
- `Architecture/06_PLAN_TESTS.md` — snapshot 2026-07-14
- `Architecture/19_VALIDATION_FINALE.md` — snapshot 2026-07-14
- `Architecture/33_CANONICAL_FRONTEND_VALIDATION.md` — snapshot 2026-07-16
- `Architecture/34_RAPPORT_CAMPAGNE_FRONTEND_16-07-2026.md` — snapshot 2026-07-16
- `Architecture/35_CAHIER_DES_CHARGES_WEB_MOBILE.md` — snapshot 2026-07-30
- `Architecture/35_PROMPT_DESIGN_WEB_MOBILE.md` — snapshot 2026-07-30
- `Architecture/35_PROMPT_EXECUTION_WEB_MOBILE.md` — snapshot 2026-07-30
- `Architecture/audit/VOICE_DISPLAY_AUDIT_2026-08-29.md` — snapshot 2026-08-29
- `Architecture/audit/VOICE_DISPLAY_VALIDATION_2026-08-29.md` — snapshot 2026-08-29
- `Architecture/audit/CLAW3D_OPENCLAW_GAP_AUDIT_2026-08-11.md` — snapshot 2026-08-11
- `Architecture/audit/PROMPTS_AUDIT_LIGNE_PAR_LIGNE.md` — snapshot 2026-07-31
- `Architecture/audit/RAPPORT_PIRE_AUDIT.md` — snapshot 2026-07-31
- `Architecture/audit/README.md` — snapshot 2026-07-31
- `Architecture/audit/rapports_bruts/P01_bootstrap_config_assemblage.md` — snapshot 2026-07-31
- `Architecture/audit/rapports_bruts/P02_auth_securite_http.md` — snapshot 2026-07-31
- `Architecture/audit/rapports_bruts/P03_api_rest_routeurs.md` — snapshot 2026-07-31
- `Architecture/audit/rapports_bruts/P04_websocket_chat_voix_actions.md` — snapshot 2026-07-31
- `Architecture/audit/rapports_bruts/P05_agents_llm_prompts.md` — snapshot 2026-07-31
- `Architecture/audit/rapports_bruts/P06_database_migrations.md` — snapshot 2026-07-31
- `Architecture/audit/rapports_bruts/P07_event_bus_notifications.md` — snapshot 2026-07-31
- `Architecture/audit/rapports_bruts/P08_integrations_os_cloud.md` — snapshot 2026-07-31
- `Architecture/audit/rapports_bruts/P09_audio_stt_tts.md` — snapshot 2026-07-31
- `Architecture/audit/rapports_bruts/P10_daemon_multi_device_screen.md` — snapshot 2026-07-31
- `Architecture/audit/rapports_bruts/P11_workers_scheduler_qualite.md` — snapshot 2026-07-31
- `Architecture/audit/rapports_bruts/P12_cognitif_cursor_devagent.md` — snapshot 2026-07-31
- `Architecture/audit/rapports_bruts/P13_module_fitness.md` — snapshot 2026-07-31
- `Architecture/audit/rapports_bruts/P14_frontend_bureau_jarvis_auth.md` — snapshot 2026-07-31
- `Architecture/audit/rapports_bruts/P15_web_mobile.md` — snapshot 2026-07-31
- `Architecture/audit/rapports_bruts/P16_android_companion.md` — snapshot 2026-07-31
- `Architecture/audit/rapports_bruts/P17_tv_war_room_mcp.md` — snapshot 2026-07-31
- `Architecture/audit/rapports_bruts/P18_tests_ci_docs.md` — snapshot 2026-07-31
- `Architecture/audit/rapports_bruts/extras/EXTRA_MISC_5fddf477.md` — snapshot 2026-07-31
- `Architecture/audit/rapports_bruts/extras/EXTRA_P09_1c90db94.md` — snapshot 2026-07-31
- `Architecture/13_PLUGINS.md` — snapshot 2026-07-11
- `Architecture/14_AI_SERVICE.md` — snapshot 2026-07-11
- `Architecture/16_CONTRATS_API.md` — snapshot 2026-07-11

### Superseded

- `Architecture/29_JARVIS_ANDROID_H24.md` → `android/README.md`
- `android/docs/ARCHITECTURE.md` → `android/README.md`

## Contrôle

Le job CI recalcule les métriques depuis le code, valide le registre, refuse tout Markdown gouverné non classé, recherche les assertions typées contradictoires et les identifiants locaux dans les surfaces publiques, puis compare ce rendu, `database/schema.sql` et `artifacts/architecture_truth.json`.
