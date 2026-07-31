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
- **Santé et activité** : entraînements, repas, hydratation, bien-être et résumé quotidien.
- **Développement** : routage des demandes techniques, plans d'exécution confirmés, travaux isolés et DevAgent pour les tâches multi-étapes.
- **Multi-appareils** : interface Web responsive, interface mobile légère, application Android native, agent Mac distant et tableau de bord TV.
- **Sécurité et fiabilité** : écoute réseau locale par défaut, sessions protégées, contrôle CSRF, permissions de fichiers strictes, sauvegardes chiffrées et rotation automatique.

## Exemples

JARVIS accepte des demandes naturelles, sans syntaxe particulière :

```text
« Fais-moi un briefing de ma journée. »
« Quels mails importants ai-je reçus depuis hier ? »
« Ajoute “envoyer le dossier” à mes tâches pour demain matin. »
« Résume ce document et transforme-le en fiche de révision. »
« Depuis combien de temps n'ai-je pas parlé à Paul ? »
« Note dans mon journal que la réunion s'est bien passée. »
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
python -m pip install -r requirements.txt
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

Pour un fonctionnement permanent avec redémarrage automatique du backend :

```bash
./scripts/launch_supervisor.sh
```

Le superviseur est alors accessible par défaut sur [http://127.0.0.1:9000](http://127.0.0.1:9000).

## Configuration utile

| Variable | Rôle |
|---|---|
| `DEEPSEEK_API_KEY` | Clé du moteur de raisonnement principal. |
| `DEEPSEEK_FAST_MODEL` / `DEEPSEEK_MAIN_MODEL` | Modèles utilisés pour les réponses rapides et les tâches complexes. |
| `DB_PATH` | Emplacement de la base SQLite. |
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
# Backend
python -m pytest tests/ jarvis/tests agents/devagent -q

# Frontend principal
cd frontend
pnpm test
pnpm typecheck
pnpm build
pnpm test:e2e
```

La CI vérifie également l'installation de production, les intégrations macOS simulées et le frontend historique de repli.

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
├── web/                # composants React partagés et frontend de repli Vite
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
