# JARVIS

JARVIS est un assistant personnel pour macOS, pensé pour centraliser les conversations, la mémoire, les tâches et les automatisations du quotidien dans une seule interface.

Il peut être utilisé depuis le Web, à la voix, via iMessage ou avec l'application Android. Les données persistantes restent sur le Mac dans SQLite. Le raisonnement s'appuie sur DeepSeek, tandis que la transcription, la synthèse vocale et l'analyse d'écran peuvent fonctionner localement.

> Une seule personnalité côté utilisateur, plusieurs agents spécialisés en interne.

## Objectif

Le projet cherche à créer un assistant réellement utile au quotidien, capable de :

- comprendre une demande en langage naturel ;
- retrouver le bon contexte dans les conversations, les tâches et les données personnelles ;
- choisir automatiquement l'agent ou l'outil adapté ;
- agir sur les applications et services du Mac après les contrôles nécessaires ;
- apprendre des habitudes sans disperser les données dans plusieurs services.

JARVIS n'est donc pas seulement un chatbot : il relie une interface de conversation, une mémoire persistante, des automatisations et l'écosystème Apple.

## Fonctionnement

Une demande suit le même pipeline, quelle que soit son origine :

```text
Web · Voix · Android · iMessage
              │
              ▼
      API FastAPI + WebSocket
              │
              ▼
   Authentification et ajout du contexte
              │
              ▼
      Routeur cognitif / orchestrateur
        ┌─────┼───────────┐
        ▼     ▼           ▼
      Agent  Outil     Modèle adapté
        └─────┼───────────┘
              ▼
 AppleScript · API · SQLite · réponse en streaming
```

1. L'utilisateur écrit ou parle à JARVIS.
2. Le backend authentifie la session et récupère uniquement le contexte utile.
3. Le routeur classe la demande : information, productivité, école, coaching, journal, développement ou action directe.
4. L'agent choisi consulte la mémoire, appelle un modèle ou utilise une intégration.
5. La réponse est diffusée à l'interface et les informations utiles sont enregistrées dans SQLite.

## Fonctionnalités

- **Chat persistant** : réponses en streaming, historique, recherche, conversations épinglées ou archivées et pièces jointes.
- **Assistant vocal** : transcription locale avec faster-whisper, synthèse vocale locale, mode mains libres, mot d'activation optionnel et enregistrements longs.
- **Mémoire personnelle** : faits, personnes, événements, journal, habitudes, engagements et recherche plein texte ou sémantique.
- **Productivité** : tâches, agenda, briefings, rappels, suivi des engagements et bilan de la journée.
- **Écosystème Apple** : lecture et envoi via Mail, Calendar, Messages et Contacts grâce à AppleScript, sans OAuth supplémentaire.
- **Suivi proactif** : tri des emails, notifications, rituels planifiés, mode Ne pas déranger, présence et analyse locale de l'écran.
- **Localisation** : points GPS, lieux favoris, visites, trajets et habitudes géographiques.
- **Relations** : historique iMessage, fiches contacts, chronologies, rappels et suggestions de messages.
- **Fitness et nutrition** : programme poids du corps modifiable en base, séances/exercices fait ou non fait, échauffements et étirements, repas/calories/protéines, eau, pesée, conseils IA et relances vocales jusqu'à validation.
- **Développement** : routage des demandes techniques, plans d'exécution confirmés, travaux isolés et DevAgent pour les tâches multi-étapes.
- **Multi-appareils** : interface Web responsive, interface mobile légère, application Android native, agent Mac distant et tableau de bord TV.
- **Sécurité et fiabilité** : écoute réseau locale par défaut, sessions protégées, contrôle CSRF, permissions strictes, sauvegardes chiffrées, rotation automatique et chiffrement SQLCipher optionnel de chaque base utilisateur.

## Exemples

JARVIS accepte des demandes naturelles, sans syntaxe particulière :

```text
« Fais-moi un briefing de ma journée. »
« Quels mails importants ai-je reçus depuis hier ? »
« Ajoute “envoyer le dossier” à mes tâches pour demain matin. »
« Résume ce document et transforme-le en fiche de révision. »
« Depuis combien de temps n'ai-je pas parlé à Paul ? »
« Note dans mon journal que la réunion s'est bien passée. »
« J'ai fait mon sport. »
« J'ai mangé du poulet et du riz, environ 700 calories. »
« Explique cette erreur puis prépare un correctif dans un projet isolé. »
```

Les actions sensibles, notamment les plans de commandes terminal, demandent une confirmation explicite avant leur exécution.

## Cas d'utilisation

| Besoin | Ce que fait JARVIS |
|---|---|
| Organiser sa journée | Regroupe agenda, tâches, météo, mails importants et rappels dans un briefing. |
| Vider sa boîte de réception | Analyse les messages non lus, fait ressortir l'urgent et prépare une réponse. |
| Travailler à la voix | Écoute une demande, la transcrit localement, répond et lit la réponse à haute voix. |
| Réviser un cours | Produit un résumé, une fiche, des exercices ou des flashcards à partir d'une demande ou d'un document. |
| Entretenir ses relations | Retrouve les derniers échanges, détecte les longs silences et suggère un message adapté. |
| Garder une mémoire personnelle | Relie journal, conversations, lieux et événements pour restituer le contexte plus tard. |
| Suivre ses habitudes | Observe les routines choisies, le temps d'écran, les déplacements ou la régularité des tâches. |
| Développer un projet | Classe la demande, prépare un plan, travaille dans un espace isolé puis expose les résultats et les tests. |

## Une journée type

Voici le rythme par défaut d'une journée avec le scheduler actif. Les heures suivent l'heure locale du Mac et les rituels peuvent être déplacés dans `.env` ou `.env.config`.

| Heure | Automatisation | Ce que cela apporte |
|---|---|---|
| 03:00 | Analyse relationnelle iMessage | Met à jour les tendances et les échanges récents. |
| 04:15 | Sauvegarde SQLite | Crée une sauvegarde chiffrée et applique la rotation configurée. |
| 07:00 | Citation du jour | Prépare la citation ironique affichée sur le tableau de bord TV. |
| 07:30 | Briefing du matin | Rassemble agenda, tâches, météo et informations importantes, puis envoie une notification. |
| 08:00 | Anniversaires | Vérifie les anniversaires présents dans les fiches contacts. |
| 10:00 | Engagements en attente | Rappelle les promesses ouvertes depuis plus de trois jours. |
| 18:30 | Revue des tâches | Signale, sur le ton de JARVIS, les tâches prévues mais non terminées. |
| 21:30 | Budget LLM | Contrôle la consommation mensuelle et les seuils d'alerte. |
| 21:45 | Débrief du soir | Résume la journée, les réussites et les éléments manqués, puis fige le score de productivité. |
| 22:00 | Résumé et temps d'écran | Génère le résumé du soir et vérifie un éventuel doomscrolling. |
| 22:40 | Extraction des engagements | Repère les promesses prises dans les messages de la journée. |
| 23:00 | Analyse des déplacements | Met à jour lieux, visites, trajets et habitudes géographiques. |
| 23:15 | Signal d'humeur | Calcule un signal comportemental discret, sans diagnostic médical. |
| 23:50 | Journal de JARVIS | Produit, si la fonction est activée, une courte entrée récapitulative. |

Des contrôles plus légers tournent aussi pendant la journée. Ils restent silencieux tant qu'aucune condition ne nécessite d'action.

| Fréquence | Contrôle |
|---|---|
| Toutes les 5 minutes | Détecte la fin d'une réunion captée et lance son résumé si cette fonction est activée. |
| Toutes les 10 minutes | Vérifie la présence et clôt une session après une longue période de silence. |
| Toutes les 20 minutes, de 09:00 à 22:40 | Propose une pause après une activité écran continue trop longue. |
| Toutes les 30 minutes | Vérifie les longues sessions de streaming. |
| Toutes les 30 minutes, de 22:00 à 03:30 | Peut signaler un retour tardif selon la localisation. |
| Toutes les heures | Recherche les tâches dont l'échéance est dépassée. |
| Toutes les 6 heures | Recherche les alertes relationnelles utiles. |

Enfin, quelques tâches de fond suivent un rythme hebdomadaire :

| Jour et heure | Automatisation |
|---|---|
| Mercredi 05:00 | Rapport sur le code dupliqué, sans réécriture automatique. |
| Mercredi 05:15 | Audit de sécurité du dépôt. |
| Samedi 05:30 | Recherche et génération optionnelle de tests manquants. |
| Dimanche 04:45 | Purge de rétention et optimisation de SQLite. |
| Dimanche 06:00 | Recherche d'améliorations et proposition de PR si l'auto-amélioration est activée. |
| Dimanche 19:00 | Recherche de lieux favoris délaissés. |
| Dimanche 20:00 | Résumé mémoire de la semaine. |
| Dimanche 21:00 | Débrief hebdomadaire vocal. |

Chaque automatisation respecte son option d'activation et ses seuils. Un passage du cron ne produit donc pas forcément une notification ou un appel à un modèle.

Le suivi live est disponible dans l'UI bureau sur **`/scheduler`** : statut du jour (fait / en attente / manqué / échec / silencieux), agrégats 7 jours pour les ticks fréquents, sortie des sorties au clic, et relance manuelle pour les jobs quotidiens ou hebdomadaires. API : `GET /api/scheduler/jobs`, `GET /api/scheduler/jobs/{id}/runs`, `POST /api/scheduler/jobs/{id}/run`.

## Stack technique

| Couche | Technologies |
|---|---|
| Backend | Python 3.12, FastAPI, Uvicorn, WebSocket, APScheduler |
| Frontend principal | Next.js 15, React 19, TypeScript, Tailwind CSS 4, TanStack Query |
| Frontend mobile léger | HTML, CSS et JavaScript sans étape de build |
| Application Android | Kotlin, Jetpack Compose, Room, WorkManager, Retrofit, FCM optionnel |
| Données | SQLite, WAL, FTS5, embeddings locaux avec sentence-transformers |
| Raisonnement | DeepSeek Flash et Main, API compatible OpenAI |
| IA locale | Ollama pour la surveillance d'écran et le tri local des notifications |
| Audio | faster-whisper, Silero VAD, Kokoro, TTSKit et synthèse vocale macOS |
| Intégrations | AppleScript, Tailscale, Google Cast, API météo |
| Tests | pytest, Vitest, Testing Library et Playwright |

## Installation rapide

### Prérequis

- macOS ;
- Python 3.12 ;
- Node.js 22 et pnpm 11.11.0 ;
- une clé API DeepSeek ;
- les autorisations macOS correspondant aux intégrations activées.

### 1. Installer le backend

```bash
git clone https://github.com/AVTAVANTTOUT2/JarvisAPI.git
cd JarvisAPI

python3.12 -m venv venv
source venv/bin/activate
python -m pip install --require-hashes \
  -r requirements/locks/production-macos-arm64-py312.txt
```

### 2. Configurer JARVIS

```bash
cp .env.example .env
```

Renseigner au minimum la clé DeepSeek dans `.env` :

```dotenv
DEEPSEEK_API_KEY=sk-...
WEB_HOST=127.0.0.1
WEB_PORT=8080
```

Le backend est fail-closed : une clé absente ou laissée à `sk-...` arrête le
démarrage avant l'initialisation de la base et des workers.

Les réglages applicatifs peuvent être séparés des secrets avec `.env.config.example` :

```bash
cp .env.config.example .env.config
```

### 3. Construire l'interface

```bash
cd frontend
pnpm install --frozen-lockfile
pnpm build
cd ..
```

### 4. Démarrer

```bash
source venv/bin/activate
python main.py
```

Ouvrir ensuite [http://127.0.0.1:8080](http://127.0.0.1:8080). Le port peut être changé avec `WEB_PORT`.

Pour un lancement manuel du superviseur :

```bash
./scripts/launch_supervisor.sh
```

Le superviseur est alors accessible par défaut sur [http://127.0.0.1:9000](http://127.0.0.1:9000).

Pour l'installer comme LaunchAgent macOS, depuis le checkout et son venv réels :

```bash
python scripts/jarvis_launchd.py install
```

L'installateur génère `~/Library/LaunchAgents/com.jarvis.supervisor.plist`,
vérifie `ProgramArguments`, `WorkingDirectory` et les logs, puis exécute
`plutil -lint` avant de charger le service. Aucun chemin utilisateur n'est
stocké dans le dépôt.

## Configuration utile

| Variable | Rôle |
|---|---|
| `DEEPSEEK_API_KEY` | Clé du moteur de raisonnement principal. |
| `DEEPSEEK_FAST_MODEL` / `DEEPSEEK_MAIN_MODEL` | Modèles utilisés pour les réponses rapides et les tâches complexes. |
| `DB_PATH` | Emplacement de la base SQLite. |
| `DATABASE_ENCRYPTION_ENABLED` | Ouvre les bases avec SQLCipher après migration via `tools/database_encryption.py`. |
| `STT_ENGINE` / `TTS_ENGINE` | Moteurs de transcription et de synthèse vocale. |
| `IMESSAGE_TARGET` | Active le bridge iMessage pour le numéro ou l'adresse indiquée. |
| `IMESSAGE_SEND_ENABLED` | Autorise explicitement l'envoi d'iMessages. Désactivé par défaut. |
| `DAEMON_ENABLED` | Active les fonctions proactives en arrière-plan. |
| `SCREEN_WATCHER_ENABLED` | Active l'analyse locale de l'écran. |
| `WEB_HOST` / `WEB_PORT` | Adresse et port d'écoute du backend. |
| `LOCATION_API_TOKEN` | Protège l'envoi de positions par un raccourci ou un client externe. |

Toutes les options sont commentées dans [`.env.example`](./.env.example) et [`.env.config.example`](./.env.config.example).

## Autorisations macOS

Selon les fonctions utilisées, macOS peut demander :

- **Accès complet au disque** pour lire la base iMessage ;
- **Automation** pour Messages, Mail, Calendar, Contacts et System Events ;
- **Microphone** pour la voix et le mot d'activation ;
- **Enregistrement de l'écran** pour le Screen Watcher ;
- **Notifications** pour les alertes système.

Le détail et les procédures de reprise sont dans [STARTUP_PROTOCOL.md](./STARTUP_PROTOCOL.md).

## Accès distant

Le backend écoute uniquement sur la boucle locale par défaut. Pour un accès depuis un téléphone, conserver ce bind local et placer un proxy HTTPS devant JARVIS, par exemple Tailscale Serve :

```dotenv
WEB_HOST=127.0.0.1
WEB_ALLOW_NETWORK_BIND=false
WEB_HTTPS=false
WEB_HTTPS_BEHIND_PROXY=true
```

```bash
tailscale serve --bg http://127.0.0.1:8080
tailscale serve status
```

Le microphone d'un navigateur distant nécessite HTTPS. Ne pas exposer directement le port HTTP de JARVIS sur le réseau.

## Tests

```bash
# Suite standard — hors ligne et déterministe (aucune connexion sortante)
python -m pytest tests/ jarvis/tests agents/devagent -q

# Lint Python (règles pyflakes F — configuration dans .ruff.toml)
python -m ruff check .

# Frontend principal
cd frontend
pnpm test
pnpm typecheck
pnpm build
pnpm test:e2e
```

### Suite standard, intégrations locales, réseau externe

La suite standard n'ouvre aucune connexion sortante. Un garde-fou global
(`conftest.py`) refuse toute connexion hors boucle locale, exactement comme le
ferait une machine sans réseau (`ConnectionRefusedError`), et liste les
tentatives refusées en fin de session avec le nom du test. Les scénarios qui
exigent vraiment Internet portent le marqueur `external_network` et sont
désélectionnés par défaut (`pytest.ini`).

| Objectif | Commande |
|---|---|
| Suite standard (hors ligne, sans réservation Metal, défaut) | `python -m pytest tests/ jarvis/tests agents/devagent -q` |
| Intégrations locales réelles (synthèse vocale réelle) | arrêter le daemon JARVIS, puis `python -m pytest -m integration_tts -v` |

Marqueurs déclarés :

- `external_network` — sort réellement sur Internet. **Plus aucun test ne le
  porte** depuis que la synthèse vocale est locale : il reste déclaré pour que
  toute future dépendance réseau d'un test soit un choix visible, jamais un
  effet de bord. Un `-m` passé en ligne de commande remplace `addopts`.
- `integration_tts` — fait réellement produire de l'audio par le moteur local.
  Cette porte matérielle est exclue de la suite standard : sur Apple Silicon,
  lancer un second Qwen3 pendant que le daemon JARVIS détient déjà le modèle
  peut saturer Metal. Elle se saute proprement si les poids sont absents et se
  lance explicitement, daemon arrêté, avec `pytest -m integration_tts`.

La CI vérifie également l'installation de production, les intégrations macOS
simulées, le build Release de l'app SwiftUI et de son widget, la release Android
minifiée par R8, ainsi que le frontend historique de repli.

## Structure du projet

```text
JarvisAPI/
├── main.py             # application FastAPI et assemblage du pipeline
├── api/                # routes REST, WebSocket, auth et services
├── agents/             # orchestrateur et agents spécialisés
├── jarvis/             # routage, confidentialité et services internes
├── database/           # schéma SQLite, migrations et accès aux données
├── integrations/       # AppleScript, DeepSeek, Ollama, météo et appareils
├── audio/              # transcription, VAD et synthèse vocale
├── scripts/            # daemons, scheduler, maintenance et installation
├── frontend/           # application Next.js canonique
├── web/                # bibliothèque de vues React partagée (non exécutable)
├── web_mobile/         # interface mobile autonome
├── android/            # application Android native
├── tv/                 # tableau de bord grand écran
├── tests/              # tests backend et contrats d'intégration
└── Architecture/       # documentation technique et décisions d'architecture
```

## Vie privée et sécurité

- Les conversations, tâches, contacts enrichis et souvenirs sont stockés localement dans SQLite.
- Les données personnelles envoyées au modèle distant passent par une couche d'anonymisation lorsque le pipeline le prévoit.
- Les captures d'écran sont analysées localement avec Ollama et ne sont pas envoyées à DeepSeek.
- La transcription et la synthèse vocale disposent de moteurs locaux.
- Les documents restent locaux par défaut avec `DOCUMENT_STRICT_LOCAL=true`.
- Les sauvegardes SQLite peuvent être chiffrées et les fichiers sensibles utilisent des permissions restrictives.
- L'écoute réseau est locale par défaut ; tout accès distant doit être protégé par HTTPS.

## Documentation

- [Architecture/INDEX.md](./Architecture/INDEX.md) : architecture, audits et décisions techniques.
- [CLAUDE.md](./CLAUDE.md) : référence détaillée du code, des routes et des conventions.
- [STARTUP_PROTOCOL.md](./STARTUP_PROTOCOL.md) : installation macOS, permissions et reprise après incident.
- [android/README.md](./android/README.md) : configuration et build de l'application Android.
- [tv/README.md](./tv/README.md) : tableau de bord TV.
- [CHANGELOG_HISTORIQUE.md](./CHANGELOG_HISTORIQUE.md) : historique détaillé du projet.
