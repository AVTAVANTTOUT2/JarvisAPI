# JARVIS — Assistant Personnel Multi-Agents

Assistant IA personnel avec interface vocale + web, propulsé par DeepSeek API (migration depuis Claude API).
Tourne en local sur Mac, mémoire SQLite, routing multi-agents avec prompt caching.

**Refonte UI / Figma** : spécifications frontend complètes (sections, API, WebSocket, composants, données, animations) dans [`FRONTEND_SPECS.md`](./FRONTEND_SPECS.md).

**Analyse du pipeline vocal** : flux complet micro → haut-parleur, chaque fonction, chaque latence, chaque point de défaillance → [`VOCAL_PIPELINE_ANALYSIS.md`](./VOCAL_PIPELINE_ANALYSIS.md).

**Protocole d'exploitation** : démarrage propre, permissions macOS, vérifications de santé, et reprise après coupure dans [`STARTUP_PROTOCOL.md`](./STARTUP_PROTOCOL.md).

## Installation

```bash
# Environnement Python
python3 -m venv venv
source venv/bin/activate

# Dépendances
pip install -r requirements.txt

# Configuration
cp .env.example .env
# Éditer .env et ajouter DEEPSEEK_API_KEY au minimum

# Lancer
python main.py
# → http://localhost:8080 (ou le port défini par WEB_PORT, ex. 8081)
#
# Si le navigateur affiche « connexion refusée » sur localhost alors que les logs indiquent « JARVIS prêt » :
# utilise **http://127.0.0.1:PORT** (remplace PORT par WEB_PORT dans `.env`, souvent 8081), pas `http://localhost` sans numéro de port.
```

> **État local (setup validé)** :
> - **Python** : 3.12 (3.14 incompatible avec scipy 1.15.3 → wheel manquant + nécessite gfortran). Le venv est créé avec `python3.12 -m venv venv`.
> - **Port** : `WEB_PORT=8081` car `whisper-server` (whisper.cpp d'un autre projet) occupe déjà `127.0.0.1:8080`.
> - **`.env`** : commentaires sur leurs propres lignes (python-dotenv parse mal les inline avec espaces multiples ou `#` collé à la valeur).
> - **Clés remplies** : `DEEPSEEK_API_KEY`, `WEATHER_API_KEY`, `TAVILY_API_KEY`, `IMESSAGE_TARGET`, `USER_NAME=Elias`.
> - **Mail** : lu via Apple Mail (Mail.app) en AppleScript — aucun token OAuth nécessaire. Comptes iCloud + Google détectés automatiquement.
> - **Calendar** : lu via Calendar.app (AppleScript), comme Mail — calendriers iCloud/Google déjà synchronisés dans l’app native.
>
> Toutes les autres variables (modèles, chemins, audio, briefings, timezone) ont des valeurs par défaut prêtes à l'emploi.

### Dernier changelog — 29 juin 2026 (23h15) : Correction réponse vocale (wake word)

**Probleme resolu** : JARVIS ne repondait pas en vocal. Cause : `wake_word_enabled` etait hardcode a `True` dans `audio_daemon.py`, sans lecture de config. Porcupine n'etant pas installe, le fallback volume (`FALLBACK_WAKE_RMS=0.03`) ne declenchait jamais avec le Blue Snowball. Resultat : le daemon restait bloque en `wake_listening`, ne consommait jamais les frames audio, et vidait la queue toutes les 6 secondes.

**Solution** (`scripts/audio_daemon.py`) :
- `self.wake_word_enabled` lit maintenant `config.WAKE_WORD_ENABLED` (defaut `"false"`)
- `WAKE_WORD_ENABLED=false` ajoute dans `.env` → ecoute continue, pas besoin de dire "Jarvis" avant de parler
- Quand `False`, le daemon passe directement en `listening` (pas `wake_listening`), la VAD consomme les frames en temps reel

**Test** :
```bash
curl -s http://127.0.0.1:8081/api/status | python3 -c "import sys,json; d=json.load(sys.stdin)['audio_daemon']; print(d['state'], d['wake_word_enabled'])"
# → listening False
```

### Dernier changelog — 29 juin 2026 (21h00) : Correction crash en cascade des services

**Probleme resolu** : Tous les services JARVIS etaient arretes (backend mort, port 8081 inaccessible). Trois causes racines identifiees et corrigees.

**Cause 1 — Shutdown handler incomplet (`supervisor.py`)** : Quand le supervisor etait tue (SIGTERM/launchd), il ne tuait pas ses processus enfants. L'ancien backend survivait, occupait le port 8081, et le nouveau supervisor ne pouvait pas demarrer de backend frais car `_start_sync()` retournait "Backend deja actif" sans verifier si le processus etait reellement gere.

**Cause 2 — Absence de health check** : Le supervisor demarrait le backend mais ne surveillait jamais s'il etait vivant. Si le backend crashait (audio_daemon PyAudio en boucle), le supervisor ne le savait pas et le frontend affichait "Backend arrete" indefiniment.

**Cause 3 — Boucle de crash audio_daemon** : PyAudio levait `OSError: Stream not open` en boucle. La « boucle immortelle » redemarrait avec un delai fixe de 3s → spam de logs + consommation CPU + cascade vers le backend.

**Corrections appliquees** :

| Fichier | Correction |
|---|---|
| `supervisor.py` | **Shutdown handler** : arret propre de TOUS les processus enfants dans l'ordre (vite_dev → tv_dashboard → ollama → backend) |
| `supervisor.py` | **Health check loop** : boucle asyncio toutes les `SUPERVISOR_HEALTH_CHECK_S` (defaut 10s) qui detecte backend mort/orphelin et le redemarre automatiquement |
| `supervisor.py` | **`_start_sync`** : detection des processus orphelins sur le port 8081 → kill force avant de demarrer un nouveau backend |
| `scripts/audio_daemon.py` | **Backoff exponentiel** : delai entre restarts passe de 3s fixe a 3s → 4.5s → 6.8s → ... → 30s max. Apres 10 crashes consecutifs, abandon 5 minutes |
| `scripts/audio_daemon.py` | **`_cleanup()` robuste** : gestion securisee de `stream.stop_stream()` / `stream.close()` sur un stream deja ferme. Verification `_stream is not None` avant appel |
| `scripts/audio_daemon.py` | **`_blocking_input` thread** : `stop_stream()` et `close()` wrappes dans des try/except OSError/AttributeError individuels |
| `scripts/screen_watcher.py` | **Anti-spam Ollama 404** : apres `SCREEN_OLLAMA_MAX_FAILURES` echecs consecutifs (defaut 5), desactive Ollama pendant `SCREEN_OLLAMA_COOLDOWN_S` (defaut 300s). Reessaie automatiquement apres le cooldown |

**Nouvelles variables d'env** :
```bash
SUPERVISOR_HEALTH_CHECK_S=10     # intervalle health check (secondes)
SCREEN_OLLAMA_MAX_FAILURES=5     # echecs avant desactivation Ollama
SCREEN_OLLAMA_COOLDOWN_S=300     # cooldown avant retenter Ollama
```

**Nouvel endpoint** : `GET /api/supervisor/status` inclut `backend_restart_count` (nombre de redemarrages automatiques du backend).

### Dernier changelog — 29 juin 2026 (19h00) : Fix pipeline vocal « je reviens vers vous »

**Probleme resolu** : JARVIS disait « je reviens vers vous dans un instant » mais ne vocalisait jamais le resultat. Cause racine : l'ancien `_process_voice_fast()` faisait 1 seul appel LLM qui generait texte + action ensemble ; le texte « je reviens... » partait en TTS avant que l'action soit executee, et la concatenation inline du resultat n'etait jamais vocalisee.

**Solution — pipeline 2 passes** (`main.py`) :

| Aspect | Avant | Apres |
|---|---|---|
| Flux | LLM dit "je reviens" + action -> concatenation inline muette | Pass 1 : LLM emet JUSTE le bloc action -> execution -> Pass 2 : LLM formule la reponse finale |
| Appels LLM | 1 (resultat perdu) | 1 si reponse directe, 2 si action |
| Prompt system | Permissif (texte + action melanges) | Strict : « JAMAIS de texte avant un bloc action » |
| Fallback | Aucun | `_fallback_action_response()` si LLM pass 2 echoue |
| Sauvegarde DB | Inline avec try/except | `_save_voice_messages()` dediee |

**Fonctions ajoutees** :
- `_process_voice_fast()` — reecrite (2 passes : decision -> execution -> reformulation)
- `_fallback_action_response(action_type, result)` — reformulation sans LLM (weather, open_app, task, calendar, terminal, reminder, mood)
- `_save_voice_messages(conv_id, user_text, assistant_text, cost)` — persistance silencieuse

**Latence** :
- Reponse directe (heure, salut) : 1 appel LLM ~1.5s
- Action (meteo, calendar) : 2 appels LLM + action ~3s

**Test** :
```bash
cd ~/JarvisAPI && source venv/bin/activate
python3 -c "
import asyncio
from main import _process_voice_fast
async def test():
    for q in ['Quelle heure est-il ?', 'Quel temps fait-il ?', 'Ouvre Safari']:
        r = await _process_voice_fast(q, 0)
        print(f'[{r[\"latency_ms\"]:.0f}ms] {q[:40]}')
        print(f'  -> {r[\"text\"][:80]}')
asyncio.run(test())
"
```

### Changelog precedent — 29 juin 2026 (17h58) : Horodatage dynamique + pipeline vocal

**Ajout principal** :
- **Horodatage dynamique `[HORODATAGE]` dans `BaseAgent.build_system_prompt()`** (`agents/__init__.py`) — calculé à chaque appel (jamais caché en prompt cache), inséré en **tout premier** dans le system prompt, avant la persona et le prompt agent. Format : `[HORODATAGE] lundi 29 juin 2026, 17:58 — Europe/Paris`. Le pipeline vocal `_process_voice_fast()` conserve son propre `datetime_str` (cohérent, pas de duplication).

### Changelog précédent — 29 juin 2026 (nuit) : Fix latence vocale + daemon audio immortel

**Problèmes résolus** :

1. **Latence vocale** — le pipeline vocal passait par l'orchestrateur complet (classification → routing → agent → contexte lourd → 2e passe LLM) soit 5-8s. Ramené à ~2s.
2. **Crash aléatoire du daemon audio** — les exceptions non catchées dans les boucles VAD/processeur tuaient le daemon silencieusement.

**`_process_voice_fast()` — pipeline vocal direct** (`main.py`) :
- Bypass total de l'orchestrateur : appel DeepSeek flash direct
- Contexte minimal : date/heure + 10 derniers messages (pas de `build_full_context`)
- Actions exécutées inline (pas de 2e passe LLM)
- Actions supportées : `weather`, `open_app`, `task`, `calendar`, `calendar_create`, `terminal`, `reminder`, `mood`

**Latence cible** :
```
t=0.0s  Fin de phrase (VAD)
t=0.5s  STT Scribe                   ~500ms
t=1.3s  DeepSeek flash direct        ~800ms
t=1.6s  TTS Edge                     ~300ms
t=1.9s  Playback
```
→ ~2 secondes (contre 5-8s avant)

**Daemon audio immortel** (`scripts/audio_daemon.py`) :
- **Boucle immortelle** : `start()` → `_run()` avec auto-restart après crash (délai 3s)
- **_run()** : crée 3 tâches parallèles (VAD, processeur, watchdog) et attend que l'une crashe → propage l'exception → nettoyage → restart
- **Boucles safe** : `_vad_loop_safe` et `_process_loop_safe` avec try/except par itération, compteur d'erreurs consécutives (50 pour VAD, 10 pour process) → force le restart
- **_mic_watchdog()** : vérifie toutes les 10s que le stream est actif et que des frames arrivent → restart si 60s de silence
- **_cleanup()** : nettoie proprement PyAudio, queues, tâches avant chaque restart
- **Heartbeat** : log toutes les ~60s pour preuve de vie dans les logs
- **Mode continu** : jamais de timeout vers idle quand `continuous_mode=True`
- **_cleanup_audio()** : inclut maintenant la tâche watchdog dans le filet de sécurité

**Correctif QueueFull — 29 juin 2026 (nuit)** :

- **Bug** : `QueueFull` dans `_blocking_input` (thread pyaudio) crashait le backend silencieusement. Le `put_nowait` était appelé via `call_soon_threadsafe`, donc l'exception levée sur l'event loop n'était jamais catchée par le `try/except` du thread.
- **Fix** : wrapper `_safe_put()` exécuté DANS le callback de l'event loop → `QueueFull` catché + drain + retry interne. Zéro `QueueFull` après le fix.
- **Anti-spam log** : flag `_mic_mute_logged` empêche le CRITICAL « Micro muet » de spammer le log (1 seul message par cycle de vie, contre des centaines avant).
- **Latence validée** : `_process_voice_fast("Bonjour Jarvis, comment vas-tu ?")` → 2136ms (cible <2s).
- **Micro problème matériel** : RMS=0.000 sur Blue Snowball, PHL et Shiver — permission macOS probablement révoquée. Vérifier : Réglages Système > Confidentialité > Microphone.


### Changelog — 29 juin 2026 (soir) : Supervisor — process permanent (port 9000)

**Nouveaute** : `supervisor.py` — un processus leger qui tourne en permanence (meme quand tout le reste est arrete) et sert le frontend React + expose une API de controle de tous les services.

**Superviseur** (`supervisor.py`) :
- Port 9000 — toujours actif, ne s'arrete jamais depuis l'UI
- Sert le frontend React (`web/dist/`) meme quand le backend est arrete
- Expose `/api/supervisor/*` : status, start/stop/restart par service, logs
- WebSocket `/ws/supervisor` : etat temps reel (push toutes les 2 secondes)
- Controle 4 processus externes via `subprocess.Popen` : backend (main.py :8081), TV dashboard (:5174), Ollama, Vite dev (:5173)
- Proxy `/api/*` → backend (retourne 503 avec hint si backend arrete)
- Sous-services backend visibles via `/api/supervisor/sub/{id}/{action}` (proxy vers `/api/control/*`)
- Auto-start du backend configurable via `SUPERVISOR_AUTO_START_BACKEND`

**Scripts** :
- `scripts/launch_supervisor.sh` — lancement interactif (`--no-backend` pour superviseur seul)
- `com.jarvis.supervisor.plist` — launchd macOS : demarrage auto au login, redemarrage automatique

**Frontend** (`web/`) :
- `ControlView.tsx` refondu : 2 niveaux (processus principaux + sous-services)
- WebSocket `/ws/supervisor` pour temps reel (plus de polling HTTP)
- Header avec PID superviseur + uptime
- Cartes par categorie (CORE, EXTERNE, DEV pour top-level ; AUDIO, CORE, INTEGRATIONS, MONITORING, ANALYSIS pour sous-services)
- Message explicite + bouton "Demarrer le backend" quand backend arrete
- `api.ts` : types `SupervisorService`, `SupervisorStatus` + helpers superviseur
- `vite.config.ts` : proxy `/api/supervisor` et `/ws/supervisor` vers port 9000

**Installation launchd** :
```bash
cp com.jarvis.supervisor.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.jarvis.supervisor.plist
```

**Lancement manuel** :
```bash
./scripts/launch_supervisor.sh
# → http://localhost:9000 (frontend) / http://localhost:9000/api/supervisor/status (API)
```

### Changelog — 29 juin 2026 : Page Control — gestion de tous les services

**Nouveauté** : page `/control` dans le frontend BIG BROTHER pour demarrer, arreter et redemarrer chaque service JARVIS individuellement ou tous d'un coup.

**Backend** (`main.py`) :
- 8 nouveaux endpoints REST sous `/api/control/` : `services` (GET), `{service}/start\|stop\|restart` (POST), `restart-all\|stop-all\|start-all` (POST), `{service}/logs` (GET)
- `_get_all_services_status()` : etat de 10 services (audio_daemon, email_watcher, jarvis_daemon, screen_watcher, imessage_bridge, scheduler, relationship_analyzer, ollama, tv_dashboard, vite_dev)
- `_start_service()` / `_stop_service()` : demarrage/arret avec imports dynamiques pour eviter les imports circulaires
- `_service_tasks` : tracking des tasks asyncio pour annulation propre
- Logs filtres par tag service dans `data/.jarvis_restart/backend.log`

**Frontend** (`web/` — React) :
- `ControlView.tsx` : grille de cartes par categorie (CORE, AUDIO, INTEGRATIONS, MONITORING, ANALYSE, EXTERNE)
- Boutons demarrage/arret/redemarrage par service + boutons globaux
- Panel logs escamotable en bas de page (30 dernieres lignes)
- Polling toutes les 5 secondes pour rafraichir l'etat
- Design system BIG BROTHER (glassmorphism, noir/blanc, JetBrains Mono)
- `api.ts` : types `ServiceInfo` + helpers `getServices`, `startService`, `stopService`, `restartService`, `restartAll`, `stopAll`, `startAll`, `getServiceLogs`
- Route `/control` ajoutee dans `App.tsx` + entree sidebar "Control" (icone `Settings2`) dans `BigBrotherLayout.tsx`

### Changelog — 29 juin 2026 (soir) : Superviseur JARVIS

**Nouveaute** : `supervisor.py`, le **processus permanent** qui controle tout le reste.

**Probleme resolu** : quand `main.py` (backend) s'arrete, le frontend est inaccessible et rien ne peut etre relance sans terminal. Le superviseur tourne en permanence et :
- Sert le frontend React (`web/dist/`) en fallback
- Expose une API de controle (`/api/supervisor/*`) pour demarrer/arreter/redemarrer les services top-level
- Maintient un WebSocket (`/ws/supervisor`) pour l'etat temps reel (pas de polling HTTP)
- Proxy transparent `/api/*` vers le backend quand il est actif

**Architecture** :
```
┌──────────────────────────────────────────────────┐
│  supervisor.py (port 9000) — TOUJOURS ACTIF      │
│  ├── Sert web/dist/ (frontend React)             │
│  ├── API /api/supervisor/* (controle)             │
│  ├── WebSocket /ws/supervisor (etat temps reel)   │
│  ├── Controle : Backend principal (main.py :8081) │
│  ├── Controle : TV Dashboard (tv/server.py :5174) │
│  ├── Controle : Ollama (ollama serve)             │
│  ├── Controle : Vite Dev (pnpm dev :5173)         │
│  └── Proxy /api/* → 127.0.0.1:8081 (si actif)    │
└──────────────────────────────────────────────────┘
```

**Fichiers crees** :
- `supervisor.py` — ~420 lignes, FastAPI + httpx + subprocess, zero LLM
- `scripts/launch_supervisor.sh` — script de lancement avec option `--no-backend`
- `com.jarvis.supervisor.plist` — launchd macOS (auto-start au login, redemarrage automatique)

**Fichiers modifies** :
- `web/src/services/api.ts` — types `SupervisorService`, `SupervisorStatus` + helpers `getSupervisorStatus`, `supervisorStart/Stop/Restart`, `supervisorStartAll/StopAll/RestartAll`, `supervisorLogs`, `getSubServices`, `subServiceAction` + `supervisorWsUrl()`
- `web/vite.config.ts` — proxy `/api/supervisor` → `127.0.0.1:9000` et `/ws/supervisor` → `ws://127.0.0.1:9000`
- `web/src/app/components/views/ControlView.tsx` — refonte architecture **2 niveaux** :
  - **Niveau 1** : processus principaux (carte large, WebSocket temps reel)
  - **Niveau 2** : sous-services (cartes compactes, polling HTTP si backend actif)
  - ID superviseur (PID, uptime) + statut WebSocket (WS LIVE / WS OFF) dans le header
  - Actions globales via API superviseur (pas via backend)

**Lancement** :
```bash
# Demarrage manuel
./scripts/launch_supervisor.sh

# Installation launchd (demarrage automatique au login)
cp com.jarvis.supervisor.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.jarvis.supervisor.plist

# Tests
curl http://localhost:9000/api/supervisor/status        # etat complet
curl -X POST http://localhost:9000/api/supervisor/backend/start  # demarrer le backend
```

**Config (.env)** :
```bash
SUPERVISOR_PORT=9000          # port du superviseur
SUPERVISOR_AUTO_START_BACKEND=true  # auto-demarrage du backend au boot superviseur
```

### Dernier changelog — 26 juin 2026 : Déblocage actions vocales

**Problème corrigé** : JARVIS ne pouvait QUE parler en mode vocal — pas d'heure, pas de météo, pas d'ouverture d'apps, pas de calendrier.

**Modifications** :

| Fichier | Modification |
|---------|-------------|
| `agents/orchestrator.py` | Injection `[HORODATAGE]` (date/heure/timezone) en première section du `memory_context` — recalculé à chaque appel |
| `agents/__init__.py` | Directive vocale enrichie : autorise explicitement les blocs `action` en mode vocal + fallback `VOICE_MAX_TOKENS` 500 |
| `main.py` | `ACTIONS_WITH_FOLLOWUP` étendu à `weather`, `calendar`, `calendar_create`, `open_app`, `mail_read`, `name_place`, `where_am_i`, `day_route` + formatteurs. Suppression double préfixe `[VOICE_MODE]` dans `_process_message_internal` |
| `config.py` | `VOICE_MAX_TOKENS` défaut 500 (était 200) |
| `.env.example` | `VOICE_MAX_TOKENS=500` |

**Impact** :
- JARVIS connaît maintenant la date et l'heure via `[HORODATAGE]` dans TOUS les contextes agent
- Les actions météo / calendrier / open_app / etc. sont exécutées PUIS reformulées en langage naturel
- Token limit augmenté pour permettre réponse + bloc action JSON en mode vocal
- Le pipeline `_process_message_internal` (utilisé par l'audio daemon) exécute les actions avec 2e passe

## Tracking GPS avec iOS Shortcuts (temporaire)

En attendant une app native, un **Raccourci** sur l’iPhone peut poster la position sur le Mac (même Wi‑Fi ou tunnel).

1. Ouvre l’app **Raccourcis** sur l’iPhone.
2. Crée un raccourci : action **Obtenir la position actuelle**, puis **Obtenir le contenu de l’URL** :
   - **URL** : `http://IP_DE_TON_MAC:WEB_PORT/api/location` (remplace par l’IP LAN du Mac et le port `WEB_PORT` du fichier `.env`, souvent 8081).
   - **Méthode** : POST  
   - **Corps** : JSON — `latitude`, `longitude`, `altitude` (optionnel), `accuracy` (optionnel), `source` : `"shortcut"`.
3. Dans **Automatisations**, par exemple *Toutes les 5 minutes*, exécute ce raccourci.

**Accès hors LAN** : tunnel (ngrok, Cloudflare Tunnel, Tailscale, etc.) vers le port JARVIS.

**Backend** : `integrations/location.py` (`LocationManager`), tables `places`, `location_history`, `visits`, `trips`, `location_patterns` ; analyse quotidienne `scripts/location_analyzer.py` (23h). UI : **Status** (carte Localisation), **Mémoire** (onglet **LIEUX**).

Dernière extension (mai 2026) : persistance + API complètes, intégration `build_full_context` / section orchestrateur `[LOCATION]`, actions `name_place` / `where_am_i` / `day_route`, planificateur 23h, documentation `CLAUDE.md` et variables `.env.example` (`LOCATION_TRACKING`, `LOCATION_PLACE_RADIUS`).

### Frontend (`web/` — React / Vite / BIG BROTHER)

- **Production** : `cd web && pnpm install && pnpm run build` puis `python main.py`. FastAPI sert `web/dist/` (`/` + `/assets/` + fallback SPA pour les routes React).
- **Routes UI** : l’index `/` redirige vers `/chat`. **BIG BROTHER** (`BigBrotherLayout.tsx`) : **sidebar** avec Chat, **Voix** (`/voice`), Dashboard, Contacts, Agenda (`/calendar`), Cartographie, Documents, Statistiques, Recherche, Données, Logs ; section libellée **Conversation** (Chat + Voix). **Barre horizontale** au-dessus du contenu (accès rapide) : Chat, Voix, Agenda, Dashboard, Contacts — pour que la voix reste visible même si la sidebar est longue ou peu regardée. Les routes `/vault` et `/ai` redirigent respectivement vers `/dashboard` et `/chat`. **Rebuild** : après changement de navigation, exécuter `pnpm run build` dans `web/` si tu sers la SPA depuis FastAPI (`web/dist/`).
- **Shell SPA** : point d’entrée `web/index.html` → `web/src/main.tsx` → `web/src/App.tsx` ; `web/package.json` + `web/tsconfig.json`. Les styles globaux Tailwind sont dans `web/src/index.css` (thème minimal + utilitaires `glass-panel`, `bg-grid-pattern`, animations utilisées par les vues).
- **Services** : implémentation dans `web/src/services/api.ts` (objet `api`, `BASE` vide + proxy) et `web/src/services/websocket.ts` (singletons `ws` / `jarvisWs`). `web/src/app/services/*.ts` ne fait que réexporter vers les imports existants `@/app/services/...`. `fetchJson` (`lib/api.ts`) s’appuie sur `API_BASE` / `VITE_API_URL` comme `api`.
- **Contacts macOS → DB** : `integrations/contacts.py` lit Contacts.app (AppleScript) ; au boot `sync_people_names()` corrige les lignes `people` encore stockées comme numéros/emails ; **`POST /api/contacts/sync`** déclenche la même sync à la demande.
- **WebSocket** : en `pnpm dev`, URL `ws://<host>:5173/ws` (proxy Vite). En build servi par FastAPI, même origine. En secours production sans proxy, `resolveWsUrl` peut cibler `:8081` ; surcharge possible avec `VITE_WS_URL`.
- **Alias Vite** : `@` → `web/src` ; imports du type `@/app/context/JarvisContext`.
- **Développement** : `python main.py` puis `cd web && pnpm dev` (port 5173). Le proxy dans `vite.config.ts` transfère `/api`, `/upload` et `/ws` vers `http://localhost:8081` (ajuster si `WEB_PORT` diffère).
- **Variables** : `web/.env` peut définir `VITE_API_URL` / `VITE_WS_URL` pour un front servi hors proxy ; en dev derrière Vite, laisser vide utilise la même origine + proxy.
- **pnpm 10** : le fichier `web/pnpm-workspace.yaml` définit `allowBuilds` pour `esbuild` et `@tailwindcss/oxide`. Sans cela, les scripts d’installation sont ignorés et `pnpm dev` peut afficher une page **« Internal Server Error »** (overlay Vite) au lieu du front.
- **Problème « le navigateur télécharge index.html au lieu d’afficher la page »** : corrigé côté serveur — les `FileResponse` pour la SPA utilisent `content_disposition_type="inline"` (Starlette met par défaut `attachment` si l’ancien code passait `filename=`, ce qui forçait le téléchargement).
- **Intégration** : `JarvisContext` branché sur `jarvisWs` ; `fetchJson` dans `lib/api.ts` réutilise la même base URL que `api.ts`.
- **Fallback** : si aucun fichier `web/dist/index.html`, mais `web/templates/` est présent, une SPA Jinja peut encore être servie (voir `_setup_frontend` dans `main.py`).
- **Données BIG BROTHER (mai 2026)** : `Dashboard.tsx` et `ContactsView.tsx` sont câblés sur `api` (`getStatus`, `getPeople`, `getPlaces`, `getNotifications`, `getPerson`, **`updatePerson`**, **`getPersonAnalytics`**, **`getPersonTimeline`**, **`sendImessage`**, **`suggestMessage`**, **`remindContact`**, `getRelationship`, `analyzeContact`). **`GET /api/people`** renvoie les contacts triés par dernière interaction (`get_people_sorted_by_recent`). **Page Contacts** : tri client par `last_mentioned` ; **description IA** ; **renommage** (`PATCH`) ; **chat contextuel** `POST .../ask` ; **analytics iMessage** (`scripts/contact_analytics.py`, calcul Python sans LLM) affichées sous le détail : score de proximité, tendance 3 mois, heatmap sentiment, sujets, non-répondus, derniers échanges, patterns, dates détectées ; **actions** envoi iMessage (`integrations/imessage.send_imessage_to_address`), suggestion Haiku, rappel → tâche ; **timeline Haiku** (`scripts/timeline_generator.py`) sur bouton. **Alertes relationnelles** : `scripts/contact_alerts.py` + job scheduler toutes les 6 h (`relationship_alerts`). Les graphiques mock (réseau SVG, activité hebdo) restent `TODO`. Types : `web/src/app/types/jarvis.ts`. Helpers : `formatRelativeTime`, `formatHoursFromMinutes`.
- **Correctifs critiques Contacts (mai 2026)** : résolution handle iMessage renforcée dans `main.py` (`_resolve_handle_with_contacts`) avec ordre explicite : `relationship_profiles` -> `imessage_analysis_cache`+Contacts -> cache Contacts inverse -> `people.name` si handle -> recherche iMessage directe (`get_conversation_with`). Logs debug ajoutés : `[resolve] name -> handle`. La recherche iMessage directe couvre maintenant **LIKE sur handle et texte message** (`integrations/imessage_reader.py`) et attache le handle aux messages retournés. `scripts/sync_contacts.py` ajoute une passe fuzzy (Levenshtein <= 2) pour corriger/fusionner les noms mal orthographiés (ex. `Merdille` -> `Bertille`). `ContactsView.tsx` : heatmap sentiment avec palette par seuils (`sentimentColor`) et message **« Analyse en cours... »** quand aucune donnée n’est disponible.

### Lancement quotidien

```bash
cd /Users/zeldris/JarvisAPI
source venv/bin/activate
python main.py
# → http://localhost:8081
```

### Frontend PWA (`pwa/` — Next.js mobile / iPhone)

La **PWA** est un client Next.js pur (zéro logique métier). Toutes les routes `/api/*` sont **proxifiées** vers le backend FastAPI via `next.config.js` (rewrites). Le backend reste la source unique de vérité — pas de DB locale, pas de cron, pas d'OAuth Google côté PWA.

```bash
# Démarrage local (deux terminaux)
# Terminal 1 — backend
cd /Users/zeldris/JarvisAPI && source venv/bin/activate && python main.py
# → http://localhost:8081

# Terminal 2 — PWA
cd /Users/zeldris/JarvisAPI/pwa && npm run dev
# → http://localhost:3000
```

**Important — le backend doit tourner en HTTP, pas HTTPS.** Le proxy server-side de Next.js refuse les certificats self-signed. Variable d'env :

```bash
# .env (racine JarvisAPI)
WEB_HTTPS=false  # défaut → HTTP. Mettre `true` uniquement si tu veux accéder directement au backend depuis l'iPhone via Tailscale.
```

```bash
# pwa/.env.local
NEXT_PUBLIC_JARVIS_API_URL=http://127.0.0.1:8081
# IPv4 explicite : Node résout "localhost" en IPv6 (::1) en premier, ce qui fail si uvicorn écoute en IPv4 only.
```

**Pas de hooks dédiés** — les pages appellent directement `useQuery` + `jarvisFetch('/api/...')` (helper dans `pwa/src/lib/api.ts`). Les hooks legacy ont été retirés au profit d'un fetching inline plus simple et plus lisible.

**Configuration** : la page `/config` du PWA appelle `/api/status` + `/api/integrations` en live pour afficher l'état réel du backend (modèles LLM, intégrations, audio, mémoire, computer access).

**Build production** : `npm run build` puis `npm start` (port 3000).

### Refonte UX PWA (juin 2026)

L'interface PWA a été entièrement repensée pour atteindre une qualité **iOS-native** :

**Parser briefing structuré** ([`pwa/src/lib/briefing-parser.ts`](pwa/src/lib/briefing-parser.ts)) — au lieu d'afficher le briefing comme un mur de texte brut, le contenu est parsé en sections typées :

- `emails` (compteur + flag urgent)
- `agenda` (texte)
- `priorities` (liste numérotée)
- `messages` (pills avec noms extraits depuis "(dont X, Y, Z)")
- `weather`
- `tasks` (en retard)
- `attention` (Point d'attention final, ton violet)
- `intro` (préambule éventuel)

Chaque section a son rendu visuel dédié dans [`BriefingCard.tsx`](pwa/src/components/dashboard/BriefingCard.tsx) avec icône Lucide (`Mail`, `Calendar`, `Target`, `MessageCircle`, `Cloud`, `AlertTriangle`, `Star`). **Aucun emoji** dans toute l'app.

**4 pages refondues** :

| Page | Contenu | Composants |
|---|---|---|
| `/dashboard` | header "Bonjour/Bonsoir, Elias." + briefing en cards + stats 2×2 (mails/urgents/events/tâches) + agenda timeline vertical + actions rapides 3×1 | `BriefingCard`, `StatCard`, `QuickAction` |
| `/mails` | badges total/non lus/urgents + banner IA violet (résumé extrait du briefing) + filter pills (Tout/Urgentes/À traiter/FYI) + liste notifs unifiée avec couleur par source/priorité | `MailItem`, `MailList`, `MailSummaryBanner`, `MailFilterPills` |
| `/tasks` | barre de progression dégradé bleu→vert + créateur rapide (input + pills priorité) + liste groupée (En cours / À faire / Terminées collapsibles) avec toggle done par tap | `ProgressBar`, `TaskCreator`, `TaskItem`, `TaskList` |
| `/config` | état live du backend en sections (Système, Intégrations, Audio, Mémoire, LLM, Watchers) avec badges ACTIF/INACTIF, refresh manuel, polling 30 s | section reusable + `IntegrationRow`, `MemStat` |

**Styles globaux** ([`pwa/src/app/globals.css`](pwa/src/app/globals.css)) : safe areas iOS (`env(safe-area-inset-*)`), animation `pageEnter` (200ms fadeIn) sur chaque navigation, `tabular-nums` partout sur les chiffres, scrollbar invisible, `100dvh` body, overscroll bloqué.

**Bottom nav** ([`BottomNav.tsx`](pwa/src/components/layout/BottomNav.tsx)) : 4 onglets (Dashboard / Mails / Tâches / Config) avec icônes Lucide, état actif en `text-[#4A9EFF]` + `strokeWidth` augmenté, `backdrop-blur-[30px]`, safe-area-inset-bottom respecté.

**Nettoyage** : 14 fichiers legacy supprimés (hooks `useMails`/`useCalendar`/`useTasks`/`useSummary`, composants `MorningSummary`/`StatsGrid`/`AgendaTimeline`/`QuickActions`/`PageHeader`/`StatusBar`/`PullToRefresh`, shared `Card`/`Badge`/`IconBox`/`Skeleton`, types orphelins, `lib/utils.ts`). Dependance `clsx` retirée (69 packages npm en moins).

**Tests** :

- [`pwa/scripts/test-parser.mjs`](pwa/scripts/test-parser.mjs) — 12 assertions sur le parser briefing (compile via le tsc local, importe le module ESM, valide structure + count + items + absence de marqueurs Markdown résiduels). `cd pwa && node scripts/test-parser.mjs`.
- [`pwa/scripts/test-endpoints.sh`](pwa/scripts/test-endpoints.sh) — 12 endpoints proxifiés testés (status, integrations, tasks, briefing, notifications, people, journal, patterns, calendar). `bash pwa/scripts/test-endpoints.sh`.

**Règles design strictes** :

- Police `-apple-system` (SF Pro sur iOS)
- Fond `#0a0a0f` partout, jamais de blanc
- Border `rgba(255,255,255,0.07)`, radius 18-20 px
- Skeleton pendant chargement, jamais d'écran blanc
- Erreur réseau = card rouge + bouton retry, jamais de crash
- `active:scale-95` sur tous les boutons tactiles
- Icônes Lucide uniquement, jamais d'emoji

### Localisation GPS PWA (juin 2026)

Le PWA envoie en continu la position de l'iPhone au backend via l'API `Geolocation` du navigateur, alimentant `LocationManager` (tables `location_history`, `places`, `visits`, `trips`, `location_patterns`).

**Service tracking** ([`pwa/src/lib/geolocation.ts`](pwa/src/lib/geolocation.ts)) :

| Fonction | Rôle |
|---|---|
| `startTracking()` | Démarre `watchPosition` + envoi immédiat de la position courante |
| `stopTracking()` | Stoppe le watcher, clear le watch ID |
| `isTracking()` | True si le watcher est actif |
| `checkPermission()` | Lit `navigator.permissions.query({ name: 'geolocation' })` |
| `requestPermission()` | Force le prompt navigateur (via un `getCurrentPosition`) |
| `sendCurrentPosition()` | Force un envoi immédiat (bouton refresh) |
| `getTrackingInfo()` | Stats : actif, dernier envoi, erreurs, intervalle, distance min |

**Garde-fous** :

- **Throttle** : pas plus d'un envoi toutes les **5 min** (sauf si `force: true`)
- **Distance minimum** : ignorer si le déplacement est < **30 m** (anti-bruit GPS)
- **Envoi forcé** au bout de 10 min même sans mouvement (heartbeat)
- **Auto-stop** sur `PERMISSION_DENIED` (code 1)

**Démarrage auto** dans [`pwa/src/app/client-layout.tsx`](pwa/src/app/client-layout.tsx) :

- Vérifie la permission via `navigator.permissions.query`
- Si `granted` → `startTracking()` immédiatement (pas de prompt intempestif)
- Si `denied`/`prompt` → attend l'activation manuelle depuis `/config`
- Écoute `result.onchange` : si l'utilisateur change l'autorisation dans Réglages Safari, le tracking suit automatiquement

**UI** :

- **Widget Dashboard** ([`LocationWidget.tsx`](pwa/src/components/dashboard/LocationWidget.tsx)) — affiche le lieu courant (nom ou "Position non nommée"), heure de dernière maj, durée passée à l'endroit, bouton refresh, liste des visites du jour. Bouton "Nommer cet endroit" inline avec input + validation Enter quand la position n'est rattachée à aucun lieu.
- **Section Config** ([`LocationConfig.tsx`](pwa/src/components/config/LocationConfig.tsx)) — toggle ON/OFF du tracking (déclenche `requestPermission()` au premier OFF→ON), état de la permission, stats (dernier envoi, erreurs, intervalle, distance min), liste des lieux connus avec compteur de visites, patterns géo détectés.

**Contrainte iOS Safari** : pas de background geolocation. Le tracking ne marche qu'au premier plan. Compensé par :

- Envoi immédiat à chaque ouverture du PWA
- Watcher actif tant que la page est visible
- Le Raccourci iOS legacy (POST `/api/location` avec `source: "shortcut"`) peut rester en complément — le backend dédoublonne par timestamp

**Catégories de lieux backend** (contrainte `CHECK` SQL) :
`home`, `school`, `work`, `gym`, `restaurant`, `shop`, `friend`, `family`, `medical`, `transport`, `leisure`, `other`. La PWA envoie `other` par défaut pour `name-current` ; à modifier ensuite via `PUT /api/places/{id}`.

**Test E2E** : [`pwa/scripts/test-location.sh`](pwa/scripts/test-location.sh) — 10 assertions sur les endpoints localisation (POST point, status, history, places, visits, trips, patterns, batch, name-current + cleanup DELETE).

```bash
bash pwa/scripts/test-location.sh
# => 10 / 10 OK
```

### Page Monitoring (`/monitoring` — frontend desktop `web/`)

Vue de test et monitoring complète, accessible depuis la sidebar (icône `Activity`). **3 onglets** :

- **Endpoints** : grille des ~25 endpoints REST principaux groupés par catégorie (Système, Mémoire, Contacts, Conversations, Tâches, Notifications, Briefing, Calendar, Localisation, Daemon, Fichiers). Chaque ligne affiche méthode, path, dernière latence, code HTTP, bouton **Test** + **Voir** (modal JSON). Bouton **Tout tester** exécute en parallèle par paquets de 4.
- **Features** : cards par intégration (Mail, Calendar, Météo, iMessage, Screen Watcher, Email Watcher, Agents LLM, TTS, STT, Localisation, Code Executor, Mémoire SQLite, Recherche). Chaque card affiche un badge état (vert / orange / rouge) + résumé textuel + bouton **Tester** + détails JSON repliable.
- **Live** : monitoring temps réel — polling `/api/status` toutes les 5 s, `/api/logs` toutes les 3 s, écoute WS `*` pour tous les événements. Compteurs (messages aujourd'hui, coût du jour, latence moyenne, taux d'erreur), sparkline de latence, stats mémoire SQLite, logs LLM récents, événements WebSocket. Bouton **Pause** / **Reprendre**.

Source : `web/src/app/components/views/MonitoringView.tsx`. Aucun build particulier — `pnpm run build` dans `web/` puis le backend sert le SPA.

### Nettoyage (juin 2026)

- **Helper AppleScript unifié** : `integrations/_applescript.py` centralise `run_applescript()` + `run_applescript_async()` + `OsascriptResult` dataclass. Utilisé par `mail.py`, `calendar_api.py`, `imessage.py`, `contacts.py`, `computer.py`, `notifications_macos.py`. ~150 lignes de duplication supprimées.
- **`agents/display_text.py`** : source unique pour `extract_leading_emotion()` + `strip_leading_emotion()` + `finalize_assistant_display_text()`. Supprime les 3 implémentations dupliquées dans `main.py`.
- **Dépendances retirées de `requirements.txt`** : `sentence-transformers`, `scipy`, `aiofiles` (jamais importés). `pytest` + `pytest-asyncio` déplacés dans `requirements-dev.txt`.
- **Scripts supprimés** : `scripts/get_teamviewer_code.py` (one-off jamais référencé), `scripts/test_calendar.py` (redondant avec `test_macos_permissions.py`).
- **PWA cleanup** : 11 fichiers morts supprimés (`pwa/src/lib/{db,push,push-client,cron,index}.ts`, `instrumentation.ts`, `stores/useAppStore.ts`, `components/summaries/*`, `PullToRefresh.tsx`, `credentials.json.example`). 8 deps npm retirées (`better-sqlite3`, `googleapis`, `node-cron`, `web-push`, `zustand`, et leurs `@types`). ~40 Mo de `node_modules` économisés.
- **Frontend desktop cleanup** : `web/src/app/services/api.ts` + `websocket.ts` (réexports morts) + `web/src/app/lib/api.ts` (fetchJson dupliqué) supprimés. Routes legacy `/vault` et `/ai` retirées. Doublons `updateTaskStatus` et `markNotificationRead` (deprecated) retirés de `api.ts`.

### Redémarrage complet (script)

Le script **`scripts/jarvis_full_restart.sh`** arrête proprement les processus en écoute sur le port backend (`WEB_PORT` lu depuis `.env`, défaut **8080** comme `config.py`) et, en mode dev, sur le port Vite (**5173**, surcharge possible via `VITE_DEV_PORT` dans `.env` si tu l’ajoutes). Il supprime ensuite les caches légers : répertoires `__pycache__` et fichiers `.pyc` hors `venv`, cache `web/node_modules/.vite`, `.pytest_cache`, puis relance le backend (et optionnellement le front).

```bash
chmod +x scripts/jarvis_full_restart.sh   # une fois

# Backend seul (premier plan)
./scripts/jarvis_full_restart.sh

# Backend + Vite (pnpm dev en arrière-plan ; arrêt auto du front à la sortie du backend)
./scripts/jarvis_full_restart.sh --dev

# Tout en arrière-plan (logs + PID dans data/.jarvis_restart/)
./scripts/jarvis_full_restart.sh --daemon
./scripts/jarvis_full_restart.sh --daemon --dev

# Redémarrage sans nettoyage des caches
./scripts/jarvis_full_restart.sh --no-clean
```

### Arrêt propre (tous les services)

Pour **arrêter** JARVIS sans relancer (backend FastAPI, workers intégrés iMessage / email watcher / daemon / scheduler, et Vite en mode `--dev`) :

```bash
cd /Users/zeldris/JarvisAPI
WEB_PORT=$(grep -E '^WEB_PORT=' .env 2>/dev/null | tail -1 | cut -d= -f2 | tr -d ' ')
WEB_PORT=${WEB_PORT:-8080}
VITE_PORT=${VITE_DEV_PORT:-5173}

# Libérer les ports (SIGTERM puis SIGKILL si besoin)
for port in "$WEB_PORT" 5173 8080; do
  pids=$(lsof -nP -iTCP:"$port" -sTCP:LISTEN -t 2>/dev/null || true)
  [[ -n "$pids" ]] && kill -TERM $pids 2>/dev/null; sleep 1
  pids=$(lsof -nP -iTCP:"$port" -sTCP:LISTEN -t 2>/dev/null || true)
  [[ -n "$pids" ]] && kill -KILL $pids 2>/dev/null
done

# Nettoyer les PID daemon (si lancé avec --daemon)
rm -f data/.jarvis_restart/backend.pid data/.jarvis_restart/web.pid
```

Équivalent rapide : la première moitié de `scripts/jarvis_full_restart.sh` (arrêt des ports) **sans** relancer `python main.py` — ou `Ctrl+C` si le backend tourne au premier plan.

**Workers arrêtés avec le backend** (un seul processus `main.py`) : bridge iMessage, email watcher, daemon (screen watcher, TTS, wake word), APScheduler. **Ollama** (`ollama serve`) et **agent distant** (`scripts/jarvis_agent.py`) sont des processus séparés : les arrêter à part si tu les avais lancés manuellement (`pkill ollama`, `pkill -f jarvis_agent`).

### Refonte personnalité (mai 2026)

Refonte complète du ton et de l'identité de JARVIS pour qu'il parle comme **le JARVIS d'Iron Man** (majordome britannique, concis, sec) au lieu d'un chatbot générique.

**Nouveaux fichiers / changements clés** :

- **`prompts/persona.txt`** (nouveau) — persona JARVIS commune. Injectée automatiquement en début de tous les system prompts via `BaseAgent.build_system_prompt()`. Règles : pas d'emoji, pas de "Quoi de neuf ?", pas de présentation comme un agent, 3 phrases max sur question simple, "Monsieur" avec ironie bienveillante.
- **`agents/__init__.py`** — `BaseAgent.build_system_prompt()` charge maintenant `prompts/persona.txt` puis concatène avec le prompt agent (`persona + "\n\n---\n\n" + agent_prompt`). Nouveau flag `inject_persona: bool = True` (désactivé sur `orchestrator` et `memory` qui ne parlent pas à l'utilisateur).
- **Tous les `prompts/*.txt`** réécrits :
  - Suppression de toute mention "Tu es l'agent X de JARVIS" → JARVIS est UNE entité.
  - Suppression des emojis dans les formats (briefing matin/soir, météo).
  - `info.txt` : format météo en texte pur, sans icônes.
  - `productivity.txt` : briefings en sections texte (`— EMAILS`, `URGENT/IMPORTANT/INFO` au lieu de pastilles colorées).
  - `coach.txt` : section "QUI TU ES" reformulée — toujours JARVIS qui parle, mode coaching.
  - `orchestrator.txt` / `memory.txt` : reformulés comme classifieur interne / système silencieux (pas de persona injectée).
- **`integrations/imessage.py`** :
  - Mémoire des 10 dernières réponses envoyées (`_recent_outgoing`) → anti-écho. Si l'utilisateur renvoie exactement le texte d'une réponse récente, on skip.
  - `await asyncio.sleep(1.0)` après chaque envoi pour laisser à Messages.app le temps d'écrire le message sortant dans `chat.db` (évite les conditions de course sur ROWID).
  - `last_check_rowid` mis à jour AVANT le traitement (déjà le cas dans `_get_new_messages`, doc renforcée).
  - Filtre SQL `is_from_me = 0` documenté comme CRITIQUE (sans lui, JARVIS retraiterait ses propres réponses → boucle).
- **Frontend (React sous `web/` ; référence historique `web/static/` + `web/templates/`)** :
  - Le badge dans le header chat affiche **"JARVIS"** en permanence (plus le nom de l'agent technique). Animation `.thinking` pour signaler une réponse en cours.
  - Les bulles assistant n'affichent plus le nom de l'agent (`meta.agent` ignoré). L'agent technique est conservé en `data-agent` HTML pour debug devtools uniquement.
  - Message d'accueil chat : "Bonjour Monsieur. Que puis-je faire pour vous ?" (au lieu de "Salut, je suis JARVIS").
  - CSS `.agent-badge` : suppression du `text-transform: lowercase` pour afficher "JARVIS" proprement.

**Résultat** : l'utilisateur parle à JARVIS. Toujours JARVIS. Le routing multi-agents reste intact côté code, mais devient transparent côté UX.

## Audio — ElevenLabs unifié

Un seul fournisseur pour STT et TTS : **ElevenLabs**. Plus de Whisper, de ffmpeg, de fichiers temporaires, ni de conversion audio.

### STT — ElevenLabs Scribe (API cloud)

- Accepte directement le **WebM/Opus** du navigateur — zéro conversion, zéro ffmpeg.
- Latence ~0.5s ; blobs < 1000 octets ignorés (bruit).
- Coût : inclus dans le forfait ElevenLabs.
- Configuration : `ELEVENLABS_API_KEY` dans `.env` (sert aussi pour le TTS).

### TTS — deux backends (Edge par défaut)

| Backend | Quand | Qualité / latence |
|---------|--------|-------------------|
| **Edge TTS** (`TTS_ENGINE=edge`, défaut) | Aucune clé requise ; Microsoft neural | Très faible latence (~200 ms réseau), voix `fr-FR-VivienneMultilingualNeural` par défaut |
| **ElevenLabs** (`TTS_ENGINE=elevenlabs`) | `ELEVENLABS_API_KEY` + `ELEVENLABS_VOICE_ID` | Meilleure nuance + émotions ; plus de latence (~1–2 s) ; modèle `eleven_multilingual_v2`, sortie `mp3_44100_128` |

**ElevenLabs setup** :
1. Créer un compte sur [elevenlabs.io](https://elevenlabs.io) (plan gratuit ou payant).
2. Choisir une voix française → copier le voice ID.
3. `.env` : `TTS_ENGINE=elevenlabs`, `ELEVENLABS_API_KEY=...`, `ELEVENLABS_VOICE_ID=...`.

**Streaming TTS** : avec ElevenLabs, les chunks MP3 arrivent au client en temps réel. Edge envoie généralement un seul bloc MP3.

### Système d'émotions (7 tags)

JARVIS commence chaque réponse par un tag `[emotion]` analysé par le système :

| Tag | Réglages ElevenLabs (stabilité / similarité / style) typiques |
|-----|----------------------------------------------------------------|
| `neutral` | 0.45 / 0.75 / 0.15 + `use_speaker_boost` |
| `warm` | plus de `style` (~0.40) |
| `serious` | stabilité plus haute |
| `concerned` | expressivité via `style` |
| `amused` | `style` élevé |
| `urgent` | stabilité + style modérés |
| `encouraging` | similaire à `warm` |

Avec Edge TTS, les émotions n’influencent pas la synthèse Microsoft (tag quand même utilisé côté UX / cohérence du modèle).

### Système d'actions vocales (7 types + lecture mails par contexte)

Quand l'utilisateur demande explicitement une action, JARVIS peut inclure un bloc `\`\`\`action {...}\`\`\`` en fin de réponse. Le serveur le parse, exécute l'action et envoie `{type: "action_result"}` au client.

**Mails (lecture)** : les questions « qui m’écrit », « que veut Untel », etc. utilisent **`[EMAILS_CONTEXT]`** injecté dans **`memory_context`** par l’orchestrateur (mots-clés boîte mail / catégories concernées). L’agent productivité a en plus **`emails_context`** via **`_collect_pro_context`**.

| Type | Exemple de demande | Résultat |
|------|--------------------|---------|
| `task` | "Crée une tâche acheter du lait" | Ligne créée en DB, badge UI |
| `reminder` | "Rappel demain 14h dentiste" | Tâche haute priorité |
| `mail` | "Envoie un mail à Pierre…" | Brouillon affiché, attente confirmation |
| `weather` | "Quel temps fait-il ?" | Données météo intégrées |
| `calendar` | "Mon agenda de la semaine" | Événements via Calendar.app |
| `mood` | "Je me sens à 7/10 aujourd'hui" | Enregistré dans `mood_log` |
| `note` | "Note : idée de projet…" | Épisode sauvé en mémoire |

**Mails (envoi)** : JARVIS demande toujours confirmation avant d'envoyer. La page `/voice` affiche une carte "Brouillon mail" avec les boutons **Envoyer** / **Annuler**.

### Optimisations de latence

- **STT cloud** : ElevenLabs Scribe, ~0.5s de latence, pas de modèle local à charger.
- **Max tokens vocal** : `VOICE_MAX_TOKENS` (défaut **500**) via `orchestrator.handle(..., voice_mode=True)` depuis `_process_message()` — même enrichissement que le chat texte (historique, mails, agents).
- **TTS streaming** : le serveur envoie les chunks MP3 (ElevenLabs) en séquence dans le même tour WebSocket ; le client accumule jusqu'à l'événement `speech_done`, puis rejoue un blob MP3 complet (sans MediaSource ; fiabilité navigateur maximale).
- **Parallélisme UX** : dès réception du JSON `transcript`, l'UI affiche ce que vous avez dit pendant que le LLM et le TTS tournent côté serveur.

### Page `/voice` — conversation mains libres (un clic)

Un seul bouton **Démarrer la conversation** lance tout le cycle. La fin de parole est détectée **côté client** avec `AnalyserNode` (VAD navigateur, pas de VAD serveur).

**Pipeline WebSocket**

```
Micro → MediaRecorder.start() (sans timeslice, un enregistrement par phrase)
→ Fin de parole détectée (silence ≥ VOICE_SILENCE_DURATION_MS, parole ≥ VOICE_MIN_SPEECH_MS)
→ stop() → onstop callback → Blob WebM complet (header + données)
→ WebSocket binaire (blobs > 2000 octets)
→ ElevenLabs Scribe (transcription directe du WebM, zéro conversion)
→ Claude Haiku (mode vocal, 200 tokens max) → réponse courte
→ TTS (ElevenLabs ou Edge) → audio MP3 → WebSocket binaire → playback
```

1. `conversation_start` → serveur répond `conversation_started` puis `listening` (timings envoyés depuis `config`).
2. Fin de parole → `stop()` du `MediaRecorder` ; **`onstop`** assemble le Blob WebM complet (un `MediaRecorder` neuf par phrase = header WebM toujours présent). Envoi en binaire.
3. Serveur met `is_processing`, envoie `processing`, transcrit (ElevenLabs Scribe, pas de ffmpeg), puis **`_process_message(..., voice_mode=True)`** (identique au chat — orchestrateur, actions, mails), renvoie `transcript`, `response`, puis éventuellement `action_result`.
4. Réponse courte + TTS → `speaking` + flux de bytes audio + `speech_done`.
5. Le client joue l'audio, puis envoie `done_playing` → serveur répond `listening` (boucle).

**Anti-écho** : tout binaire pendant `is_speaking` ou `is_processing` est ignoré côté serveur ; le client désactive analyse / vide les buffers tant que JARVIS parle ou traite (`echoCancellation`, `noiseSuppression`, `autoGainControl` sur `getUserMedia`). Le micro peut être arrêté pendant la lecture TTS selon l'implémentation du client.

**UI** : orb HUD (canvas ~60 fps, états inactif / écoute / traitement / parole), bandeau transcription utilisateur et réponse JARVIS (`action_result` en sous-titre badge), visualiseur type égaliseur (barres cyan micro / amber sortie), petit historique des derniers tours. Code : `web/src/app/components/pages/Voice.tsx` (classe équivalente `HandsFreeConversation`).

**À part** du chat « mode conversation continue » legacy (`conversation_mode` + segments) décrit encore dans FRONTEND_SPECS pour référence.

#### Écoute continue (enregistrement long, JARVIS silencieux)

Sur `/voice`, la carte **Écoute continue** (ambre) enregistre le micro sans réponse intermédiaire : `MediaRecorder.start(5000)`, envoi binaire au fil de l’eau, WebSocket `recording_start` / `recording_stop`. Pipeline : Scribe (un appel par chunk média) → Haiku + `continuous_extractor.txt` (découpe ~12k caractères) → Sonnet + `continuous_synthesizer.txt` → tâches, calendrier Apple, `user_facts`, `people`, épisode `recording`, notification bureau. Config : `RECORDING_MAX_DURATION_MIN`, `RECORDING_CHUNK_SIZE_MB`, `RECORDING_SUMMARY_ONLY`. Table SQLite `recordings` ; `GET /api/recordings`, `GET /api/recordings/{id}` ; section **Documents > ENREGISTREMENTS**.

#### Vocal & mails — derniers réglages (mai 2026)

- Injection `[EMAILS_CONTEXT]` dans `memory_context` depuis l’orchestrateur quand la question évoque la boîte mail (hors route productivité pure, où `_collect_pro_context` suffit).
- **Voix** : un seul pipeline — `_process_message()` dans `main.py` ; `voice_mode=True` → `orchestrator.handle(..., voice_mode=True)` (préfixe `[VOICE_MODE]`, `ctx["voice_mode"]`, `_route_task` évite Gemini si vocal).
- **`mail_read`** dans `actions.py` : `await get_unread` corrigé (bug executor + coroutine) ; la persona ne pousse plus cette action.
- **Chat stream** : ordre WebSocket corrigé — `response_clean` est envoyé **avant** `done`, afin que le client applique le texte final nettoyé tant que `assistantStreamIdRef` pointe encore sur la bulle en cours (évite les artefacts ```action``` / ```json``` / ```save``` et les tags `[emotion]` restés dans le flux chunké).
- **Page `/voice`** : le contenu de l’événement `response` est passé dans `cleanAssistantResponse()` comme sur le chat texte.

## Agents

| Agent          | Rôle                                          | Modèle           | Statut |
|----------------|-----------------------------------------------|------------------|--------|
| Orchestrateur  | Route chaque message vers le bon agent        | Haiku 4.5        | ✅ Phase 1 |
| Info           | Météo, web, questions factuelles, chat léger  | Haiku 4.5        | ✅ Phase 1 (sans intégrations externes) |
| École          | Cours, résumés, flashcards, exercices, devoirs | Sonnet 4.6 + Gemini CLI | 🚧 Phase 2 (agent OK, RAG/PDF à venir) |
| Productivité   | Email, calendar, tâches, briefings            | Sonnet 4.6 + Gemini CLI | ✅ Phase 4 |
| Life Coach     | Relations, émotions, patterns, décisions      | Sonnet / Opus    | ✅ Phase 5 |
| Journal        | Journal intime, extraction d'insights JSON    | Sonnet 4.6       | ✅ Phase 5 |
| Mémoire        | Mémoire transversale, détection patterns      | Haiku 4.5        | ✅ Phase 5 |

## État d'avancement

### ✅ Phase 1 — Fondations (terminée)

**Backend**
- `config.py` — chargement `.env`, mapping modèles par agent
- `llm.py` — client async Claude API (chat, chat_stream, quick_classify), prompt caching, tracking coûts
- `database/__init__.py` — schéma SQLite complet (12 tables) + helpers CRUD (save_message, life_profile, people, mood, patterns, etc.)
- `agents/__init__.py` — `BaseAgent` (load_prompt, build_system_prompt, _call_llm) + registry global
- `agents/orchestrator.py` — classification Haiku → dispatch vers l'agent ciblé, support streaming, build_context (life_profile + episodes + patterns + people)
- `agents/info.py` — agent par défaut Haiku (small talk + questions factuelles, intégrations météo/web à brancher en Phase 4)
- `main.py` — FastAPI + WebSocket `/ws` (streaming), routes `/`, `/api/status`, `/api/memory`, `/upload`

**Frontend**

- SPA actuelle sous **`web/`** : projet **Vite + React + TypeScript + Tailwind** (`pnpm build` → `web/dist`). Client REST + WebSocket conforme à `FRONTEND_SPECS.md`.
- Ancienne SPA éventuelle (fallback Jinja + fichiers sous `web/static/` / `web/templates/`), si vous les conservez en local hors dépôt uniquement.

**Configuration**
- `.env.example` — template complet documenté

**Test rapide :**
```bash
python main.py
# → ouvre http://localhost:8080
# → tape "Salut ça va ?" — le routeur classifie en INFO,
#    puis l'agent Info répond en streaming.
```

### 🔧 Extension — Support Gemini CLI

**Logique** : Claude reste le cerveau (orchestration, mémoire, conversation), Gemini est délégué pour le contenu long et autonome qui n'a pas besoin du contexte mémoire JARVIS. Économie de coûts API + parallélisation possible.

**`config.py`** :
- `GEMINI_CLI_PATH` (défaut `gemini`) — binaire à invoquer
- `GEMINI_MODEL` (défaut `gemini-2.5-pro`)
- `GEMINI_TASKS` — set des 9 types délégués : `exercise`, `dissertation`, `essay`, `code`, `report`, `summary_long`, `email_draft`, `file_generation`, `flashcards_bulk`
- `SCHOOL_OUTPUT_DIR` (défaut `./data/outputs/school`)

**`llm.py`** — nouvelles fonctions pour invoquer Gemini en **subprocess** (pas d'API HTTP) :
- `gemini_chat(prompt, system="")` → bloquant, prompt sur stdin, timeout 180s, gère `FileNotFoundError` (CLI non installée) et `TimeoutError` avec messages user-friendly. Retourne le même dict que `chat()` (`content`, `tokens_in=0`, `tokens_out=0`, `cost=0.0`, `model`, `stop_reason`).
- `gemini_chat_stream(prompt, system="")` → async generator qui yield chaque ligne de stdout au fur et à mesure (lecture via `process.stdout.readline()`).
- `classify_task_type(user_message)` → décide via Claude Haiku si la demande va à `"gemini"` (contenu long) ou `"claude"` (conversationnel/contextuel).
- Ajout de `config.GEMINI_MODEL` dans `MODEL_COSTS` avec `(0.0, 0.0, 0.0)` (gratuit via auth Google perso ou quota AI Studio).

**`.env.example`** — bloc `── Gemini CLI ──` avec `GEMINI_CLI_PATH=gemini` et `GEMINI_MODEL=gemini-2.5-pro`.

**`agents/__init__.py`** — `BaseAgent` enrichi pour le routing intra-agent :
- `_call_llm` renommé en `_call_claude` (alias `_call_llm = _call_claude` conservé pour rétro-compatibilité avec les agents existants)
- `_call_gemini(user_message, conversation_id, system="")` — délègue à `llm.gemini_chat`, persiste avec `cost=0.0`, log `"production terminée (gratuit)"`
- `_route_task(user_message, conversation_id, context, history)` — utilise `llm.classify_task_type` puis :
  - **Route Gemini** : Claude Haiku produit un brief court (type d'exo, matière, consignes, format, niveau BTS) → concaténé à la demande originale → envoyé à Gemini avec le system prompt JARVIS de l'agent
  - **Route Claude** : appel standard `_call_claude` avec contexte mémoire complet
- Logs `[agent_name] Route → Gemini CLI / Claude` pour debug

**`CLAUDE.md`** — nouvelle section `## Architecture dual-LLM — Claude (cerveau) + Gemini CLI (production)` insérée entre `Architecture multi-agents` et `Structure du projet`. Documente :
- Le rôle de Claude (routing, coaching, mémoire, journal, pré-analyse)
- Le rôle de Gemini CLI (devoirs, code, rédaction longue, fichiers, résumés longs, flashcards en masse)
- Tableau de routing exhaustif par type de tâche
- Flux type complet pour un exercice (Haiku classifie → Haiku brief → Gemini produit → parse `save` JSON → fichier sauvé)
- Règle simple dev : `>500 tokens ou génère un fichier → Gemini`, le reste → Claude
- Pointeurs vers `config.GEMINI_TASKS`, `BaseAgent._route_task()`, et les fonctions de `llm.py`

### ✅ Phase 2 — Agent École + Upload PDF + Documents UI (terminée)

**Backend**

- **`agents/school.py`** — `SchoolAgent(BaseAgent)` :
  - `handle()` délègue à `self._route_task()` (Claude pour analyse/fiche, Gemini CLI pour devoirs longs) puis détecte le bloc ` ```save ` et persiste le fichier
  - `handle_stream()` : pseudo-streaming (chunks de 20 chars + `await asyncio.sleep(0.01)` entre chaque) parce que Gemini subprocess ne stream pas en JSON. Yield `{type: classification|chunk|done|saved_file}`
  - `_save_school_file(response_text)` : regex `r"```save\s*\n(.*?)\n```"` (DOTALL) → parse JSON → slug ASCII de la matière (`Économie` → `economie` via `unicodedata.NFKD`) → crée `data/outputs/school/[matière]/` → écrit le devoir nu (tout ce qui précède le bloc save). Ajoute un en-tête `# {title}` auto pour les `.md`

- **`agents/orchestrator.py`** — `handle_stream()` détecte si l'agent ciblé expose `handle_stream` et lui délègue (sinon fallback sur `llm.chat_stream` Claude générique). C'est ce qui permet à l'agent école de router vers Gemini CLI tout en gardant l'UX streaming côté WebSocket.

- **`database/__init__.py`** — `save_school_document(title, content, doc_type, file_path)` + `get_school_documents(limit)`. Le second utilise `LENGTH(content)` pour ne pas charger les longs textes en mémoire.

- **`main.py`** :
  - `register_agent(school_agent)` au démarrage + création des dossiers `SCHOOL_OUTPUT_DIR` et `UPLOAD_DIR`
  - `POST /upload` refondu : extrait le texte des PDF avec **`fitz` (pymupdf)** boucle `page.get_text()`, lit directement les `.txt`/`.md`, sauvegarde brute pour les images (OCR Phase 4). Persiste dans `school_documents`. Retourne `{status, filename, size, content_length, doc_type, doc_id}`
  - `GET /api/outputs` : `rglob` récursif dans `data/outputs/school/`, retourne `[{filename, subject, path, size_kb, created_at}]` triés par date
  - `GET /api/outputs/{filepath:path}` : `FileResponse` avec **protection path traversal** (`target.relative_to(root)` lève `ValueError` si tentative d'évasion → HTTP 403)
  - `/api/memory` enrichi avec `school_documents`

**Frontend** — nouvelle section **Documents** :

- **`web/templates/index.html`** : 4ème onglet sidebar 📚 Documents avec drop zone + 2 grilles (fichiers produits / documents uploadés)
- **`web/static/style.css`** : `.drop-zone` (bordure pointillée, hover bleu, drag-over highlight), `.file-card` (grid responsive auto-fill 260px min, hover lift), `.subject-badge` (pill bleue lowercase)
- **`web/static/app.js`** :
  - Drag & drop natif (preventDefault sur dragover, classe `.dragover` toggle, drop → `uploadFiles(files)`)
  - `uploadFiles()` : POST séquentiel `/upload` avec `FormData`, message de status global avec compteur OK/erreur
  - `loadDocuments()` : fetch parallèle `/api/outputs` + `/api/memory`, render 2 grilles
  - Event WebSocket `saved_file` : affiche un message système dans le chat avec le chemin
  - Format de date FR `15 mars, 14:32`

**Test rapide** :
```bash
python main.py
# → http://localhost:8080
# → onglet Chat : "Fais-moi une dissertation de 3 pages sur la mondialisation"
#   → Haiku classifie SCHOOL → school._route_task → Gemini CLI produit → fichier sauvé
#   → message "📄 Fichier sauvé : school/economie/eco_dissertation_mondialisation.md"
# → onglet Documents : la dissertation apparaît, clic "Télécharger"
```

**`prompts/school.txt`** — capacités étendues pour l'agent École :
- Ajout des capacités 8 (faire un exo/devoir complet) et 9 (rédiger dissertation/étude de cas/rapport) dans la liste "CE QUE TU SAIS FAIRE"
- Nouvelle section `QUAND ON TE DONNE UN SUJET D'EXERCICE / DEVOIR` : workflow en 5 étapes (lire → identifier matière/niveau → chercher en mémoire → produire le travail prêt à rendre → bloc de sauvegarde)
- Spécification du **format de sortie fichier** : bloc ` ```save ` JSON avec `{action, filename, subject, type, title}` à la fin de chaque devoir produit
- Convention de nommage `[matière]_[type]_[sujet_court].md` rangé automatiquement dans `data/outputs/school/[matière]/` (cf. `SCHOOL_OUTPUT_DIR`)
- Règles de qualité BTS (vocabulaire technique, exemples concrets, prêt à rendre — pas un brouillon)

### ✅ Phase 3 — Audio + Conversation vocale (terminée)

**Voix naturelle dans les deux sens** avec émotions et mode conversation continue.

**Backend — STT + TTS**

- **`audio/stt.py`** — `STT` : ElevenLabs Scribe (API cloud). `transcribe(audio_bytes, language="fr")` via `httpx`. Accepte directement le WebM/Opus du navigateur — pas de conversion, pas de ffmpeg.
- **`audio/tts.py`** — `TTSEngine` : 2 backends :
  1. **ElevenLabs** — API REST via `httpx`, émotions via `voice_settings` (stability/style). Config : `ELEVENLABS_API_KEY` + `ELEVENLABS_VOICE_ID`.
  2. **Edge TTS** (gratuit, défaut) — voix `fr-FR-VivienneMultilingualNeural`.
- **`audio/__init__.py`** — réexporte `stt`, `tts`.

**VAD** : côté client uniquement (Web Audio API `AnalyserNode`). Détection de parole par volume en temps réel. Pas de `webrtcvad` ni de VAD serveur.

**Système d'émotions (7 émotions)** :

JARVIS adapte sa voix au contexte émotionnel de chaque réponse.

1. **`prompts/persona.txt`** : chaque réponse commence par un tag `[emotion]` sur la 1ère ligne (ex: `[warm]`, `[serious]`, `[amused]`). Ce tag n'est PAS affiché à l'utilisateur.
2. **`agents/__init__.py`** : `BaseAgent._extract_emotion(response)` parse le tag, retourne `(emotion, texte_sans_tag)`. Intégré dans `_call_claude()` et `_call_gemini()`.
3. **`audio/tts.py`** : `tts.synthesize(text, emotion="warm")` adapte les paramètres voix (ElevenLabs: stability/style ; Edge: ignoré).

Tags disponibles : `neutral`, `warm`, `serious`, `concerned`, `amused`, `urgent`, `encouraging`.

**Mode conversation continue** (composer chat — legacy) :

Un bouton « Mode conversation » à côté du micro dans l'onglet Chat. **La page dédiée `/voice`** est le flux principal. Quand ce mode legacy est activé :
- Le micro écoute en continu
- Détection de silence côté client → envoi automatique
- Le serveur transcrit (Scribe) → traite → TTS avec émotion → renvoie l'audio
- **Anti-écho** : le client stoppe le micro quand JARVIS parle, reprend à la fin (`done_playing` → `listening`)

**Config** (`.env.example`) :
```bash
ELEVENLABS_API_KEY=                   # STT + TTS
ELEVENLABS_VOICE_ID=                  # voix TTS ElevenLabs
TTS_ENGINE=edge                       # "elevenlabs" ou "edge"
```

### ✅ Phase 4 — Productivité (terminée)

JARVIS gère maintenant les emails, le calendrier (Apple natif), les tâches et la météo. Intégrations sans OAuth pour Mail/Calendar + agent dédié + briefings quotidiens + scheduler local.

**Backend — intégrations**

- **`integrations/weather.py`** — `WeatherClient` :
  - `get_current(city=None)` → `{city, temp, feels_like, description, humidity, wind_speed (km/h), icon (emoji)}` via OpenWeatherMap
  - `get_forecast(city=None, days=3)` agrège les prévisions 3h sur N jours (min/max + description/icône les plus fréquentes)
  - Mapping des codes météo OWM → emojis (`☀️ 🌤️ ⛅ ☁️ 🌧️ ⛈️ ❄️ 🌫️`)
  - Pas d'OAuth — juste `WEATHER_API_KEY` dans `.env`

- **`integrations/mail.py`** — `AppleMailClient` (AppleScript, zéro config) :
  - `is_available()` → vérifie Mail.app via `osascript`
  - `get_unread(max=20)` → `[{id, from, subject, date, snippet}]`
  - `get_unread_ids(max=100)` → `[str]` (IDs seuls, rapide, pour le snapshot backlog)
  - `get_message(id)` → message complet avec body (tronqué à 3000 chars)
  - `send(to, subject, body)` → envoi via Mail.app
  - `mark_read(id)` → marque lu via AppleScript
  - Tout passe par `_run_applescript()` + `loop.run_in_executor` (non-bloquant)
  - **Aucun OAuth, aucun token** — il suffit que Mail.app soit configuré

- **`integrations/calendar_api.py`** — `AppleCalendarClient` (AppleScript sur Calendar.app, zéro OAuth) :
  - `get_today_events()` / `get_week_events()` → liste `{summary, start (HH:MM), end, location, notes, calendar}`
  - `create_event(...)` crée un événement dans le calendrier choisi ou le premier disponible
  - `get_calendars()` liste les noms de calendriers
  - Permission Automation pour Calendar.app au premier `osascript`

- **`integrations/notifications_macos.py`** — notifications bureau (`display notification`) pour mails importants, briefings, patterns (voir code).
- **`scripts/scheduler.py`** — APScheduler : briefing matin à `MORNING_BRIEFING_TIME`, contrôle des tâches en retard chaque heure.
- **`integrations/__init__.py`** — imports conditionnels (`try/except`) : si une intégration échoue à s'initialiser (clé manquante, token absent), elle vaut `None` mais le serveur démarre

**Backend — agent + endpoints**

- **`agents/productivity.py`** — `ProductivityAgent(BaseAgent)` :
  - `_collect_pro_context()` collecte en parallèle (`asyncio.gather`) emails/calendar/météo + tâches DB, formate chaque en bloc lisible (`emails_context`, `calendar_context`, `weather_context`, `tasks_context`, `pro_context` avec date FR)
  - `handle()` enrichit le context avec ces données puis délègue à `self._route_task()` (Claude pour triage/résumé, Gemini CLI pour rédaction longue d'email)
  - `handle_stream()` : pseudo-streaming chunks de 20 chars + `await asyncio.sleep(0.01)` (compatible WebSocket)
  - `morning_briefing()` : assemble emails + calendar + tâches + météo, appelle Sonnet avec `prompts/productivity.txt` et le user message `"Génère le briefing du matin."`, sauvegarde dans `daily_briefings`
  - `evening_summary()` : récupère les conversations du jour (`get_daily_messages`) + tâches en cours, résumé via Sonnet, sauvegarde dans `daily_briefings.evening_summary` (UPSERT sur la date)

- **`database/__init__.py`** — nouveaux helpers :
  - `create_task(title, description, priority, due_date, category)` → id
  - `update_task_status(task_id, status)` → bool, remplit `completed_at` si `done`
  - `get_task(task_id)` → dict ou None
  - `get_tasks(status=None)` refondu avec tri par priorité (high < medium < low) puis date d'échéance, NULL en dernier
  - `get_daily_messages(date=None)` → tous les messages du jour
  - `save_daily_briefing(date, morning, evening)` → UPSERT sur la date

- **`main.py`** — register agent + 5 nouveaux endpoints :
  - `GET /api/integrations` → `{mail, calendar, weather, imessage, email_watcher}` (booléens)
  - `GET /api/briefing?kind=morning|evening` → génère et retourne le briefing
  - `GET /api/tasks?status=todo|doing|done` → liste filtrée
  - `POST /api/tasks` body `{title, description?, priority?, due_date?, category?}` → tâche créée
  - `PATCH /api/tasks/{id}` body `{status: "todo|doing|done"}` → tâche mise à jour

**Frontend — section Tâches + bouton briefing**

- **HTML** : 5ème onglet sidebar ✅ Tâches (form ajout rapide titre+priorité+bouton, 3 listes : En cours / À faire / Terminées en `<details>` accordéon) + bouton 📋 Briefing dans le header du chat
- **CSS** : `.task-form` grid (input flex + select + bouton accent), `.task-item` avec barre de priorité colorée à gauche (`.task-prio-high/medium/low` rouge/jaune/vert), `.task-cat` pill catégorie, `.integration-row` pour le status (badge ON/OFF vert/rouge)
- **JS** :
  - `loadTasks()` fetch parallèle `/api/tasks` + `/api/tasks?status=done`, render dans 3 conteneurs séparés selon le status
  - `renderTaskList()` génère les actions contextuelles : todo → "▶ Commencer" + "✓ Terminer", doing → "⏸ Pause" + "✓ Terminer", done → "↺ Rouvrir"
  - `updateTaskStatus(id, status)` PATCH puis reload
  - Création via `task-form` submit (POST + reload)
  - `renderStatus()` enrichi : nouvelle carte Intégrations qui appelle `/api/integrations` et affiche Gmail/Calendar/Météo + lignes STT/TTS de la Phase 3
  - Bouton briefing : POST `/api/briefing?kind=morning` → affiche la réponse comme bulle assistant

**Setup Mail (rien à faire)**

Les emails sont lus via Apple Mail (Mail.app) en AppleScript. Aucune configuration OAuth nécessaire.

1. Mail.app doit être configuré avec ton compte Gmail (ou tout autre provider)
2. Terminal/iTerm doit avoir la permission Automation (Réglages > Confidentialité > Automatisation)
3. La première fois, macOS demandera d'autoriser le contrôle de Mail.app — accepte

**Setup Calendar (rien à faire de plus que Mail)**

Calendar.app doit contenir tes calendriers (iCloud, Google, etc.). Réglages > Confidentialité > Automation pour Calendar au premier appel.

**Diagnostic Calendar (mai 2026)** :
- `integrations/calendar_api.py` réveille maintenant Calendar avant le check (`tell application "Calendar" to launch`) et capture `stdout` + `stderr` + `returncode` (timeout ~5–6s sur le check).
- Les scripts AppleScript ciblent désormais l’app par **bundle id** (`com.apple.iCal`) pour éviter les soucis de localisation, et un fallback `open -gj -b com.apple.iCal` existe si AppleScript renvoie `-600`.
- `GET /api/integrations` renvoie désormais `calendar: { available, error, details }` au lieu d’un simple booléen, pour voir exactement pourquoi ça échoue (ex: permission Automation refusée).
- Test rapide sans serveur : `python scripts/test_calendar.py` (affiche la sortie exacte AppleScript).

### ✅ Phase 5 — Life Coach + Journal + Mémoire (terminée)

**LE cœur de JARVIS — ce qui le rend personnel.** Trois agents qui transforment chaque conversation en mémoire vivante.

**Backend — agents**

- **`agents/coach.py`** — `CoachAgent` (Sonnet par défaut, **Opus** pour les sujets structurants) :
  - `_should_escalate()` : pré-check Haiku (5 tokens, temp 0.0) sur "ce sujet est-il structurant ? OUI/NON" → si OUI → switch automatique vers Opus
  - `_enrich_context()` injecte `people_context`, `patterns_context`, `mood_context` (7 derniers moods)
  - `_extract_people_mentions()` détecte les noms connus dans le message user via regex `\b{nom}\b` (case + accent insensitive grâce à `unicodedata.NFKD`) et met à jour `last_mentioned`
  - JAMAIS Gemini ici — le coaching exige tout le contexte mémoire JARVIS
  - Pseudo-streaming chunks 20 chars + `await asyncio.sleep(0.01)`

- **`agents/journal.py`** — `JournalAgent` (Sonnet) :
  - Reçoit du texte libre, répond brièvement (2-3 phrases) ET joint un bloc ` ```json``` ` avec `{mood, energy, people_mentioned, topics, key_insights, pattern_match, action_items}`
  - `_process_journal_data()` parse le bloc et alimente :
    1. `mood_log` via `save_mood(mood, energy, context=topics_joined)`
    2. `people` via `upsert_person()` + `add_people_event(name, "journal_mention", context)`
    3. `episodes` via `save_episode(agent="journal", importance=6, tags=topics)` pour chaque insight
    4. `patterns` via `find_or_create_pattern(pattern_match)` (incrémente si existe, crée sinon)
    5. `tasks` via `create_task(item, category="perso")` pour chaque action_item
  - Émet un event `journal_extracted` après le done streaming

- **`agents/memory.py`** — `MemoryAgent` (Haiku) — **silencieux, jamais d'interaction utilisateur directe** :
  - `process_conversation(conversation_id)` est appelé **en background** (`asyncio.create_task`) après chaque conversation > 2 messages
  - Construit un résumé brut de l'historique → envoie à Haiku avec `prompts/memory.txt` qui retourne un JSON `{should_store, episode, updates: {life_profile, people, mood, patterns, tasks}, pattern_alert}`
  - `_parse_and_apply()` applique TOUTES les updates en DB de façon idempotente
  - `weekly_summary()` (Sonnet pour qualité) : agrège épisodes + patterns + moods de la semaine → résumé structuré JSON → sauvegardé dans `weekly_summaries`

**Backend — DB (helpers Phase 5)**

- `add_life_profile_entry(category, content)`, `update_life_profile_entry(id, content)`, `delete_life_profile_entry(id)`, `get_life_profile_entries()` (avec ids pour édition UI)
- `add_people_event(person_id_or_name, event_type, content)` — résout par nom OU par id, crée la personne si absente
- `create_pattern(type, description)`, `update_pattern(id, increment)`, `find_or_create_pattern(description, type)` — déduplication par description identique
- `get_weekly_episodes(days=7)` — épisodes via `datetime('now', '-N days')`
- `save_weekly_summary(week_start, summary, patterns_spotted, recommendations)` — JSON sérialisé pour les listes

**Backend — endpoints**

| Route | Méthode | Description |
|---|---|---|
| `/api/life-profile` | GET | `{grouped, entries (avec ids)}` |
| `/api/life-profile` | POST | `{category, content}` → ajoute |
| `/api/life-profile/{id}` | PUT | `{content}` → met à jour |
| `/api/life-profile/{id}` | DELETE | supprime |
| `/api/people` | GET / POST | liste / upsert `{name, relationship?, dynamics?, …}` |
| `/api/people/{name}` | GET | fiche complète + events récents |
| `/api/people/{name}` | PATCH | met à jour la fiche (`name`, `relationship`, `personality_notes`, `dynamics`, `patterns`) — même identifiant par nom insensible à la casse ; nom déjà pris → 409 |
| `/api/people/{name}/analytics` | GET | métriques iMessage (`contact_analytics`, Python sans LLM) |
| `/api/people/{name}/timeline` | GET | timeline événements Haiku (`generate_timeline`) |
| `/api/people/{name}/send` | POST | `{text}` envoi iMessage |
| `/api/people/{name}/suggest-message` | POST | suggestion message Haiku |
| `/api/people/{name}/remind` | POST | `{when}` tâche « recontacter » |
| `/api/people/{name}/ask` | POST | chat contextualisé `{question}` (Sonnet + profil + timeline + iMessage si dispo) |
| `/api/journal` | GET | moods (30) + episodes journal (30) |
| `/api/journal` | POST | `{content}` → réponse + extraction JSON |
| `/api/patterns` | GET | patterns actifs |

Plus le **traitement mémoire en background** dans le WebSocket : à la fermeture de la connexion, si l'historique > 2 messages → `asyncio.create_task(_run_memory_in_background(conv_id))` qui ne bloque PAS le close.

**Frontend — section Mémoire refondue (4 onglets internes)**

- **Onboarding banner** : visible uniquement si `entries.length === 0` après chargement de `/api/life-profile`. Disparaît dès qu'on ajoute la 1ère entrée.

- **🌱 Life Profile** : 5 catégories (`values`, `goals`, `fears`, `patterns`, `strengths`) chacune avec :
  - Placeholder explicatif quand vide ("Ce qui compte le plus pour toi…")
  - Liste d'entrées avec ✏️ édition inline (input remplace le span, `Enter`/`blur` pour valider, `Esc` pour annuler) et 🗑️ suppression (avec `confirm()`)
  - Bouton `+ Ajouter` qui déploie un mini formulaire inline

- **👥 People** : grid responsive de cartes (`auto-fill minmax(280px, 1fr)`) avec nom + badge relation + dynamique + patterns + date dernière mention. Clic → expand pour fetch `/api/people/{name}` et afficher les événements récents. Form d'ajout en haut (nom + relation + notes).

- **📓 Journal** : textarea libre + bouton 📓 Enregistrer → POST `/api/journal` → affiche la réponse de Claude (sans le bloc ```json) en encadré accent. Mini-graph **mood des 14 derniers jours** : barres horizontales colorées (rouge ≤3, jaune ≤6, vert >6) avec hauteur proportionnelle au score, tooltip date+score+énergie. Liste des entrées récentes (importance, date, summary).

- **🔁 Patterns** : cartes triées par occurrences décroissantes, badge type, statut, dates premier/dernier vu, `×N` géant en accent. Patterns avec status `resolved` grisés (opacity 0.5).

**Setup recommandé**

```bash
python main.py
# → http://localhost:8080
# → onglet 🧠 Mémoire → Life Profile (banner d'onboarding)
# → remplir au moins 2-3 valeurs/objectifs/peurs (5 minutes)
# → c'est prêt — toutes tes conversations seront analysées en background
#   par l'agent mémoire qui détectera tes patterns au fil du temps
```

### 📱 Bridge iMessage (macOS uniquement)

JARVIS peut être contacté **depuis ton iPhone** via iMessage : tu envoies un message à ton propre numéro, JARVIS le traite (via l'orchestrateur, exactement comme depuis le web), et te répond directement dans la conversation iMessage.

**Comment ça marche** :
- **Lecture** : polling READONLY de `~/Library/Messages/chat.db` toutes les N secondes (par défaut 3s) sur la table `message` filtrée sur `is_from_me=0` + `handle.id = IMESSAGE_TARGET`
- **Envoi** : `subprocess.run(["osascript", "-e", script])` qui pilote Messages.app via AppleScript pour envoyer la réponse
- **Sécurité** : seuls les messages venant de `IMESSAGE_TARGET` sont traités (pas tes autres contacts), et un préfixe optionnel filtre encore plus strictement
- **Init au démarrage** : on saute tous les messages déjà présents (`MAX(ROWID)`), donc seuls les NOUVEAUX messages reçus pendant que JARVIS tourne sont traités

**`integrations/imessage.py`** — `IMessageBridge` :
- `is_available()` : check `chat.db` accessible (`sqlite3.connect(?mode=ro)` test)
- `_get_new_messages()` : SQL filtré + tracking de `last_check_rowid` pour éviter les doublons
- `_send_message(text)` : split en chunks de 2000 chars, escape AppleScript (`\\` `"` `\n`), envoi séquentiel
- `_apply_prefix_filter()` : si `IMESSAGE_PREFIX` défini, ne traite que les messages commençant par ce mot (regex `\b` insensible casse + ponctuation tolérée comme `"jarvis:"`, `"Jarvis,"`, `"jarvis -"`)
- `start_polling(interval)` : boucle async qui ne crash jamais (try/except en dur), arrêtable via `stop()` ou `task.cancel()`

**`config.py`** :
- `IMESSAGE_TARGET` (vide par défaut → bridge désactivé) — ton numéro `+33...` ou email iMessage
- `IMESSAGE_POLLING_INTERVAL` (défaut `3.0`) — secondes entre chaque check
- `IMESSAGE_PREFIX` (défaut vide) — si défini, ne réponds qu'aux messages commençant par ce mot

**`main.py`** :
- Au startup : si bridge dispo → `asyncio.create_task(imessage_bridge.start_polling(...))` en background
- Au shutdown : `bridge.stop()` + `task.cancel()`
- `/api/status` enrichi avec `imessage: {available, target, prefix}`
- `/api/integrations` enrichi avec `imessage`

#### Setup iMessage (UNE FOIS sur Mac)

```bash
# 1. Permission Full Disk Access (lecture chat.db)
#    Réglages Système > Confidentialité et sécurité > Accès complet au disque
#    → ajouter Terminal.app (ou iTerm/VSCode/Cursor selon où tu lances JARVIS)

# 2. Renseigner ton numéro dans .env
echo 'IMESSAGE_TARGET=+33612345678' >> .env
# (optionnel) Pour ne répondre qu'aux messages commençant par "jarvis" :
echo 'IMESSAGE_PREFIX=jarvis' >> .env

# 3. Lancer JARVIS
python main.py
# → log "iMessage bridge activé — écoute les messages de +33612345678"

# 4. Depuis ton iPhone, envoie un iMessage à ton propre numéro
#    "jarvis quel temps fait-il à Lille ?"
# → macOS demandera la permission Automation pour Messages.app au 1er envoi → accepter
# → JARVIS répond dans iMessage en quelques secondes
```

**Troubleshooting** :
- **"Pas d'accès à chat.db"** dans les logs → manque Full Disk Access pour le terminal qui lance JARVIS
- **`osascript` exit 1** → manque la permission Automation pour Messages.app (accordée au 1er envoi)
- **Messages non reçus** → vérifier que ton iPhone envoie bien en iMessage (bulle bleue) et pas SMS (verte)

### Email watcher proactif (mai 2026)

JARVIS surveille **en continu** ta boîte mail (Apple Mail) et agit de lui-même.

**Principe** : `scripts/email_watcher.py` poll les non-lus toutes les `EMAIL_CHECK_INTERVAL` secondes (défaut 120s). Chaque nouveau mail est envoyé à Claude Haiku (~$0.001/mail) qui décide seul s'il mérite d'être signalé. **Aucun filtre regex local** — Haiku décide tout.

**Deux types de mails notifiés, le reste est ignoré** :

| Haiku dit… | JARVIS fait |
|--|--|
| `reason: "payment"` (facture, prélèvement, virement…) | Notification priority=high + tâche `category="finance"` avec montant + alerte iMessage |
| `reason: "request"` (une vraie personne demande quelque chose) | Notification priority=medium + tâche `category="email"` + alerte iMessage |
| `reason: "ignore"` (newsletters, promos, notifs auto…) | Log silencieux, aucune action |

**Premier cycle au boot (rattrapage)** : au premier `_check_new_emails()` après démarrage, les non-lus du cycle (cap `MAX_UNREAD_PER_CYCLE`) déjà présents dans `email_summaries` sont seulement marqués comme vus ; ceux **absents** de la base sont analysés (Haiku) puis enregistrés, y compris les `reason: ignore` (priorité `low`, trace en DB). Hydratation du cache avec **`get_all_processed_email_ids()`** (tous les `gmail_id` connus). Après une longue coupure : `python scripts/catchup_after_downtime.py`, ou à chaud **`POST /api/email-watcher/catchup`** (réinitialise aussi le cache Mail après timeout). Mail.app doit répondre à AppleScript (timeout probe **90s** avec activation de Mail).

**Anti-doublons** : cache mémoire `last_processed_ids` + hydratation DB au boot (`get_all_processed_email_ids`) + UPSERT sur `email_summaries.gmail_id`.

**Coût** : ~$0.001/mail analysé. Body tronqué à 1500 chars, max_tokens=200, temperature=0.0.

**Notifications UI** : badge rouge sidebar, panneau dropdown, toasts auto pour urgent/high, polling 30s.

**Endpoints** : `GET /api/notifications`, `GET /api/notifications/all`, `POST /api/notifications/{id}/read`, `POST /api/notifications/read-all`, **`POST /api/email-watcher/catchup`** (rattrapage mail à la demande).

**Robustesse** : boucle `try/except` global, `MAX_UNREAD_PER_CYCLE=20`, parser JSON tolérant.

### Mémoire profonde (mai 2026)

6 nouvelles tables ajoutées dans `database/schema.sql` et `database/__init__.py` pour la mémoire profonde de JARVIS :

| Table | Rôle | Helpers CRUD |
|-------|------|-------------|
| `user_facts` | Faits extraits des conversations (préférences, habitudes, infos perso) avec versioning (`is_current`, `superseded_by`) | `add_fact`, `get_facts`, `get_all_facts_summary`, `invalidate_fact`, `search_facts` |
| `relationship_profiles` | Profils relationnels enrichis par personne (style de communication, dynamique, attachement, confiance) | `upsert_relationship_profile`, `get_relationship_profile`, `get_all_relationship_profiles` |
| `relationship_events` | Timeline des événements relationnels (date, type, impact, leçons) | `add_relationship_event`, `get_relationship_timeline` |
| `cross_insights` | Insights croisés entre personnes/domaines (patterns transversaux) | `add_cross_insight`, `get_active_insights`, `increment_insight` |
| `life_context` | Contextes de vie actifs (période d'exams, déménagement, etc.) avec impact sur mood/productivité | `add_life_context`, `get_active_life_context`, `close_life_context` |
| `imessage_analysis_cache` | Curseur d'analyse iMessage par handle (évite de ré-analyser les anciens messages) | `get_analysis_cursor`, `update_analysis_cursor` |

Fonction agrégateur `build_full_context()` : construit le contexte complet structuré (facts + life_profile + patterns + life_context + moods + people_profiles + cross_insights + episodes) pour injection dans les prompts Sonnet.

### Lecteur iMessage pour analyse relationnelle (mai 2026)

Nouveau module `integrations/imessage_reader.py` — accès **READONLY** à `~/Library/Messages/chat.db` pour l'analyse relationnelle par le `RelationshipAnalyzer`. Distinct du bridge iMessage (`integrations/imessage.py`) qui gère le polling temps réel + envoi.

**`IMessageReader`** :
- `is_available()` — vérifie l'accès à `chat.db` (connexion `?mode=ro`)
- `get_all_contacts()` — contacts uniques avec nombre de messages et dernière date
- `get_conversation(handle, limit, since_rowid)` — messages d'un contact depuis un ROWID donné (analyse incrémentale)
- `get_conversation_with(name_or_handle, limit)` — recherche par nom ou numéro (match partiel case insensitive)
- `search_messages(query, limit)` — recherche `LIKE` dans tous les messages

Singleton : `imessage_reader` instancié au chargement du module.

**`prompts/imessage_extractor.txt`** — prompt d'extraction JSON structuré pour analyser une conversation iMessage. Retourne : profil relationnel (`relationship_guess`, `sentiment`, `power_dynamic`, `trust_level`), faits atomiques sur l'utilisateur et le contact, événements notables, et patterns observés. Utilisé avec Haiku/Sonnet via le `RelationshipAnalyzer`.

### Analyseur relationnel — worker background (mai 2026)

Nouveau fichier `scripts/relationship_analyzer.py` — worker background qui utilise Haiku pour extraire des données structurées des conversations iMessage et les stocker en DB.

**Architecture 2 tiers** :
- **Tier 1 (Haiku)** : lit les messages bruts iMessage → extrait du JSON structuré → stocke dans la DB (~$0.002/analyse)
- **Tier 2 (Sonnet/Opus)** : ne voit JAMAIS les messages bruts, raisonne uniquement avec les données structurées de la DB

**`RelationshipAnalyzer`** — 3 modes d'exécution :
- `run_initial_scan()` — première analyse complète de tout l'historique iMessage (skip les contacts avec < 10 messages ou déjà analysés)
- `run_daily_update()` — analyse incrémentale des nouveaux messages depuis le dernier cursor (skip si < 5 nouveaux messages)
- `analyze_single_contact(name_or_handle)` — analyse à la demande d'un contact spécifique, retourne sa fiche `people`

**Pipeline par batch** : les messages sont découpés en batches de 50, chaque batch est envoyé à Haiku avec le prompt `prompts/imessage_extractor.txt` (placeholders `{{user_name}}`, `{{handle}}`, `{{messages}}`). Le JSON retourné est parsé (bloc ```json ou fallback JSON brut) puis stocké en DB via :
1. `upsert_person` — crée/met à jour la fiche personne
2. `upsert_relationship_profile` — profil relationnel enrichi (style, dynamique, confiance, topics, sentiment)
3. `add_fact` — faits extraits sur l'utilisateur (source `imessage`)
4. `add_relationship_event` — événements notables (source `imessage`)
5. `find_or_create_pattern` — patterns relationnels observés

**Analyse incrémentale** : chaque batch met à jour le cursor via `update_analysis_cursor(handle, last_rowid, count)` pour éviter de ré-analyser les anciens messages.

Singleton : `analyzer = RelationshipAnalyzer()` instancié au chargement du module.

**Orchestrateur enrichi** : `build_context()` dans `agents/orchestrator.py` utilise maintenant `build_full_context()` pour assembler un contexte dense avec `[USER_FACTS]`, `[PEOPLE]` (avec profils relationnels), `[CROSS_INSIGHTS]`, `[LIFE_CONTEXT]`, et `[MOOD]` en plus du life profile, épisodes et patterns existants. Ce contexte est caché via prompt caching.

**Agent mémoire enrichi** : `agents/memory.py` stocke aussi dans les nouvelles tables :
- `facts_learned` → `add_fact()` (faits atomiques catégorisés)
- `life_context_change` → `add_life_context()` (changement de phase de vie)
- `cross_insights` → `add_cross_insight()` (patterns multi-personnes)
- `add_event` significatif → `add_relationship_event()` (timeline relationnelle)

**Nouveaux endpoints** :
- `POST /api/analyze-contact` : body `{name}` → lance l'analyse Haiku d'un contact iMessage, retourne le profil mis à jour
- `GET /api/relationship/{name}` : profil complet d'un contact (person + relationship_profile + timeline d'événements)

**Intégration au lifespan** : au démarrage, si iMessage est disponible, un scan initial est lancé en background (`run_initial_scan`). L'analyse quotidienne incrémentale peut être programmée via le scheduler (3h du matin).

### Contexte conversationnel (mai 2026)

JARVIS garde maintenant le fil de la conversation : les **30 derniers messages** de la session WebSocket en cours sont injectés dans chaque appel LLM.

**Problème résolu** : chaque message était traité de manière isolée — JARVIS oubliait ce qu'on venait de lui dire 10 secondes avant. Il répond maintenant en tenant compte de tout l'historique de la session.

**Implémentation** :

- **`agents/orchestrator.py`** — nouvelle méthode `_build_history(conversation_id, limit=30)` :
  - Lit les N derniers messages de la conversation via `get_conversation_history()`
  - Filtre : uniquement `user` et `assistant` (pas de `system`), contenu non vide
  - Exclut le dernier message user (c'est celui en cours — il sera ajouté par `_call_claude`)
  - Retourne une liste `[{role, content}, ...]` prête pour l'API Claude
  - Appelée dans `handle()`, `handle_stream()` (texte et vocal via `voice_mode`)
  - L'historique est stocké dans `ctx["history"]`

- **`agents/__init__.py`** — `_call_claude()` modifié :
  - Lit `context["history"]` et l'injecte comme messages précédents avant le message utilisateur
  - Fallback sur le paramètre `history` (rétrocompatibilité)
  - `build_system_prompt()` ignore la clé `"history"` (pas un placeholder prompt)

- **`main.py`** — `_process_message()` (pipeline unique WebSocket) :
  - Passe `conversation_id` à `orchestrator.handle_stream()` / `orchestrator.handle(..., voice_mode=...)`
  - Audio mains libres et push-to-talk utilisent la même fonction avec `voice_mode=True` (pas de `handle_voice`)

- **`database/__init__.py`** — `get_conversation_history()` : `ORDER BY created_at ASC` explicite

**Flux résultant** :
```
User envoie message N
  ↓
_process_message sauve le message user en DB
  ↓
orchestrator._build_history(conversation_id)
  → SELECT les 30 derniers messages (ORDER BY created_at ASC)
  → filtre user/assistant, exclut le dernier user (= message N)
  → retourne [{role: "user", content: "msg 1"}, {role: "assistant", content: "rép 1"}, ...]
  ↓
ctx["history"] = historique
  ↓
agent._call_claude(message_N, context=ctx)
  → messages = ctx["history"] + [{role: "user", content: message_N}]
  → Claude voit tout le fil de conversation
```

**Coût** : les 30 messages historiques sont envoyés comme tokens input à chaque appel. Avec prompt caching sur le system prompt (life profile + mémoire), le surcoût reste modéré (~1000-3000 tokens input supplémentaires par message).

### ⏳ Phases suivantes (raffinements)
- **Phase 2bis** (École) : embeddings sur les `school_documents` + recherche RAG, OCR images, flashcards SRS
- **Phase 3bis** (Audio) : wake word "Jarvis…", streaming TTS via `chat_stream`
- **Phase 4bis** (Productivité) : briefings auto via launchd (cron macOS), création d'événements depuis le chat
- **Phase 5bis** (Mémoire) : embeddings sur les épisodes pour recherche sémantique, résumé hebdomadaire automatique le dimanche soir, alerte UI quand `pattern_alert` est important

### Contrôle ordinateur (macOS)

- **`integrations/computer.py`** : `ComputerControl` — commandes shell via `asyncio.create_subprocess_shell` (timeout, `cwd` home, `is_safe` sur motifs dangereux : `rm -rf /`, `shutdown`, `curl|bash`, etc.), raccourcis `open -a`, AppleScript, `pbpaste` / `pbcopy`, `pmset`, Wi‑Fi `airport -I`, `df`, recherche `find`.
- **`actions.py`** : types `terminal`, `open_app`, `find_file`, `clipboard`, `system_info`. Les commandes sensibles (`rm`, `mv` vers `~/`, `sudo`, `brew uninstall`) renvoient `needs_confirmation: true` jusqu’à exécution avec confirmation (le client envoie un message WebSocket `action_confirm` qui repasse l’action avec `confirmed: true` côté serveur).
- **`main.py`** : après exécution, types `terminal` / `find_file` / `system_info` / `clipboard` déclenchent une **2e passe** orchestrateur pour reformuler le résultat (événement `response_followup`, pas d’affichage brut du stdout).
- **API** : `/api/status` et `/api/integrations` incluent `computer: { available, shell }`.
- **Config** : `COMPUTER_ACCESS`, `COMPUTER_SHELL`, `COMPUTER_TIMEOUT` (documentés dans `.env.example`).

### Exécution de code avancée (mai 2026)

JARVIS intègre un moteur d'exécution de code avancé capable de traduire des instructions en langage naturel en code, de l'exécuter, et de debugger automatiquement si une erreur survient. Tout est transparent pour l'utilisateur : c'est JARVIS qui sait coder.

- **`integrations/code_executor.py`** : wrapper `CodeExecutor` (singleton `code_executor`). Exécution async dans un thread, patterns dangereux bloqués (suppression système, format disque, shutdown, fork bomb), timeout configurable, reset automatique de la conversation entre exécutions.
- **Routing intelligent** (`actions.py`) : le choix entre `computer.run()` (subprocess basique) et `code_executor.execute()` (moteur avancé) est automatique. Si l'action contient `complex:true` ou si le texte est du langage naturel (détecté par `_is_natural_language()`), le moteur avancé prend la main.
- **2e passe enrichie** (`main.py`) : `_format_action_result_for_followup()` inclut désormais le code exécuté, l'output structuré, les erreurs et le résumé pour une reformulation naturelle par l'orchestrateur.
- **Persona enrichie** (`prompts/persona.txt`) : nouvelles instructions pour les tâches complexes (écrire un script, debugger un projet, déployer un serveur, analyser des données) avec le flag `complex:true`.
- **API** : `/api/status` et `/api/integrations` incluent `code_executor: { available, engine }`.
- **Config** : `CODE_EXECUTOR_ENABLED`, `CODE_EXECUTOR_TIMEOUT`, `CODE_EXECUTOR_MODEL` (documentés dans `.env.example`).

### Calendrier Apple, notifications bureau, session mémoire (mai 2026)

- **Calendar.app** : `integrations/calendar_api.py` — lecture/création via AppleScript, sans OAuth (calendriers déjà dans l’app macOS).
- **Notifications bureau** : `DESKTOP_NOTIFICATIONS` et `NOTIFICATION_SOUND` (`.env`) pilotent les bandeaux `display notification`.
- **`integrations/notifications_macos.py`** : alertes système pour mails importants (email watcher), briefing matin généré par le scheduler, pattern mémoire à la 3e occurrence, tâches en retard (job horaire ; au plus une notif par tâche et par jour civil).
- **`scripts/scheduler.py`** : APScheduler au démarrage FastAPI (`start_scheduler` / `shutdown_scheduler`).
- **Mémoire cross-session** : `get_last_conversation_summary()` injectée dans `config.PRIOR_SESSION_SUMMARY` → section `[DERNIÈRE_SESSION]` dans le contexte orchestrateur après reconnexion WebSocket.
- **Accueil** : événement WebSocket `welcome` (Haiku) la première connexion du jour — contextualise mood, retards, mails, agenda.
- **`/api/status`** : champ `memory` avec compteurs SQLite (`count_memory_stats`).
- **Robustesse** : `_process_message` enveloppé dans `try/except` global ; AppleScript (Mail, Calendar, Messages, `computer.run_applescript`) : timeout 30s et une retry si timeout.
- **Actions** : `execute_action` logue `ok=` après chaque branche (succès ou échec parsé).

## Architecture

```
Input texte (WebSocket)
   ↓
Orchestrateur (Haiku, ~50 tokens)
   → classifie en SCHOOL | PRODUCTIVITY | COACH | INFO | JOURNAL
   ↓
Agent spécialisé (Haiku / Sonnet / Opus selon)
   → reçoit system prompt avec [LIFE_PROFILE + MEMORY] caché (cache_control: ephemeral)
   → reçoit les 30 derniers messages de la conversation comme historique
   → stream la réponse via WebSocket
   ↓
Persistance : messages + cost trackés en SQLite
```

## Endpoints

| Route                          | Méthode | Description                                                                       |
|--------------------------------|---------|-----------------------------------------------------------------------------------|
| `/`                            | GET     | SPA (5 sections : Chat / Tâches / Documents / Mémoire / Status)                   |
| `/ws`                          | WS      | Chat temps réel — JSON (`text`, `action_confirm`, …) + binaires audio ; streaming, `action_result`, `response_followup`, TTS MP3 |
| `/api/status`                  | GET     | Stats jour, modèles, agents, audio, iMessage, email_watcher, **`computer`**, **`memory`**, **`location`** |
| `/api/memory`                  | GET     | Life profile + people + episodes récents + `school_documents`                     |
| `/api/recordings`              | GET     | Liste des enregistrements continus (résumé + compteurs d’actions)                  |
| `/api/recordings/{id}`         | GET     | Détail : transcription, synthèse JSON, actions                                   |
| `/upload`                      | POST    | Upload doc scolaire — extrait texte PDF (`fitz`) → `school_documents`             |
| `/api/outputs`                 | GET     | Liste des fichiers produits dans `data/outputs/school/`                           |
| `/api/outputs/{filepath:path}` | GET     | Télécharge un fichier produit (path traversal protégé)                            |
| `/api/integrations`            | GET     | `mail`, `calendar`, `weather`, `imessage`, `email_watcher`, **`computer`**, **`location_tracking`**      |
| `/api/briefing?kind=morning`   | GET     | Génère le briefing du matin (ou `kind=evening` pour le soir)                      |
| `/api/tasks`                   | GET     | Liste des tâches (filtre `?status=todo\|doing\|done`)                             |
| `/api/tasks`                   | POST    | Crée une tâche `{title, description?, priority?, due_date?, category?}`           |
| `/api/tasks/{id}`              | PATCH   | Met à jour le status `{status: "todo\|doing\|done"}`                              |
| `/api/life-profile`            | GET     | Life profile groupé + entrées avec ids                                            |
| `/api/life-profile`            | POST    | `{category, content}` → ajoute une entrée                                         |
| `/api/life-profile/{id}`       | PUT/DELETE | Édite ou supprime                                                              |
| `/api/people`                  | GET / POST | Liste / upsert d'une personne                                                  |
| `/api/people/{name}`           | GET / PATCH | Fiche complète + events récents / mise à jour partielle (renommage, relation…) |
| `/api/people/{name}/analytics` | GET     | Métriques iMessage (Python pur : proximité, tendance, sujets, patterns…)       |
| `/api/people/{name}/timeline`  | GET     | Timeline événements Haiku sur l’historique (365 j / 500 msg max)              |
| `/api/people/{name}/send`      | POST    | `{ "text": "..." }` → envoi iMessage via Messages.app                         |
| `/api/people/{name}/suggest-message` | POST | Suggestion courte Haiku pour répondre au contact                       |
| `/api/people/{name}/remind`    | POST    | `{ "when": "..." }` → tâche catégorie `relation`                               |
| `/api/people/{name}/ask`       | POST    | Chat contextualisé sur le contact `{ "question": "..." }` — erreurs en JSON si échec |
| `/api/journal`                 | GET     | Moods + épisodes journal                                                          |
| `/api/journal`                 | POST    | `{content}` → réponse + extraction JSON                                           |
| `/api/patterns`                | GET     | Patterns actifs                                                                   |
| `/api/notifications`           | GET     | Notifications non lues (triées par priorité urgent → low)                         |
| `/api/notifications/all`       | GET     | Historique complet `?limit=50`                                                    |
| `/api/notifications/{id}/read` | POST    | Marque une notification comme lue                                                 |
| `/api/notifications/read-all`  | POST    | Marque toutes les notifications comme lues                                        |
| `/api/analyze-contact`         | POST    | `{name}` → analyse Haiku d'un contact iMessage                                   |
| `/api/relationship/{name}`     | GET     | Profil complet : person + relationship\_profile + timeline                        |
| `/api/location`                | POST    | Point GPS `{latitude, longitude, …}` → `LocationManager.process_location`          |
| `/api/location/batch`          | POST    | `{"points":[…]}` — points avec `timestamp` optionnel (tri chronologique)           |
| `/api/places`                  | GET/POST | Liste lieux nommés / création `{name, category, latitude, longitude, …}`       |
| `/api/places/{id}`             | PUT/DELETE | Mise à jour (`radius` → `radius_meters`) / suppression                         |
| `/api/places/{id}/stats`       | GET     | Stats visites (fréquence par jour, heures moyennes)                               |
| `/api/location/status`         | GET     | Position récente, visite en cours                                                |
| `/api/location/history`        | GET     | Historique points `?hours=24`                                                     |
| `/api/visits`                  | GET     | Visites récentes `?days=7`                                                        |
| `/api/visits/today`            | GET     | Visites du jour civil                                                             |
| `/api/trips`                   | GET     | Trajets récents `?days=7`                                                         |
| `/api/location/patterns`       | GET     | Patterns géographiques actifs                                                     |
| `/api/location/name-current`   | POST    | Crée un lieu au dernier point GPS `{name, category?}`                           |
| `/api/control/services`       | GET     | Liste tous les services (interne + externe) avec état et contrôlabilité        |
| `/api/control/{service}/start` | POST    | Démarre un service (audio_daemon, email_watcher, jarvis_daemon, screen_watcher, imessage_bridge, scheduler, relationship_analyzer, ollama, tv_dashboard) |
| `/api/control/{service}/stop`  | POST    | Arrête un service                                                              |
| `/api/control/{service}/restart` | POST  | Redémarre un service (stop + pause 1s + start)                                 |
| `/api/control/restart-all`     | POST    | Redémarre tous les services internes séquentiellement                           |
| `/api/control/stop-all`        | POST    | Arrête tous les services internes                                               |
| `/api/control/start-all`       | POST    | Démarre tous les services internes                                               |
| `/api/control/{service}/logs`  | GET     | Dernières lignes de log filtrées par tag `?lines=30`                            |

## Coût estimé

~$10-15/mois pour un usage solo quotidien avec prompt caching activé sur le bloc `[LIFE_PROFILE + MEMORY]` (~90% de réduction sur les tokens input cachés).

## Stack

- **Backend** : Python 3.12 + FastAPI + WebSocket (uvicorn)
- **DB** : SQLite (fichier `data/jarvis.db`)
- **LLM** : Claude API (Haiku / Sonnet / Opus) + Gemini CLI (subprocess, gratuit)
- **Frontend** : HTML + CSS + JS vanilla (pas de framework, MediaRecorder API pour le micro)
- **Audio** : ElevenLabs Scribe (STT cloud) + TTS ElevenLabs ou Edge TTS (défaut) ; VAD côté client (Web Audio API)
- **PDF** : `pymupdf` (`fitz`) pour l'extraction texte
- **Productivité** : Apple Mail + Apple Calendar (AppleScript) + OpenWeatherMap (`httpx`)
- **Embeddings (à venir)** : `all-MiniLM-L6-v2` local pour le RAG des cours

## Résultats de test — 3 mai 2026

Checklist exhaustive exécutée automatiquement (API, WebSocket, DB, logs).

### Score global

| Catégorie | OK | BUG | Non testable | Absent |
|-|-|-|-|-|
| Démarrage | 6 | 0 | 0 | 2 |
| Chat texte | 7 | 0 | 0 | 0 |
| Routing agents | 5 | 0 | 0 | 0 |
| Agent École | 2 | 0 | 5 | 0 |
| Agent Coach | 3 | 0 | 0 | 0 |
| Agent Journal | 3 | 1 | 0 | 0 |
| Mémoire | 8 | 0 | 1 | 0 |
| Mémoire profonde | 5 | 0 | 0 | 0 |
| iMessage | 4 | 0 | 0 | 0 |
| Emails | 7 | 0 | 0 | 0 |
| Voix | 0 | 0 | 5 | 0 |
| Tâches | 4 | 0 | 0 | 0 |
| Notifications | 4 | 0 | 1 | 0 |
| Briefings | 4 | 1 | 0 | 0 |
| Status | 5 | 0 | 0 | 0 |
| Persona | 5 | 0 | 0 | 0 |
| Robustesse | 3 | 0 | 1 | 0 |
| **Total** | **75** | **2** | **13** | **2** |

### Bugs identifiés

1. **Briefing : tag `[neutral]` visible** — `morning_briefing()` / `evening_summary()` dans `agents/productivity.py` appellent `llm.chat()` directement, pas `self._call_claude()`. Le tag émotion n'est pas strippé et apparaît au début du briefing affiché.
2. **Journal : bloc JSON visible dans le chat** — `handle_stream()` dans `agents/journal.py` envoie le texte complet y compris le bloc ` ```json...``` `. Le frontend (`Chat.tsx`) ne strip pas ce bloc avant affichage.

### Fonctionnalités absentes

3. **Scheduler (APScheduler/cron)** — Les briefings matin (07:30), soir (22:00) et résumé hebdomadaire ne se déclenchent pas automatiquement. Déclenchement uniquement via UI ou `/api/briefing`.
4. **Carnet Contacts macOS** — Pas d'intégration Contacts.app pour résoudre les numéros iMessage en noms. Le `+33767787818` reste un numéro au lieu d'afficher un prénom.

### Non testables (nécessitent un navigateur)

- Push-to-talk, mode conversation mains-libres, VAD, orbe canvas
- Reconnexion WebSocket après coupure réseau
- Toasts notifications en temps réel
- Gemini CLI (non installé sur la machine de test)

### Points forts confirmés

- **Persona JARVIS impeccable** : 0 emoji, 0 chatbot, 0 mention d'agent, ton majordome avec « Monsieur »
- **Routing 5/5 correct** : SCHOOL, COACH, JOURNAL, PRODUCTIVITY, INFO tous bien classifiés
- **Contexte conversationnel fonctionnel** : les 30 derniers messages sont injectés, JARVIS cite correctement les messages précédents
- **Email watcher robuste** : backlog ignoré, anti-doublons, 37 emails analysés, 14 notifications créées, tâches auto-générées
- **Mémoire profonde active** : 39 user_facts, 13 profils relationnels, 113 événements, 25 patterns détectés via scan iMessage

---

## Module IA Personnelle — Conversations persistantes (Claude-like)

### Vue d'ensemble

Interface de chat avec système de conversations persistantes, auto-titrage Haiku, recherche cross-conversations, upload de documents, et navigation sidebar à la façon Claude — mais dans le design system BIG BROTHER (glassmorphism, noir/blanc, JetBrains Mono).

### Backend

#### Nouvelles tables DB

**`conversations`** — colonnes ajoutées via migration `ALTER TABLE` idempotente :
- `title TEXT` — titre auto-généré par Haiku ou édité manuellement
- `pinned BOOLEAN` — épinglée en haut de sidebar
- `archived BOOLEAN` — archivée (hors liste principale)
- `tags TEXT` — JSON array de tags
- `last_message_at DATETIME` — mise à jour après chaque échange
- `message_count INTEGER` — compteur mis à jour après chaque échange

**`conversation_documents`** — documents attachés à une conversation :
- `conversation_id` — FK vers conversations (CASCADE DELETE)
- `filename`, `original_name`, `file_path`, `file_type`, `file_size`
- `extracted_text` — texte extrait (PDF, txt…) injecté automatiquement dans le contexte
- `summary` — résumé Haiku (2-3 phrases)

#### Helpers DB (`database/__init__.py`)

| Fonction | Description |
|---|---|
| `get_conversations(limit, archived)` | Liste triée par `last_message_at` |
| `get_conversation_detail(conv_id)` | Messages + documents inclus |
| `update_conversation(conv_id, **kwargs)` | Met à jour title, pinned, archived… |
| `update_conversation_activity(conv_id)` | Rafraîchit `last_message_at` + `message_count` |
| `delete_conversation(conv_id)` | Supprime messages + docs + conversation |
| `search_conversations(query, limit)` | LIKE sur titres et contenu des messages |
| `save_conversation_document(...)` | Enregistre un doc attaché |
| `get_conversation_documents(conv_id)` | Liste les docs d'une conversation |

#### Auto-titrage Haiku (`_maybe_title_conversation`)

Appelée via `asyncio.create_task(...)` après chaque message, cette fonction génère un titre court (3-6 mots) avec Haiku dès que la conversation a ≥ 2 messages et pas encore de titre. Exemple : `"Révision exam droit"`, `"Planning semaine"`.

#### Documents dans le contexte

Quand des documents sont attachés à une conversation, leur texte extrait (jusqu'à 3000 chars chacun) est automatiquement préfixé au message de l'utilisateur dans `_process_message()`, sous la forme `[DOCUMENT: nom_fichier]`.

#### Action SEARCH_CONVERSATIONS

JARVIS peut chercher dans toutes les conversations passées quand l'utilisateur dit "on avait parlé de...", "cherche dans nos conversations sur...". L'action déclenche `search_conversations()` puis une 2e passe LLM pour reformuler les résultats naturellement.

#### WebSocket multi-conversations

Nouveaux types de messages supportés :
- `{"type": "switch_conversation", "conversation_id": 42}` → change la conversation active, charge l'historique
- `{"type": "new_conversation"}` → crée une nouvelle conversation
- Serveur envoie `{"type": "conversation_switched", "conversation_id": X, "title": "..."}` en retour
- Serveur envoie `{"type": "conversation_updated", "conversation_id": X, "title": "...", "message_count": N}` après chaque message

#### Endpoints API

| Route | Méthode | Description |
|---|---|---|
| `/api/conversations` | GET | Liste (params: `archived`, `limit`) |
| `/api/conversations/search` | GET | Recherche (param: `q`) |
| `/api/conversations/{id}` | GET | Détail + messages + documents |
| `/api/conversations/{id}` | PATCH | Renommer, épingler, archiver |
| `/api/conversations/{id}` | DELETE | Suppression complète |
| `/api/conversations/{id}/archive` | POST | Archiver |
| `/api/conversations/{id}/pin` | POST | Basculer épinglé |
| `/api/conversations/{id}/upload` | POST | Upload document (PDF, txt, md, etc.) |

### Frontend (`web/src/app/components/views/ChatView.tsx`)

#### Layout 2 colonnes

- **Sidebar gauche (272px)** : bouton "Nouvelle conversation", barre de recherche, liste groupée (Épinglées / Aujourd'hui / Hier / 7 jours / Plus anciennes), menu contextuel (Renommer, Épingler, Archiver, Supprimer)
- **Zone de chat droite** : header avec titre éditable + compteur + épingle, messages scrollables avec streaming progressif, composer avec drop zone + textarea auto-resize + commandes slash

#### Commandes slash

- `/nouveau` — nouvelle conversation
- `/cherche [texte]` — recherche dans les conversations
- `/briefing` — briefing matin
- `/tâche [texte]` — créer une tâche

#### Upload de documents

Drag & drop ou bouton ⊕. Les fichiers supportés (PDF, txt, md, csv, json, py, js, ts, html, css) sont uploadés via `POST /api/conversations/{id}/upload`, analysés par Haiku, et leur texte est injecté dans le contexte des prochains messages.

#### Écran de bienvenue

Quand une conversation est vide, affiche le logo JARVIS + 4 suggestions cliquables ("Résume mes mails non lus", "Planifie ma semaine", "Aide-moi à réviser", "Analyse mon humeur ce mois").

#### Navigation

La page Chat (`/chat`) est l'entrée par défaut de l'application. Accessible depuis la sidebar BIG BROTHER.

---

## Module Cartographie — Page `/map` (mai 2026)

Visualisation géospatiale des lieux, trajets et habitudes de déplacement.

### Composant : `web/src/app/components/views/MapView.tsx`

Layout deux colonnes identique au design system BIG BROTHER (glassmorphism, noir/blanc, Inter + JetBrains Mono).

#### Sidebar gauche (~320px)

- **Titre** : "CARTOGRAPHIE" uppercase + sous-titre font-mono
- **Toggles Heatmap / Routes** : même style que les filtres Contacts (actif = fond blanc texte noir)
- **Stats rapides** : nombre de lieux + visites aujourd'hui (2 cartes glassmorphism côte à côte)
- **Activité 7 jours** : barres horizontales par jour de semaine (données réelles `GET /api/visits?days=7`), tooltip hover avec nombre + durée totale
- **Lieux fréquents** : top 8 triés par `visit_count`, indicateur d'intensité (point blanc/gris), clic → illumine le marqueur SVG correspondant
- **Patterns détectés** : liste des patterns depuis `GET /api/location/patterns`

#### Zone carte (SVG, flex-grow)

Carte schématique entièrement en SVG sans dépendance externe (pas de Mapbox/Google Maps) :

| Couche | Description |
|--------|-------------|
| Fond | Grille blanche 40×40px, opacité 0.03 |
| Heatmap | Cercles concentriques proportionnels à `visit_count`, toggle ON/OFF |
| Routes | Lignes pointillées animées entre lieux liés par des trajets, épaisseur ∝ fréquence |
| Marqueurs | Cercle principal + glow + anneaux pulsants si sélectionné, label apparaît au hover |
| Position | Marqueur bleu pulsant "Vous êtes ici" si `current_location` disponible (poll 30s) |

**Projection** : fonction `projectToSVG` normalise les coordonnées GPS dans l'espace SVG avec 12% de padding.

**Zoom** : boutons +/− qui modifient `zoomLevel` appliqué via `transform: scale()`.

#### Panel info lieu

Overlay coin supérieur droit (300px), apparaît au clic sur un marqueur ou un item de la liste :
- Emoji catégorie + nom + ID formaté `#0001`
- Stats : visites totales, durée moyenne
- Détails : catégorie, dernière visite, adresse, coordonnées
- Actions : Renommer (`PUT /api/places/{id}`), Supprimer (`DELETE /api/places/{id}`)

#### Autres fonctionnalités

- **"Nommer cet endroit"** : bouton visible si position actuelle hors lieu nommé → `POST /api/location/name-current`
- **Formulaire ajout manuel** : slide-down en bas de carte pour ajouter un lieu avec nom, catégorie, latitude, longitude → `POST /api/places`
- **État vide** : message d'onboarding + bouton "Ajouter un lieu manuellement" si aucun lieu en base
- **Responsive** : sous 720px, sidebar passe au-dessus de la carte (layout vertical)

### Navigation

Accessible via `NavLink` "Cartographie" dans la sidebar BIG BROTHER (`/map`).

---

## Module Documents — Page `/documents` (mai 2026)

Gestion centralisée des uploads, fichiers produits et enregistrements.

### Composant : `web/src/app/components/views/DocumentsView.tsx`

Page pleine largeur avec sections empilées verticalement. Design system BIG BROTHER (glassmorphism, noir/blanc, font-mono).

| Section | Description |
|---------|-------------|
| **Header** | Titre + sous-titre + toggle grille/liste + compteur `X fichiers • Y MB` |
| **Zone upload** | Drop zone dashed, drag & drop + clic → `POST /upload`, barre de résultats avec ✓/✗ auto-dismiss 5s |
| **Stats** | 4 cartes : fichiers produits, docs uploadés, enregistrements, stockage total |
| **Fichiers produits** | `GET /api/outputs` — grille ou tableau, badge matière, bouton télécharger (`/api/outputs/{path}`) |
| **Documents chargés** | `GET /api/memory → school_documents` — icône selon `doc_type`, taille en chars |
| **Enregistrements** | `GET /api/recordings` — expand au clic → `GET /api/recordings/{id}` : résumé, synthèse, badges actions, transcription repliable |

Accessible via `NavLink` "Documents" dans la sidebar BIG BROTHER (`/documents`).

---

## Module Statistiques — Page `/analytics` (mai 2026)

Tableau de bord analytique complet avec graphiques Recharts.

### Composant : `web/src/app/components/views/AnalyticsView.tsx`

| Section | Source | Visualisation |
|---------|--------|---------------|
| Métriques principales | `GET /api/status` + `GET /api/people` | 4 cartes glassmorphism |
| Humeur & Énergie | `GET /api/journal → moods[]` | `ComposedChart` : courbe blanche (mood) + courbe grise (énergie) + aire dégradée |
| Contacts par type | `GET /api/people` | `PieChart` donut avec total au centre + légende |
| Tâches statut + priorité | `GET /api/tasks` | 2 `BarChart` empilés |
| Activité conversations | `GET /api/conversations` | `BarChart` 7 jours |
| Top contacts | `GET /api/people → message_count` | Barres horizontales custom (divs) |
| Patterns actifs | `GET /api/patterns` + `GET /api/location/patterns` | Cartes compactes avec badge couleur |
| Mémoire JARVIS | `GET /api/status → memory` | Grille 3×2 de mini-cartes |
| Top lieux | `GET /api/places` | Barres horizontales custom |
| Coûts API | `GET /api/status → today` | 3 métriques + barre bicolore input/output |

**Sélecteur de période** : toggle 7j / 30j / 90j / Tout — filtre côté client les moods, tâches, patterns et conversations. Les contacts et lieux restent non filtrés.

**Tooltip glassmorphism** uniforme pour tous les graphiques Recharts : fond noir 92% opacité, blur 16px, bordure blanche 10%.

**Palette monochrome** : blanc (#fff), gris clair (#a1a1a1), gris moyen (#6b7280), gris foncé (#374151).

Accessible via `NavLink` "Statistiques" dans la sidebar BIG BROTHER (`/analytics`).

---

## Corrections — Page Contacts (mai 2026)

Campagne de tests et corrections systématiques des 15 fonctionnalités de la page Contacts.

### Bugs corrigés

| Test | Fonctionnalité | Problème | Correction |
|------|---------------|---------|------------|
| TEST 3 | Analytics (score, sentiment, topics, last_exchanges) | `days=90` trop court — messages Bertille datent de 18+ mois | `compute_all()` : défaut porté à `days=730` + fallback progressif (365→730→1825) |
| TEST 4 | Sentiment heatmap | Données vides à cause du days=90 | Corrigé via TEST 3 |
| TEST 7 | Derniers échanges iMessage | Idem days=90 | Corrigé via TEST 3 |
| TEST 8 | Sujets récurrents | Parasité par les réactions tapback ("a aimé", "a adoré", "a ajouté") et les URLs | Filtrage des réactions + stop-words enrichis (ajouté, adoré, aimé, https, toujours, encore…) |
| TEST 9 | Messages non répondus | Idem days=90 | Corrigé via TEST 3 |
| TEST 11 | Suggestion de message | `days=30` ultra-court → analytics vides | Porté à `days=365` |
| TEST 13 | Timeline Haiku | `days=365` insuffisant (messages > 1 an) | Porté à `days=730` dans `timeline_generator.py` |
| TEST 15 | Événements récents | `get_person()` ne lisait que `people_events` (vide) alors que les vraies données sont dans `relationship_events` | Merge des deux tables dans `get_person()`, lecture `summary` en priorité sur `content` |
| TEST 15 | Affichage événements (frontend) | `ev.content` non défini pour `relationship_events` ; date `event_date` non affichée | Frontend lit `ev.summary \|\| ev.content` et `ev.event_date \|\| ev.created_at` |
| Graphique | Historique des interactions | Données mock figées | Remplacé par les données réelles `analytics.trend.months` (avec fallback mock si non disponible) |

### Fonctionnalités confirmées OK

| Test | Fonctionnalité | Statut |
|------|---------------|--------|
| TEST 1 | Liste contacts (tri, filtre, recherche) | ✅ 89 contacts, tri `last_mentioned` DESC, filtre catégorie et recherche nom fonctionnels |
| TEST 2 | Résolution handle iMessage | ✅ Résolution via `relationship_profiles.handle` (chaîne en 4 étapes) |
| TEST 5 | Description IA | ✅ Génération + cache `ai_description` |
| TEST 6 | Chat contextuel (ask) | ✅ Réponse Sonnet avec profil + événements + derniers messages |
| TEST 10 | Envoi iMessage | ✅ Endpoint correct (refus texte vide, résolution handle) |
| TEST 12 | Rappel de contact | ✅ Crée tâche catégorie `relation` |
| TEST 14 | Renommer un contact | ✅ PATCH `/api/people/{name}` avec gestion collision 409 |

### Ajouts

- **`GET /api/debug/resolve/{name}`** — endpoint de diagnostic pour inspecter la résolution de handle d'un contact.
- **`contact_analytics.compute_all()`** — fallback automatique vers des fenêtres plus larges si aucun message dans la période demandée.

---

## Pipeline unifié — JARVIS a accès à TOUT (mai 2026)

### Principe

`_process_message()` dans `main.py` est **le seul point d'entrée** pour parler à JARVIS. Texte, voix, recherche, contacts, journal — tout passe par ce pipeline. JARVIS a toujours accès à l'ensemble des données disponibles, quelle que soit la page.

### Nouvelles fonctions dans `main.py`

**`_build_enriched_context(text, conversation_id) → dict`**
Construit le contexte enrichi à partir de toutes les sources :
- *Permanent* : documents attachés à la conversation en cours
- *Conditionnel* (détection par mots-clés) : mails, calendar, météo, tâches, localisation, documents scolaires, enregistrements, conversations passées

**`_process_message_internal(text, conversation_id, voice_mode) → dict`**
Pipeline JARVIS complet sans WebSocket. Utilisé par les endpoints REST. Appelle `_build_enriched_context`, l'orchestrateur, exécute les actions (avec 2e passe), sauvegarde le message, auto-titre la conversation. Retourne `{text, emotion, action, agent, model, cost}`.

**`_process_message(ws, text, conv_id, ...)` (modifié)**
Enveloppe maintenant `_build_enriched_context` + orchestre + envoi WebSocket + TTS. Plus de logique de contexte dupliquée.

### Endpoints mis à jour

| Endpoint | Avant | Après |
|---|---|---|
| `POST /api/journal` | `journal_agent.handle()` direct | `_process_message_internal()` → orchestrateur route vers JOURNAL automatiquement |
| `POST /api/people/{name}/ask` | `llm.chat()` direct avec prompt spécialisé | `_process_message_internal()` avec message enrichi (profil + events + iMessage) ; fallback LLM si exception |

### Page Recherche (`/search` — `SearchView.tsx`)

Nouvelle page avec deux modes :
- **Recherche rapide** : filtrage côté client en temps réel (conversations, contacts, tâches, documents). Mise en évidence des termes dans les résultats. Filtres par catégorie.
- **Demander à JARVIS** : envoi de la question via `ws.sendText()`. Réponse JARVIS en streaming affichée au-dessus des résultats filtrés. JARVIS peut utiliser ses actions (`search_conversations`, `imessage_search`, `find_file`) pour fouiller dans toutes les données.

### Page Voix (`/voice` — `VoiceView.tsx`)

Mode mains libres complet. Pipeline : micro → VAD local → MediaRecorder (WebM/Opus) → WebSocket binaire → backend STT/LLM/TTS → MP3 binaire → playback Audio.

**UI :**
- Orbe animée Canvas qui réagit au volume du micro (couleurs : **cyan** = écoute, **ambre** = JARVIS parle, **violet** = réflexion, **gris** = inactif)
- Sélecteur TTS en haut à droite (dropdown glassmorphism) : Edge / ElevenLabs / Apple (Mac) — connecté aux endpoints `GET /api/settings/tts` et `PATCH /api/settings/tts`
- Zone de transcription en bas (dernier transcript utilisateur + réponse JARVIS en streaming progressif)
- Bouton central "Démarrer la conversation" / "Arrêter" (style terminal)

**Logique Web Audio :**
- `getUserMedia({ audio: { echoCancellation, noiseSuppression, autoGainControl } })`
- `AudioContext` → `AnalyserNode` → boucle `requestAnimationFrame` pour calcul RMS en temps réel
- **VAD local** : seuil de volume (0.015), silence > 1200ms après parole → `mediaRecorder.stop()` → envoi blob WebM complet via `ws.sendBinary()`
- Relance automatique du `MediaRecorder` après chaque envoi (sauf si JARVIS parle ou traite)

**Anti-écho strict :**
- `processing` / `speaking` → enregistrement coupé, VAD ignoré
- Chunks MP3 reçus du serveur accumulés via `ws.onBinary()` puis assemblés en blob à `speech_done`
- Playback via `new Audio(URL.createObjectURL(blob))`
- `audio.onended` → envoie `{ type: "done_playing" }` au WebSocket → réactive le micro

**Cleanup :** `useEffect` cleanup coupe le micro, ferme l'AudioContext, annule le RAF au démontage.

**Robustesse & diagnostic (mai 2026) :**
- `AudioContext` via `window.AudioContext || window.webkitAudioContext` ; `resume()` appelé dans le handler du bouton **Démarrer** (geste utilisateur).
- `getUserMedia` dans `try/catch` : message d’erreur affiché (bannière + `alert` si refus / pas de micro).
- Avant tout envoi critique : `ws.isSocketOpen()` ; `websocket.ts` expose `isSocketOpen()` et `send` / `sendBinary` / `sendText` ne font rien si la socket n’est pas `OPEN` (retour `false`).
- **Panneau Debug** (bouton fixe en bas à droite) : état micro, état AudioContext, jauge VAD 0–255 (`getByteTimeDomainData`), WebSocket connecté/déconnecté, dernier événement WS, horodatage du dernier envoi de blob audio.
- Logs console : `[VAD] Volume détecté`, `[VAD] Silence prolongé…`, `[VAD] Blob enregistré…`, `[WS] Envoi du blob audio…`.
- **MediaRecorder** : `audio/webm;codecs=opus` → fallback `audio/webm` → `audio/mp4` (Safari) selon `isTypeSupported`.

### WebSocket singleton

`ws.connect()` est appelé **une seule fois** dans `BigBrotherLayout.tsx` (useEffect au montage). Retiré de `ChatView.tsx`. Tous les composants partagent la même connexion via le singleton `ws` exporté par `websocket.ts`.

---

## Module Données (`/data` — `DataView.tsx`)

Page de monitoring des bases de données et d'export. 8 sections :

1. **Overview** — 4 cartes : enregistrements total, 6 bases de données, chiffrement AES-256, intégrations actives
2. **Bases de données** — 6 cartes (Messages & Conversations, Contacts & Relations, Faits & Connaissances, Patterns & Insights, Localisation, Documents & Médias) avec compteurs depuis `status.memory` et badge "Sain"
3. **Répartition stockage** — barre horizontale segmentée en niveaux de gris proportionnelle aux compteurs + légende pastilles + compteurs + pourcentages
4. **Intégrations** — état de chaque intégration (Apple Mail, Calendar, Météo, iMessage, Email Watcher, STT, TTS, Contrôle Mac) depuis `/api/integrations` + `status.audio`
5. **Agents** — grille des 6 agents enregistrés avec point vert et description hardcodée
6. **Modèles LLM** — tableau Haiku/Sonnet/Opus/Gemini avec model ID en font-mono et rôle
7. **Activité récente** — 10 dernières notifications depuis `/api/notifications` avec icône par source + timestamp relatif
8. **Export** — bouton qui agrège status, contacts, journal, tâches, patterns, lieux et conversations en un JSON → téléchargement `jarvis-export-YYYY-MM-DD.json`

Bouton "Synchroniser" en haut recharge toutes les données (icône animée). Responsive 2×2 sur mobile pour les cartes overview.

---

## Polish général — Tests fonctionnels complets (mai 2026)

### Rapport de tests

| Composant | Statut | Notes |
|-----------|--------|-------|
| Build TypeScript | ✅ | 0 erreurs, bundle 858 KB |
| Démarrage backend | ✅ | DB initialisée, 6 agents, scheduler, iMessage, email watcher |

### Pages testées

| Page | Route | Statut | Vérifications |
|------|-------|--------|---------------|
| Chat | `/chat` | ✅ | Conversations chargées, suggestions, input message, WebSocket streaming |
| Contacts | `/contacts` | ✅ | Liste 89 contacts, détails avec analytics, description IA, événements |
| Cartographie | `/map` | ✅ | État vide correct avec message explicatif et bouton ajout lieu |
| Documents | `/documents` | ✅ | 2 docs affichés, zone upload visible, toggle grille/liste |
| Statistiques | `/analytics` | ✅ | Tous les graphiques Recharts rendus, période sélectionnable |
| Recherche | `/search` | ✅ | Toggle mode rapide/JARVIS, filtres par catégorie, résultats groupés |
| Données | `/data` | ✅ | 8 sections complètes, export JSON fonctionnel |

### Infrastructure

| Élément | Statut | Détails |
|---------|--------|---------|
| Navigation | ✅ | 8 entrées sidebar (Chat, Dashboard, Contacts, Carto, Documents, Stats, Recherche, Données) |
| WebSocket | ✅ | Singleton connecté au montage (BigBrotherLayout), partagé entre toutes les pages |
| Notifications | ✅ | Badge dans header, 8 notifications affichées |
| Erreurs console | ✅ | 0 erreurs JavaScript de l'app JARVIS |
| API endpoints | ✅ | 11/11 fonctionnels |

### Endpoints vérifiés

```
✅ /api/status      — OK
✅ /api/people      — 89 contacts
✅ /api/tasks       — 45 tâches
✅ /api/patterns    — 910 patterns
✅ /api/journal     — OK
✅ /api/life-profile — OK
✅ /api/outputs     — 0 fichiers produits
✅ /api/notifications — 8 notifications
✅ /api/conversations — 50 conversations
✅ /api/places      — 0 lieux
✅ /api/integrations — mail=true, weather=true, imessage=true, email_watcher=true
```

### Points d'amélioration identifiés (non-bloquants) — CORRIGÉS

1. **Titres conversations** : ✅ CORRIGÉ — `_maybe_title_conversation()` déclenche maintenant l'auto-titrage dès qu'il y a 1 message user + 1 message assistant (au lieu de >= 2 messages quelconques)
2. **Compteur messages contacts** : ✅ CORRIGÉ — `/api/people` retourne maintenant `message_count` via une nouvelle colonne `imessage_count` dans la table `people`, synchronisée depuis `imessage_analysis_cache` à chaque analyse
3. **Noms contacts** : ✅ AMÉLIORÉ — `relationship_analyzer` utilise le `likely_name` extrait par Haiku pour renommer automatiquement les contacts dont le nom est un numéro de téléphone. Nouvelle fonction `rename_person_if_phone_number()` dans `database/__init__.py`
4. **Dashboard** : ✅ VÉRIFIÉ — Route `/dashboard` pointe correctement vers le composant `Dashboard.tsx` qui est fonctionnel avec appels API réels

---

## Améliorations implémentées (mai 2026)

### 1. Auto-titrage plus agressif

**Fichier** : `main.py`

`_maybe_title_conversation()` vérifie maintenant la présence d'au moins 1 message user ET 1 message assistant avant de générer le titre, au lieu de simplement vérifier `len(msgs) >= 2`.

```python
has_user = any(m.get("role") == "user" for m in msgs)
has_assistant = any(m.get("role") == "assistant" for m in msgs)
if not (has_user and has_assistant):
    return
```

### 2. Compteur messages contacts

**Fichiers** : `database/__init__.py`, `scripts/relationship_analyzer.py`

- Nouvelle colonne `imessage_count INTEGER` dans la table `people`
- Migration automatique via `_migrate_people_imessage_count()`
- `get_people_sorted_by_recent()` retourne maintenant `message_count` (utilise `imessage_count` ou fallback sur le nombre d'events)
- `sync_imessage_counts_to_people()` synchronise les compteurs depuis `imessage_analysis_cache` vers `people`
- Appelé automatiquement à la fin de `run_initial_scan()` et `run_daily_update()`

### 3. Renommage automatique des contacts numéro → nom

**Fichiers** : `database/__init__.py`, `scripts/relationship_analyzer.py`

- Nouvelle fonction `rename_person_if_phone_number(person_id, new_name)` vérifie si le nom actuel est un numéro de téléphone (regex `^[\+\d\s\-\.]+$`) et le remplace si oui
- `_store_results()` dans l'analyzer utilise le `likely_name` extrait par Haiku pour renommer les contacts
- Log `[analyzer] Renamed phone +33... → Prénom` quand un renommage a lieu

### 4. Dashboard fonctionnel

**Fichier** : `web/src/app/components/views/Dashboard.tsx`

Le Dashboard était déjà fonctionnel avec les appels API :
- `api.getStatus()` — messages du jour, tokens
- `api.getPeople()` — top 5 contacts
- `api.getPlaces()` — compteur lieux
- `api.getNotifications()` — activité récente

Route `/dashboard` correctement configurée dans `App.tsx`, entrée "Dashboard" visible dans la sidebar `BigBrotherLayout.tsx`.

---

## Maintenance DB — purge conversations (mai 2026)

Demande exécutée : suppression complète des conversations JARVIS pour repartir avec une liste de chat vide.

Opérations effectuées sur `data/jarvis.db` :
- `DELETE FROM conversation_documents`
- `DELETE FROM messages WHERE conversation_id IS NOT NULL`
- `DELETE FROM conversations`

Vérification :
- `conversations: 147 -> 0`
- `messages: 140 -> 0`
- endpoint `GET /api/conversations` : `0` conversation retournée

---

## Maintenance DB — fusion doublons contacts (mai 2026)

Demande exécutée : fusion des doublons visibles dans la liste contacts (`Bertille Doublon`, `Elias`, `Bertille`).

Constat :
- les 3 fiches pointaient vers le même handle iMessage : `+33783920665`

Opérations effectuées :
- conservation de la fiche `Bertille`
- migration des liaisons de `Elias` + `Bertille Doublon` vers `Bertille` :
  - `relationship_events.person_id`
  - `people_events.person_id`
  - `relationship_profiles.person_id`
- suppression des profils relationnels en trop (garde le plus récent)
- suppression des 2 fiches doublons dans `people`

Vérification API :
- `Bertille count: 1`
- `Elias count: 0`
- `Bertille Doublon count: 0`

---

## Sync profonde macOS (mai 2026)

Nouvel outil de réparation exhaustive : `scripts/force_full_mac_sync.py`

### Objectif
- forcer l’import complet des contacts macOS + conversations iMessage
- corriger les dates iMessage (Apple Cocoa Epoch) et les compteurs
- réaligner `people`, `relationship_profiles`, `imessage_analysis_cache`

### Ce qui a été ajouté

1. **Contacts robustes (`integrations/contacts.py`)**
   - extraction directe SQLite AddressBook (read-only) depuis :
     - `~/Library/Application Support/AddressBook/Sources/*/AddressBook-v22.abcddb`
   - fallback AppleScript conservé
   - `build_cache()` unifié sur la sortie consolidée sqlite + AppleScript

2. **iMessage complet (`integrations/imessage_reader.py`)**
   - nouvelle méthode `get_all_conversation_stats_full()`
   - requête SQL complète (`chat` + `chat_handle_join` + `handle` + `message`)
   - récupération `msg_count`, `first_date`, `last_date`, `last_rowid`

3. **Conversion dates Cocoa Epoch (critique)**
   - conversion exacte utilisée en SQL :
   - `CASE WHEN ABS(m.date) > 1000000000000 THEN (m.date / 1000000000.0) + 978307200 ELSE m.date + 978307200 END`
   - couvre nanosecondes (macOS récents) et secondes (anciens)

4. **UPSERT massif DB (`database/__init__.py`)**
   - nouvelle fonction `force_upsert_people_from_mac_sync(records)`
   - met à jour/crée `people` + correction de `last_mentioned`
   - met à jour `imessage_count`
   - upsert `relationship_profiles.handle`
   - remplit/actualise `imessage_analysis_cache` avec `last_rowid` et total max

### Script exécutable

```bash
python scripts/force_full_mac_sync.py
```

### Exemple de run réel
- contacts indexés : `612`
- conversations distinctes iMessage : `324`
- records upsert : `324`
- dates corrigées : `50`
- profils upsert : `324`
- cache iMessage upsert : `324`

---

## Logs Système (`/logs`) — traçabilité LLM (mai 2026)

Ajout d’un module complet de logs pour tracer les actions décidées/exécutées par le LLM.

### Backend / DB

- **Nouvelle table** `llm_action_logs` (dans `database/__init__.py` + `database/schema.sql`) :
  - `id`, `created_at`, `agent`, `action_type`, `payload`, `status`, `execution_time_ms`
- **Helpers SQLite** :
  - `log_llm_action(agent, action_type, payload, status, execution_time_ms)`
  - `get_llm_logs(limit=100, action_type=None)`
- **Endpoint API** :
  - `GET /api/logs?limit=100&type=<action_type>`
  - Retourne `{ logs: [...], count }` trié du plus récent au plus ancien.

### Interception des actions

- `actions.py` :
  - instrumentation de `execute_action(...)` avec chronométrage (`execution_time_ms`)
  - log automatique non bloquant (`asyncio.create_task` + threadpool DB)
  - statut `success` / `error` selon résultat
- `main.py` :
  - log `pending` dès qu’un bloc `action` est détecté (pipeline WS et interne)
  - log des actions internes silencieuses :
    - `auto_title`
    - `context_enrichment`
    - `journal_extract`

### Frontend

- **Nouvelle page** `web/src/app/components/views/LogsView.tsx` (design BIG BROTHER) :
  - header `SYSTEM LOGS` + compteurs (actions du jour / erreurs)
  - filtres (`action_type`, `limit`) + bouton rafraîchir
  - liste type terminal horodatée (`HH:MM:SS`)
  - badge statut (success/error/pending)
  - payload JSON replié/dépliable
- `api.ts` :
  - ajout `api.getLogs(...)` + type `LlmActionLog`
- Routing / navigation :
  - route `/logs` ajoutée dans `App.tsx`
  - entrée `Logs Système` ajoutée dans la sidebar (`BigBrotherLayout.tsx`) avec icône `TerminalSquare`

---

## Page Agenda (`/calendar`) — mai 2026

### Backend

- **`integrations/calendar_api.py`** : ajout de `get_events(start_date, end_date)` — récupère les événements Calendar.app sur une plage de dates ISO dynamique via AppleScript. Méthode interne `_events_range_script()` construit les dates AppleScript absolues, `_parse_events_output_full()` retourne des objets avec datetimes ISO 8601 complètes (pas juste HH:MM). La méthode `_to_iso()` convertit les dates locales AppleScript en ISO.
- **`main.py`** :
  - `GET /api/calendar?start=...&end=...` — liste les événements entre deux dates ISO.
  - `POST /api/calendar` — crée un événement (`{title|summary, start, end?, location?, notes?, calendar?}`), écrit directement dans Calendar.app.
  - `POST /api/calendar/test` — crée un événement de test "TEST JARVIS — à supprimer" (debug rapide de la chaîne Calendar).
- **`actions.py`** : l'action `calendar_create` (type `"calendar_create"`) mappe maintenant `summary|title`, `start|date`, `notes|description` et journalise explicitement succès/échec.
- **`integrations/calendar_api.py`** (durcissement mai 2026) :
  - `end` optionnel (défaut : `start + 1h`), correction auto si `end <= start`.
  - formats `start/end` acceptés : `YYYY-MM-DD HH:MM`, `YYYY-MM-DDTHH:MM`, `DD/MM/YYYY HH:MM`, `demain 14h`, `vendredi 10:00`, `14:00`.
  - sélection du calendrier cible plus robuste (préférence iCloud sinon premier calendrier disponible).
  - startup FastAPI : ouverture préventive de Calendar.app (`open -a Calendar`) pour réduire les erreurs AppleScript `-600`.

### Frontend

- **Nouvelle page** `web/src/app/components/views/CalendarView.tsx` (design BIG BROTHER) :
  - **Header** : titre "CALENDAR SYSTEM", toggle Mois/Semaine, navigation (Précédent/Suivant/Aujourd'hui), bouton "Nouvel Événement".
  - **Vue Mois** : grille 7 colonnes (Lun→Dim), cellules glass-panel avec badges événements (heure + titre tronqué). Jour courant mis en évidence (cercle blanc). Clic sur un jour ouvre la modal pré-remplie.
  - **Vue Semaine** : colonnes 7 jours + axe horaire 0h-23h, événements positionnés en blocs calculés (top/height selon heure début/fin). Hover affiche lieu.
  - **Modal d'ajout** : panneau glass-panel centré avec backdrop blur, champs Titre, Début (datetime-local), Fin (datetime-local), Lieu, Notes. Validation → `POST /api/calendar`.
  - **Dépendance** : `date-fns` v4 ajoutée au `package.json` (locale `fr` pour le formatage).
- **`api.ts`** : ajout `api.getCalendarEvents(start, end)` et `api.createCalendarEvent(body)` + interface `CalendarEvent`.
- **Routing** : route `/calendar` dans `App.tsx`.
- **Sidebar** : entrée "Agenda" avec icône `CalendarDays` (lucide-react) dans `BigBrotherLayout.tsx`.

### Synchronisation

L'agenda est 100% synchronisé avec Calendar.app (iCloud/Google/etc.) :
- Lecture via AppleScript (pas de cache, requête fraîche à chaque navigation).
- Écriture via `create_event` → apparaît immédiatement dans Calendar.app → synchro iCloud/Google native.
- JARVIS (via chat/voix) utilise l'action `calendar_create` pour écrire dans Calendar.app.

---

## Optimisation latence vocale — Haiku forcé en voice_mode (mai 2026)

### Problème
En mode vocal (`voice_mode=True`), les requêtes routées vers `CoachAgent` (Sonnet/Opus) ou `SchoolAgent` (Sonnet/Gemini) avaient un TTFT trop élevé pour une conversation naturelle à l'oral.

### Solution implémentée

#### `agents/__init__.py` — `BaseAgent`

**`_call_claude`** : le forçage Haiku + `VOICE_MAX_TOKENS` se déclenche maintenant si `voice_mode=True` **ou** si `context["voice_mode"] == True`. Avant, le paramètre explicite était requis, ce qui permettait à `_call_with_routing` de passer au travers sans déclencher l'override.

```python
is_voice = voice_mode or bool((context or {}).get("voice_mode"))
if is_voice:
    eff_model = config.HAIKU_MODEL
    mt = min(mt, getattr(config, "VOICE_MAX_TOKENS", 500))
```

**`build_system_prompt`** : si `context["voice_mode"]` est actif, une directive vocale est automatiquement ajoutée en fin de prompt système pour forcer des réponses concises à l'oral (pour tous les agents) :

```
DIRECTIVE VOCALE : Tu parles actuellement à l'oral. Tes réponses doivent être
extrêmement concises, naturelles et conversationnelles. Pas de Markdown, pas de
listes à puces, pas de longs paragraphes. 3 phrases maximum sauf si l'utilisateur
demande explicitement un développement.
```

#### `agents/coach.py` — `CoachAgent._call_with_routing`

Court-circuit ajouté : en voice_mode, l'appel `_should_escalate` (Haiku supplémentaire ~100 ms) est bypassé et on délègue directement à `_call_claude` avec `voice_mode=True`.

```python
if context.get("voice_mode"):
    return await self._call_claude(..., voice_mode=True)
# sinon : escalade Sonnet/Opus normale
```

### Comportement garanti en mode vocal

| Agent | Chat texte | Voice mode |
|-------|-----------|------------|
| Info | Haiku | Haiku |
| School | Sonnet ou Gemini | Haiku (bypass Gemini via `_route_task`) |
| Coach | Sonnet ou Opus | Haiku (bypass escalade) |
| Productivity | Haiku/Sonnet | Haiku |
| Journal | Sonnet | Haiku |

Le system prompt de l'agent spécialisé est conservé (rôle coach / prof intact), seul le moteur LLM change.

## Sélection dynamique du moteur TTS (mai 2026)

### Objectif

Permettre de changer de voix TTS depuis l'interface web, sans redémarrer le serveur.

### Nouveaux fichiers / fonctions

#### `audio/tts.py` — `MacOSTTSEngine`

Nouveau backend utilisant exclusivement des outils natifs macOS :

```
say -v Thomas -o /tmp/jarvis.aiff "texte"
afconvert -f m4af -d aac /tmp/jarvis.aiff /tmp/jarvis.m4a
```

- Aucune dépendance réseau, aucune clé API.
- Fichiers temporaires dans un `TemporaryDirectory` isolé (auto-supprimé).
- Sortie AAC/M4A lisible par Chrome, Firefox, Safari via `new Audio(url)`.
- Singleton `macos_tts`, voix configurable via `MACOS_TTS_VOICE` dans `.env` (défaut : `Thomas`).

Fonction utilitaire `get_tts_by_name(name)` — retourne le bon singleton :
- `"macos"` → `MacOSTTSEngine`
- `"edge"` / `"elevenlabs"` → `TTSEngine` (singleton existant)

#### `database/__init__.py` — table `app_settings`

```sql
CREATE TABLE IF NOT EXISTS app_settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
```

Helpers :
- `get_setting(key, default)` — lit depuis la DB
- `set_setting(key, value)` — UPSERT dans la DB

#### `main.py` — routes dynamiques

| Route | Méthode | Description |
|---|---|---|
| `/api/settings/tts` | GET | Retourne `{"engine": "edge"}` (DB ou fallback `.env`) |
| `/api/settings/tts` | PATCH | `{"engine": "macos"|"elevenlabs"|"edge"}` — change à la volée |

`_send_tts_streaming` lit `get_setting("tts_engine")` à chaque appel → pas de cache, changement instantané.

#### `web/src/app/components/views/DataView.tsx` — sélecteur UI

Section "Moteur Vocal" avec un **Segmented Control** glassmorphism (3 boutons) :

- **ElevenLabs** — Cloud · Haute qualité
- **Apple M4** — Local · Zéro latence réseau
- **Edge** — Cloud · Gratuit

Au clic : `PATCH /api/settings/tts` + toast de confirmation inline (vert = succès, rouge = moteur indisponible).

### Variables d'env

```bash
MACOS_TTS_VOICE=Thomas  # Voix macOS native (défaut : Thomas — voix française)
# say -v ? pour lister toutes les voix disponibles
```

## HTTPS local (micro navigateur + Tailscale) — mai 2026

### Problème

`getUserMedia` (micro) est bloqué par le navigateur en HTTP. Les connexions depuis l'iPhone via Tailscale doivent utiliser HTTPS/WSS.

### Architecture

```
iPhone (Tailscale) ──► https://100.123.50.38:8081  (FastAPI + cert custom)
Mac (dev)          ──► https://localhost:5173       (Vite + cert basicSsl)
                            └─ proxy ──► https://localhost:8081 (FastAPI)
```

### Setup — une seule fois

```bash
# 1. Générer les certificats (détecte l'IP Tailscale automatiquement)
chmod +x scripts/generate_ssl.sh
./scripts/generate_ssl.sh
# → certs/cert.pem  (public)
# → certs/key.pem   (privé — dans .gitignore)

# 2. Faire confiance au cert sur macOS (navigateur + système)
sudo security add-trusted-cert -d -r trustRoot \
  -k /Library/Keychains/System.keychain certs/cert.pem

# 3. Redémarrer JARVIS
./scripts/jarvis_full_restart.sh --daemon --dev
```

### Faire confiance au cert sur iPhone

1. Envoie `certs/cert.pem` sur ton iPhone (AirDrop ou mail)
2. **Installer** : Réglages > Général > Gestion VPN et appareils → installe le profil
3. **Activer** : Réglages > Général > À propos > Certificats de confiance → active JARVIS

### Ce qui a changé

| Fichier | Modification |
|---|---|
| `scripts/generate_ssl.sh` | Génère cert RSA 2048 bits, SAN = localhost + 127.0.0.1 + IP Tailscale, validité 825j |
| `.gitignore` | `certs/*.pem` exclu du dépôt git |
| `main.py` — `main()` | Détecte `certs/cert.pem` au démarrage → `ssl_certfile` + `ssl_keyfile` sur uvicorn, sinon HTTP (fallback) |
| `web/vite.config.ts` | `basicSsl()` pour le dev Vite ; proxy → `https://localhost:8081` avec `secure: false` ; `host: '0.0.0.0'` |
| `web/package.json` | `@vitejs/plugin-basic-ssl` en devDependency |
| `services/websocket.ts` | Déjà protocol-aware (`wss:` si HTTPS) — aucun changement |
| `services/api.ts` | Chemins relatifs → héritent du proto de la page — aucun changement |

### Régénérer le cert (ex : nouvelle IP Tailscale)

```bash
./scripts/generate_ssl.sh   # détecte l'IP Tailscale courante automatiquement
./scripts/jarvis_full_restart.sh --daemon --dev
```

## Journal de travail agent — 6 mai 2026

### Après-midi (15:04) — Résumé conversations

- Fichier généré : `resume_conversations_2026-05-06.md` (timeline complète 09:04 → 15:04).

### Après-midi (15:31) — Audit complet du projet

Audit exhaustif en 8 parties. Corrections appliquées :

**Compilation** : 41 fichiers Python OK, build TypeScript OK (bundle réduit 932→931 KB).

**Imports** : 25/25 modules Python importables. Aucun import cassé dans le code applicatif.

**Pipeline unifié** (`main.py`) : duplication éliminée — l'injection des documents de conversation (`get_conversation_documents`) était faite dans les 3 fonctions (`_build_enriched_context`, `_process_message_internal`, `_process_message`). Factorisée dans `_build_enriched_context` uniquement ; les deux autres réutilisent son résultat.

**Endpoints** : 19/19 fonctionnels (HTTPS).

**Refactoring frontend** :
- Helpers de temps dupliqués (`timeAgo` dans AnalyticsView, DocumentsView, MapView ; `formatDuration` dans MapView, DocumentsView) → consolidés dans `web/src/app/lib/timeFormat.ts`.
- Imports API standardisés : tous les composants importent depuis `@/services/api` et `@/services/websocket` (chemin canonique). Les fichiers `@/app/services/` sont des réexports confirmés.
- `console.log` de debug dans VoiceView enveloppés dans `import.meta.env.DEV`.
- `print()` dans `database/__init__.py` → `logger.info()`.

**Scheduler** : 3 jobs manquants ajoutés → 7/7 configurés :
1. Briefing matin (configurable)
2. Résumé soir (configurable via `EVENING_SUMMARY_TIME`)
3. Résumé hebdomadaire (dimanche 20:00)
4. Analyse relationnelle iMessage (3:00)
5. Alertes relationnelles (toutes les 6h)
6. Tâches en retard (toutes les heures, anti-spam par jour)
7. Analyse localisation (23:00)

**DB** : 26/26 tables attendues présentes + 6 tables bonus (school_subjects, school_flashcards, weekly_summaries, llm_action_logs, app_settings, sqlite_sequence).

**Prompts** : 14/14 fichiers tous référencés dans le code Python. Aucun orphelin.

**Config** : `.env.example` et `config.py` parfaitement alignés. Aucune variable orpheline ou manquante.

**Fichiers modifiés** :
- `web/src/app/lib/timeFormat.ts` (5 helpers consolidés)
- `web/src/app/components/views/AnalyticsView.tsx` (import centralisé, suppression helper local)
- `web/src/app/components/views/DocumentsView.tsx` (idem)
- `web/src/app/components/views/MapView.tsx` (idem)
- `web/src/app/components/views/VoiceView.tsx` (console.log → DEV guard)
- `web/src/app/components/views/CalendarView.tsx` (import standardisé)
- `web/src/app/components/views/ContactsView.tsx` (import standardisé)
- `web/src/app/components/views/Dashboard.tsx` (import standardisé)
- `database/__init__.py` (print → logger, ajout import logging)
- `main.py` (élimination duplication docs dans pipeline)
- `scripts/scheduler.py` (3 jobs ajoutés : evening_summary, weekly_summary, relationship_analysis_daily)

### Après-midi (16:06) — Diagnostic permissions macOS

Les intégrations Mail, Calendar et iMessage sont INACTIVES. Le diagnostic (`scripts/test_macos_permissions.py`) identifie :
- **Mail / Calendar** : timeout AppleScript — probablement un prompt Automation macOS non validé.
- **iMessage (chat.db)** : `unable to open database file` — Full Disk Access manquant pour Terminal/Cursor.
- **Messages (envoi)** : erreur `-1728` sur `name of every account`.

**Corrections de logging** :
- `integrations/mail.py` : `is_available()` et `_run_applescript()` enrichis avec capture `stderr`, messages d'instruction permissions, détection erreur `-600` et "Not authorized".
- `integrations/imessage.py` : `is_available()` distingue désormais `OperationalError` (permission) vs `DatabaseError` vs erreur générique, avec messages d'instruction Full Disk Access.

**Script diagnostic** : `scripts/test_macos_permissions.py` teste les 4 points de blocage (Mail, Calendar, chat.db, Messages envoi) sans charger FastAPI, avec instructions de correction pour chaque échec.

**Pour corriger** :
1. Reglages Systeme > Confidentialite et securite > Automatisation > cocher Terminal/Cursor pour Mail, Calendrier, Messages.
2. Reglages Systeme > Confidentialite et securite > Acces complet au disque > ajouter Terminal/Cursor.
3. Relancer JARVIS.

### Après-midi (16:22) — Intégration Kokoro TTS

Nouveau moteur TTS local via `kokoro-onnx` (ONNX Runtime, Mac M4). Zéro réseau, zéro API cloud.

**Setup** :
```bash
pip install kokoro-onnx
mkdir -p models/kokoro
curl -L -o models/kokoro/kokoro-v0_19.onnx \
  https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files/kokoro-v0_19.onnx
curl -L -o models/kokoro/voices.bin \
  https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files/voices.bin
```

**Config** (`.env`) : `TTS_ENGINE=kokoro`, `KOKORO_VOICE=af_nicole`, `KOKORO_LANG=fr-fr`.

**Performances Mac M4** : chargement 0.25s, synthèse 1.6s pour 4.8s d'audio (24 kHz WAV).

**Architecture** (`audio/tts.py`) :
- `KokoroTTSEngine` : lazy-loading du modèle ONNX au premier appel `synthesize()`.
- Conversion PCM float32 → WAV 16-bit via `soundfile` + `io.BytesIO`.
- Si Kokoro échoue (fichiers manquants, erreur ONNX) → fallback automatique sur macOS TTS puis Edge TTS.
- Support streaming via `create_stream()` (chunks WAV indépendants).

**Fichiers** :
- `audio/tts.py` — `KokoroTTSEngine` + singleton `kokoro_tts` + mise à jour `get_tts_by_name()`
- `config.py` — `KOKORO_VOICE`, `KOKORO_LANG`
- `.env.example` — nouvelles variables documentées
- `scripts/test_kokoro.py` — diagnostic standalone (8 étapes : fichiers, imports, chargement, synthèse, conversion)
- `.gitignore` — `models/` exclu du dépôt

### Après-midi (16:40) — Frontend Kokoro + fix pipeline voix

**Kokoro dans le frontend** :
- `VoiceView.tsx` : Kokoro ajouté dans le sélecteur TTS (dropdown header)
- `DataView.tsx` : Kokoro ajouté dans les options TTS (page Données)

**Fix pipeline voix (conversation mains libres)** — pourquoi ça ne marchait pas :
- Le client construisait le Blob audio en forçant `type: 'audio/mpeg'`. Quand Kokoro envoie du WAV, le navigateur échouait silencieusement à lire du "MP3" qui était en fait du WAV → `onerror` → cycle repartait sans son.
- Le backend envoie maintenant `audio_mime` dans l'event `speaking` (`audio/wav` pour Kokoro, `audio/mpeg` pour les autres).
- Le client stocke le MIME reçu et construit le Blob avec le bon type.
- L'event `done` (fin LLM) ne remet plus en `listening` pendant que le TTS est actif (race condition qui relançait le micro avant la fin de la réponse audio).
- L'event `response_clean` est maintenant géré dans VoiceView pour afficher le texte nettoyé.

**Fichiers modifiés** :
- `web/src/app/components/views/VoiceView.tsx` (kokoro + mime dynamique + fix cycle voix)
- `web/src/app/components/views/DataView.tsx` (kokoro dans TTS_OPTIONS)
- `main.py` (`_send_tts_streaming` envoie `audio_mime` dans l'event `speaking`)

### Soir (20:35) — STT voix : compréhension FR améliorée

Correctif ciblé sur `audio/stt.py` pour éviter les transcriptions parasites type `(musique)` / `(rire)` :
- passage de `scribe_v1` à `scribe_v2`
- envoi de `tag_audio_events=false`
- nettoyage regex d'un tag audio résiduel en début de phrase

Objectif : améliorer la compréhension réelle de ce que tu dis sur la page Voice, avec des phrases plus propres envoyées au LLM.

### Soir (20:40) — Debug voix live (haut droite)

Ajout d'un panneau diagnostic **live** sur la page Voice, positionné en haut à droite, pour suivre en temps réel le pipeline micro -> STT :
- taille du blob audio envoyé (`blob_bytes`)
- transcription STT brute (`stt_raw`)
- transcription STT nettoyée (`stt_clean`)

**Implémentation** :
- `audio/stt.py` : mémorisation `last_raw_text` / `last_clean_text` à chaque transcription
- `main.py` : envoi d'un event WebSocket `voice_debug` après STT avec `blob_bytes`, `stt_raw`, `stt_clean`
- `web/src/app/components/views/VoiceView.tsx` : écoute de `voice_debug` + affichage live dans le panneau diagnostic fixé en haut à droite (ouvert par défaut)

### Soir (20:48) — Correctif "écoute fantôme" Voice

Fix d'un bug de statut Voice : la page pouvait passer en `listening` sur des events WebSocket globaux alors que la session micro locale n'était pas active (d'où `Micro —`, `AudioContext —`, aucun blob envoyé).

**Correctif** :
- `web/src/app/components/views/VoiceView.tsx` : tous les handlers WS (`listening`, `processing`, `chunk`, `response`, `speaking`, `error`, etc.) ignorent désormais les événements tant que `activeRef.current` est `false`.
- Résultat attendu : plus de faux "Écoute en cours", et démarrage micro uniquement après clic explicite sur "Démarrer la conversation".

### Soir (20:50) — Garde-fou visuel + réglage Kokoro FR

Amélioration UX pour éviter toute ambiguïté d'état voix :
- `web/src/app/components/views/VoiceView.tsx` : le libellé central n'affiche plus "Écoute en cours..." tant que `micPermission !== 'granted'` (affiche "Micro non autorisé" à la place).

Vérification et ajustement `.env` (voix Kokoro) :
- `TTS_ENGINE=kokoro` (inchangé)
- `KOKORO_VOICE` passé de `bm_george` à `bm_lewis`
- `KOKORO_LANG` passé de `fr-fr` à `fr`
- `VOICE_MAX_TOKENS=200` vérifié (ok)

Redémarrage propre effectué via `scripts/jarvis_full_restart.sh` pour appliquer la configuration.

### Soir (21:10) — Alignement partiel sur zeldrisDASH (flush voix)

Comparaison avec le pipeline vocal de `zeldrisDASH` (worktree local) : son comportement robuste repose sur des envois audio réguliers même quand la détection de fin de phrase est imparfaite.

Portage d'un garde-fou équivalent dans JARVIS :
- `web/src/app/components/views/VoiceView.tsx`
  - ajout de `MAX_UTTERANCE_MS=7000`
  - ajout d'un **flush forcé** du blob audio après ce délai, même sans détection de silence fiable
  - objectif : éviter l'état bloqué "Écoute en cours" sans envoi STT

Effet attendu : au pire, un envoi STT se produit toutes ~7 secondes de parole continue.

---

## Daemon JARVIS — sentinelle permanente (mai 2026)

Ajout du **plus gros module structurant** de JARVIS : un daemon qui tourne 24/7 en parallèle du serveur web (lancé via `asyncio.create_task` dans le lifespan FastAPI). Il transforme JARVIS d'« assistant qui répond quand on le sollicite » en **majordome qui veille en permanence**.

### Principe — 3 niveaux d'arbitrage

```
[1] Pixel diff (Pillow)              → 0 token, 0 LLM, ~5 ms
       ↓ (changement >= 5 %)
[2] LLM local (Ollama qwen2.5-vl)    → 0 token API, ~500 ms
       ↓ (notable détecté)
[3] Claude API (Haiku)               → tokens réels, voix JARVIS
```

95 % du travail tourne en local (Ollama). Claude API ne reçoit **que des résumés texte** déjà digérés — jamais d'images. Coût par jour estimé : quelques centimes.

### Architecture multi-machines

- **Mac Mini = serveur** : tourne JARVIS + daemon + Ollama (qwen2.5-vl:7b + qwen2.5:7b).
- **MacBook Pro = client léger** : `scripts/jarvis_agent.py` (un fichier, ~280 lignes, dépendances `requests` + `Pillow`). Capture l'écran, envoie via Tailscale, joue les TTS reçus.
- Connexion via Tailscale (`http://100.x.x.x:8081`) avec token d'authentification généré au premier `register_device`.

### Modules ajoutés

| Fichier | Rôle |
|---|---|
| `scripts/screen_watcher.py` | Capture (`screencapture`) + diff pixel + analyse Ollama vision. Stockage `screen_activity`. Tracking `app_usage` (style Screen Time). Callbacks `on_notable` / `on_idle`. |
| `scripts/jarvis_daemon.py` | Daemon principal — orchestre screen watcher, surveillance iMessage (handle.id), surveillance Mail, rappels Calendar, file TTS jouée via `afplay`, wake word Porcupine, conversation mains libres, health check des devices distants. |
| `scripts/jarvis_agent.py` | Script autonome pour MacBook (un seul fichier). Threads heartbeat / capture écran / polling TTS. Ne contient AUCUNE logique IA — tout est délégué au Mac Mini. |

### Tables SQLite ajoutées

| Table | Rôle |
|---|---|
| `screen_activity` | Une ligne par cycle d'analyse écran (device, app, activity, mood, notable, change_pct, screenshot_hash, created_at). Indexée sur date, device, app. |
| `app_usage` | Temps cumulé par (device, app, date). UPSERT avec `ON CONFLICT(device, app, date) DO UPDATE SET duration_seconds = duration_seconds + excluded.duration_seconds`. |
| `devices` | Machines connectées (device_id, device_name, device_type, is_active, is_online, last_heartbeat, ip_tailscale, auth_token). |
| `work_sessions` | Sessions de travail détectées par le daemon (started_at, ended_at, duration_min, description). |

Helpers DB ajoutés à `database/__init__.py` :
- `save_screen_activity`, `get_screen_activity`, `get_current_screen_context`
- `upsert_app_usage`, `get_app_usage`, `get_app_usage_range`
- `register_device` (génère `auth_token` unique au premier appel), `update_device_heartbeat`, `set_active_device`, `get_active_device`, `get_all_devices`, `mark_device_offline`
- `start_work_session`, `end_work_session`, `get_work_sessions`

### Triage local (Ollama qwen2.5:7b)

Pour chaque iMessage / mail entrant, le daemon demande à Ollama (~500 ms, 0 token Claude) :

> *« L'utilisateur travaille. Cet événement vient d'arriver : … Dois-je l'interrompre vocalement ? OUI/NON. »*

Si **OUI** → Claude formule une notification courte → TTS local (ou push vers le device distant).
Si **NON** → la notification est stockée en DB mais aucune voix ne se déclenche.

Règles de triage : message personnel d'un ami/famille → OUI ; mail urgent ou pro important → OUI ; spam, newsletter, pub, notification système → NON ; message de groupe sans mention directe → NON.

### Wake word "Jarvis" (Porcupine)

Désactivé par défaut (`WAKE_WORD_ENABLED=false`). Activation :

1. Inscription gratuite sur [Picovoice Console](https://console.picovoice.ai) — clé d'accès gratuite pour usage personnel.
2. `pip install pvporcupine pyaudio`.
3. `.env` : `WAKE_WORD_ENABLED=true` + `PORCUPINE_ACCESS_KEY=...`.

À la détection : daemon bascule en mode `conversation` → `tts.synthesize("Oui Monsieur, je vous écoute.")` → boucle micro VAD pyaudio → STT ElevenLabs → orchestrateur (`_process_message_internal` avec `voice_mode=True`) → TTS. Termine sur silence > 15 s ou phrases du genre "merci jarvis", "c'est tout jarvis".

### Endpoints API ajoutés

| Route | Méthode | Description |
|---|---|---|
| `/api/devices/register` | POST | `{device_id, device_name, device_type, ip_tailscale?}` → token unique |
| `/api/devices/{id}/heartbeat` | POST | Maintien en ligne (toutes les 30 s côté agent) |
| `/api/devices/{id}/screen` | POST | Reçoit un screenshot base64 → analyse Ollama → notification éventuelle TTS |
| `/api/devices/{id}/tts` | GET | Polling : récupère un MP3 base64 à jouer (file d'attente par device) |
| `/api/devices/{id}/activate` | POST | Marque cette machine comme active (l'écran analysé par défaut) |
| `/api/devices` | GET | `{devices, active}` — liste + machine active |
| `/api/screen-activity` | GET | `?hours=24&device=...` — analyses d'écran récentes |
| `/api/screen-activity/current` | GET | `?device=...` — dernier contexte écran (≤ 5 min) |
| `/api/app-usage` | GET | `?days=7&device=...` — temps par app (style Screen Time) |

### Intégration au pipeline JARVIS

`_build_enriched_context` (dans `main.py`) ajoute deux sources :
- **`screen_context`** (toujours injecté si disponible) : `Écran : VS Code — code Python (mood: focused)`. Permet à JARVIS de répondre « je vois que tu codes en Python… » sans qu'on lui ait dit.
- **`screen_time_context`** (conditionnel sur mots-clés `temps`, `productivité`, `screen time`, `distrait`, `procrastin`, …) : top 10 apps avec minutes du jour.

### Frontend — widget Machines (Dashboard)

Ajout d'une carte **Machines** dans `Dashboard.tsx` : liste des devices avec :
- Icône selon `device_type` (`Monitor` / `Laptop` / `Smartphone`).
- Indicateur en ligne (point blanc/gris) + badge `ACTIF` sur la machine dont l'écran est analysé.
- Bouton **Activer** sur les machines en ligne non actives.

Helpers TypeScript dans `services/api.ts` : `api.getDevices()`, `api.activateDevice(id)`, `api.getScreenActivity(hours, device?)`, `api.getCurrentScreenContext(device?)`, `api.getAppUsage(days, device?)`. Types associés : `DeviceInfo`, `ScreenActivityRow`, `AppUsageRow`.

### Setup Ollama (Mac Mini)

```bash
# Installer Ollama (https://ollama.com)
brew install ollama
ollama serve  # tourne en background

# Modèles nécessaires
ollama pull qwen2.5-vl:7b   # vision (analyse screenshots)
ollama pull qwen2.5:7b      # triage notifications (léger)

# Vérifier
ollama ps    # modèles chargés et leur RAM
ollama list  # modèles disponibles
```

Tous les paramètres sont configurables via `.env` :
- `OLLAMA_URL=http://localhost:11434`
- `SCREEN_VISION_MODEL=qwen2.5-vl:7b`
- `TRIAGE_MODEL=qwen2.5:7b`

### Variables d'env ajoutées

```bash
# Daemon
DAEMON_ENABLED=true
SCREEN_WATCHER_ENABLED=true
SCREEN_WATCHER_INTERVAL=12              # secondes
SCREEN_CHANGE_THRESHOLD=5               # % de pixels minimum pour noter
SCREEN_ANALYSIS_THRESHOLD=15            # % pour déclencher Ollama vision
SCREEN_VISION_MODEL=qwen2.5-vl:7b
TRIAGE_MODEL=qwen2.5:7b
OLLAMA_URL=http://localhost:11434
DEVICE_ID=                              # vide = hostname système
DEVICE_NAME=Mac Mini M4
WAKE_WORD_ENABLED=false
PORCUPINE_ACCESS_KEY=
DAEMON_TTS_COOLDOWN=30                  # secondes anti-spam vocal en mode veille
```

### Démarrage

Le daemon démarre automatiquement au lancement de `python main.py` (sauf si `DAEMON_ENABLED=false`). Logs visibles : `[daemon] démarrage en mode veille`, `[screen] démarré — interval=12s, seuils=5%/15%`.

### Lancer l'agent sur le MacBook

```bash
# Sur le MacBook (avec Tailscale activé)
python3 -m venv venv-agent
source venv-agent/bin/activate
pip install -r requirements-agent.txt   # juste requests + Pillow

# Récupérer le token sur le Mac Mini
curl -X POST http://100.123.50.38:8081/api/devices/register \
     -H "Content-Type: application/json" \
     -d '{"device_id": "macbook-pro", "device_name": "MacBook Pro M5", "device_type": "laptop"}'
# → {"ok": true, "token": "abc123..."}

# Lancer l'agent
python scripts/jarvis_agent.py --server http://100.123.50.38:8081 --token abc123...
```

Permissions macOS requises (sur la machine où tourne le screen watcher / l'agent) :
- **Enregistrement d'écran** (Réglages > Confidentialité > Enregistrement de l'écran) pour `screencapture`.
- **Automation** pour Terminal/Cursor → System Events (récupération de l'app au premier plan).

### Sécurité

- Le `auth_token` est stocké dans la DB et envoyé en `Authorization: Bearer …` par l'agent. Il sert d'authentification minimale — Tailscale fournit le chiffrement réseau.
- Aucune image ne quitte l'écosystème local : Ollama tourne sur le Mac Mini, les screenshots sont décodés et jetés après analyse, seuls les résumés texte (`activity`, `notable`) sont stockés en SQLite.
- Anti-spam vocal : `DAEMON_TTS_COOLDOWN=30 s` en mode veille pour éviter de devenir une nuisance sonore.

### Fichiers modifiés

- `database/schema.sql` + `database/__init__.py` (nouvelles tables + helpers).
- `config.py` + `.env.example` (variables daemon).
- `requirements.txt` (Pillow, pvporcupine, pyaudio).
- `requirements-agent.txt` (nouveau — minimal pour MacBook).
- `scripts/screen_watcher.py` (nouveau).
- `scripts/jarvis_daemon.py` (nouveau).
- `scripts/jarvis_agent.py` (nouveau — script autonome).
- `main.py` (imports DB, endpoints `/api/devices/*`, `/api/screen-activity*`, `/api/app-usage`, démarrage daemon dans lifespan, contexte écran dans `_build_enriched_context`).
- `web/src/services/api.ts` (helpers `getDevices`, `activateDevice`, `getScreenActivity`, `getCurrentScreenContext`, `getAppUsage` + types).
- `web/src/app/components/views/Dashboard.tsx` (widget Machines).
- `CLAUDE.md` (section dédiée).

## Journal des opérations manuelles

### 2026-05-18 — Rattrapage post-coupure (mails + iMessage)

- **Premier essai** : `./venv/bin/python scripts/catchup_after_downtime.py` (~6 min 40, `exit_code: 0`) — **Mail** en timeout 60s (Mail.app / Automation) ; iMessage + `force_full_mac_sync` OK ; `email_summaries` inchangé (~46 lignes, derniers en mai 2026-05-06).
- **Second passage (mail OK)** : après correctifs `integrations/mail.py` (`activate` + timeout 90s + `reset_availability_cache`) et reset cache avant script — **20 non-lus** analysés en un cycle ; base passée à **66** lignes `email_summaries` ; exemples notifiés : Spotify (paiement), Uber (reçu).
- **API** : `POST /api/email-watcher/catchup` (réhydratation + rattrapage + reset cache Mail, mutex avec la boucle du watcher) — **nécessite un redémarrage du backend** pour charger le nouveau `main.py`.
- **Code** : `EmailWatcher.run_catchup_cycle()`, mutex `_cycle_lock`, stats `_last_cycle_stats` ; doc mise à jour (`README`, `STARTUP_PROTOCOL`, `CLAUDE.md`).

### 2026-05-06 — Remplacement de `chat.db` (Messages)

- Sauvegarde créée : `/Users/zeldris/Library/Messages/chat.db.backup-20260506-225402`
- Base remplacée : `/Users/zeldris/Library/Messages/chat.db`
- Source utilisée : `/Users/zeldris/Desktop/chat.db`

### 2026-05-06 — Protocole de démarrage et reprise

- Nouveau document créé : `STARTUP_PROTOCOL.md`
- Contenu : checklist complète des autorisations macOS, séquence de démarrage propre, vérifications santé API/WebSocket/intégrations, et procédure de reprise après coupure de courant (avec commandes de contrôle SQLite).

### 2026-05-06 — Démarrage total exécuté

- Commande exécutée : `./scripts/jarvis_full_restart.sh --daemon --dev`
- Backend actif : `https://127.0.0.1:8081`
- Frontend dev actif : `https://127.0.0.1:5173`
- Vérifications effectuées : `GET /api/status` OK, `GET /api/integrations` OK.

### 2026-05-06 — Fix doc ERR_EMPTY_RESPONSE

- Diagnostic : `ERR_EMPTY_RESPONSE` venait d'un accès `http://` sur un backend local exposé en `https://`.
- Mise à jour de `STARTUP_PROTOCOL.md` : URLs HTTPS explicites, commandes `curl -sk`, section dépannage dédiée `ERR_EMPTY_RESPONSE`.

### 2026-05-06 — Sync forcée Messages vers DB JARVIS

- Script exécuté : `./venv/bin/python scripts/force_full_mac_sync.py`
- Résultat : `exit_code=0`
- Rapport :
  - `contacts_indexed: 612`
  - `conversation_rows: 323`
  - `records_upserted: 323`
  - `db_result: {'input_records': 323, 'created': 4, 'updated': 319, 'dates_corrected': 0, 'profiles_upserted': 323, 'cache_upserted': 323, 'merged_duplicates': 0}`

### 2026-05-06 — Resync contacts + messages + dates (post import chat.db)

- `force_full_mac_sync.py` relancé avec succès :
  - `contacts_indexed: 612`
  - `conversation_rows: 323`
  - `records_upserted: 323`
  - `db_result: {'input_records': 323, 'created': 0, 'updated': 323, 'dates_corrected': 0, 'profiles_upserted': 323, 'cache_upserted': 323, 'merged_duplicates': 0}`
- `sync_contacts.py` exécuté via `PYTHONPATH=/Users/zeldris/JarvisAPI` (résolution import `database`) : `exit_code=0`.

### 2026-05-06 — Correctif critique boucle iMessage

- Arrêt immédiat du serveur, puis patch anti-boucle appliqué.
- `integrations/imessage.py` :
  - filtre SQL durci (`is_from_me = 0` + `text != ''`),
  - anti-retraitement par `processed_rowids`,
  - mise à jour défensive de `last_check_rowid` par message,
  - log debug de cycle (`last_rowid`).
- `scripts/jarvis_daemon.py` :
  - garde-fou : si bridge iMessage actif, le daemon n'interroge plus `chat.db` pour iMessage,
  - suppression de la formulation via `_process_message_internal` pour iMessage daemon (TTS direct uniquement).
- `main.py` :
  - garde-fou anti-loop dans `_process_message_internal` pour ignorer un texte ressemblant à une auto-réponse JARVIS.
- Nettoyage doublons DB après boucle :
  - `tasks_deleted=7`
  - `notifications_deleted=11`
- Vérification : compilation Python OK (`py_compile`) et backend redémarré.

### 2026-05-07 — Redémarrage total

- Redémarrage complet exécuté : `./scripts/jarvis_full_restart.sh --daemon --dev`
- Process relancés :
  - backend PID `38136`
  - frontend Vite PID `38139`
- Backend HTTPS confirmé en logs (`uvicorn` sur `0.0.0.0:8081`).

### 2026-05-07 — Listener iMessage vérifié

- Listener iMessage déjà actif (aucune action supplémentaire requise).
- Validation :
  - `/api/status` → `"imessage": {"available": true, ...}`
  - logs backend → `"[iMessage] Polling démarré"` et réception de messages `rowid` récents.

### 2026-05-07 — Listener activé manuellement (session Cursor)

- Commande lancée : `source venv/bin/activate && python main.py`
- Backend démarré en HTTPS sur `https://127.0.0.1:8081`
- Vérification : `curl -sk https://127.0.0.1:8081/api/status` retourne `imessage.available=true` et `email_watcher.running=true`.

### 2026-05-07 — Suivi tâche listener (notification d'échec)

- La tâche shell de démarrage a fini avec `exit_code=1` car le port `8081` était déjà occupé (`address already in use`).
- Contrôle effectué juste après : `curl -sk https://127.0.0.1:8081/api/status` retourne `200` et confirme un backend actif.
- Action: aucune relance supplémentaire pour éviter de dupliquer les processus.

### 2026-05-07 — Correctif chaîne Calendar (création d'événements)

- Diagnostic exécuté :
  - `osascript -e 'tell application "Calendar" to return name of every calendar'` OK (Calendar accessible).
  - création AppleScript directe d'un événement test OK.
  - `/api/integrations` indiquait Calendar disponible quand le backend était en ligne.
- Cause principale corrigée : `calendar_create` échouait fréquemment quand `end` était absent (cas naturel "demain 14h"), alors que `create_event` exigeait `start`+`end`.
- Correctifs appliqués :
  - `integrations/calendar_api.py` : parsing dates robuste (ISO + expressions naturelles FR/EN), `end` optionnel (`+1h`), fallback calendrier iCloud/premier calendrier, messages d'erreur explicites.
  - `actions.py` : mapping robuste des champs (`summary|title`, `start|date`, `notes|description`) + logs succès/échec détaillés.
  - `main.py` : ouverture préventive de Calendar.app au startup, `POST /api/calendar` assoupli (`summary` accepté, `end` optionnel), nouvel endpoint `POST /api/calendar/test`.
  - `prompts/persona.txt` : exemples `CALENDAR_CREATE` clarifiés avec dates naturelles et `end` optionnel.
- Validation locale :
  - `python -m py_compile integrations/calendar_api.py actions.py main.py` OK.
  - Remarque runtime : backend local instable dans cette session (arrêts/redémarrages externes), donc validation API finale à refaire en une passe avec serveur stable.

### 2026-05-07 — Réparation sync Contacts+iMessage (audit + dedupe)

- Pré-vol:
  - backup DB créé: `data/backups/jarvis.db.20260507-141030.bak`
  - backend vérifié en écoute sur `8081` (instance JarvisAPI active)
- Blocage critique confirmé:
  - `~/Library/Messages/chat.db` inaccessible depuis le contexte d'exécution (`OperationalError: unable to open database file`)
  - `force_full_mac_sync.py` exécuté mais import source impossible (`contacts_indexed=0`, `conversation_rows=0`)
- Réparations DB appliquées côté cible JARVIS:
  - fusion conservative des personnes doublons par handle identique: **27 fusions**
  - nettoyage lignes doublons `relationship_profiles` (même person+handle): **27 suppressions**
  - resync compteurs `imessage_count` depuis cache: **306 mises à jour**
- État DB après correction:
  - `people`: `335 -> 308`
  - `relationship_profiles`: `333 -> 306`
  - `duplicate_handle_groups_count`: `8 -> 0`
  - `duplicate_name_groups_count`: `1 -> 0`
- Nouveau check reproductible:
  - script `scripts/imessage_sync_health_check.py` (backend unique, accès chat.db, statut API, doublons DB, erreurs critiques récentes)
- Rapport détaillé:
  - `data/reports/sync_repair_report_20260507.md`
  - `data/reports/dedupe_people_20260507-121755.json`
  - `data/reports/dedupe_profiles_20260507-141828.json`

### 2026-05-07 — Script récupération TeamViewer

- Ajout du script `scripts/get_teamviewer_code.py`.
- Le script tente d'abord la récupération via CLI TeamViewer (`teamviewer info` / binaire app), puis fallback AppleScript (lecture UI de la fenêtre TeamViewer).
- Sorties disponibles : format lisible humain (par défaut) et JSON (`--json`), avec option debug `--show-raw`.
- Vérification locale : compilation Python OK (`python3 -m py_compile scripts/get_teamviewer_code.py`).

### 2026-05-07 — Tentative capture d'écran Bureau

- Demande utilisateur: prendre une capture d'écran et la déposer sur le Bureau.
- Commande exécutée: `screencapture "/Users/zeldris/Desktop/capture_20260507_1428.png"`.
- Résultat: échec `could not create image from display` (capture écran indisponible dans ce contexte d'exécution).

### 2026-05-10 — Restauration solution démarrage services (transcript perdu)

- Demande utilisateur: retrouver la solution précédente de démarrage des services JARVIS.
- Source retrouvée: transcript de session `07866a2e-c6f6-41b9-bd4a-3ef51219926e`.
- Solution restaurée: redémarrage propre backend + frontend, puis vérification `GET /api/status` et contrôle des permissions macOS (`Accès complet au disque` pour iMessage, `Enregistrement de l’écran` pour screen watcher), avec installation recommandée du modèle Ollama `qwen2.5-vl:7b`.

### 2026-06-01 — Arrêt propre de tous les services

- Demande utilisateur : arrêter JARVIS et tous ses services proprement.
- État constaté : backend (port **8081**), Vite (**5173**) et processus `main.py` / `jarvis_agent` déjà inactifs ; fichiers PID obsolètes (`26151`, `26154`).
- Actions : libération des ports `8081`, `5173`, `8080` (déjà libres) ; suppression de `data/.jarvis_restart/backend.pid` et `web.pid` ; vérification qu’aucun processus JARVIS ne tourne.
- Documentation : section **« Arrêt propre (tous les services) »** ajoutée sous « Redémarrage complet (script) ».

### 2026-05-18 — Redémarrage propre + autorisations + vérifications

- Redémarrage exécuté : `./scripts/jarvis_full_restart.sh --daemon --dev` (arrêt propre des ports `WEB_PORT` / Vite, nettoyage caches légers, relance `nohup`).
- Dernière relance (cette session) : backend PID `26151` (écoute `https://0.0.0.0:8081`), Vite sur le port **5173 en HTTPS** (`https://127.0.0.1:5173/`, PID shell `26154`, processus `node` actif). Fichiers PID : `data/.jarvis_restart/backend.pid` et `web.pid`.
- Relances antérieures (même jour) : backend `91501`, Vite `91504` (historique).
- Logs de démarrage : `data/.jarvis_restart/backend.log` confirme `Application startup complete`, bridge iMessage actif, email watcher, daemon en veille.
- **Autorisations** : procédure détaillée pour tout voir à l'écran (Terminal au premier plan, lancement sans `--daemon`, tableau des droits) ajoutée dans `STARTUP_PROTOCOL.md` section **« 2 bis) Voir toutes les invites d'autorisation a l'ecran »**.
- **Vérification locale** (à lancer sur ton Mac, dans le même réseau que le serveur) :
  - `curl -sk https://127.0.0.1:8081/api/status`
  - `curl -sk https://127.0.0.1:8081/api/integrations`
  - navigateur : `https://127.0.0.1:8081` et/ou `https://localhost:5173` (certificat auto-signé : accepter une fois).

---

## 2026-06-14 — Dashboard TV "War Room"

Objectif : creer un ecran de monitoring type "centre de commandement militaire" pour TV Philips 55" OLED (Google TV), affiche en plein ecran 24/7 en mode kiosk.

### Fichiers crees

Tout le code vit dans `~/JarvisAPI/tv/` (31 fichiers, **zero modification** des fichiers existants) :

```
tv/
├── server.py                     # FastAPI dedie (port 5174, HTTP)
├── config.py                     # Configuration centralisee
├── data_sources/ (11 modules)    # weather, stats, automations, calendar, tasks, messages,
│                                   emails, notifications, devices, mood
├── static/
│   ├── css/tv.css                # Style militaire dark (scanlines, glow, grid 1920x1080)
│   ├── js/ (14 modules)          # clock, weather, stats, automations, calendar, tasks,
│   │                               messages, emails, notifications, mood, globe (Three.js),
│   │                               voice-overlay (SSE overlay vocal), utils, main
│   └── assets/fonts/             # JetBrains Mono woff2 (local)
├── templates/tv.html             # Template Jinja2 unique
├── com.jarvis.tv.plist           # Service macOS launchd
└── README.md                     # Documentation complete
```

### Architecture

- **Serveur** : FastAPI + Uvicorn port **5174**, HTTP uniquement (reseau local).
- **Securite** : middleware IP whitelist (`192.168.1.0/24`, `100.64.0.0/10`, `127.0.0.1`). Hors whitelist → 403.
- **Donnees** : double source — SQLite read-only (`data/jarvis.db`) + proxy vers backend principal (`https://127.0.0.1:8081`).
- **Frontend** : vanilla JS, CSS grid, Three.js CDN pour le globe 3D. Zero npm/webpack.

### Widgets (13)

| Widget | Source | Refresh |
|--------|--------|---------|
| Horloge | JS local | 1s |
| Meteo Lille | Open-Meteo (gratuit) | 15min |
| Humeur | SQLite mood_log | 5min |
| Serveur | psutil + launchctl + Ollama | 10s |
| Actions IA | SQLite llm_action_logs (24h) | 30s |
| Calendrier | Backend :8081 (Apple Calendar) | 5min |
| Taches | SQLite tasks | 2min |
| Messages | iMessage (chat.db) + chat JARVIS | 30s |
| Emails | SQLite email_summaries | 5min |
| Notifications | SQLite notifications | 30s |
| Globe 3D | Three.js wireframe + arcs | continu |
| Devices | SQLite devices | 1min |
| Cout API | SQLite messages (24h) | 1min |

### Demarrage

```bash
cd ~/JarvisAPI/tv
pip install fastapi uvicorn jinja2 httpx psutil aiofiles --break-system-packages
python3 server.py
# → http://localhost:5174
```

### Service launchd

```bash
cp ~/JarvisAPI/tv/com.jarvis.tv.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.jarvis.tv.plist
```

### TV Philips via ADB

```bash
adb connect 192.168.1.XXX
adb shell am start -a android.intent.action.VIEW \
    -d "http://192.168.1.YYY:5174" \
    -n com.android.chrome/com.google.android.apps.chrome.Main \
    --ez "create_new_tab" true
```

### Tests effectues

- Imports Python : OK (config, server, tous les data_sources)
- Routes FastAPI : 13 endpoints enregistres
- Data sources : weather (Open-Meteo), mood, tasks, emails, notifications, devices, messages, stats (ollama=true) — tous OK
- Demarrage serveur : Uvicorn OK sur 0.0.0.0:5174
- `/api/health` : retourne `{"tv":"ok","timestamp":...}`
- `/api/mood` : retourne les donnees d'humeur correctement

### 2026-06-26 — Overlay vocal temps reel sur la TV

Ajout d'un overlay transparent qui s'affiche sur le dashboard TV quand le daemon audio est actif :

**Fichiers modifies :**

| Fichier | Modification |
|---------|-------------|
| `tv/server.py` | +SSE `/api/events`, +WebSocket client vers backend :8081, +tv_event_queue |
| `tv/static/js/voice-overlay.js` | Nouveau — classe vanilla JS, connexion SSE, mise a jour orbe + transcription + reponse |
| `tv/templates/tv.html` | +div#voice-overlay (orbe anime, etat, transcription, reponse), +@keyframes voice-orb-pulse |
| `tv/static/css/tv.css` | +regles #voice-overlay (transition opacity/transform) |
| `tv/README.md` | +section Overlay vocal (fonctionnement, etats, endpoint API) |

**Architecture du flux :**

```
AudioDaemon._broadcast_state() → broadcast_ws() (main.py :8081)
  → connected_ws → TV WebSocket client (_ws_listener)
  → tv_event_queue → SSE /api/events
  → EventSource (navigateur TV) → VoiceOverlay.handleEvent()
  → updateState() / showTranscript() / showResponse()
```

**Etats visuels :** idle (cache), wake_listening/listening (cyan), processing (violet), speaking (orange), error (rouge).
**Disparition :** 3 secondes apres retour en idle.
**Latence :** <200ms (SSE local, zero polling).
**Zero modification** du backend principal (`main.py` port 8081).

Setup TV Philips :
```bash
adb connect 192.168.3.82:5555
adb shell am start -a android.intent.action.VIEW \
    -d "http://IP_MAC_MINI:5174" \
    -n com.android.chrome/com.google.android.apps.chrome.Main
```

---

## Architecture dual-LLM stricte — package `jarvis/` (juin 2026)

Séparation **définitive et non-contournable** entre deux backends LLM, pour
garantir qu'aucune donnée privée d'Elias ne quitte jamais le Mac.

| Backend | Rôle | Modèle |
|---|---|---|
| **LOCAL** | Messages d'Elias (chat, résumé de messages) | `mlx-community/Qwen3-30B-A3B-4bit` via MLX-LM (subprocess) |
| **DEEPSEEK** | Tout le reste (mail, RAG, tâches, docs) | `deepseek-v4-pro` via `api.deepseek.com` (HTTP) |

**Règle absolue** : aucune donnée brute issue de la base messages, ni aucune PII
non-masquée, ne transite vers DeepSeek.

### Structure

```
jarvis/
├── __init__.py            # exporte JARVISRouter, DataSource, EmailPayload, RouterStats, exceptions
├── exceptions.py          # JARVISError, LocalBackendError, DeepSeekBackendError, DataLeakError
├── settings.py            # config env (clé DeepSeek lue paresseusement à l'appel réseau)
├── models.py              # DataSource (enum), EmailPayload (dataclass), RouterStats
├── router.py              # JARVISRouter — point d'entrée unique de routage
├── backends/
│   ├── local.py           # LocalBackend — mlx_lm.generate en subprocess async, prompt préfixé "/think"
│   └── deepseek.py        # DeepSeekBackend — httpx, garde-fou DataBoundary appelé AVANT chaque requête
├── pii/
│   ├── anonymizer.py      # PIIAnonymizer — pseudonymisation par tokens [PERSON_1]… (spaCy + fallback regex)
│   └── boundary.py        # DataBoundary — bloque toute fuite de données messages, sanitize_chunks RAG
├── pytest.ini             # asyncio_mode = auto
└── tests/                 # 44 tests (anonymizer, boundary, router) — backends mockés, zéro I/O réel
```

### Garanties implémentées

- `JARVISRouter.chat()` et `summarize(source=MESSAGES)` → **LOCAL uniquement** (assert explicite, jamais `self.deepseek`).
- `DeepSeekBackend.generate()` appelle **toujours** `DataBoundary.check()` sur le prompt et le system, **avant** tout accès réseau (non-configurable).
- `PIIAnonymizer` : même entité → même token ; mapping **uniquement en mémoire** (jamais loggué/sérialisé/écrit disque), détruit (`mapping.clear()`) immédiatement après dé-anonymisation, même en cas d'erreur (`finally`).
- `deanonymize()` tolère le reformatage des tokens par le LLM (`[Person_1]`, `[ email 1 ]` → restaurés).
- `session_id` = UUID v4 unique par anonymisation, jamais réutilisé.
- `EmailPayload` n'expose aucun champ `messages`/`conversation` (fuite structurellement impossible).
- `DataBoundary.FORBIDDEN_PATTERNS` : `message_id=`, `conversation_id=`, `SELECT … FROM messages`, `db.messages.` → `DataLeakError` + `RouterStats.boundary_violations += 1`.

### Flux email → DeepSeek

```
EmailPayload → PIIAnonymizer.anonymize(subject+body)
            → DataBoundary.check (dans DeepSeekBackend.generate)
            → DeepSeek (system: "garde les tokens [PERSON_N] intacts")
            → PIIAnonymizer.deanonymize(réponse, mapping) → mapping détruit → Elias
```

### Variables d'environnement (`.env` / `~/.zshrc`)

```bash
export DEEPSEEK_API_KEY="sk-..."          # jamais en dur dans le code
export DEEPSEEK_BASE_URL="https://api.deepseek.com/v1"
export DEEPSEEK_MODEL="deepseek-v4-pro"
export JARVIS_LOCAL_MODEL="mlx-community/Qwen3-30B-A3B-4bit"
export JARVIS_VENV="$HOME/mlx-env"
export JARVIS_PII_USE_SPACY="true"        # false = fallback regex pur
```

### Dépendances

```bash
pip install -r requirements.txt           # ajoute spacy, pytest, pytest-asyncio (httpx déjà présent)
python -m spacy download fr_core_news_sm  # NER PII (sinon fallback regex automatique)
```

### Tests

```bash
source venv/bin/activate
python -m pytest jarvis/tests -q          # 44 passed
```

Le NER spaCy est **optionnel** : si `fr_core_news_sm` n'est pas installe,
`PIIAnonymizer` bascule automatiquement sur un fallback regex.

## 2026-06-16 — Reconstruction PWA Next.js (`pwa/`)

La PWA a ete integralement reconstruite en tant qu'application Next.js 14 independante, cohabitant avec le backend Python FastAPI existant. Le repertoire `pwa/` contient l'integralite du projet.

### Stack PWA

```
Frontend : Next.js 14 (App Router) + TypeScript + Tailwind CSS
Icons    : lucide-react (zero emoji)
State    : Zustand + TanStack Query (react-query)
DB       : SQLite via better-sqlite3 (fichier jarvis-pwa.db)
Mail     : googleapis (Gmail API) + googleapis (Calendar API)
LLM      : MLX-LM en subprocess (Qwen3-30B-A3B)
Push     : web-push (VAPID)
PWA      : next-pwa + service worker custom
Deploy   : pm2 sur le Mac, port 3000, bind 0.0.0.0
Acces    : Tailscale (IP privée Mac:3000)
```

### Structure (`pwa/` — 60+ fichiers)

```
pwa/
  public/
    manifest.json                    # PWA manifest
    sw.js                            # Service worker (cache + push)
    icons/icon-192.png, icon-512.png # Icones app
  src/
    app/
      layout.tsx                     # Metadata + viewport PWA
      client-layout.tsx              # Enregistrement SW + providers
      page.tsx                       # redirect -> /dashboard
      providers.tsx                  # QueryClientProvider
      globals.css                    # Theme dark, safe-area, SF Pro
      dashboard/page.tsx             # Dashboard : briefing, stats, agenda
      mails/page.tsx                 # Mails : resume IA, filtres, liste
      tasks/page.tsx                 # Taches : progression, creation, liste
      config/page.tsx                # Config : notifs, comptes, LLM, systeme
      api/
        mail/route.ts                # GET /api/mail?filter=&limit=
        mail/summary/route.ts        # GET /api/mail/summary (LLM)
        mail/[id]/route.ts           # PATCH /api/mail/:id
        calendar/route.ts            # GET /api/calendar
        tasks/route.ts               # GET + POST /api/tasks
        tasks/[id]/route.ts          # PATCH + DELETE /api/tasks/:id
        summary/morning/route.ts     # GET /api/summary/morning (LLM)
        summary/evening/route.ts     # GET /api/summary/evening (LLM)
        summary/history/route.ts     # GET /api/summary/history
        notifications/subscribe/route.ts  # POST /api/notifications/subscribe
        notifications/send/route.ts  # POST /api/notifications/send
        status/route.ts              # GET /api/status
    components/
      shared/  (Card, IconBox, Badge, Skeleton)
      layout/  (BottomNav, StatusBar, PageHeader, PullToRefresh)
      dashboard/ (MorningSummary, StatsGrid, QuickActions, AgendaTimeline)
      mails/  (MailSummaryBanner, MailFilterPills, MailList, MailItem)
      tasks/  (TaskList, TaskItem, TaskCreator, ProgressBar)
      summaries/ (SummaryCard, SummaryHistory)
    lib/       (db.ts, gmail.ts, calendar.ts, llm.ts, push.ts, cron.ts, push-client.ts, utils.ts)
    stores/    (useAppStore.ts)
    hooks/     (useMails.ts, useTasks.ts, useSummary.ts, useCalendar.ts)
    types/     (index.ts)
    instrumentation.ts              # CRON jobs (07:00 matin, 19:00 soir)
```

### Pages

| Route | Description |
|-------|-------------|
| `/dashboard` | Briefing IA du matin, stats (mails/urgents/agenda/taches), actions rapides, timeline agenda |
| `/mails` | Resume IA des mails recents, filtres (Tout/Urgents/Non lus), liste avec icones contextuelles |
| `/tasks` | Barre de progression, creation rapide, liste groupee (Aujourd'hui / A faire), priorites |
| `/config` | Notifications, statuts comptes (Gmail/Calendar), LLM local, infos systeme |

### Design System

- **Dark mode** exclusif : fond `#0a0a0f`, cartes glassmorphism
- **Police** : SF Pro via `-apple-system`
- **Radius** : cartes 22px, boutons 18px, pills 20px
- **ZERO emoji** — icones Lucide React uniquement
- **Bottom nav** : glass backdrop blur 30px, navigation mobile-native
- **Safe areas** iOS : `env(safe-area-inset-*)`

### Demarrage

```bash
cd pwa
npm install

# Configurer les variables dans .env.local :
# GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET (OAuth Gmail/Calendar)
# VAPID_PUBLIC_KEY, VAPID_PRIVATE_KEY (genere avec npx web-push generate-vapid-keys)
# NEXT_PUBLIC_VAPID_PUBLIC_KEY

# Placer credentials.json a la racine de pwa/ (OAuth Google)

# Dev
npm run dev
# -> http://0.0.0.0:3000

# Prod
npm run build
pm2 start npm --name jarvis-pwa -- start
pm2 save
```

### Acces mobile via Tailscale

```bash
tailscale ip -4          # -> 100.x.x.x
# Sur iPhone : Safari -> http://100.x.x.x:3000 -> Ajouter a l'ecran d'accueil
```

### Notifications Push

1. Generer les cles VAPID : `npx web-push generate-vapid-keys`
2. Copier dans `.env.local`
3. Le navigateur demande l'autorisation au premier chargement
4. Briefing matin (07:00) et bilan soir (19:00) envoyes automatiquement via CRON

### Integration avec le backend Python

La PWA Next.js est **principalement un frontal** pour le backend Python FastAPI. Elle :
- Utilise le proxy Next.js (`/api/*` -> `http://127.0.0.1:8081`) pour toutes les donnees
- N'a **pas** de base de donnees locale (tout passe par le backend)
- N'a **pas** de LLM local (le backend utilise Claude API + Gemini CLI)
- Affiche les donnees du backend : mails, taches, calendrier, localisation...

### Pages PWA

| Route | Description |
|---|---|
| `/dashboard` | Accueil : briefing, stats, localisation, agenda, actions rapides |
| `/map` | **Carte interactive** : Leaflet + OpenStreetMap (tuiles sombres CARTO), markers colores par categorie de lieu, breadcrumb GPS, polylines des trajets, timeline 14 jours, fiche detail (stats lieu, visites, trajets) |
| `/mails` | Liste des mails recents |
| `/tasks` | Gestion des taches |
| `/config` | Configuration : tracking GPS, lieux connus, patterns, audio |

### Page Carte (`/map`)

Carte interactive full-screen avec :

- **Fond de carte** : CARTO dark tiles (gratuit, pas de cle API)
- **Marqueurs** : chaque lieu nomme (home/work/school/gym/shop/other) a un marqueur colore. Le lieu selectionne dans la timeline est mis en evidence (scale + glow).
- **Breadcrumb GPS** : points de passage (`CircleMarker` semi-transparents) avec tooltip affichant le nom du lieu et la precision GPS.
- **Trajets** : polylines colorees par mode de transport (pied=vert, velo=bleu, voiture=orange, transport=violet), lignes pointillees pour la marche.
- **Timeline** : barre horizontale defilable sur 14 jours avec compteur de visites/trajets par jour. Selection d'un jour filtre les donnees affichees sur la carte.
- **Fiche detail** : bottom sheet qui s'ouvre au clic sur un marqueur/trajet/point. Affiche les stats du lieu (visites, duree moyenne, derniere visite), les trajets associes (distance, duree, vitesse).

**APIs utilisees** : `/api/places`, `/api/places/{id}/stats`, `/api/location/history?hours=48`, `/api/visits?days=14`, `/api/trips?days=14`

**Stack technique** : Leaflet + react-leaflet v4 (React 18 compatible), import dynamique (`ssr: false`), TanStack Query pour le cache, Tailwind CSS + dark mode.

Elle n'appelle pas le backend Python (port 8081). Les deux applications sont isolees et peuvent tourner simultanement.

### Coexistence avec le frontend Vite (`web/`)

| Aspect | `web/` (Vite/React) | `pwa/` (Next.js) |
|--------|---------------------|-------------------|
| Port | 5173 (dev), servi par FastAPI (8081) | 3000 (independant) |
| UI | Desktop (BigBrother sidebar) | Mobile native (bottom nav) |
| PWA | Non | Oui (manifest + SW) |
| Push | Non | Oui (web-push + VAPID) |
| LLM | Claude API (via backend Python) | MLX-LM local (subprocess) |
| DB | SQLite gere par backend Python | SQLite propre (`jarvis-pwa.db`) |

---

## Changelog

### 2026-06-25 — Phase 1 Migration Claude → DeepSeek (fondations)

**Fichiers modifies :**

| Fichier | Changement |
|---------|-----------|
| `.env.example` | Section Claude remplacee par DeepSeek (`DEEPSEEK_API_KEY`, `DEEPSEEK_BASE_URL`, `DEEPSEEK_FAST_MODEL`, `DEEPSEEK_MAIN_MODEL`) |
| `.env` | `ANTHROPIC_API_KEY` / `HAIKU_MODEL` / `SONNET_MODEL` / `OPUS_MODEL` commentes. Section DeepSeek ajoutee (cle placeholder a remplir) |
| `config.py` | Suppression `ANTHROPIC_API_KEY`, `HAIKU_MODEL`, `SONNET_MODEL`, `OPUS_MODEL`. Ajout `DEEPSEEK_API_KEY`, `DEEPSEEK_BASE_URL`, `DEEPSEEK_FAST_MODEL`, `DEEPSEEK_MAIN_MODEL`. `AGENT_MODELS` migre. Section dual-LLM nettoyee (garde `JARVIS_LOCAL_MODEL`/`JARVIS_VENV`). `CODE_EXECUTOR_MODEL` pointe sur `DEEPSEEK_MAIN_MODEL`. |
| `requirements.txt` | Suppression `anthropic==0.52.*`. `httpx` deja present (utilise comme client HTTP DeepSeek). |
| `llm.py` | **Refonte complete** : `import anthropic` → `import httpx`. `chat()` et `chat_stream()` appellent `https://api.deepseek.com/v1/chat/completions`. `quick_classify()` et `classify_task_type()` utilisent `DEEPSEEK_FAST_MODEL`. `MODEL_COSTS` avec tarifs DeepSeek. Gemini CLI intact. Prompt caching Anthropic supprime (DeepSeek cache automatique). Signature `chat()` identique (parametre `use_cache` conserve pour compatibilite, mais ignore). |
| `agents/__init__.py` | `BaseAgent.model` → `DEEPSEEK_MAIN_MODEL`. Voice mode → `DEEPSEEK_FAST_MODEL`. Brief Gemini → `DEEPSEEK_FAST_MODEL`. |

**Mapping des modeles :**
- `claude-haiku-4-5` → `deepseek-v3-0324` (`DEEPSEEK_FAST_MODEL`)
- `claude-sonnet-4-6` → `deepseek-v4-0625` (`DEEPSEEK_MAIN_MODEL`)
- `claude-opus-4-6` → `deepseek-v4-0625` (`DEEPSEEK_MAIN_MODEL`)

### 2026-06-25 — Phase 4 Migration agents specialises

**Fichiers modifies (14 remplacements dans 7 fichiers) :**

| Fichier | Ligne(s) | Ancien | Nouveau |
|---------|----------|--------|---------|
| `agents/orchestrator.py` | 182, 495 | `config.HAIKU_MODEL` | `config.DEEPSEEK_FAST_MODEL` |
| `agents/coach.py` | 78 | `config.SONNET_MODEL` | `config.DEEPSEEK_MAIN_MODEL` |
| `agents/coach.py` | 103 | `config.HAIKU_MODEL` | `config.DEEPSEEK_FAST_MODEL` |
| `agents/coach.py` | 163 | `config.OPUS_MODEL if escalate else config.SONNET_MODEL` | `config.DEEPSEEK_MAIN_MODEL` |
| `agents/productivity.py` | 111, 237, 285 | `config.SONNET_MODEL` | `config.DEEPSEEK_MAIN_MODEL` |
| `agents/school.py` | 40 | `config.SONNET_MODEL` | `config.DEEPSEEK_MAIN_MODEL` |
| `agents/info.py` | 20 | `config.HAIKU_MODEL` | `config.DEEPSEEK_FAST_MODEL` |
| `agents/journal.py` | 59 | `config.SONNET_MODEL` | `config.DEEPSEEK_MAIN_MODEL` |
| `agents/memory.py` | 79 | `config.HAIKU_MODEL` | `config.DEEPSEEK_FAST_MODEL` |
| `agents/memory.py` | 416 | `config.SONNET_MODEL` | `config.DEEPSEEK_MAIN_MODEL` |

**Verification :** Zero reference residuelle a `HAIKU_MODEL`, `SONNET_MODEL` ou `OPUS_MODEL` dans le dossier `agents/`.

### 2026-06-25 — Phase 5 Migration main.py

**Fichiers modifies (9 remplacements dans 1 fichier) :**

| Fichier | Ligne(s) | Ancien | Nouveau |
|---------|----------|--------|---------|
| `main.py` | 333 | `config.HAIKU_MODEL` | `config.DEEPSEEK_FAST_MODEL` |
| `main.py` | 364 | `getattr(config, "ANTHROPIC_API_KEY", None)` | `getattr(config, "DEEPSEEK_API_KEY", None)` |
| `main.py` | 423 | `config.HAIKU_MODEL` | `config.DEEPSEEK_FAST_MODEL` |
| `main.py` | 587-588 | `config.ANTHROPIC_API_KEY` + warning | `config.DEEPSEEK_API_KEY` + warning |
| `main.py` | 821-823 | `"haiku"/"sonnet"/"opus"` dans status API | `"fast"/"main"` (opus supprime) |
| `main.py` | 1520 | `config.HAIKU_MODEL` | `config.DEEPSEEK_FAST_MODEL` |
| `main.py` | 1700 | `config.SONNET_MODEL` | `config.DEEPSEEK_MAIN_MODEL` |
| `main.py` | 2002 | `config.HAIKU_MODEL` | `config.DEEPSEEK_FAST_MODEL` |
| `main.py` | 2689 | `config.HAIKU_MODEL` | `config.DEEPSEEK_FAST_MODEL` |

**Details :**
- 6× `HAIKU_MODEL → DEEPSEEK_FAST_MODEL` (description IA, salutation, suggestion message, resume document, titre conversation)
- 1× `SONNET_MODEL → DEEPSEEK_MAIN_MODEL` (fallback contact chat)
- 1× `ANTHROPIC_API_KEY → DEEPSEEK_API_KEY` (check clé welcome + warning demarrage)
- 1× refonte status API : `haiku/sonnet/opus` → `fast/main` (gemini conserve)
- Verification : zero reference residuelle a `HAIKU_MODEL`, `SONNET_MODEL`, `OPUS_MODEL`, `ANTHROPIC_API_KEY` dans `main.py`

**Etapes restantes (Phase 6-7) :** scripts (`email_watcher.py`, `relationship_analyzer.py`, etc.), `code_executor.py`, verification complete.

## Audio Daemon (micro natif Mac Mini — juin 2026)

**Module** : `scripts/audio_daemon.py` — ecoute le micro physique du Mac Mini (Blue Snowball), transcrit en local via `faster-whisper` (tiny), appelle DeepSeek v4-flash/v4-pro, et joue la reponse via le TTS macOS natif (`say` + `afconvert`). Zero latence reseau, zero cout API (hors LLM DeepSeek).

**Architecture 2 boucles (correctif 26 juin 2026)** : le pipeline VAD et le pipeline STT+LLM+TTS sont separes en deux coroutines independantes.
- `_vad_loop` : lecture micro + VAD avec seuil de silence adaptatif + detection d'interruption — jamais bloque
- `_process_loop` : STT → LLM → TTS → purge post-TTS — peut bloquer 5-10s sans impacter le VAD

Cette separation elimine les `asyncio.QueueFull` et les coupures de phrase.

### Lancement

```bash
# IMPORTANT : doit etre lance depuis Terminal.app (pas Cursor, pas screen)
# car macOS exige une connexion au window server pour le dialogue de permission micro.
open -a Terminal scripts/launch_backend.sh
```

Au premier lancement, un dialogue **« Terminal souhaite acceder au microphone »** apparait. L'accepter. Ensuite le daemon audio capture le Blue Snowball.

### Pipeline (100% local sauf LLM)

```
Blue Snowball → PyAudio PCM 16kHz → VAD RMS
  ↓
faster-whisper tiny (local, CTranslate2 Apple Silicon)
  ↓
DeepSeek v4-flash (voix) ou v4-pro (principal)
  ↓
macOS say + afconvert (AAC/M4A) → afplay
```

### Etats

`idle` → wake word → `wake_listening` → parole → `listening` → silence → `processing` → `speaking` → retour → `wake_listening` (conversation continue) ou `idle` (timeout 15s).

### Wake word

- **Porcupine** (Picovoice) si `PORCUPINE_ACCESS_KEY` est definie
- **Fallback volume** sinon : detection RMS > 0.03 pendant 500ms comme declencheur

### Endpoints

| Route | Methode | Description |
|--|--|--|
| `/api/audio-daemon/status` | GET | Etat complet (stt_engine, tts_engine, state...) |
| `/api/audio-daemon/start` | POST | Demarre le daemon |
| `/api/audio-daemon/stop` | POST | Arrete le daemon |
| `/api/audio-daemon/wake-word` | POST | Active/desactive le wake word |
| `/api/audio-daemon/continuous` | POST | `{"enabled":true}` — mode ecoute continue sans wake word |

### Variables d'env

```bash
AUDIO_DAEMON_ENABLED=true              # active le daemon au boot
AUDIO_DAEMON_STT_ENGINE=local          # "local" = faster-whisper, "" = ElevenLabs Scribe
AUDIO_DAEMON_STT_MODEL=tiny            # tiny (75Mo) ou small (500Mo, plus precis)
AUDIO_DAEMON_SPEECH_THRESHOLD=0.02     # RMS seuil de parole (0.02 = plus conservateur)
AUDIO_DAEMON_SILENCE_MS=1500           # ms de silence avant fin de phrase (1.5s)
AUDIO_DAEMON_MIN_SPEECH_MS=600         # duree minimale de parole (600ms)
AUDIO_DAEMON_MAX_UTTERANCE_S=15        # duree max d'une utterance
AUDIO_DAEMON_CONVERSATION_TIMEOUT=45.0 # secondes avant retour veille (~37s de replique apres reponse)
AUDIO_DAEMON_INPUT_DEVICE=Blue Snowball # micro specifique (vide = auto Blue Snowball)
AUDIO_DAEMON_WAKE_SOUND=true           # bip de confirmation au wake word
PORCUPINE_ACCESS_KEY=                  # cle gratuite sur https://console.picovoice.ai
```

### Debug

```bash
# Etat en temps reel
curl -s http://127.0.0.1:8081/api/audio-daemon/status | python3 -m json.tool

# Mode continu (parle librement)
curl -s -X POST -H "Content-Type: application/json" \
  -d '{"enabled":true}' http://127.0.0.1:8081/api/audio-daemon/continuous

# Verifier que le micro capte (depuis le shell qui a la permission)
python3 -c "
import pyaudio, struct, math
pa = pyaudio.PyAudio()
stream = pa.open(rate=16000, channels=1, format=8, input=True, frames_per_buffer=480)
max_rms = 0
for _ in range(100):
    c = stream.read(480, exception_on_overflow=False)
    samples = struct.unpack(f'{len(c)//2}h', c)
    max_rms = max(max_rms, math.sqrt(sum(s*s for s in samples)/(len(c)//2))/32768.0)
stream.close(); pa.terminate()
print(f'RMS max: {max_rms:.6f}')
"
```

### Correctif 26 juin 2026 — Coupure audio + QueueFull

**Problemes resolus :**

1. **Phrase coupee (VAD trop agressif)** :
   - Seuil de silence adaptatif : si parole < 2s, exige au moins 2s de silence avant de couper
   - Seuil de silence passe de 1200ms a 1500ms par defaut
   - Purge de la queue audio apres chaque TTS (500ms de delai + drain complet)
   - Filtrage des transcriptions post-TTS (< 10 caracteres dans les 2s suivant le TTS)
   - Tracking `_last_tts_end` pour identifier les residus d'echo

2. **QueueFull (buffer overflow)** :
   - Architecture 2 boucles : `_vad_loop` (jamais bloque) + `_process_loop` (peut bloquer)
   - Queue audio bornee a `maxsize=300` (~9s de buffer)
   - Queue d'utterances bornee a `maxsize=3` (phrases completes)
   - Drain intelligent dans le callback pyaudio : garde les ~3s recentes, jette le reste
   - Le thread pyaudio skip les chunks quand `_tts_playing` est actif (anti-echo + evite QueueFull)

3. **Interruption utilisateur preservee** :
   - `_interrupt_event` partage entre les deux boucles
   - La boucle VAD detecte l'interruption, tue le TTS, signale l'event
   - La boucle processeur verifie l'event avant chaque etape (STT, LLM, TTS)

### Frontend

- **VoiceView** : carte glassmorphism avec toggles ON/OFF, Wake Word, Continu, indicateur d'etat temps reel (WebSocket `audio_daemon_state`), affiche `stt_engine` + `tts_engine`
- **Dashboard** : mini widget a cote de Machines

### Coexistence avec /voice web

Le daemon audio (micro Mac) et la page `/voice` (micro navigateur) utilisent des micros differents et convergent vers `_process_message_internal`. Aucun conflit possible.

### Dernier changelog — 29 juin 2026 (23h40) : Initialisation du repo Git + push GitHub

**Repo cree** : [`https://github.com/AVTAVANTTOUT2/JarvisAPI`](https://github.com/AVTAVANTTOUT2/JarvisAPI) (prive).

**Preparation** :
- `gh` installe via Homebrew (`brew install gh`)
- Authentifie en tant que `AVTAVANTTOUT2` (device auth)
- Git config : `user.name=AVTAVANTTOUT2`, `user.email=208137561+AVTAVANTTOUT2@users.noreply.github.com`
- Branche renommee `master` → `main`

**Fichiers exclus du commit initial** (ajoutes au `.gitignore`) :
- `*.bak` — fichiers backup (`main.py.bak`)
- `resume_conversations_*.md` — resumes de conversations personnelles
- `pwa/*.db-shm` — SQLite WAL shared memory

**Commit initial** : 214 fichiers, 59 362 lignes inserees.