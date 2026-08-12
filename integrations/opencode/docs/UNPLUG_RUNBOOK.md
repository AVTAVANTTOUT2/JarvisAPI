# Runbook de désactivation et retrait

## Désactiver sans supprimer

```bash
export AGENTIC_RUNTIME=disabled
python -m integrations.opencode.scripts.manager stop
python -m integrations.opencode.scripts.manager status
```

## Désinstaller le binaire et les états privés

```bash
python -m integrations.opencode.scripts.manager uninstall
python -m integrations.opencode.tools.removal_proof
```

## Retirer le plugin du code

Dans une branche dédiée, supprimer exactement `integrations/opencode/`, puis
exécuter la matrice de [REMOVAL_PROOF.md](REMOVAL_PROOF.md). Le cœur générique,
les routes, les clients et les boucles doivent continuer à démarrer ; une
demande agentique termine explicitement `provider_unavailable`. Ne supprimer
aucun run utilisateur ni aucun autre dossier d'intégration.
