# Protocole de démarrage total (propre) + reprise après coupure

Ce document décrit la check-list opérationnelle pour démarrer JARVIS proprement sur Mac, vérifier que tout est sain, et récupérer rapidement après une coupure de courant.

## 1) Pré-requis (une fois)

- macOS avec Python 3.12, `venv`, `pip`, `pnpm` installés.
- Projet présent dans `/Users/zeldris/JarvisAPI`.
- Fichier `.env` prêt (au minimum `ANTHROPIC_API_KEY`, `WEB_PORT`, `IMESSAGE_TARGET`).
- Applications Apple ouvertes au moins une fois : `Messages`, `Mail`, `Calendar`, `Contacts`.
- Si daemon local activé: `Ollama` installé + modèles téléchargés (`qwen2.5-vl:7b`, `qwen2.5:7b`).

## 2) Autorisations macOS a valider

## Acces complet au disque

Dans `Reglages > Confidentialite et securite > Acces complet au disque`, autoriser:
- Terminal (ou iTerm)
- Cursor
- Python (si appele directement)

Necessaire pour lire `~/Library/Messages/chat.db` et les bases locales Apple.

## Automation (Apple Events)

Au premier usage, macOS demande les droits d'automatisation. Autoriser:
- Terminal/Cursor -> Messages.app
- Terminal/Cursor -> Mail.app
- Terminal/Cursor -> Calendar.app
- Terminal/Cursor -> Contacts.app
- Terminal/Cursor -> System Events

Necessaire pour AppleScript (iMessage, Mail, Agenda, fenetre active).

## Microphone

Autoriser Cursor/Terminal si usage vocal (`/voice`, STT).

## Enregistrement d'ecran

Necessaire pour `screen_watcher` / daemon (capture + analyse):
- Autoriser Terminal/Cursor dans `Enregistrement de l'ecran`.

## Notifications

Autoriser les notifications pour le terminal ou l'app lanceur, sinon les alertes desktop ne sortent pas.

## 2 bis) Voir toutes les invites d'autorisation a l'ecran (ordre pratique)

macOS affiche les boites **Automation** et certaines alertes sur l'**application parente** qui execute le code (Terminal, iTerm, Cursor). En mode `nohup` / `--daemon`, les invites peuvent passer moins visibles ou etre rattachees au mauvais processus.

**Pour tout declencher et tout voir en une passe (recommande une fois apres install ou gros changement de droits)** :

1. Ouvre **Terminal.app** (pas un sous-shell invisible), garde la fenetre au premier plan.
2. Arrete tout ce qui ecoute deja sur `WEB_PORT` (souvent `8081`) et sur `5173` si tu utilises Vite, pour eviter les doublons.
3. Lance **sans daemon** (premier plan) :
   ```bash
   cd /Users/zeldris/JarvisAPI
   source venv/bin/activate
   ./scripts/jarvis_full_restart.sh --dev
   ```
   Le backend reste attache a ce terminal : les prompts **Automation** (Messages, Mail, Calendar, Contacts, System Events) apparaissent ici lors des premiers appels AppleScript.
4. Ouvre une fois **Messages**, **Mail**, **Calendar**, **Contacts** (reduit la fenetre si besoin) pour reduire les erreurs `-600` au demarrage.
5. Va dans **Reglages > Confidentialite et securite** et verifie manuellement (les invites ne couvrent pas tout) :
   - **Acces complet au disque** : Terminal + Cursor (+ Python si lance directement).
   - **Enregistrement de l'ecran** : Terminal + Cursor (daemon / screen watcher).
   - **Microphone** : Terminal + Cursor (page `/voice`, STT).
   - **Notifications** : activer pour l'app qui affiche les alertes JARVIS si besoin.
6. Dans le navigateur : ouvre `https://127.0.0.1:8081` (ou `https://localhost:5173` en dev Vite) et **accepte le certificat local** une fois.

**Liste des autorisations que JARVIS peut te demander (selon ce que tu actives)** :

| Ordre typique | Permission | Declencheur concret |
|---------------|------------|---------------------|
| 1 | Acces complet au disque | Lecture `~/Library/Messages/chat.db`, caches Contacts SQLite |
| 2 | Automation -> Messages | Envoi iMessage (`osascript`), bridge |
| 3 | Automation -> Mail | Email watcher, lecture mails |
| 4 | Automation -> Calendar | Agenda, rappels |
| 5 | Automation -> Contacts | Sync noms / resolution handles |
| 6 | Automation -> System Events | App au premier plan (ecran / contexte) |
| 7 | Enregistrement de l'ecran | `screen_watcher`, captures |
| 8 | Microphone | STT WebSocket, wake word si active |
| 9 | Notifications | Centre de notifications macOS pour alertes JARVIS |

Quand tout est regle, tu peux repasser en arriere-plan : `./scripts/jarvis_full_restart.sh --daemon --dev`.

## 3) Demarrage quotidien propre (sequence recommandee)

## Etape A - Ouvrir un terminal propre

```bash
cd /Users/zeldris/JarvisAPI
source venv/bin/activate
```

## Etape B - Redemarrage propre backend (recommande)

```bash
./scripts/jarvis_full_restart.sh
```

Option dev frontend live:

```bash
./scripts/jarvis_full_restart.sh --dev
```

Option daemon (background):

```bash
./scripts/jarvis_full_restart.sh --daemon
./scripts/jarvis_full_restart.sh --daemon --dev
```

## Etape C - Verifier l'UI

- Ouvrir `https://127.0.0.1:WEB_PORT` (souvent `8081`).
- Verifier que la route ouvre l'app React (`/chat`) sans telechargement de fichier.
- Si le navigateur affiche une alerte certificat local, accepter l'exception locale.
- En mode dev Vite, utiliser `https://localhost:5173`.

## 4) Verification de sante apres demarrage

## API status

Tester:

```bash
curl -sk https://127.0.0.1:8081/api/status
```

Verifier:
- `ok` global / service up.
- integrateurs actifs attendus (`email_watcher`, `computer`, `code_executor`, daemon selon config).
- pas d'erreur critique dans les logs startup.

## Integrations attendues

```bash
curl -sk https://127.0.0.1:8081/api/integrations
```

Verifier au minimum:
- `imessage_bridge` actif si `IMESSAGE_TARGET` renseigne.
- `mail` et `calendar` disponibles.
- `code_executor` selon `CODE_EXECUTOR_ENABLED`.
- `daemon` / screen watcher selon variables.

## Frontend + WebSocket

- Envoyer un message simple dans le chat web ("ping").
- Confirmer reponse streaming et persistance conversation.

## iMessage

- Envoyer un message test depuis l'iPhone (avec prefixe si configure).
- Verifier reception + reponse sans boucle.

## Mail watcher

- Confirmer que le watcher tourne (visible via `/api/status`).
- Rattrapage manuel sans script : `curl -sk -X POST https://127.0.0.1:WEB_PORT/api/email-watcher/catchup` (remplacer `WEB_PORT`, souvent 8081). Réponse JSON : `mail_available`, `unread_fetched`, `first_cycle_to_analyze`, etc.
- Optionnel: envoyer un mail de test et verifier creation notif/tache si pertinent.

## Daemon (si active)

- Verifier qu'Ollama repond:

```bash
curl -s http://localhost:11434/api/tags
```

- Verifier activite device/API:

```bash
curl -sk https://127.0.0.1:8081/api/devices
```

## 5) Procedure rapide en cas de probleme

## Cas 1 - Port occupe / backend inaccessible

1. Lancer `./scripts/jarvis_full_restart.sh`
2. Re-tester `https://127.0.0.1:8081/api/status`

## Cas 1b - `ERR_EMPTY_RESPONSE` sur localhost

Cause classique: URL en `http` alors que le backend tourne en `https`.

- Mauvais: `http://localhost:8081`
- Correct: `https://localhost:8081` ou `https://127.0.0.1:8081`
- Front dev: `https://localhost:5173`

## Cas 2 - AppleScript bloque (Mail/Messages/Calendar)

1. Ouvrir l'app concernee manuellement une fois.
2. Revalider permissions `Automation`.
3. Relancer backend avec le script restart.

## Cas 3 - Voix KO

1. Verifier permission micro.
2. Vérifier `AUDIO_DAEMON_STT_ENGINE`, le modèle local et `TTS_ENGINE`.
3. Relancer et refaire un test vocal court.

## Cas 4 - Daemon KO

1. Verifier `DAEMON_ENABLED=true`.
2. Verifier Ollama en local (`/api/tags`).
3. Relancer backend puis verifier `/api/devices` et `/api/screen-activity/current`.

## 6) Protocole apres coupure de courant

## Objectif

Repartir proprement sans corruption DB, sans doublons de workers, et avec validation de tous les services critiques.

## Sequence de reprise

1. Redemarrer le Mac, ouvrir une session.
2. Ouvrir `Messages`, `Mail`, `Calendar`, `Contacts` une fois.
3. Ouvrir terminal:

```bash
cd /Users/zeldris/JarvisAPI
source venv/bin/activate
./scripts/jarvis_full_restart.sh --dev
```

4. Verifier API:

```bash
curl -sk https://127.0.0.1:8081/api/status
curl -sk https://127.0.0.1:8081/api/integrations
```

5. Verifier logs: aucun crash en boucle, aucun traceback recurrent.
6. Tester un message chat web + un ping iMessage.
6bis. Si le serveur etait eteint longtemps (semaines) : ouvrir **Mail.app** au premier plan, puis lancer `python scripts/catchup_after_downtime.py` (rattrapage non-lus absents de `email_summaries` + analyse iMessage incrementale + sync `force_full_mac_sync`). Si les logs indiquent un **TIMEOUT Mail** (60s), repeter apres avoir accepte un prompt **Automation** en attente ou reactive Mail.
7. Si daemon active: verifier Ollama + `/api/devices`.
8. Si tout est bon, laisser tourner.

## Verification integrite minimale DB (optionnel, recommande)

Si suspicion de corruption SQLite apres coupure:

```bash
sqlite3 /Users/zeldris/JarvisAPI/data/jarvis.db "PRAGMA quick_check;"
```

Resultat attendu: `ok`.

Pour Messages DB (lecture seule):

```bash
sqlite3 "/Users/zeldris/Library/Messages/chat.db" "PRAGMA quick_check;"
```

## 7) Checklist "pret pour la journee"

- Backend UP (`/api/status` OK).
- Frontend accessible et chat repond.
- WebSocket stable (streaming OK).
- iMessage bridge OK.
- Mail watcher OK.
- Calendar integration OK.
- Audio (micro + TTS) OK si necessaire.
- Daemon/Ollama OK si active.
- Aucune erreur critique repetee en logs.

## 8) Commandes utiles (copier-coller)

```bash
# Restart propre
cd /Users/zeldris/JarvisAPI && source venv/bin/activate && ./scripts/jarvis_full_restart.sh --dev

# Status systeme JARVIS
curl -sk https://127.0.0.1:8081/api/status
curl -sk https://127.0.0.1:8081/api/integrations

# Verification DB locale
sqlite3 /Users/zeldris/JarvisAPI/data/jarvis.db "PRAGMA quick_check;"
```
