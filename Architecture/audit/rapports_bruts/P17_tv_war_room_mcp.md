<!--
source_agent: bc-019fb8a5-48b0-7611-a9f6-a4885fd172de
agent_name: Audit sécurité tv mcp
agent_url: https://cursor.com/agents/bc-019fb8a5-48b0-7611-a9f6-a4885fd172de
agent_status: IDLE
created_at: 2026-07-31T14:47:53.321000+00:00
extracted_msg_index: 135
extracted_at: 2026-07-31T15:02:18.225585+00:00
-->

# AUDIT — P17 — TV et MCP browser

## Métadonnées
- Agent / modèle : Auto (Composer) — auditeur surface TV/CDP
- Date : 2026-07-31
- Commit audité (`git rev-parse HEAD`) : `2a6e0adc528aed4723d3a0477e17667867945f77`
- Branche : `main` (HEAD détachée)
- Fichiers dans le périmètre (count) : 40
- Fichiers lus (count) : 38 (2 binaires polices exclus)
- Couverture estimée : 95 % (sources texte 100 % ; polices woff2 non décompilées ; payload gzip de `front_tv/` inspecté structurellement, pas décompressé byte-à-byte)

## Synthèse exécutive
Le bridge CDP côté Mac cible bien `localhost`, mais le serveur TV bind `0.0.0.0:5174` sans session, avec une IP whitelist contournable via `X-Forwarded-For` — fuite PII réaliste (mails, iMessage, tâches) en lecture SQLite directe. MCP n’expose pas de shell libre, mais `tv_navigate` accepte toute URL et `tv_press_key` accepte tout keycode hors allowlist. IPs LAN (`192.168.3.82` / `.52`) et username macOS sont versionnés dans plists/scripts. XSS contenu largement échappé sur `tv-v2` (chemin actif) ; injection de classe CSS résiduelle sur le JS legacy. `aiohttp` est importé par le MCP et absent de `requirements.txt`. LaunchAgents auto-start réveillent ADB + CDP + dashboard au boot.

## Findings

### F-P17-001
- Sévérité : CRITICAL
- Type : sécurité
- Titre : Bypass whitelist IP via `X-Forwarded-For` non authentifié
- Preuve : `tv/server.py:161-168`
```python
forwarded = request.headers.get("X-Forwarded-For")
if forwarded:
    return forwarded.split(",")[0].strip()
```
Combiné à `TV_HOST=0.0.0.0` (`tv/config.py:18`) et absence totale d’auth session (`tv/README.md:42` « Pas d'authentification »).
- Impact : tout client réseau peut envoyer `X-Forwarded-For: 127.0.0.1` et lire `/api/messages`, `/api/emails`, `/api/tasks`, `/api/notifications`, `/api/health` (backend_data), SSE vocal.
- Repro / condition : `curl -H 'X-Forwarded-For: 127.0.0.1' http://<mac>:5174/api/emails` depuis une IP hors whitelist.
- Correctif proposé (sans coder) : ignorer XFF sauf reverse-proxy de confiance explicite ; sinon binder `127.0.0.1` / Tailscale only ; exiger token/session TV.
- Confiance : haute

### F-P17-002
- Sévérité : CRITICAL
- Type : sécurité
- Titre : Dashboard TV = lecture PII sans auth (contournement auth API principale)
- Preuve : `tv/server.py:361-394` + data_sources SQLite `mode=ro` (`tv/data_sources/emails.py:25-32`, `messages.py:96-105`, `notifications.py:39-55`, `tasks.py:38-53`) ; `tv/config.py:18-19` bind all interfaces ; whitelist `/24` + Tailscale `100.64.0.0/10` (`tv/config.py:27-32`).
- Impact : surface « monitoring » exposée au LAN/Tailnet sans PIN ; iMessage, résumés mails, tâches, mood, coûts API. Contourne le fail-closed auth du backend (frontière P02).
- Repro / condition : machine dans `192.168.3.0/24` ou Tailscale, ou bypass F-P17-001.
- Correctif proposé : auth dédiée TV (token device / cookie session) ; réduire whitelist ; ne pas binder `0.0.0.0` sans TLS+auth.
- Confiance : haute

### F-P17-003
- Sévérité : HIGH
- Type : sécurité
- Titre : `tv_navigate` — navigation CDP sans allowlist d’URL
- Preuve : `scripts/tv_mcp_server.py:201-207`, `343-344`
```python
result = await cdp_put("/json/new", {"url": url})
...
result = await tv_browser.navigate(arguments.get("url", DASHBOARD_URL))
```
- Impact : un client MCP (ou prompt injection Cursor) ouvre n’importe quelle URL sur la TV (`javascript:`, phishing, hosts internes LAN = pivot navigateur). Contrôle browser kiosk 24/7.
- Repro / condition : outil MCP `tv_navigate` avec `url` arbitraire après `ensure_ready()`.
- Correctif proposé : allowlist `http(s)://` + hosts dashboard uniquement ; refuser `file:`, `javascript:`, IP privées hors War Room.
- Confiance : haute

### F-P17-004
- Sévérité : HIGH
- Type : sécurité
- Titre : `tv_press_key` — keycode Android arbitraire (pas d’allowlist stricte)
- Preuve : `scripts/tv_mcp_server.py:360-373`
```python
keycode = key_map.get(arguments.get("key", "").upper(), arguments.get("key", ""))
code, _, _ = await adb("shell", "input", "keyevent", keycode)
```
- Impact : pas d’injection shell (`create_subprocess_exec`) mais envoi de `KEYCODE_POWER`, `KEYCODE_SLEEP`, appels, etc. Contrôle physique TV via MCP. Docstring tools promet « DPAD… » mais le fallback accepte tout.
- Repro / condition : `tv_press_key` avec `key: KEYCODE_POWER`.
- Correctif proposé : refuser si hors `key_map` ; jamais passer l’argument brut.
- Confiance : haute

### F-P17-005
- Sévérité : HIGH
- Type : sécurité
- Titre : ADB réseau + forward CDP auto au boot (LaunchAgent)
- Preuve : `scripts/launch_tv_browser.sh:9-12,25-57` ; `tv/com.jarvis.tv-browser.plist:12-13,22-27` (`RunAtLoad`, `TV_IP=192.168.3.82`) ; MCP `adb connect` + `adb forward tcp:9222` (`scripts/tv_mcp_server.py:104-127`).
- Impact : au login macOS, connexion ADB TCP non authentifiée vers la TV + bridge CDP local. Qui contrôle le LAN TV (port 5555) ou le localhost Mac (9222) contrôle le navigateur kiosk (et via ADB : shell TV). `settings put global policy_control immersive.full=*` (L37) mute le chrome système.
- Repro / condition : LaunchAgent chargé ; `adb connect 192.168.3.82:5555` depuis le LAN.
- Correctif proposé : ADB USB ou clé/auth ; ne pas `RunAtLoad` le bridge CDP ; documenter risque ; restreindre forward.
- Confiance : haute

### F-P17-006
- Sévérité : HIGH
- Type : sécurité / dette
- Titre : `aiohttp` importé par MCP, absent de `requirements.txt`
- Preuve : `scripts/tv_mcp_server.py:32` `import aiohttp` ; `rg aiohttp requirements.txt` → 0 match (httpx présent L15).
- Impact : MCP TV non installable depuis le venv déclaré ; dépendance fantôme / drift supply-chain ; audits précédents (P01) déjà signalés.
- Repro / condition : `pip install -r requirements.txt` puis `python scripts/tv_mcp_server.py` → `ModuleNotFoundError`.
- Correctif proposé : ajouter pin `aiohttp` ou remplacer CDP HTTP par `httpx` déjà déclaré.
- Confiance : haute

### F-P17-007
- Sévérité : MEDIUM
- Type : sécurité
- Titre : IPs d’infra et chemins utilisateur commités (TV_IP / dashboard / plists)
- Preuve : défauts `192.168.3.82` / `192.168.3.52:5174` dans `scripts/tv_mcp_server.py:39-42`, `scripts/launch_tv_browser.sh:9-12`, `tv/com.jarvis.tv-browser.plist:22-23` ; chemins `/Users/zeldris/JarvisAPI/...` dans les deux plists (`tv/com.jarvis.tv.plist:9-19`, `tv-browser.plist:10-18`).
- Impact : pas de secret cryptographique, mais fingerprinting réseau + username + arborescence ; plists cassés si le path réel diffère (`JARVIS` vs `JarvisAPI` — cohérent avec F-P01-001).
- Repro / condition : clone public du dépôt.
- Correctif proposé : défauts vides / placeholders ; env only ; plists générés hors git ou template sans IP/user.
- Confiance : haute

### F-P17-008
- Sévérité : MEDIUM
- Type : sécurité
- Titre : XSS / injection attribut classe sur widgets legacy (priority non allowlistée)
- Preuve : `tv/static/js/tasks.js:10` `class="task-dot '+t.priority+'"` ; `tv/static/js/notifications.js:8-9` `notif-item '+p` ; data_sources renvoient `priority` DB `.lower()` sans whitelist HTML (`tv/data_sources/tasks.py:63`, `notifications.py:65-77`). Chemin actif `/` sert `tv-v2.html` (`tv/server.py:267`) qui échappe mieux (`tv/static/js/tv-v2.js:29,180,197`).
- Impact : si `tv.html` + CDN legacy remis en service, ou si un champ priority est empoisonné, break-out d’attribut → XSS stocké affichant contenu iMessage/mail. Sur `tv-v2` : contenu user majoritairement `esc()` / `textContent` (overlay vocal L305-306) — OK.
- Repro / condition : servir `tv.html` + priority DB `high"><img src=x onerror=…>`.
- Correctif proposé : whitelist enum CSS ; retirer template/CDN legacy ou le verrouiller.
- Confiance : moyenne (actif = tv-v2 ; legacy toujours dans le tree)

### F-P17-009
- Sévérité : MEDIUM
- Type : sécurité
- Titre : Template legacy `tv.html` charge CDN tiers `@latest` (supply-chain)
- Preuve : `tv/templates/tv.html:127-128`
```html
<script src="https://unpkg.com/lucide@latest/dist/umd/lucide.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js"></script>
```
+ `tv/static/css/tv.css:7` Google Fonts. Non monté par la route `/` actuelle mais fichier livré sous `/` templates + static.
- Impact : compromission unpkg/`@latest` → JS arbitraire sur dashboard PII si template réactivé.
- Repro / condition : basculer TemplateResponse vers `tv.html`.
- Correctif proposé : vendor local only (comme tv-v2) ; supprimer ou isoler legacy.
- Confiance : haute (surface latente)

### F-P17-010
- Sévérité : MEDIUM
- Type : sécurité
- Titre : Proxy backend sans credentials + `verify=False` + WS non authentifié
- Preuve : `tv/data_sources/calendar.py:24-28` ; `tv/server.py:191-192`, `90-100` (`ws://…/ws` sans cookie/token).
- Impact : frontière auth API : le process TV lit calendar/status si le backend accepte loopback sans session ; TLS désactivé même en HTTPS. Fuite `backend_data` via `/api/health` (`tv/server.py:309-318`).
- Repro / condition : TV et backend co-localisés ; GET `/api/health` après accès.
- Correctif proposé : token service TV→API ; ne pas relayer `backend_data` brut ; `verify` configurable.
- Confiance : moyenne (comportement exact auth WS = P04)

### F-P17-011
- Sévérité : MEDIUM
- Type : sécurité
- Titre : LaunchAgent TV KeepAlive + bind public = surface permanente
- Preuve : `tv/com.jarvis.tv.plist:12-15` `KeepAlive`+`RunAtLoad` ; serveur `0.0.0.0:5174`.
- Impact : redémarrage auto du dashboard non authentifié après crash ; persistance post-reboot.
- Repro / condition : `launchctl load` du plist.
- Correctif proposé : KeepAlive seulement si auth+bind restreint ; sinon manual start.
- Confiance : haute

### F-P17-012
- Sévérité : LOW
- Type : sécurité
- Titre : CDP HTTP client = localhost only (OK partiel) ; comment/doc surévaluent JS evaluate
- Preuve : `scripts/tv_mcp_server.py:72-76` `http://localhost:{CDP_LOCAL_PORT}` ; `adb forward tcp:…` (L119-120) — binding host typiquement loopback ; mais `evaluate()` L238-247 est un stub no-op alors que l’en-tête L8 promet « Exécuter du JavaScript ».
- Impact : checklist « CDP localhost only » = **OK côté client MCP** ; risque résiduel = tout process local Mac parle à :9222. Pas d’eval JS via MCP aujourd’hui (atténuant).
- Repro / condition : `curl http://127.0.0.1:9222/json/list` sur le Mac hôte.
- Correctif proposé : documenter trust boundary localhost ; retirer promesse JS ou implémenter derrière allowlist.
- Confiance : haute

### F-P17-013
- Sévérité : LOW
- Type : smell / dead-code
- Titre : Imports morts et outil `evaluate` mort dans MCP
- Preuve : `scripts/tv_mcp_server.py:24-29` (`signal`, `subprocess`, `field`, `Path` inutilisés) ; `evaluate` jamais branché dans `tools/list` / `tools/call`.
- Impact : bruit, fausse surface d’attaque documentaire.
- Repro / condition : lecture statique.
- Correctif proposé : purge imports + méthode morte.
- Confiance : haute

### F-P17-014
- Sévérité : LOW
- Type : doc-drift
- Titre : README whitelist incomplète vs `config.py`
- Preuve : `tv/README.md:40` liste `192.168.1.0/24, 100.64.0.0/10, 127.0.0.1` — omet `192.168.3.0/24` présent `tv/config.py:29`.
- Impact : opérateur croit le réseau TV exclu ; il est inclus.
- Correctif proposé : aligner doc et code.
- Confiance : haute

### F-P17-015
- Sévérité : INFO
- Type : smell
- Titre : `front_tv/JARVIS War Room.html` = bundle offline, hors serveur TV
- Preuve : fichier unique ~268 Ko, bundler `__bundler/manifest` ; non référencé par `tv/server.py`.
- Impact : pas de surface runtime actuelle ; artefact design. Risque limité au double-clic local.
- Correctif proposé : archiver hors tree runtime ou documenter « mock only ».
- Confiance : haute

### F-P17-016
- Sévérité : INFO
- Type : sécurité
- Titre : Pas de commande shell arbitraire exposée comme outil MCP
- Preuve : tools list L282-330 = navigate/screenshot/info/dashboard/refresh/press_key/status ; `run_cmd` via `create_subprocess_exec` argv (`L51-64`) ; ADB args construits en liste.
- Impact : checklist « Pas de commande arbitraire via MCP » = **OK pour shell** ; atténué par F-P17-003/004 (URL/keycode).
- Confiance : haute

## Contrats vérifiés
| Contrat / invariant | Statut | Preuve |
|---|---|---|
| 1. CDP localhost only | **PARTIEL OK** | Client MCP → `localhost` (`tv_mcp_server.py:76`) ; `adb forward` local ; TV ADB TCP LAN = hors localhost |
| 2. Pas de commande arbitraire via MCP | **PARTIEL OK** | Pas de shell libre ; URL libre + keycode libre (F-003/004) |
| 3. Secrets TV_IP dans repo | **KO** | IPs + `/Users/zeldris/...` versionnés (F-007) — pas de clé API TV |
| 4. XSS dashboard | **PARTIEL OK** | `tv-v2` + `esc`/`textContent` OK ; legacy class injection + CDN (F-008/009) |
| 5. LaunchAgent auto-start risques | **KO** | `RunAtLoad` browser+server ; KeepAlive server ; ADB/CDP/immersive (F-005/011) |
| Auth dashboard TV | **KO** | IP only + XFF bypass (F-001/002) |
| deps aiohttp déclarée | **KO** | import sans pin requirements (F-006) |
| Route `/` = tv-v2 (pas CDN) | **OK** | `server.py:267` |
| SQL data_sources paramétrées | **OK** | `?` binds observés emails/tasks/messages |
| MCP stdio JSON-RPC only | **OK** | pas de bind réseau MCP (`main` L412+) |

## Frontières / dépendances
- Signale vers **P02** (auth) : TV lit `jarvis.db` et proxy `/api/calendar|/api/status` sans session → contournement du verrou fail-closed.
- Signale vers **P04** (WS) : `tv/server.py` `_ws_listener` ouvre `ws://BACKEND/ws` sans credentials.
- Signale vers **P01** (requirements/plists) : `aiohttp` manquant ; chemins LaunchAgent `/Users/zeldris/JarvisAPI` (déjà F-P01-001).
- Signale vers **P08** (intégrations) : `integrations.apple_data` pour iMessage TV (`messages.py`).
- Attendus consommés ailleurs : port `5174` CORS (`main.py` hors périmètre), supervisor contrôle TV, actions Chromecast/`TV_IP` dans `config.py` racine / `actions.py` (hors inclusion stricte — noté frontière).

## Fichiers non lus
| Fichier | Motif |
|---|---|
| `tv/static/assets/fonts/JetBrainsMono-Bold.woff2` | binaire police |
| `tv/static/assets/fonts/JetBrainsMono-Regular.woff2` | binaire police |

*(Le payload base64/gzip de `front_tv/JARVIS War Room.html` a été audité au niveau structure/scripts bundler, pas décompressé asset-par-asset.)*

## Couverture
Fichiers lus (chemins relatifs, triés) :

1. `front_tv/JARVIS War Room.html`
2. `scripts/launch_tv_browser.sh`
3. `scripts/tv_mcp_server.py`
4. `tv/com.jarvis.tv-browser.plist`
5. `tv/com.jarvis.tv.plist`
6. `tv/config.py`
7. `tv/data_sources/__init__.py`
8. `tv/data_sources/automations.py`
9. `tv/data_sources/calendar.py`
10. `tv/data_sources/devices.py`
11. `tv/data_sources/emails.py`
12. `tv/data_sources/messages.py`
13. `tv/data_sources/mood.py`
14. `tv/data_sources/notifications.py`
15. `tv/data_sources/rituals.py`
16. `tv/data_sources/server_stats.py`
17. `tv/data_sources/tasks.py`
18. `tv/data_sources/weather.py`
19. `tv/README.md`
20. `tv/server.py`
21. `tv/static/css/tv.css`
22. `tv/static/js/automations.js`
23. `tv/static/js/calendar.js`
24. `tv/static/js/clock.js`
25. `tv/static/js/emails.js`
26. `tv/static/js/globe.js`
27. `tv/static/js/main.js`
28. `tv/static/js/messages.js`
29. `tv/static/js/mood.js`
30. `tv/static/js/notifications.js`
31. `tv/static/js/stats.js`
32. `tv/static/js/tasks.js`
33. `tv/static/js/tv-v2.js`
34. `tv/static/js/utils.js`
35. `tv/static/js/voice-overlay.js`
36. `tv/static/js/weather.js`
37. `tv/templates/tv-v2.html`
38. `tv/templates/tv.html`