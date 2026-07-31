<!--
source_agent: bc-019fb866-b5a9-7c88-a270-32f88b2789dd
agent_name: Agents LLM prompts
agent_url: https://cursor.com/agents/bc-019fb866-b5a9-7c88-a270-32f88b2789dd
agent_status: IDLE
created_at: 2026-07-31T13:39:27.081000+00:00
extracted_msg_index: 201
extracted_at: 2026-07-31T14:37:19.332627+00:00
-->

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