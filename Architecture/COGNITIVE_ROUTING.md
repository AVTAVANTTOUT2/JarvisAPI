# Routage cognitif

Dernière mise à jour : 2026-07-16

## Rôle

Classer chaque entrée utilisateur (chat, voix, Android, iMessage, `/loop`) en une `TaskIntent` déterministe avant tout LLM lourd, puis dispatcher vers Flash, Main, Cursor ou un outil.

## Fichiers clés

| Fichier | Responsabilité |
|---------|----------------|
| `jarvis/cognitive/router.py` | Règles regex + construction `TaskIntent` |
| `jarvis/cognitive/models.py` | Dataclass `TaskIntent` |
| `jarvis/cognitive/context_planner.py` | Budget caractères + sections contexte |
| `jarvis/cognitive/capability_registry.py` | Capacités disponibles / risque |
| `api/chat_cognitive.py` | Hook chat WS + REST (event + ack + enqueue Cursor) |
| `api/voice_cognitive.py` | Raccourcis voix (briefing, Cursor ack, heavy follow-up) |
| `api/chat_context.py` | Injecte `__routing` / `__context_trace` |
| `api/router_cognitive.py` | API REST d’introspection |

## Flux

```
texte utilisateur
  → route_request(text, interaction_mode)
  → TaskIntent { domain, complexity, execution_type, reasoning_model, ... }
  → si cursor + CURSOR_DELEGATION_ENABLED + CLI OK → enqueue job
  → sinon tool / answer Flash / answer Main
```

### Ordre des règles (déterministe)

1. Signaux techniques **forts** → `execution_type=cursor`, domaine `dev`
2. Explications techniques (« explique », « c’est quoi ») → `answer` / `dev_explain` (pas Cursor)
3. Verbe d’action + nom technique faible → Cursor
4. Raisonnement lourd non technique → Main (`strategy`, etc.)
5. Briefing → domaine `briefing`
6. Contacts / outils (météo avant agenda) → `tool`
7. Défaut → Flash conversation

### Mode vocal

- `reasoning_model` reste Flash pour la réponse parlée.
- Cursor / heavy : ack court immédiat ; travail en arrière-plan (job Cursor ou passe Main + résumé Flash).

## ContextPlanner

Branché dans `_build_enriched_context` (`api/chat_context.py`) :

- lit l’intent (`__routing`)
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
- iMessage bridge historique peut encore passer par l’orchestrateur classique selon le chemin ; le hook cognitif est garanti sur chat WS/REST et voix branchée.
- Domaines `tv` / `system` dépendent des flags d’intégration existants.
