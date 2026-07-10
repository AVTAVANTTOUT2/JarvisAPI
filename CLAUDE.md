# CLAUDE.md — JARVIS Multi-Agent Personal Assistant

## Qui suis-je

JARVIS est un assistant personnel multi-agents avec interface vocale + web, tournant en local sur Mac. Il couvre tous les aspects de la vie de l'utilisateur : école (BTS), productivité (mail, calendar), coaching de vie (relations, émotions, patterns), et information (météo, web). Chaque agent a un rôle défini, un modèle LLM assigné, et accède à une mémoire partagée SQLite.

> **Note migration LLM (2026)** : le backend LLM est passé de l'API Claude
> (Anthropic) à **DeepSeek** (API format OpenAI, via httpx dans `llm.py`).
> Les mentions historiques « Haiku / Sonnet / Opus » dans ce document se
> lisent ainsi : Haiku → `config.DEEPSEEK_FAST_MODEL` (rapide, pas cher),
> Sonnet/Opus → `config.DEEPSEEK_MAIN_MODEL` (raisonnement). Le mapping
> agent → modèle est centralisé dans `config.AGENT_MODELS`.

## Stack technique

- **Backend** : Python 3.12 + FastAPI + WebSocket
- **Frontend** : React 19 + Vite + Tailwind (SPA dans `web/`, build `web/dist/` servi par FastAPI)
- **Base de données** : SQLite (fichier local `data/jarvis.db`)
- **LLM** : DeepSeek API (format OpenAI, `llm.py`) — routing fast/main, mode « tâche lourde » (max_tokens élevé) pour les productions longues
- **STT** : ElevenLabs Scribe (API cloud, accepte directement WebM/Opus — zéro ffmpeg)
- **TTS** : deux backends dans `audio/tts.py` — **Edge TTS** (défaut, faible latence) ou **ElevenLabs** (qualité / émotions, `eleven_multilingual_v2`).
- **VAD** : côté client uniquement (Web Audio API `AnalyserNode`)
- **Embeddings** : `all-MiniLM-L6-v2` via `sentence-transformers` (local, pour RAG)
- **Recherche web** : Tavily API ou SearXNG local
- **Météo** : OpenWeatherMap API

## Personnalité JARVIS

JARVIS est **UNE seule entité** du point de vue de l'utilisateur. Les agents (`info`, `school`, `productivity`, `coach`, `journal`, `memory`) sont des **rouages internes** invisibles. L'utilisateur ne doit JAMAIS voir le mot "agent" dans une réponse, ni se faire dire "je suis l'agent X".

### La persona (prompts/persona.txt)

`prompts/persona.txt` contient la voix unique de JARVIS — inspirée du JARVIS d'Iron Man : majordome IA britannique, concis, légèrement formel, humour pince-sans-rire, proactif. Ce fichier est **injecté automatiquement en début de tous les system prompts** des agents qui parlent à l'utilisateur, via `BaseAgent.build_system_prompt()`.

```
[persona JARVIS]   ← prompts/persona.txt (commun à tous)
---
[agent-specific]   ← prompts/{agent}.txt (capacités, formats…)
```

### Règles absolues du ton

- **Pas d'emoji.** Jamais. Aucun. Même pas pour la météo. Tout est texte.
- **Pas de chatbot.** Pas de "Quoi de neuf ?", "N'hésite pas !", "Je suis là pour t'aider !", "Super question !".
- **Pas de présentation comme un agent ou une IA.** "Je suis JARVIS" suffit (et seulement quand pertinent).
- **3 phrases max** pour une question simple. La donnée d'abord, le contexte ensuite si pertinent.
- **Tu** sur l'utilisateur, mais avec respect. "Monsieur" parfois, avec une pointe d'ironie bienveillante.
- **Pas de point d'exclamation** sauf urgence réelle.

### Quels agents reçoivent la persona ?

| Agent | `inject_persona` | Pourquoi |
|---|---|---|
| `info`, `school`, `productivity`, `coach`, `journal` | `True` (défaut) | Parlent à l'utilisateur — voix JARVIS obligatoire |
| `orchestrator` | `False` | Classifieur interne — sortie consommée par du code (`SCHOOL`, `INFO`, …) |
| `memory` | `False` | Système silencieux — sortie JSON consommée par le parser |

`BaseAgent.inject_persona: bool = True` est le défaut. Surcharger à `False` uniquement pour les agents internes dont la sortie n'est jamais affichée à l'utilisateur.

### Test rapide

```bash
# Dans le chat web
"salut"
# JARVIS doit répondre : "Bonjour Monsieur. Que puis-je faire pour vous ?"
# (et NON : "Salut ! 👋 Quoi de neuf ?")

"quel temps il fait ?"
# JARVIS doit répondre : "Lille, 18 degrés, couvert. Parapluie pour cet après-midi."
# (et NON : "☀️ À Lille il fait 18°C ! Quelques nuages mais sympa !")
```

## Architecture multi-agents

### Principe de routing

```
Input utilisateur (texte ou audio transcrit)
    │
    ▼
ORCHESTRATEUR (Haiku 4.5, ~50 tokens)
Classifie en: SCHOOL | PRODUCTIVITY | COACH | INFO | JOURNAL
    │
    ├── SCHOOL     → Agent École (Sonnet 4.6)
    ├── PRODUCTIVITY → Agent Productivité (Haiku triage / Sonnet rédaction)
    ├── COACH      → Agent Life Coach (Sonnet / Opus si décision profonde)
    ├── INFO       → Agent Info (Haiku 4.5)
    └── JOURNAL    → Agent Journal (Sonnet 4.6)
```

Tous les agents partagent un **Memory Agent** transversal qui gère la mémoire épisodique, le life profile, les fiches people, et la détection de patterns.

### Modèles et coûts

Le mapping agent → modèle vit dans `config.AGENT_MODELS` (surchargez via `.env`) :

| Agent | Modèle (défaut) | Quand |
|-------|--------|-------|
| Orchestrateur | `DEEPSEEK_FAST_MODEL` (`deepseek-v4-flash`) | Chaque message (classification) |
| École | `DEEPSEEK_MAIN_MODEL` (`deepseek-v4-pro`) | Résumés, fiches, exercices |
| Productivité (triage) | `DEEPSEEK_FAST_MODEL` | Résumé emails, check calendar |
| Productivité (rédaction) | `DEEPSEEK_MAIN_MODEL` | Rédiger un email, briefing |
| Life Coach | `DEEPSEEK_MAIN_MODEL` | Analyse relations, coaching |
| Life Coach (deep) | `DEEPSEEK_MAIN_MODEL` | Décisions structurantes uniquement |
| Info | `DEEPSEEK_FAST_MODEL` | Météo, questions factuelles |
| Journal | `DEEPSEEK_MAIN_MODEL` | Extraction insights du journal |
| Mémoire | `DEEPSEEK_FAST_MODEL` | Résumés, détection patterns |

**Ordre du system prompt inchangé** : `[LIFE_PROFILE]` + `[MEMORY_CONTEXT]` d'abord, puis `[AGENT_INSTRUCTIONS]`. DeepSeek gère le prompt caching automatiquement côté serveur (pas de `cache_control` explicite — le cache hit est lu dans `usage.prompt_cache_hit_tokens`).

## Routing des tâches — standard vs tâche lourde

Tout passe par **DeepSeek**. `llm.classify_task_type()` (fast model, ~10 tokens)
détecte les demandes de **production longue** et retourne `"heavy"` ou
`"standard"` :

| Tâche                              | Route                                        |
|------------------------------------|----------------------------------------------|
| Exercice / devoir complet          | heavy — `DEEPSEEK_MAIN_MODEL`, `HEAVY_TASK_MAX_TOKENS` |
| Dissertation / rapport / code long | heavy                                        |
| Résumé long (PDF entier, livre)    | heavy                                        |
| Flashcards en masse (>20)          | heavy                                        |
| Classification de message          | standard — `DEEPSEEK_FAST_MODEL`             |
| Analyse relationnelle / coaching   | standard — `DEEPSEEK_MAIN_MODEL`             |
| Extraction insights journal        | standard — `DEEPSEEK_MAIN_MODEL`             |
| Résumé email court                 | standard — `DEEPSEEK_FAST_MODEL`             |

### Flux type pour un exercice

```
Utilisateur : "Fais-moi une dissertation sur la mondialisation"
   ↓
fast — orchestrateur classifie en SCHOOL
   ↓
fast — classify_task_type() détecte une tâche lourde → "heavy"
   ↓
DEEPSEEK_MAIN_MODEL — produit le devoir complet avec le contexte mémoire
                      JARVIS (max_tokens = HEAVY_TASK_MAX_TOKENS, défaut 8192)
   ↓
Le système parse le bloc ```save JSON``` à la fin de la réponse
   ↓
Fichier sauvé dans data/outputs/school/[matière]/[filename].md
```

### Implémentation

- Chaque agent peut utiliser `self._route_task()` dans `BaseAgent` : mode vocal
  → réponse courte (`VOICE_MAX_TOKENS`) ; tâche lourde → `DEEPSEEK_MAIN_MODEL`
  avec `HEAVY_TASK_MAX_TOKENS` ; sinon appel standard avec le modèle de l'agent.
- La détection bas-niveau est `llm.classify_task_type()`.

> **Note (2026)** : l'ancienne délégation **Gemini CLI** (subprocess) a été
> supprimée — les tâches lourdes sont désormais servies par DeepSeek avec un
> plafond de tokens élevé, en conservant le contexte mémoire JARVIS.

## Bridge iMessage (macOS)

JARVIS est accessible **depuis l'iPhone** via iMessage : on parle à JARVIS comme à n'importe quel contact, il répond dans la conversation. Pas d'API officielle Apple — on s'appuie sur deux mécanismes natifs macOS :

### Lecture des messages reçus

`~/Library/Messages/chat.db` est une base SQLite mise à jour en temps réel par Messages.app. JARVIS s'y connecte en **READONLY** (`mode=ro` URI) et fait un polling toutes les `IMESSAGE_POLLING_INTERVAL` secondes (défaut 3s) :

```sql
SELECT m.ROWID, m.text, m.date, h.id AS handle_id
FROM message m
LEFT JOIN handle h ON m.handle_id = h.ROWID
WHERE m.ROWID > ?         -- last_check_rowid (init = MAX(ROWID) au démarrage)
  AND m.is_from_me = 0    -- messages reçus uniquement
  AND m.text IS NOT NULL  -- skip réactions, attachments seuls
  AND m.text != ''        -- skip messages vides
  AND h.id = ?            -- IMESSAGE_TARGET (ton propre numéro)
ORDER BY m.ROWID ASC
```

### Envoi des réponses

`subprocess.run(["osascript", "-e", script])` pilote Messages.app. Le script AppleScript :

```applescript
tell application "Messages"
    set targetService to 1st account whose service type = iMessage
    set targetBuddy to participant "+33612345678" of targetService
    send "réponse de JARVIS" to targetBuddy
end tell
```

Les réponses longues sont splittées en chunks de 2000 chars. Échappement strict des `\\`, `"` et `\n` dans la string AppleScript.

### Permissions macOS requises

| Permission | Pourquoi | Comment |
|---|---|---|
| Full Disk Access | Lire `chat.db` | Réglages > Confidentialité > Accès complet au disque → ajouter Terminal/iTerm |
| Automation pour Messages.app | Envoyer via osascript | Demandée par macOS au 1er envoi → accepter le prompt |

### Flux complet

```
[iPhone] User envoie "jarvis quel temps fait-il ?" en iMessage
   ↓ (synchro Apple)
[Mac] chat.db reçoit la nouvelle ligne dans `message`
   ↓ (polling 3s)
[JARVIS] _get_new_messages() détecte un ROWID > last_check_rowid
   ↓
[JARVIS] _apply_prefix_filter() : si IMESSAGE_PREFIX="jarvis" → strip → "quel temps fait-il ?"
   ↓
[JARVIS] orchestrator.handle() → INFO → météo Lille
   ↓
[JARVIS] _send_message() → osascript → Messages.app envoie la réponse
   ↓
[iPhone] Reçoit la réponse dans la même conversation iMessage
```

### Sécurité

- Filtrage strict côté serveur : seuls les messages dont `handle.id = IMESSAGE_TARGET` sont traités. Si un autre contact t'écrit, JARVIS l'ignore.
- Préfixe optionnel `IMESSAGE_PREFIX` : si tu mets `IMESSAGE_PREFIX=jarvis`, seuls les messages commençant par "jarvis" (case insensitive, ponctuation tolérée) sont traités. Permet d'avoir une conversation normale avec toi-même + déclencher JARVIS à la demande.
- Aucun écrit dans `chat.db` (READONLY).
- Le bridge iMessage est le **seul** composant autorisé à répondre aux messages.
- Le daemon ne répond **jamais** aux iMessages : il ne fait que la notification vocale/TTS.

### Variables d'env

```bash
IMESSAGE_TARGET=+33612345678   # ton numéro/email iMessage. Vide = bridge désactivé.
IMESSAGE_POLLING_INTERVAL=3    # secondes
IMESSAGE_PREFIX=jarvis         # optionnel, vide = traite tous les messages
```

## Localisation GPS et habitudes

L’app native iOS (ou un **raccourci iOS Shortcuts** en attendant) envoie des points `POST /api/location` sur le Mac. Côté serveur : **`integrations/location.py`** (`LocationManager`) enregistre l’historique, résout les **lieux nommés** (`places`, rayon Haversine / `radius_meters`), ouvre et ferme les **visites** (`visits`), crée des **trajets** entre deux lieux (`trips`). Les helpers SQLite sont dans **`database/location_helpers.py`**.

### Tables (schéma)

- `places` — lieux nommés + stats agrégées (`visit_count`, `avg_duration_min`)
- `location_history` — points GPS bruts (avec `place_id` résolu si dedans)
- `visits` — segments arrivée / départ dans un lieu nommé
- `trips` — liaison deux lieux + mode de transport estimé par vitesse moyenne
- `location_patterns` — motifs géographiques (`routine`, `absence`, …)

### Contexte orchestrateur

`build_full_context()` inclut `current_location`, `current_visit`, `today_visits`, `location_patterns`. L’**orchestrateur** ajoute une section **`[LOCATION]`** dans `memory_context`.

### Analyse quotidienne

**`scripts/location_analyzer.py`** — à **23:00** (`scheduler`) appelle Haiku avec **`prompts/location_analyzer.txt`**, insère des lignes dans `location_patterns` et `user_facts`, notifie en cas d’anomalie (Centre de notifications macOS + table `notifications`).

### Actions vocales (`actions.py`)

Types `name_place`, `where_am_i`, `day_route` (prompts déclarés dans `persona.txt`).

### API (aperçu)

`POST /api/location`, `POST /api/location/batch`, `GET/POST/PUT/DELETE /api/places`, `GET /api/places/{id}/stats`, `GET /api/location/status`, `GET /api/location/history`, `GET /api/visits`, `GET /api/visits/today`, `GET /api/trips`, `GET /api/location/patterns`, `POST /api/location/name-current`.

### Config

`LOCATION_TRACKING`, `LOCATION_PLACE_RADIUS` dans `config` / `.env`.

## Email watcher proactif (Apple Mail)

JARVIS surveille **en continu** ta boîte mail via `scripts/email_watcher.py` et `integrations/mail.py` (AppleScript → Mail.app). Le worker tourne en background (`asyncio.create_task` dans le lifespan FastAPI, comme le bridge iMessage) et poll les non-lus toutes les `EMAIL_CHECK_INTERVAL` secondes (défaut 120). Pour chaque mail jamais vu, il l'envoie à Claude Haiku qui retourne un JSON structuré, puis exécute des actions automatiques.

**Aucun OAuth nécessaire** — les emails sont lus via Mail.app qui est déjà configuré avec le compte Gmail/iCloud de l'utilisateur. Même pattern que le bridge iMessage : `subprocess.run(["osascript", "-e", script])` wrappé dans `asyncio.run_in_executor()`.

### Pipeline d'analyse

```
Toutes les EMAIL_CHECK_INTERVAL secondes (défaut 120s) :
  ↓
mail_client.get_unread(20)                   # non-lus via AppleScript
  ↓
1er cycle ? → backlog ignoré (IDs ajoutés au cache, 0 analyse)
  ↓
filter (id ∉ self.last_processed_ids)       # skip les déjà traités
  ↓
mail_client.get_message(id)                  # body complet
  ↓
truncate body à 1500 chars                  # cap anti-coût
  ↓
Haiku 4.5 + prompts/email_analyzer.txt      # max_tokens=200, temperature=0.0
  ↓
parse JSON {notify, reason, summary,
            amount, from_name, action_needed, deadline}
  ↓
notify=false → log silencieux, fin.
notify=true  → notification + tâche + iMessage + email_summaries
```

### Philosophie : Haiku décide tout

**Aucun filtre regex local.** Pas de liste d'expéditeurs ignorés, pas de mots-clés prioritaires. Tous les nouveaux mails passent par Haiku, qui décide seul via `prompts/email_analyzer.txt`.

Deux types notifiés :
- **PAIEMENT** (`reason: "payment"`) — facture, prélèvement, virement, commande, abonnement, amende, loyer, salaire, devis, relance
- **DEMANDE** (`reason: "request"`) — une vraie personne (pas un robot, pas une marque) qui attend une réponse/action de l'utilisateur

Tout le reste (`reason: "ignore"`) est silencieusement logué et oublié. **Mieux vaut manquer un mail que déranger pour rien.**

### Actions automatiques

| Haiku dit… | JARVIS fait |
|--|--|
| `notify:true, reason:"payment"` | `create_notification(priority="high")` + `create_task(category="finance", title="Paiement: …", description="Montant: …")` + iMessage |
| `notify:true, reason:"request"` | `create_notification(priority="medium")` + `create_task(category="email", title=action_needed)` + iMessage |
| `notify:false` | Log `Ignoré : {sender} — {subject}`, rien d'autre |
| Si `notify:true` | Persist dans `email_summaries` via UPSERT |

### Anti-doublon

1. **Cache mémoire `last_processed_ids`** — peuplé à chaque cycle.
2. **Hydratation au boot** via `get_all_processed_email_ids()` — tous les `gmail_id` déjà en `email_summaries` rechargés au démarrage.
3. **`UNIQUE` sur `email_summaries.gmail_id`** + UPSERT — backstop DB.

### Premier cycle après démarrage (rattrapage)

Le **premier cycle** de `_check_new_emails()` (`_initialized=False`) : parmi les non-lus du cycle (cap `MAX_UNREAD_PER_CYCLE`), ceux **déjà** présents dans `email_summaries` sont seulement marqués comme vus ; ceux **absents** de la base sont **analysés** (Haiku) puis enregistrés (y compris les `reason: ignore` avec résumé stocké en priorité `low`). Log : `Premier cycle : X déjà en base, Y à analyser`. Script manuel : `python scripts/catchup_after_downtime.py`.

### Robustesse

- Boucle `try/except` global → ne crashe jamais.
- `MAX_BODY_CHARS = 1500` : tronque les mails longs.
- `MAX_UNREAD_PER_CYCLE = 20` : cap par cycle.
- Parser JSON tolérant : JSON brut, blocs ```json, ou JSON noyé dans du texte.
- Si Mail.app est inaccessible → retry silencieux au prochain cycle.

### Notifications dans l'UI

- Bouton "🔔 Notifications" en bas de la sidebar → badge rouge avec compteur, classe `has-urgent` (animation `pulse-danger`) si au moins une urgente.
- Au clic, panneau dropdown listant les non-lues (priorité urgent → high → medium → low). Bouton "Marquer lu" par item + "Tout marquer comme lu" en pied de panneau.
- **Toasts** : pour chaque nouvelle notif urgent/high détectée entre 2 polls, slide-in depuis le haut du chat (border `--danger` ou `--warning`), auto-dismiss 8s, boutons "Voir" (ouvre le panneau + marque lu) et "✕".
- 1er poll au boot : on pré-remplit `state.seenNotifIds` mais on n'affiche **pas** de toasts (sinon flood au démarrage si plein de notifs en attente).
- Polling `/api/notifications` toutes les 30s via `setInterval`.

### Briefing matin enrichi

`productivity_agent.morning_briefing()` appelle `_collect_pro_context(use_email_summaries=True)` :
- **Skip** l'appel `mail_client.get_unread()` (économie tokens + latence).
- Lit `get_recent_email_summaries(15)` → ces résumés ont déjà été analysés par le watcher.
- Lit `get_unread_notifications(15)` → injectées dans le contexte + comptées dans le user message (`prefix += f"\n⚠️ {urgent_count} notification(s) URGENTE(s) en attente"`).
- Le prompt `prompts/productivity.txt` expose maintenant `{{emails_context}}` (pré-analysés) + `{{notifications_context}}` (alertes en attente).

### Endpoints

| Route | Méthode | Description |
|--|--|--|
| `/api/notifications` | GET | Non lues triées par priorité |
| `/api/notifications/all?limit=50` | GET | Historique (lues + non lues) |
| `/api/notifications/{id}/read` | POST | Marquer une notif lue |
| `/api/notifications/read-all` | POST | Tout marquer lu |
| `/api/email-watcher/catchup` | POST | Rattrapage mail : reset cache Mail + cycle « premier démarrage » (non-lus absents de `email_summaries`) |
| `/api/status` | GET | Inclut `email_watcher: {running, check_interval, processed_count}` |
| `/api/integrations` | GET | Inclut `email_watcher: bool` |

### Variables d'env

```bash
EMAIL_CHECK_INTERVAL=120      # secondes entre 2 scans (défaut 2 min)
```

### Coût

- Par email analysé : ~$0.001 (Haiku, ~300 in / ~150 out)
- 50 mails/jour : ~$0.05/jour, ~$1.50/mois
- L'hydratation du cache au boot garantit que **chaque mail n'est analysé qu'une seule fois** dans toute la vie de la base SQLite.

## Contrôle ordinateur (macOS)

JARVIS peut exécuter des actions sur le Mac local via `integrations/computer.py` (subprocess + AppleScript), pilotées par des blocs ```action``` dans les réponses (voir `prompts/persona.txt`).

**Module** : `ComputerControl` — `run()` (shell `COMPUTER_SHELL`, timeout, motifs dangereux refusés dans `is_safe`), `open_app`, `find_files`, `clipboard` (`pbcopy` / `pbpaste`), `get_battery` / `get_wifi` / `get_disk_space`, `get_running_apps`, `get_active_window`, `run_applescript`.

**Actions** (`actions.py` → `execute_action`) : `terminal`, `open_app`, `find_file`, `clipboard`, `system_info`. Types déclenchant une **2e passe LLM** (réformulation, pas de stdout brut dans le chat) : `terminal`, `find_file`, `system_info`, `clipboard` — flag `ACTIONS_WITH_FOLLOWUP` dans `main.py`.

**Sécurité** : patterns bloqués (ex. `rm -rf /`, `shutdown`, fork bomb, `curl | bash`, etc.) ; commandes `rm`, `mv` vers `~/`, `sudo`, `brew uninstall` → retour `needs_confirmation` tant que l’action n’a pas `confirmed: true`. Le client envoie `{ "type": "action_confirm", "action": { ... "confirmed" implicite côté serveur } }`.

**WebSocket** : message `action_confirm` exécute l’action avec confirmation et, si besoin, envoie `response_followup` + persistance de la synthèse.

**Config** : `COMPUTER_ACCESS`, `COMPUTER_SHELL`, `COMPUTER_TIMEOUT` dans `.env`. **`/api/status`** et **`/api/integrations`** exposent `computer: { available, shell }`.

## Exécution de code avancée (Open Interpreter)

JARVIS intègre **Open Interpreter** comme moteur d'exécution avancé pour les tâches complexes. Ce moteur est **complètement invisible** pour l'utilisateur — il voit JARVIS qui sait coder, debugger, déployer. Pas de mention d'Open Interpreter dans les réponses.

### Architecture dual-exécution

```
Action terminal reçue
 │
 ├── complex:false (ou absent) + commande shell
 │    → subprocess basique (computer.run)
 │
 └── complex:true OU langage naturel détecté
      → Open Interpreter (code_executor.execute)
      → Traduit l'instruction en code/shell
      → Exécute, debug, réessaie si erreur
      → Retourne output + code exécuté + résumé
```

### Module : `integrations/code_executor.py`

**`CodeExecutor`** — wrapper autour d'Open Interpreter, singleton `code_executor`.

- `execute(instruction, timeout)` : exécution async dans un thread, retourne `{ok, output, code, errors, summary}`
- `_is_safe(instruction)` : patterns bloqués (suppression système, format disque, shutdown, fork bomb)
- `reset()` : vide la conversation de l'interpréteur entre les exécutions
- Auto-run activé (pas de confirmation interactive)
- Utilise Claude (Sonnet par défaut) via LiteLLM comme backend LLM

### Routing intelligent (`actions.py`)

`_action_terminal()` choisit automatiquement le moteur :

1. Si `complex:true` dans l'action → `code_executor.execute()`
2. Si le texte est du langage naturel (détecté par `_is_natural_language()`) → `code_executor.execute()`
3. Sinon → `computer.run()` (subprocess classique)

`_is_natural_language()` détecte les verbes d'action courants (crée, installe, configure, deploy, build, fix...).

### 2e passe LLM enrichie

`_format_action_result_for_followup()` gère maintenant les résultats structurés du code executor : instruction originale, blocs de code exécutés, output, erreurs, résumé — le tout reformulé par l'orchestrateur en langage naturel pour l'utilisateur.

### Config

```bash
CODE_EXECUTOR_ENABLED=true       # active/désactive le moteur avancé
CODE_EXECUTOR_TIMEOUT=120        # timeout en secondes
CODE_EXECUTOR_MODEL=             # modèle Claude utilisé (défaut: Sonnet)
```

### Exemples de capacités

| Demande utilisateur | Routing | Comportement |
|---|---|---|
| "ls -la ~/Documents" | subprocess | Commande simple |
| "Crée un script Python qui convertit mes CSV en JSON" | code_executor | Écrit le code, l'exécute, montre le résultat |
| "Mon projet Flask ne démarre plus, regarde pourquoi" | code_executor | Lit les logs, identifie l'erreur, propose un fix |
| "Analyse le fichier data.csv et dis-moi les tendances" | code_executor | Pandas + stats + résumé |
| "brew install redis" | subprocess | Installation simple |
| "Installe redis et lance-le" | code_executor | Multi-étapes intelligent |

**`/api/status`** et **`/api/integrations`** exposent `code_executor: { available, engine }`.

## Audio — ElevenLabs unifié (STT + TTS)

Un seul fournisseur pour STT et TTS : **ElevenLabs**. Plus de Whisper, de ffmpeg, de fichiers temporaires, ni de conversion audio.

### STT — ElevenLabs Scribe (`audio/stt.py`)

- Accepte directement WebM/Opus du navigateur — zéro conversion, zéro ffmpeg
- Latence ~0.5s ; blobs < 1000 octets ignorés
- Coût : inclus dans le forfait ElevenLabs
- Config : `ELEVENLABS_API_KEY` (sert aussi pour le TTS)

### TTS — Edge ou ElevenLabs (`audio/tts.py`)

Selon `TTS_ENGINE` dans `.env` :

1. **ElevenLabs** — si `TTS_ENGINE=elevenlabs` **et** `ELEVENLABS_API_KEY` + `ELEVENLABS_VOICE_ID` sont définis. API REST `httpx`, émotions via `voice_settings`, modèle `eleven_multilingual_v2`, sortie `mp3_44100_128`.
2. **Edge TTS** — sinon (défaut `TTS_ENGINE=edge`). Latence réduite ; voix par défaut `fr-FR-VivienneMultilingualNeural` (`config.TTS_VOICE`). Émotions (tag Claude) sans effet sur la voix Edge.

`tts.synthesize(text, emotion="warm")` retourne des bytes MP3. En mode mains libres, `synthesize_stream` envoie des chunks jusqu'à fermeture du flux ; la session WebSocket termine avec `speech_done` pour que le client assemble un fichier MP3 valide avant lecture.

### VAD — côté client uniquement

Détection de parole par volume en temps réel via Web Audio API `AnalyserNode` dans `Voice.tsx`. Seuils configurables (`VOICE_SILENCE_DURATION_MS`, `VOICE_MIN_SPEECH_MS`). Pas de `webrtcvad`, pas de VAD serveur.

### Système d'émotions (7 tags)

7 émotions : `neutral`, `warm`, `serious`, `concerned`, `amused`, `urgent`, `encouraging`.

**Pipeline** :
1. `prompts/persona.txt` demande à Claude de commencer chaque réponse par `[emotion]` sur la 1ère ligne.
2. `BaseAgent._extract_emotion(response)` (regex `^\s*\[(\w+)\]\s*\n?`) extrait le tag et retourne `(emotion, texte_propre)`.
3. `_call_claude()` appelle `_extract_emotion` avant de retourner. L'émotion est dans `result["emotion"]`.
4. `orchestrator.handle_stream()` strip le tag du flux streaming (les chunks n'affichent PAS `[warm]` dans la bulle).
5. `_process_message()` (`main.py`, unique pipeline WebSocket texte + audio) passe `emotion` à `tts.synthesize()` pour adapter la voix.

### Mode conversation mains libres — page `/voice` (recommandé)

Pipeline vocal complet :
```
Micro → MediaRecorder.start() (sans timeslice, un enregistrement par phrase)
→ Fin de parole (silence ≥ VOICE_SILENCE_DURATION_MS)
→ stop() → onstop → Blob WebM complet → WebSocket binaire
→ ElevenLabs Scribe (transcription directe WebM, zéro conversion)
→ Claude Haiku (mode vocal, VOICE_MAX_TOKENS, [VOICE_MODE])
→ TTS (ElevenLabs ou Edge) → chunks MP3 → WebSocket → playback
```

Détails du flux :
```
[Client] conversation_start → création conversation "voice"
[Serveur] conversation_started (+ silence_duration_ms, min_speech_ms) puis listening
[Client] Boucle AnalyserNode ; fin de parole → Blob WebM complet → ws.send(blob)
[Serveur] is_processing=true → ignore tout autre binaire ; processing
[Serveur] ElevenLabs Scribe → transcript
[Serveur] **même pipeline que le chat** : `_process_message(..., voice_mode=True, stream=False)` — appelle
          `orchestrator.handle(..., voice_mode=True)` (préfixe LLM `[VOICE_MODE]`, `ctx["voice_mode"]`, historique, mails
          via `append_recent_mails_to_context`, productivité / coach / journal comme le texte) ; blocs `action` exécutés
          ; TTS en streaming via `_send_tts_streaming`
[Client] Accumule chunks ; à speech_done : blob audio/mpeg → Audio.play()
[Client] Audio fini → done_playing
[Serveur] listening → boucle ; is_processing/is_speaking à false
```

**Anti-écho** : binaire ignoré tant que `is_speaking` ou `is_processing` ; buffers micro vidés côté client quand JARVIS parle ; `getUserMedia` avec suppression d'écho / bruit / AGC.

**Latence** : `VOICE_MAX_TOKENS` (défaut 500), STT cloud Scribe (~0.5s), Haiku (~300ms), TTS Edge (~200ms).

**UI React** : orbe canvas (4 phases), transcripts, EQ, historique léger (`web/src/app/components/pages/Voice.tsx`).

### Mode écoute continue (enregistrement long)

**Pas** de réponse entre les phrases. Le client envoie `recording_start` + label, puis des **blobs WebM** toutes les 5 s (`MediaRecorder.start(5000)`), `getUserMedia` **sans** echoCancellation/noiseSuppression. Tant que `active_recording.is_active`, le serveur **accumule** les octets (pas de STT). `recording_stop` → transcription segment par segment (fichiers valides) → `ContinuousRecording._synthesize` (Haiku par morceaux de texte, puis Sonnet) → `create_task` / `calendar_client.create_event` / `add_fact` / `upsert_person` / `save_episode` / notif macOS. Table `recordings` ; WebSocket : `recording_processing`, `recording_transcribing` (progress), `recording_analyzing`, `recording_done`.

### Mode conversation continue (legacy — chat)

Flux historique encore possible depuis le composer chat : `conversation_mode` + segments — voir codebase `main.py` / specs.

### Config

```bash
ELEVENLABS_API_KEY=                 # STT Scribe + TTS (même clé)
ELEVENLABS_VOICE_ID=                # voix TTS ElevenLabs
TTS_ENGINE=edge                     # edge (défaut) ou elevenlabs
TTS_VOICE=fr-FR-VivienneMultilingualNeural
VOICE_SILENCE_DURATION_MS=1200     # fin de phrase ; page /voice (client)
VOICE_MIN_SPEECH_MS=400
VOICE_MAX_TOKENS=500               # réponses ML courtes pour la voix

# Écoute continue (enregistrement long, page /voice)
RECORDING_MAX_DURATION_MIN=180
RECORDING_CHUNK_SIZE_MB=20
RECORDING_SUMMARY_ONLY=false
```

## Structure du projet

```
jarvis/
├── main.py                  # Entry point FastAPI + WebSocket + routes
├── config.py                # Charge .env, expose tous les settings
├── llm.py                   # Client DeepSeek API (chat, stream, classify)
├── actions.py               # execute_action : tâches, mails, terminal, ordinateur…
├── .env                     # Clés API (gitignored)
├── .env.example             # Template
├── CLAUDE.md                # Ce fichier
│
├── agents/
│   ├── __init__.py          # BaseAgent + registry
│   ├── orchestrator.py      # Router (Haiku) → classifie et dispatche
│   ├── school.py            # Notes, résumés, flashcards, RAG cours
│   ├── productivity.py      # Email, calendar, tâches, briefings
│   ├── coach.py             # Relations, émotions, patterns, décisions
│   ├── info.py              # Météo, recherche web, questions rapides
│   ├── journal.py           # Journal intime → extraction insights JSON
│   └── memory.py            # Mémoire transversale, résumés, patterns
│
├── audio/
│   ├── stt.py               # STT ElevenLabs Scribe (API cloud, WebM direct)
│   ├── tts.py               # TTS Edge (défaut) ou ElevenLabs + émotions
│   └── continuous_recorder.py  # Écoute continue : accumulation → Scribe → Haiku + Sonnet → actions
│
├── integrations/
│   ├── mail.py              # Apple Mail (AppleScript, zéro config)
│   ├── calendar_api.py      # Calendar.app (AppleScript)
│   ├── weather.py           # OpenWeatherMap
│   ├── web_search.py        # Tavily API
│   ├── computer.py          # Shell sécurisé, AppleScript, infos système (macOS)
│   ├── code_executor.py     # Exécution de code avancée (Open Interpreter, invisible user)
│   ├── notifications_macos.py  # Notifications bureau macOS
│   └── location.py          # LocationManager — GPS, visites, trajets
│
├── database/
│   ├── __init__.py          # init_db(), get_db() context manager
│   ├── schema.sql           # Schéma SQLite complet (toutes les tables)
│   ├── location_helpers.py  # CRUD localisation (lieux, historique, visites, trajets, patterns)
│   └── queries.py           # Helpers CRUD (save_message, get_life_profile, etc.)
│
├── web/
│   ├── src/
│   │   ├── services/
│   │   │   ├── api.ts          # api REST (BASE vide, proxy Vite)
│   │   │   └── websocket.ts     # ws / jarvisWs
│   │   ├── app/                 # App React, context, vues, pages
│   │   └── main.tsx
│   ├── vite.config.ts
│   └── dist/                    # `pnpm run build`
│
├── prompts/                 # System prompts de chaque agent (fichiers .txt)
│   ├── persona.txt          # Persona JARVIS commune (injectée dans tous les agents user-facing)
│   ├── orchestrator.txt
│   ├── school.txt
│   ├── productivity.txt
│   ├── coach.txt
│   ├── info.txt
│   ├── journal.txt
│   ├── memory.txt
│   ├── email_analyzer.txt   # Prompt JSON-only pour l'analyse d'emails (Haiku)
│   ├── location_analyzer.txt  # Haiku — habitudes géographiques (JSON)
│   ├── continuous_extractor.txt   # Haiku — extraction JSON par morceaux (écoute continue)
│   └── continuous_synthesizer.txt # Sonnet — synthèse finale JSON (écoute continue)
│
├── scripts/                 # Workers async lancés au startup (lifespan FastAPI)
│   ├── email_watcher.py     # Surveillance email proactive (Apple Mail) → tâches/notifs/iMessage auto
│   ├── location_analyzer.py # Analyse Haiku des habitudes géo (23h) → patterns + faits
│   └── scheduler.py         # Briefing matin, analyse géo 23h, tâches en retard, notifs macOS
│
├── data/
│   ├── jarvis.db            # SQLite (auto-créé)
│   └── uploads/             # Documents uploadés
│
├── credentials/             # Réservé (tokens tiers si besoin) ; Mail & Calendar via AppleScript, pas d'OAuth
├── requirements.txt
└── README.md
```

## Base de données SQLite — Schéma complet

Le fichier `database/schema.sql` contient toutes les tables. Les voici regroupées par domaine :

### Mémoire épisodique
- `conversations` : id, started_at, ended_at, agent, summary
- `messages` : id, conversation_id (FK), role (user/assistant), content, agent, model, tokens_in, tokens_out, cost, created_at
- `episodes` : id, agent, content, summary, importance (1-10), tags (JSON), embedding (BLOB), created_at

### Life Coach
- `life_profile` : id, category (values/goals/fears/patterns/strengths), content
- `people` : id, name (UNIQUE), relationship, personality_notes, dynamics, patterns, last_mentioned
- `people_events` : id, person_id (FK), event_type, content, lesson_learned, created_at
- `mood_log` : id, mood_score (1-10), energy_level (1-10), context, triggers, created_at
- `patterns` : id, pattern_type, description, occurrences, first_seen, last_seen, status (active/resolved/monitoring)

### École
- `school_subjects` : id, name (UNIQUE), teacher, schedule (JSON), notes
- `school_documents` : id, subject_id (FK), title, content, doc_type, file_path, embedding (BLOB)
- `school_flashcards` : id, subject_id (FK), question, answer, next_review, ease_factor, interval_days

### Productivité
- `tasks` : id, title, description, priority (high/medium/low), status (todo/doing/done), due_date, category, completed_at
- `email_summaries` : id, gmail_id (UNIQUE), sender, subject, summary, action_needed, priority — **alimentée par `email_watcher`** (UPSERT idempotent par `gmail_id`)
- `daily_briefings` : id, date (UNIQUE), morning_briefing, evening_summary
- `notifications` : id, source (email/pattern/calendar/system), title, content, priority (urgent/high/medium/low), read, email_id, created_at — alimentée par `email_watcher` (mails urgent/high) et lue par `/api/notifications` + UI

### Localisation
- `places` : lieux nommés (catégorie, lat/lng, `radius_meters`, stats)
- `location_history` : points GPS (résolution `place_id` optionnelle)
- `visits` : visites (arrivée, départ, durée, jour de semaine)
- `trips` : trajets entre lieux (durée, distance, mode estimé, `route_points` JSON)
- `location_patterns` : patterns géographiques détectés (Haiku + analyse quotidienne)

### Résumés
- `weekly_summaries` : id, week_start, summary, patterns_spotted (JSON), recommendations (JSON)

### Mémoire profonde (architecture 2 tiers)
- `user_facts` : id, category, content, source (conversation/imessage/journal/manual), confidence, is_current, superseded_by → faits atomiques sur l'utilisateur avec versioning
- `relationship_profiles` : id, person_id (FK), handle, communication_style, response_pattern, topics (JSON), sentiment, power_dynamic, attachment_style, trust_level, interaction_frequency → profils relationnels enrichis
- `relationship_events` : id, person_id (FK), event_date, event_type, summary, impact_on_user, lessons, source → timeline factuelle par relation
- `cross_insights` : id, insight_type, content, people_involved (JSON), evidence, actionable, occurrences, status → patterns transversaux multi-personnes
- `life_context` : id, period_start, period_end, context_type, description, impact_on_mood, impact_on_productivity, active → contexte de vie temporel
- `imessage_analysis_cache` : id, handle (UNIQUE), last_analyzed_rowid, last_analyzed_at, total_messages_analyzed → curseur d'analyse incrémentale par contact

## Conventions de code

### Python
- **Async partout** : FastAPI est async, toutes les fonctions agents sont `async def`
- **Type hints** sur toutes les fonctions
- **f-strings** pour le formatage
- **Pas de classes inutiles** : fonctions simples quand une classe n'apporte rien
- **Gestion d'erreurs** : try/except autour des appels API, jamais crash silencieux
- **Logging** : `import logging; logger = logging.getLogger(__name__)`

### Agents
- Chaque agent hérite de `BaseAgent` (défini dans `agents/__init__.py`)
- Chaque agent a un `name`, un `model` par défaut, une méthode `async handle()`
- Les system prompts sont dans `prompts/{agent_name}.txt` avec des placeholders `{{variable}}`
- Le contexte (life profile, mémoire, etc.) est injecté dans le system prompt avant l'appel LLM
- **Ordre du system prompt** : `[cached: life_profile + memory_context]` → `[agent_instructions]`

### Client LLM (`llm.py`)
- Client **httpx** partagé (pool de connexions) vers l'API DeepSeek (format OpenAI, `/v1/chat/completions`)
- Fonction `chat()` : appel standard avec retry/backoff sur 429/5xx, retourne `{content, tokens_in, tokens_out, cache_hit, cost, model}`
- Fonction `chat_stream()` : générateur async qui yield les chunks (SSE)
- Fonction `quick_classify()` : classification rapide via `DEEPSEEK_FAST_MODEL`, retourne un string
- Fonction `classify_task_type()` : détecte les productions longues → route « heavy »
- **Prompt caching** : automatique côté DeepSeek — le cache hit est lu dans `usage.prompt_cache_hit_tokens`
- **Tracking des coûts** : chaque appel calcule et retourne le coût estimé (`estimate_cost`, table `MODEL_COSTS`)

### Frontend
- **Dark mode par défaut** (fond #0d1117, texte #e6edf3)
- **WebSocket** pour le chat en temps réel + streaming des réponses
- **Audio** : MediaRecorder API pour capturer le micro, envoi en WebSocket binaire
- **SPA** : une seule page HTML, navigation par sections (chat, journal, mémoire, status)
- **Responsive** : fonctionne sur mobile

### API endpoints
- `GET /` → page principale (SPA)
- `WS /ws` → WebSocket chat (texte JSON + audio binaire)
- `POST /upload` → upload de documents (PDF, images, texte)
- `GET /api/memory` → life profile + people
- `POST /api/memory` → modifier le life profile
- `GET /api/status` → stats d'utilisation, agents actifs, coûts
- `GET /api/stats/weekly?days=7` → série d'activité quotidienne (messages, vocal, tokens, coût) + variations jour/jour + totaux
- `GET /api/costs` → dépenses LLM (jour / 7j / mois, par modèle) + budget configuré
- `GET /api/backups`, `POST /api/backups/run` → sauvegardes SQLite (VACUUM INTO, rotation `BACKUP_KEEP`, job 04:15)
- `POST /api/maintenance/run` → purge de rétention (screen/location/logs/notifs lues) + optimisation FTS/WAL (job dim 04:45)
- `GET /api/rituals/today`, `POST /api/rituals/{roast|debrief|quote}/run` → rituels quotidiens (table `daily_rituals`, jobs 18:30 / 21:45 / 07:00)
- `GET /api/productivity/score` → score hebdo déterministe 0-100 (50 + 8×faites − 12×en retard) ; widget TV `/api/rituals` côté serveur TV
- `POST /api/rituals/weekly/run` → debrief hebdo vocal (dim 21:00, TTS daemon)
- `GET /api/mood/signals?days=14` → signaux comportementaux quotidiens (écran + messages, zéro diagnostic, job 23:15)
- `GET /api/stats/compare` → comparatif toi vs toi (7 derniers jours vs 7 précédents, ton neutre)
- `GET/PATCH /api/commitments` → promesses traquées (extraction 22:40, rappel sec des oubliées 10:00)
- `GET/POST/DELETE /api/dnd` → mode « silence total sauf feu » (seul l'urgent passe : TTS daemon, iMessage watcher)
- `GET /api/meetings` → réunions captées (opt-in MEETING_CAPTURE_ENABLED, micro daemon → résumé + actions, table recordings label 'réunion')
- `GET /api/presence` → présence bureau par le son (micro daemon audio ; arrivée = bruit > `PRESENCE_NOISE_RMS` → « Vous êtes là, Monsieur » ; départ = `PRESENCE_TIMEOUT_MIN` min de silence, tick /10 min)

## Outillage DevOps (lot 10-20)

| Domaine | Endpoints | Fichiers |
|---|---|---|
| Migrations SQLite | `GET/POST /api/migrations/status\|run` | `scripts/db_migrations.py`, `database/migrations/` |
| Perf + rollback | (interne, hook loop.py) | `scripts/perf_regression.py` |
| Code dupliqué | `GET /api/quality/duplicates`, `POST .../scan` | `scripts/duplicate_scanner.py` |
| Audit sécurité | `GET /api/quality/security`, `POST .../scan`, `POST .../{id}/fix` | `scripts/security_audit.py` |
| Tests manquants | `POST /api/quality/tests/generate` | `scripts/test_coverage_scan.py` |
| CI locale | `POST /api/quality/ci/run`, `POST .../install-hook` | `scripts/local_ci.py`, `scripts/install_git_hooks.py` |
| Self-healing | `GET /api/self-healing/status`, `POST .../diagnose` | `scripts/self_healing.py` (hook `supervisor.py`) |
| DevAgent — PR auto | `POST /api/devagent/{id}/pr` | `agents/devagent/pr.py` |
| DevAgent — staging | `POST /api/devagent/{id}/deploy`, `GET .../deployments` | `agents/devagent/staging.py` |
| DevAgent — rebase sûr | `POST /api/devagent/{id}/rebase` | `agents/devagent/git_ops.py` |
| DevAgent — refactor | `POST /api/devagent/{id}/refactor` | `agents/devagent/refactor.py` |
| DevAgent — autorun | `POST /api/devagent/autorun` | `agents/devagent/autorun.py` |

Détails et garde-fous de chaque feature dans les docstrings de tête de
chaque module — tous suivent le principe : **report-only sur la codebase
JARVIS elle-même** (jamais de mutation auto du code de l'assistant sans
opt-in explicite), **application + validation automatique réservée aux
projets DevAgent** (isolés, versionnés, testés → rollback trivial).
Self-healing est le plus encadré : diagnostic seul par défaut
(`SELF_HEALING_ENABLED=false`), patch encore plus explicitement opt-in
(`SELF_HEALING_AUTO_APPLY=false`), rollback automatique si la même
boucle de crash revient sous `SELF_HEALING_REGRESSION_WINDOW_MIN`.
- `GET /api/journal` → historique du journal
- `POST /api/journal` → nouvelle entrée journal

## Flux de données

### Message texte normal
```python
# 1. User envoie via WebSocket
msg = {"type": "text", "content": "J'ai un exam de droit demain"}

# 2. main.py reçoit → orchestrateur classifie
category = await orchestrator.classify(msg["content"])  # → "SCHOOL"

# 3. Contexte mémoire construit
context = orchestrator.build_context()  # life_profile + recent_episodes + patterns

# 4. Agent spécialisé traite
agent = get_agent(category.lower())  # → SchoolAgent
result = await agent.handle(msg["content"], conversation_id, context)

# 5. Réponse streamée au client
await ws.send_json({"type": "response", "agent": "school", "content": result["response"]})

# 6. Mémoire mise à jour
memory_agent.maybe_store(msg["content"], result["response"], agent="school")
```

### Message audio (push-to-talk, conversation legacy, mains libres)

Après STT, **le même** `_process_message()` que le texte (`main.py`) : `save_message` user, `orchestrator.handle_stream` (push / legacy avec `voice_mode=True` → en interne `stream=False` et `orchestrator.handle(..., voice_mode=True)`) ou `handle` seul, extraction `action`, TTS si demandé. Pas de `handle_voice` séparé.

## Mémoire profonde — Architecture 2 tiers

### Principe

1. **Haiku (Extracteur)** : lit les messages bruts (iMessage, conversations JARVIS) et extrait des données structurées en JSON → stockées dans 6 tables spécialisées. C'est cheap (~$0.002 par analyse). Il ne raisonne pas, il EXTRAIT.
2. **Sonnet/Opus (Raisonneur)** : ne voit JAMAIS les messages bruts. Il reçoit uniquement les données structurées via `build_full_context()` dans son contexte, et RAISONNE avec. Le contexte est dense et petit → cache hits massifs.

### build_full_context()

Fonction centrale dans `database/__init__.py` qui assemble toutes les données mémoire en un seul appel :

```python
def build_full_context() -> dict:
    return {
        "user_facts": get_all_facts_summary(),      # {category: [facts]}
        "life_profile": get_life_profile(),          # {category: [items]}
        "active_patterns": get_active_patterns(),    # [pattern dicts]
        "active_life_context": get_active_life_context(),  # [life_context dicts]
        "recent_moods": get_recent_moods(14),        # [mood dicts]
        "people_profiles": get_all_relationship_profiles(),  # [profile dicts avec JOIN people]
        "cross_insights": get_active_insights(),     # [insight dicts]
        "recent_episodes": get_recent_episodes(10),  # [episode dicts]
    }
```

L'orchestrateur formate ce dict en texte dense injecté dans `memory_context` du system prompt. Sections : `[LIFE_PROFILE]`, `[USER_FACTS]`, `[PEOPLE]`, `[RECENT_EPISODES]`, `[ACTIVE_PATTERNS]`, `[CROSS_INSIGHTS]`, `[LIFE_CONTEXT]`, `[MOOD]`.

### Analyse relationnelle iMessage

- **`integrations/imessage_reader.py`** : lecteur READONLY de `~/Library/Messages/chat.db`. Méthodes : `get_all_contacts()`, `get_conversation(handle, limit, since_rowid)`, `get_recent_conversation`, **`get_conversation_for_period(handle, days, limit)`** (fenêtre glissante pour analytics), `search_messages(query)`.
- **`scripts/contact_analytics.py`** : classe **`ContactAnalytics`** — métriques relationnelles **sans LLM** (score proximité, tendance, heatmap sentiment heuristique, sujets par mots, non-répondus, patterns de réponse, etc.). Singleton **`contact_analytics`**.
- **`scripts/contact_alerts.py`** : **`check_relationship_alerts()`** — silence inhabituel vs fréquence moyenne, dernier message entrant sans réponse > 24 h → **`create_notification`** (source `relationship`) ; anti-doublon titre récent.
- **`scripts/timeline_generator.py`** : **`generate_timeline(name)`** — historique iMessage découpé par blocs de 50 messages, extraction JSON Haiku des événements marquants.
- **`scripts/scheduler.py`** : job **`relationship_alerts`** — **`CronTrigger(hour='*/6')`** appelle **`check_relationship_alerts`**.
- **`integrations/imessage.py`** : **`send_imessage_to_address(address, text)`** — envoi osascript vers un destinataire arbitraire (utilisé par **`POST /api/people/{name}/send`**).
- **`scripts/relationship_analyzer.py`** : worker background. 3 modes : `run_initial_scan()` (boot), `run_daily_update()` (3h du matin), `analyze_single_contact(name)` (à la demande).
- **`prompts/imessage_extractor.txt`** : prompt Haiku qui extrait `person`, `facts_about_user`, `facts_about_contact`, `notable_events`, `patterns_observed` en JSON.
- **Pipeline** : messages découpés en batches de 50 → Haiku extrait → JSON parsé → stocké via `upsert_person`, `upsert_relationship_profile`, `add_fact`, `add_relationship_event`, `find_or_create_pattern`. Curseur incrémental via `imessage_analysis_cache`.
- **Contacts.app (`integrations/contacts.py`)** : lecture du carnet macOS via AppleScript pour mapper téléphones / emails → nom affiché. Au démarrage : `contacts_reader.build_cache()` puis logs d’exemple ; en arrière-plan : `scripts/sync_contacts.sync_people_names()` met à jour la table `people` lorsque le champ `name` est encore un numéro ou un email (fusion si une entrée avec le vrai nom existe déjà). **`POST /api/contacts/sync`** force cette synchronisation. **`GET /api/contacts`** enrichit les handles iMessage avec `resolve_handle()` quand le cache est disponible. Permission macOS : Automation pour Contacts.app. L’analyseur (`relationship_analyzer`) utilise `likely_name` (Haiku) si présent, sinon `resolve_handle(handle)` avant `upsert_person`.
- **Vue Contacts (UI + API)** : `GET /api/people` utilise `get_people_sorted_by_recent()` qui retourne maintenant `message_count` (colonne `imessage_count` synchronisée depuis `imessage_analysis_cache`). Description IA (`GET` / `POST .../description*`). **`PATCH /api/people/{name}`** (renommage). **`POST .../ask`** (Sonnet, logs `[contact_chat]`). **Analytics** : **`GET /api/people/{name}/analytics`** → `ContactAnalytics.compute_all()` (Python pur). **Timeline Haiku** : **`GET /api/people/{name}/timeline`**. **Actions** : **`POST .../send`** (`send_imessage_to_address`), **`POST .../suggest-message`** (Haiku), **`POST .../remind`** → `create_task` catégorie `relation`. UI : sections score, tendance, sentiment, sujets, non-répondus, échanges, patterns, dates, actions, timeline ; même style glass que le reste.
- **Renommage automatique** : lors de l'analyse iMessage, si le nom du contact est un numéro de téléphone et que Haiku retourne un `likely_name`, le contact est automatiquement renommé via `rename_person_if_phone_number()`.

### Agent mémoire enrichi

`agents/memory.py` stocke maintenant aussi dans les nouvelles tables :
- `facts_learned` → `add_fact()` (catégorie + contenu + confidence)
- `life_context_change` → `add_life_context()` (type + description)
- `cross_insights` → `add_cross_insight()` (patterns multi-personnes)
- `add_event` sur people → `add_relationship_event()` si événement significatif

### Endpoints

| Route | Méthode | Description |
|---|---|---|
| `/api/analyze-contact` | POST | `{name}` → lance l'analyse Haiku d'un contact iMessage |
| `/api/contacts/sync` | POST | Re-synchronise les noms dans `people` depuis Contacts.app (numéros/emails → noms) |
| `/api/people` | GET | Liste des contacts triés par dernière interaction ( `last_mentioned` puis événements, puis `created_at` ) |
| `/api/people/{name}` | PATCH | Met à jour la fiche (`name`, `relationship`, etc.) — même nom insensible à la casse ; collision de nom → 409 |
| `/api/people/{name}/analytics` | GET | Métriques iMessage calculées (`contact_analytics`, sans LLM) |
| `/api/people/{name}/timeline` | GET | Chronologie événements Haiku (`generate_timeline`) |
| `/api/people/{name}/send` | POST | `{text}` envoi iMessage |
| `/api/people/{name}/suggest-message` | POST | Suggestion de message Haiku |
| `/api/people/{name}/remind` | POST | `{when}` création tâche relation |
| `/api/people/{name}/ask` | POST | `{"question":"..."}` — réponse Sonnet contextualisée (profil + timeline + derniers messages iMessage via `get_recent_conversation` / handle profil) |
| `/api/people/{name}/description` | GET | Description courte en cache (`people.ai_description`) ou génération Haiku puis cache |
| `/api/people/{name}/description/refresh` | POST | Efface le cache et régénère la description |
| `/api/relationship/{name}` | GET | Profil complet : person + relationship_profile + timeline |

## Prompt caching — implémentation

```python
# Dans llm.py — chaque appel agent structure le system prompt ainsi :
system_blocks = [
    {
        "type": "text",
        "text": f"{life_profile_text}\n\n{memory_context}",
        "cache_control": {"type": "ephemeral"}  # ← ce bloc est caché
    },
    {
        "type": "text",
        "text": agent_instructions  # ← ce bloc change par agent
    }
]
```

Le life profile + mémoire (~4000 tokens) est identique entre les appels → cache hit → -90% sur ces tokens input.

## Escalade Opus

L'agent Coach doit détecter quand escalader vers Opus. Critères :
- Décision de carrière, rupture, déménagement, investissement
- L'utilisateur dit explicitement "c'est important" ou "j'ai besoin de réfléchir sérieusement"
- Le mood_score est < 3 (crise émotionnelle)
- Le sujet implique plusieurs personnes et des dynamiques complexes

Implémentation : le Coach fait un pre-check rapide (Haiku, 20 tokens) : "Ce sujet nécessite-t-il une analyse profonde ? OUI/NON". Si OUI → Opus.

## Variables d'environnement (.env)

```bash
DEEPSEEK_API_KEY=sk-...
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_FAST_MODEL=deepseek-v4-flash   # classification, triage, extraction
DEEPSEEK_MAIN_MODEL=deepseek-v4-pro     # rédaction, coaching, raisonnement
HEAVY_TASK_MAX_TOKENS=8192              # plafond tokens des productions longues
ELEVENLABS_API_KEY=         # STT Scribe + TTS (même clé)
ELEVENLABS_VOICE_ID=        # voix TTS ElevenLabs
TTS_ENGINE=edge             # edge (défaut) ou elevenlabs
TTS_VOICE=fr-FR-VivienneMultilingualNeural
WEATHER_API_KEY=...
WEATHER_CITY=Lille
TAVILY_API_KEY=...
DB_PATH=./data/jarvis.db
UPLOAD_DIR=./data/uploads
USER_NAME=Nolann
LANGUAGE=fr
TIMEZONE=Europe/Paris
WEB_PORT=8080

# Accès ordinateur local (macOS)
COMPUTER_ACCESS=true
COMPUTER_SHELL=/bin/zsh
COMPUTER_TIMEOUT=30

# Exécution de code avancée
CODE_EXECUTOR_ENABLED=true       # active/désactive le moteur avancé
CODE_EXECUTOR_TIMEOUT=120        # timeout en secondes
CODE_EXECUTOR_MODEL=             # modèle Claude utilisé (défaut: Sonnet)

# Calendrier : Calendar.app (aucune clé) — comptes iCloud/Google déjà dans l'app
# Startup : ouvrir Calendar.app au démarrage réduit les erreurs AppleScript `-600` ("application not running")
# Notifications macOS (display notification)
DESKTOP_NOTIFICATIONS=true
NOTIFICATION_SOUND=Glass
```

### Correctif Calendar (mai 2026)

- `actions.py` (`calendar_create`) doit accepter les alias de payload LLM :
  - `summary|title`
  - `start|date`
  - `notes|description`
- `integrations/calendar_api.py::create_event` doit accepter `end` optionnel (défaut `start + 1h`).
- Formats de date supportés pour `start`/`end` :
  - absolus : `YYYY-MM-DD HH:MM`, `YYYY-MM-DDTHH:MM`, `DD/MM/YYYY HH:MM`
  - naturels : `demain 14h`, `vendredi 10:00`, `14:00` (aujourd'hui)
- Endpoint de debug rapide :
  - `POST /api/calendar/test` crée un événement test "TEST JARVIS — à supprimer".

## Module IA Personnelle — Conversations persistantes

### Architecture

Le module Chat (`/chat`) offre un système de conversations persistantes type Claude, intégré dans le design BIG BROTHER.

### Tables DB

**`conversations`** enrichie avec : `title`, `pinned`, `archived`, `tags`, `last_message_at`, `message_count`.
Migration idempotente via `_migrate_conversations()` dans `init_db()`.

**`conversation_documents`** (nouvelle) — textes extraits des fichiers uploadés, injectés automatiquement dans le contexte LLM.

### Helpers DB (`database/__init__.py`)

`get_conversations`, `get_conversation_detail`, `update_conversation`, `update_conversation_activity`, `delete_conversation`, `search_conversations`, `save_conversation_document`, `get_conversation_documents`.

### Auto-titrage

`_maybe_title_conversation(conv_id)` — appelée via `asyncio.create_task(...)` après chaque message. Génère un titre 3-6 mots avec Haiku si la conversation a au moins 1 message user ET 1 message assistant (pas encore de titre).

### Documents dans le contexte

`_process_message()` préfixe le contenu des docs attachés (`[DOCUMENT: nom]\n{extracted_text[:3000]}`) au message de l'utilisateur avant d'appeler l'orchestrateur.

### Action SEARCH_CONVERSATIONS

`actions.py` — `_action_search_conversations()` : recherche dans toutes les conversations via `search_conversations()`, résultats formatés pour la 2e passe LLM. Ajoutée à `ACTIONS_WITH_FOLLOWUP` dans `main.py`.

`prompts/persona.txt` — action documentée : déclenche sur "on avait parlé de...", "cherche dans nos conversations...".

### WebSocket multi-conversations

- `{"type": "switch_conversation", "conversation_id": N}` → change la conv active
- `{"type": "new_conversation"}` → crée une nouvelle conv
- Serveur → `conversation_switched` + `conversation_updated` (titre + message_count)

### Endpoints

| Route | Méthode | Description |
|---|---|---|
| `/api/conversations/search` | GET | `?q=` — recherche contenu + titres |
| `/api/conversations` | GET | Liste (`archived`, `limit`) |
| `/api/conversations/{id}` | GET | Détail + messages + docs |
| `/api/conversations/{id}` | PATCH | `title`, `pinned`, `archived`, `tags` |
| `/api/conversations/{id}` | DELETE | Suppression complète |
| `/api/conversations/{id}/archive` | POST | Archiver |
| `/api/conversations/{id}/pin` | POST | Basculer épinglé |
| `/api/conversations/{id}/upload` | POST | Upload fichier (PDF/txt/md/csv/json/py…) |

### Frontend (`web/src/app/components/views/ChatView.tsx`)

Layout 2 colonnes :
- **Sidebar** : bouton "Nouvelle conversation", recherche, liste groupée (Épinglées / Aujourd'hui / Hier / 7j / Plus anciennes), menu contextuel (Renommer / Épingler / Archiver / Supprimer)
- **Chat** : header titre éditable + compteur, messages scrollables + streaming progressif, composer (drop zone, textarea auto-resize, commandes slash `/nouveau /cherche /briefing /tâche`, bouton upload)
- **Écran bienvenue** : logo J + 4 suggestions cliquables pour nouvelle conversation

Navigation : `/chat` est la route par défaut de l'app (`App.tsx`).

## Pipeline unifié — JARVIS a accès à TOUT, partout

**Règle absolue** : `_process_message()` dans `main.py` est le seul point d'entrée pour parler à JARVIS. Chat, voix, recherche, contacts, journal — tout passe par ce pipeline. JARVIS a toujours accès à l'ensemble des données disponibles.

### Architecture du pipeline

```
Entrée utilisateur (WS texte, WS audio, REST endpoint)
 │
 ▼
_build_enriched_context(text, conversation_id)
 ├── PERMANENT : documents attachés à la conversation
 └── CONDITIONNEL (mots-clés) :
     ├── Mails/contacts → Mail.app
     ├── Planning/agenda → Calendar.app
     ├── Météo → OpenWeatherMap
     ├── Tâches → DB tasks
     ├── Localisation → location_manager
     ├── Fichiers/docs → school_documents + recordings
     └── Mémoire passée → conversations récentes
 │
 ▼
orchestrator.handle(text, conversation_id, context=enriched_ctx)
 ├── build_context() : life_profile, user_facts, people, patterns, location...
 ├── context.update(enriched_ctx) : merge du contexte externe
 └── dispatch → agent spécialisé
 │
 ▼
_extract_action_from_text() → execute_action() → 2e passe si ACTIONS_WITH_FOLLOWUP
 │
 ▼
finalize_assistant_display_text() → save_message() → _maybe_title_conversation()
```

### Trois fonctions, un pipeline

| Fonction | Usage | WebSocket | TTS |
|---|---|---|---|
| `_build_enriched_context(text, conv_id)` | Assemble le contexte enrichi | — | — |
| `_process_message_internal(text, conv_id)` | Pipeline complet pour endpoints REST | Non | Non |
| `_process_message(ws, text, conv_id, ...)` | Pipeline complet pour WebSocket | Oui | Optionnel |

`_process_message()` appelle `_build_enriched_context()` puis envoie via WebSocket.
`_process_message_internal()` appelle `_build_enriched_context()` puis retourne un dict.

### Points d'entrée REST qui utilisent le pipeline unifié

- `POST /api/journal` → `_process_message_internal()` (routé JOURNAL automatiquement)
- `POST /api/people/{name}/ask` → message enrichi du contexte personne → `_process_message_internal()`

### Détection de contexte conditionnel (mots-clés)

```python
# Mails : "mail", "email", "courrier", "boîte", "reçu", "envoyé" + noms de personnes connues
# Calendar : "planning", "agenda", "rdv", "demain", "cours", "emploi du temps"
# Météo : "météo", "temps", "pluie", "soleil", "parapluie", "chaud", "froid"
# Tâches : "tâche", "todo", "à faire", "en retard", "deadline", "échéance"
# Localisation : "où", "position", "lieu", "ici", "maison", "bureau"
# Fichiers : "fichier", "document", "cours", "pdf", "rapport", "devoir"
# Mémoire : "on avait", "la dernière fois", "tu te souviens", "on a parlé"
```

### Frontend — WebSocket singleton

`ws.connect()` est appelé **une seule fois** dans `BigBrotherLayout.tsx` au montage du layout. Tous les composants (`ChatView`, `SearchView`, etc.) utilisent le singleton `ws` exporté par `websocket.ts` directement — ils appellent `ws.on()` et `ws.sendText()` sans jamais appeler `ws.connect()`.

### Page Recherche (SearchView)

`/search` expose deux modes :
- **Recherche rapide** : filtrage client (conversations, contacts, tâches, documents scolaires) avec mise en évidence des termes
- **Demander à JARVIS** : la question est envoyée via `ws.sendText()` — JARVIS peut utiliser les actions `search_conversations`, `imessage_search`, `find_file` pour fouiller dans toutes les données. La réponse s'affiche en streaming au-dessus des résultats filtrés.

## Phases de construction

### Phase 1 — Fondations
Créer : `main.py`, `config.py`, `llm.py`, `database/`, `agents/__init__.py`, `agents/orchestrator.py`, interface web chat basique avec WebSocket. Objectif : envoyer un message texte → router → réponse d'un agent par défaut.

### Phase 2 — Agent École
Créer : `agents/school.py`, pipeline upload documents (PDF → extraction texte), résumés, fiches de révision, flashcards. RAG avec embeddings sur les docs de cours.

### Phase 3 — Audio
Créer : `audio/stt.py` (ElevenLabs Scribe), `audio/tts.py` (Edge TTS / ElevenLabs), intégration WebSocket audio. Objectif : parler au micro → réponse vocale.

### Phase 4 — Productivité
Créer : `integrations/mail.py` (Apple Mail via AppleScript), `integrations/calendar_api.py`, `agents/productivity.py`. Briefing matin automatique (cron/launchd).

### Phase 5 — Life Coach
Créer : `agents/coach.py`, `agents/journal.py`, `agents/memory.py`. Interface d'onboarding life profile. Détection de patterns. People memory.

## Règles absolues

1. **Jamais de crash silencieux** — log l'erreur, retourne un message d'erreur à l'utilisateur
2. **Jamais de clé API dans le code** — tout dans `.env`
3. **Toujours tracker les coûts** — chaque appel LLM sauvegarde tokens + cost dans la DB
4. **Le français est la langue par défaut** — tous les prompts, l'UI, les réponses
5. **Le code doit tourner en local sur Mac** — pas de dépendance cloud obligatoire sauf l'API LLM (DeepSeek)
6. **SQLite, pas PostgreSQL** — simplicité pour un usage solo
7. **Pas de Docker** — exécution directe avec `python main.py`

## Outil de réparation — Sync profonde macOS (mai 2026)

Script ajouté : `scripts/force_full_mac_sync.py`

### Rôle

Forcer une synchronisation exhaustive des contacts + conversations iMessage depuis macOS vers SQLite JARVIS, en corrigeant les dates.

### Détails techniques

- **Contacts** (`integrations/contacts.py`)
  - extraction hybride : SQLite AddressBook (`AddressBook-v22.abcddb`) + fallback AppleScript
  - cache handle/email/numéro consolidé pour résolution robuste

- **iMessage** (`integrations/imessage_reader.py`)
  - `get_all_conversation_stats_full()` lit `chat`, `chat_handle_join`, `handle`, `message`
  - récupère `msg_count`, `first_message_at`, `last_message_at`, `last_rowid`

- **Conversion date Cocoa (Apple Epoch)**
  - SQL exact :
  - `CASE WHEN ABS(m.date) > 1000000000000 THEN (m.date / 1000000000.0) + 978307200 ELSE m.date + 978307200 END`
  - couvre nanosecondes et secondes selon version macOS

- **UPSERT massif DB** (`database/__init__.py`)
  - `force_upsert_people_from_mac_sync(records)`
  - met à jour `people.last_mentioned`, `people.imessage_count`
  - upsert `relationship_profiles.handle`
  - alimente `imessage_analysis_cache` avec `last_analyzed_rowid` réel

### Check santé sync (mai 2026)

Script ajouté : `scripts/imessage_sync_health_check.py`

Vérifie en un appel :
- backend unique (port 8081 + process `main.py`)
- accès `~/Library/Messages/chat.db`
- statut API (`/api/status`) dont `imessage.available`
- métriques DB (`duplicate_handle_groups_count`, profils orphelins, cache rowid)
- erreurs critiques récentes (`data/.jarvis_restart/backend.log`)

Commande :
```bash
source venv/bin/activate
python scripts/imessage_sync_health_check.py
```

## Daemon JARVIS — sentinelle permanente

Le **daemon** est lancé au démarrage de FastAPI (lifespan) via `asyncio.create_task` à côté du serveur web. Il transforme JARVIS d'« assistant qui répond » en **majordome qui veille en permanence** : screen watcher, surveillance iMessage / Mail, rappels Calendar, file TTS jouée localement (`afplay`), wake word Porcupine optionnel.

### Principe — 3 niveaux d'arbitrage

```
[1] Pixel diff (Pillow)                  → 0 token, 0 LLM
       ↓ (changement >= SCREEN_CHANGE_THRESHOLD %)
[2] LLM local (Ollama qwen2.5-vl)        → 0 token API, ~500 ms
       ↓ (analysis["notable"] non vide)
[3] Claude (Haiku via _process_message_internal)  → tokens réels, voix JARVIS
       ↓
TTS Edge / Kokoro / ElevenLabs → afplay (local) ou file `/api/devices/{id}/tts` (distant)
```

95 % du travail tourne en local (Ollama). Claude API ne reçoit **que des résumés texte** (`activity`, `notable`) — jamais d'images.

### Architecture multi-machines

- **Mac Mini = serveur** : tourne JARVIS + daemon + Ollama (`qwen2.5-vl:7b` + `qwen2.5:7b`).
- **MacBook Pro / autre Mac = client léger** : `scripts/jarvis_agent.py` (script autonome, dépendances minimales `requests` + `Pillow`). Capture l'écran, envoie au serveur via Tailscale, joue le TTS reçu via `/api/devices/{id}/tts`.
- Connexion via Tailscale (`http://100.x.x.x:WEB_PORT`) avec `auth_token` généré au premier `register_device`.

### Modules

| Fichier | Rôle |
|---|---|
| `scripts/screen_watcher.py` | `screen_watcher` (singleton). `start()` boucle de capture → diff pixel → analyse Ollama si changement. Callbacks `on_notable(notable, context)` et `on_idle(minutes)` branchés par le daemon. |
| `scripts/jarvis_daemon.py` | `daemon` (singleton). 5+ boucles parallèles : `_tts_loop`, `_notification_loop` (iMessage + Mail), `screen_watcher.start()`, `_calendar_reminder_loop` (toutes les 5 min), `_device_health_loop` (toutes les 30 s), `_wake_word_loop` (optionnel). |
| `scripts/jarvis_agent.py` | Client autonome distant — un seul fichier, threads `_heartbeat_loop` / `_screen_loop` / `_tts_poll_loop`. Aucune logique IA — tout est délégué au serveur. |

### Tables SQLite

| Table | Colonnes clés |
|---|---|
| `screen_activity` | `device, app, activity, mood, notable, screenshot_hash, change_pct, created_at` |
| `app_usage` | `device, app, date, duration_seconds, session_count` (UNIQUE sur `device, app, date`) |
| `devices` | `device_id, device_name, device_type, is_active, is_online, last_heartbeat, ip_tailscale, auth_token` |
| `work_sessions` | `device, app, started_at, ended_at, duration_min, description` |

Helpers DB (dans `database/__init__.py`) :
- Écran : `save_screen_activity`, `get_screen_activity(hours, device)`, `get_current_screen_context(device)` (≤ 5 min)
- Apps : `upsert_app_usage(device, app, seconds)` (incrémente sur clé unique), `get_app_usage(date, device)`, `get_app_usage_range(days, device)`
- Devices : `register_device` (génère `auth_token`), `update_device_heartbeat`, `set_active_device`, `get_active_device`, `get_all_devices`, `mark_device_offline`
- Sessions : `start_work_session`, `end_work_session`, `get_work_sessions(days)`

### Triage local (Ollama qwen2.5:7b)

`JarvisDaemon._local_triage(event_description)` envoie à Ollama un prompt qui réclame OUI/NON. Coût Claude API = 0 token. En cas d'échec ou de doute → False (silence > faux positifs).

Règles : message personnel → OUI ; mail urgent / pro important → OUI ; spam, newsletter, notif système → NON ; message de groupe sans mention directe → NON.

### Pipeline iMessage du daemon

À distinguer de `integrations/imessage.py` (bridge iMessage qui écoute uniquement `IMESSAGE_TARGET`) : ici, le daemon scanne **tous les nouveaux messages** de tous les contacts. À l'init, on lit `MAX(ROWID)` pour ne pas retraiter le backlog. Pour chaque nouveau message reçu (`is_from_me=0`) :

1. Résolution du nom via `contacts_reader.resolve_handle()`.
2. Triage local Ollama (~500 ms).
3. Si **OUI** → Claude formule une notification courte → `tts_queue.put(text)`.
4. Toujours : `create_notification(source="imessage", priority=...)` pour l'historique UI.

### Wake word "Jarvis"

Désactivé par défaut (`WAKE_WORD_ENABLED=false`). Activation : compte Picovoice (gratuit perso) + `pip install pvporcupine pyaudio`.

À la détection : daemon bascule en mode `conversation` → `tts_queue.put("Oui Monsieur, je vous écoute.")` → boucle `_listen_with_vad` (VAD volume basique pyaudio) → `stt.transcribe` → `_process_message_internal(..., voice_mode=True)` → `tts_queue.put(result["text"])`. Termine sur silence > 15 s ou phrases du genre "merci jarvis", "c'est tout jarvis".

### Endpoints API

| Route | Méthode | Description |
|---|---|---|
| `/api/devices/register` | POST | `{device_id, device_name, device_type, ip_tailscale?}` → `{token}` |
| `/api/devices/{id}/heartbeat` | POST | Maintien en ligne (toutes les 30 s côté agent) |
| `/api/devices/{id}/screen` | POST | Reçoit screenshot base64 → analyse Ollama → save_screen_activity → notif TTS éventuelle |
| `/api/devices/{id}/tts` | GET | Polling : retourne un MP3 base64 à jouer sur le device distant |
| `/api/devices/{id}/activate` | POST | Marque cette machine comme `is_active=1` (les autres à 0) |
| `/api/devices` | GET | `{devices, active}` |
| `/api/screen-activity` | GET | `?hours=24&device=...` |
| `/api/screen-activity/current` | GET | dernier contexte (≤ 5 min) |
| `/api/app-usage` | GET | `?days=7&device=...` (style Screen Time) |

File TTS par device : `_device_tts_queues: dict[device_id, asyncio.Queue]`. Quand l'analyse Ollama d'un screenshot distant retourne un `notable`, Claude formule une notification → TTS local → base64 → mis dans la queue → l'agent vient la chercher en GET.

### Intégration au pipeline

`_build_enriched_context` (dans `main.py`) ajoute deux clés :

- **`screen_context`** (toujours injecté si dispo, ≤ 5 min) : `Écran : VS Code — code Python (mood: focused)`.
- **`screen_time_context`** (conditionnel sur mots-clés `temps`, `productivité`, `screen time`, `distrait`, `procrastin`, …) : top 10 apps avec minutes du jour.

L'orchestrateur reçoit ces clés dans le contexte → JARVIS peut répondre « je vois que tu codes en Python… » sans qu'on lui ait dit, ou « tu as passé 3h sur YouTube aujourd'hui ».

### Frontend

`web/src/services/api.ts` :
- `api.getDevices()` → `{devices, active}`
- `api.activateDevice(id)`
- `api.getScreenActivity(hours, device?)`, `api.getCurrentScreenContext(device?)`
- `api.getAppUsage(days, device?)`
- Types : `DeviceInfo`, `ScreenActivityRow`, `AppUsageRow`

`web/src/app/components/views/Dashboard.tsx` : widget **Machines** dans la section bottom (icône Monitor / Laptop / Smartphone selon `device_type`, point en ligne, badge `ACTIF`, bouton Activer).

### Setup Ollama (Mac Mini)

```bash
brew install ollama
ollama serve
ollama pull qwen2.5-vl:7b   # vision pour screenshots (~5 GB)
ollama pull qwen2.5:7b      # triage léger (~5 GB)
ollama ps    # voir RAM utilisée
```

### Variables d'env

```bash
DAEMON_ENABLED=true
SCREEN_WATCHER_ENABLED=true
SCREEN_WATCHER_INTERVAL=12
SCREEN_CHANGE_THRESHOLD=5
SCREEN_ANALYSIS_THRESHOLD=15
SCREEN_VISION_MODEL=qwen2.5-vl:7b
TRIAGE_MODEL=qwen2.5:7b
OLLAMA_URL=http://localhost:11434
DEVICE_ID=                       # vide = hostname système
DEVICE_NAME=Mac Mini M4
WAKE_WORD_ENABLED=false
PORCUPINE_ACCESS_KEY=
DAEMON_TTS_COOLDOWN=30           # secondes anti-spam vocal en mode veille
```

### Permissions macOS requises

- **Enregistrement d'écran** (sur la machine où le screen watcher tourne) : Réglages > Confidentialité > Enregistrement de l'écran → ajouter Terminal / Cursor.
- **Automation** pour System Events (lecture de l'app au premier plan via AppleScript).
- **Full Disk Access** (déjà requis par le bridge iMessage existant).

### Lancer l'agent sur une machine distante

```bash
# Sur le MacBook (Tailscale activé)
python3 -m venv venv-agent && source venv-agent/bin/activate
pip install -r requirements-agent.txt   # juste requests + Pillow

# Récupérer le token via curl ou via /api/devices/register
python scripts/jarvis_agent.py --server http://100.123.50.38:8081 --token abc123...
```

L'agent enregistre automatiquement la machine, envoie un heartbeat toutes les 30 s, capture l'écran toutes les 12 s, et joue les TTS reçus via `afplay`.

## TV Browser MCP Bridge — Contrôle navigateur TV via CDP

Kiwi Browser (Chromium 137 open-source) est installé sur la TV Philips. Un bridge MCP permet à Cursor de contrôler le navigateur TV directement via le Chrome DevTools Protocol.

### Architecture

```
Cursor IDE (MCP Client)
  ↓ stdio JSON-RPC
scripts/tv_mcp_server.py (Python, 7 outils MCP)
  ↓ HTTP CDP (localhost:9222)
ADB forward tcp:9222 → localabstract:chrome_devtools_remote
  ↓
Kiwi Browser (com.kiwibrowser.browser) sur TV Philips (192.168.3.82)
  ↓
Dashboard JARVIS WAR ROOM (http://192.168.3.52:5174/)
```

### Outils MCP

| Outil | Action |
|-------|--------|
| `tv_navigate` | Ouvre une URL sur la TV |
| `tv_screenshot` | Capture d'écran TV (base64 PNG via ADB screencap) |
| `tv_get_info` | Titre + URL de la page active |
| `tv_open_dashboard` | Ouvre le dashboard War Room |
| `tv_refresh` | F5 sur la page active |
| `tv_press_key` | Touche clavier (HOME, BACK, DPAD_UP/DOWN/LEFT/RIGHT/CENTER) |
| `tv_status` | État connexion ADB + CDP + dashboard |

### Fichiers

| Fichier | Rôle |
|--------|------|
| `scripts/tv_mcp_server.py` | Serveur MCP — communication stdin/stdout JSON-RPC |
| `scripts/launch_tv_browser.sh` | Script shell : ADB connect + réveil TV + lancement Kiwi + forward CDP |
| `tv/com.jarvis.tv-browser.plist` | LaunchAgent macOS — démarrage auto au boot |
| `~/.cursor/mcp.json` | Configuration Cursor → pointe sur `tv-browser` MCP server |

### Quick start

```bash
# Lancement manuel du bridge TV
bash scripts/launch_tv_browser.sh

# Test MCP
echo '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"tv_status","arguments":{}}}' \
  | python3 scripts/tv_mcp_server.py

# Test CDP direct
curl -s http://localhost:9222/json/list | python3 -m json.tool

# Puppeteer via CDP
node -e "
const puppeteer = require('puppeteer-core');
(async () => {
  const browser = await puppeteer.connect({browserURL:'http://localhost:9222'});
  const pages = await browser.pages();
  console.log(await pages[0].title());
  await browser.disconnect();
})();
"
```

### Variables d'env

```bash
TV_IP=192.168.3.82        # IP TV Philips
TV_ADB_PORT=5555          # Port ADB TV
CDP_LOCAL_PORT=9222       # Port local bridge CDP
TV_DASHBOARD_URL=http://192.168.3.52:5174/  # Dashboard URL
```

## Lot 1 — Prédictions et rétrospectives (données déjà en base, aucun ML)

Neuf features additionnelles, toutes des heuristiques déterministes sur des
données déjà collectées (iMessage, GPS, app_usage, tasks, commitments) —
jamais de modèle entraîné, chaque score expose son raisonnement (`explanation`
ou `factors`).

| Module | Rôle |
|---|---|
| `scripts/message_predictor.py` | Probabilité qu'un contact écrive bientôt (intervalle médian, heure/jour habituels) |
| `scripts/favorite_places.py` | Lieux favoris (`visit_count` ≥ seuil) + détection d'opportunités manquées (lieu délaissé) |
| `scripts/doomscroll_detector.py` | Journées où le temps sur les apps à risque dépasse `DOOMSCROLL_DAILY_MINUTES` |
| `scripts/procrastination_cost.py` | Coût (heures + estimation monétaire optionnelle) des tâches laissées en plan |
| `scripts/jarvis_journal.py` | Journal quotidien tenu par JARVIS lui-même (point de vue majordome), 23:50 |
| `scripts/day_scoring.py` | Score « journée exceptionnelle » + « indice de chance » (formule fixe, calculé au debrief 21:45) |
| `scripts/commitment_consistency.py` | Score de cohérence promesses/actions (ratio tenus/résolus, étend `commitments`) |
| `scripts/relationship_graph.py` | Graphe vivant des relations (nœuds `people`, arêtes utilisateur↔contact + contact↔contact via `cross_insights`) |
| `scripts/time_machine.py` | Reconstruction chronologique d'une journée (messages, tâches, visites, humeur, écran, journal) — sans photos/appels/musique |

### Endpoints

| Route | Méthode | Description |
|---|---|---|
| `/api/predictions/messages` | GET | Prédictions triées par probabilité décroissante |
| `/api/places/favorites` | GET | Lieux les plus fréquentés |
| `/api/places/missed-opportunities` | GET | Lieux favoris délaissés |
| `/api/doomscroll` | GET | Journées de doomscrolling sur les N derniers jours |
| `/api/procrastination/cost` | GET | Coût des tâches procrastinées |
| `/api/jarvis-journal` | GET | Entrées du journal de JARVIS |
| `/api/jarvis-journal/generate` | POST | Force la génération de l'entrée du jour |
| `/api/day-scores` | GET | Top jours par `exceptional_score` ou `luck_score` |
| `/api/day-scores/{date}` | GET | Score détaillé d'une date |
| `/api/commitments/consistency` | GET | Score de cohérence promesses/actions |
| `/api/relationship-graph` | GET | Graphe vivant des relations |
| `/api/life-context` | GET/POST, `/{id}/close` POST | Périodes de vie détectées (déménagement, rupture...) |
| `/api/time-machine/{date}` | GET | Reconstruction chronologique d'une journée |

### Scheduler (jobs additionnels)

- `jarvis_journal` (23:50, `JARVIS_JOURNAL_ENABLED`) : génère l'entrée du jour.
- `doomscroll_check` (22:00) : notifie une fois par jour si seuil dépassé.
- `missed_opportunities` (dim 19:00) : notifie une fois par semaine ISO les lieux délaissés.
- Le score du jour (`day_scoring.score_day`) est calculé automatiquement à chaque debrief du soir (21:45), en plus du score de productivité existant.

### Hors scope (nécessitent des sources de données que JARVIS ne collecte pas)

Photos, appels téléphoniques, historique musical, navigation web — explicitement
exclus de `time_machine.py`. Les 16 autres features du lot « Production Ready »
(alter ego, détecteur de mensonge, HUD Iron Man, simulateur de scénarios,
avocat du diable, jumeau numérique, etc.) restent à spécifier avant implémentation.

## Sécurité — verrouillage app, sessions, jetons, CSP (mai 2026)

Avant ce lot, **aucune authentification n'existait** : n'importe qui
atteignant le port du serveur (tout le Tailnet) pouvait lire/écrire toutes
les données (mails, journal, localisation, conversations). Correctif :
verrouillage complet **fail-closed** — tant qu'aucun secret n'est configuré,
tous les endpoints `/api/*` (hors `/api/auth/*`) répondent `428`.

### Modules

| Fichier | Rôle |
|---|---|
| `auth.py` | PIN/passphrase (hash `scrypt`, jamais en clair), sessions DB-backed (jeton opaque, seul le hash SHA-256 est stocké), anti-brute-force (verrou global après `AUTH_LOCKOUT_MAX_ATTEMPTS` échecs) |
| `main.py` (`security_middleware`) | En-têtes de sécurité (CSP, X-Frame-Options, Referrer-Policy, Permissions-Policy, HSTS si HTTPS) sur toute réponse ; verrou de session sur `/api/*` (allowlist : `/api/auth/*`, ingestion device/localisation qui s'authentifient autrement) ; vérification Origin/Referer sur les requêtes qui modifient l'état (défense en profondeur — SameSite=Strict protège déjà l'essentiel) |
| `web/src/app/components/auth/LockGate.tsx` | Écran de configuration/déverrouillage + verrouillage automatique client après `AUTO_LOCK_MINUTES` d'inactivité |
| `scripts/db_maintenance.py` | Chiffrement optionnel des sauvegardes (Fernet/AES, clé dérivée de `BACKUP_ENCRYPTION_PASSPHRASE`) + `restore_backup()` (déchiffre si besoin, snapshot de sécurité de la base courante avant d'écraser) |

### Failles corrigées

1. **Aucune authentification** → verrou PIN/passphrase + sessions, fail-closed tant que non configuré.
2. **Jetons device jamais vérifiés** — `register_device` générait un `auth_token` mais `heartbeat`/`screen` ne le vérifiaient jamais (n'importe qui pouvait usurper un device_id). Corrigé : `X-Device-Token` obligatoire, comparaison `hmac.compare_digest`.
3. **`/api/location*` (Shortcuts iOS) et lecture de l'historique GPS** ouverts sans contrôle — jeton partagé optionnel (`LOCATION_API_TOKEN`) pour l'ingestion ; les endpoints de lecture/écriture consultés depuis le navigateur passent désormais par le verrou de session standard.
4. **Aucun en-tête de sécurité** sur les réponses FastAPI (CSP, X-Frame-Options absents) — ajoutés globalement.
5. **Aucune restauration de sauvegarde possible** — `restore_backup()` + `POST /api/backups/{name}/restore` ajoutés (protection contre le path traversal, snapshot de sécurité automatique).
6. **Sauvegardes en clair sur disque** — chiffrement Fernet optionnel (`BACKUP_ENCRYPTION_ENABLED`).

### Endpoints

| Route | Méthode | Description |
|---|---|---|
| `/api/auth/status` | GET | `{configured, authenticated, locked_out, lockout_seconds, auto_lock_minutes}` |
| `/api/auth/setup` | POST | `{secret}` — une seule fois, ouvre une session |
| `/api/auth/unlock` | POST | `{secret}` — ouvre une session (soumis au verrou anti-brute-force) |
| `/api/auth/verify` | POST | `{secret}` — ré-authentification écran verrouillé (ne touche pas à la session) |
| `/api/auth/logout` | POST | Révoque la session courante |
| `/api/auth/change-secret` | POST | `{current, new}` — révoque toutes les autres sessions |
| `/api/auth/sessions` | GET | Sessions actives (device, IP, dernière activité) |
| `/api/auth/sessions/{id}/revoke` | POST | Révoque une session précise (ex. téléphone perdu) |
| `/api/backups/{name}/restore` | POST | Restaure une sauvegarde (déchiffre si `.enc`, snapshot de sécurité avant) |

### Config

```bash
SESSION_COOKIE_NAME=jarvis_session
SESSION_MAX_AGE_DAYS=30
SESSION_INACTIVITY_DAYS=14
AUTH_LOCKOUT_MAX_ATTEMPTS=5
AUTH_LOCKOUT_MINUTES=15
AUTO_LOCK_MINUTES=5
WEB_HTTPS=false                  # true → cookie Secure + HSTS
LOCATION_API_TOKEN=              # vide = /api/location reste ouvert (Shortcuts)
BACKUP_ENCRYPTION_ENABLED=false
BACKUP_ENCRYPTION_PASSPHRASE=
```

### Limites assumées (documentées, pas corrigées dans ce lot)

- `POST /api/devices/register` reste sans authentification (un nouveau
  device s'auto-enregistre) — protégé uniquement par le périmètre réseau
  privé (Tailscale/LAN), comme `/api/location` sans jeton configuré.
- Le verrou anti-brute-force est **global** (mono-utilisateur), pas par IP —
  cohérent avec un usage personnel, mais un attaquant sur le réseau peut
  bloquer temporairement l'accès légitime en multipliant les échecs.
- Pas d'audit externe (pentest réel) — l'audit est un examen de code, pas
  une certification.
- Écran de verrouillage implémenté côté `web/` (SPA principale) ; `pwa/`
  (Next.js) n'a pas encore son propre LockGate — à faire dans un lot dédié
  PWA offline-first.

## PWA offline-first — Service Worker, file d'écriture, push (mai 2026)

`web/` (SPA principale, Vite + React) devient une vraie PWA installable et
utilisable hors ligne — c'est l'interface qui porte déjà le LockGate, donc
celle visée par « le téléphone devient l'interface principale ».

### Service Worker (Workbox, mode injectManifest)

- Source : `web/src/sw.ts` — précache l'app shell (JS/CSS/HTML) uniquement,
  **jamais** les réponses `/api/*` (données personnelles) dans le cache HTTP
  du navigateur. La lecture hors-ligne de données passe par IndexedDB côté
  application (`src/lib/offline/readCache.ts`), pas par ce cache HTTP.
- `NavigationRoute` avec `denylist: [/^\/api\//, /^\/ws/]` : toute navigation
  de page sert l'app shell depuis le précache si hors ligne, mais les appels
  API ne sont jamais servis depuis un cache obsolète.
- Gère aussi les évènements `push` / `notificationclick` et un message
  `jarvis:flush-offline-queue` envoyé aux clients ouverts sur un event
  `sync` (Background Sync, best-effort — indisponible sur Safari/iOS).
- `main.py` sert explicitement `sw.js`, `manifest.webmanifest`,
  `registerSW.js` et `icons/` à la racine (le Service Worker DOIT être
  servi à la racine `/sw.js` pour contrôler toute l'app — il ne peut pas
  vivre sous `/assets`).

### File d'écriture hors ligne (`web/src/lib/offline/`)

- `db.ts` : base IndexedDB (`idb`) avec deux stores — `writeQueue` (clé
  auto-incrémentée pour un ordre chronologique garanti même si plusieurs
  écritures arrivent dans la même milliseconde) et `readCache`.
- `queue.ts` : `enqueueWrite()`, `flushQueue()` (rejoue dans l'ordre,
  s'arrête au premier échec réseau pour ne pas marteler), `initOfflineSync()`
  (retente au retour réseau, au message Background Sync, et par un filet de
  sécurité périodique de 30s pour Safari/iOS).
- `readCache.ts` : cache de lecture avec TTL pour affichage "dernières
  données connues" (pas branché sur toutes les vues — voir limites).
- Politique de conflit **volontairement simple** : dernière écriture
  gagne, aucune fusion. Une vraie résolution de conflits multi-device
  demanderait un versioning par entité — hors scope de ce lot.
- Intégré concrètement sur la création de tâche (`TasksView.tsx`) comme
  point de référence : hors ligne, la tâche apparaît immédiatement avec un
  badge « en attente », puis se synchronise et l'écran se recharge au
  retour réseau (`jarvis:offline-sync-done`). Vérifié en conditions réelles
  (Playwright, réseau coupé/rétabli).
- Purge automatique du cache/file IndexedDB à la déconnexion
  (`clearOfflineDB()` dans `LockGate`) — hygiène de confidentialité.

### Notifications Push (Web Push, RFC 8291/8292)

- `push.py` — implémentation **maison** (VAPID + chiffrement aes128gcm),
  volontairement sans dépendre de `pywebpush` (sa dépendance `http_ece` ne
  compile plus avec les outils actuels). Utilise uniquement `cryptography`
  (déjà une dépendance directe). Testé par un round-trip de chiffrement
  complet avec un déchiffreur indépendant dans les tests.
- Clé VAPID générée une fois, persistée dans `app_settings` (jamais exposée
  côté client — seule la clé publique sert de `applicationServerKey`).
- `create_notification()` déclenche un envoi push (thread daemon,
  best-effort, jamais bloquant) pour les priorités `urgent`/`high` ;
  supprime l'abonnement si le push service répond 404/410 (expiré).
- Frontend : `web/src/lib/push.ts` (`subscribeToPush`), bannière
  `NotificationsPrompt.tsx` (une fois, si permission `default`).

### Installation (Android/iPhone)

- `manifest.webmanifest` généré par `vite-plugin-pwa` (icônes 192/512 +
  512 maskable, `display: standalone`, `start_url: /chat`).
- Métadonnées iOS ajoutées à la main dans `index.html` (Safari ignore
  certains champs du manifest pour le mode standalone) :
  `apple-mobile-web-app-capable`, `apple-touch-icon`, etc.
- `InstallPrompt.tsx` : bouton natif via `beforeinstallprompt` sur
  Android/Chrome ; instructions manuelles (Partager → Sur l'écran
  d'accueil) sur iOS où cet évènement n'existe pas.

### Endpoints

| Route | Méthode | Description |
|---|---|---|
| `/api/push/vapid-public-key` | GET | Clé publique pour `PushManager.subscribe` |
| `/api/push/subscribe` | POST | Enregistre un abonnement (`endpoint`, `keys.{p256dh,auth}`) |
| `/api/push/unsubscribe` | POST | Supprime un abonnement (`endpoint`) |

### Tests

- Backend (pytest) : `tests/test_push.py` (chiffrement aes128gcm — round-trip
  avec un déchiffreur indépendant, structure du JWT VAPID, signature
  vérifiée cryptographiquement), `tests/test_push_subscriptions.py`,
  `tests/test_push_endpoints.py`.
- Frontend (vitest + fake-indexeddb) : `web/src/lib/offline/queue.test.ts`,
  `readCache.test.ts` — `pnpm test` dans `web/`.
- Vérification réelle (Playwright, réseau coupé via `context.set_offline`) :
  création de tâche hors ligne → badge "en attente" → IndexedDB contient
  l'écriture → réseau rétabli → synchronisation automatique en ~3s →
  IndexedDB vidée → UI rechargée.

### Limites assumées (documentées, pas corrigées dans ce lot)

- Résolution de conflits multi-device volontairement absente (dernière
  écriture gagne) — pas de versioning par entité.
- Seule la création de tâche est branchée sur la file hors ligne pour
  l'instant (point de référence) ; les autres écritures (journal, contacts,
  etc.) suivraient le même patron si besoin.
- `readCache.ts` existe mais n'est pas encore branché sur les vues (lecture
  hors ligne des listes tâches/notifications) — l'app shell fonctionne hors
  ligne, mais les données déjà chargées ne persistent pas automatiquement
  entre sessions sans réseau pour l'instant.
- `pwa/` (l'app Next.js séparée) n'a reçu ni le Service Worker ni le
  LockGate de ce lot — tout le travail a porté sur `web/`, l'interface
  principale.
- Vérification faite dans un navigateur headless en sandbox (pas de vrai
  téléphone) — conso batterie/CPU réelle et comportement d'installation
  natif à valider sur device physique.
