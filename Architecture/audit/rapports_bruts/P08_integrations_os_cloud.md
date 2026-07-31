<!--
source_agent: bc-019fb867-4e8a-753b-bd17-a9bcda25b111
agent_name: Intégrations OS/cloud
agent_url: https://cursor.com/agents/bc-019fb867-4e8a-753b-bd17-a9bcda25b111
agent_status: IDLE
created_at: 2026-07-31T13:40:06.216000+00:00
extracted_msg_index: 157
extracted_at: 2026-07-31T14:37:19.332823+00:00
-->

# AUDIT P08 — Intégrations

## 0. MÉTA

| Champ | Valeur |
|---|---|
| `ID_PERIMETRE` | P08 |
| `NOM` | Intégrations |
| `MODE` | Lecture seule — ligne par ligne |
| `INCLUS` | `integrations/**/*.py` (28 modules) |
| `EXCLUS` | `actions.py` → P04 ; routage cognitif Cursor → P12 |
| `DATE` | 2026-07-31 |
| `METHODE` | Lecture source + grep AST/patterns + croisement tests `test_apple_data.py` |

---

## 1. CHECKLIST

| # | Critère | Statut | Preuve |
|---|---|---|---|
| 1 | `apple_data` : façade unique `chat.db`, `mode=ro`, `query_only` | **PASS** | `apple_data.py:92-102` — `file:…?mode=ro` + `PRAGMA query_only = ON` + factory `_ReadOnlyConnection` |
| 2 | Aucune autre connexion `chat.db` | **PASS** | Seul `sqlite3.connect` sur Messages = `apple_data.py:94`. Reader/import/bridge délèguent. Garde AST `tests/test_apple_data.py:160-214`. `contacts.py:119` ouvre **AddressBook**, pas Messages |
| 3 | `computer.run` : shell vs allowlist ; `is_safe` | **FAIL partiel** | Denylist regex (`computer.py:18-50`) + `create_subprocess_shell` (`:64-71`) + `env={**os.environ}` — **pas d’allowlist**. Terminal LLM passe par `shell_safety` (P04) |
| 4 | `shell_safety.py` : plans one-shot, pas d’exec avant confirm | **PASS** | `prepare_shell_plan` enregistre sans exécuter (`:335-372`) ; `execute_shell_plan` consomme une fois (`:392-398`, `:431`) ; `create_subprocess_exec` sans shell (`:442`) |
| 5 | iMessage send : échappement AS + split 2000 | **FAIL partiel** | Corps échappé (`imessage.py:175-177`, `_applescript.py:212-221`) + `MESSAGE_CHUNK_SIZE=2000` (`:33`, `:215-218`). **`self.target` non échappé** (`:185`) |
| 6 | Location Haversine / radius | **PASS** (délégation) | `location.py` appelle `resolve_place` / `haversine` de `database/location_helpers.py:31-57` (`dist <= radius_meters`) |
| 7 | Timeouts httpx/subprocess partout | **PASS** avec notes | `_applescript` timeout ; weather 10s ; web_search 8s ; deepseek 120/15 ; fcm 15s ; shell_safety `wait_for` ; cursor `communicate(timeout=)`. Contacts AddressBook : **pas de `timeout=`** sur `sqlite3.connect` |
| 8 | `code_executor` : surface morte vs dangereuse | **FAIL** (dormant armé) | Aucun appelant `.execute()` hors status. Mais init avec `auto_run=True` si `CODE_EXECUTOR_ENABLED` défaut `true` (`config.py:216`, `code_executor.py:32-47`) |
| 9 | Pas de clé API hardcodée | **PASS** | Clés via `config.*` / fichier FCM path. Aucun `sk-` / Bearer littéral dans `integrations/` |

---

## 2. FINDINGS

### F-P08-01 — HAUTE  
**`computer.run` = shell + denylist, pas allowlist**  
`integrations/computer.py:44-71`

- `is_safe` = blacklist (rm -rf /, sudo rm, curl\|bash…). Contournable (`rm -rf "$HOME"`, `python -c …`, `osascript`, etc.).
- `create_subprocess_shell` + `executable=COMPUTER_SHELL` + copie complète de `os.environ` (fuite de secrets vers l’enfant).
- Atténuation : `_action_terminal` (P04) n’appelle plus `computer.run` — utilise `shell_safety`. Mais `find_files` / `open_app` / infos système passent encore par `run()` ; l’API publique reste dangereuse si réutilisée.

**Reco :** déprécier `run()` pour toute entrée LLM ; restreindre aux argv fixes via `create_subprocess_exec` ; env minimal.

---

### F-P08-02 — HAUTE  
**`code_executor` dormant mais armé**  
`integrations/code_executor.py:30-47`, `61-84`

- Open Interpreter, `auto_run = True`, `safe_mode = "auto"`, denylist FR très étroite.
- Défaut `CODE_EXECUTOR_ENABLED=true`.
- Aucun `.execute()` en prod (seul `api/misc_status.py` lit `.available`) → surface **morte pour le flux terminal**, **dangereuse si reconnectée**.

**Reco :** défaut `false` ; ne pas instancier l’interpréteur au import ; ou supprimer le module.

---

### F-P08-03 — MOYENNE  
**Adresse iMessage non échappée dans AppleScript**  
`integrations/imessage.py:182-187`

```185:186:integrations/imessage.py
            f'    set targetBuddy to participant "{self.target}" of targetService\n'
            f'    send "{escaped}" to targetBuddy\n'
```

- Le corps est échappé ; `self.target` non.
- `send_imessage_to_address(address, …)` (`:365-380`) accepte une adresse API → injection AppleScript possible via `"` / `\`.

**Reco :** `escape_applescript_string(self.target)` (et valider format téléphone/email).

---

### F-P08-04 — MOYENNE  
**FCM : `token_uri` issu du JSON service account**  
`integrations/fcm.py:50-82`

- `aud` et `httpx.post` utilisent `credentials.get("token_uri", …)`.
- Un fichier SA compromis → SSRF / vol de JWT signé.
- Timeouts OK (15s). Clé lue depuis path config, pas hardcodée.

**Reco :** allowlist `token_uri` ∈ `{https://oauth2.googleapis.com/token}` ; permissions fichier 0600 (hors P08).

---

### F-P08-05 — BASSE  
**Contacts AddressBook : `mode=ro` sans `query_only` / timeout**  
`integrations/contacts.py:119`

- Pas `chat.db` (checklist 2 OK).
- Incohérent avec le standard `apple_data`.

**Reco :** aligner sur `timeout=` + `PRAGMA query_only=ON`.

---

### F-P08-06 — BASSE  
**`escape_applescript_string` omet `\r`**  
`integrations/_applescript.py:212-221`

- Échappe `\`, `"`, `\n` seulement.
- `\r` dans titre notif / mail / message peut casser le littéral AS.

**Reco :** `.replace("\r", "\\r")`.

---

### F-P08-07 — INFO  
**Haversine hors `integrations/`**  
Logique correcte dans `database/location_helpers.py` ; `location.py` ne recalcule pas — acceptable pour P08, documenté.

---

## 3. INVENTAIRE MODULES (sécurité)

| Module | Rôle | Timeouts | Secrets | Risque résiduel |
|---|---|---|---|---|
| `apple_data.py` | Façade `chat.db` RO | sqlite timeout 5s | — | Faible |
| `imessage.py` / `imessage_reader.py` / `imessage_import.py` / `imessage_cursor.py` | Bridge / lecture / import | via façade + AS 30s | — | F-P08-03 |
| `imessage_daemon_client.py` | HTTP local :8193 | urllib 10s | — | Faible (loopback) |
| `_applescript.py` | osascript unifié | oui | — | F-P08-06 |
| `mail.py` / `calendar_api.py` / `contacts.py` / `notifications_macos.py` | AppleScript apps | oui (Calendar `pgrep`/`open` 2–2.5s) | — | Contacts SQLite F-P08-05 |
| `computer.py` | Shell denylist | `wait_for` | env complet | **F-P08-01** |
| `shell_safety.py` | Allowlist + plan opaque | oui | plans `token_urlsafe` | Faible (modèle correct) |
| `code_executor.py` | Open Interpreter | `wait_for` | `config.DEEPSEEK_*` | **F-P08-02** |
| `weather.py` / `web_search.py` / `deepseek_client.py` | HTTP | 10 / 8 / 120s | config | Faible |
| `fcm.py` | FCM v1 | 15s | fichier SA | F-P08-04 |
| `location.py` | Visites / trajets | N/A (DB) | — | Haversine OK (DB) |
| `ollama_client.py` / `ollama_control.py` | Vision / process | httpx + subprocess 3s | — | Popen serve volontairement sans timeout process |
| `cursor_*.py` | Délégation CLI | oui + killpg | `cursor_env` filtré | Subprocess OK ici ; **router → P12** |

---

## 4. CURSOR (`cursor_*.py`) — sous-périmètre P08 vs P12

**Audité ici (subprocess / sécurité) :**
- `cursor_cli.py` : `subprocess.run` timeout 15–20s, env safe, refuse CLI sans `--print`.
- `cursor_delegation.py` : worktree `jarvis/cursor/<id>`, jamais main ; `Popen` + `start_new_session` + `communicate(timeout)` + `killpg` ; prompt redacté ; confirmation avant run.
- `cursor_env.py` : pas de dump `os.environ` ; filtre KEY/TOKEN/SECRET.
- `cursor_required_tests.py` : `shell=False`, exe confinés au worktree, timeout borné ≤900s.

**À signaler à P12 (hors P08) :** composition prompts / routing Flash-Main / templates `prompts/cursor/*` / politiques `jarvis/cognitive/*` / confirmation UX / reprise jobs au lifespan.

---

## 5. RENVOIS

| Cible | Sujet |
|---|---|
| **P04** (`actions.py`) | `_action_terminal` → `prepare_shell_plan` / `execute_shell_plan` ; helpers `computer.open_app` / `find_files` / clipboard / battery |
| **P12** | Routeur cognitif, allowlist Ollama guard, orchestration délégation Cursor côté `jarvis/cognitive` |
| **P02 / auth** | Auth des endpoints qui appellent `send_imessage_to_address` / FCM (hors `integrations/`) |

---

## 6. VERDICT

| Dimension | Verdict |
|---|---|
| `chat.db` RO centralisé | **Conforme** |
| Shell LLM confirmé (`shell_safety`) | **Conforme** |
| Surface shell legacy (`computer.run`) | **Non conforme** (denylist + shell + env plein) |
| `code_executor` | **Non conforme** (dormant armé, défaut enabled) |
| iMessage send | **Partiel** (corps OK, destinataire KO) |
| HTTP / timeouts / secrets littéraux | **Conforme** |
| Cursor subprocess | **Conforme** (détail cognitif → P12) |

**Verdict global P08 : PARTIEL — 2 HAUTE, 2 MOYENNE, 2 BASSE.**  
Priorité correctifs : F-P08-03 (échappement `target`) → F-P08-02 (désarmer `code_executor`) → F-P08-01 (retirer/confiner `computer.run`) → F-P08-04 (allowlist `token_uri`).