# Canal WebSocket TV — `/ws/tv/events`

Canal temps réel dédié à l'écran mural : **authentifié**, **strictement
descendant**, **séparé du chat**. Il diffuse un sous-ensemble explicite
d'événements JARVIS et n'accepte aucune donnée entrante.

## Pourquoi un second endpoint

`/ws` transporte le chat, l'audio, les commandes et les confirmations
d'action. La TV est un appareil allumé en permanence, visible depuis le salon,
posé sur un réseau domestique et piloté par un navigateur Android que personne
ne met à jour. Lui donner accès à `/ws` reviendrait à lui accorder trois choses
dont elle n'a aucun besoin : lire des conversations, émettre des commandes, et
recevoir des transcriptions vocales.

Le canal TV inverse la posture par défaut : rien ne sort qui n'ait été
explicitement traduit, rien n'entre du tout.

Avant ce lot, le serveur TV se connectait à `ws://backend:8081/ws` **sans
aucune authentification** et filtrait côté client les messages
`audio_daemon_*`. Depuis le verrouillage d'application, ce handshake était
refusé en 4401 : l'overlay vocal ne recevait plus rien. Le canal dédié corrige
la panne et la cause.

## Architecture

```
Producteurs                Traduction                Transport
───────────                ──────────                ─────────
audio_daemon ──┐
               ├─► jarvis/tv_events.py ──► TvEventHub ──► api/ws_tv.py ──► tv/server.py
event_bus  ────┘   allowlist de types      file bornée    /ws/tv/events    relais SSE
(6 types)          allowlist de champs     drop-oldest    lecture seule    vers le navigateur
                   redaction + bornage
```

| Fichier | Rôle |
|---|---|
| `jarvis/tv_events.py` | Schéma, allowlist, redaction, hub de diffusion, pont bus |
| `api/ws_tv.py` | Endpoint, authentification, lecture seule, heartbeat, limites |
| `main.py` | Montage de `/ws/tv/events`, à côté de `/ws` inchangé |
| `api/lifespan.py` | Miroir de l'état du daemon audio vers le canal TV |
| `tv/server.py` | Consommateur : relaie vers le SSE `/api/events` de la TV |
| `tests/test_ws_tv_events.py` | 45 cas — authentification, contenu, lecture seule, backpressure |

## Authentification

Le canal réutilise la frontière déjà en place pour `/api/control/*` :

1. **Origine** — si un en-tête `Origin` est présent (donc un navigateur), il
   doit correspondre exactement au schéma, à l'hôte et au port du handshake,
   ou figurer dans `CSRF_ALLOWED_ORIGINS`. Sinon : fermeture **4403**, avant
   toute lecture du jeton, pour qu'une page hostile n'apprenne rien.
2. **Boucle locale** — le pair TCP doit être `127.0.0.1`, `::1` ou `localhost`.
   Le canal ne franchit pas la machine : un écran distant passe par le serveur
   TV, qui tourne sur ce Mac.
3. **Jeton** — en-tête `X-Jarvis-Control-Token`, comparé en temps constant au
   secret privé `data/.supervisor_control_token` (fichier `0600` généré au
   premier démarrage du backend). Absent ou invalide : fermeture **4401**.

Un navigateur ne peut pas poser d'en-tête personnalisé sur un handshake
WebSocket : ce canal est donc, de fait, réservé aux clients locaux non
navigateur. La vérification d'origine reste appliquée en défense de profondeur.

Le refus est prononcé **après** acceptation du handshake, volontairement :
Starlette transforme une fermeture antérieure en un HTTP 403 opaque, et le
relais TV a besoin de distinguer « le backend redémarre » de « ton jeton n'est
plus valide » pour choisir son délai de reconnexion.

### Codes de fermeture

| Code | Signification | Déclencheur |
|---|---|---|
| 4401 | Non authentifié | Jeton absent, invalide, pair distant, canal désactivé |
| 4403 | Origine refusée | `Origin` présent et non conforme |
| 4405 | Violation lecture seule | `TV_WS_MAX_CLIENT_VIOLATIONS` trames entrantes |
| 4408 | Client lent | Envoi expiré, ou budget de pertes dépassé |
| 4413 | Trame trop volumineuse | Trame entrante > `TV_WS_MAX_CLIENT_MESSAGE_BYTES` |
| 4429 | Trop de connexions | `TV_WS_MAX_CONNECTIONS` déjà atteint |

Tout refus d'authentification est journalisé en **ERROR** sur le logger
`jarvis.ws_tv`, avec une étiquette fermée (`jeton_invalide`, `hors_boucle_locale`,
`origine_navigateur_refusee`…) et l'adresse du pair. **Aucune valeur fournie par
le client n'est journalisée** — donc jamais un jeton, valide ou non. Un test
paramétré le vérifie sur les trois chemins de refus.

## Schéma d'événement

```json
{
  "schema_version": 1,
  "event_id": "0f0a...-uuid4",
  "type": "tv.notification",
  "timestamp": 1785312000.482,
  "device_id": "mac_mini",
  "state": "high",
  "source": "database.notifications",
  "payload": {"notification_id": 42, "priority": "high", "title": "Facture EDF"}
}
```

`schema_version` est incrémenté à toute suppression ou renommage d'un champ de
premier niveau. `event_id` permet une déduplication après reconnexion.
`state` est un identifiant court normalisé (32 caractères, sans espace).

### Types diffusés

| Type | `state` | Payload autorisé |
|---|---|---|
| `tv.voice_state` | `idle`, `listening`, `processing`, `speaking`, `error`… | `enabled`, `wake_word_enabled`, `continuous_mode`, `last_interaction`, `error`, (`user_text`, `jarvis_text` sur opt-in) |
| `tv.notification` | priorité | `notification_id`, `priority`, `notification_source`, `title` |
| `tv.task` | `created` / `updated` | `task_id`, `title`, `priority`, `status`, `due_date`, `category` |
| `tv.system` | `service_up`, `service_down`, `error` | `service`, `detail` |
| `tv.heartbeat` | `alive` | *(vide)* |

### Ce qui n'est jamais traduit

`message.sent`, `conversation.updated`, `episode.saved`, `fact.added`,
`memory.updated`, `person.upserted` et `pattern.detected` n'ont **aucune**
traduction TV. Ce n'est pas un oubli à combler : le contenu d'une conversation
ou d'une mémoire n'a rien à faire sur un écran de salon. Deux tests le
verrouillent — l'un sur la table de traduction, l'autre sur les handlers
réellement enregistrés au bus.

Les transcriptions vocales suivent la même logique : `user_text` et
`jarvis_text` sont retirés du payload tant que
`TV_EVENTS_INCLUDE_TRANSCRIPTS=false` (défaut).

### Filtrage appliqué avant diffusion

Dans cet ordre, pour que le moins de données possible traverse chaque étape :

1. **Allowlist de clés** par type (`TV_PAYLOAD_FIELDS`).
2. **Retrait des transcriptions** si l'opt-in est fermé.
3. **Redaction des secrets** (`jarvis.security.redaction`) : clés sensibles
   remplacées, motifs de jetons et clés d'API masqués dans les valeurs.
4. **Bornage** : chaînes tronquées à `TV_EVENT_MAX_TEXT_CHARS`, profondeur
   limitée à 2, 10 éléments par liste, 10 clés par sous-dictionnaire.
5. **Plafond de taille** : un événement sérialisé au-delà de
   `TV_WS_MAX_EVENT_BYTES` n'est **pas** diffusé et un avertissement est
   journalisé — mieux vaut une absence visible qu'un affichage amputé sans le dire.

## Robustesse

- **Heartbeat** — `tv.heartbeat` toutes les `TV_WS_HEARTBEAT_SECONDS` en
  l'absence d'événement. Il maintient la connexion et prouve que le canal est
  vivant, y compris une nuit entière sans activité.
- **Détection de déconnexion** — la boucle de lecture (qui n'existe que pour
  refuser les écritures) détecte `websocket.disconnect` immédiatement ; la
  boucle d'émission est annulée dans la foulée.
- **Nettoyage** — l'abonnement au hub et le créneau de connexion sont libérés
  dans un `finally`, y compris en cas d'exception.
- **Limite de connexions** — `TV_WS_MAX_CONNECTIONS` canaux simultanés.
- **Backpressure** — file bornée par abonné. Pleine, elle perd son événement le
  plus ancien (l'état courant vaut mieux qu'un historique en retard) et compte
  la perte. Au-delà de `TV_WS_MAX_DROPPED_EVENTS`, l'abonné est déclaré en
  débordement et déconnecté en 4408. Un envoi qui n'aboutit pas en
  `TV_WS_SEND_TIMEOUT_SECONDS` produit la même fermeture. Un client lent ne
  ralentit donc jamais les autres, ni le producteur.
- **Taille des trames entrantes** — au-delà de
  `TV_WS_MAX_CLIENT_MESSAGE_BYTES`, fermeture immédiate en 4413, sans consommer
  le budget de violations.

## Consommateur : le serveur TV

`tv/server.py` ouvre le canal avec le jeton lu dans
`data/.supervisor_control_token`, puis relaie vers son propre flux SSE
`/api/events`, dans le format historique attendu par l'overlay du navigateur :
`tv.voice_state` redevient `{"type": "audio_daemon_state", "state": …}`, les
autres types TV passent tels quels, le heartbeat du canal est absorbé (le SSE
émet déjà le sien). Le contrat navigateur est donc inchangé.

Le serveur TV n'envoie **jamais** rien sur ce canal. Un refus
d'authentification (4401/4403 ou handshake rejeté) est réessayé toutes les
30 secondes, contre 5 secondes pour une panne réseau : une erreur de
configuration ne doit pas marteler le backend.

## Configuration

```bash
TV_EVENTS_ENABLED=true
TV_EVENTS_DEVICE_ID=                  # vide → DEVICE_ID, sinon "mac_mini"
TV_EVENTS_INCLUDE_TRANSCRIPTS=false   # texte de conversation sur l'écran : opt-in
TV_EVENT_MAX_TEXT_CHARS=200
TV_WS_MAX_CONNECTIONS=4
TV_WS_QUEUE_MAXSIZE=100
TV_WS_MAX_DROPPED_EVENTS=200
TV_WS_HEARTBEAT_SECONDS=20
TV_WS_SEND_TIMEOUT_SECONDS=5
TV_WS_MAX_EVENT_BYTES=8192
TV_WS_MAX_CLIENT_MESSAGE_BYTES=4096
TV_WS_MAX_CLIENT_VIOLATIONS=3
```

Aucun jeton à configurer : le canal réutilise le secret du canal supervisor.

## Vérification manuelle

```bash
python - <<'PY'
import asyncio, json, pathlib, websockets

token = pathlib.Path("data/.supervisor_control_token").read_text().strip()

async def main() -> None:
    async with websockets.connect(
        "ws://127.0.0.1:8081/ws/tv/events",
        additional_headers={"X-Jarvis-Control-Token": token},
    ) as ws:
        async for raw in ws:
            print(json.loads(raw))

asyncio.run(main())
PY
```

Sans en-tête, la connexion se ferme en 4401 et le backend journalise
`[ws/tv] connexion refusée — motif=jeton_absent`.

## Limites assumées

- **Pas de TTL sur le jeton.** Le secret supervisor est un fichier local sans
  expiration : son invalidation passe par la rotation (réécriture du fichier),
  qui refuse immédiatement l'ancienne valeur. C'est ce que teste le cas dit
  « jeton expiré ». Un vrai TTL supposerait un mécanisme d'émission de tickets
  que rien ne justifie pour un canal loopback.
- **Pas de rejeu à la reconnexion.** Le canal diffuse le présent ; un client
  qui se reconnecte a perdu ce qui s'est produit pendant la coupure. Les
  widgets TV concernés lisent déjà l'état complet en HTTP.
- **File SSE unique côté serveur TV.** Le relais partage une file entre tous
  les navigateurs connectés à `/api/events` — limitation antérieure à ce lot,
  sans effet tant qu'un seul écran est branché.
- **Pas de TLS sur le canal.** Il est restreint à la boucle locale, où le
  chiffrement n'ajoute rien face au modèle de menace retenu. Tout usage
  au-delà de la machine devrait passer par le serveur TV, pas par ce canal.
