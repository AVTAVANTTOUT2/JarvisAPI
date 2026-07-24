# Registre des capacités

Dernière mise à jour : 2026-07-16

## Rôle

Catalogue unique des capacités exposées au routeur et à l’UI : disponibilité, risque, confirmation requise, exécuteur (`jarvis_tool` | `cursor` | `deepseek` | `system`), et `action_type` aligné sur `actions.execute_action`.

## Fichiers clés

| Fichier | Rôle |
|---------|------|
| `jarvis/cognitive/capability_registry.py` | `Capability`, `CapabilityRegistry`, singleton |
| `actions.py` | Exécution réelle des `action_type` |
| `api/router_cognitive.py` | `GET /api/cognitive/capabilities` |
| `web/.../CognitiveView.tsx` | Affichage UI |

## Modèle

```python
@dataclass(frozen=True)
class Capability:
    name: str
    available: bool
    risk: str                    # low | medium | high
    requires_confirmation: bool
    executor: Executor
    description: str
    dependencies: tuple[str, ...] = ()
    action_type: str | None = None
```

## Exemples enregistrés

| Nom | Exécuteur | Confirmation | Notes |
|-----|-----------|--------------|-------|
| `calendar.create` | jarvis_tool | oui | Calendar.app |
| `calendar.read` | jarvis_tool | non | |
| `mail.read` / `mail.send` | jarvis_tool | send = oui | |
| `tasks.create` | jarvis_tool | non | |
| `contacts.resolve` | jarvis_tool | non | |
| `computer.shell` | system | oui, toujours | Plan opaque one-shot, allowlist, `LLM_SHELL_WORKSPACE` |
| `code.execute` | system | non via terminal | Wrapper legacy ; tâches complexes routées vers Cursor |
| `cursor.delegate` | cursor | selon risque | CLI cache uniquement au refresh chaud |
| `briefing.generate` | deepseek | non | BriefingEngine |
| `voice.fast_answer` | deepseek | non | Flash |

La liste exacte est construite dans `CapabilityRegistry.refresh()`.

## Disponibilité Cursor

Sur le chemin chaud du routage, `refresh()` **ne lance pas** de subprocess CLI. Il lit uniquement le cache `cursor_delegation._cli_info` (rempli au premier enqueue ou via `GET /api/cursor/status`).

## Endpoint

`GET /api/cognitive/capabilities` → liste sérialisée `to_dict()`.

## Alignement actions.py

Chaque capacité outil porte un `action_type` correspondant aux types acceptés par `execute_action` (ex. `calendar_create`, `task`, `mail`). Les capacités Cursor / DeepSeek n’ont pas forcément d’`action_type` — elles passent par le routeur / délégation.

## Limites connues

- `available=True` pour Mail/Calendar signifie « intégration configurée dans le code », pas un health-check AppleScript à chaque refresh.
- Les nouvelles actions ajoutées à `actions.py` doivent être enregistrées ici pour apparaître dans l’UI Cognitive.
- Pas de versioning sémantique des capacités — le nom string est la clé stable.
