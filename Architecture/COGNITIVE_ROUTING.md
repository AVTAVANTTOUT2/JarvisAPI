# Routage cognitif

Dernière mise à jour : 2026-08-10

## Rôle

Classer chaque entrée utilisateur (chat, voix, Android, iMessage, `/loop`) en une `TaskIntent` déterministe avant tout LLM lourd, puis dispatcher vers Flash, Main, Cursor ou un outil.

## Fichiers clés

| Fichier | Responsabilité |
|---------|----------------|
| `jarvis/cognitive/router.py` | Règles regex + construction `TaskIntent` |
| `jarvis/cognitive/models.py` | Dataclass `TaskIntent` |
| `jarvis/cognitive/context_planner.py` | Budget caractères + sections contexte |
| `jarvis/cognitive/capability_registry.py` | Capacités disponibles / risque |
| `api/chat_cognitive.py` | Préambule chat : routage, proposition Cursor, confirmation « lance » |
| `api/chat_processing.py` | Moteur canonique REST (journal, contacts…) — appelle le préambule cognitif |
| `api/ws_messages.py` | Pipeline WebSocket texte/voix — même préambule cognitif |
| `api/voice_cognitive.py` | Raccourcis voix (briefing, Cursor ack, heavy follow-up, confirmation « lance ») |
| `api/ws_action_messages.py` | Confirmations structurées WS (`action_confirm` / `action_cancel` via `proposal_id`) |
| `api/action_confirmations.py` | Propositions serveur (shell, food, terminal…) et phrases de confirmation texte |
| `api/chat_context.py` | Injecte `__routing` / `__context_trace` |
| `api/router_cognitive.py` | API REST d'introspection |

Les modules `api/chat_cognitive.py`, `api/chat_processing.py` et `api/ws_messages.py` restent chacun sous 500 lignes (contrat Phase 4) : la logique Cursor partagée vit dans `chat_cognitive.py`, les transports l'importent.

## Flux

```
texte utilisateur
  → route_request(text, interaction_mode)
  → TaskIntent { domain, complexity, execution_type, reasoning_model, ... }
  → should_run_cursor_cognitive_path(...) ?
       ├ oui → maybe_delegate_chat_to_cursor(...)
       │         ├ phrase de confirmation + job pending → confirm + ack
       │         └ nouvelle tâche technique → enqueue (auto_start=false) + ack
       └ non → tool / answer Flash / answer Main (orchestrateur classique)
```

### Ordre des règles (déterministe)

1. Signaux techniques **forts** → `execution_type=cursor`, domaine `dev`
2. Explications techniques (« explique », « c'est quoi ») → `answer` / `dev_explain` (pas Cursor)
3. Verbe d'action + nom technique faible → Cursor
4. Raisonnement lourd non technique → Main (`strategy`, etc.)
5. Briefing → domaine `briefing`
6. Contacts / outils (météo avant agenda) → `tool`
7. Défaut → Flash conversation

### Mode vocal

- `reasoning_model` reste Flash pour la réponse parlée.
- Cursor / heavy : ack court immédiat ; travail en arrière-plan (job Cursor ou passe Main + résumé Flash).
- La confirmation « lance » / « vas-y » est gérée dans `api/voice_cognitive.py` avec la même priorité que le chat (proposition shell/food/terminal avant job Cursor).

## Délégation Cursor — proposition et confirmation

Les jobs Cursor ne démarrent **jamais** sans confirmation explicite (`auto_start=false`, `require_confirmation=true` dans `integrations/cursor_delegation.py`).

### Proposition (nouvelle tâche technique)

1. `route_request` classe la demande en `execution_type=cursor`.
2. `maybe_delegate_chat_to_cursor` appelle `cursor_delegation.enqueue(...)` avec statut `awaiting_confirmation` ou `proposal`.
3. JARVIS répond avec un ack (chat : lien vers l'onglet Délégations ; voix : « Dites lance pour démarrer »).

### Confirmation texte (« lance », « vas-y », …)

Les phrases courtes de confirmation **ne sont pas** classées `cursor` par le routeur — elles retombent en `execution_type=answer`. Le pipeline chat les traite quand même via `should_run_cursor_cognitive_path` :

| Condition | Emprunte le chemin Cursor |
|-----------|---------------------------|
| `execution_type == "cursor"` | Oui (nouvelle tâche) |
| Phrase de confirmation (`is_cursor_confirmation_phrase`) **et** aucune proposition serveur en attente pour la session | Oui → `maybe_confirm_pending_cursor` |
| Phrase de confirmation **mais** `peek_pending_proposal` retourne shell / food / terminal | Non — la proposition action prime (parité voix) |

Phrases reconnues côté Cursor (`api/chat_cognitive.py`, regex `_CONFIRM_RE`) : `lance`, `vas-y`, `confirme`, `go`, `ok lance`, `démarre` / `demarre` (seules, avec ponctuation optionnelle).

`maybe_confirm_pending_cursor` liste les jobs `awaiting_confirmation` / `proposal`, filtre par `interaction_mode` (`chat`, `voice`, `android`), prend le plus récent, puis appelle `cursor_delegation.confirm(job_id)`.

### Confirmation structurée (UI)

Les actions sensibles (terminal, calendrier, Uber Eats…) passent par `api/action_confirmations.py` :

- le serveur stocke une proposition opaque liée à `(session_id, conversation_id)` ;
- le client envoie `{"type":"action_confirm","proposal_id":"<opaque>"}` — traité par `api/ws_action_messages.handle_ws_action_decision` ;
- les phrases texte (`oui`, `vas-y`, `lance`, …) sont gérées séparément dans `api/chat_processing.py` / `api/ws_messages.py` via `is_imperative_confirmation` et `_pop_pending_action_if_confirmed`.

Les deux mécanismes coexistent : une proposition shell en attente **bloque** le chemin Cursor sur « lance ».

### Points d'entrée

| Transport | Fichier | Fonction |
|-----------|---------|----------|
| WebSocket chat/voix | `api/ws_messages.py` | `_process_message` |
| REST (journal, contacts, …) | `api/chat_processing.py` | `_process_message_internal` |
| Voix fast-path | `api/voice_cognitive.py` | `maybe_handle_cognitive_voice` |
| WS action UI | `api/ws_action_messages.py` | `handle_ws_action_decision` |

Tests de régression : `tests/test_chat_cognitive_cursor_confirm.py`, `tests/test_action_confirmation_boundary.py`.

## ContextPlanner

Branché dans `_build_enriched_context` (`api/chat_context.py`) :

- lit l'intent (`__routing`)
- sélectionne les sections (mails, agenda, tâches, localisation…)
- applique un budget caractères
- stocke `__context_trace` pour debug UI

## Config

```bash
CURSOR_DELEGATION_ENABLED=true
VOICE_REASONING_MODEL=
MAIN_REASONING_MODEL=
```

Si Cursor est désactivé ou CLI indisponible, le routeur renvoie un fallback honnête (pas de simulation de job).

## Endpoints

| Route | Rôle |
|-------|------|
| `POST /api/cognitive/route` | Classifie un texte (debug / UI) |
| `GET /api/cognitive/capabilities` | Registre des capacités |
| `GET /api/cognitive/llm-policy` | Politique LLM effective |

## Limites connues

- Classification 100 % regex : pas de LLM de triage pour le routeur cognitif.
- iMessage bridge historique peut encore passer par l'orchestrateur classique selon le chemin ; le hook cognitif est garanti sur chat WS/REST et voix branchée.
- Domaines `tv` / `system` dépendent des flags d'intégration existants.
- `maybe_confirm_pending_cursor` sélectionne le job Cursor le plus récent du mode courant ; plusieurs jobs en attente simultanés ne sont pas exposés individuellement par phrase texte.
