# Pont MCP JARVIS

## Rôle et transport

Le pont MCP expose au runtime uniquement des opérations JARVIS explicitement
accordées au run. OpenCode lance un proxy stdio sans autorité ; le broker et
l'enveloppe de capacité restent dans le processus parent JARVIS. La
configuration injectée ne contient ni bearer, ni scope, ni identité de run :

```json
{
  "mcp": {
    "jarvis": {
      "type": "local",
      "command": [
        "/chemin/absolu/python",
        "-m",
        "integrations.opencode.mcp.server",
        "proxy",
        "--transport",
        "unix",
        "--bootstrap-socket",
        "/tmp/jarvis-mcp-<aleatoire>/bootstrap.sock",
        "--socket-path",
        "/tmp/jarvis-mcp-<aleatoire>/broker.sock"
      ],
      "environment": {"PYTHONPATH": "/racine/absolue/JARVIS"},
      "enabled": true
    }
  }
}
```

Le répertoire éphémère est créé en `0700`, ses sockets en `0600`, sans lien
symbolique. JARVIS lie d'abord le PID du serveur OpenCode. Le bootstrap vérifie
ensuite l'UID et l'ascendance du proxy, remet le bearer exactement une fois,
ferme puis supprime immédiatement son endpoint. Le bearer n'apparaît donc ni
dans `argv`, ni dans l'environnement, ni dans un fichier ou un log ; la socket
broker n'accepte ensuite que le PID ayant réclamé le bootstrap. Le nom de socket
est un localisateur non secret : une course locale ne peut provoquer qu'un refus
fermé ou un déni de service.

Ce transport sécurisé est activé sur macOS et Linux. Sur Windows, où cette
version ne dispose pas d'une preuve de peer PID/UID testée, le broker échoue
explicitement avec `unsupported_secure_peer_transport` et n'expose aucun outil
MCP. Le lifecycle OpenCode et les autres fonctions restent disponibles.

Le serveur implémente le protocole MCP `2025-11-25` sur des objets JSON-RPC
délimités par ligne. Il prend en charge `initialize`, `notifications/initialized`,
`tools/list`, `tools/call` et `ping`. Toute méthode, forme ou version non prévue
échoue explicitement.

## Enveloppe de capacité

JARVIS conserve l'enveloppe en mémoire parent. Elle contient audience, run,
profil, scopes, identité device/inode du workspace résolu, émission, expiration
(entre 60 et 900 secondes) et nonce. Le child ne reçoit qu'un bearer opaque lié
à cette enveloppe exacte et ne peut ni la sérialiser ni l'élargir.

Pour chaque appel, le serveur ajoute lui-même une propriété `_jarvis` :

```json
{
  "_jarvis": {
    "run_id": "<run-id-exact>",
    "tool_call_id": "<id-stable-et-unique>",
    "origin": "agent_runtime",
    "bypass_agentic_reclassification": true
  }
}
```

Le child ne peut pas fournir ou remplacer cette propriété réservée. Cette garde
empêche qu'un effet issu du runtime soit reclassifié comme une nouvelle demande
agentique. Elle ne contourne ni scope, ni profil, ni approbation. `_jarvis`
n'est jamais transmis au handler métier.

Un outil à effet reste invisible et refusé sans approbation JARVIS. Le reçu
reste en mémoire parent et lie exactement approval ID, run, outil, digest des
arguments et expiration (au plus dix minutes et jamais au-delà de la capacité).
Il est réservé une seule fois avant l'appel métier. Un replay exact ne peut que
relire le résultat idempotent déjà journalisé ; un état `pending` après crash est
ambigu et échoue fermé jusqu'à une récupération parent explicite.

## Outils exposés

| Outil | Scope | Risque | Entrée importante | Comportement |
|---|---|---|---|---|
| `jarvis_tasks_list` | `tasks:read` | lecture seule | `status`: `all`, `todo`, `doing` ou `done` | lit les tâches du profil actif |
| `jarvis_tasks_create` | `tasks:write` | réversible | `title` et `idempotency_key` obligatoires | crée une tâche bornée dans le profil actif |

Le manifeste déclare aussi `workspace:read`, `workspace:write` et `tests:run`
pour les outils natifs du runtime, mais ils ne deviennent actifs que si le run
les accorde. Une demande read-only ne reçoit jamais edit/write ; bash n'est
jamais activé dans OpenCode. Les commandes de test allowlistées restent exécutées
par JARVIS hors du processus fournisseur.

Les résultats MCP sont marqués `untrusted_tool_data`, structurés et redactés.
Les contenus d'une tâche ne deviennent donc jamais des instructions système.

## Idempotence et audit

Chaque run possède un journal privé
`.runtime/.../state/capabilities/<run-haché>.idempotency.json`. Pour une
mutation, la clé et le digest canonique du payload sont persistés et fsync en
état `pending` avant l'effet, puis scellés avec le résultat. Même clé et même
payload rejouent le résultat ; même clé avec payload différent est refusée. Un
crash entre effet et scellement laisse `pending` et bloque tout replay implicite.
Le journal est borné et échoue fermé lorsqu'il est plein.

L'ID du run, l'ID d'appel, l'outil et le niveau de risque sont présents dans la
réponse structurée et les événements JARVIS génériques. Les secrets, arguments
arbitraires et contenu brut ne doivent pas être copiés dans les événements ou
logs. L'audit durable reste celui de JARVIS ; le journal MCP est un mécanisme
local de non-répétition, pas la source de vérité métier.

## Ajouter un outil

Un nouvel outil exige simultanément :

1. un scope minimal déclaré dans le manifeste et compris par le cœur générique ;
2. un schéma strict (`additionalProperties: false`) et des bornes explicites ;
3. un handler profilé, idempotent pour tout effet et sans réentrée agentique ;
4. redaction des entrées/sorties, timeout et niveau de risque ;
5. tests de scope absent/expiré, profil, traversée, replay, collision de clé,
   injection de prompt et fuite de secret ;
6. mise à jour du threat model et de cette table.

Ne jamais exposer un shell général, un secret, une primitive de commit/push ou
un client réseau arbitraire via MCP.
