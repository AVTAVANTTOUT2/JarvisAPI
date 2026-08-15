# Apple Shortcuts — pont JARVIS

JARVIS pilote tes **raccourcis personnalisés** macOS/iOS (Shortcuts.app) et
accepte des recettes iPhone qui appellent le backend.

## Frontière AppleScript vs Raccourcis

| Domaine | Mécanisme | ADR |
|---|---|---|
| Mail, Calendar, Messages, Contacts | AppleScript (données) | ADR-016 |
| Raccourcis perso (HomeKit, Siri, Focus, Back Tap) | CLI `/usr/bin/shortcuts` | ADR-029 |

Les deux coexistent. JARVIS ne remplace pas AppleScript pour les apps système ;
il ajoute un pont **allowlisté** pour tes automatisations maison.

## Activer

```bash
# .env
APPLE_SHORTCUTS_ENABLED=true
APPLE_SHORTCUTS_INGEST_TOKEN=$(openssl rand -hex 24)
```

Redémarre le backend. Ouvre **`/shortcuts`** dans l’UI (ou) :

```bash
curl -s -b cookies.txt http://127.0.0.1:8081/api/apple/shortcuts/status | jq
```

## Enregistrer un raccourci existant (recommandé : UI)

1. Crée le raccourci dans l’app **Raccourcis** (ex. HomeKit « allume la chambre »).
2. Ouvre **`/shortcuts` → Installés**.
3. Clique **Enregistrer** (ou importe tous les non listés).
4. Définis un alias vocal (« chambre ») dans l’onglet Registre.
5. Dis à JARVIS : « lance le raccourci chambre » → confirme le plan.

### Via API

```bash
curl -s -b cookies.txt http://127.0.0.1:8081/api/apple/shortcuts/installed | jq
curl -s -b cookies.txt -H "X-CSRF-Token: $CSRF" \
  -H "Content-Type: application/json" \
  -d '{"name":"allume la chambre","alias":"chambre","risk":"medium"}' \
  http://127.0.0.1:8081/api/apple/shortcuts/registry
```

## Lancer depuis l’UI

Dans `/shortcuts` → Registre → **Lancer** → bandeau de confirmation → Confirmer.
Même garde-fou que le chat / la voix : plan opaque à usage unique.

## Recettes iOS / macOS

`GET /api/apple/shortcuts/recipes` (aussi onglet Recettes) décrit les assemblages :

| Recette | Effet |
|---|---|
| `jarvis_location` | GPS → `POST /api/location` (`LOCATION_API_TOKEN`) |
| `jarvis_ask` | Texte → réponse JARVIS (`APPLE_SHORTCUTS_INGEST_TOKEN`) |
| `jarvis_quick_task` | Crée une tâche sans conversation |
| `register_homekit_example` | Brancher un raccourci HomeKit sur la voix |
| `siri_phrase_homekit` | Phrase Siri + même nom dans le registre |
| `focus_mode_bridge` | Mode Concentration → signal à JARVIS |

## Sécurité

- Opt-in explicite.
- Allowlist SQLite : aucun nom inventé par le LLM.
- Confirmation humaine (plan opaque à usage unique) — toujours, même si le
  champ `requires_confirmation` est exposé en base.
- Abandon / remplacement d’une proposition chat → révocation du plan shortcut.
- Entrées écrites uniquement sous `data/apple_shortcuts_workspace/`.
- Ingest Bearer rate-limité, hors cookie de session.

Voir `Architecture/adr/ADR-029-apple-shortcuts-bridge.md`.
