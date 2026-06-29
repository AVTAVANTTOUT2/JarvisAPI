# JARVIS TV — War Room Dashboard

Ecran de monitoring type "centre de commandement militaire" pour TV Philips 55" OLED (Google TV).
Affichage plein ecran 24/7 en mode kiosk.

## Demarrage rapide

```bash
# Installer les dependances
pip install fastapi uvicorn jinja2 httpx psutil aiofiles --break-system-packages

# Lancer le serveur
cd ~/JarvisAPI/tv && python3 server.py

# Ouvrir http://localhost:5174
```

## Service macOS (launchd)

```bash
cp ~/JarvisAPI/tv/com.jarvis.tv.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.jarvis.tv.plist
launchctl list | grep jarvis.tv
```

## Lancer sur TV Philips via ADB

```bash
adb connect 192.168.1.XXX
adb shell am start -a android.intent.action.VIEW \
    -d "http://192.168.1.YYY:5174" \
    -n com.android.chrome/com.google.android.apps.chrome.Main \
    --ez "create_new_tab" true
```

Remplacer XXX = IP TV, YYY = IP Mac Mini.

## Securite

- IP Whitelist: 192.168.1.0/24, 100.64.0.0/10, 127.0.0.1
- Toute IP non autorisee recois HTTP 403
- Pas d'authentification (IP suffit)
- Headers: X-Content-Type-Options: nosniff, X-Frame-Options: DENY

## Endpoints API

| Endpoint | Source |
|----------|--------|
| GET / | Dashboard HTML |
| GET /api/health | Healthcheck |
| GET /api/events | **SSE — evenements daemon audio temps reel** |
| GET /api/weather | Open-Meteo (gratuit) |
| GET /api/stats | CPU/RAM/Disk + Services + Ollama |
| GET /api/automations | SQLite llm_action_logs |
| GET /api/calendar | Backend :8081 |
| GET /api/tasks | SQLite tasks |
| GET /api/messages | iMessage + Chat SQLite |
| GET /api/emails | SQLite email_summaries |
| GET /api/notifications | SQLite notifications |
| GET /api/devices | SQLite devices |
| GET /api/mood | SQLite mood_log |
| GET /api/status | Proxy backend |

## Widgets

| Widget | Description |
|--------|-------------|
| Horloge | HH:MM:SS + date, 1s refresh |
| Meteo | Open-Meteo Lille, previsions 3 jours |
| Humeur | Dernier mood + energie |
| Serveur | CPU/RAM/Disque + services + Ollama + backend status |
| Actions IA | 24h d'actions LLM, couleur par agent |
| Calendrier | Evenements Apple Calendar du jour |
| Taches | Taches actives, priorite par couleur |
| Messages | iMessage + chat JARVIS |
| Emails | Resumes emails watcher |
| Notifications | Alertes non lues |
| Globe 3D | Three.js wireframe + arcs + particules |
| Footer | Devices connectes + cout API jour |
| **Overlay vocal** | **Transcription + reponse temps reel du daemon audio** |

## Overlay vocal

Le dashboard affiche en temps reel les interactions vocales avec JARVIS :

- Ecoute le WebSocket du backend principal (port 8081)
- Relaye via SSE (`/api/events`) vers le navigateur TV
- Overlay avec orbe anime + transcription de l'utilisateur + reponse de JARVIS
- Disparait automatiquement 3 secondes apres le retour en veille
- Latence < 200ms sur reseau local

**Necessite** : daemon audio actif (`AUDIO_DAEMON_ENABLED=true` dans `.env`)

### Etats visuels

| Etat | Couleur orbe | Label |
|------|-------------|-------|
| `idle` | Gris (#52525b) | VEILLE — overlay masque |
| `wake_listening` | Cyan (#00d4ff) | ECOUTE |
| `listening` | Cyan (#00d4ff) | ECOUTE |
| `processing` | Violet (#a855f7) | TRAITEMENT |
| `speaking` | Orange (#f59e0b) | JARVIS PARLE |
| `error` | Rouge (#ef4444) | ERREUR |

## Architecture

```
tv/
├── server.py            FastAPI port 5174
├── config.py            Configuration
├── data_sources/        11 modules Python (SQLite + API)
├── static/css/tv.css    Style militaire dark
├── static/js/           14 modules JS vanilla
├── templates/tv.html    Template unique
├── com.jarvis.tv.plist  Service launchd
└── README.md
```

## Depannage

```bash
# Logs
tail -f ~/JarvisAPI/logs/tv.log

# Verifier port
lsof -i :5174

# Verifier backend
curl -k https://127.0.0.1:8081/api/status

# Arreter service
launchctl unload ~/Library/LaunchAgents/com.jarvis.tv.plist
```
