# Mise à niveau

Une mise à niveau est une modification de `release-manifest.json`, jamais un
auto-update du binaire. Elle exige : tag et version exacts, checksums de chaque
plateforme, revue des advisories, hash de licence, contrat client, tests
offline/live/removal et preuve de rollback.

```bash
python -m integrations.opencode.scripts.manager install
python -m integrations.opencode.scripts.manager verify
python -m integrations.opencode.scripts.manager smoke-test --workspace /chemin/worktree
python tools/run_integration_ci.py --phase offline
python tools/run_integration_ci.py --phase live
python tools/run_integration_ci.py --phase removal
```

En cas d'échec, arrêter le runtime, restaurer le manifest et le binaire épinglé
précédents, puis relancer `verify`. Aucun état de run ne doit être supprimé
pour masquer un échec de reprise.
