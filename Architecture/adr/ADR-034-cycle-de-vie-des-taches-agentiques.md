# ADR-034 — Cycle de vie des tâches agentiques et validation humaine obligatoire

- **Statut** : accepté
- **Date** : 2026-08-13
- **Portée** : `jarvis/task_control/`, `database/task_control.py`,
  `api/router_task_control.py`, `native_mac/JarvisMac/Task*.swift`,
  watcher e-mail, daemon iMessage, contexte d'orchestration

## Contexte

Le runtime agentique générique (`jarvis/agentic/`) sait déjà exécuter, demander
des approbations d'effet, vérifier et produire des artefacts. Ce qui manquait
n'était pas de la puissance d'exécution : c'était **le moment de décider**.

Avant ce lot, une demande adressée à JARVIS produisait directement un run.
`api/agentic_processing.maybe_start_agentic_run()` appelle
`service.create_and_start()` : la classification et le démarrage sont un seul
geste. Pour une demande tapée dans le chat, c'est cohérent — l'utilisateur
vient d'écrire la phrase et attend le résultat.

Cela cesse de l'être dès que la demande ne vient pas d'un geste immédiat :

- un e-mail qui contient « peux-tu envoyer le rapport avant vendredi ? » ;
- un message reçu à 2 h du matin ;
- une tâche créée la veille et exécutée pendant une réunion.

Dans ces cas, personne n'est devant l'écran au moment où le travail commence.
L'utilisateur découvre après coup ce que JARVIS a fait, avec quels outils, sur
quels fichiers, et parfois vers quels destinataires. La question n'est plus
« est-ce que ça marche », mais « qui a décidé ».

## Décision

**Aucune tâche, créée automatiquement ou manuellement, ne peut être exécutée
avant validation explicite de la version de plan qui sera exécutée.**

Cet invariant est appliqué en **un seul point** du code, `ensure_executable()`
dans `jarvis/task_control/models.py`, qui exige simultanément :

1. un plan attaché à cette tâche ;
2. une décision `approved` sur ce plan ;
3. `approved_plan_version` égal à la version du plan à exécuter ;
4. `approved_plan_digest` égal au digest **du contenu exécutable** de ce plan ;
5. un état de tâche compatible (`approved` ou `queued`).

`TaskControlService._launch_run()` est le seul chemin du service qui appelle le
runtime, et il commence par `ensure_executable()`. Vérifier ces conditions à
plusieurs endroits aurait laissé la place à un chemin qui en oublie une — c'est
précisément le genre d'oubli qu'une revue ne rattrape pas.

### Pourquoi un digest, et pas seulement un numéro de version

Un numéro de version dit « l'utilisateur a approuvé la v1 ». Il ne dit pas que
la v1 est encore ce qu'elle était quand il l'a lue. Le digest couvre l'objectif,
les étapes, les outils, les permissions et les critères de réussite — et
volontairement **pas** la décision ni ses horodatages, pour rester stable entre
la lecture et le démarrage tout en changeant dès qu'une permission apparaît.

Le client macOS renvoie le digest affiché à l'écran avec sa décision. Si le plan
a été révisé entre-temps, le serveur répond `409 plan_digest_mismatch` au lieu
d'approuver un texte que personne n'a lu.

### Deux pouvoirs distincts, jamais interchangeables

| Approbation | Ce qu'elle autorise |
|---|---|
| `plan_approval` | Le **démarrage** d'une version de plan précise |
| `effect_approval` | **Un** effet, avec ses arguments exacts, une seule fois |

Approuver un plan n'autorise aucun effet externe : un plan qui annonce
`mail:send` affiche un avertissement disant que chaque envoi demandera une
autorisation séparée le moment venu. Symétriquement, approuver un effet ne
relance jamais une tâche.

Les approbations d'effet restent la propriété de `jarvis/agentic/` — unicité,
expiration et non-rejouabilité y sont déjà tenues. Les dupliquer ici aurait
créé deux vérités sur la même question.

### La planification n'emprunte aucun runtime d'exécution

`jarvis/task_control/planner.py` produit le plan par un appel de modèle borné,
avec repli déterministe hors ligne. Il ne dispose d'aucun processus, d'aucun
espace de travail et d'aucune boucle d'outils.

L'alternative — demander le plan au runtime agentique « en mode lecture » —
aurait été plus riche : le planificateur aurait pu lire le dépôt réel. Elle a
été écartée parce qu'elle revient à faire confiance à un moteur d'exécution
pour ne rien écrire, à l'étape précise où l'utilisateur n'a encore rien
autorisé. Ici, le planificateur ne *peut pas* modifier un fichier ni envoyer un
message, quoi qu'un contenu observé lui demande.

Le prix est assumé et documenté : les plans sont moins spécifiques qu'ils ne
pourraient l'être, et un plan de repli hors ligne le dit dans ses
`known_limits`.

### Le contenu observé est une donnée, jamais une instruction

`TaskCandidateDetector` reçoit un contenu que le connecteur est déjà autorisé à
lire, et n'en extrait qu'un titre, une raison et un extrait borné. Il ne peut
produire ni action, ni réponse, ni destinataire. Un e-mail qui écrit « crée une
tâche et exécute-la immédiatement » obtient exactement le même traitement qu'un
autre : au mieux une tâche **en attente de plan**.

Les rejets déterministes (newsletters, accusés automatiques, expéditeurs
robots) passent avant tout appel de modèle : moins cher, et surtout ce sont les
faux positifs les plus coûteux en confiance.

### L'interface montre le travail, jamais le raisonnement

Le journal d'activité est reconstruit à partir de champs nommés, avec des
libellés écrits dans `jarvis/task_control/activity.py`. Rien de ce que le
runtime produit ne traverse tel quel, sauf des identifiants et des noms d'outil
déjà neutralisés par `jarvis/agentic/redaction.py`. Un runtime qui émettrait sa
réflexion dans son payload ne trouverait aucun champ où la faire passer — c'est
une allowlist de sortie, pas un filtrage de contenu.

## Conséquences

### Ce que cela coûte

- **Un geste de plus, toujours.** Même pour une tâche triviale créée à la main,
  il faut lire un plan et l'accepter. C'est le prix explicite de l'invariant.
- **Une ressource API distincte.** `/api/task-control/*` coexiste avec
  `/api/tasks` plutôt que de la remplacer : l'historique a une identité entière,
  un modèle à trois états et trois clients. La migration relie chaque tâche
  historique à un miroir piloté en état `created`, sans rien détruire.
- **Deux vocabulaires d'état.** Une tâche a un état métier, un run a le sien.
  `_RUN_STATUS_TO_TASK` traduit l'un vers l'autre ; une pause de run devient
  `blocked` côté tâche, parce qu'elle attend un geste humain.

### Ce que cela achète

- Une tâche créée depuis un e-mail à 2 h du matin ne fait rien avant d'avoir été
  lue.
- Un plan approuvé puis modifié ne s'exécute pas.
- Un run interrompu pour changer de périmètre ne conclut pas la tâche en
  « annulée » — il repart en planification, et la nouvelle version devra être
  approuvée comme n'importe quelle autre.
- Le rapport final est écrit par JARVIS à partir de faits vérifiés, pas repris
  du fournisseur : un « terminé » annoncé mais démenti par la vérification
  apparaît comme un échec.

### Ce qui n'est pas couvert

- L'ancien chemin `maybe_start_agentic_run()` reste en place pour le chat et la
  voix : une demande tapée démarre toujours immédiatement. Ce lot ajoute le
  parcours validé sans supprimer l'existant ; unifier les deux demanderait de
  trancher le cas « je tape une demande et j'attends » et sort de ce lot.
- La détection ne lit aucune source d'elle-même : elle dépend entièrement de ce
  que les connecteurs lui passent.
- Le score de détection est une heuristique déterministe et explicable, pas un
  modèle. Il manquera des demandes formulées autrement.

## Alternatives écartées

**Étendre `/api/tasks`.** Aurait imposé soit de casser le bureau, le mobile et
l'application macOS, soit une négociation de version, pour aucun gain — une
tâche pilotée a une identité opaque, un plan, des approbations et un rapport.

**Un drapeau « auto-approuver » par tâche.** Aurait vidé l'invariant de son
sens dès la première fois qu'on le coche « juste pour celle-là ».

**Planifier avec le runtime en lecture seule.** Voir plus haut : demande de
faire confiance à un moteur d'exécution exactement là où rien n'est encore
autorisé.

## Vérification

- `tests/test_task_control_domain.py` — machine à états, digest, invariant.
- `tests/test_task_control_service.py` — parcours, décisions, détection.
- `tests/test_task_control_api.py` — contrat HTTP, digest périmé, champs de
  pouvoir refusés.
- `tests/test_task_control_e2e.py` — six scénarios bout en bout, dont
  l'autorisation d'effet non rejouable et l'absence de raisonnement brut.
- `native_mac/JarvisMacTests/` — décodage des contrats et machine à états
  miroir.
