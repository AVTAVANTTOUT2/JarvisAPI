# Apple Music natif — hors runtime agentique

> **For agentic workers:** Implement task-by-task. Steps use checkbox syntax.

**Goal:** « met du werenoi » joue Werenoi dans Music.app au tour suivant, sans tâche, sans plan, sans runtime agentique. Le Control Center (`/control`) affiche si le MCP est up ou down.

**Symptom today:** `musique` / `apple music` / `playlist` sont des effets externes (`jarvis/agentic/classifier.py`). `maybe_start_agentic_run` crée un plan (`AGENTIC_REQUIRE_PLAN_APPROVAL=true`). Le MCP n’est monté que dans le runtime agentique après `media:publish`. Le chat DeepSeek n’a aucune action `music_*`. « execute » n’approuve pas le plan. « met du werenoi » sans mot-clé tombe sur une recherche de conversation.

**Architecture:** même patron que Mail / Calendar / météo — outil JARVIS local, pas une mission. Un module `integrations/apple_music.py` parle au binaire déjà installé `apple-music-mcp` (v0.1.0, stdio). Le runtime agentique continue de le monter pour les vrais workflows média (ffmpeg, transcode) ; le playback simple n’y passe plus.

**Tech stack:** Python 3.12, FastAPI existant, `actions.execute_action`, Control Center `/api/control/services`, pytest. Aucune nouvelle dépendance. Aucune route HTTP nouvelle si possible (évite le bump OpenAPI).

---

## Décisions

1. **Outil direct, pas agentique.** Retirer `apple music`, `musique`, `playlist` de `_EXTERNAL_EFFECT_TERMS`. Les garder comme *hints* du profil `media` uniquement pour un workflow multi-étapes (transcode, ffmpeg, « puis »). « Joue Werenoi sur Apple Music » devient `DIRECT_ACTION` et n’entre plus dans `maybe_start_agentic_run`.
2. **Un client, deux consommateurs.** Extraire `_apple_music_mcp_path()` de l’adapter du runtime d’exécution média vers `integrations/apple_music.py`. Le runtime agentique importe le même résolveur. Pas de second binaire, pas de copie AppleScript parallèle (ADR-016 : on ne réécrit pas Music.app ; on consomme le MCP déjà là).
3. **Pas de daemon.** `apple-music-mcp serve` est stdio à la demande. Control Center : `can_control: false` (comme l’ingestion launchd). `running` / `healthy` = binaire présent + `doctor` OK. Pas de start/stop.
4. **Sonde doctor, pas tools/list.** `apple-music-mcp doctor` (~600 ms, non destructif) + `capabilities`. Cache processus ~10 s pour ne pas spammer Apple Events à chaque poll Control / Health.
5. **Confirmation : playback non, mutation playlist oui.** play / pause / next / previous / volume / shuffle = immédiat. create/delete/rename playlist = confirmation comme `run_shortcut`. Pas de paiement, pas de réseau tiers.
6. **Catalogue Apple Music indisponible en v0.1.0.** `catalog_search` est ❌. La recherche est **bibliothèque locale**. Si Werenoi n’est pas dans la lib, JARVIS le dit et s’arrête — il ne bascule pas sur une tâche agentique ni sur Shortcuts.
7. **Intercept avant le classifieur agentique.** Fast-path dans `api/chat_processing.py` *avant* `maybe_start_agentic_run`, même pipeline voix / WS / REST. Sinon le mot `musique` (aujourd’hui) ou le LLM (demain) reprend la main.
8. **Santé : non critique.** Composant `apple_music` dans `jarvis/health.py`, jamais dans `CRITICAL_COMPONENTS`. Absent sur Linux CI = `unknown` / `optional_runtime_absent`, pas `unavailable` global. Aucun chemin fichier dans les détails publics (`doctor` affiche `/System/Applications/Music.app` — filtrer).
9. **Cursor MCP ≠ JARVIS.** Le serveur `user-apple-music` de l’IDE reste hors sujet. Seul le binaire `~/.local/bin/apple-music-mcp` compte pour le produit.

---

## Flux cible

```
« met du werenoi » / « joue werenoi » / « mets de la musique de werenoi »
        │
        ▼
maybe_handle_music_intent()     # déterministe, avant agentic
        │
        ├─ match play-verb + requête  → music_search(library) → play_artist|play_track
        ├─ match pause/next/stop/volume → music_playback / music_preferences
        └─ pas un intent musique      → pipeline actuel (agentic éventuellement)
```

Réponse type : « Werenoi — [titre], lecture lancée. » Trois phrases max, pas d’emoji, pas « Monsieur » (pas un greeting).

---

## Intent déterministe (pièges)

Positifs : `joue X`, `play X`, `mets du X`, `met du X`, `mets de la musique de X`, `lance X sur apple music`, `pause`, `suivant`, `monte le son`.

Négatifs (ne pas voler) :

- `lance` seul, `lance la tâche`, `execute`, `approuve` → contrôle agentique
- `lance les tests`, `lance la commande git` → devops
- `met du sel`, `mets la table` → pas musique (pas de hit bibliothèque → fallthrough, **ne pas inventer**)
- `playlist` dans « transcode la playlist puis publie » → reste agentique via `puis` / workflow, pas via le mot `playlist` tout seul

Si la recherche bibliothèque est vide : message d’échec, `ok: false`, **return** (ne pas `None` qui laisserait l’orchestrateur chercher une conversation titrée « Met du werenoi »).

---

## Contrats d’action (persona + `execute_action`)

Un seul type `music`, discriminé par `action` — comme `tv` / `run_shortcut`, pas dix types.

```json
{"type":"music","action":"play","query":"werenoi"}
{"type":"music","action":"pause"}
{"type":"music","action":"next"}
{"type":"music","action":"previous"}
{"type":"music","action":"stop"}
{"type":"music","action":"state"}
{"type":"music","action":"set_volume","volume":40}
```

Follow-up LLM : uniquement `state` (lire le titre). Play/pause n’ont pas besoin d’une 2e passe.

---

## Control Center + santé

`api/service_control.py` — carte sous-service, catégorie `integrations` :

| Champ | Valeur |
|---|---|
| `id` | `apple_music` |
| `name` | Apple Music MCP |
| `running` | `doctor` OK |
| `healthy` | idem |
| `can_control` | `false` |
| `state` | `healthy` / `degraded` / `unavailable` / `unknown` |
| `error` | code fermé (`binary_missing`, `doctor_failed`, `automation_denied`) — jamais un chemin |

`GET /api/integrations` : clé `apple_music` (miroir du status, comme `apple_shortcuts`).

`jarvis/health.py` : sonde optionnelle, `PUBLIC_REASONS` + `optional_runtime_absent` déjà là. Détails allowlistés : `engine=musicapp`, `offline=true`. Pas de `Path`.

`ControlView.tsx` : icône (Music / Disc), pas de boutons start/stop si `can_control === false` (déjà le cas). Label catégorie INTEGRATIONS déjà présent.

`HealthView.tsx` : label + icône pour `apple_music`.

---

### Task 1: Façade `integrations/apple_music.py` + sonde Control Center

- [x] Module : `resolve_binary()`, `status()` via `doctor`/`capabilities` (timeout 2 s, cache 10 s), `call_tool(name, arguments)` session stdio MCP courte (initialize + tools/call + close).
- [x] Déplacer `_apple_music_mcp_path` hors de l’adapter du runtime d’exécution ; l’adapter appelle `resolve_binary()`.
- [x] Ajouter la carte dans `_get_all_services_status()` ; `get_service_detail("apple_music")` renvoie le status sans chemin.
- [x] Brancher `api/misc_integrations.py` → `apple_music`.
- [x] `ControlView` : icône `apple_music`. `HealthView` seulement si Task 3 est faite en même temps, sinon attendre Task 3.
- [x] Tests : binaire absent → `running=false`, `error=binary_missing` ; doctor mocké OK → `running=true` ; aucun path dans le JSON. Adapter du runtime : test existant `test_media_profile_mounts_installed_apple_music_mcp` inchangé en comportement.
- [x] Vérifier : `python -m pytest` sur les tests d’adapter du runtime d’exécution + `tests/test_control_plane_auth.py -q` + un nouveau `tests/test_apple_music.py`.

### Task 2: Sortir le playback du classifieur agentique

- [x] Retirer `apple music`, `musique`, `playlist` de `_EXTERNAL_EFFECT_TERMS`.
- [x] Inverser les asserts : `classify_agentic_request("Joue Werenoi sur Apple Music")` → `DIRECT_ACTION`. Idem `test_agentic_profiles.py` (ce texte ne sélectionne plus le profil `media`).
- [x] Garder le hint `media` pour ffmpeg / transcode / « puis ».
- [x] Test : `maybe_start_agentic_run("mets de la musique de werenoi…")` retourne `None` (pas de plan). Monkeypatch runtime disabled inutile si la catégorie n’est plus déléguée.
- [x] Vérifier : `python -m pytest tests/test_agentic_domain.py tests/test_agentic_profiles.py tests/test_agentic_api.py -q`

### Task 3: Fast-path chat + action + persona + contexte

- [x] `maybe_handle_music_intent(text)` appelé dans `api/chat_processing.py` **avant** `maybe_start_agentic_run`.
- [x] `actions.py` : `type=music` → façade. Allowlist d’arguments. `ACTIONS_WITH_FOLLOWUP` : seulement `state`.
- [x] `jarvis/cognitive/capability_registry.py` : `music.play` / `music.control`, `executor=jarvis_tool`, `available` = binaire présent (cache, pas de subprocess dans `refresh()` — même règle que Cursor).
- [x] `jarvis/cognitive/router.py` : motif musique → `domain=media`, `execution_type=tool` (pas `agentic`).
- [x] `prompts/persona.txt` : action MUSIC. Règle : un verbe de lecture n’est **jamais** `search_conversations`.
- [x] Contexte : injecter l’état lecture **seulement** si intent musique ou mot-clé (pas +600 ms sur chaque tour). Champs : piste, artiste, état, volume — pas de chemin.
- [x] Tests : « met du werenoi » → `play` + query `werenoi`, zéro `task_control`. « execute » ne change pas. Recherche vide → refus déterministe, pas d’orchestrateur. Linux sans binaire → message d’indisponibilité, pas d’exception.
- [x] Vérifier : `python -m pytest tests/test_apple_music.py tests/test_cognitive_routing.py tests/test_agentic_domain.py -q`

### Task 4: Santé + mémoire Serena + ADR court

- [x] `probe_apple_music()` dans `jarvis/health.py` + `PUBLIC_DETAIL_KEYS` si besoin (`engine` existe déjà).
- [x] Tests `tests/test_health_contract.py` : composant présent, raisons publiques, pas de path.
- [x] ADR-036 (10–20 lignes) : Music.app via MCP binaire local = outil JARVIS ; le runtime agentique garde le même binaire pour `media:publish` ; playback simple n’est plus un effet externe agentique. Amendement volontaire d’ADR-016 (pas osascript maison). (ADR-035 est déjà la délégation de vie.)
- [x] Mémoire Serena `integrations/apple_music_mcp` : remplacer « routé vers profil media + porte de plan » par le fast-path.
- [x] Vérifier : `python -m pytest tests/test_health_contract.py -q` ; `python3 tools/audit_architecture_truth.py` si l’ADR / le graphe santé change le contrat documenté.

---

## Hors scope

- File d’attente (« Up Next ») : non supportée par le backend Apple Events v0.1.0.
- Recherche catalogue / Apple Music Streaming hors bibliothèque.
- Daemon MCP permanent, start/stop Control Center.
- Brancher le MCP Cursor IDE sur le chat JARVIS.
- Changer `AGENTIC_REQUIRE_PLAN_APPROVAL` (le playback ne doit plus arriver à cette porte).
- Nouvelles routes `/api/music/*` (YAGNI : control + integrations + actions suffisent).

## Plafond assumé

`# ponytail: library search only, catalog_search when apple-music-mcp exposes it`

Si Werenoi n’est pas dans la bibliothèque locale, JARVIS refuse clairement. Ajouter le catalogue le jour où le binaire le sait, pas avant.
