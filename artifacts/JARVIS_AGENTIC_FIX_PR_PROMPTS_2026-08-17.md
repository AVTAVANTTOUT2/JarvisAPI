# Prompts séquentiels de correction du pipeline agentique JARVIS

Ces prompts sont conçus pour être donnés un par un à Claude ou Codex. Chaque prompt doit partir d’un nouveau main contenant déjà la PR précédente.

Rapport source : artifacts/JARVIS_AGENTIC_PIPELINE_TEST_REPORT_2026-08-17.md

## Mode d’emploi

1. Attendre que la PR précédente soit validée et fusionnée.
2. Ouvrir une nouvelle tâche Claude/Codex dans le dépôt JARVIS.
3. Coller uniquement le prompt de la PR suivante.
4. Laisser l’agent créer sa branche, corriger, tester, commit, push et ouvrir la PR.
5. Ne pas fusionner une PR si ses critères d’acceptation ou sa CI ne passent pas.
6. Repartir ensuite du nouveau main, jamais de la branche de la PR précédente.

## Ordre recommandé

| PR | Agent conseillé | Branche proposée | Objectif |
|---:|---|---|---|
| 1 | Claude | fix/agentic-approved-permissions | Garantir que les permissions approuvées sont exactement celles du run |
| 2 | Codex | fix/agentic-task-run-state-sync | Supprimer la course entre états Task Control et runtime |
| 3 | Codex | fix/opencode-doom-loop | Corriger les boucles répétées d’outils OpenCode |
| 4 | Claude | fix/agentic-negative-intents | Respecter les contraintes « ne pas exécuter/modifier » |
| 5 | Claude | fix/agentic-fallback-plans | Remplacer les plans génériques par un fallback spécialisé et fail-closed |
| 6 | Codex | fix/agentic-hermetic-tests | Rendre les tests de profils hors réseau et aligner spaCy |
| 7 | Codex | fix/opencode-only-regression | Verrouiller OpenCode uniquement et ajouter la régression globale |

---

## PR 1 — Fidélité des permissions approuvées

Agent conseillé : Claude.

~~~text
Tu travailles dans le dépôt /Users/zeldris/JARVIS.

Mission : corriger la frontière de consentement du pipeline agentique afin que les permissions montrées et approuvées dans un plan soient exactement celles données au run OpenCode.

Contexte obligatoire :
- pars du dernier main à jour ; la branche de travail doit être créée depuis ce main ;
- lis intégralement AGENTS.md, CLAUDE.md, README.md et artifacts/JARVIS_AGENTIC_PIPELINE_TEST_REPORT_2026-08-17.md ;
- inspecte les modèles, la persistance, le digest du plan, TaskControlService._launch_run, les routes API, l’app macOS et les tests existants ;
- utilise uniquement OpenCode pour le runtime agentique ; Cursor est legacy et ne doit être ni lancé, ni utilisé comme fallback, ni réintroduit dans ce flux ;
- préserve les changements sans rapport avec cette mission.

Défaut à résoudre :
Lors du test réel, le plan macOS affichait uniquement workspace:read, mais le run créé après approbation recevait workspace:read, workspace:write et tests:run. L’utilisateur n’approuve donc pas fidèlement les capacités d’exécution.

Comportement exigé :
1. Le plan durable contient la liste canonique exacte des permissions nécessaires à l’exécution.
2. Cette liste est exposée par l’API et affichée dans Missions Jarvis avant la décision humaine.
3. Le digest/version du plan couvre les permissions approuvées.
4. Au démarrage, run.permissions doit être strictement égal à approved_plan.permissions.
5. Si le routage ou le profil demande une permission supplémentaire après approbation, le démarrage est refusé avant toute création/démarrage de runtime.
6. Une élévation légitime crée une nouvelle version de plan et exige une nouvelle approbation.
7. Ne règle pas le problème en retirant silencieusement workspace:write à une tâche d’écriture : le plan doit annoncer l’écriture.
8. Les anciennes données persistées doivent rester lisibles. Définis une migration/fallback fail-closed explicite pour les anciens plans sans permissions, sans leur accorder implicitement des droits plus larges.

Implémentation :
- cherche la cause racine et réutilise les abstractions existantes ;
- modifie le moins de fichiers possible, mais couvre modèle, persistance, API, UI macOS et tests si chacun est réellement touché ;
- n’ajoute aucune nouvelle dépendance ;
- ne supprime pas les approbations d’effet existantes : les permissions du plan et les approbations d’effet sont deux barrières complémentaires.

Tests obligatoires :
- plan de lecture : permissions affichées et run identiques ;
- plan d’écriture : workspace:write et tests:run visibles avant approbation, puis identiques dans le run ;
- tentative d’élévation après approbation : refus avant runtime ;
- révision avec permissions différentes : nouveau digest et nouvelle approbation ;
- ancienne version de plan sans champ : comportement fail-closed déterministe ;
- décodage/affichage macOS de la liste exacte ;
- tests ciblés Task Control, agentic profiles, API et tests natifs macOS concernés.

Critères d’acceptation :
- invariant automatisé approved_permissions == run.permissions ;
- aucune permission implicite ajoutée dans _launch_run ;
- aucune création de job Cursor ;
- aucune régression sur refus, révision, annulation ou idempotence ;
- lint, typage et tests ciblés passent.

Livraison :
1. crée la branche fix/agentic-approved-permissions depuis main ;
2. implémente et vérifie la correction ;
3. inspecte le diff final et retire tout changement hors périmètre ;
4. commit avec un message explicite ;
5. push la branche ;
6. ouvre une PR non fusionnée intitulée « Fix agentic approved permission integrity » ;
7. dans la PR, documente cause racine, invariant ajouté, migration, tests exécutés et résultats exacts ;
8. donne-moi l’URL de la PR et les éventuelles limites restantes.

Ne t’arrête pas à un diagnostic ou à un plan : la tâche n’est terminée que lorsque la PR est ouverte et que les validations demandées ont été exécutées.
~~~

---

## PR 2 — Synchronisation des états tâche/run

Agent conseillé : Codex.

~~~text
Tu travailles dans /Users/zeldris/JARVIS depuis le dernier main à jour, après fusion de la PR de fidélité des permissions.

Mission : supprimer la course qui affiche une mission running alors que le run OpenCode est encore queued sous pression mémoire.

Avant toute modification :
- lis AGENTS.md, CLAUDE.md, README.md et artifacts/JARVIS_AGENTIC_PIPELINE_TEST_REPORT_2026-08-17.md ;
- examine TaskControlService._launch_run, AgenticService.create_run/start_run/create_and_start, l’admission, les abonnements aux événements, la persistance et l’app macOS ;
- vérifie l’état Git et préserve les changements utilisateur ;
- OpenCode est le seul runtime autorisé. Aucun appel ou fallback Cursor.

Défaut reproduit :
- plan approuvé dans Missions Jarvis ;
- le run persistant est queued avec agent.run.resource_wait et admission_reason=memory_pressure ;
- la tâche et l’UI sont pourtant running/« Exécution lancée » ;
- une actualisation manuelle ne corrige pas l’état.

Cause à traiter :
Les premiers événements du run peuvent être émis avant que task.agentic_run_id soit persisté. Task Control ne retrouve alors pas la tâche via find_task_by_run, puis _launch_run force l’état RUNNING sans relire l’état réel du run.

Comportement exigé :
1. Associer durablement le run à la tâche avant que les premiers événements d’admission puissent être perdus.
2. Après chaque tentative de démarrage, persister l’état réel du run, pas un RUNNING supposé.
3. Un run queued/provisioning doit produire une tâche queued/provisioning cohérente et un message UI adapté.
4. resource_wait doit rester visible dans l’activité et ne doit pas être perdu.
5. running n’est affiché qu’après l’événement agent.run.started.
6. L’annulation en file doit produire cancelling puis cancelled et empêcher toute reprise ultérieure.
7. Un rejeu ou un événement précoce doit être idempotent et ne doit jamais créer un second run.
8. La reprise FIFO et la limite de concurrence doivent rester fonctionnelles.

Implémentation attendue :
- préfère le flux existant create_run → association persistée → start_run si cela résout proprement la course ;
- évite une nouvelle abstraction si les primitives existantes suffisent ;
- ajoute une réconciliation ciblée uniquement si elle est nécessaire ;
- garde une seule source de vérité pour la conversion AgenticRunStatus → TaskStatus.

Tests obligatoires :
- événement queued reçu avant la fin de _launch_run ;
- resource_wait immédiat pour memory_pressure ;
- passage ultérieur queued → provisioning → running ;
- annulation pendant queued ;
- double appel start_execution sur le même plan : un seul run ;
- rejeu d’événements et ordre hors séquence ;
- limite de concurrence 1 avec deux tâches et ordre FIFO ;
- contrat API et affichage macOS : « En attente de ressources » et non « En cours » ;
- suite Task Control, admission, agentic service, WebSocket et tests macOS concernés.

Critères d’acceptation :
- invariant task.status cohérent avec run.status après create/start ;
- aucun événement resource_wait perdu ;
- aucun run dupliqué ;
- aucune création de job Cursor ;
- tests, lint et typage passent.

Livraison :
1. branche fix/agentic-task-run-state-sync créée depuis main ;
2. correction complète et tests de non-régression ;
3. commit et push ;
4. ouvre une PR non fusionnée intitulée « Fix Task Control and agentic run state synchronization » ;
5. la description de PR doit inclure la chronologie de la course avant/après et tous les résultats de tests ;
6. retourne l’URL de la PR.

Ne rends pas seulement une analyse. Termine par une PR ouverte.
~~~

---

## PR 3 — Anti-boucle OpenCode et événements d’outils

Agent conseillé : Codex.

~~~text
Tu travailles dans /Users/zeldris/JARVIS depuis le dernier main à jour, après fusion des PR permissions et synchronisation d’état.

Mission : empêcher les boucles OpenCode d’outils identiques tout en conservant l’abort de sécurité et en évitant les faux positifs.

Contexte à lire :
- AGENTS.md, CLAUDE.md, README.md ;
- artifacts/JARVIS_AGENTIC_PIPELINE_TEST_REPORT_2026-08-17.md, notamment le run réel terminé par doom_loop_same_action ;
- intégration OpenCode, mapping des événements SSE, budgets, garde anti-boucle, persistance des événements et tests real_binary_e2e.

Contraintes :
- OpenCode uniquement ; ne lance et ne réactive jamais Cursor ;
- aucune dépendance supplémentaire ;
- conserve le serveur isolé par run, le réseau loopback et le mécanisme /abort ;
- préserve les limites de budget déjà en place.

Défaut observé :
Un workflow de bilan/journal a démarré correctement dans OpenCode puis a produit 10 démarrages de jarvis_jarvis_knowledge_search et 2 de jarvis_jarvis_tasks_list, avec seulement 5 fins d’outils, avant un abort budget_exceeded avec violation doom_loop_same_action et aucun livrable.

Résultat attendu :
1. Construire une empreinte stable à partir du nom d’outil et des arguments normalisés/redacted.
2. Différencier :
   - un événement provider dupliqué ;
   - un retry du même appel ;
   - deux recherches réellement différentes ;
   - une répétition sans progrès.
3. Dédupliquer les événements identiques grâce aux identifiants externes lorsqu’ils existent.
4. Après deux appels identiques sans nouvelle information, injecter au runtime un feedback explicite demandant synthèse, changement de stratégie ou clarification utilisateur.
5. Si une troisième répétition identique survient sans progrès, arrêter proprement bien avant dix appels.
6. Produire une erreur utilisateur exploitable avec violation, outil concerné, nombre de répétitions et prochaine action possible, sans exposer d’arguments sensibles.
7. Toujours confirmer l’ACK de /abort et terminer dans un état canonique.
8. Ne pas bloquer des recherches différentes, les lectures paginées, ni un retry après erreur transitoire.
9. Un workflow guidé doit pouvoir terminer avec une réponse textuelle sans artefact fichier lorsque c’est son livrable attendu.

Tests obligatoires :
- doublon exact d’événement SSE : un seul événement persistant ;
- mêmes outil et arguments répétés sans progrès : feedback puis arrêt au troisième ;
- même outil avec arguments différents : autorisé ;
- retry après erreur retryable : autorisé dans le budget ;
- séquence started/completed correctement appariée ;
- abort ACK et état terminal ;
- message redacted ;
- workflow guidé qui recherche une fois puis répond : succès ;
- fournisseur loopback real_binary_e2e reproduisant la boucle ;
- suites OpenCode, chaos, budgets, persistance et Task Control concernées.

Critères d’acceptation :
- aucun scénario ne peut atteindre dix appels identiques silencieux ;
- pas de faux positif sur des appels différents ;
- aucune donnée sensible dans événements/erreurs ;
- aucune création de job Cursor ;
- tous les tests ciblés passent.

Livraison :
1. branche fix/opencode-doom-loop depuis main ;
2. implémentation minimale à la cause racine ;
3. tests ciblés puis suite OpenCode pertinente ;
4. commit, push ;
5. ouvre une PR non fusionnée intitulée « Stop repeated OpenCode tool loops safely » ;
6. décris dans la PR l’algorithme d’empreinte, les seuils, les faux positifs évités et les tests ;
7. retourne l’URL de la PR.

La mission n’est complète qu’avec la PR ouverte.
~~~

---

## PR 4 — Contraintes négatives et demandes sans exécution

Agent conseillé : Claude.

~~~text
Tu travailles dans /Users/zeldris/JARVIS depuis le dernier main à jour.

Mission : corriger le routage afin que JARVIS respecte les contraintes explicites « ne pas exécuter », « ne pas modifier », « lecture seule » et « dis-moi seulement » avant toute élévation agentique.

Lis d’abord :
- AGENTS.md, CLAUDE.md, README.md ;
- artifacts/JARVIS_AGENTIC_PIPELINE_TEST_REPORT_2026-08-17.md ;
- le classifieur agentique, maybe_start_agentic_run, les profils de capacités, le routage adaptatif, Task Control ingest et les tests de langage naturel.

Contraintes :
- OpenCode est le seul runtime agentique ;
- Cursor est legacy et ne doit pas être utilisé ;
- le correctif doit être déterministe, explicable et testable hors réseau ;
- ne remplace pas tout le classifieur par une liste fragile de mots-clés : ajoute une extraction ciblée de contraintes qui s’applique avant l’élévation de capacités.

Défaut reproduit :
« Dis-moi si tous les tests passent, mais ne les exécute pas. » crée une mission et un plan alors que le résultat correct est d’expliquer qu’on ne peut pas connaître l’état actuel sans exécuter les tests.

Comportement exigé :
1. Extraire les contraintes négatives françaises et anglaises pertinentes avant le routage.
2. Une interdiction d’exécution empêche tests:run, workspace:write, tasks:write et la création automatique d’une mission.
3. Une interdiction de modification peut autoriser une analyse strictement read-only si elle est réellement possible.
4. Si la question exige une action interdite pour être vérifiée, JARVIS explique la limite au lieu d’inventer un résultat.
5. Les contraintes doivent être conservées dans le contexte public/diagnostic sans exposer de raisonnement interne.
6. Les demandes positives restent fonctionnelles :
   - « lance les tests » doit pouvoir être agentique ;
   - « analyse le dépôt sans modifier » doit sélectionner un profil read-only ;
   - « ne modifie pas le code mais lance les tests » peut demander tests:run sans workspace:write ;
   - une négation citée comme exemple et non comme instruction ne doit pas annuler l’action.
7. Le comportement doit être identique sur API, dashboard web, macOS et voix.

Tests obligatoires :
- formulations françaises : ne lance pas, n’exécute pas, sans modifier, lecture seule, dis-moi seulement ;
- formulations anglaises équivalentes ;
- négations combinées ;
- citation/mention non impérative ;
- demandes positives proches pour éviter les faux négatifs ;
- vérification qu’aucune tâche et aucun run ne sont créés pour le prompt de reproduction ;
- vérification des permissions minimales pour lecture seule et tests sans écriture ;
- tests sans aucun accès Hugging Face ou réseau.

Critères d’acceptation :
- le prompt de reproduction renvoie une explication et crée 0 tâche/0 run ;
- les capacités interdites ne peuvent pas réapparaître plus tard dans le pipeline ;
- aucune création de job Cursor ;
- tests de classifieur, API, Task Control et canaux passent.

Livraison :
1. branche fix/agentic-negative-intents depuis main ;
2. correction à la cause racine et tests ;
3. lint, typage et suites ciblées ;
4. commit, push ;
5. ouvre une PR non fusionnée intitulée « Respect negative execution constraints in agentic routing » ;
6. documente les règles de précédence et cas limites dans la PR ;
7. retourne l’URL de la PR.

Ne t’arrête pas avant l’ouverture de la PR.
~~~

---

## PR 5 — Plans de repli spécialisés et fail-closed

Agent conseillé : Claude.

~~~text
Tu travailles dans /Users/zeldris/JARVIS depuis le dernier main à jour, avec les corrections précédentes déjà fusionnées.

Mission : remplacer le plan de repli universel et générique par une planification spécialisée par catégorie, suffisamment précise pour une approbation éclairée, et fail-closed lorsqu’une tâche d’écriture reste indéterminée.

Contexte obligatoire :
- lis AGENTS.md, CLAUDE.md, README.md et artifacts/JARVIS_AGENTIC_PIPELINE_TEST_REPORT_2026-08-17.md ;
- inspecte jarvis/task_control/planner.py, api/agentic_planning.py, les modèles de plan, la persistance, l’API, Missions Jarvis macOS et les tests ;
- tiens compte de la PR permissions déjà fusionnée : le plan doit annoncer les permissions exactes ;
- OpenCode uniquement, aucun fallback Cursor.

Défaut observé :
Deux demandes très différentes ont reçu le même plan :
1. rassembler le contexte ;
2. réaliser le travail ;
3. vérifier et rendre compte.
Le plan indiquait qu’aucune analyse du dépôt n’avait été faite, n’identifiait pas les fichiers/livrables/tests et annonçait des autorisations incomplètes.

Comportement exigé :
1. Marquer explicitement la source/qualité du plan : modèle, fallback spécialisé ou indisponible.
2. Pour une tâche d’écriture, ne jamais proposer un plan exécutable si le workspace cible, les livrables, les permissions ou les validations nécessaires sont inconnus.
3. Dans ce cas, rester fail-closed : demander une précision ou placer la tâche en état de planification bloquée, sans approbation possible et sans run.
4. Pour les catégories qui peuvent utiliser un fallback déterministe, générer des étapes spécialisées :
   - lecture/recherche ;
   - exécution de tests ;
   - édition réversible ;
   - workflow personnel ;
   - tâche planifiée.
5. Chaque plan exécutable doit contenir objectif, contexte compris, étapes concrètes, livrables, outils, permissions exactes, tests/validations, risques, limites et critères de réussite.
6. La qualité/source et les éléments manquants doivent être exposés par l’API et l’app macOS.
7. Une indisponibilité du planner ne doit jamais devenir une autorisation implicite.
8. Les plans existants restent lisibles, mais un ancien plan incomplet ne peut pas être démarré avec des permissions implicites.

Tests obligatoires :
- planner modèle disponible ;
- planner indisponible pour lecture seule : fallback spécialisé acceptable ;
- planner indisponible pour écriture avec cible inconnue : fail-closed, aucun bouton d’approbation utile, aucun run ;
- écriture avec cible temporaire et livrables connus : plan spécialisé complet ;
- tests:run sans workspace:write ;
- workflow personnel sans artefact fichier obligatoire ;
- révision après ajout d’une information manquante ;
- API, persistance, digest, UI macOS et transitions Task Control ;
- aucun accès réseau dans les tests unitaires.

Critères d’acceptation :
- aucun plan exécutable ne contient seulement les trois étapes génériques historiques ;
- aucun run d’écriture ne démarre avec un plan incomplet ;
- permissions exactes visibles et couvertes par le digest ;
- aucune création de job Cursor ;
- lint, typage et tests passent.

Livraison :
1. branche fix/agentic-fallback-plans depuis main ;
2. implémente le plus petit design cohérent avec les modèles existants ;
3. ajoute les tests de non-régression ;
4. commit, push ;
5. ouvre une PR non fusionnée intitulée « Make agentic fallback planning specific and fail closed » ;
6. inclus dans la PR des exemples avant/après pour lecture et écriture, ainsi que les résultats exacts des tests ;
7. retourne l’URL de la PR.

La réponse finale seule ne suffit pas : ouvre effectivement la PR.
~~~

---

## PR 6 — Tests de profils hermétiques et compatibilité spaCy

Agent conseillé : Codex.

~~~text
Tu travailles dans /Users/zeldris/JARVIS depuis le dernier main à jour.

Mission : rendre toute la suite de profils agentiques déterministe et hors réseau, puis supprimer l’avertissement de compatibilité spaCy sans masquer un vrai problème de version.

Lis :
- AGENTS.md, CLAUDE.md, README.md ;
- artifacts/JARVIS_AGENTIC_PIPELINE_TEST_REPORT_2026-08-17.md ;
- tests/test_agentic_profiles.py, le routeur sémantique, le chargement des embeddings, les requirements/locks et la CI.

Problèmes à résoudre :
1. La suite complète test_agentic_profiles.py tente de télécharger sentence-transformers/all-MiniLM-L6-v2 et expire en retries Hugging Face lorsque le réseau est refusé.
2. fr_core_news_sm 3.7.0 est chargé avec spaCy 3.8.14 et émet un avertissement de compatibilité.

Contraintes :
- les tests unitaires de routage ne doivent jamais dépendre d’Internet ;
- n’ajoute pas un modèle lourd au dépôt ;
- n’ignore pas globalement les erreurs réseau ;
- n’étouffe pas l’avertissement spaCy : aligne réellement les versions ou isole un fallback explicitement testé ;
- OpenCode uniquement ; aucun changement qui réactive Cursor.

Comportement exigé :
1. Injecter dans les tests de profils un embedder déterministe/factice via le point d’injection le plus proche du design existant.
2. Garantir qu’aucun import ou constructeur de test ne déclenche un téléchargement implicite.
3. Faire échouer rapidement et clairement le runtime de production si un modèle obligatoire manque, ou utiliser le fallback local déjà prévu ; pas de retry réseau silencieux dans les tests.
4. Ajouter un test qui bloque les sockets ou définit le mode offline et prouve que test_agentic_profiles.py termine.
5. Aligner spaCy et fr_core_news_sm dans les fichiers de dépendances/locks réellement utilisés sur macOS et en CI, ou adapter le chargeur selon les versions supportées du projet.
6. Conserver le comportement fonctionnel du routeur après remplacement de l’embedder de test.

Validations obligatoires :
- HF_HUB_OFFLINE=1 et TRANSFORMERS_OFFLINE=1 ;
- python -m pytest tests/test_agentic_profiles.py -q ;
- tests du classifieur, du routeur adaptatif et de la mémoire sémantique concernés ;
- ruff/lint et vérification des locks ;
- vérification qu’aucun warning de compatibilité spaCy ne reste dans la suite ciblée ;
- documenter toute différence entre environnement macOS et CI.

Critères d’acceptation :
- test_agentic_profiles.py passe intégralement sans réseau ;
- aucun timeout/retry Hugging Face ;
- pas d’avertissement spaCy de version incompatible ;
- aucune dégradation des routes de profils ;
- aucune création de job Cursor.

Livraison :
1. branche fix/agentic-hermetic-tests depuis main ;
2. correction minimale et tests ;
3. mets à jour uniquement les locks réellement nécessaires ;
4. commit, push ;
5. ouvre une PR non fusionnée intitulée « Make agentic profile tests hermetic » ;
6. indique dans la PR les commandes exécutées, durées, résultats et fichiers de dépendances modifiés ;
7. retourne l’URL de la PR.

Ne conclus pas avant que la PR soit ouverte.
~~~

---

## PR 7 — Durcissement OpenCode uniquement et régression globale

Agent conseillé : Codex.

~~~text
Tu travailles dans /Users/zeldris/JARVIS depuis le dernier main à jour, après fusion des six PR de correction précédentes.

Mission finale : rendre l’exigence « OpenCode uniquement » explicite et durable, ajouter une suite de régression couvrant le pipeline complet corrigé, puis ouvrir la PR de validation finale.

Références obligatoires :
- AGENTS.md, CLAUDE.md, README.md ;
- artifacts/JARVIS_AGENTIC_PIPELINE_TEST_REPORT_2026-08-17.md ;
- toutes les modifications fusionnées des PR précédentes ;
- configuration runtime, registry, fallback, Task Control, OpenCode, DevAgent, API, app macOS et CI.

Règle absolue :
Cursor est du code legacy. Il peut rester lisible pour l’historique/migration, mais le pipeline agentique courant ne doit jamais créer, lancer ou sélectionner un job Cursor. OpenCode est le seul runtime autorisé et le système doit échouer fermé s’il est indisponible.

Travail demandé :
1. Définir explicitement AGENTIC_RUNTIME=opencode dans les configurations/templates de déploiement pertinentes.
2. Conserver AGENTIC_RUNTIME_FALLBACK=disabled.
3. Vérifier que auto, une valeur inconnue ou l’absence de binaire ne peuvent pas créer un job Cursor.
4. Préserver la lecture des anciennes données Cursor sans réactiver leur exécution.
5. Ajouter une suite de régression intégrée, sans réseau public, couvrant :
   - identité OpenCode sans tâche ;
   - négation explicite sans mission/run ;
   - refus de plan ;
   - révision et digest ;
   - écriture réversible dans un workspace temporaire ;
   - permissions approuvées identiques au run ;
   - refus d’autorisation d’effet ;
   - memory_pressure avec tâche réellement queued ;
   - annulation en file ;
   - idempotence ;
   - concurrence 1 et FIFO ;
   - anti-boucle d’outil ;
   - OpenCode indisponible sans fallback ;
   - completed uniquement après verdict PASS et preuve/livrable attendu.
6. Réutiliser le fournisseur loopback et les fixtures existantes ; ne crée pas une deuxième infrastructure de test.
7. Ajouter les contrats manquants au niveau le plus proche de la cause, puis un seul test E2E de synthèse.
8. Mettre à jour la documentation d’exploitation et de diagnostic OpenCode, sans réécrire toute la documentation.

Validation obligatoire :
- intégrations OpenCode hors réseau ;
- real_binary_e2e avec loopback ;
- Task Control domaine/service/E2E ;
- admission, annulation, chaos, budgets, profils, persistance, registry et WebSocket ;
- DevAgent/livraison/worktrees ;
- nouveau test de régression globale ;
- lint, typage et build pertinents ;
- si l’environnement macOS est disponible : vérification manuelle Missions Jarvis pour permissions, queued et annulation ;
- si le dashboard est disponible : prompt d’identité retournant OpenCode et 0 nouveau run ;
- comparer avant/après le nombre de cursor_delegation_jobs et prouver qu’il est inchangé.

Critères d’acceptation :
- 100 % des nouveaux runs ont runtime_id=opencode ;
- 0 nouveau job Cursor ;
- runtime indisponible => erreur fail-closed exploitable ;
- approved_permissions == run.permissions ;
- task.status == état réel du run pour queued/running/terminal ;
- aucun plan d’écriture incomplet exécutable ;
- aucune boucle d’outil identique silencieuse ;
- completed exige PASS et la preuve attendue ;
- toute la suite ciblée passe hors réseau.

Livraison :
1. crée la branche fix/opencode-only-regression depuis le dernier main ;
2. implémente le durcissement et la régression sans corriger silencieusement d’autres sujets ;
3. exécute toutes les validations ci-dessus et conserve les résultats exacts ;
4. inspecte le diff pour vérifier qu’aucun secret, artefact temporaire ou changement legacy inutile n’est inclus ;
5. commit, push ;
6. ouvre une PR non fusionnée intitulée « Enforce OpenCode-only agentic pipeline » ;
7. la PR doit contenir une matrice avant/après des 14 scénarios, les résultats de tests, les limites non exécutées et la preuve qu’aucun job Cursor n’a été créé ;
8. retourne l’URL de la PR.

Ne fusionne pas la PR toi-même. La mission est terminée uniquement lorsque la PR est ouverte et prête à être revue.
~~~

## Contrôle final après fusion de la PR 7

Une fois les sept PR fusionnées, rejouer le benchmark du rapport initial depuis un main propre. Le résultat attendu est :

- tous les tests agentiques ciblés passent hors réseau ;
- dashboard : OpenCode, aucune mission pour une requête sans action ;
- app macOS : permissions exactes avant approbation ;
- memory_pressure : tâche et run tous deux queued ;
- annulation : état terminal cohérent ;
- workflow guidé : réponse utile sans boucle ;
- écriture temporaire : artefact livré après PASS ;
- 100 % des nouveaux runs OpenCode ;
- 0 nouveau job Cursor.
