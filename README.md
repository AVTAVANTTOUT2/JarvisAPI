# JARVIS

Assistant personnel autonome, multi-agents, voice-first. Tourne entièrement en local sur Mac — SQLite pour la mémoire, DeepSeek pour le raisonnement, AppleScript pour l'écosystème Apple (zéro OAuth). Persona majordome britannique : sec, précis, zéro emoji.

```
"salut"
→ "Bonjour Monsieur. Que puis-je faire pour vous ?"
```

## Dernier changelog — 14 juillet 2026

### Phase 6 — frontend unifié et SDK auth

- `frontend/` devient l'interface canonique Next.js 15/React 19 : elle choisit automatiquement le layout desktop ou mobile et réutilise les vues existantes.
- `jarvis_auth/` fournit un unique `AuthClient`, hook `useLockGate()` et composant `LockGate` fail-closed aux interfaces desktop, mobile et unifiée.
- `frontend/src/lib/api.ts` est l'unique wrapper réseau ; toutes les requêtes et tous les uploads incluent le cookie de session.
- FastAPI sert `frontend/out` en priorité. `web/dist` reste le fallback racine et l'ancienne PWA reste disponible sous `/m/` pour un rollback sans interruption.
- Validation locale : 9 Vitest, 3 Playwright, 4 contrats FastAPI, typecheck et trois builds frontend réussis.

### Historique du 11 juillet 2026

### Fix microphone — HTTP → HTTPS
- **Gardien `navigator.mediaDevices`** dans `web/src/app/components/views/VoiceView.tsx` :
  message d'erreur explicite quand `getUserMedia` est absent (connexion HTTP).
  L'API `getUserMedia` est une API de « contexte securise » — indisponible sur
  HTTP sauf `localhost`. L'erreur `Cannot read properties of undefined (reading 'getUserMedia')`
  est remplacee par une explication claire avec les solutions.
- **`scripts/generate_certs.sh`** : nouveau script de generation de certificat
  auto-signe avec SANs (localhost, hostname, IPs Tailscale detectees automatiquement).
- **`.env.example`** : documentation enrichie de `WEB_HTTPS`.
- **Installation** : section dediee au microphone/HTTPS dans le README.

Pour activer le micro depuis un iPhone en acces distant :
```bash
bash scripts/generate_certs.sh
# puis WEB_HTTPS=true dans .env, redemarrer main.py
```

### Architecture Review — audit complet
- **Rapport d'architecture complet** dans le dossier [`Architecture/`](./Architecture/INDEX.md)
- 23 problèmes identifiés (4 critiques, 6 majeurs, 8 modérés, 5 mineurs)
- 10 ADR (Architecture Decision Records)
- Plan de migration en 6 phases, 15 jours
- Aucune modification de code tant que le rapport n'est pas validé

### PWA mobile — détection automatique + redirection
- Ajout de la détection automatique de terminal mobile (`_is_mobile_device`) via User-Agent
- Redirection automatique `GET /` → `/m/` pour les téléphones (iPhone, Android Mobile, etc.)
- La PWA est servie depuis le **même port** (WEB_PORT) sous le préfixe `/m/` — l'authentification est partagée automatiquement (même origine HTTP, cookie `jarvis_session`)
- Build statique Next.js (`output: 'export'`, `basePath: '/m'`) → `pwa/out/`
- Script de build : `bash scripts/build_pwa.sh`
- Variables d'env : `PWA_ENABLED`, `PWA_DIR`, `PWA_URL`
- `config.py` : nouvelles variables `PWA_ENABLED`, `PWA_DIR`, `PWA_URL`, `WEB_DIST_DIR`
- `main.py` : `_is_mobile_device()`, `_setup_pwa_frontend()`, redirection mobile dans `_setup_frontend()`
- `pwa/next.config.js` : export statique conditionnel (next-pwa en dev, export en prod)
- `pwa/src/lib/api.ts` ajoutait alors `credentials: 'include'`; ce fichier historique a été remplacé en Phase 6 par `frontend/src/lib/api.ts`

### Pull & build — intégration commit distant "Mode écoute : diarization"
- `git pull origin main` — commit `27d3609` fusionné sans conflit avec les 5 fichiers locaux modifiés
- 18 fichiers ajoutés/modifiés : diarization (`audio/continuous_recorder.py`, `audio/stt.py`), recherche sémantique (`scripts/semantic_search.py`), 7 nouveaux fichiers de tests, mise à jour `config.py` / `database/__init__.py` / `main.py` / `CLAUDE.md`
- `requirements.txt` auto-merged : ajout local `catt>=0.12` + ajout distant `sentence-transformers`, `annoy`, etc.
- `pip install -r requirements.txt` OK (avertissement non-bloquant : `kokoro-onnx` vs `numpy 1.26.4`)
- `pnpm install && pnpm build` OK — 3067 modules, Service Worker actif, 53 assets précachés

### Changements locaux préservés (non commités)
| Fichier | Contenu |
|---|---|
| `actions.py` | Contrôle TV complet : WoL, Google Cast fallback (deep standby), anti-spam 30s, `_adb_connect_ensure`, `_wake_tv_via_cast`, `_open_tv_dashboard` |
| `prompts/persona.txt` | Doc commandes TV mise à jour (`on` ouvre dashboard, `wol` magic packet seul) |
| `requirements.txt` | Ajout `catt>=0.12` (Google Cast) |
| `scripts/audio_daemon.py` | Fix segfault PyAudio Apple Silicon, phrases fantômes TV, silence micro non-crash |
| `scripts/screen_watcher.py` | Anti-RAM-kill : espacement minimum 120s entre analyses Ollama vision |

## Ancien changelog — import iMessage + fusion commits en attente

**Import iMessage** — nouveau systeme d'import idempotent et incremental de `chat.db` vers `jarvis.db` :
- 8 nouvelles tables SQLite (`imessage_handles`, `imessage_chats`, `imessage_messages`, etc.)
- Triple cle de deduplication (ROWID, GUID, hash SHA256)
- Curseur de synchronisation incremental par ROWID
- Reconciliation automatique post-import
- Script CLI (`scripts/imessage_import.py`) : import, sync, audit, status, reset
- 33 tests unitaires et d'integration

Trois commits orphelins de `claude/workflow-project-improvements-yknzqs`, jamais inclus dans la PR #7 (fix web), ont ete rebases sur `main` et fusionnes :

| Domaine | Ajouts |
|---------|--------|
| Présence | détection arrivée/départ au bureau via le son (`scripts/presence.py`) |
| Mood | signal d'humeur comportemental discret (déviation vs baseline) |
| Rituels | debrief hebdo vocal (dimanche 21h), comparatif semaine, retour tardif |
| Batch 2 | running gags, alerte binge streaming, tracker réunions, engagements/promesses, DND |
| Voix | cache TTS spéculatif + raccourci « répète » (`audio/tts_cache.py`) |
| Docs | README condensé aligné codebase + `CHANGELOG_HISTORIQUE.md` (archive ancien README) |

29 tests dédiés : `test_batch_gags_dnd`, `test_presence_mood_weekly`, `test_voice_cache`.

## Ce que JARVIS fait

- **Converse** — chat web avec conversations persistantes, page vocale mains libres, bridge iMessage (on lui parle depuis l'iPhone comme à un contact), wake word « Jarvis » optionnel.
- **Se souvient** — mémoire à deux étages : extraction structurée (faits, personnes, événements, patterns, running gags) par le modèle rapide, raisonnement sur données denses par le modèle principal. 26+ tables SQLite, recherche plein-texte FTS5.
- **Surveille** — emails (Mail.app, analyse LLM, silence sur le non-important, drapeau rouge sur l'urgent), écran (Ollama vision local, 0 token API), position GPS (lieux, visites, trajets), relations iMessage (analytics sans LLM + analyse quotidienne), présence au bureau par le son.
- **Agit** — tâches, calendrier, envoi d'emails et d'iMessages, terminal sécurisé, exécution de code multi-étapes, mode autonome `/loop`, DevAgent (interview → spec → boucle plan/code/test/fix/commit dans un projet isolé).
- **Rythme la journée** — briefing matin, roast des tâches non faites (18:30), debrief du soir (21:45), citation ironique (07:00), debrief hebdo vocal (dimanche 21:00), anniversaires, pause café, alerte binge streaming, retour tardif, signal d'humeur comportemental (zéro diagnostic).
- **Se protège** — sauvegardes SQLite quotidiennes avec rotation, purge de rétention hebdomadaire, budget LLM mensuel avec alertes, heures calmes, mode « silence total sauf feu », client LLM avec retry/backoff.

## Architecture

```
                    ┌──────────────────────────────────────────────┐
                    │        Supervisor (port 9000, 24/7)          │
                    │   sert le front, contrôle/relance le backend │
                    └──────────────────┬───────────────────────────┘
                                       │
   Next.js 15 responsive ┐    ┌──────────▼──────────┐    ┌─ scheduler APScheduler (29 jobs)
   web/pwa fallbacks ─────┼──▶ │  Backend FastAPI    │ ◀──┼─ daemon sentinelle (écran, iMessage,
   TV War Room (5174) ────┘    │  (port 8081)        │    │  mails, calendar, TTS local)
   iPhone (iMessage) ──────▶ │  WS /ws + REST      │    └─ audio daemon (micro, VAD, wake word,
                             └──────────┬──────────┘       présence, réunions, TTS spéculatif)
                                        │
                    ┌───────────────────┼───────────────────┐
              ┌─────▼─────┐      ┌──────▼──────┐      ┌─────▼─────┐
              │Orchestrator│ ───▶ │ 6 agents    │      │  SQLite   │
              │(classif.)  │      │ info school │      │ jarvis.db │
              └────────────┘      │ produc coach│      │ 72 tables │
                                  │ journal mem.│      └───────────┘
                                  └─────────────┘
        LLM : DeepSeek API (fast = classification/triage, main = raisonnement,
        mode tâche lourde à max_tokens élevé) · vision locale : Ollama qwen2.5-vl
```

**Contrat de pipeline unique** : `pipeline.py` expose les points d'entrée publics, configurés par `main.py`; les implémentations vivent dans `api/chat_processing.py` et `api/ws_messages.py`. Chat, voix, iMessage, recherche et journal partagent le même enrichissement/orchestration.

## Stack

| Couche | Techno |
|---|---|
| Backend | Python 3.12 · FastAPI · WebSocket · APScheduler |
| LLM | DeepSeek API (format OpenAI, httpx, retry/backoff, pool partagé) |
| Vision locale | Ollama `qwen2.5-vl:7b` (écran) + `qwen2.5:7b` (triage) — 0 token API |
| Base | SQLite (`data/jarvis.db`), WAL, FTS5, sauvegardes `VACUUM INTO` |
| STT | faster-whisper local d'abord, ElevenLabs Scribe en fallback |
| TTS | Edge TTS (défaut) · ElevenLabs · macOS `say` · Kokoro — 7 émotions, cache spéculatif |
| Frontend canonique | Next.js 15 · React 19 · Tailwind v4 (`frontend/`, responsive desktop/mobile) |
| Fallbacks | Vite (`web/dist`) · PWA Next.js 14 (`pwa/out` sous `/m/`) |
| TV | Dashboard War Room FastAPI + JS (`tv/`, port 5174, Philips 55") |
| Apple | Mail, Calendar, Messages, Contacts via AppleScript — zéro OAuth |
| Multi-device | Tailscale + `scripts/jarvis_agent.py` (client léger MacBook) |

## Installation (Mac)

```bash
# 1. Environnement (Python 3.12 requis)
python3.12 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 2. Configuration
cp .env.example .env
# Éditer .env — au minimum DEEPSEEK_API_KEY. Tout le reste a des défauts sains.

# 3. Frontend unifié
cd frontend && pnpm install && pnpm build && cd ..

# 4. Lancer
python main.py            # backend seul → http://127.0.0.1:8080 (WEB_PORT)
                          # (8081 sur le setup actuel — 8080 occupé par whisper-server)
# ou en 24/7 :
./scripts/launch_supervisor.sh    # supervisor 9000 + backend auto-relancé
python scripts/jarvis_launchd.py install   # démarrage auto au boot macOS
```

**Permissions macOS** (Reglages > Confidentialite) : Acces complet au disque (chat.db iMessage), Automation (Messages, Mail, Calendar, Contacts, System Events), Microphone (daemon audio), Enregistrement de l'ecran (screen watcher). Check-list complete : [STARTUP_PROTOCOL.md](./STARTUP_PROTOCOL.md).

### HTTPS et microphone (acces distant iPhone / navigateur externe)

Le microphone du navigateur (`getUserMedia`) est une **API de contexte securise** — indisponible sur une connexion HTTP simple, sauf sur `localhost`. Si tu accedes a JARVIS depuis un iPhone ou un autre appareil via Tailscale (ex. `http://100.123.50.38:8081`), le micro sera bloque.

**Solution : activer HTTPS avec un certificat auto-signe.**

```bash
# 1. Generer un certificat auto-signe (inclut automatiquement les IPs Tailscale detectees)
bash scripts/generate_certs.sh

# 2. Activer HTTPS dans .env
#    Ajouter ou modifier :
#      WEB_HTTPS=true

# 3. Redemarrer JARVIS
python main.py
# → demarre en https://0.0.0.0:8081

# 4. Acceder depuis l'iPhone (ou autre navigateur distant) :
#    https://100.123.50.38:8081
#    → Accepter l'avertissement "Certificat non valide" (c'est normal, il est auto-signe)
```

Le script `generate_certs.sh` cree `certs/cert.pem` et `certs/key.pem`, lus automatiquement par `main.py` quand `WEB_HTTPS=true`. Les certificats sont deja dans `.gitignore` (cles privees = jamais commitees).

**Pas besoin de HTTPS si tu accedes en local** (`http://localhost:8081`) — le micro fonctionne directement sur localhost. Le HTTPS n'est necessaire que pour l'acces distant (IP Tailscale, reseau local, etc.).

**Vérifier que tout est sain** :

```bash
python -m pytest tests/ jarvis/tests agents/devagent -q   # suite complète
python scripts/imessage_sync_health_check.py               # santé sync iMessage
curl http://127.0.0.1:8081/api/status                      # état runtime
```

## Frontend unifié et accès téléphone

Le frontend Next.js 15 est servi **depuis le même port** que le backend (WEB_PORT, défaut 8081). Il choisit le layout mobile pour les téléphones/écrans étroits et le layout desktop pour les autres terminaux. Le cookie `jarvis_session` reste même origine.

### Fonctionnement

```
Requête GET / (tous navigateurs)
 │
 └── frontend/out/ (Next.js statique)
     ├── téléphone / viewport < 768 px → layout mobile
     └── desktop / tablette → layout desktop

/m/ reste disponible → pwa/out/ historique (rollback)
frontend/out absent → web/dist/ historique (fallback automatique)
```

**Détection mobile** : combinaison User-Agent + largeur de viewport, couverte par tests. Les tablettes Android reçoivent le layout desktop.

### Build et déploiement

```bash
# 1. Build canonique (Next.js → export statique dans frontend/out/)
cd frontend && pnpm install && pnpm build && cd ..

# 2. Démarrer le backend (ou redémarrer s'il tourne déjà)
python main.py

# 3. Accéder depuis un téléphone (sur le même réseau/Tailscale)
#    http://TON_IP:8081/          → layout mobile automatique
#    http://TON_IP:8081/m/        → ancienne PWA de rollback
```

### Variables d'env

| Variable | Défaut | Description |
|---|---|---|
| `FRONTEND_DIST_DIR` | `./frontend/out` | Build statique canonique Next.js 15 |
| `PWA_ENABLED` | `true` | Active le fallback PWA historique sous `/m/` |
| `PWA_DIR` | `./pwa/out` | Répertoire du build statique PWA |
| `PWA_URL` | (vide) | URL externe optionnelle (si PWA sur un autre port/domaine). Vide = servie depuis FastAPI sous `/m/` |
| `WEB_DIST_DIR` | `./web/dist` | Répertoire du build SPA desktop (fallback) |

### Auth

Toutes les interfaces partagent `jarvis_auth/LockGate`. Tant que `/api/auth/status` n'a pas confirmé une session valide — ou si le serveur est inaccessible — aucun contenu privé n'est rendu. Le wrapper API commun transmet toujours `credentials: 'include'`.

### Installation sur l'écran d'accueil (iOS/Android)

- **Android (Chrome)** : un bouton "Installer" natif apparaît (événement `beforeinstallprompt`)
- **iOS (Safari)** : Partager → "Sur l'écran d'accueil"

## Configuration

Tout vit dans `.env` — [.env.example](./.env.example) documente chaque variable. Les blocs :

| Bloc | Variables clés |
|---|---|
| LLM | `DEEPSEEK_API_KEY` (obligatoire), `DEEPSEEK_FAST_MODEL`, `DEEPSEEK_MAIN_MODEL`, `HEAVY_TASK_MAX_TOKENS` |
| Audio | `TTS_ENGINE` (edge/elevenlabs/macos/kokoro), `ELEVENLABS_API_KEY`, `AUDIO_DAEMON_ENABLED`, `WAKE_WORD_ENABLED` |
| iMessage | `IMESSAGE_TARGET` (ton numéro — vide = bridge off), `IMESSAGE_SEND_ENABLED`, `IMESSAGE_PREFIX` |
| Import iMessage | `IIMPORT_BATCH_SIZE` (5000), `IIMPORT_MAX_RETRIES` (3), `IIMPORT_SYNC_INTERVAL` (300) |
| Sentinelle | `DAEMON_ENABLED`, `SCREEN_WATCHER_*`, `OLLAMA_URL`, `EMAIL_CHECK_INTERVAL` |
| Fiabilité | `BACKUP_*`, `RETENTION_*`, `LLM_BUDGET_MONTHLY`, `QUIET_HOURS_START/END` |
| Rituels | `ROAST_TIME`, `DEBRIEF_TIME`, `QUOTE_TIME`, `WEEKLY_DEBRIEF_TIME`, `RITUALS_TTS` |
| Vigies | `BREAK_ALERT_MINUTES`, `BINGE_ALERT_MINUTES`, `LATE_RETURN_HOUR`, `PRESENCE_TIMEOUT_MIN` |
| Voix | `SPECULATIVE_TTS_ENABLED`, `VOICE_SESSION_GRACE_S`, `VOICE_MAX_TOKENS` |
| Opt-in | `MEETING_CAPTURE_ENABLED` (résumé de réunions au micro — off par défaut) |

## Les agents

| Agent | Modèle | Rôle |
|---|---|---|
| `orchestrator` | fast | Classifie chaque message (SCHOOL / PRODUCTIVITY / COACH / INFO / JOURNAL) et dispatche |
| `info` | fast | Météo, recherche web, questions factuelles |
| `school` | main | Cours, fiches, flashcards, devoirs complets (mode tâche lourde, fichiers sauvés dans `data/outputs/`) |
| `productivity` | fast/main | Emails, calendrier, tâches, briefings matin/soir |
| `coach` | main | Relations, émotions, décisions — reçoit tout le contexte mémoire |
| `journal` | main | Extraction d'insights des entrées de journal |
| `memory` | fast | Silencieux — alimente faits, personnes, patterns, contexte de vie |

L'utilisateur ne voit jamais le mot « agent » : JARVIS est une seule entité. La persona commune est injectée depuis `prompts/persona.txt` dans tous les agents user-facing.

À part : **DevAgent** (`agents/devagent/`) — développement autonome dans `dev_projects/{slug}/` (venv + git isolés) : interview adaptative → spec verrouillée → boucle plan → code → test → fix → commit avec budget d'itérations/tokens et juge d'acceptation.

## Import iMessage (chat.db vers jarvis.db)

Import idempotent et incremental de l'historique iMessage complet depuis `~/Library/Messages/chat.db`. Stocke les donnees brutes (handles, chats, messages, pieces jointes, reactions) dans 8 nouvelles tables SQLite avec triple cle de deduplication (ROWID, GUID, hash de contenu).

```bash
# Import initial complet
python scripts/imessage_import.py

# Sync incrementale (nouveaux messages uniquement)
python scripts/imessage_import.py --sync

# Audit de coherence sans import
python scripts/imessage_import.py --check

# Etat du curseur
python scripts/imessage_import.py --status

# Reinitialiser le curseur
python scripts/imessage_import.py --reset
```

Tables : `imessage_handles`, `imessage_chats`, `imessage_chat_handles`, `imessage_messages`, `imessage_attachments`, `imessage_message_attachments`, `imessage_reactions`, `imessage_sync_cursor`.

Garanties : idempotence (relancer N fois = meme resultat), incremental (curseur ROWID), reconciliation auto, contraintes UNIQUE physiques.

## Automatisations (scheduler, 23 jobs)

| Quand | Quoi |
|---|---|
| 04:15 | Sauvegarde SQLite (`VACUUM INTO`, rotation `BACKUP_KEEP`) |
| dim 04:45 | Maintenance : purge de rétention + optimize FTS/WAL |
| 07:00 | Citation ironique du jour (widget TV) |
| 07:30 | Briefing du matin (+ notification macOS) |
| 08:00 | Anniversaires des contacts (`people.birthday`) |
| 10:00 | Rappel des promesses ouvertes > 3 jours |
| chaque heure | Tâches en retard |
| /5 min | Clôture des réunions captées (opt-in) |
| /10 min | Tick présence (départ après silence) |
| /20 min (9-22h) | Pause café si écran continu ≥ 90 min |
| /30 min | Binge streaming ; retour tardif (22h-3h) |
| /6 h | Alertes relationnelles (silences inhabituels, messages sans réponse) |
| 18:30 | Roast des tâches non faites |
| 21:30 | Contrôle du budget LLM mensuel |
| 21:45 | Debrief du soir + score productivité figé |
| 22:00 | Résumé du soir (mémoire) |
| 22:40 | Extraction des engagements du jour |
| 23:00 | Analyse des habitudes géographiques |
| 23:15 | Signal d'humeur comportemental |
| dim 20:00 / 21:00 | Résumé hebdo mémoire / debrief hebdo vocal |
| 03:00 | Analyse relationnelle iMessage incrémentale |

## API

Surface complète documentée dans [CLAUDE.md](./CLAUDE.md). Les groupes :

- `WS /ws` — chat + voix temps réel (streaming, actions, TTS, reprise de session après coupure < 3 min)
- `/api/status`, `/api/integrations`, `/api/stats/weekly`, `/api/stats/compare`, `/api/costs`
- `/api/conversations*` — persistance type Claude (titres auto, épingles, recherche FTS, upload de documents)
- `/api/people*` — contacts : analytics iMessage, timeline, description IA, envoi, suggestions, running gags
- `/api/tasks`, `/api/notifications`, `/api/commitments`, `/api/journal`, `/api/memory`
- `/api/location*`, `/api/places*`, `/api/visits*`, `/api/trips` — GPS et lieux
- `/api/rituals*`, `/api/productivity/score`, `/api/mood/signals`, `/api/presence`, `/api/meetings`
- `/api/backups*`, `/api/maintenance/run`, `/api/dnd` — fiabilité et silence
- `/api/devices*`, `/api/screen-activity*`, `/api/app-usage` — multi-machines
- `/api/devagent*` — projets de développement autonomes

## Vie privée

- **DataBoundary** (package `jarvis/`) : les messages bruts ne quittent jamais le Mac — modèle MLX local pour le personnel, DeepSeek uniquement après anonymisation PII (spaCy NER + regex).
- L'analyse d'écran tourne sur **Ollama local** : l'API ne reçoit que des résumés texte, jamais d'images.
- La capture de réunions est **opt-in** et ne conserve que du texte transcrit, jamais d'audio.
- `chat.db` (iMessage) est ouvert en lecture seule ; l'envoi est désactivé par défaut (`IMESSAGE_SEND_ENABLED=false`).

## Structure

```
JarvisAPI/
├── main.py               # Assemblage FastAPI, lifespan et configuration pipeline
├── api/                  # 12 routeurs + handlers WebSocket/frontend
├── supervisor.py         # process 24/7 port 9000 — sert le front, relance le backend
├── config.py             # .env → settings typés
├── llm.py                # client DeepSeek (chat, stream, classify, coûts)
├── actions.py            # exécution des blocs ```action``` des réponses
├── agents/               # orchestrateur + 6 agents + devagent/ + easter_eggs
├── audio/                # STT local/cloud, TTS 4 backends, VAD, cache spéculatif
├── database/             # schéma, migrations idempotentes, helpers, FTS
├── integrations/         # Mail, Calendar, iMessage, Contacts, météo, GPS, computer
├── scripts/              # scheduler, daemons, rituels, watchers, maintenance, présence
├── prompts/              # persona + system prompts par agent (.txt)
├── jarvis/               # dual-LLM privacy : PII, router, backends MLX/DeepSeek
├── frontend/             # Next.js 15 responsive → frontend/out prioritaire
├── jarvis_auth/          # SDK auth et LockGate partagés
├── web/                  # Vues desktop + fallback Vite
├── pwa/                  # Vues mobiles + fallback historique sous /m/
├── tv/                   # dashboard TV War Room (port 5174)
└── tests/                # suite pytest backend (voir Architecture/06_PLAN_TESTS.md)
```

## Tests et CI

```bash
python -m pytest tests/ jarvis/tests agents/devagent -q   # suite backend complète
cd frontend && pnpm test && pnpm typecheck && pnpm build  # frontend canonique
cd frontend && pnpm test:e2e                              # desktop + mobile
```

CI GitHub Actions (`.github/workflows/ci.yml`) sur chaque push/PR : import des ~100 modules, pytest complet, tests/typecheck/build Vite et tests/typecheck/build du frontend Next.js unifié.

## Documentation

| Fichier | Contenu |
|---|---|
| [Architecture/](./Architecture/INDEX.md) | **Audit architectural complet** — 23 problèmes, 10 ADR, plan de migration 6 phases |
| [CLAUDE.md](./CLAUDE.md) | Référence technique complète (architecture, tables, endpoints, conventions) |
| [STARTUP_PROTOCOL.md](./STARTUP_PROTOCOL.md) | Démarrage propre, permissions macOS, reprise après coupure |
| [VOCAL_PIPELINE_ANALYSIS.md](./VOCAL_PIPELINE_ANALYSIS.md) | Pipeline vocal micro → haut-parleur, latences, points de défaillance |
| [CHANGELOG_HISTORIQUE.md](./CHANGELOG_HISTORIQUE.md) | Archive des changelogs détaillés (ancien README) |
