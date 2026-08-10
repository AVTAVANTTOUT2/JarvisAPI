# Resource Guard JARVIS — Design

Date: 2026-08-06  
Statut: approved (approche 1, politique A)

## Objectif

Empêcher les crashes Mac (32 Go) causés par des process JARVIS qui s’accumulent : Ollama/`llama-server` résidents, sidecars TTS MLX orphelins/doublons, daemons audio/screen en double. Jamais tuer Codex, Claude Desktop, Chrome, Cursor.

## Décisions

1. **Watchdog dans le supervisor** + module pur `jarvis/resource_guard.py` (pas de LaunchAgent séparé).
2. **Politique A** : allowlist stricte de marqueurs sous `PROJECT_DIR` (+ `ollama serve` / runners Ollama gérés via `stop_ollama`).
3. **Orphelins/doublons** : nettoyés dès détection (bug de lifecycle).
4. **Ollama idle** : `stop_ollama()` seulement si screen watcher arrêté depuis ≥ TTL.
5. **Dry-run** et seuils via `.env`.

## Composants

| Fichier | Rôle |
|---|---|
| `jarvis/resource_guard.py` | Inventaire, classification, plan d’actions, exécution injectable |
| `config.py` / `.env.example` | Flags et seuils |
| `supervisor.py` | Tick périodique + `GET /api/supervisor/resources` |
| `tests/test_resource_guard.py` | Unitaire, zéro process réel tué |

## Niveaux mémoire

- `ok` : inventaire ; orphelins/doublons quand même nettoyés
- `warn` : free+purgeable < `WARN_FREE_MB` — log + API
- `critical` : free+purgeable < `CRITICAL_FREE_MB` — + stop Ollama idle si politique active

## Sécurité

- Aucun kill si la cmdline ne matche pas un marqueur JARVIS absolu (chemin dépôt) ou le contrat Ollama existant.
- Jamais de `pkill` large (`python`, `node`, etc.).
- `dry_run=true` journalise sans signal.
- Les PIDs dans l’arbre `_managed_pids()` du supervisor sont épargnés (sauf surplus TTS au-delà du cap, en gardant le/les enfants gérés).

## Hors scope

- Kill Codex / IDE / navigateurs
- Limites cgroup (non portables macOS)
- Unload partiel Ollama sans stop (keep_alive déjà 30s côté SW)
