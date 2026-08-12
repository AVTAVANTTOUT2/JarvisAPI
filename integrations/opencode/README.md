# Plugin runtime OpenCode

Ce dossier est l'unique frontière fournisseur de l'intégration OpenCode. Le
cœur `jarvis/agentic`, l'API, les clients, le scheduler et le DevAgent ne
l'importent jamais statiquement : ils découvrent `plugin.json` et son factory
au démarrage.

- version épinglée : **1.18.16** ;
- installation locale : `python -m integrations.opencode.scripts.manager install` ;
- vérification : `python -m integrations.opencode.scripts.manager verify` ;
- arrêt et retrait : voir [docs/UNPLUG_RUNBOOK.md](docs/UNPLUG_RUNBOOK.md) ;
- index documentaire : [docs/README.md](docs/README.md).

Le runtime n'est pas autorisé à committer, pousser, ouvrir une PR, fusionner
ou déployer. JARVIS conserve ces responsabilités et vérifie les preuves avant
tout statut `completed`.
