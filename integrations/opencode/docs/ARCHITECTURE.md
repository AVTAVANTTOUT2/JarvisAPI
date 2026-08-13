# Architecture du plugin

## Frontière de responsabilité

Le cœur JARVIS découvre les runtimes en parcourant dynamiquement
`integrations/*/plugin.json`. Il ne contient ni import statique ni identifiant
de ce fournisseur. Le manifeste déclare l'entrypoint
`integrations.opencode.register:create_runtime`, chargé seulement lorsqu'un
run sélectionne ce runtime. Supprimer le dossier retire donc le fournisseur du
registre sans retirer le domaine agentique générique.

Le flux nominal est :

1. un canal JARVIS classifie une demande et crée un run générique persistant ;
2. le service agentique applique profil, idempotence, budget et admission ;
3. le registre charge paresseusement l'adaptateur déclaré par le manifeste ;
4. l'adaptateur vérifie ou installe le binaire épinglé, provisionne un serveur
   privé et crée une session ;
5. le serveur consomme uniquement les capacités natives autorisées et le pont
   MCP local propre au run, y compris les approbations dynamiques one-shot ;
6. l'adaptateur traduit le SSE fournisseur en événements JARVIS, sans exposer
   les messages bruts ni le raisonnement interne ;
7. JARVIS persiste les événements et artefacts, gère les approbations, puis
   exécute sa vérification déterministe avant tout état `completed`.
8. l'admission (mémoire, concurrence d'écriture, priorité utilisateur) peut
   garder un run en `queued` avec `agent.run.resource_wait` plutôt que de
   lancer un OOM.

OpenCode ne possède donc ni l'état métier, ni le profil, ni la politique Git,
ni la décision finale. Les commits, push, PR et validations de dépôt restent
la responsabilité de DevAgent/JARVIS.

## Composants

- `register.py` : factory minimale, sans processus ni réseau à l'import ;
- `adapter.py` : traduction du protocole générique, confinement du workspace,
  agents obligatoires et sélection d'un modèle connecté ;
- `client/` : client HTTP/SSE typé contre le contrat 1.18.16 ;
- `lifecycle/` : téléchargement, vérification, démarrage, santé, arrêt et
  propriété du processus ;
- `config/` : configuration durcie et layout privé ;
- `security/` : chemins, environnement, redaction et défense contre
  l'injection d'instructions ;
- `mcp/` : serveur stdio par run et outils JARVIS à capacités ;
- `scripts/manager.py` : interface d'exploitation JSON ;
- `tests/` et `tools/removal_proof.py` : contrats hors ligne et preuve de
  suppression.

## Processus et stockage

Un seul serveur privé et un seul run actif sont utilisés séquentiellement par
instance d'adaptateur. La concurrence globale est arbitrée par JARVIS. Le
serveur écoute exclusivement sur une adresse et un port loopback alloué à la
volée, avec Basic Auth éphémère. Le partage, mDNS, CORS additionnel, Web UI et
mise à jour automatique sont désactivés.

Tous les fichiers fournisseur sont confinés sous
`integrations/opencode/.runtime/` :

```text
.runtime/
├── bin/          # binaire épinglé
├── config/       # manager.json, opencode.json et XDG_CONFIG_HOME
├── data/         # HOME et XDG_DATA_HOME
├── cache/        # XDG_CACHE_HOME
├── state/        # installation, PID, auth, capacités, idempotence
├── logs/         # stdout/stderr privés
└── tmp/          # archives et extraction bornée
```

Les répertoires sont privés (`0700`) et les états sensibles sont écrits de
façon atomique et privée (`0600`) sur les plateformes POSIX. Liens symboliques,
jonctions/réparse points détectables et sorties de frontière sont refusés.

## Agents internes

La configuration exige `jarvis-planner`, `jarvis-executor`,
`jarvis-reviewer` et `jarvis-coding`. Planner et reviewer sont en lecture
seule. Executor et coding demandent une validation pour les effets ; les
commandes `git commit`, `git push`, `git merge` et `git rebase` restent
interdites. Aucun agent fournisseur ne parle directement à l'utilisateur :
les résumés sûrs repassent par JARVIS.

## Événements et limites

Seuls les statuts de session, demandes/réponses de permission, étapes outils
sanitisées et mises à jour de tâches sont traduits. Le contenu arbitraire, les
arguments d'outils, les secrets et toute chaîne de pensée sont écartés ou
redactés. Les sorties outils sont bornées à 32 KiB et 500 lignes ; délais,
reconnexions, budgets et limites de concurrence sont appliqués aux frontières
correspondantes.
