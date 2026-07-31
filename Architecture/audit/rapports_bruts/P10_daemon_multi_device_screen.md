<!--
source_agent: bc-019fb873-3149-73f9-87f4-f5472ce1d257
agent_name: Daemon multi-device screen
agent_url: https://cursor.com/agents/bc-019fb873-3149-73f9-87f4-f5472ce1d257
agent_status: IDLE
created_at: 2026-07-31T13:53:10.431000+00:00
extracted_msg_index: 131
extracted_at: 2026-07-31T14:37:19.332936+00:00
-->

```yaml
id_perimetre: P10
nom: Daemon multi-device et screen
mode: lecture_seule
date: 2026-07-31
verdict_global: PASS_AVEC_RESERVES
resume: >
  Pairage (TTL / one-time / rate-limit IP), hash-only device token et header
  X-Device-Token sont correctement implémentés. Vision locale Ollama + Claude
  texte-only tenu sur le chemin local. Réserve majeure : l'analyse remote
  /api/devices/{id}/screen appelle _analyze_with_ollama(img) avec 1 argument
  alors que la signature en exige 3 — la vision distant est morte. TTS cooldown
  présent côté daemon local, absent côté file TTS remote. Control /api/control/*
  authentifié via session OU supervisor localhost+header.
```

---

## 1. Périmètre lu

| Fichier | Lignes | Statut lecture |
|---|---:|---|
| `scripts/jarvis_daemon.py` | 588 | complet |
| `scripts/screen_watcher.py` | 813 | complet |
| `scripts/jarvis_agent.py` | 376 | complet |
| `scripts/jarvis_launchd.py` | 298 | complet |
| `api/router_devices.py` | 295 | complet |
| `api/router_daemon.py` | 185 | complet |
| `database/screen_daemon.py` | 394 | complet |
| `requirements-agent.txt` | 14 | complet (imports agent) |

Hors périmètre cité mais nécessaire aux preuves checklist : `api/middleware.py` (gate session / device / supervisor), `config.py` (TTL / cooldown), `auth.hash_token`.

---

## 2. CHECKLIST

| # | Item | Statut | Preuve |
|---|---|---|---|
| 1 | Pairing codes : TTL, one-time, rate-limit IP | **OK** | `router_devices.py:74-116` + `screen_daemon.py:137-231` — TTL `DEVICE_PAIRING_TTL_MINUTES`, `used_at` one-shot, `device_pairing_attempts` par `client_key=request.client.host`, 429+Retry-After |
| 2 | Token device : hash only, header uniforme | **OK** | SHA-256 via `auth.hash_token` ; stocké `token_hash` ; raw rendu 1× au register/rotate ; listes API sans `token_hash` ; `_require_device_token` + `X-Device-Token` + `hmac.compare_digest` ; agent headers homogènes |
| 3 | Screenshot : Ollama local ; Claude texte only | **PARTIEL** | Local SW : images → Ollama only ; daemon `_on_screen_notable` envoie **texte** à `process_message_internal`. Remote : **appel cassé** (sig mismatch) → pas d'Ollama, fallback `remote_no_analysis` ; si ça marchait, Claude ne reçoit toujours que le texte `notable` |
| 4 | TTS cooldown / anti-spam | **PARTIEL** | Daemon local : `DAEMON_TTS_COOLDOWN` + DND + quiet hours (`jarvis_daemon.py:445-507`). Remote : queue `maxsize=10` sans cooldown ni quiet hours |
| 5 | Supervisor control auth | **OK** | `router_daemon.py` sans auth locale ; gate middleware : session navigateur **ou** `127.0.0.1/::1` + `X-Jarvis-Supervisor: 1` sur `/api/control/*` |
| 6 | Permissions / échecs silencieux | **PARTIEL** | Token agent `0600` + dir `0700` OK. Multiples `except: pass` (heartbeat/TTS agent, save screen remote). Capture/osascript échouent en warning/debug sans alerte opérateur |

---

## 3. CONSTATS

### P10-C01 — HAUTE — Vision remote cassée (signature)

- **Fichier** : `api/router_devices.py:174` vs `scripts/screen_watcher.py:645-647`
- **Fait** : `await _sw._analyze_with_ollama(img)` (1 positional) alors que la méthode exige `(img, app, window_info)` sans défaut. Confirmé AST : `call argc=1`, `defaults=0`.
- **Effet** : `TypeError` capturé L175-176 → `analysis=None` → branche `remote_no_analysis`. Aucune vision Ollama, aucun TTS notable remote. Pas de test couvrant `api_device_screen` + analyse.
- **Reco** : `await _sw._analyze_with_ollama(img, declared_app, None)` (ou wrapper public).

### P10-C02 — MOYENNE — Pas de plafond taille `image_b64`

- **Fichier** : `api/router_devices.py:149-164`
- **Fait** : décodage base64 + `PIL.Image.open` sans limite octets / dimensions. Device authentifié peut saturer RAM/CPU.
- **Reco** : rejeter si `len(image_b64)` > N ou pixels > plafond (aligné resize agent 1280×800).

### P10-C03 — MOYENNE — TTS remote sans cooldown / anti-spam

- **Fichier** : `api/router_devices.py:42-51, 203-216`
- **Fait** : file par device `maxsize=10` seulement. Pas de `DAEMON_TTS_COOLDOWN`, DND, ni quiet hours sur ce chemin (contrairement au daemon local).
- **Reco** : partager la politique cooldown/DND avant `queue.put_nowait`.

### P10-C04 — MOYENNE — Agent distant : pas d'exigence Tailscale/TLS

- **Fichier** : `scripts/jarvis_agent.py:339-345, 249-258`
- **Fait** : `--server` accepte `http://…` ; pas de vérif préfixe Tailscale / HTTPS ; screenshots JPEG voyage en clair si HTTP. Token 0600 OK. `ip_tailscale` non envoyé au register.
- **Reco** : refuser non-HTTPS hors loopback ; optionnellement vérifier CGNAT Tailscale ; envoyer `ip_tailscale`.

### P10-C05 — BASSE — Rate-limit pairing = IP socket

- **Fichier** : `api/router_devices.py:101` + `screen_daemon.py:149-171`
- **Fait** : `client_key = request.client.host`. Derrière proxy/Serve loopback, tous les essais partagent la même clé (lockout global ou contournement selon topologie).
- **Reco** : clé dérivée de `X-Forwarded-For` / Tailscale identity si proxy déclaré.

### P10-C06 — BASSE — Health devices : horodatages naïfs

- **Fichier** : `scripts/jarvis_daemon.py:550-565`
- **Fait** : `datetime.now()` local vs `last_heartbeat` SQLite `CURRENT_TIMESTAMP` (UTC typique) → faux offline / retard de bascule active.
- **Reco** : bornes UTC cohérentes (`timezone.utc`).

### P10-C07 — BASSE — Curseur iMessage avancé avant traitement

- **Fichier** : `scripts/jarvis_daemon.py:268-272`
- **Fait** : `advance_consumer_cursor(max rowid)` avant la boucle de triage/notif. Crash mid-loop → messages non notifiés, curseur déjà avancé.
- **Reco** : avancer après traitement réussi par message / batch.

### P10-C08 — BASSE — Échecs silencieux agent + save remote

- **Fichiers** : `jarvis_agent.py:202-203, 334-335` ; `router_devices.py:230-231`
- **Fait** : heartbeat/TTS `except: pass` ; save activity remote `except: pass`. Masque révocation token / disque plein.
- **Reco** : log warning throttlé ; backoff si 401.

### P10-C09 — INFO — Docstring daemon obsolète (Ollama triage)

- **Fichier** : `scripts/jarvis_daemon.py:7-16, 363-395`
- **Fait** : en-tête promet « triage local Ollama » ; `_local_triage` utilise DeepSeek Flash (`TRIAGE_MODEL` → `DEEPSEEK_FAST_MODEL`). Politique 2026 correcte ; doc mensongère.
- **Reco** : aligner le docstring (Ollama = Screen Watcher only).

### P10-C10 — INFO — Wake word stub (délégation P09)

- **Fichier** : `scripts/jarvis_daemon.py:522-546`
- **Fait** : si `WAKE_WORD_ENABLED`, boucle no-op + warning ; micro exclusif `audio_daemon`. Pas de double capture. Hors scope audio_daemon (P09).

### P10-C11 — INFO — `requirements-agent.txt` cohérent

- Imports tiers agent : `requests`, `PIL` uniquement — match `requests>=2.31`, `Pillow>=10.0`. OK.

### P10-C12 — INFO — launchd : chemins non quotés

- **Fichier** : `scripts/jarvis_launchd.py:46-52`
- **Fait** : `cd {PROJECT_DIR}` / `exec {VENV_PYTHON}` sans quotes. Casse si espaces dans le path. Auth N/A (local user LaunchAgent).

---

## 4. Carte de flux (vérifiée)

```text
[Local Mac Mini]
  jarvis_daemon.start
    ├─ _tts_loop (cooldown/DND/quiet) → voice_queue → audio_daemon play
    ├─ _notification_loop (5s)
    │    ├─ iMessage (skip si bridge) → DeepSeek triage → TTS + notif UI
    │    └─ Mail (skip si email_watcher) → DeepSeek triage → TTS + notif UI
    ├─ screen_watcher.ensure_started(require_ollama)
    │    └─ capture → crop → Ollama vision → on_notable(texte) → DeepSeek texte → TTS
    ├─ calendar 5 min
    ├─ device_health 30s (offline >120s)
    └─ wake_word (stub → P09)

[Remote jarvis_agent]
  pairing_code → POST /register (public, code one-time)
  token 0600 → X-Device-Token
  heartbeat 30s / screen JPEG si diff≥15% / poll TTS 2s
       │
       ▼
  router_devices.screen → Ollama ???  ← C01 CASSE
                       → process_message_internal(texte) si notable
                       → queue TTS device
```

**Invariant tenu (local)** : aucune image n'est passée à Claude/DeepSeek — uniquement `activity`/`notable` texte.

**Invariant remote** : même intention, mais analyse Ollama injoignable (C01).

---

## 5. Contrôles sécurité (synthèse)

| Contrôle | Résultat |
|---|---|
| Pairing start = session navigateur | OK (`middleware` non bypass ; test `test_pairing_start_requires_browser_session`) |
| Register = code one-time + rate-limit | OK |
| Token jamais re-listé | OK (tests pairing) |
| Heartbeat/screen/TTS = device token | OK (`_require_device_token` + bypass session ciblé) |
| Activate/rotate/revoke/list/screen-activity = session | OK |
| `/api/control/*` = session ou supervisor local+header | OK |
| Launchd KeepAlive 24/7 | OK (pas d'auth réseau ; scope user) |

---

## 6. VERDICT

| Champ | Valeur |
|---|---|
| **Verdict** | `PASS_AVEC_RESERVES` |
| **Bloquant prod multi-device** | **Oui — C01** (remote screen intelligence morte) |
| **Bloquant sécurité auth device** | Non — pairing/token/header solides |
| **Bloquant privacy image→cloud** | Non sur chemin local ; remote n'analyse pas (fail closed de fait) |
| **Actions prioritaires** | 1) Fix call `_analyze_with_ollama` 2) Cap taille image 3) Cooldown TTS remote 4) Durcir agent HTTPS |

Lecture seule : aucune modification, aucun commit, aucun PR.