# SELF_REPAIR

## Politique LLM (2026)

- **DeepSeek Flash** = interaction vocale rapide, triage, formulation courte
- **DeepSeek Main** = raisonnement lourd non technique, prompts Cursor, briefings complets
- **Cursor CLI** (`agent --print`) = exécution technique (worktree isolé, jamais main)
- **Ollama** = Screen Watcher uniquement (vision)

Voir aussi : `jarvis/cognitive/`, `integrations/cursor_delegation.py`, `prompts/cursor/`.

## Implémentation

Document généré dans le cadre de `feat/jarvis-cognitive-voice-cursor-autonomy`.
Les détails opérationnels vivent dans le code et les tests `tests/test_cognitive_routing.py`.
