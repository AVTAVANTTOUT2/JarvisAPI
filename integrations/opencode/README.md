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

## Livraison de développement via Task Control

Le chemin interne `TaskControlService.create_engineering_task()` lie le dépôt,
les validations JARVIS et `opencode@1.18.16` à l'empreinte du plan. Avant
l'approbation exacte, aucun worktree et aucun processus ne sont créés. Après
l'édition, JARVIS — pas OpenCode — exécute les tests dans son sandbox, vérifie
le manifeste de fichiers, crée le commit local et persiste les reçus. Le push
et la PR restent désactivés.

Le runner live est séparé en deux gestes opérateur. Il lit uniquement la clé
injectée dans l'environnement et n'écrit jamais `.env` :

```bash
python integrations/opencode/scripts/task_control_delivery_live.py plan \
  --repo /chemin/absolu/du/depot \
  --title "Corriger calculator.add" \
  --request "Corriger la soustraction en addition" \
  --test "python3 -m pytest tests/test_calculator.py -q" \
  --acceptance "calculator.add(2, 3) retourne 5" \
  --idempotency-key "ticket-123"

python integrations/opencode/scripts/task_control_delivery_live.py approve \
  --task-id TASK_ID_RENVOYE \
  --plan-version VERSION_RENVOYEE \
  --plan-digest DIGEST_RENVOYE
```

Sans `DEEPSEEK_API_KEY` non factice dans le processus, le runner s'arrête avant
toute création et rend `production: NOT_EXECUTED`. La preuve hermétique sans
clé reste :

```bash
pytest -m external_network \
  integrations/opencode/tests/test_real_binary_e2e.py \
  -k task_control_delivery -q
```
