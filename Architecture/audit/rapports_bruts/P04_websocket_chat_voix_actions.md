<!--
source_agent: bc-019fb866-6788-7f50-a4c9-702e84c2357b
agent_name: Pipeline unifié chat voix
agent_url: https://cursor.com/agents/bc-019fb866-6788-7f50-a4c9-702e84c2357b
agent_status: IDLE
created_at: 2026-07-31T13:39:07.061000+00:00
extracted_msg_index: 142
extracted_at: 2026-07-31T14:37:19.332564+00:00
-->

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