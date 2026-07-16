# Politique LLM JARVIS

Dernière mise à jour : 2026-07-16

## Rôle

Définir clairement quel moteur exécute quoi. L’utilisateur voit une seule entité JARVIS ; en interne, quatre backends sont cloisonnés.

| Moteur | Usage autorisé | Usage interdit |
|--------|----------------|----------------|
| DeepSeek Flash (`VOICE_REASONING_MODEL` / `DEEPSEEK_FAST_MODEL`) | Voix, triage, formulation courte, outils instantanés | Raisonnement long non technique |
| DeepSeek Main (`MAIN_REASONING_MODEL` / `DEEPSEEK_MAIN_MODEL`) | Stratégie lourde, prompts Cursor, briefings complets, coaching | Exécution de code sur le dépôt |
| Cursor CLI (`agent --print`) | Travail technique en worktree isolé | Conversation générale, météo, contacts |
| Ollama | Screen Watcher (vision) + contrôle process Ollama | Conversation, triage daemon, chat, voix |

## Fichiers clés

- `jarvis/cognitive/ollama_guard.py` — allowlist runtime + scan statique
- `integrations/ollama_client.py` — client HTTP qui passe par le garde-fou
- `jarvis/cognitive/router.py` — choix Flash / Main / Cursor / outil
- `config.py` — `VOICE_REASONING_MODEL`, `MAIN_REASONING_MODEL`, `OLLAMA_REASONING_ENABLED`, `TRIAGE_MODEL`, `SCREEN_VISION_MODEL`
- `scripts/screen_watcher.py` — seul consommateur vision Ollama
- `integrations/ollama_control.py` — contrôle process (start/stop/status)

## Garde-fou Ollama

Toute requête HTTP vers l’API Ollama doit passer par `ollama_http_request()` :

1. Les frames de plomberie (`ollama_guard`, `ollama_client`) sont **sautés**.
2. Le premier frame **applicatif** du dépôt doit appartenir à l’allowlist :
   - `scripts/screen_watcher.py`
   - `integrations/ollama_control.py`
3. Sinon → `OllamaPolicyError`.

`OLLAMA_REASONING_ENABLED=true` n’ouvre **pas** la conversation à Ollama ; il ne concerne que d’éventuels chemins expérimentaux documentés. Le triage daemon (`scripts/jarvis_daemon._local_triage`) utilise Ollama texte (`TRIAGE_MODEL`) pour filtrer les notifications iMessage/Mail — hors chemin conversationnel.

Le scan statique (`scan_ollama_violations`) refuse les imports directs hors allowlist. Le test `tests/test_cognitive_routing.py` exige `offenders == []`.

## Mapping modèles (config)

```bash
DEEPSEEK_FAST_MODEL=deepseek-v4-flash
DEEPSEEK_MAIN_MODEL=deepseek-v4-pro
VOICE_REASONING_MODEL=          # défaut = FAST
MAIN_REASONING_MODEL=           # défaut = MAIN
OLLAMA_URL=http://localhost:11434
SCREEN_VISION_MODEL=qwen2.5-vl:7b
TRIAGE_MODEL=qwen2.5:7b
OLLAMA_REASONING_ENABLED=false
```

## Endpoint de lecture

`GET /api/cognitive/llm-policy` expose la politique effective (modèles Flash/Main, flags Cursor/Ollama) pour l’UI Cognitive.

## Limites connues

- Le triage daemon iMessage/Mail reste sur Ollama texte (hors conversation utilisateur).
- Les agents historiques (`agents/*.py`) appellent encore DeepSeek via `llm.py` ; le routeur cognitif s’intercale avant pour les entrées chat/voix branchées.
- Aucune bascule silencieuse Flash → Ollama en cas d’erreur API.
