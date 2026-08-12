# Audit JARVIS ↔ Claw3D ↔ OpenClaw — 11 août 2026

## Verdict

Oui, il manque encore un **plan de contrôle et de télémétrie agents unifié** à
JARVIS pour que Claw3D soit aussi pertinent qu'avec OpenClaw. Le problème visible
actuel n'est toutefois pas un manque d'avatars ou de scène 3D : la scène ne reçoit
aucune donnée valide.

Le frontend Claw3D est actif, mais son connecteur JARVIS échoue avant de pouvoir
charger le roster :

1. le backend JARVIS utilise un certificat TLS auto-signé que le processus Node
   Claw3D ne reconnaît pas (`DEPTH_ZERO_SELF_SIGNED_CERT`) ;
2. même avec ce certificat explicitement accepté pour le diagnostic, les deux
   routes génériques consommées par Claw3D sont protégées par la session JARVIS et
   répondent `401` sans cookie ;
3. Claw3D transforme cet échec en `502 visual_connector_unavailable` pour le
   snapshot et le flux SSE.

Le polish 3D ne doit donc commencer qu'après réparation de ce plan de données.

## Preuves runtime

État observé le 11 août 2026 :

- JARVIS écoute en HTTPS sur `127.0.0.1:8081` ;
- Claw3D écoute sur `127.0.0.1:3000` et annonce l'adaptateur
  `jarvis-readonly` activé ;
- `GET https://127.0.0.1:8081/api/status` échoue d'abord sur la confiance TLS
  depuis Node, puis répond `401` lorsque le certificat est accepté pour le test ;
- `GET http://127.0.0.1:3000/api/visual-runtime/v1/snapshot` répond `502` ;
- `GET http://127.0.0.1:3000/api/visual-runtime/v1/events` répond `502` ;
- la page `/office` elle-même répond `200`.

Le certificat est valide jusqu'en octobre 2028 et possède les SAN nécessaires
(`localhost`, `jarvis.local`, `127.0.0.1`) ; seul son ancrage de confiance manque
au processus Node. Désactiver globalement la vérification TLS n'est pas une
solution acceptable.

## Ce que JARVIS possède déjà

La fondation n'est pas à reconstruire :

- `EventBus` asynchrone avec journal SQLite durable ;
- SSE reprenable avec identifiants croissants et fenêtre de replay bornée ;
- événements `agent.start`, `agent.response`, `agent.action`,
  `agent.action_result`, tâches et notifications ;
- huit agents réellement enregistrés au démarrage : `devops`, `food`, `info`,
  `school`, `productivity`, `coach`, `journal`, `memory` ;
- délégations Cursor persistantes et boucle d'ingénierie autonome ;
- tâches, notifications, workflows agentiques et supervision de services ;
- intégration Claw3D confinée, épinglée et optionnelle.

La base runtime contient notamment 461 `agent.start`, 455 `agent.response`,
73 `agent.action`, 73 `agent.action_result`, 15 `task.created` et
13 `task.updated`. Les données existent donc ; elles ne sont ni exposées ni
projetées de façon cohérente vers la scène.

## Écarts fonctionnels qui empêchent une visualisation pertinente

### P0 — Connectivité réellement cassée

Le connecteur lit actuellement `/api/status` et `/api/events/stream`, deux routes
génériques privées. Il n'a ni chaîne de confiance TLS dédiée ni mécanisme
d'authentification compatible avec le verrou de session JARVIS.

Correction recommandée : créer un contrat JARVIS dédié, minimal et pré-sanitized,
par exemple :

- `GET /api/visual/v1/snapshot` ;
- `GET /api/visual/v1/events` ;
- `GET /api/visual/v1/health`.

Ces routes ne doivent jamais exposer prompts, réponses, arguments d'outils,
chemins locaux ou secrets. Elles peuvent être accessibles uniquement sur
loopback, avec un contrat explicitement public car non sensible, ou protégées
par une capacité de service strictement `visual:read`. La première option reste
la plus cohérente avec la frontière actuelle « aucun secret relayé ».

Le certificat public JARVIS doit être copié dans l'état confiné Claw3D et chargé
comme CA additionnelle au démarrage Node. Il ne faut pas utiliser
`NODE_TLS_REJECT_UNAUTHORIZED=0`.

### P1 — Roster statique et incomplet

`/api/status` publie une liste codée en dur de six agents, alors que le registre
runtime en contient huit. `devops` et `food` ont produit des événements mais ne
peuvent pas apparaître dans le snapshot. Le reducer visuel ne crée pas un acteur
inconnu à la volée ; leurs événements sont donc sans effet à l'écran.

Le snapshot doit être dérivé du registre réel et inclure :

- identité stable, nom d'affichage, rôle et capacité ;
- présence (`online`, `offline`, `disabled`) ;
- état (`idle`, `planning`, `working`, `waiting`, `reviewing`, `blocked`,
  `error`) ;
- activité sûre et courte ;
- tâche/run courant, progression, parent et horodatage ;
- dernier heartbeat et expiration de lease.

### P1 — Pas de modèle canonique `AgentRun` / `WorkItem`

Le travail est aujourd'hui réparti entre appels `BaseAgent`, workflows
agentiques, tâches personnelles, jobs Cursor, boucle d'ingénierie et daemons.
Chaque sous-système possède ses statuts, mais aucun modèle commun ne permet de
répondre proprement à : « qui travaille ? sur quoi ? depuis quand ? avec quel
résultat ? ».

Il faut introduire une projection canonique, sans réécrire les moteurs :

- `AgentRun`: id, actor, kind, phase, started/updated/finished, parent, progress,
  safe summary, outcome ;
- `WorkItem`: id, titre sûr, statut, priorité, owner, source, progression ;
- événements normalisés : `run.started`, `run.phase_changed`,
  `run.progressed`, `run.blocked`, `run.completed`, `run.failed`,
  `work.assigned`, `work.updated` ;
- adaptateurs internes pour BaseAgent, Cursor, engineering-team, scheduler et
  voice pipeline.

### P1 — « Ce qu'ils font » n'existe pas dans le contrat visuel

L'adaptateur supprime correctement le contenu privé, mais ne le remplace pas par
une description sûre. Claw3D ne reçoit que l'état et le canal. Il peut montrer
« working », pas « analyse les tests du module auth ».

La description doit être créée à la source, pas extraite du prompt brut :

- `activity_label` contrôlé et plafonné ;
- `phase_label` ;
- `tool_category` allowlistée, jamais les arguments ;
- `progress_current` / `progress_total` lorsque connu ;
- résumé d'issue/PR/tâche déjà public ou explicitement sanitizé.

### P1 — Cycle de vie incomplet en cas d'erreur

Un appel `BaseAgent` émet `agent.start`, puis `agent.response` uniquement si
l'appel LLM réussit. Une exception peut laisser l'avatar bloqué en `working`.
Il faut un état terminal systématique (`failed` ou `cancelled`) ainsi qu'une
lease/expiration pour ramener automatiquement un acteur abandonné à `idle` ou
`error`.

### P1 — Snapshot incomplet

Le snapshot Claw3D initialise toujours `tasks`, `notifications` et `history` à
vide. Ces données n'apparaissent que si un événement compatible arrive dans la
petite fenêtre SSE récente. Une tâche active antérieure ou un run long disparaît
donc au rechargement.

Le snapshot dédié doit reconstruire la vérité courante depuis les projections
persistantes, puis le SSE ne transporte que les deltas.

### P2 — La version lancée n'utilise pas Claw3D à 100 %

Le lifecycle JARVIS démarre uniquement `apps/claw3d-ui`, une surface confinée
avec `/office` et `/office/builder`. L'application Claw3D principale contient
également les écrans et opérations de flotte, chat, tâches, présence, standups,
skills, permissions et gateway, mais elle n'est pas lancée par cette intégration.

C'est un choix d'architecture explicite : le mode actuel est **visuel,
read-only**, pas un control plane. Obtenir 100 % de la parité fonctionnelle
Claw3D/OpenClaw exige ensuite un **JARVIS Gateway Adapter** authentifié et
capability-based, pas seulement davantage de composants 3D.

## Écart avec OpenClaw

OpenClaw fournit à Claw3D un plan de contrôle unifié :

| Domaine | OpenClaw | JARVIS aujourd'hui | Manque prioritaire |
|---|---|---|---|
| Agents | agents isolés avec workspace, état et sessions propres | agents métier enregistrés dans un même runtime | registre dynamique + projection de présence |
| Runs | sous-agents suivis comme tâches de fond, sessions et annonces | appels LLM, Cursor et engineering-team séparés | `AgentRun` commun |
| Parallélisme | swarm borné avec progression et résultats structurés | plusieurs moteurs, aucune télémétrie commune | événements de progression/parenté |
| Gateway | protocole central avec auth, sessions, événements et capacités | REST/SSE métier + WebSockets spécialisés | adaptateur Gateway JARVIS |
| Skills/outils | skills chargés avec gating, allowlists et overrides | prompts et capacités dispersés | catalogue runtime lisible et permissionné |
| Autonomie | standing orders, tâches de fond et escalades | scheduler et boucle d'ingénierie existants | vue commune des programmes autonomes |
| Claw3D | application complète de bureau et de contrôle | workspace visuel read-only uniquement | activer progressivement les capacités sûres |

La bonne cible n'est pas de copier OpenClaw. JARVIS possède déjà des briques
plus adaptées à son architecture locale ; il faut les présenter sous un contrat
de contrôle cohérent que Claw3D comprend.

## Plan recommandé

### Phase A — Rendre visible et fiable (P0)

1. ajouter la confiance TLS locale sans désactiver la vérification ;
2. créer les endpoints visuels JARVIS sanitizés ;
3. remplacer la liste statique par le registre runtime ;
4. ajouter un test d'intégration réel HTTPS + auth/route publique + snapshot +
   première trame SSE ;
5. rendre l'erreur visible dans l'office avec cause exploitable et healthcheck.

Critère : les huit agents apparaissent après un rechargement et un appel réel
fait passer l'avatar `idle → working → idle/error`.

### Phase B — Montrer le travail réel (P1)

1. introduire `AgentRun` / `WorkItem` et leur projection persistante ;
2. instrumenter BaseAgent, Cursor, engineering-team, scheduler et voice ;
3. publier activité sûre, progression, blocages et résultats ;
4. reconstruire tâches/runs actifs dans le snapshot ;
5. ajouter heartbeats, leases, reprise et déduplication.

Critère : l'office répond fidèlement à « qui fait quoi, où en est-il, et pourquoi
est-il bloqué ? » après restart et reconnexion.

### Phase C — Exploiter Claw3D à 100 % de façon sûre (P2)

1. définir un protocole `JARVIS Gateway` versionné ;
2. implémenter l'adaptateur Claw3D côté serveur ;
3. séparer capacités read-only, chat, task-control, approvals et administration ;
4. activer les écrans Claw3D par capability flags ;
5. garder les actions sensibles derrière session, CSRF/jeton de service,
   confirmations et journal d'audit.

Critère : les fonctions riches sont activées sans exposer cookie, prompt,
secret ou commande non autorisée au navigateur.

### Phase D — Perfection visuelle et performance (après A/B)

Optimiser ensuite avatars, animations par phase, déplacement vers des zones de
travail, bulles d'activité, timeline, caméra, filtres, accessibilité, rendu mobile,
LOD, budgets GPU, reconnect UX et E2E visuels. Sans les phases A/B, ce travail ne
ferait qu'embellir un tableau vide.

## Validations exécutées

- tests lifecycle JARVIS/Claw3D : **33 passés**, 1 warning de dépréciation
  `httpx` ;
- tests visuels Claw3D : **23 passés** ;
- typecheck visuel : **passé** ;
- sondes live : page `200`, snapshot `502`, events `502`, backend privé `401`
  après acceptation diagnostique du certificat ;
- les tests connecteur actuels mockent le réseau et ne couvrent pas la chaîne
  HTTPS + verrou de session réelle, ce qui explique leur succès malgré la panne.

## Décision recommandée

Commencer par les phases A puis B en conservant le mode read-only. C'est le
chemin le plus rapide pour obtenir une office 3D utile et honnête. N'activer la
parité de contrôle complète qu'ensuite, avec un choix produit explicite, car elle
modifie la frontière de sécurité actuellement documentée.

## Sources de comparaison

- OpenClaw, runtime multi-agent : https://docs.openclaw.ai/concepts/multi-agent
- OpenClaw, sous-agents : https://docs.openclaw.ai/tools/subagents
- OpenClaw, swarm : https://docs.openclaw.ai/tools/swarm
- OpenClaw, skills : https://docs.openclaw.ai/tools/skills
- OpenClaw, standing orders : https://docs.openclaw.ai/automation/standing-orders
- Claw3D : https://github.com/AVTAVANTTOUT2/Claw3D

