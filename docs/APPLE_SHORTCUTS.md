# Apple Shortcuts — pont JARVIS

JARVIS pilote tes **raccourcis personnalisés** macOS/iOS (Shortcuts.app) et
accepte des recettes iPhone qui appellent le backend.

## Activer

```bash
# .env
APPLE_SHORTCUTS_ENABLED=true
APPLE_SHORTCUTS_INGEST_TOKEN=$(openssl rand -hex 24)
```

Redémarre le backend. Vérifie :

```bash
curl -s -b cookies.txt http://127.0.0.1:8081/api/apple/shortcuts/status | jq
```

## Enregistrer un raccourci existant

1. Crée le raccourci dans l'app **Raccourcis** (ex. HomeKit « allume la chambre »).
2. Liste les noms exacts :

```bash
curl -s -b cookies.txt http://127.0.0.1:8081/api/apple/shortcuts/installed | jq
```

3. Enregistre-le dans le registre allowlist :

```bash
curl -s -b cookies.txt -H "X-CSRF-Token: $CSRF" \
  -H "Content-Type: application/json" \
  -d '{"name":"allume la chambre","alias":"chambre","risk":"medium"}' \
  http://127.0.0.1:8081/api/apple/shortcuts/registry
```

4. Dis à JARVIS : « lance le raccourci chambre » → confirme le plan.

## Recettes iOS / macOS

`GET /api/apple/shortcuts/recipes` décrit les assemblages :

| Recette | Effet |
|---|---|
| `jarvis_location` | GPS → `POST /api/location` (`LOCATION_API_TOKEN`) |
| `jarvis_ask` | Texte → réponse JARVIS (`APPLE_SHORTCUTS_INGEST_TOKEN`) |
| `jarvis_quick_task` | Crée une tâche sans conversation |
| `register_homekit_example` | Brancher un raccourci HomeKit sur la voix |

## Sécurité

- Opt-in explicite.
- Allowlist SQLite : aucun nom inventé par le LLM.
- Confirmation humaine (plan opaque à usage unique).
- Entrées écrites uniquement sous `data/apple_shortcuts_workspace/`.
- Ingest Bearer rate-limité, hors cookie de session.

Voir `Architecture/adr/ADR-029-apple-shortcuts-bridge.md`.
