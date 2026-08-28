# Rapport de validation

Les suites sont réparties entre tests hermétiques par défaut et E2E vrai
binaire opt-in (`external_network`, sans réseau public ni clé externe). Le
rapport exact du SHA livré figure dans la description de la draft PR.

Couverture obligatoire : installation/checksum/version, loopback et Basic
Auth, refus CORS hostile, sessions/SSE/abort, deux runs concurrents isolés,
config/plugin workspace hostiles, reprise d'orphelin, MCP readonly et mutateur
approuvé, budgets/doom-loop, approbations/idempotence/inbox, vérification,
DevAgent worktree/tests/commit/draft PR/CI, clients et preuve de suppression.

La preuve C1 `test_real_binary_task_control_delivery_runs_jarvis_pytest_and_commits`
traverse en plus la frontière produit complète : requête utilisateur, plan
Task Control signé et approuvé, vrai binaire épinglé, édition du worktree,
`pytest` lancé par JARVIS, reçu de test, commit local et rapport final. Le
provider de modèle est une fixture loopback ; le run DeepSeek de production
reste `NOT_EXECUTED` tant qu'aucune clé n'est injectée dans le processus.

Commandes canoniques :

```bash
python -m pytest integrations/opencode/tests -q
python -m pytest -m external_network integrations/opencode/tests -q
python -m ruff check integrations/opencode
python tools/run_integration_ci.py --phase offline
python tools/run_integration_ci.py --phase live
python tools/run_integration_ci.py --phase removal
```

## Validation du 12 août 2026

Résultats locaux sur la branche de livraison :

- suite Python JARVIS hors bac à sable : **2 512 réussis**, 7 désélectionnés
  (E2E opt-in), avec 1 avertissement de dépréciation Torch historique ;
- cœur DevAgent/livraison : **60 réussis**, y compris la preuve qu'un ancien
  `DEVAGENT_AUTO_DEPLOY_STAGING=true` ne peut plus déclencher de déploiement ;
- relais JARVIS/Claw3D : **30 réussis** ;
- plugin hors E2E : **136 réussis**, 8 E2E désélectionnés ; phase `live` vrai
  binaire : **8 réussis**, 21 tests non-live désélectionnés ;
- Web : **82 réussis**, typecheck réussi ; frontend unifié : **48 réussis**,
  typecheck et build production de 32 pages réussis ;
- Android debug/release : **120 tâches Gradle réussies**, APK/mapping/lint et
  conservation R8 des DTO contrôlés ; macOS : projet XcodeGen reproductible,
  build Release app/widget réussi et **203 tests Apple/macOS réussis** ;
- Ruff global et `git diff --check` : réussis ;
- schéma, vérité d'architecture, OpenAPI, SDK Python et dette technique :
  synchronisés, 0 erreur documentaire et 0 dette active ;
- preuve de retrait rapide : réussie, 261 fichiers Python compilés en mémoire,
  110 tables créées, aucun fournisseur découvert, spawn, accès réseau ou
  résidu runtime.

Les huit tests vrai binaire lient exclusivement `127.0.0.1` et des sockets Unix
locales, sans clé externe ni réseau public. Ils couvrent notamment auth privée,
concurrence, configuration projet hostile, reprise d'orphelin, lecture seule,
édition attestée, gate rouge vers vert, approbation refusée et nettoyage. Ils
constituent une porte `live` obligatoire en CI. La preuve de retrait `--full`
constitue un job macOS dédié dépendant des cinq autres portes de livraison. La
CI reconstruit aussi tous les clients depuis un environnement propre au SHA
livré.
