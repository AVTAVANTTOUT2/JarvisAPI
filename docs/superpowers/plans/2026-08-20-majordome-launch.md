# Majordome — `launch` + charte de confiance (plus de goutte-à-goutte)

> **For agentic workers:** Implement task-by-task. Steps use checkbox syntax.
> Ne pas commencer la Task 2 avant que la Task 1 soit verte : YouTube doit marcher sans la charte.

**Goal:** « ouvre YouTube, la chaîne de Squeezie » ouvre la chaîne. Ensuite, toute ouverture locale (app, URL, fichier, schéma) et tout raccourci marqué de confiance s’exécutent **sans** demander « oui ». L’envoi, l’argent, le shell libre et les missions code restent confirmés.

**Symptom today:** `open_app` ne sait lancer que `open -a AppName`. Le test `test_only_the_exact_open_dash_a_form_is_accepted` **refuse** `open -a Safari https://…`. Le prompt ne montre que Safari. Le modèle ouvre Safari et dit à l’utilisateur de naviguer. Ce n’est pas un manque de permission : `.env.config` a déjà `COMPUTER_ACCESS=true`.

**Architecture:** un primitifs macOS (`/usr/bin/open`) + une charte de classes (pas une action par app) + honorer la colonne Shortcuts déjà là (`requires_confirmation`). Patron ADR-036 : outil local, pas une mission.

**Tech stack:** `integrations/computer.py`, `actions.execute_action`, `config.py`, pytest. Aucune nouvelle dépendance. Pas de nouvelle route HTTP pour le lot 1. Pas de computer-use.

---

## Décisions

1. **Pas d’intégration YouTube / Netflix / Maps.** Un seul verbe `launch` (alias conservé : `open_app`). Cible = URL, schéma, fichier sous `$HOME`, ou nom d’app.
2. **`open` n’est pas du shell.** Ça reste `_run_argv` validé. On n’ajoute pas `open` à l’allowlist terminal. On n’ouvre pas `AGENTIC_REQUIRE_PLAN_APPROVAL=false`.
3. **Confirmation selon la classe, pas selon l’app.** `local.launch` / `local.media` / `local.read` = auto. `comms.send` / `money` / `shell` / `destructive` / `agentic.code` = confirm (inchangé).
4. **Charte une fois, dans `.env.config`.** `JARVIS_TRUST_PROFILE=majordomo|standard|restricted`. Défaut code = `restricted` (install neuve fail-closed). Cette machine = `majordomo`. Ce n’est pas un mode dieu : mail/iMessage/Uber/rm restent confirmés.
5. **Shortcuts : honorer la colonne existante.** `apple_shortcut_registry.requires_confirmation` existe déjà, défaut `1`. `_action_run_shortcut` l’ignore et confirme **toujours**. Ne pas ajouter `auto_run`. Si `requires_confirmation=0` **et** `risk=low` **et** profil `majordomo`/`standard` → exécuter. `risk=high` confirme même si la case est décochée.
6. **Préférer `open` à AppleScript.** Chaque `tell application "Foo"` re-déclenche le TCC par app. `open` ne le fait pas.
7. **Accessibilité = lot ultérieur.** Un TCC unique, pas le chemin par défaut. Hors scope de cette livraison.
8. **Fichiers env.** Flags applicatifs → `.env.config` / `.env.config.example`. Jamais dans `.env` (secrets). `.env.example` reste fail-closed (`COMPUTER_ACCESS=false`, profil `restricted`) pour un clone neuf.

---

## Flux cible

```
« ouvre youtube squeezie » / « ouvre https://youtube.com/@Squeezie » / « ouvre Notes »
        │
        ▼
bloc ```action```  type=launch|open_app
        │
        ▼
resolve_launch_target()     # URL / schéma / fichier $HOME / nom d'app
        │
        ├─ interdit (javascript:, path hors home, app hors allowlist) → refus clair
        └─ autorisé → /usr/bin/open …   sans confirmation
```

Réponse type : « Chaîne de Squeezie, YouTube. » Trois phrases max. Pas « navigue ensuite vers… ». Pas « Monsieur ».

---

## Contrats d’action

Un type `launch`, `open_app` reste un alias (même handler). Discriminé par les champs, pas par dix types.

```json
{"type":"launch","url":"https://www.youtube.com/@Squeezie"}
{"type":"launch","url":"https://www.youtube.com/@Squeezie","app":"YouTube"}
{"type":"launch","name":"Notes"}
{"type":"open_app","name":"Safari"}
{"type":"launch","path":"~/Documents/rapport.pdf"}
```

Formes argv acceptées par `_validate_argv` (aujourd’hui seule la 1 est légale) :

| argv | Rôle |
|---|---|
| `open -a AppName` | inchangé |
| `open URL` | http(s) + schémas allowlistés |
| `open -a AppName URL` | ouvrir l’URL **dans** une app |
| `open /chemin/sous/$HOME` | fichier / dossier local |

Toujours refusés : `open -a App --args …`, chemins d’app, `javascript:`, `data:`, `file:` hors home, credentials dans l’URL, `smb://` / `afp://` / `ssh://`.

Schémas allowlistés (config, pas du code par app) :

```
https, http, file,
youtube, spotify, maps, x-apple.systempreferences, shortcuts, mailto
```

`mailto:` ouvre Composer, il n’envoie pas — auto. L’envoi reste l’action `mail` confirmée.

Petit catalogue **données** dans `integrations/launch_targets.py` (dict Python, pas un JSON à part) : hôtes YouTube / Spotify / Maps → URL canonique. « squeezie » + youtube → `https://www.youtube.com/@Squeezie`. Ce n’est pas une intégration YouTube.

---

## Charte (`JARVIS_TRUST_PROFILE`)

| Classe | restricted | standard | majordomo |
|---|---|---|---|
| `local.launch` | allowlist apps si renseignée | auto | auto |
| `local.read` | auto | auto | auto |
| `local.media` | auto | auto | auto |
| `local.shortcuts` low + `requires_confirmation=0` | confirm | auto | auto |
| `local.shortcuts` high | confirm | confirm | confirm |
| `comms.send` / `money` / `shell` / `destructive` | confirm | confirm | confirm |
| `agentic.code` (plan + digest) | inchangé | inchangé | inchangé |

`restricted` = comportement actuel (Shortcuts toujours confirmés, `open -a` seul jusqu’à la Task 1).

---

## Fichiers touchés (carte)

| Fichier | Rôle |
|---|---|
| `integrations/computer.py` | Nouvelles formes `open` validées ; `open_url` / `open_path` |
| `integrations/launch_targets.py` | Catalogue hôtes + résolution query → URL |
| `actions.py` | `_action_open_app` devient launch ; alias `launch` |
| `prompts/persona.txt` | Remplacer OPEN_APP par LAUNCH ; interdiction de « navigue ensuite » |
| `jarvis/cognitive/capability_registry.py` | `computer.launch` (garder `computer.open_app` comme alias dispo) |
| `config.py` | `JARVIS_TRUST_PROFILE`, `LAUNCH_URL_SCHEMES` |
| `.env.example` | fail-closed documenté |
| `.env.config.example` | `COMPUTER_ACCESS=true`, `JARVIS_TRUST_PROFILE=majordomo`, Shortcuts on |
| `database/apple_shortcuts.py` / `actions.py` | Honorer `requires_confirmation` |
| `scripts/macos_permission_doctor.py` | Task 4 — sondes TCC, zéro reset |
| `Architecture/adr/ADR-037-charte-majordome.md` | Décision courte |

---

### Task 1: Primitifs `launch` (ferme YouTube)

- [x] `integrations/launch_targets.py` : `resolve_launch_target(url=None, name=None, path=None, app=None) -> LaunchSpec`. Refuse plutôt qu’inventer. YouTube : host `youtube.com` / `youtu.be` / `m.youtube.com` ; query sans URL → `https://www.youtube.com/@{slug}`.
- [x] `ComputerControl` : étendre `_validate_argv` aux 4 formes ci-dessus. Méthodes `open_app` (inchangée), `open_url(url, app=None)`, `open_path(path)`. Toujours `COMPUTER_ACCESS` + `COMPUTER_ALLOWED_APPS` pour `-a`.
- [x] **Inverser** `tests/test_computer_control_argv.py` : `test_only_the_exact_open_dash_a_form_is_accepted` doit **accepter** `(_OPEN, "-a", "Safari", "https://youtube.com/@Squeezie")` et `(_OPEN, "https://youtube.com/@Squeezie")`. Garder le refus de `--args`, des chemins d’app, des URL `javascript:`.
- [x] `actions.py` : `type in {"launch", "open_app"}` → même handler. Champs `url` / `path` / `name`/`app_name`/`app`. Pas dans `ACTIONS_REQUIRING_CONFIRMATION`. Rester dans `ACTIONS_WITH_FOLLOWUP`.
- [x] `prompts/persona.txt` : LAUNCH avec l’exemple Squeezie. Règle : « Tu exécutes. Tu ne dis jamais à l’utilisateur d’ouvrir un navigateur ou de coller une URL. »
- [x] Tests actions : `execute_action({"type":"launch","url":"https://www.youtube.com/@Squeezie"})` appelle `open_url` (mock argv), `ok=True`, pas `needs_confirmation`. `open_app` Safari reste vert (`test_process_message_internal_executes_open_app`).
- [x] Vérifier : unitaires `launch_targets` / `launch_action` / `computer_control_argv` / `action_confirmation_gate` verts. `test_voice_action_execution` complet peut pendre sur kevent hors de ce lot.

### Task 2: Charte + `.env*` (pas de nouvelle route)

- [x] `config.py` : `JARVIS_TRUST_PROFILE` (`restricted`/`standard`/`majordomo`, défaut `restricted`). `LAUNCH_URL_SCHEMES` (frozenset, défaut = liste ci-dessus). Helper `trust_allows(class_name) -> bool`.
- [x] `.env.example` : documenter fail-closed (`JARVIS_TRUST_PROFILE=restricted`, `COMPUTER_ACCESS=false`, `APPLE_SHORTCUTS_ENABLED=false`).
- [x] `.env.config.example` : `COMPUTER_ACCESS=true`, `JARVIS_TRUST_PROFILE=majordomo`, `APPLE_SHORTCUTS_ENABLED=true`, `COMPUTER_ALLOWED_APPS=` vide. Ajouter le bloc Shortcuts manquant (aujourd’hui absent de `.env.config.example`).
- [x] Cette machine : `.env.config` (gitignored) — `JARVIS_TRUST_PROFILE=majordomo`, `APPLE_SHORTCUTS_ENABLED=true`. Déjà `COMPUTER_ACCESS=true`. **Ne pas** écrire ces flags dans `.env` (fichier secrets).
- [x] `capability_registry` : `computer.launch` `risk=low` `requires_confirmation=False` `available=COMPUTER_ACCESS`. `apple.shortcuts.run` : `requires_confirmation` = True seulement si le profil n’autorise pas l’auto (la confirmation réelle reste par raccourci).
- [x] Tests config : profil inconnu → `restricted`. `javascript` jamais dans `LAUNCH_URL_SCHEMES`. `test_config_example_contains_no_secret_keys` reste vert.
- [x] Vérifier : `python -m pytest tests/test_env_loader.py tests/test_computer_control_argv.py tests/test_trust_charter.py -q`

### Task 3: Shortcuts de confiance (colonne déjà là)

- [x] `_action_run_shortcut` : si `row["requires_confirmation"]` est faux **et** `row["risk"] == "low"` **et** `trust_allows("local.shortcuts")` → `run_shortcut_async` immédiat, **sans** `create_plan`. Sinon, plan opaque inchangé.
- [x] `risk=high` ou `medium` : toujours plan + confirm, même case décochée. La case UI ne peut pas éluder un raccourci dangereux.
- [x] Tests : inverser l’absolu de `test_action_run_shortcut_requires_registry_and_confirmation` — le ghost reste refusé ; un registre `requires_confirmation=0, risk=low` + profil majordomo **n’a pas** `needs_confirmation`. Un `risk=high` l’a encore. Un `confirmed:true` du modèle sans `shortcut_plan_id` reste ignoré sur le chemin confirmé.
- [x] Prompt : « Un raccourci de confiance déjà au registre se lance. N’invente jamais un nom. »
- [x] Vérifier : `python -m pytest tests/test_apple_shortcuts.py -q`

### Task 4: Doctor TCC (un wizard, pas du goutte-à-goutte)

- [x] `scripts/macos_permission_doctor.py` : sondes **non destructives** (pas de `tccutil reset`). v1 = Full Disk Access (`chat.db` 1 octet, aucun chemin exposé) + Accessibilité (`AXIsProcessTrusted`). Automation / micro / écran volontairement hors v1 (dialogues TCC).
- [x] Brancher en lecture seule sur `GET /api/integrations` (clé `macos_permissions`) — même famille que `apple_music`. Pas de nouvelle opération OpenAPI.
- [x] Linux CI : `unknown` / `optional_runtime_absent`, jamais `unavailable` global.
- [x] Vérifier : `python -m pytest tests/test_health_contract.py tests/test_public_openapi.py -q` (ajuster si le contrat a bougé).

### Task 5: ADR + mémoire + audit

- [x] `Architecture/adr/ADR-037-charte-majordome.md` (court) : primitifs `open` ; charte par classe ; Shortcuts honorent `requires_confirmation` ; pas de mode dieu send/pay ; amende ADR-029 (confirm **n’est plus** toujours). Ne pas amender ADR-034 (missions code inchangées).
- [x] Mémoire Serena `architecture/majordome_launch`.
- [x] `python3 tools/audit_architecture_truth.py` si l’ADR / la santé / OpenAPI bougent.

---

## Hors scope

- Accessibilité / computer-use / screenshots cliquables.
- `AGENTIC_REQUIRE_PLAN_APPROVAL=false`.
- Action `open_youtube`, intégration Netflix, Playwright pour ouvrir une URL.
- Auto-send mail / iMessage, auto-paiement Uber.
- `open --args`, shell `open` via `terminal`.
- Nouvelles tables SQLite.
- Changer le défaut `COMPUTER_ACCESS=false` dans `config.py` (le clone neuf reste fail-closed ; cette machine est déjà `true`).

## Plafond assumé

`# ponytail: open + trusted shortcuts cover 95%; Accessibility later if a target has no URL scheme`

Si une app n’a ni URL, ni schéma, ni raccourci enregistré, JARVIS le dit et propose d’enregistrer un raccourci. Il n’invente pas de clics.

---

## Ordre d’exécution

1. Task 1 → Squeezie s’ouvre.
2. Task 2 → la charte existe en config.
3. Task 3 → plus de « oui » sur les raccourcis low de confiance.
4. Task 4 → un écran / script au lieu de popups au fil de l’eau.
5. Task 5 → fige la décision.

Cette machine, **avant** le code Task 1 : `COMPUTER_ACCESS` est déjà `true`. Allumer `APPLE_SHORTCUTS_ENABLED=true` dans `.env.config` (opt-in existant) pour que Task 3 ait un pont. Les raccourcis confirment encore jusqu’à Task 3.
